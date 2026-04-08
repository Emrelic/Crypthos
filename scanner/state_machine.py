"""Scanner State Machine - orchestrates the SCANNING->BUYING->SELLING cycle.
Single thread, sequential state transitions.
Supports up to max_positions concurrent positions."""
import re
import time
import threading
import pandas as pd
from loguru import logger
from core.config_manager import ConfigManager
from core.event_bus import EventBus
from core.constants import (EventType, ScannerState, OrderSide, OrderType)
from market.binance_rest import BinanceRestClient
from scanner.symbol_universe import SymbolUniverse
from scanner.batch_fetcher import BatchKlineFetcher
from scanner.scanner_scorer import ScannerScorer, ScanResult
from scanner.scanner_scorer_mr import MRScannerScorer, MRScanResult
from scanner.system_b_scanner import SystemBScanner, SystemBScanResult
from scanner.system_d_scanner import SystemDScanner, SystemDScanResult
from scanner.system_e_scanner import SystemEScanner, SystemEScanResult
from scanner.system_f_scanner import SystemFScanner, SystemFScanResult
from scanner.system_g_scanner import SystemGScanner, SystemGScanResult
from scanner.system_h_scanner import SystemHScanner, SystemHScanResult
from scanner.system_i_scanner import SystemIScanner, SystemIScanResult
from scanner.system_j_scanner import SystemJScanner, SystemJScanResult
from scanner.system_m_scanner import SystemMScanner, SystemMScanResult
from scanner.system_n_scanner import SystemNScanner, SystemNScanResult
from scanner.position_manager import PositionManager
from scanner.timeframe_selector import TimeframeSelector
from indicators.indicator_engine import IndicatorEngine
from analysis.confluence import ConfluenceScorer
from analysis.market_regime import MarketRegimeDetector
from analysis.divergence import DivergenceDetector
from analysis.orderbook_analyzer import OrderBookAnalyzer
from analysis.btc_correlation import BTCCorrelationEngine

TF_LADDER = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h"]


class ScannerStateMachine:
    """Main scanner loop: IDLE -> SCANNING -> BUYING -> loop.

    While SCANNING, also checks all held positions for exit signals.
    No more HOLDING state - scanning and holding happen simultaneously.
    """

    def __init__(self, config: ConfigManager, event_bus: EventBus,
                 rest_client: BinanceRestClient,
                 symbol_info_cache=None):
        self._config = config
        self._event_bus = event_bus
        self._rest = rest_client
        self._symbol_info_cache = symbol_info_cache

        # State
        self._state = ScannerState.IDLE
        self._running = False
        self._stop_event = threading.Event()
        self._thread = None

        # Scanner components
        self._universe = SymbolUniverse(
            rest_client,
            top_n=config.get("strategy.max_symbols_to_scan", 50),
            min_volume_usdt=config.get("scanner.min_volume_24h_usdt", 5_000_000),
        )
        self._fetcher = BatchKlineFetcher(
            rest_client,
            max_workers=config.get("scanner.fetch_workers", 10),
            requests_per_second=config.get("scanner.requests_per_second", 3.5),
        )
        self._scorer = ScannerScorer(config)
        self._mr_scorer = MRScannerScorer(config)
        self._position_mgr = PositionManager(config, event_bus)
        self._tf_selector = TimeframeSelector(rest_client, config=config)
        self._ob_analyzer = OrderBookAnalyzer()
        self._btc_corr = BTCCorrelationEngine(rest_client, config)

        # Holding-phase analysis (separate engine for held symbols)
        self._hold_engine = IndicatorEngine(config)
        self._hold_confluence = ConfluenceScorer(threshold=4.0, config=config)
        self._hold_regime = MarketRegimeDetector()
        self._hold_divergence = DivergenceDetector(lookback=20)

        # External components (set by controller)
        self._order_executor = None
        self._pair_switcher = None
        self._market_service = None
        self._risk_manager = None
        self._binance_app = None
        self._order_logger = None

        # Scan results (for GUI)
        self._last_scan_results: list[ScanResult] = []
        self._last_mr_results: list[MRScanResult] = []  # MR pool results
        self._last_candidate: ScanResult = None
        self._scan_count = 0
        self._last_trade_result: dict = {}
        self._failed_symbols: dict[str, float] = {}  # symbol -> fail timestamp
        # Current indicators for held positions (updated every check cycle)
        self._held_indicators: dict[str, dict] = {}  # symbol -> indicator snapshot
        self._failed_cooldown = 300  # skip failed symbols for 5 minutes

        # Loss cooldown: skip symbols that recently closed at a loss
        self._loss_cooldown_symbols: dict[str, float] = {}  # symbol -> loss timestamp
        self._loss_cooldown_seconds = config.get("strategy.loss_cooldown_seconds", 600)  # 10 min default

        # Track server-side trailing stops: symbol -> {callback_pct, timestamp, activation_price}
        self._server_trailing: dict[str, dict] = {}

        # Anti-churning: track trade frequency
        self._trade_timestamps: list[float] = []
        self._max_trades_per_hour = config.get("scanner.max_trades_per_hour", 12)

        # Close retry tracking: symbol -> {"count": int, "next_retry": float}
        self._close_retries: dict[str, dict] = {}
        self._max_close_retries = 5

        # Race condition guard: symbols currently being sold (prevents double-sell)
        self._selling_lock = threading.Lock()
        self._selling_symbols: set[str] = set()

        # Market context cache: {symbol: {funding_rate, oi_change_pct}}
        self._market_context: dict[str, dict] = {}

        # Pending limit orders: symbol -> {order_id, limit_price, side, size, atr,
        #   candidate, leverage, margin_usdt, placed_time, timeout, qty_precision}
        self._pending_limits: dict[str, dict] = {}

        # Per-coin daily loss ban: symbol -> [loss_timestamp, ...]
        self._coin_loss_history: dict[str, list[float]] = {}

        # ── System B (Wave Analysis) ──
        self._system_b_scanner = SystemBScanner(config)
        self._last_system_b_results: list[SystemBScanResult] = []
        self._system_b_regime_history: dict[str, list[str]] = {}  # symbol -> [regime, ...] (hysteresis)

        # ── System D (Sıralı Coin Analiz) ──
        self._system_d_scanner = SystemDScanner(config)
        self._last_system_d_results: list[SystemDScanResult] = []

        # ── System E (Yüksek Kaldıraç Yön Kesinliği) ──
        self._system_e_scanner = SystemEScanner(config)
        self._last_system_e_results: list[SystemEScanResult] = []

        # ── System F (Son Kursun) ──
        self._system_f_scanner = SystemFScanner(config)
        self._last_system_f_results: list[SystemFScanResult] = []

        # ── System G (Per-Coin Optimized) ──
        self._system_g_scanner = SystemGScanner(config)
        self._last_system_g_results: list[SystemGScanResult] = []

        # ── System M (AlphaTrend PRO) ──
        self._system_m_scanner: SystemMScanner | None = None
        self._last_system_m_results: list[SystemMScanResult] = []
        self._system_m_decisions: list[dict] = []  # karar log'u (max 200)

        # ── System N (AlphaTrend PRO v2 — System M kopyası, geliştirme için) ──
        self._system_n_scanner: SystemNScanner | None = None
        self._last_system_n_results: list[SystemNScanResult] = []
        self._system_n_decisions: list[dict] = []  # karar log'u (max 500)
        self._system_n_scan_count: int = 0  # System N'e özel tarama sayacı
        self._system_n_scan_start_time: float = 0.0  # bu scan döngüsünün başlangıç zamanı
        # System N loss protection: ardışık reverse takibi
        self._system_n_reverse_history: dict[str, list[float]] = {}  # symbol -> [reverse_timestamps]

        # ── System J (Max Leverage First) ──
        self._system_j_scanner: SystemJScanner | None = None
        self._last_system_j_results: list[SystemJScanResult] = []

        # ── System I (Unified) ──
        self._system_i_scanner: SystemIScanner | None = None
        self._last_system_i_results: list[SystemIScanResult] = []
        self._si_last_deep_scan_time: float = 0.0

        # ── System H (Hibrit: A+B+D+F) ──
        self._system_h_scanner = SystemHScanner(config)
        self._last_system_h_results: list[SystemHScanResult] = []
        self._sg_last_full_scan_time = 0.0
        self._sg_shortlist: list[str] = []
        self._sg_cached_symbols: list[str] = []
        self._sg_cached_klines: dict[str, dict[str, list]] = {}
        self._sg_cached_volume_map: dict[str, float] = {}
        self._sg_cached_ob_map: dict[str, dict] = {}
        self._sg_cached_btc_direction: str = "FLAT"
        # İki katmanlı tarama state
        self._sf_last_full_scan_time: float = 0.0
        self._sf_shortlist: list[str] = []
        self._sf_last_full_results: list[SystemFScanResult] = []
        self._sf_cached_klines: dict[str, dict[str, list]] = {}
        self._sf_cached_market_ctx: dict = {}
        self._sf_cached_ob_map: dict = {}
        self._sf_cached_beta_map: dict = {}
        self._sf_cached_btc_direction: str = "FLAT"
        self._sf_cached_volume_map: dict = {}
        self._sf_cached_symbols: list[str] = []

        # Server order verification: last check time (interval from config)
        self._last_order_verify_time: float = 0.0
        self._order_verify_interval: float = config.get(
            "strategy.order_verify_interval", 30.0)

    # ──── Setters ────

    def set_order_executor(self, executor) -> None:
        self._order_executor = executor

    def set_pair_switcher(self, ps) -> None:
        self._pair_switcher = ps

    def set_market_service(self, ms) -> None:
        self._market_service = ms

    def set_risk_manager(self, rm) -> None:
        self._risk_manager = rm

    def set_binance_app(self, app) -> None:
        self._binance_app = app

    def set_order_logger(self, ol) -> None:
        self._order_logger = ol

    # ──── Control ────

    def start(self) -> None:
        if self._running:
            return

        # Sync existing API positions before starting
        self._sync_api_positions()

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._main_loop, daemon=True,
                                        name="ScannerStateMachine")
        self._thread.start()
        # Start position monitor thread (checks every 3 seconds)
        self._monitor_thread = threading.Thread(
            target=self._position_monitor_loop, daemon=True,
            name="PositionMonitor")
        self._monitor_thread.start()
        self._transition(ScannerState.SCANNING)
        logger.info("Scanner started (with position monitor)")

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=15)
        self._transition(ScannerState.IDLE)
        logger.info("Scanner stopped")

    def _transition(self, new_state: ScannerState) -> None:
        old = self._state
        self._state = new_state
        self._event_bus.publish(EventType.SCANNER_STATE_CHANGE, {
            "old_state": old.value,
            "new_state": new_state.value,
        })
        logger.info(f"Scanner: {old.value} -> {new_state.value}")

    # ──── Main Loop ────

    def _main_loop(self) -> None:
        _last_state_save = 0.0
        while self._running:
            try:
                # Periyodik state save (60sn'de bir, crash/kill koruması)
                _now = time.time()
                if _now - _last_state_save > 60 and self._position_mgr.has_position:
                    try:
                        self._position_mgr.save_state()
                    except Exception:
                        pass
                    _last_state_save = _now

                if self._state == ScannerState.SCANNING:
                    if self._config.get("system_n.enabled", False):
                        self._do_scanning_system_n()
                    elif self._config.get("system_m.enabled", False):
                        self._do_scanning_system_m()
                    elif self._config.get("system_j.enabled", False):
                        self._do_scanning_system_j()
                    elif self._config.get("system_i.enabled", False):
                        self._do_scanning_system_i()
                    elif self._config.get("system_h.enabled", False):
                        self._do_scanning_system_h()
                    elif self._config.get("system_g.enabled", False):
                        self._do_scanning_system_g()
                    elif self._config.get("system_f.enabled", False):
                        self._do_scanning_system_f()
                    elif self._config.get("system_e.enabled", False):
                        self._do_scanning_system_e()
                    elif self._config.get("system_d.enabled", False):
                        self._do_scanning_system_d()
                    elif self._config.get("system_b.enabled", False):
                        self._do_scanning_system_b()
                    else:
                        self._do_scanning()
                elif self._state == ScannerState.BUYING:
                    self._do_buying()
                elif self._state == ScannerState.HOLDING:
                    # Legacy: check positions then go back to scanning
                    self._check_held_positions()
                    self._transition(ScannerState.SCANNING)
                elif self._state == ScannerState.SELLING:
                    self._do_selling()
                elif self._state == ScannerState.COOLDOWN:
                    self._do_cooldown()
                else:
                    self._stop_event.wait(timeout=1)
            except Exception as e:
                import traceback
                logger.error(f"Scanner error in {self._state.value}: {e}\n{traceback.format_exc()}")
                self._stop_event.wait(timeout=5)

    # ──── SCANNING State ────

    def _do_scanning(self) -> None:
        """Scan top 50 symbols, score each, pick best candidate.
        Also check all held positions for exit signals."""

        focus_mode = self._config.get("strategy.focus_mode", False)

        # FOCUS MODE: if we have a position, don't scan — just monitor
        if focus_mode and self._position_mgr.has_position:
            self._do_focus_monitoring()
            return

        # Step 0: Check pending limit orders (fill/timeout)
        if self._pending_limits:
            self._check_pending_limits()

        # Step A: Check held positions first
        if self._position_mgr.has_position:
            self._check_held_positions()

        self._scan_count += 1
        logger.info(f"Scan #{self._scan_count} starting... "
                    f"[positions: {self._position_mgr.position_count}/"
                    f"{self._position_mgr.max_positions}]")

        # 1. Get symbol universe
        symbols = self._universe.refresh()
        if not symbols:
            logger.warning("No symbols to scan")
            self._stop_event.wait(timeout=10)
            return

        # 2. Dynamic timeframe analysis (refreshes every 5 min)
        dynamic_tf = self._config.get("strategy", {}).get("dynamic_timeframe", True)
        symbol_intervals = {}
        if dynamic_tf and self._tf_selector.needs_refresh():
            lev_override = self._config.get("leverage.max_leverage", 0)
            self._tf_selector.refresh(symbols, leverage_override=lev_override)

        if dynamic_tf:
            for sym in symbols:
                symbol_intervals[sym] = self._tf_selector.get_timeframe(sym)

        # 3. Batch fetch klines (each coin at its own timeframe)
        default_interval = self._config.get("indicators.kline_interval", "1m")
        limit = self._config.get("strategy.kline_limit", 200)
        klines_map = self._fetcher.fetch_batch(
            symbols, default_interval, limit,
            symbol_intervals=symbol_intervals if dynamic_tf else None,
        )

        # 3a. Refresh BTC correlation data (every 5 min, cached)
        self._btc_corr.refresh()

        # 3b. Two-phase sentiment: funding rates for all, OI+depth for top 15
        # Phase 1: Funding rates (1 API call for ALL symbols)
        market_ctx = self._fetch_funding_rates(list(klines_map.keys()))

        # Phase 1 scoring: rank without OI/depth (funding rate only)
        ticker_data = self._universe.get_all_tickers()
        preliminary = self._scorer.score_batch(klines_map, ticker_data, market_ctx)

        # Phase 2: Fetch OI + OrderBook for top 15 by preliminary score
        top_15_symbols = [r.symbol for r in preliminary[:15]]
        self._fetch_oi_depth(top_15_symbols, market_ctx)
        self._market_context = market_ctx  # cache for position display

        # Phase 2 scoring: re-score top 15 with full sentiment data
        top_15_klines = {s: klines_map[s] for s in top_15_symbols if s in klines_map}
        top_15_results = self._scorer.score_batch(top_15_klines, ticker_data, market_ctx)

        # Merge: top 15 re-scored + rest unchanged
        top_15_set = set(top_15_symbols)
        results = top_15_results + [r for r in preliminary if r.symbol not in top_15_set]
        results.sort(key=lambda r: abs(r.score), reverse=True)

        # Enrich results with leverage and timeframe data
        if dynamic_tf:
            for r in results:
                coin_info = self._tf_selector.get_coin_info(r.symbol)
                if coin_info:
                    r.leverage = coin_info.max_leverage
                    r.timeframe = coin_info.optimal_tf
                else:
                    r.timeframe = symbol_intervals.get(r.symbol, default_interval)

        # Multi-timeframe analysis for top 5 (enriches mtf_data field)
        try:
            self._fetch_mtf_data(results)
        except Exception as e:
            logger.debug(f"MTF analysis failed: {e}")

        # === MEAN REVERSION POOL ===
        mr_enabled = self._config.get("strategy.mean_reversion_enabled", False)
        mr_results = []
        mr_symbols = {}  # symbol -> source ("R" or "G->R")
        if mr_enabled:
            mr_max_adx = self._config.get("strategy.mr_max_adx", 18)
            gray_low = mr_max_adx      # 18
            gray_high = self._config.get("strategy.adx_regime_strong_trend", 25)

            # Separate coins into pools based on ADX from trend scoring
            for r in results:
                adx = r.adx if hasattr(r, 'adx') else 0
                if adx < gray_low:
                    # Net range: ADX < 18
                    mr_symbols[r.symbol] = "R"
                elif adx < gray_high:
                    # Gray zone: ADX 18-25 — classify via voting
                    indicators = r.indicator_values if hasattr(r, 'indicator_values') else {}
                    zone = self._mr_scorer.classify_gray_zone(indicators)
                    if zone == "RANGE":
                        mr_symbols[r.symbol] = "G->R"

            if mr_symbols:
                mr_klines = {s: klines_map[s] for s in mr_symbols if s in klines_map}
                mr_source_map = {s: src for s, src in mr_symbols.items() if s in mr_klines}
                mr_results = self._mr_scorer.score_batch(
                    mr_klines, market_ctx, mr_source_map)

                # Enrich with leverage and timeframe
                if dynamic_tf:
                    for r in mr_results:
                        coin_info = self._tf_selector.get_coin_info(r.symbol)
                        if coin_info:
                            r.leverage = coin_info.max_leverage
                            r.timeframe = coin_info.optimal_tf
                        else:
                            r.timeframe = symbol_intervals.get(r.symbol, default_interval)

                logger.info(f"MR pool: {len(mr_symbols)} coins, "
                            f"{sum(1 for r in mr_results if r.eligible)} eligible")

        self._last_mr_results = mr_results

        # Remove MR-routed coins from TREND results (each coin belongs to one pool only)
        if mr_symbols:
            results = [r for r in results if r.symbol not in mr_symbols]

        self._last_scan_results = results
        logger.info(f"Scan results: {len(results)} trend, "
                    f"{len([r for r in results if r.eligible])} eligible, "
                    f"{len(mr_results)} MR")

        # 5. Find best eligible candidate
        now = time.time()
        self._failed_symbols = {
            s: t for s, t in self._failed_symbols.items()
            if now - t < self._failed_cooldown
        }
        # Expire old loss cooldowns
        self._loss_cooldown_symbols = {
            s: t for s, t in self._loss_cooldown_symbols.items()
            if now - t < self._loss_cooldown_seconds
        }

        # Periodic cleanup: coin loss history (remove entries older than ban window)
        ban_hours = self._config.get("strategy.coin_daily_ban_hours", 24)
        ban_cutoff = now - (ban_hours * 3600)
        for sym in list(self._coin_loss_history.keys()):
            self._coin_loss_history[sym] = [
                t for t in self._coin_loss_history[sym] if t > ban_cutoff
            ]
            if not self._coin_loss_history[sym]:
                del self._coin_loss_history[sym]

        # Cleanup: expired close retries (stale entries from permanently failed closes)
        for sym in list(self._close_retries.keys()):
            if sym not in self._position_mgr.get_held_symbols():
                self._close_retries.pop(sym, None)

        # Cleanup: stale server trailing entries (orphaned)
        for sym in list(self._server_trailing.keys()):
            if sym not in self._position_mgr.get_held_symbols():
                self._server_trailing.pop(sym, None)

        # Cleanup: stale held indicators
        for sym in list(self._held_indicators.keys()):
            if sym not in self._position_mgr.get_held_symbols():
                self._held_indicators.pop(sym, None)

        eligible = [r for r in results if r.eligible]
        min_score = self._config.get("strategy.min_buy_score", 55)

        # Log top 5 scores for debugging (eligible or not)
        for i, r in enumerate(results[:5]):
            fr_str = f" FR={r.funding_rate*100:+.3f}%" if r.funding_rate else ""
            oi_str = f" OI={r.oi_change_pct:+.1f}%" if r.oi_change_pct else ""
            logger.info(f"  #{i+1} {r.symbol}: score={r.score:+.1f} "
                        f"dir={r.direction} eligible={r.eligible}"
                        f"{fr_str}{oi_str} "
                        f"reject={r.reject_reason or '-'}")
        if eligible:
            logger.info(f"  Eligible: {len(eligible)} symbols, "
                        f"top={eligible[0].symbol} score={eligible[0].score:+.1f}")

        # Collect all valid candidates (not just the first one)
        candidates = []
        for r in eligible:
            if abs(r.score) >= min_score:
                if r.symbol in self._failed_symbols:
                    logger.debug(f"Skipping {r.symbol} (recently failed)")
                    continue
                if r.symbol in self._loss_cooldown_symbols:
                    remaining = self._loss_cooldown_seconds - (now - self._loss_cooldown_symbols[r.symbol])
                    logger.info(f"Skipping {r.symbol} (loss cooldown, {remaining:.0f}s kaldi)")
                    continue
                if self._position_mgr.is_holding(r.symbol):
                    continue
                if r.symbol in self._pending_limits:
                    continue  # limit order pending for this symbol
                # Per-coin daily loss ban check
                coin_ok, coin_reason = self._check_coin_daily_ban(r.symbol)
                if not coin_ok:
                    logger.info(f"Skipping {r.symbol} ({coin_reason})")
                    continue
                # ATR safety check: skip coins where no timeframe provides safe ATR
                # Math: if ATR > target at every timeframe, SL will be too tight
                if dynamic_tf:
                    coin_info = self._tf_selector.get_coin_info(r.symbol)
                    if coin_info and not coin_info.is_safe:
                        logger.info(f"Skipping {r.symbol}: ATR unsafe at all timeframes "
                                    f"(best={coin_info.optimal_tf} "
                                    f"ATR={coin_info.optimal_atr_pct:.3f}% "
                                    f"> target={coin_info.target_atr_pct:.3f}%)")
                        continue
                candidates.append(r)

        # === MR CANDIDATES ===
        mr_candidates = []
        if mr_enabled and mr_results:
            mr_eligible = [r for r in mr_results if r.eligible]
            mr_min_score = self._config.get("strategy.mr_min_score", 65)
            mr_max_pos = self._config.get("strategy.mr_max_positions", 2)

            for r in mr_eligible:
                if abs(r.score) < mr_min_score:
                    continue
                if r.symbol in self._failed_symbols:
                    continue
                if r.symbol in self._loss_cooldown_symbols:
                    continue
                if self._position_mgr.is_holding(r.symbol):
                    continue
                if r.symbol in self._pending_limits:
                    continue
                coin_ok, coin_reason = self._check_coin_daily_ban(r.symbol)
                if not coin_ok:
                    continue
                # Check MR slot limit
                if self._position_mgr.get_mr_position_count() >= mr_max_pos:
                    break
                mr_candidates.append(r)

            if mr_eligible:
                logger.info(f"  MR eligible: {len(mr_eligible)} symbols, "
                            f"candidates: {len(mr_candidates)}, "
                            f"top={mr_eligible[0].symbol if mr_eligible else '-'} "
                            f"score={mr_eligible[0].score:+.1f}" if mr_eligible else "")

        # Publish scan results for GUI
        self._event_bus.publish(EventType.SCANNER_UPDATE, {
            "scan_count": self._scan_count,
            "total_symbols": len(symbols),
            "scored": len(results),
            "eligible": len(eligible),
            "mr_scored": len(mr_results),
            "mr_eligible": len([r for r in mr_results if r.eligible]) if mr_results else 0,
            "positions": self._position_mgr.position_count,
            "max_positions": self._position_mgr.max_positions,
            "top_5": [
                {"symbol": r.symbol, "score": r.score, "direction": r.direction,
                 "regime": r.regime.get("regime", "?"),
                 "confluence": r.confluence.get("score", 0)}
                for r in results[:5]
            ],
            "candidate": candidates[0].symbol if candidates else None,
            "mr_candidate": mr_candidates[0].symbol if mr_candidates else None,
        })

        close_only = self._config.get("strategy.close_only", False)
        if close_only:
            if self._position_mgr.has_position:
                logger.info(f"Close-only mode: monitoring {self._position_mgr.position_count} position(s), no new trades")
            else:
                logger.info("Close-only mode: no positions left, waiting...")
            scan_interval = self._config.get("strategy.scan_interval_seconds", 30)
            self._wait(scan_interval)
            return

        if candidates and self._position_mgr.has_capacity:
            # Check available margin before attempting buys
            real_balance = 0.0
            if self._order_executor and hasattr(self._order_executor, "get_balance"):
                try:
                    real_balance = self._order_executor.get_balance()
                except Exception:
                    pass
            if real_balance > 0 and real_balance < 0.30:
                logger.info(f"Available balance too low ({real_balance:.2f}$), waiting for positions to close")
                scan_interval = self._config.get("strategy.scan_interval_seconds", 30)
                self._wait(scan_interval)
                return

            # Buy as many candidates as we have capacity for
            bought_any = False
            for candidate in candidates:
                # Count pending limits as occupied slots
                total_occupied = self._position_mgr.position_count + len(self._pending_limits)
                if total_occupied >= self._position_mgr.max_positions:
                    break
                if not self._check_trade_frequency():
                    break

                # Direction balance check: enforce long/short ratio
                dir_ok, dir_reason = self._check_direction_balance(candidate.direction)
                if not dir_ok:
                    logger.info(f"Skipping {candidate.symbol} ({dir_reason})")
                    continue

                # ADX regime: MTF confirmation check
                if getattr(candidate, 'adx_regime', '') and candidate.adx_regime != "NO_TRADE":
                    mtf_ok, mtf_reason = self._check_mtf_confirmation(candidate)
                    if not mtf_ok:
                        logger.info(f"Skipping {candidate.symbol}: {mtf_reason}")
                        continue

                cand_tf = symbol_intervals.get(candidate.symbol, default_interval) if dynamic_tf else default_interval
                logger.info(f"Candidate found: {candidate.symbol} "
                            f"score={candidate.score:+.1f} dir={candidate.direction} "
                            f"regime={candidate.regime.get('regime')} "
                            f"adx_regime={getattr(candidate, 'adx_regime', '')} "
                            f"confluence={candidate.confluence.get('score', 0):+.1f} "
                            f"tf={cand_tf}")
                self._last_candidate = candidate
                if self._do_buying_inline():
                    bought_any = True
                else:
                    continue  # try next candidate (might be coin-specific issue)

            # === MR BUYING (after trend, if capacity remains) ===
            if mr_candidates and self._position_mgr.has_capacity:
                mr_max_pos = self._config.get("strategy.mr_max_positions", 2)
                for mr_cand in mr_candidates:
                    total_occupied = self._position_mgr.position_count + len(self._pending_limits)
                    if total_occupied >= self._position_mgr.max_positions:
                        break
                    if self._position_mgr.get_mr_position_count() >= mr_max_pos:
                        break
                    if not self._check_trade_frequency():
                        break

                    dir_ok, dir_reason = self._check_direction_balance(mr_cand.direction)
                    if not dir_ok:
                        logger.info(f"Skipping MR {mr_cand.symbol} ({dir_reason})")
                        continue

                    logger.info(f"[MR] Candidate: {mr_cand.symbol} "
                                f"score={mr_cand.score:+.1f} dir={mr_cand.direction} "
                                f"BB={mr_cand.bb_percent_b:.0%} RSI={mr_cand.rsi:.0f} "
                                f"Vol={mr_cand.volume_ratio:.1f}x src={mr_cand.source}")
                    self._last_candidate = mr_cand
                    if self._do_mr_buying_inline(mr_cand):
                        bought_any = True

            # Short wait then scan again if still have capacity
            if self._position_mgr.has_capacity and bought_any:
                self._wait(5)
            else:
                scan_interval = self._config.get("strategy.scan_interval_seconds", 30)
                self._wait(scan_interval)
        else:
            # Also try MR buying even without trend candidates
            if mr_candidates and self._position_mgr.has_capacity:
                # Check balance
                real_balance = 0.0
                if self._order_executor and hasattr(self._order_executor, "get_balance"):
                    try:
                        real_balance = self._order_executor.get_balance()
                    except Exception:
                        pass
                if real_balance <= 0 or real_balance >= 0.30:
                    mr_max_pos = self._config.get("strategy.mr_max_positions", 2)
                    bought_any = False
                    for mr_cand in mr_candidates:
                        total_occupied = self._position_mgr.position_count + len(self._pending_limits)
                        if total_occupied >= self._position_mgr.max_positions:
                            break
                        if self._position_mgr.get_mr_position_count() >= mr_max_pos:
                            break
                        if not self._check_trade_frequency():
                            break

                        dir_ok, dir_reason = self._check_direction_balance(mr_cand.direction)
                        if not dir_ok:
                            continue

                        logger.info(f"[MR] Candidate: {mr_cand.symbol} "
                                    f"score={mr_cand.score:+.1f} dir={mr_cand.direction} "
                                    f"BB={mr_cand.bb_percent_b:.0%} RSI={mr_cand.rsi:.0f}")
                        self._last_candidate = mr_cand
                        if self._do_mr_buying_inline(mr_cand):
                            bought_any = True

                    if bought_any:
                        self._wait(5)
                        return

            if not self._position_mgr.has_capacity:
                logger.info(f"Max positions reached ({self._position_mgr.position_count}/"
                            f"{self._position_mgr.max_positions}). Monitoring only.")
            scan_interval = self._config.get("strategy.scan_interval_seconds", 30)
            self._wait(scan_interval)

    # ──── Focus Mode Monitoring ────

    def _do_focus_monitoring(self) -> None:
        """Focus mode: only monitor open positions, no new scanning.
        Checks every 2 seconds with full indicator analysis."""
        symbols = list(self._position_mgr.get_held_symbols())
        if not symbols:
            return

        for symbol in symbols:
            try:
                ticker = self._rest.get_ticker_price(symbol)
                current_price = float(ticker.get("price", 0))
                if current_price <= 0:
                    continue
            except Exception:
                continue

            # Full indicator analysis for held position (using position's timeframe)
            indicators = {}
            confluence = {}
            regime = {}
            divergences = []
            try:
                pos = self._position_mgr.get_position(symbol)
                interval = pos.timeframe if pos else self._config.get("indicators.kline_interval", "1m")
                klines = self._rest.get_klines(symbol, interval, limit=200)
                if klines and len(klines) > 50:
                    indicators = self._hold_engine.compute_all(klines)
                    confluence = self._hold_confluence.score(indicators)
                    regime = self._hold_regime.detect(indicators)
                    if self._config.get("strategy.divergence_exit_enabled", False):
                        ind_series = {}
                        for name in ["RSI", "OBV"]:
                            ind = self._hold_engine.get_indicator(name)
                            if ind and ind._series is not None:
                                ind_series[name] = ind._series
                        divergences = self._hold_divergence.detect_all(klines, ind_series)
            except Exception as e:
                logger.debug(f"Focus analysis error for {symbol}: {e}")

            # Save current indicators for GUI
            if indicators or confluence:
                self._held_indicators[symbol] = {
                    "indicators": indicators,
                    "confluence": confluence,
                    "price": current_price,
                }

            exit_reason = self._position_mgr.check_position(
                symbol, current_price,
                confluence=confluence,
                regime=regime,
                divergences=divergences,
            )

            # KIRMIZI KURAL 3: Server emirleri pozisyon açıkken DEĞİŞTİRİLMEZ.
            # İlk konulan SL + trailing pozisyon kapanana kadar durur.

            if exit_reason == self._position_mgr.EXIT_PARTIAL_TP:
                pos_obj = self._position_mgr.get_position(symbol)
                if pos_obj and pos_obj.entry_mode == "SYSTEM_F":
                    self._execute_partial_tp_system_f(symbol, current_price)
                else:
                    self._execute_partial_tp(symbol, current_price)
                continue  # Don't full-close, position stays open

            if exit_reason != "HOLD":
                logger.info(f"[FOCUS] Exit signal for {symbol}: "
                            f"{exit_reason} @ {current_price:.6f}")
                self._sell_position(symbol, current_price, exit_reason)
                # After closing, will start scanning again next cycle
                return

        # Still holding — publish position update for GUI
        pos = self._position_mgr.position
        if pos:
            self._event_bus.publish(EventType.SCANNER_UPDATE, {
                "scan_count": self._scan_count,
                "total_symbols": 0,
                "scored": 0,
                "eligible": 0,
                "positions": self._position_mgr.position_count,
                "max_positions": self._position_mgr.max_positions,
                "top_5": [],
                "candidate": None,
                "focus_mode": True,
                "focus_symbol": pos.symbol,
            })

        # Short sleep — focus mode checks more frequently
        time.sleep(2)

    # ──── Market Context (Funding Rate + Open Interest) ────

    def _fetch_funding_rates(self, symbols: list[str]) -> dict[str, dict]:
        """Phase 1: Fetch funding rates for ALL symbols (1 API call).
        Returns: {symbol: {funding_rate, oi_change_pct: 0, ...}}"""
        ctx = {}
        try:
            premium_data = self._rest.get_all_premium_index()
            funding_map = {}
            for item in premium_data:
                sym = item.get("symbol", "")
                fr = float(item.get("lastFundingRate", 0))
                funding_map[sym] = fr

            for sym in symbols:
                ctx[sym] = {
                    "funding_rate": funding_map.get(sym, 0.0),
                    "oi_change_pct": 0.0,
                }
        except Exception as e:
            logger.debug(f"Funding rate fetch failed: {e}")
            for sym in symbols:
                ctx[sym] = {"funding_rate": 0.0, "oi_change_pct": 0.0}
        return ctx

    def _fetch_oi_depth(self, symbols: list[str], ctx: dict[str, dict]) -> None:
        """Phase 2: Fetch OI history + OrderBook depth for given symbols (mutates ctx).
        Called after preliminary scoring so only top candidates get detailed data.
        Uses ThreadPoolExecutor for parallel fetching."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        ticker_data = self._universe.get_all_tickers()
        for sym in symbols:
            if sym not in ctx:
                ctx[sym] = {"funding_rate": 0.0, "oi_change_pct": 0.0}

        def _fetch_one(sym):
            """Fetch OI + depth for a single symbol."""
            result = {}
            # OI change
            try:
                oi_hist = self._rest.get_open_interest_hist(sym, period="5m", limit=6)
                if oi_hist and len(oi_hist) >= 2:
                    oldest_val = float(oi_hist[0].get("sumOpenInterestValue", 0))
                    latest_val = float(oi_hist[-1].get("sumOpenInterestValue", 0))
                    if oldest_val > 0:
                        result["oi_change_pct"] = round(
                            ((latest_val - oldest_val) / oldest_val) * 100, 2)
            except Exception:
                pass
            # Order Book depth (10 levels — sufficient for wall detection)
            try:
                depth = self._rest.get_depth(sym, limit=20)
                vol_24h = ticker_data.get(sym, {}).get("volume_24h", 0)
                thin_sec = self._config.get("strategy.thin_book_seconds", 5.0)
                result["ob"] = self._ob_analyzer.analyze(
                    depth, volume_24h=vol_24h, thin_book_seconds=thin_sec)
            except Exception:
                pass
            return sym, result

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_fetch_one, sym): sym for sym in symbols}
            for future in as_completed(futures):
                try:
                    sym, result = future.result()
                    if "oi_change_pct" in result:
                        ctx[sym]["oi_change_pct"] = result["oi_change_pct"]
                    ob = result.get("ob")
                    if ob:
                        ctx[sym]["ob_imbalance"] = ob.get("weighted_imbalance", 0.0)
                        ctx[sym]["ob_wall_signal"] = ob.get("wall_signal", "NONE")
                        ctx[sym]["ob_liquidity"] = ob.get("liquidity_score", 0.0)
                        ctx[sym]["ob_thin_book"] = ob.get("thin_book", False)
                        ask_wall = ob.get("ask_wall")
                        bid_wall = ob.get("bid_wall")
                        ctx[sym]["ob_wall_seconds"] = 0.0
                        if ask_wall and ob.get("wall_signal") == "UP_BLOCKED":
                            ctx[sym]["ob_wall_seconds"] = ask_wall.get("wall_seconds", 9999.0)
                        elif bid_wall and ob.get("wall_signal") == "DOWN_BLOCKED":
                            ctx[sym]["ob_wall_seconds"] = bid_wall.get("wall_seconds", 9999.0)
                        ctx[sym]["ob_ask_depth_seconds"] = ob.get("ask_depth_seconds", 0.0)
                        ctx[sym]["ob_bid_depth_seconds"] = ob.get("bid_depth_seconds", 0.0)
                except Exception:
                    pass

    # ──── Multi-Timeframe Analysis ────

    @staticmethod
    def _get_upper_tfs(base_tf: str) -> tuple:
        """Get 2-up and 5-up timeframes from base on the TF ladder."""
        try:
            idx = TF_LADDER.index(base_tf)
        except ValueError:
            return "1h", "4h"
        tf_2up = TF_LADDER[min(idx + 2, len(TF_LADDER) - 1)]
        tf_5up = TF_LADDER[min(idx + 5, len(TF_LADDER) - 1)]
        return tf_2up, tf_5up

    def _fetch_mtf_data(self, results: list) -> None:
        """Fetch upper-timeframe indicators for top 5 results.
        Populates result.mtf_data = {tf: {indicators, confluence, signal}} for each."""
        mtf_engine = IndicatorEngine(self._config)
        mtf_confluence = ConfluenceScorer(threshold=4.0, config=self._config)

        for r in results[:5]:
            base_tf = getattr(r, 'timeframe', '1m')
            tf_2up, tf_5up = self._get_upper_tfs(base_tf)
            mtf = {}

            for tf in (tf_2up, tf_5up):
                if tf == base_tf:
                    continue  # skip if clamped to same TF
                try:
                    klines = self._rest.get_klines(r.symbol, tf, limit=200)
                    if klines is not None and len(klines) > 50:
                        indicators = mtf_engine.compute_all(klines)
                        confluence = mtf_confluence.score(indicators)
                        conf_score = confluence.get("score", 0)
                        if conf_score >= 0:
                            signal = "LONG"
                        else:
                            signal = "SHORT"
                        mtf[tf] = {
                            "indicators": indicators,
                            "confluence": confluence,
                            "signal": signal,
                        }
                except Exception as e:
                    logger.debug(f"MTF fetch error {r.symbol}@{tf}: {e}")

            r.mtf_data = mtf

    def _check_mtf_confirmation(self, candidate) -> tuple:
        """Check if 2-up and 5-up timeframes confirm the candidate direction.
        Returns (ok, reason). If MTF data is missing, passes gracefully."""
        strat = self._config.get("strategy", {})
        if not strat.get("adx_regime_mtf_required", True):
            return True, ""

        mtf = getattr(candidate, 'mtf_data', {}) or {}
        if not mtf:
            # MTF data not available (coin not in top 5) — skip check
            return True, ""

        base_tf = getattr(candidate, 'timeframe', '1m')
        tf_2up, tf_5up = self._get_upper_tfs(base_tf)
        direction = candidate.direction  # "LONG" or "SHORT"

        for label, tf in [("2up", tf_2up), ("5up", tf_5up)]:
            if tf == base_tf:
                continue  # clamped to same TF, skip
            entry = mtf.get(tf)
            if not entry:
                continue  # data not available, skip
            mtf_signal = entry.get("signal", "")
            if mtf_signal and mtf_signal != direction:
                return False, f"mtf_{label}_{tf}_conflict ({mtf_signal} vs {direction})"

        return True, ""

    # ──── Pending Limit Order Management ────

    def _check_pending_limits(self) -> None:
        """Check all pending limit orders for fill or timeout.
        Called every scan cycle."""
        if not self._pending_limits:
            return

        now = time.time()
        filled = []
        expired = []

        # Fetch all open orders once, then filter per symbol
        try:
            all_open_orders = self._rest.get_open_orders() or []
        except Exception:
            all_open_orders = []

        for symbol, info in list(self._pending_limits.items()):
            elapsed = now - info["placed_time"]

            # Check if order is filled via API (using pre-fetched orders)
            try:
                orders = [o for o in all_open_orders if o.get("symbol") == symbol]
                # If our limit order is no longer in open orders, it was filled
                has_open_limit = any(
                    o.get("type") == "LIMIT" and o.get("status") in ("NEW", "PARTIALLY_FILLED")
                    for o in orders
                )

                if not has_open_limit:
                    # Order filled (or cancelled externally)
                    # Check if position actually exists
                    if self._order_executor and hasattr(self._order_executor, '_has_open_position'):
                        if self._order_executor._has_open_position(symbol):
                            filled.append(symbol)
                        else:
                            # Order was cancelled externally, clean up
                            expired.append(symbol)
                            logger.info(f"Limit order {symbol} cancelled externally")
                    else:
                        filled.append(symbol)
                    continue

            except Exception as e:
                logger.debug(f"Check pending limit {symbol}: {e}")

            # Check timeout
            if elapsed >= info["timeout"]:
                expired.append(symbol)

        # Handle filled orders
        for symbol in filled:
            self._on_limit_filled(symbol)

        # Handle expired orders
        for symbol in expired:
            self._on_limit_expired(symbol)

    def _on_limit_filled(self, symbol: str) -> None:
        """Handle a filled limit order: recheck signal, then open position tracking."""
        info = self._pending_limits.pop(symbol, None)
        if not info:
            return

        candidate = info["candidate"]
        strat = self._config.get("strategy", {})
        recheck = strat.get("limit_recheck_signal", True)

        fill_price = info["limit_price"]  # approximate fill price
        # Try to get actual fill price from API
        try:
            ticker = self._rest.get_ticker_price(symbol)
            fill_price = float(ticker.get("price", fill_price))
        except Exception:
            pass

        logger.info(f"Limit order FILLED: {symbol} @ ~{fill_price:.6f}")

        # System J: skip signal recheck, directly open position
        if info.get("entry_mode") == "SYSTEM_J":
            self._on_limit_filled_system_j(symbol, fill_price, info)
            return

        # System E: skip signal recheck, directly open position
        if info.get("entry_mode") == "SYSTEM_E":
            self._on_limit_filled_system_e(symbol, info, fill_price)
            return

        # System I: skip signal recheck, directly open position
        if info.get("entry_mode") == "SYSTEM_I":
            self._on_limit_filled_system_i(symbol, info, fill_price)
            return

        # System H: skip signal recheck, directly open position
        if info.get("entry_mode") == "SYSTEM_H":
            self._on_limit_filled_system_h(symbol, info, fill_price)
            return

        # System D: skip signal recheck, directly open position
        if info.get("entry_mode") == "SYSTEM_D":
            self._on_limit_filled_system_d(symbol, info, fill_price)
            return

        # Signal recheck: only close if STRONG REVERSAL (opposite direction signal)
        # LONG pozisyonda iken → sadece SHORT sinyali (conf < -threshold) gelirse kapat
        # Nötr veya zayıf sinyal → devam et (fee kaybını önle)
        if recheck:
            try:
                tf = info.get("timeframe", self._config.get("indicators.kline_interval", "5m"))
                klines = self._rest.get_klines(symbol, tf, limit=200)
                if klines is not None and not klines.empty:
                    result = self._scorer.score_symbol(symbol, klines)
                    conf_score = result.confluence.get("score", 0)
                    recheck_threshold = strat.get("min_confluence", 4.0)

                    # LONG iken sadece güçlü SHORT sinyali varsa kapat
                    if info["direction"] == "LONG" and conf_score <= -recheck_threshold:
                        logger.warning(f"Limit filled but STRONG REVERSAL for {symbol} "
                                       f"(conf={conf_score:.1f} <= -{recheck_threshold:.0f}, "
                                       f"SHORT sinyali), closing immediately")
                        self._cancel_limit_position(symbol, info)
                        return
                    # SHORT iken sadece güçlü LONG sinyali varsa kapat
                    elif info["direction"] == "SHORT" and conf_score >= recheck_threshold:
                        logger.warning(f"Limit filled but STRONG REVERSAL for {symbol} "
                                       f"(conf={conf_score:.1f} >= +{recheck_threshold:.0f}, "
                                       f"LONG sinyali), closing immediately")
                        self._cancel_limit_position(symbol, info)
                        return

                    logger.info(f"Signal recheck OK: {symbol} conf={conf_score:.1f} "
                                f"(no strong reversal, proceeding)")
            except Exception as e:
                logger.warning(f"Signal recheck failed for {symbol}: {e}, proceeding anyway")

        # Open position tracking
        pos_tf = self._tf_selector.get_timeframe(symbol) if \
            self._config.get("strategy", {}).get("dynamic_timeframe", True) else \
            self._config.get("indicators.kline_interval", "1m")

        regime_info = getattr(candidate, 'regime', {}) or {}
        entry_mode = info.get("entry_mode", "TREND")
        mr_tp = info.get("mr_tp_price", 0.0)
        conf_score_val = (candidate.confluence.get("score", 0)
                          if hasattr(candidate.confluence, 'get') else 0)
        self._position_mgr.open_position(
            symbol, info["side"], fill_price, info["size"], info["atr"],
            leverage=info["leverage"],
            margin_usdt=info["margin_usdt"],
            timeframe=pos_tf,
            entry_score=candidate.score,
            entry_confluence=conf_score_val,
            entry_adx=candidate.adx,
            entry_rsi=candidate.rsi,
            entry_regime=regime_info.get("regime", "") if hasattr(regime_info, 'get') else "",
            entry_regime_confidence=regime_info.get("confidence", 0) if hasattr(regime_info, 'get') else 0,
            entry_bb_width=regime_info.get("bb_width", 0) if hasattr(regime_info, 'get') else 0,
            entry_mode=entry_mode,
            mr_tp_price=mr_tp,
        )

        if self._risk_manager:
            self._risk_manager.record_order(
                info["size"], fill_price,
                margin_usdt=info["margin_usdt"] if info["lev_enabled"] else None,
            )

        # Log filled order
        if self._order_logger:
            self._order_logger.log_order(
                symbol=symbol, side=info["side"].value, order_type="Limit",
                price=fill_price, size=info["size"],
                notional_usdt=info["size"] * fill_price,
                status="filled",
                trigger_source=f"limit_filled:{candidate.score:+.0f}",
            )

        # Place initial server-side trailing stop — her pozisyona emir konulur
        if self._order_executor and hasattr(self._order_executor, '_rest'):
            pos_obj = self._position_mgr.get_position(symbol)
            if pos_obj:
                self._place_initial_trailing(symbol, pos_obj, fill_price, info["atr"])

        self._event_bus.publish(EventType.ORDER_PLACED, {
            "symbol": symbol, "side": info["side"].value,
            "size": info["size"], "price": fill_price,
            "order_type": "LIMIT_FILLED",
        })

    def _on_limit_expired(self, symbol: str) -> None:
        """Handle an expired limit order: cancel on exchange, then try market fallback
        if signal is still strong enough (market_fallback_on_limit_timeout)."""
        info = self._pending_limits.pop(symbol, None)
        if not info:
            return

        elapsed = time.time() - info["placed_time"]
        logger.info(f"Limit order EXPIRED: {symbol} after {elapsed:.0f}s, cancelling")

        # Cancel on exchange
        try:
            self._rest.cancel_all_orders(symbol)
        except Exception as e:
            logger.warning(f"Cancel limit order failed for {symbol}: {e}")

        # System D: no market fallback, just cancel and move on
        if info.get("entry_mode") == "SYSTEM_D":
            logger.info(f"[SysD] Limit expired for {symbol}, no fallback (next scan will re-evaluate)")
            return

        # Market fallback: if FRESH signal still valid AND price hasn't drifted too far
        strat = self._config.get("strategy", {})
        fallback_enabled = strat.get("market_fallback_on_limit_timeout", True)

        if fallback_enabled and self._order_executor:
            candidate = info.get("candidate")
            if not candidate:
                logger.info(f"Market fallback skipped for {symbol}: no candidate info")
            else:
                # --- 1) Fresh signal check: recalculate score from live klines ---
                fresh_score = None
                try:
                    tf = info.get("timeframe",
                                  self._config.get("indicators.kline_interval", "5m"))
                    klines = self._rest.get_klines(symbol, tf, limit=200)
                    if klines is not None and not klines.empty:
                        result = self._scorer.score_symbol(symbol, klines)
                        fresh_score = result.confluence.get("score", 0)
                except Exception as e:
                    logger.warning(f"Market fallback fresh signal check failed for {symbol}: {e}")

                min_score = strat.get("min_buy_score", 70)
                min_conf = strat.get("min_confluence", 4.0)

                if fresh_score is None:
                    logger.info(f"Market fallback skipped for {symbol}: "
                                f"could not compute fresh signal")
                elif info["direction"] == "LONG" and fresh_score < min_conf:
                    logger.info(f"Market fallback skipped for {symbol}: "
                                f"fresh confluence too weak for LONG "
                                f"(conf={fresh_score:.1f} < {min_conf:.0f})")
                elif info["direction"] == "SHORT" and fresh_score > -min_conf:
                    logger.info(f"Market fallback skipped for {symbol}: "
                                f"fresh confluence too weak for SHORT "
                                f"(conf={fresh_score:.1f} > -{min_conf:.0f})")
                else:
                    # --- 2) Price drift check: don't market-enter if price moved > N×ATR ---
                    max_drift_atr = strat.get("market_fallback_max_drift_atr", 1.5)
                    atr = info.get("atr", 0)
                    limit_price = info["limit_price"]
                    try:
                        ticker = self._rest.get_ticker_price(symbol)
                        current_price = float(ticker.get("price", limit_price))
                    except Exception:
                        current_price = limit_price

                    drift = abs(current_price - limit_price)
                    max_drift = atr * max_drift_atr if atr > 0 else float("inf")

                    if drift > max_drift:
                        logger.info(
                            f"Market fallback skipped for {symbol}: "
                            f"price drifted too far from limit "
                            f"(drift={drift:.6f} > {max_drift:.6f}, "
                            f"{max_drift_atr:.1f}×ATR)")
                    else:
                        logger.info(
                            f"Limit expired, fresh signal valid "
                            f"(conf={fresh_score:.1f}) and price drift OK "
                            f"(drift={drift:.6f} <= {max_drift:.6f}), "
                            f"falling back to MARKET: {symbol}")
                        try:
                            success = self._order_executor.execute_order(
                                symbol=symbol, side=info["side"],
                                order_type=OrderType.MARKET,
                                size=info["size"], tp_percent=0, sl_percent=0,
                                leverage=info.get("leverage", 1),
                                qty_precision=info.get("qty_precision", 3),
                                ensure_isolated=(info.get("lev_enabled", False) and
                                                 self._config.get("leverage.mode", "isolated")
                                                 == "isolated"),
                            )
                            if success:
                                info["market_fallback"] = True
                                self._pending_limits[symbol] = info
                                self._on_limit_filled(symbol)

                                if self._order_logger:
                                    self._order_logger.log_order(
                                        symbol=symbol, side=info["side"].value,
                                        order_type="Market",
                                        price=current_price, size=info["size"],
                                        notional_usdt=info["size"] * current_price,
                                        status="filled",
                                        trigger_source="limit_market_fallback",
                                    )
                                return  # Successfully fell back to market
                            else:
                                logger.warning(f"Market fallback failed for {symbol}")
                        except Exception as e:
                            logger.error(f"Market fallback error for {symbol}: {e}")

        # Log cancellation (no fallback or fallback failed)
        if self._order_logger:
            self._order_logger.log_order(
                symbol=symbol, side=info["side"].value, order_type="Limit",
                price=info["limit_price"], size=info["size"],
                notional_usdt=0,
                status="cancelled",
                trigger_source="limit_timeout",
            )

    def _cancel_limit_position(self, symbol: str, info: dict) -> None:
        """Signal reversed after limit fill — close the position immediately."""
        try:
            if self._order_executor and hasattr(self._order_executor, "close_position"):
                self._order_executor.close_position(symbol, info["side"], info["size"])
            else:
                close_side = (OrderSide.SELL_SHORT if info["side"] == OrderSide.BUY_LONG
                              else OrderSide.BUY_LONG)
                if self._order_executor:
                    self._order_executor.execute_order(
                        symbol=symbol, side=close_side,
                        order_type=OrderType.MARKET,
                        size=info["size"], reduce_only=True,
                        qty_precision=info.get("qty_precision", 3),
                    )
            logger.info(f"Limit position closed (signal reversed): {symbol}")
        except Exception as e:
            logger.error(f"Failed to close reversed limit position {symbol}: {e}")

    # ──── Check Held Positions ────

    def _check_held_positions(self) -> None:
        """Check all held positions for exit signals with full indicator analysis.
        Also detects positions closed externally (manual close, server SL/TP)."""

        # === Detect externally closed positions ===
        self._detect_external_closes()

        # === Verify server orders (SL + trailing) for held positions ===
        self._verify_server_orders()

        for symbol in list(self._position_mgr.get_held_symbols()):
            # Skip symbols currently being sold by monitor thread
            if symbol in self._selling_symbols:
                continue

            # System M/N pozisyonları burada işlenmez — kendi sinyal bazlı çıkışı var
            pos_check = self._position_mgr.get_position(symbol)
            if pos_check and getattr(pos_check, 'entry_mode', '') in ("SYSTEM_M", "SYSTEM_N"):
                continue

            try:
                ticker = self._rest.get_ticker_price(symbol)
                current_price = float(ticker.get("price", 0))
            except Exception as e:
                logger.warning(f"Price fetch failed for held {symbol}: {e}")
                continue

            # Full indicator analysis using position's timeframe
            indicators = {}
            confluence = {}
            divergences = []
            try:
                pos = self._position_mgr.get_position(symbol)
                interval = pos.timeframe if pos else "1m"
                klines = self._rest.get_klines(symbol, interval, limit=200)
                if klines is not None and len(klines) > 50:
                    indicators = self._hold_engine.compute_all(klines)
                    confluence = self._hold_confluence.score(indicators)
                    if self._config.get("strategy.divergence_exit_enabled", False):
                        ind_series = {}
                        for name in ["RSI", "OBV"]:
                            ind = self._hold_engine.get_indicator(name)
                            if ind and ind._series is not None:
                                ind_series[name] = ind._series
                        divergences = self._hold_divergence.detect_all(klines, ind_series)
            except Exception as e:
                logger.warning(f"Indicator analysis failed for held {symbol}: {e}")

            # Save current indicators for GUI
            if indicators or confluence:
                self._held_indicators[symbol] = {
                    "indicators": indicators,
                    "confluence": confluence,
                    "price": current_price,
                }
                logger.debug(f"Held indicators updated for {symbol}: "
                             f"conf_score={confluence.get('score', 'N/A')}, "
                             f"ind_count={len(indicators)}")
            else:
                logger.warning(f"No indicators computed for held {symbol} "
                               f"(klines may have failed or returned <50 candles)")

            # System I: güncel rejimi hesapla ve pozisyona yaz (regime shift çıkışı için)
            pos = self._position_mgr.get_position(symbol)
            if pos and getattr(pos, 'entry_mode', '') == "SYSTEM_I" and self._system_i_scanner:
                try:
                    tf = pos.timeframe or "5m"
                    klines_regime = self._rest.get_klines(symbol, tf, limit=200)
                    if klines_regime and len(klines_regime) >= 20:
                        regime_r = self._system_i_scanner.compute_regime(
                            symbol, klines_regime, direction_result=None)
                        pos.current_regime = regime_r.regime
                except Exception:
                    pass

            exit_reason = self._position_mgr.check_position(
                symbol, current_price,
                confluence=confluence,
                divergences=divergences,
            )

            # KIRMIZI KURAL 3: Server emirleri pozisyon açıkken DEĞİŞTİRİLMEZ.

            if exit_reason == self._position_mgr.EXIT_PARTIAL_TP:
                pos = self._position_mgr.get_position(symbol)
                if pos and pos.entry_mode == "SYSTEM_F":
                    self._execute_partial_tp_system_f(symbol, current_price)
                else:
                    self._execute_partial_tp(symbol, current_price)
                continue  # Don't full-close, position stays open

            if exit_reason != "HOLD":
                pos = self._position_mgr.get_position(symbol)
                tf_str = f" tf={pos.timeframe}" if pos else ""
                logger.info(f"Exit signal for {symbol}: {exit_reason} @ {current_price:.6f}{tf_str}")
                self._sell_position(symbol, current_price, exit_reason)

    def _detect_external_closes(self) -> None:
        """Check Binance API for positions closed externally (manual, SL, TP, trailing).
        Removes phantom positions from internal tracking."""
        if not self._order_executor or not hasattr(self._order_executor, 'get_open_positions'):
            return

        held_symbols = set(self._position_mgr.get_held_symbols())
        if not held_symbols:
            return

        try:
            api_positions = self._order_executor.get_open_positions()

            # GÜVENLİK: API başarısız → hiçbir şey yapma (phantom silme riski)
            if api_positions is None:
                logger.debug("[EXTERNAL CLOSE] API okuma başarısız — kontrol atlanıyor")
                return

            # Build map: symbol → positionAmt (signed: positive=LONG, negative=SHORT)
            # Sadece amt != 0 olanları kaydet (amt=0 = kapalı pozisyon)
            api_pos_map = {}
            for p in api_positions:
                sym = p.get("symbol", "")
                amt = float(p.get("positionAmt", 0))
                if amt != 0:
                    api_pos_map[sym] = amt

            api_symbols = set(api_pos_map.keys())

            # Check 1: symbol completely gone from API or amt=0
            closed_externally = held_symbols - api_symbols

            # Check 2: symbol exists but DIRECTION FLIPPED (SL/trailing açtı ters pozisyon)
            for symbol in held_symbols & api_symbols:
                pos = self._position_mgr.get_position(symbol)
                if not pos:
                    continue
                api_amt = api_pos_map.get(symbol, 0)
                sys_is_long = pos.side == OrderSide.BUY_LONG
                api_is_long = api_amt > 0
                if api_amt != 0 and sys_is_long != api_is_long:
                    logger.critical(
                        f"[DIRECTION FLIP] {symbol}: Sistem={'LONG' if sys_is_long else 'SHORT'} "
                        f"API={'LONG' if api_is_long else 'SHORT'} (amt={api_amt}) "
                        f"— YÖN DEĞİŞMİŞ, pozisyon takipten siliniyor + emirler iptal")
                    # Orphan emirleri temizle (ters yöndeki SL/trailing)
                    try:
                        self._order_executor._rest.cancel_all_orders(symbol)
                    except Exception:
                        pass
                    closed_externally.add(symbol)

            for symbol in closed_externally:
                pos = self._position_mgr.get_position(symbol)
                if not pos:
                    continue

                # Get last price for PnL calculation
                try:
                    ticker = self._rest.get_ticker_price(symbol)
                    exit_price = float(ticker.get("price", 0))
                except Exception:
                    exit_price = pos.entry_price

                # ── Gerçek kapanış nedenini Binance'den tespit et ──
                exit_reason = self._detect_real_exit_reason(
                    symbol, pos, exit_price)

                logger.warning(f"[CLOSE] {symbol} kapandi: {exit_reason} | "
                               f"Giris={pos.entry_price:.6f} Cikis={exit_price:.6f}")

                # Cancel orphan server orders (SL + trailing) for closed position
                if self._order_executor and hasattr(self._order_executor, '_rest'):
                    try:
                        cancel_result = self._order_executor._rest.cancel_all_orders(symbol)
                        logger.info(f"[CLOSE] {symbol}: orphan orders cancelled: {cancel_result}")
                    except Exception as e:
                        logger.warning(f"[CLOSE] {symbol}: cancel orphan orders failed: {e}")

                # Clean up internal state
                self._held_indicators.pop(symbol, None)
                self._server_trailing.pop(symbol, None)
                self._close_retries.pop(symbol, None)

                result = self._position_mgr.close_position(
                    symbol, exit_price, exit_reason)
                self._last_trade_result = result

                if self._risk_manager and result:
                    pnl = result.get("pnl_usdt", 0)
                    self._risk_manager.record_trade_result(pnl)
                    notional = result.get("size", 0) * result.get("exit_price", 0)
                    self._risk_manager.release_exposure(
                        notional_usdt=notional,
                        margin_usdt=result.get("margin_usdt"),
                    )

                # Loss cooldown + coin ban for external closes (server SL/trailing)
                if result:
                    pnl_usdt = result.get("pnl_usdt", 0)
                    if pnl_usdt < 0:
                        entry_mode = getattr(pos, 'entry_mode', '') if pos else ''
                        if entry_mode == "SYSTEM_D":
                            # System D kendi cooldown ayarını kullanır
                            sd = self._config.get("system_d", {})
                            if sd.get("cooldown_enabled", False):
                                cooldown_s = sd.get("cooldown_seconds", 600)
                                self._loss_cooldown_symbols[symbol] = time.time()
                                logger.info(f"[SysD COOLDOWN] {symbol}: {cooldown_s}s re-entry yasagi "
                                            f"(PnL={pnl_usdt:+.4f} USDT)")
                            # cooldown kapalıysa kaydetme
                        elif entry_mode == "SYSTEM_N":
                            # System N kendi config'ini kullanır
                            sn_opt = self._config.get("system_n", {}).get("optional_features", {})
                            if sn_opt.get("loss_cooldown_enabled", False):
                                cooldown_s = sn_opt.get("loss_cooldown_seconds", 600)
                                self._loss_cooldown_symbols[symbol] = time.time()
                                logger.info(f"[SysN COOLDOWN] {symbol}: {cooldown_s}s re-entry yasagi "
                                            f"(PnL={pnl_usdt:+.4f} USDT, external_close)")
                            if sn_opt.get("coin_ban_enabled", False):
                                self._record_coin_loss(symbol)
                                logger.info(f"[SysN BAN KAYDI] {symbol}: external close ban sayaci artti")
                        else:
                            self._loss_cooldown_symbols[symbol] = time.time()
                            logger.info(f"[LOSS COOLDOWN] {symbol}: {self._loss_cooldown_seconds}s re-entry yasagi "
                                        f"(PnL={pnl_usdt:+.4f} USDT, external_close)")
                        # Tüm sistemler için coin loss kaydı (System N hariç, kendi bloğunda yapıyor)
                        if entry_mode != "SYSTEM_N":
                            self._record_coin_loss(symbol)

                self._event_bus.publish(EventType.TRADE_RESULT, result or {})

                # Log to database (both order + trade record)
                if self._order_logger and pos:
                    close_side = (OrderSide.SELL_SHORT if pos.side == OrderSide.BUY_LONG
                                  else OrderSide.BUY_LONG)
                    self._order_logger.log_order(
                        symbol=symbol, side=close_side.value, order_type="Market",
                        price=exit_price, size=pos.size,
                        notional_usdt=pos.size * exit_price,
                        status="placed",
                        trigger_source="exit:external_close",
                    )
                    # Log complete trade record
                    if result:
                        fee_pct = self._config.get("strategy.fee_pct", 0.10) / 100.0
                        fee_usdt = result.get("notional_usdt", 0) * fee_pct
                        from datetime import datetime as dt
                        entry_t = result.get("entry_time", 0)
                        open_time = dt.fromtimestamp(entry_t).isoformat() if entry_t else ""
                        config_snapshot_id = self._order_logger.get_current_snapshot_id()
                        self._order_logger.log_trade(
                            open_time=open_time,
                            close_time=dt.now().isoformat(),
                            symbol=symbol,
                            side=result.get("side", ""),
                            leverage=result.get("leverage", 1),
                            margin_usdt=result.get("margin_usdt", 0),
                            notional_usdt=result.get("notional_usdt", 0),
                            entry_price=result.get("entry_price", 0),
                            exit_price=exit_price,
                            size=result.get("size", 0),
                            pnl_usdt=result.get("pnl_usdt", 0),
                            pnl_percent=result.get("pnl_percent", 0),
                            roi_percent=result.get("roi_percent", 0),
                            fee_usdt=fee_usdt,
                            exit_reason="external_close",
                            hold_seconds=result.get("hold_seconds", 0),
                            highest_price=result.get("highest_price", 0),
                            lowest_price=result.get("lowest_price", 0),
                            initial_sl=result.get("initial_sl", 0),
                            initial_tp=result.get("initial_tp", 0),
                            atr_at_entry=result.get("atr_at_entry", 0),
                            timeframe=result.get("timeframe", ""),
                            entry_score=result.get("entry_score", 0),
                            entry_confluence=result.get("entry_confluence", 0),
                            entry_adx=result.get("entry_adx", 0),
                            entry_rsi=result.get("entry_rsi", 0),
                            entry_regime=result.get("entry_regime", ""),
                            entry_regime_confidence=result.get("entry_regime_confidence", 0),
                            entry_bb_width=result.get("entry_bb_width", 0),
                            funding_fee_usdt=0,
                            config_snapshot_id=config_snapshot_id,
                        )

            # External close sonrası hemen state kaydet
            if closed_externally:
                try:
                    self._position_mgr.save_state()
                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"External close detection error: {e}")

    def _detect_real_exit_reason(self, symbol: str, pos, exit_price: float) -> str:
        """Binance API'den gerçek kapanış nedenini tespit et.
        Önce fiyat karşılaştırması ile tahmin, sonra userTrades ile doğrulama.
        Returns: 'SL_MARKET', 'TP_MARKET', 'TRAILING_STOP', 'MANUAL_CLOSE', 'external_close'"""
        entry = pos.entry_price
        sl = pos.initial_sl
        tp = pos.initial_tp
        is_long = pos.side == OrderSide.BUY_LONG

        # 1. Fiyat bazlı tahmin (hızlı, API gerektirmez)
        if entry > 0 and sl > 0:
            sl_dist = abs(exit_price - sl) / entry * 100
        else:
            sl_dist = 999.0
        if entry > 0 and tp > 0:
            tp_dist = abs(exit_price - tp) / entry * 100
        else:
            tp_dist = 999.0

        # SL veya TP fiyatına %0.5'ten yakın kapanma → o emir tetiklendi
        price_reason = "external_close"
        if sl_dist < 0.5:
            price_reason = "SL_MARKET"
        elif tp_dist < 0.5:
            price_reason = "TP_MARKET"
        elif (is_long and exit_price > entry) or (not is_long and exit_price < entry):
            price_reason = "TRAILING_STOP"  # kârda kapanmış, muhtemelen trailing
        else:
            price_reason = "MANUAL_CLOSE"  # zararda ama SL'ye ulaşmamış

        # 2. Binance userTrades ile doğrulama (daha kesin)
        try:
            rest = self._order_executor._rest if self._order_executor else self._rest
            if rest:
                # Son 5 dakikanın trade'lerini al
                import time as _time
                end_ms = int(_time.time() * 1000)
                start_ms = end_ms - 300_000  # 5dk
                trades = rest.get_account_trades(
                    symbol=symbol, start_time=start_ms, end_time=end_ms, limit=10)
                if trades:
                    # Son reduce_only trade'i bul (pozisyon kapatma)
                    for t in reversed(trades):
                        if t.get("buyer") != (not is_long):
                            # Bu trade pozisyonu kapatmıyor
                            continue
                        # realizedPnl > 0 → kârda kapanmış
                        rpnl = float(t.get("realizedPnl", 0))
                        trade_price = float(t.get("price", 0))

                        # Trade fiyatını SL/TP ile karşılaştır
                        if trade_price > 0 and entry > 0:
                            if sl > 0 and abs(trade_price - sl) / entry * 100 < 0.3:
                                price_reason = "SL_MARKET"
                            elif tp > 0 and abs(trade_price - tp) / entry * 100 < 0.3:
                                price_reason = "TP_MARKET"
                            elif rpnl > 0:
                                price_reason = "TRAILING_STOP"
                        break
        except Exception as e:
            logger.debug(f"[EXIT DETECT] {symbol}: userTrades check failed: {e}")

        logger.info(f"[EXIT DETECT] {symbol}: {price_reason} | "
                    f"exit={exit_price:.6f} sl={sl:.6f} tp={tp:.6f} "
                    f"sl_dist={sl_dist:.3f}% tp_dist={tp_dist:.3f}%")
        return price_reason

    @staticmethod
    def _classify_order_type(order: dict) -> str:
        """Binance order dict'inden emir tipini belirle.
        Algo API, regular API ve farklı response formatlarını destekler."""
        _KNOWN = {"STOP_MARKET", "TRAILING_STOP_MARKET", "TAKE_PROFIT_MARKET",
                  "STOP", "TAKE_PROFIT", "MARKET", "LIMIT"}
        for field in ("orderType", "type", "origType", "algoOrderType"):
            candidate = order.get(field, "")
            if candidate in _KNOWN:
                return candidate
        # Fallback: bilinen bir tip bulunamadı
        return order.get("type", "") or order.get("orderType", "")

    def _build_order_map(self, orders: list) -> dict:
        """Order listesinden per-symbol emir sayımı oluştur."""
        order_map = {}
        for o in orders:
            sym = o.get("symbol", "")
            if sym not in order_map:
                order_map[sym] = {"sl": 0, "trail": 0, "other": 0, "total": 0}
            order_map[sym]["total"] += 1
            otype = self._classify_order_type(o)
            if otype == "STOP_MARKET":
                order_map[sym]["sl"] += 1
            elif otype in ("TRAILING_STOP_MARKET", "TAKE_PROFIT_MARKET"):
                # TRAILING_STOP_MARKET: trend trailing, TAKE_PROFIT_MARKET: ranging sabit TP
                # Verify açısından ikisi de "SL dışı koruma emri" — trail olarak say
                order_map[sym]["trail"] += 1
            else:
                order_map[sym]["other"] += 1
        return order_map

    def _verify_server_orders(self) -> None:
        """GÜVENLİK TARAMASI — 4 KURAL:
        ─────────────────────────────────────────────────────
        KURAL 2: Eksik emiri olan pozisyon tespit et → eksik emri koy
        KURAL 3: Pozisyonu olmayan artık emirleri temizle
        KURAL 4: Bir pozisyonda 2+ SL veya 2+ trailing varsa → temizle + doğru koy

        HER POZİSYON = TAM 1 STOP_MARKET + TAM 1 TRAILING_STOP_MARKET

        GÜVENLİK: API okuma başarısız → HİÇBİR ŞEY YAPMA
        ─────────────────────────────────────────────────────"""
        now = time.time()
        if now - self._last_order_verify_time < self._order_verify_interval:
            return
        self._last_order_verify_time = now

        if not self._order_executor or not hasattr(self._order_executor, '_rest'):
            return

        rest = self._order_executor._rest
        held_symbols = set(self._position_mgr.get_held_symbols())

        # System M/N pozisyonlarını çıkar — sinyal bazlı, server SL/trailing kullanmaz
        held_symbols = {s for s in held_symbols
                        if getattr(self._position_mgr.get_position(s), 'entry_mode', '') not in ("SYSTEM_M", "SYSTEM_N")}

        if not held_symbols:
            return

        # ── 1. Tüm emirleri tek çağrıda oku (bulk) ──
        all_combined = rest.get_all_open_orders_combined()

        # Bulk başarısız → per-symbol fallback
        if all_combined is None:
            logger.warning("[ORDER VERIFY] Bulk API okuma başarısız — "
                           "per-symbol fallback deneniyor")
            self._verify_server_orders_per_symbol(rest, held_symbols)
            return

        # ── 2. Per-symbol emir sayımı (düzeltilmiş type parsing) ──
        order_map = self._build_order_map(all_combined)

        # ── 3. Durum logu ──
        total_orders = sum(v["total"] for v in order_map.values())
        total_sl = sum(v["sl"] for v in order_map.values())
        total_trail = sum(v["trail"] for v in order_map.values())
        expected = len(held_symbols) * 2
        status = "OK" if (total_sl + total_trail) == expected else "EKSİK"
        logger.info(f"[ORDER VERIFY] {status} | {total_orders} emir "
                    f"(SL={total_sl}, trail={total_trail}, beklenen={expected}) | "
                    f"pozisyon={len(held_symbols)}")

        # İlk çalışmada response yapısını logla (teşhis için)
        algo_samples = [o for o in all_combined if o.get("_source") == "algo"]
        if algo_samples:
            s = algo_samples[0]
            logger.info(f"[ORDER VERIFY] Algo örnek key'ler: {sorted(s.keys())}")
            # Tüm type-related field'ları logla (teşhis)
            logger.info(f"[ORDER VERIFY] Algo örnek type alanları: "
                        f"type={s.get('type')}, orderType={s.get('orderType')}, "
                        f"origType={s.get('origType')}, "
                        f"algoOrderType={s.get('algoOrderType')}")

        strat = self._config.get("strategy", {})
        self._apply_order_rules(rest, held_symbols, order_map, strat)

    def _verify_server_orders_per_symbol(self, rest, held_symbols: set) -> None:
        """Bulk read başarısız olduğunda her sembol için tek tek sorgula."""
        strat = self._config.get("strategy", {})
        order_map = {}
        failed_symbols = []

        for symbol in held_symbols:
            try:
                orders = rest.get_symbol_open_orders_combined(symbol)
                if orders is None:
                    failed_symbols.append(symbol)
                    continue
                sym_map = self._build_order_map(orders)
                order_map.update(sym_map)
                # Sembol order_map'te yoksa boş ekle
                if symbol not in order_map:
                    order_map[symbol] = {"sl": 0, "trail": 0, "other": 0, "total": 0}
            except Exception as e:
                logger.error(f"[ORDER VERIFY] {symbol}: per-symbol read failed: {e}")
                failed_symbols.append(symbol)

        if failed_symbols:
            logger.warning(f"[ORDER VERIFY] Per-symbol fallback: {len(failed_symbols)} "
                           f"sembol okunamadı: {failed_symbols}")

        # Başarılı okunanlar için kuralları uygula
        verified_symbols = held_symbols - set(failed_symbols)
        if verified_symbols:
            total_sl = sum(order_map.get(s, {}).get("sl", 0) for s in verified_symbols)
            total_trail = sum(order_map.get(s, {}).get("trail", 0) for s in verified_symbols)
            logger.info(f"[ORDER VERIFY] Per-symbol: {len(verified_symbols)} sembol okundu "
                        f"(SL={total_sl}, trail={total_trail})")
            self._apply_order_rules(rest, verified_symbols, order_map, strat)

    def _apply_order_rules(self, rest, held_symbols: set, order_map: dict,
                           strat: dict) -> None:
        """Verify kurallarını uygula. Davranış config'den kontrol edilir:
        - order_verify_clean_orphans: pozisyonsuz emirleri sil/bırak
        - order_verify_fix_duplicates: mükerrer emirleri sil+yenile / bırak
        - order_verify_no_cancel: eksik emirde sadece ekle / sil+yenile
        """
        no_cancel = strat.get("order_verify_no_cancel", True)
        fix_dupes = strat.get("order_verify_fix_duplicates", False)
        clean_orphans = strat.get("order_verify_clean_orphans", True)

        # ── KURAL 3: Pozisyonu olmayan artık emirleri temizle ──
        orphan_symbols = set(order_map.keys()) - held_symbols
        for sym in orphan_symbols:
            info = order_map[sym]
            if info["sl"] > 0 or info["trail"] > 0:
                if clean_orphans:
                    logger.warning(f"[ORDER VERIFY] {sym}: Pozisyon YOK ama "
                                   f"{info['total']} emir var — orphan temizleniyor")
                    try:
                        rest.cancel_all_orders(sym)
                    except Exception as e:
                        logger.error(f"[ORDER VERIFY] {sym}: orphan temizleme hatası: {e}")
                else:
                    logger.info(f"[ORDER VERIFY] {sym}: Pozisyon YOK, "
                                f"{info['total']} orphan emir var — "
                                f"temizleme KAPALI, bırakıldı")

        # ── KURAL 2 + 4: Her pozisyon için kontrol ──
        for symbol in held_symbols:
            pos = self._position_mgr.get_position(symbol)
            if not pos or pos.entry_price <= 0:
                continue
            # System M/N: sinyal bazlı — server SL/trailing kullanmaz, emir koymayı atla
            if getattr(pos, 'entry_mode', '') in ("SYSTEM_M", "SYSTEM_N"):
                continue

            info = order_map.get(symbol, {"sl": 0, "trail": 0, "other": 0, "total": 0})

            # KURAL 4: Mükerrer emir var (2+ SL veya 2+ trailing)
            if info["sl"] > 1 or info["trail"] > 1:
                if fix_dupes:
                    logger.warning(f"[ORDER VERIFY] {symbol}: MÜKERRER "
                                   f"(SL={info['sl']}, trail={info['trail']}) "
                                   f"— temizlenip 1+1 yeniden konuyor")
                    try:
                        rest.cancel_all_orders(symbol)
                    except Exception as e:
                        logger.error(f"[ORDER VERIFY] {symbol}: cancel failed: {e}")
                    self._place_missing_orders(symbol, pos, strat,
                                               need_sl=True, need_trailing=True)
                    continue
                else:
                    logger.info(f"[ORDER VERIFY] {symbol}: mükerrer emir var "
                                f"(SL={info['sl']}, trail={info['trail']}) — "
                                f"temizleme KAPALI, bırakıldı")

            # Yeterli emir var → OK
            if info["sl"] >= 1 and info["trail"] >= 1:
                logger.debug(f"[ORDER VERIFY] {symbol}: OK "
                             f"(SL={info['sl']}, trail={info['trail']})")
                continue

            # KURAL 2: Eksik emir — sadece ekle veya sil+yenile
            need_sl = info["sl"] == 0
            need_trail = info["trail"] == 0

            missing = []
            if need_sl:
                missing.append("SL")
            if need_trail:
                missing.append("TRAILING")

            if no_cancel:
                # Sadece eksik olanı ekle, mevcut emirlere DOKUNMA
                logger.warning(f"[ORDER VERIFY] {symbol}: EKSİK {', '.join(missing)} "
                               f"(mevcut: SL={info['sl']}, trail={info['trail']}) "
                               f"— sadece eksik ekleniyor")
                self._place_missing_orders(symbol, pos, strat, need_sl, need_trail)
            else:
                # Hepsini sil + 1 SL + 1 trailing yeniden koy
                logger.warning(f"[ORDER VERIFY] {symbol}: EKSİK {', '.join(missing)} "
                               f"(mevcut: SL={info['sl']}, trail={info['trail']}) "
                               f"— temizlenip 1+1 yeniden konuyor")
                try:
                    rest.cancel_all_orders(symbol)
                except Exception as e:
                    logger.error(f"[ORDER VERIFY] {symbol}: cancel failed: {e}")
                self._place_missing_orders(symbol, pos, strat,
                                           need_sl=True, need_trailing=True)

    # Sentinel: API başarısız → pozisyon durumu bilinmiyor (silme güvenli değil)
    _API_FAILED = (-1, None)

    def _get_actual_qty_and_side(self, symbol: str) -> tuple:
        """Binance API'den gerçek pozisyon miktarı ve yönünü oku.
        Returns:
          (abs_qty, is_long)  — pozisyon bulundu
          (0, None)           — pozisyon API'de yok (amt=0 veya symbol yok)
          _API_FAILED (-1, None) — API çağrısı başarısız, durum bilinmiyor
        """
        try:
            api_positions = self._order_executor.get_open_positions()
            if api_positions is None:
                # API başarısız — pozisyon durumu bilinmiyor
                logger.warning(f"[ORDER REPAIR] {symbol}: API returned None — "
                               f"durum bilinmiyor, silme yapılmayacak")
                return self._API_FAILED
            # API başarılı ama boş liste → hiç pozisyon yok
            for p in api_positions:
                if p.get("symbol") == symbol:
                    amt = float(p.get("positionAmt", 0))
                    if amt == 0:
                        return (0, None)
                    return (abs(amt), amt > 0)
            # Symbol listede yok → pozisyon yok
            return (0, None)
        except Exception as e:
            logger.warning(f"[ORDER REPAIR] {symbol}: API qty read failed: {e}")
            return self._API_FAILED

    def _place_missing_orders(self, symbol: str, pos, strat: dict,
                               need_sl: bool, need_trailing: bool) -> None:
        """Eksik emirleri koy. Mevcut emirlere dokunma.
        Emir koymadan önce Binance'den GERÇEK qty okunur (stale pos.size koruması).

        SL  = entry ± N×ATR (orijinal plan)
        Trail = entry ± M×ATR tetik, K×ATR callback (orijinal plan)
        """
        try:
            rest = self._order_executor._rest
            pp = self._order_executor._get_price_precision(symbol)
            is_long = pos.side == OrderSide.BUY_LONG
            close_side = "SELL" if is_long else "BUY"
            entry_regime = getattr(pos, 'entry_regime', '')
            entry_price = pos.entry_price

            atr = pos.atr_at_entry if hasattr(pos, 'atr_at_entry') else 0
            if entry_price <= 0:
                logger.warning(f"[ORDER REPAIR] {symbol}: entry={entry_price} — cannot repair")
                return
            # ATR=0 fallback: entry fiyatının %2'si
            if atr <= 0:
                atr = entry_price * 0.02
                logger.warning(f"[ORDER REPAIR] {symbol}: ATR=0, fallback ATR=%2 of entry "
                               f"= {atr:.8f}")

            # ── GERÇEK QTY + YÖN: Binance'den oku ──
            actual_qty, api_is_long = self._get_actual_qty_and_side(symbol)
            if actual_qty < 0:
                # API başarısız — durum bilinmiyor, GÜVENLİ davran: repair'ı atla
                logger.warning(f"[ORDER REPAIR] {symbol}: API başarısız — "
                               f"repair atlanıyor (pozisyon silinmeyecek)")
                return
            if actual_qty == 0:
                # Pozisyon API'de yok — kapanmış olabilir
                logger.warning(f"[ORDER REPAIR] {symbol}: API'de pozisyon YOK — "
                               f"repair iptal, pozisyon kapatılıyor")
                self._position_mgr.close_position(symbol, pos.entry_price,
                                                  reason="api_position_gone")
                return
            # ── YÖN KONTROLÜ: API yönü sistem yönüyle eşleşmeli ──
            if api_is_long is not None and api_is_long != is_long:
                logger.critical(f"[ORDER REPAIR] {symbol}: YÖN UYUMSUZLUĞU! "
                                f"Sistem={'LONG' if is_long else 'SHORT'} "
                                f"API={'LONG' if api_is_long else 'SHORT'} "
                                f"— repair DURDURULDU, pozisyon takipten siliniyor")
                self._position_mgr.close_position(symbol, entry_price,
                                                  reason="direction_mismatch")
                # Ters yöndeki orphan emirleri temizle
                try:
                    self._order_executor._rest.cancel_all_orders(symbol)
                    logger.info(f"[ORDER REPAIR] {symbol}: orphan emirler iptal edildi")
                except Exception:
                    pass
                return
            if actual_qty != pos.size:
                logger.warning(f"[ORDER REPAIR] {symbol}: QTY MISMATCH! "
                               f"pos.size={pos.size} → API={actual_qty} (güncellendi)")
                pos.size = actual_qty

            logger.info(f"[ORDER REPAIR] {symbol}: başlıyor "
                        f"(need_sl={need_sl}, need_trailing={need_trailing}, "
                        f"entry={entry_price}, ATR={atr:.8f}, qty={actual_qty}, "
                        f"side={'LONG' if is_long else 'SHORT'}, regime={entry_regime})")

            # ── Config multiplier'ları ──
            entry_mode = getattr(pos, 'entry_mode', 'TREND')

            # ── Sisteme göre eksik emir onarımı ──
            if entry_mode == "SYSTEM_J":
                sl_placed, trail_placed = self._place_missing_orders_system_j(symbol, pos)
            elif entry_mode == "SYSTEM_I":
                sl_placed, trail_placed = self._place_missing_orders_system_i(
                    symbol, pos, rest, pp, is_long, close_side,
                    entry_price, actual_qty, entry_regime,
                    need_sl, need_trailing)
            elif entry_mode == "SYSTEM_H":
                sl_placed, trail_placed = self._place_missing_orders_system_h(
                    symbol, pos, rest, pp, is_long, close_side,
                    entry_price, actual_qty, entry_regime,
                    need_sl, need_trailing)
            elif entry_mode == "SYSTEM_D":
                sl_placed, trail_placed = self._place_missing_orders_system_d(
                    symbol, pos, rest, pp, is_long, close_side,
                    entry_price, actual_qty, entry_regime,
                    need_sl, need_trailing)
            elif entry_mode == "SYSTEM_B":
                sl_placed, trail_placed = self._place_missing_orders_system_b(
                    symbol, pos, rest, pp, is_long, close_side,
                    entry_price, actual_qty, entry_regime,
                    need_sl, need_trailing)
            else:
                sl_placed, trail_placed = self._place_missing_orders_system_a(
                    symbol, pos, strat, rest, pp, is_long, close_side,
                    entry_price, atr, actual_qty, entry_mode, entry_regime,
                    need_sl, need_trailing)

            # Sonuç özeti + başarısız olursa erken verify tetikle
            any_failed = (need_sl and not sl_placed) or (need_trailing and not trail_placed)
            logger.info(f"[ORDER REPAIR] {symbol}: sonuç — "
                        f"SL={'OK' if sl_placed or not need_sl else 'FAIL'}, "
                        f"Trail={'OK' if trail_placed or not need_trailing else 'FAIL'}")
            if any_failed:
                self._last_order_verify_time = 0.0
                logger.warning(f"[ORDER REPAIR] {symbol}: eksik emir kaldı — "
                               f"erken verify tetiklendi")

        except Exception as e:
            logger.error(f"[ORDER REPAIR] {symbol}: repair failed: {e}")
            import traceback
            logger.error(f"[ORDER REPAIR] traceback: {traceback.format_exc()}")
            self._last_order_verify_time = 0.0  # erken verify

    def _place_missing_orders_system_a(self, symbol, pos, strat, rest, pp,
                                        is_long, close_side, entry_price,
                                        atr, actual_qty, entry_mode,
                                        entry_regime, need_sl, need_trailing):
        """System A: ATR bazlı eksik emir onarımı."""
        sl_atr_mult = strat.get("server_sl_atr_mult", 2.0)
        activate_mult = strat.get("trailing_atr_activate_mult", 3.0)
        distance_mult = strat.get("trailing_atr_distance_mult", 1.0)

        # MR override
        if entry_mode == "MEAN_REVERSION" and strat.get("mr_trailing_enabled", True):
            activate_mult = strat.get("mr_trailing_activate_atr", 1.5)
            distance_mult = strat.get("mr_trailing_callback_atr", 0.5)
        elif strat.get("adx_regime_enabled", False) and entry_regime in (
                "RANGING", "WEAK_TREND", "STRONG_TREND"):
            prefix = {"RANGING": "adx_regime_ranging",
                      "WEAK_TREND": "adx_regime_weak",
                      "STRONG_TREND": "adx_regime_strong"}[entry_regime]
            sl_atr_mult = strat.get(f"{prefix}_sl_atr", sl_atr_mult)
            activate_mult = strat.get(f"{prefix}_trail_activate_atr", activate_mult)
            distance_mult = strat.get(f"{prefix}_trail_callback_atr", distance_mult)

        sl_placed = False
        trail_placed = False

        if need_sl:
            if is_long:
                sl_price = round(entry_price - (atr * sl_atr_mult), pp)
            else:
                sl_price = round(entry_price + (atr * sl_atr_mult), pp)
            try:
                result = rest.place_order(
                    symbol=symbol, side=close_side,
                    order_type="STOP_MARKET",
                    quantity=actual_qty, stop_price=sl_price,
                    reduce_only=True,
                )
                sl_placed = True
                algo_id = result.get("algoId", "?") if isinstance(result, dict) else "?"
                logger.info(f"[ORDER REPAIR] {symbol}: Eksik SL kondu "
                            f"@ {sl_price} ({sl_atr_mult}xATR) qty={actual_qty} "
                            f"algoId={algo_id}")
            except Exception as e:
                logger.error(f"[ORDER REPAIR] {symbol}: SL placement FAILED: {e}")

        if need_trailing:
            callback_pct = (atr * distance_mult) / entry_price * 100
            callback_pct = max(0.1, min(5.0, round(callback_pct, 1)))

            if is_long:
                activation_price = round(entry_price + (atr * activate_mult), pp)
            else:
                activation_price = round(entry_price - (atr * activate_mult), pp)

            try:
                result = rest.place_order(
                    symbol=symbol, side=close_side,
                    order_type="TRAILING_STOP_MARKET",
                    quantity=actual_qty,
                    stop_price=activation_price,
                    callback_rate=callback_pct,
                    reduce_only=True,
                )
                trail_placed = True
                algo_id = result.get("algoId", "?") if isinstance(result, dict) else "?"
                self._server_trailing[symbol] = {
                    "callback_pct": callback_pct,
                    "activation_price": activation_price,
                    "sl_price": 0,
                    "timestamp": time.time(),
                    "renewal_count": getattr(pos, 'trailing_renewal_count', 0),
                }
                logger.info(f"[ORDER REPAIR] {symbol}: Eksik TRAILING kondu "
                            f"activation={activation_price} callback={callback_pct}% "
                            f"({activate_mult}xATR tetik, {distance_mult}xATR callback) "
                            f"qty={actual_qty} algoId={algo_id}")
            except Exception as e:
                logger.error(f"[ORDER REPAIR] {symbol}: Trailing placement FAILED: {e}")

        return sl_placed, trail_placed

    def _place_missing_orders_system_b(self, symbol, pos, rest, pp,
                                        is_long, close_side, entry_price,
                                        actual_qty, entry_regime,
                                        need_sl, need_trailing):
        """System B: G bazlı eksik emir onarımı.
        SL = sl_carpan × G (%), Trailing/TP = rejime göre."""
        sb = self._config.get("system_b", {})
        G = getattr(pos, 'entry_bb_width', 0)
        is_ranging = entry_regime in ("RANGING", "WEAK_RANGING",
                                      "SYNCED:RANGING", "SYNCED:WEAK_RANGING")

        sl_placed = False
        trail_placed = False

        if G <= 0:
            # G değeri yoksa ATR fallback
            atr = pos.atr_at_entry if hasattr(pos, 'atr_at_entry') else 0
            if atr <= 0:
                atr = entry_price * 0.02
            G = (atr / entry_price * 100)
            logger.warning(f"[ORDER REPAIR SysB] {symbol}: G=0, ATR fallback → G={G:.3f}%")

        logger.info(f"[ORDER REPAIR SysB] {symbol}: G={G:.3f}% "
                    f"regime={entry_regime} side={'LONG' if is_long else 'SHORT'}")

        # ── 1. SL: sl_carpan × G ──
        if need_sl:
            sl_carpan = sb.get("sl_carpan", 1.5)
            sl_offset = entry_price * (sl_carpan * G / 100)
            if is_long:
                sl_price = round(entry_price - sl_offset, pp)
            else:
                sl_price = round(entry_price + sl_offset, pp)

            try:
                result = rest.place_order(
                    symbol=symbol, side=close_side,
                    order_type="STOP_MARKET",
                    quantity=actual_qty, stop_price=sl_price,
                    reduce_only=True,
                )
                sl_placed = True
                algo_id = result.get("algoId", "?") if isinstance(result, dict) else "?"
                logger.info(f"[ORDER REPAIR SysB] {symbol}: SL kondu @ {sl_price} "
                            f"({sl_carpan}×G={sl_carpan * G:.2f}%) qty={actual_qty} "
                            f"algoId={algo_id}")
            except Exception as e:
                logger.error(f"[ORDER REPAIR SysB] {symbol}: SL FAILED: {e}")

        # ── 2. Trailing veya TP (rejime göre) ──
        if need_trailing and is_ranging:
            # Ranging: sabit TP (bant bazlı) — G/2 mesafe
            tp_offset = entry_price * (G / 2 / 100)
            if is_long:
                tp_price = round(entry_price + tp_offset, pp)
            else:
                tp_price = round(entry_price - tp_offset, pp)

            try:
                result = rest.place_order(
                    symbol=symbol, side=close_side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=actual_qty, stop_price=tp_price,
                    reduce_only=True,
                )
                trail_placed = True  # TP counts as the "trailing" slot
                algo_id = result.get("algoId", "?") if isinstance(result, dict) else "?"
                logger.info(f"[ORDER REPAIR SysB] {symbol}: TP kondu @ {tp_price} "
                            f"(ranging, G/2={G / 2:.2f}%) algoId={algo_id}")
            except Exception as e:
                logger.error(f"[ORDER REPAIR SysB] {symbol}: TP FAILED: {e}")
        elif need_trailing:
            # Trend: trailing (tetik_carpan × G, trail_carpan × G callback)
            tetik_carpan = sb.get("tetik_carpan", 2.5)
            trail_carpan = sb.get("trail_carpan", 0.5)

            activation_offset = entry_price * (tetik_carpan * G / 100)
            if is_long:
                activation_price = round(entry_price + activation_offset, pp)
            else:
                activation_price = round(entry_price - activation_offset, pp)

            callback_pct = trail_carpan * G
            callback_pct = max(0.1, min(5.0, round(callback_pct, 1)))

            try:
                result = rest.place_order(
                    symbol=symbol, side=close_side,
                    order_type="TRAILING_STOP_MARKET",
                    quantity=actual_qty,
                    stop_price=activation_price,
                    callback_rate=callback_pct,
                    reduce_only=True,
                )
                trail_placed = True
                algo_id = result.get("algoId", "?") if isinstance(result, dict) else "?"
                self._server_trailing[symbol] = {
                    "callback_pct": callback_pct,
                    "activation_price": activation_price,
                    "timestamp": time.time(),
                }
                logger.info(f"[ORDER REPAIR SysB] {symbol}: TRAILING kondu "
                            f"activation={activation_price} ({tetik_carpan}×G) "
                            f"callback={callback_pct}% ({trail_carpan}×G) "
                            f"algoId={algo_id}")
            except Exception as e:
                logger.error(f"[ORDER REPAIR SysB] {symbol}: TRAILING FAILED: {e}")

        return sl_placed, trail_placed

    def _execute_partial_tp(self, symbol: str, current_price: float) -> None:
        """Close a portion of the position at N×ATR profit, keep rest for trailing."""
        pos = self._position_mgr.get_position(symbol)
        if not pos:
            return

        strat = self._config.get("strategy", {})
        close_pct = strat.get("partial_tp_close_pct", 0.5)

        # Get qty precision from symbol info cache
        qty_precision = 3
        if self._symbol_info_cache:
            try:
                sym_info = self._symbol_info_cache.get(symbol)
                if sym_info:
                    qty_precision = sym_info.quantity_precision
            except Exception:
                pass

        close_size = round(pos.size * close_pct, qty_precision)

        if close_size <= 0:
            logger.warning(f"[PARTIAL_TP] {symbol}: close_size is 0 after rounding, skipping")
            return

        close_side = (OrderSide.SELL_SHORT if pos.side == OrderSide.BUY_LONG
                      else OrderSide.BUY_LONG)

        success = False
        if self._order_executor:
            try:
                if hasattr(self._order_executor, "close_position"):
                    success = self._order_executor.close_position(
                        symbol, pos.side, close_size,
                        limit_exit=False, limit_offset_pct=0.0)
                else:
                    success = self._order_executor.execute_order(
                        symbol=symbol, side=close_side,
                        order_type=OrderType.MARKET,
                        size=close_size, reduce_only=True,
                        qty_precision=qty_precision,
                    )
            except Exception as e:
                logger.error(f"[PARTIAL_TP] Order failed for {symbol}: {e}")

        if success:
            remaining = round(pos.size - close_size, qty_precision)
            self._position_mgr.update_position_size(symbol, remaining)

            pnl_pct = self._position_mgr._get_pnl_pct(pos, current_price)
            logger.info(f"[PARTIAL_TP] {symbol}: closed {close_pct*100:.0f}% "
                        f"({close_size} qty) at {pnl_pct:+.1f}%, "
                        f"remaining {remaining} qty for trailing")

            if self._order_logger:
                self._order_logger.log_order(
                    symbol=symbol, side=close_side.value,
                    order_type="Market",
                    price=current_price, size=close_size,
                    notional_usdt=close_size * current_price,
                    status="filled",
                    trigger_source="partial_tp",
                )
        else:
            # Reset the flag so it can be retried next cycle
            pos.partial_tp_taken = False
            logger.warning(f"[PARTIAL_TP] {symbol}: order failed, will retry next cycle")

    def _sell_position(self, symbol: str, exit_price: float, reason: str) -> None:
        """Sell a specific position. Thread-safe: prevents double-sell via _selling_symbols guard."""
        # Race condition guard: prevent two threads from selling the same symbol
        with self._selling_lock:
            if symbol in self._selling_symbols:
                logger.debug(f"Skipping {symbol} sell — already being sold by another thread")
                return
            self._selling_symbols.add(symbol)

        try:
            self._sell_position_inner(symbol, exit_price, reason)
        finally:
            with self._selling_lock:
                self._selling_symbols.discard(symbol)

    def _sell_position_inner(self, symbol: str, exit_price: float, reason: str) -> None:
        """Internal sell logic (called by _sell_position under guard)."""
        pos = self._position_mgr.get_position(symbol)
        if not pos:
            return

        close_side = (OrderSide.SELL_SHORT if pos.side == OrderSide.BUY_LONG
                      else OrderSide.BUY_LONG)

        logger.info(f"Closing {symbol}: {close_side.value} {pos.size}")

        success = True
        if self._order_executor:
            # API mode: close directly, no pair switching needed
            if hasattr(self._order_executor, "close_position"):
                strat = self._config.get("strategy", {})
                limit_exit = strat.get("limit_exit_enabled", False)
                limit_offset = 0.0

                # Emergency çıkışta limit emir zorla (maker fee %0.02 vs taker %0.05)
                is_emergency = "EMERGENCY" in reason
                if is_emergency:
                    limit_exit = True
                    # Emergency'de küçük offset: hızlı dolsun ama maker fee olsun
                    limit_offset = 0.05  # %0.05 offset — neredeyse market ama limit
                    logger.info(f"[EMERGENCY LIMIT] {symbol}: likidasyon oncesi limit emir ile cikis")
                elif limit_exit and pos.atr_at_entry > 0 and pos.entry_price > 0:
                    atr_offset_mult = strat.get("limit_exit_atr_offset", 0.2)
                    limit_offset = (pos.atr_at_entry * atr_offset_mult / pos.entry_price) * 100

                success = self._order_executor.close_position(
                    symbol, pos.side, pos.size,
                    limit_exit=limit_exit,
                    limit_offset_pct=limit_offset)
            else:
                # Legacy UI mode
                if self._pair_switcher:
                    self._pair_switcher.switch_to(symbol)
                    time.sleep(2)
                success = self._order_executor.execute_order(
                    symbol=symbol, side=close_side, order_type=OrderType.MARKET,
                    size=pos.size, reduce_only=True,
                    qty_precision=3,
                )

        if success:
            self._close_retries.pop(symbol, None)  # Clear retry counter on success
            self._held_indicators.pop(symbol, None)  # Clean up indicator cache
            self._server_trailing.pop(symbol, None)  # Clean up server trailing tracking
            # Cancel orphan server orders (SL + trailing) after position closed
            if self._order_executor and hasattr(self._order_executor, '_rest'):
                result = self._order_executor._rest.cancel_all_orders(symbol)
                if result.get("errors"):
                    logger.warning(f"Orphan order cancel issues for {symbol}: {result['errors']}")
            result = self._position_mgr.close_position(symbol, exit_price, reason)
            self._last_trade_result = result

            # Hemen state kaydet (crash koruması)
            try:
                self._position_mgr.save_state()
            except Exception:
                pass

            if self._risk_manager:
                pnl = result.get("pnl_usdt", 0)
                self._risk_manager.record_trade_result(pnl)
                notional = result.get("size", 0) * result.get("exit_price", 0)
                self._risk_manager.release_exposure(
                    notional_usdt=notional,
                    margin_usdt=result.get("margin_usdt"),
                )

            # Loss cooldown: prevent re-entry into same coin after loss
            pnl_usdt = result.get("pnl_usdt", 0)
            if pnl_usdt < 0:
                self._loss_cooldown_symbols[symbol] = time.time()
                # SL/likidasyon/server çıkışlarında coin ban kaydı (sinyal çıkışlarında değil)
                ban_reasons = ("STOP_LOSS", "EMERGENCY_ANTI_LIQ", "external_close",
                               "STOP_LOSS_FORCED", "EMERGENCY_ANTI_LIQ_FORCED")
                if reason in ban_reasons:
                    self._record_coin_loss(symbol)
                    logger.info(f"[COIN BAN KAYDI] {symbol}: SL/likidasyon cikisi, ban sayaci artti "
                                f"(PnL={pnl_usdt:+.4f} USDT, neden={reason})")
                logger.info(f"[LOSS COOLDOWN] {symbol}: {self._loss_cooldown_seconds}s re-entry yasagi "
                            f"(PnL={pnl_usdt:+.4f} USDT)")

            self._event_bus.publish(EventType.TRADE_RESULT, result)

            # Log sell order to database
            if self._order_logger:
                self._order_logger.log_order(
                    symbol=symbol, side=close_side.value, order_type="Market",
                    price=exit_price, size=pos.size,
                    notional_usdt=pos.size * exit_price,
                    status="placed",
                    trigger_source=f"exit:{reason}",
                )
                # Log complete trade record
                fee_pct = self._config.get("strategy.fee_pct", 0.10) / 100.0
                fee_usdt = result.get("notional_usdt", 0) * fee_pct
                from datetime import datetime
                entry_t = result.get("entry_time", 0)
                open_time = datetime.fromtimestamp(entry_t).isoformat() if entry_t else ""
                config_snapshot_id = self._order_logger.get_current_snapshot_id()
                self._order_logger.log_trade(
                    open_time=open_time,
                    close_time=datetime.now().isoformat(),
                    symbol=symbol,
                    side=result.get("side", ""),
                    leverage=result.get("leverage", 1),
                    margin_usdt=result.get("margin_usdt", 0),
                    notional_usdt=result.get("notional_usdt", 0),
                    entry_price=result.get("entry_price", 0),
                    exit_price=exit_price,
                    size=result.get("size", 0),
                    pnl_usdt=result.get("pnl_usdt", 0),
                    pnl_percent=result.get("pnl_percent", 0),
                    roi_percent=result.get("roi_percent", 0),
                    fee_usdt=fee_usdt,
                    exit_reason=reason,
                    hold_seconds=result.get("hold_seconds", 0),
                    highest_price=result.get("highest_price", 0),
                    lowest_price=result.get("lowest_price", 0),
                    initial_sl=result.get("initial_sl", 0),
                    initial_tp=result.get("initial_tp", 0),
                    atr_at_entry=result.get("atr_at_entry", 0),
                    timeframe=result.get("timeframe", ""),
                    entry_score=result.get("entry_score", 0),
                    entry_confluence=result.get("entry_confluence", 0),
                    entry_adx=result.get("entry_adx", 0),
                    entry_rsi=result.get("entry_rsi", 0),
                    entry_regime=result.get("entry_regime", ""),
                    entry_regime_confidence=result.get("entry_regime_confidence", 0),
                    entry_bb_width=result.get("entry_bb_width", 0),
                    funding_fee_usdt=0,
                    config_snapshot_id=config_snapshot_id,
                )
        else:
            # Track retry count with exponential backoff
            retry_info = self._close_retries.get(symbol, {"count": 0})
            retry_info["count"] += 1
            backoff = min(2 ** retry_info["count"], 60)  # 2s, 4s, 8s, 16s, 32s, 60s
            retry_info["next_retry"] = time.time() + backoff
            self._close_retries[symbol] = retry_info

            if retry_info["count"] >= self._max_close_retries:
                logger.error(f"Failed to close {symbol} after {retry_info['count']} attempts, "
                             f"removing from tracking (position may have been closed by exchange)")
                # Force-remove from position manager
                result = self._position_mgr.close_position(symbol, exit_price, reason + "_FORCED")
                self._last_trade_result = result
                self._event_bus.publish(EventType.TRADE_RESULT, result)
                self._close_retries.pop(symbol, None)
            else:
                logger.error(f"Failed to close {symbol}, retry {retry_info['count']}/{self._max_close_retries} "
                             f"(next in {backoff}s)")

    # ──── BUYING State ────

    def _check_trade_frequency(self) -> bool:
        """Check if we've exceeded the hourly trade limit."""
        now = time.time()
        self._trade_timestamps = [t for t in self._trade_timestamps
                                  if now - t < 3600]
        # Cap list size as safety measure
        if len(self._trade_timestamps) > 100:
            self._trade_timestamps = self._trade_timestamps[-100:]
        if self._max_trades_per_hour and len(self._trade_timestamps) >= self._max_trades_per_hour:
            logger.info(f"Trade frequency limit reached: "
                        f"{len(self._trade_timestamps)}/{self._max_trades_per_hour} "
                        f"trades in last hour. Waiting...")
            return False
        return True

    def _check_direction_balance(self, direction: str) -> tuple[bool, str]:
        """Check if opening a new position in this direction is allowed by balance ratio.

        Ratio X:Y means: majority <= X * (floor(minority / Y) + 1)
        Based on CURRENTLY OPEN positions (Seçenek B).
        """
        strat = self._config.get("strategy", {})
        if not strat.get("direction_balance_enabled", False):
            return True, ""

        ratio_str = strat.get("direction_balance_ratio", "")
        if not ratio_str or ratio_str == "off":
            return True, ""

        # Parse ratio "X-Y" format
        try:
            parts = str(ratio_str).split("-")
            ratio_x = int(parts[0])
            ratio_y = int(parts[1]) if len(parts) > 1 else 1
        except (ValueError, IndexError):
            return True, ""

        if ratio_x <= 0 or ratio_y <= 0:
            return True, ""

        longs, shorts = self._position_mgr.get_direction_counts()

        if direction == "LONG":
            new_longs = longs + 1
            new_shorts = shorts
        else:
            new_longs = longs
            new_shorts = shorts + 1

        majority = max(new_longs, new_shorts)
        minority = min(new_longs, new_shorts)

        # Formula: majority <= X * (floor(minority / Y) + 1)
        max_allowed = ratio_x * (minority // ratio_y + 1)

        if majority > max_allowed:
            # Determine which direction is needed
            if new_longs > new_shorts:
                needed = "SHORT"
            else:
                needed = "LONG"
            reason = (f"direction_balance ({longs}L/{shorts}S + {direction} "
                      f"= {majority}:{minority} > {ratio_x}:{ratio_y} limit={max_allowed}, "
                      f"need {needed})")
            return False, reason

        return True, ""

    def _check_coin_daily_ban(self, symbol: str) -> tuple[bool, str]:
        """Check if a coin is banned due to too many losses in 24h."""
        strat = self._config.get("strategy", {})
        limit = strat.get("coin_daily_loss_limit", 0)
        if limit <= 0:
            return True, ""

        ban_hours = strat.get("coin_daily_ban_hours", 24)
        now = time.time()
        cutoff = now - (ban_hours * 3600)

        # Clean old entries
        if symbol in self._coin_loss_history:
            self._coin_loss_history[symbol] = [
                t for t in self._coin_loss_history[symbol] if t > cutoff
            ]
            loss_count = len(self._coin_loss_history[symbol])
            if loss_count >= limit:
                remaining_h = ban_hours - (now - self._coin_loss_history[symbol][0]) / 3600
                return False, (f"coin_daily_ban ({symbol}: {loss_count} losses "
                               f"in {ban_hours}h, banned ~{remaining_h:.1f}h remaining)")

        return True, ""

    def _record_coin_loss(self, symbol: str) -> None:
        """Record a loss for per-coin daily ban tracking."""
        if symbol not in self._coin_loss_history:
            self._coin_loss_history[symbol] = []
        self._coin_loss_history[symbol].append(time.time())

    def _check_coin_daily_ban_system_n(self, symbol: str) -> tuple[bool, str]:
        """System N için coin ban kontrolü — kendi config'inden okur.
        Global _check_coin_daily_ban() strategy.* okur (24h),
        bu metot system_n.optional_features.* okur (8h)."""
        sn_cfg = self._config.get("system_n", {})
        opt = sn_cfg.get("optional_features", {})

        if not opt.get("coin_ban_enabled", False):
            return True, ""

        limit = opt.get("coin_daily_loss_limit", 3)
        if limit <= 0:
            return True, ""

        ban_hours = opt.get("coin_daily_ban_hours", 8)
        now = time.time()
        cutoff = now - (ban_hours * 3600)

        if symbol in self._coin_loss_history:
            self._coin_loss_history[symbol] = [
                t for t in self._coin_loss_history[symbol] if t > cutoff
            ]
            loss_count = len(self._coin_loss_history[symbol])
            if loss_count >= limit:
                remaining_h = ban_hours - (now - self._coin_loss_history[symbol][0]) / 3600
                return False, (f"coin_ban ({symbol}: {loss_count} zarar "
                               f"{ban_hours}h icinde, ~{remaining_h:.1f}h kaldi)")

        return True, ""

    def _check_reverse_allowed_system_n(self, symbol: str) -> tuple[bool, str]:
        """System N ardışık reverse koruması.
        max_consecutive_reverses aşıldıysa reverse engellenir, sadece close yapılır."""
        sn_cfg = self._config.get("system_n", {})
        opt = sn_cfg.get("optional_features", {})

        if not opt.get("reverse_protection_enabled", False):
            return True, ""

        max_rev = opt.get("max_consecutive_reverses", 2)
        window_s = opt.get("reverse_window_seconds", 1800)
        now = time.time()

        # Eski kayıtları temizle
        if symbol in self._system_n_reverse_history:
            self._system_n_reverse_history[symbol] = [
                t for t in self._system_n_reverse_history[symbol]
                if now - t < window_s
            ]
            count = len(self._system_n_reverse_history[symbol])
            if count >= max_rev:
                return False, (f"ardisik reverse limiti ({count}/{max_rev} "
                               f"son {window_s // 60}dk icinde)")

        return True, ""

    def _record_reverse_system_n(self, symbol: str) -> None:
        """System N reverse kaydı — ardışık reverse takibi için."""
        if symbol not in self._system_n_reverse_history:
            self._system_n_reverse_history[symbol] = []
        self._system_n_reverse_history[symbol].append(time.time())

    def _do_buying(self) -> None:
        """Place order via API (legacy state machine entry), then return to SCANNING."""
        if not self._last_candidate:
            self._transition(ScannerState.SCANNING)
            return
        if not self._check_trade_frequency():
            self._transition(ScannerState.SCANNING)
            scan_interval = self._config.get("strategy.scan_interval_seconds", 30)
            self._wait(scan_interval)
            return
        self._do_buying_inline()
        self._transition(ScannerState.SCANNING)

    def _do_mr_buying_inline(self, mr_cand: MRScanResult) -> bool:
        """Place MR order: limit entry at BB band with MR-specific params.
        Reuses most of _do_buying_inline but with MR entry_mode and mr_tp_price."""
        # Store original candidate, set MR candidate as current
        original_candidate = self._last_candidate

        # Create a synthetic ScanResult-like object for _do_buying_inline compatibility
        # MR always uses limit entry at the band price
        self._last_candidate = mr_cand
        self._mr_buying_active = True
        try:
            success = self._do_buying_inline()
        finally:
            self._mr_buying_active = False
            if not success:
                self._last_candidate = original_candidate
        return success

    def _do_buying_inline(self) -> bool:
        """Place order for self._last_candidate. Returns True on success."""
        candidate = self._last_candidate
        if not candidate:
            return False

        symbol = candidate.symbol
        price = candidate.price
        atr = candidate.atr
        direction = candidate.direction

        side = OrderSide.BUY_LONG if direction == "LONG" else OrderSide.SELL_SHORT

        # 1. Get fresh price from API
        try:
            ticker = self._rest.get_ticker_price(symbol)
            price = float(ticker.get("price", price))
        except Exception:
            pass

        # 1b. BTC correlation check — prevent excessive portfolio beta
        btc_ok, btc_reason = self._btc_corr.check_position(
            symbol, direction, self._position_mgr.get_all_positions())
        if not btc_ok:
            logger.info(f"Skipping {symbol}: {btc_reason}")
            self._failed_symbols[symbol] = time.time()
            return False

        # 2. Determine leverage mode
        lev_enabled = self._config.get("leverage.enabled", False)
        leverage = None
        margin_usdt = None
        qty_precision = 3

        if lev_enabled:
            min_lev = self._config.get("leverage.min_leverage", 10)
            max_lev = self._config.get("leverage.max_leverage", 125)

            # Read available (free) balance from API
            real_balance = 0.0
            if self._order_executor and hasattr(self._order_executor, "get_balance"):
                try:
                    real_balance = self._order_executor.get_balance()
                except Exception as e:
                    logger.warning(f"Could not read API balance: {e}")

            # Position sizing mode: "percentage" or "fixed"
            sizing_mode = self._config.get("leverage.position_sizing", "fixed")
            if sizing_mode == "percentage":
                # Emre Ortalama: portfolio_divider (1/N of REALIZED portfolio)
                # Portfolio = free cash + sum of entry margins (NOT unrealized PnL)
                # Kurallar:
                #   wallet >= 12$: margin = wallet / 12
                #   4$ <= wallet < 12$: margin = 1$ (sabit)
                #   wallet < 4$: margin = wallet / 4
                divider = self._config.get("strategy.portfolio_divider", 0)
                if divider > 0:
                    locked_margin = self._position_mgr.get_total_margin()
                    wallet = real_balance + locked_margin
                    if wallet <= 0:
                        balance = self._config.get("risk.initial_balance", 5.0)
                        if self._risk_manager:
                            balance = self._risk_manager._current_balance
                        wallet = balance

                    # GUI'den ayarlanabilir parametreler
                    min_wallet = self._config.get("strategy.portfolio_min_wallet", 12.0)
                    fixed_margin = self._config.get("strategy.portfolio_fixed_margin", 1.0)
                    micro_divider = self._config.get("strategy.portfolio_micro_divider", 4)

                    if wallet >= min_wallet:
                        # Normal: 1/N of portfolio (ornek: 1/12)
                        margin_usdt = round(wallet / divider, 2)
                        sizing_label = f"1/{divider} of {wallet:.2f}$"
                    elif wallet >= 4.0:
                        # Küçük portföy: sabit margin (varsayilan 1$)
                        margin_usdt = fixed_margin
                        sizing_label = f"{fixed_margin}$ sabit ({wallet:.2f}$ portföy)"
                    else:
                        # Mikro portföy: 1/N of portfolio (varsayilan 1/4)
                        micro_div = max(2, micro_divider)
                        margin_usdt = round(wallet / micro_div, 2)
                        sizing_label = f"1/{micro_div} of {wallet:.2f}$"

                    logger.info(f"Portfolio: {wallet:.2f}$ "
                                f"(free={real_balance:.2f}$ + locked={locked_margin:.2f}$) "
                                f"→ margin={margin_usdt}$ ({sizing_label})")
                else:
                    available = real_balance if real_balance > 0 else (
                        self._config.get("risk.initial_balance", 5.0))
                    portfolio_pct = self._config.get("leverage.portfolio_percent", 25)
                    margin_usdt = round(available * portfolio_pct / 100.0, 2)
                    sizing_label = f"{portfolio_pct}%"

                # Minimum margin: use all available if below 1$
                if margin_usdt < 1.0:
                    if real_balance >= 0.30:
                        margin_usdt = round(min(real_balance * 0.95, real_balance - 0.01), 2)
                        logger.info(f"Low balance mode: using {margin_usdt}$ "
                                    f"(avbl={real_balance:.2f}$)")
                    else:
                        logger.warning(f"Balance too low: {real_balance:.2f}$ "
                                       f"(need at least 0.30$)")
                        return False

                if real_balance > 0 and margin_usdt > real_balance * 0.95:
                    margin_usdt = round(real_balance * 0.95, 2)

                logger.info(f"Position sizing: {sizing_label} = "
                            f"{margin_usdt}$ margin (avbl={real_balance:.2f}$)")
            else:
                margin_usdt = self._config.get("leverage.margin_usdt", 1.0)
                max_pos = self._config.get("leverage.max_position_usdt", 50.0)
                if margin_usdt > max_pos:
                    margin_usdt = max_pos
                if real_balance > 0 and margin_usdt > real_balance * 0.95:
                    margin_usdt = round(real_balance * 0.95, 2)
                    logger.info(f"Margin capped to {margin_usdt}$ (avbl={real_balance}$)")
                if margin_usdt < 1.0:
                    logger.warning(f"Available margin too low: {margin_usdt}$ (need 1$+, avbl={real_balance}$)")
                    return False

            # Get qty precision from symbol info cache
            qty_precision = 3
            if self._symbol_info_cache:
                try:
                    sym_info = self._symbol_info_cache.get(symbol)
                    if sym_info:
                        qty_precision = sym_info.quantity_precision
                except Exception as e:
                    logger.warning(f"SymbolInfo fetch failed for {symbol}: {e}")

            # Get max leverage from API (authenticated — accurate)
            available_max = self._rest.get_max_leverage(symbol, margin_usdt * max_lev)
            logger.info(f"{symbol} API max leverage: {available_max}x")

            if available_max < min_lev:
                logger.warning(f"{symbol} max leverage {available_max}x < "
                               f"min required {min_lev}x, skipping")
                self._failed_symbols[symbol] = time.time()
                return False

            leverage = min(max_lev, available_max)

            # Position sizing: margin x leverage = notional
            notional_usdt = margin_usdt * leverage

            # Check minimum notional and adjust margin if needed
            min_notional = 5.0
            if self._symbol_info_cache:
                try:
                    si = self._symbol_info_cache.get(symbol)
                    if si:
                        min_notional = si.min_notional
                except Exception:
                    pass

            # Save original margin for limit checks
            original_margin = margin_usdt

            if notional_usdt < min_notional and price > 0:
                needed_margin = min_notional / leverage * 1.05
                max_allowed = self._config.get("risk.max_single_order_usdt", 50.0)
                # Don't let min_notional blow up position size beyond 2x target
                # This prevents BTC etc. from consuming disproportionate portfolio
                original_margin = margin_usdt
                if needed_margin > original_margin * 2.0:
                    logger.warning(f"{symbol} min notional {min_notional}$ needs "
                                   f"{needed_margin:.2f}$ margin (target was "
                                   f"{original_margin:.2f}$, >2x), skipping")
                    self._failed_symbols[symbol] = time.time()
                    return False
                if needed_margin <= max_allowed:
                    logger.info(f"Adjusting margin {margin_usdt}$ -> {needed_margin:.2f}$ "
                                f"to meet min notional {min_notional}$")
                    margin_usdt = round(needed_margin, 2)
                    notional_usdt = margin_usdt * leverage
                else:
                    logger.warning(f"{symbol} min notional {min_notional}$ needs "
                                   f"{needed_margin:.2f}$ margin (max {max_allowed}$), skipping")
                    self._failed_symbols[symbol] = time.time()
                    return False

            size_qty = round(notional_usdt / price,
                             qty_precision) if price > 0 else 0

            # Also check min quantity
            min_qty = 1
            if self._symbol_info_cache:
                try:
                    si = self._symbol_info_cache.get(symbol)
                    if si:
                        min_qty = si.min_qty
                except Exception:
                    pass
            if size_qty < min_qty:
                needed_notional = min_qty * price * 1.05
                needed_margin = needed_notional / leverage
                max_allowed = self._config.get("risk.max_single_order_usdt", 50.0)
                # Cap total adjustment to 2x of ORIGINAL target (not current margin)
                # This prevents min_notional + min_qty from stacking to 4x
                if needed_margin > original_margin * 2.0:
                    logger.warning(f"{symbol} min qty {min_qty} needs "
                                   f"{needed_margin:.2f}$ margin (target was "
                                   f"{original_margin:.2f}$, >2x total), skipping")
                    self._failed_symbols[symbol] = time.time()
                    return False
                # Only adjust if min_qty requires MORE than current margin
                if needed_margin > margin_usdt:
                    if needed_margin <= max_allowed:
                        logger.info(f"Adjusting margin for min qty: {margin_usdt}$ -> "
                                    f"{needed_margin:.2f}$ (min_qty={min_qty})")
                        margin_usdt = round(needed_margin, 2)
                        notional_usdt = margin_usdt * leverage
                        size_qty = round(notional_usdt / price, qty_precision)
                    else:
                        logger.warning(f"{symbol} min qty {min_qty} needs "
                                       f"{needed_margin:.2f}$ margin, skipping")
                        self._failed_symbols[symbol] = time.time()
                        return False

            # Dynamic SL/TP from leverage (dual-layer protection)
            # Katman 1: Server SL at 50% of liq distance (FEE + SLIPPAGE DAHIL)
            # Katman 2: Emergency software close at 80% (in position_manager)
            strat = self._config.get("strategy", {})
            liq_factor = strat.get("liq_factor", 70) / 100.0
            liq_pct = (1.0 / leverage) * liq_factor
            sl_liq_pct = strat.get("sl_liq_percent", 50) / 100.0
            tp_liq_mult = strat.get("tp_liq_multiplier", 3.0)

            # Fee-aware SL: hedef kayıp = SL + fee + slippage
            # Fee ROI = round-trip fee × leverage
            # Slippage tahmini = fee × slippage_mult
            # SL ROI = hedef_ROI - fee_ROI - slippage_ROI
            fee_pct = strat.get("fee_pct", 0.10) / 100.0
            fee_roi = fee_pct * leverage * 100  # fee as % of margin
            slippage_mult = strat.get("slippage_mult", 0.5)
            slippage_roi = fee_roi * slippage_mult  # estimated slippage
            raw_sl_roi = liq_pct * sl_liq_pct * leverage * 100  # hedef toplam kayıp
            net_sl_roi = max(raw_sl_roi - fee_roi - slippage_roi, fee_roi)  # fee düşülmüş SL
            sl_price_pct = net_sl_roi / (leverage * 100)  # geri fiyat mesafesine çevir

            sl_roi = round(net_sl_roi, 1)

            # Minimum SL mesafesi: fee + spread koruması
            # Pozisyon açılır açılmaz SL'ye takılmasını engeller
            # Min SL = 3 × (round-trip fee + tahmini spread)
            # spread tahmini = fee × 0.5 (maker-taker farkı)
            spread_est = fee_pct * 0.5
            min_sl_distance = 3.0 * (fee_pct + spread_est)
            if sl_price_pct < min_sl_distance:
                logger.warning(f"Order rejected: SL too tight for {symbol} "
                               f"(SL={sl_price_pct*100:.3f}% < min={min_sl_distance*100:.3f}% "
                               f"= 3×(fee+spread)). Fee/spread kaybi riski.")
                self._failed_symbols[symbol] = time.time()
                return False

            # Only calculate TP if tp_enabled in config
            if strat.get("tp_enabled", False):
                tp_price_pct = liq_pct * tp_liq_mult
                tp_roi = round(tp_price_pct * leverage * 100, 1)
            else:
                tp_roi = 0

            logger.info(f"LEVERAGE: {leverage}x margin={margin_usdt}$ "
                        f"notional={notional_usdt:.1f}$ qty={size_qty} "
                        f"SL={sl_price_pct*100:.3f}%(ROI-{sl_roi}%) "
                        f"fee_ROI={fee_roi:.0f}% slip={slippage_roi:.0f}% "
                        f"toplam_kayip={raw_sl_roi:.0f}% "
                        f"Emergency@80%liq")
        else:
            # Legacy non-leverage sizing
            if self._risk_manager:
                size_usdt = self._risk_manager.kelly_position_size(
                    self._risk_manager._current_balance, price, atr
                )
                min_usdt = self._config.get("scanner.min_order_usdt", 5.0)
                size_usdt = max(size_usdt, min_usdt)
            else:
                size_usdt = self._config.get("scanner.default_order_usdt", 5.0)

            size_qty = round(size_usdt / price, 2) if price > 0 else 0
            sl_pct = (atr * 2.0 / price * 100) if price > 0 else 2.0
            tp_pct = (atr * 4.0 / price * 100) if price > 0 else 5.0
            sl_roi = sl_pct
            tp_roi = tp_pct

        # 3. Validate order
        if self._risk_manager:
            valid, reason = self._risk_manager.validate_order(
                size_qty, price, symbol,
                margin_usdt=margin_usdt if lev_enabled else None,
                leverage=leverage if lev_enabled else None,
            )
            if not valid:
                logger.warning(f"Order rejected: {reason}")
                self._failed_symbols[symbol] = time.time()
                return False

        # 4. Execute order (SL/TP handled by _place_initial_trailing, not here)
        lev_str = f" LEV={leverage}x" if leverage else ""

        # Check if limit entry mode is enabled
        limit_enabled = strat.get("limit_entry_enabled", False) if strat else False
        limit_atr_offset = strat.get("limit_atr_offset", 0.5) if strat else 0.5
        limit_timeout = strat.get("limit_timeout_seconds", 300) if strat else 300

        # Mean Reversion override: always limit entry at BB band price
        is_mr = getattr(self, '_mr_buying_active', False)
        if is_mr:
            limit_enabled = True
            # MR felsefesi: giriş BB bandında olmalı, genel ATR offset'te değil
            # BB band fiyatını hedefle, küçük buffer ile dolma olasılığını artır
            mr_indicators = getattr(candidate, 'indicator_values', {})
            bb_lower = mr_indicators.get("BB_Lower", 0) if mr_indicators else 0
            bb_upper = mr_indicators.get("BB_Upper", 0) if mr_indicators else 0
            mr_band_buffer_atr = 0.1  # band'a 0.1×ATR buffer (dolma kolaylığı)
            if direction == "LONG" and bb_lower > 0 and atr > 0:
                # LONG: alt banda yakın al, küçük buffer ile biraz üstüne koy
                limit_atr_offset = max((price - bb_lower) / atr - mr_band_buffer_atr, 0.05)
                logger.info(f"[MR] {symbol}: limit entry at BB lower band "
                            f"(BB_L={bb_lower:.6f}, offset={limit_atr_offset:.2f}xATR, "
                            f"BB%={getattr(candidate, 'bb_percent_b', 0):.0%})")
            elif direction == "SHORT" and bb_upper > 0 and atr > 0:
                # SHORT: üst banda yakın sat, küçük buffer ile biraz altına koy
                limit_atr_offset = max((bb_upper - price) / atr - mr_band_buffer_atr, 0.05)
                logger.info(f"[MR] {symbol}: limit entry at BB upper band "
                            f"(BB_U={bb_upper:.6f}, offset={limit_atr_offset:.2f}xATR, "
                            f"BB%={getattr(candidate, 'bb_percent_b', 0):.0%})")
            else:
                # Fallback: BB verisi yoksa genel offset kullan
                limit_atr_offset = strat.get("limit_atr_offset", 0.5)
                logger.info(f"[MR] {symbol}: limit entry {limit_atr_offset}xATR (fallback) "
                            f"(BB={getattr(candidate, 'bb_percent_b', 0):.0%})")

        # ADX regime override: entry type + ATR offset
        adx_regime = getattr(candidate, 'adx_regime', '') if candidate else ''
        if not is_mr and adx_regime and strat.get("adx_regime_enabled", False):
            if adx_regime == "RANGING":
                limit_enabled = True
                limit_atr_offset = strat.get("adx_regime_ranging_entry_atr", 2.0)
            elif adx_regime == "WEAK_TREND":
                limit_enabled = True
                limit_atr_offset = strat.get("adx_regime_weak_entry_atr", 1.0)
            elif adx_regime == "STRONG_TREND":
                limit_enabled = False  # market entry
            logger.info(f"[ADX REGIME] {symbol}: {adx_regime} → "
                        f"{'limit ' + str(limit_atr_offset) + 'xATR' if limit_enabled else 'market'}")

        if limit_enabled and atr > 0 and self._order_executor:
            # LIMIT ORDER: place at offset price
            offset = atr * limit_atr_offset
            if direction == "LONG":
                limit_price = price - offset
            else:
                limit_price = price + offset

            # Round to price precision
            pp = 4
            if self._order_executor and hasattr(self._order_executor, '_get_price_precision'):
                pp = self._order_executor._get_price_precision(symbol)
            limit_price = round(limit_price, pp)

            logger.info(f"Placing LIMIT order: {side.value} {size_qty} {symbol} "
                        f"limit={limit_price:.{pp}f} (market={price:.{pp}f}, "
                        f"offset={limit_atr_offset}xATR={offset:.{pp}f})"
                        f"{lev_str}")

            success = self._order_executor.execute_order(
                symbol=symbol, side=side, order_type=OrderType.LIMIT,
                price=limit_price,
                size=size_qty, tp_percent=0, sl_percent=0,
                leverage=leverage,
                qty_precision=qty_precision,
                ensure_isolated=(lev_enabled and
                                 self._config.get("leverage.mode", "isolated")
                                 == "isolated"),
            )
            if not success:
                logger.error("Limit order placement failed")
                self._failed_symbols[symbol] = time.time()
                return False

            # Track pending limit order — don't open position yet
            self._pending_limits[symbol] = {
                "limit_price": limit_price,
                "side": side,
                "direction": direction,
                "size": size_qty,
                "atr": atr,
                "candidate": candidate,
                "leverage": leverage if lev_enabled else 1,
                "margin_usdt": margin_usdt if lev_enabled else 0.0,
                "placed_time": time.time(),
                "timeout": limit_timeout,
                "qty_precision": qty_precision,
                "lev_enabled": lev_enabled,
                "entry_mode": "MEAN_REVERSION" if getattr(self, '_mr_buying_active', False) else "TREND",
                "mr_tp_price": getattr(candidate, 'mr_tp_target', 0.0) if getattr(self, '_mr_buying_active', False) else 0.0,
            }

            # Record trade timestamp for frequency limiter
            self._trade_timestamps.append(time.time())

            # Log limit order
            if self._order_logger:
                self._order_logger.log_order(
                    symbol=symbol, side=side.value, order_type="Limit",
                    price=limit_price, size=size_qty,
                    notional_usdt=notional_usdt if lev_enabled else size_qty * price,
                    status="pending",
                    trigger_source=f"scanner_limit:{candidate.score:+.0f}",
                )

            logger.info(f"Limit order pending: {symbol} @ {limit_price:.{pp}f}, "
                        f"timeout={limit_timeout}s")
            return True  # Order placed, will be tracked

        # MARKET ORDER (default or fallback)
        logger.info(f"Placing order: {side.value} {size_qty} {symbol} @ {price:.6f}"
                    f"{lev_str} SL_ROI={sl_roi:.1f}%")

        if self._order_executor:
            success = self._order_executor.execute_order(
                symbol=symbol, side=side, order_type=OrderType.MARKET,
                size=size_qty, tp_percent=0, sl_percent=0,
                leverage=leverage,
                qty_precision=qty_precision,
                ensure_isolated=(lev_enabled and
                                 self._config.get("leverage.mode", "isolated")
                                 == "isolated"),
            )
            if not success:
                logger.error("Order execution failed")
                self._failed_symbols[symbol] = time.time()
                return False

        # 5. Open position tracking (with optimal timeframe)
        pos_tf = self._tf_selector.get_timeframe(symbol) if \
            self._config.get("strategy", {}).get("dynamic_timeframe", True) else \
            self._config.get("indicators.kline_interval", "1m")
        regime_info = candidate.regime or {}
        # Use ADX regime as entry_regime when enabled, fallback to market regime
        effective_regime = (adx_regime if adx_regime
                           else regime_info.get("regime", ""))
        # Determine entry mode (MR or TREND)
        is_mr = getattr(self, '_mr_buying_active', False)
        entry_mode = "MEAN_REVERSION" if is_mr else "TREND"
        mr_tp = 0.0
        if is_mr and hasattr(candidate, 'mr_tp_target'):
            mr_tp = candidate.mr_tp_target

        self._position_mgr.open_position(
            symbol, side, price, size_qty, atr,
            leverage=leverage if lev_enabled else 1,
            margin_usdt=margin_usdt if lev_enabled else 0.0,
            timeframe=pos_tf,
            entry_score=candidate.score,
            entry_confluence=candidate.confluence.get("score", 0) if hasattr(candidate.confluence, 'get') else 0,
            entry_adx=candidate.adx,
            entry_rsi=candidate.rsi,
            entry_regime=effective_regime,
            entry_regime_confidence=regime_info.get("confidence", 0),
            entry_bb_width=regime_info.get("bb_width", 0),
            entry_mode=entry_mode,
            mr_tp_price=mr_tp,
        )

        if self._risk_manager:
            self._risk_manager.record_order(
                size_qty, price,
                margin_usdt=margin_usdt if lev_enabled else None,
            )

        # Log order to database
        if self._order_logger:
            self._order_logger.log_order(
                symbol=symbol, side=side.value, order_type="Market",
                price=price, size=size_qty,
                tp_percent=tp_roi if tp_roi else None,
                sl_percent=sl_roi if sl_roi else None,
                notional_usdt=notional_usdt if lev_enabled else size_qty * price,
                status="placed",
                trigger_source=f"scanner:{candidate.score:+.0f}",
            )

        # 7. Place initial server-side trailing stop (safety net from start)
        # Her pozisyona emir konulur — leverage olsun olmasın
        if self._order_executor and hasattr(self._order_executor, '_rest'):
            pos_obj = self._position_mgr.get_position(symbol)
            if pos_obj:
                self._place_initial_trailing(symbol, pos_obj, price, atr)

        # Record trade timestamp for frequency limiter
        self._trade_timestamps.append(time.time())
        return True

    # ──── SELLING State (legacy, for manual sells) ────

    def _do_selling(self) -> None:
        """Close the first position that needs closing."""
        if not self._position_mgr.has_position:
            self._transition(ScannerState.SCANNING)
            return

        pos = self._position_mgr.position
        symbol = pos.symbol
        exit_price = getattr(self, "_sell_price", pos.entry_price)
        exit_reason = getattr(self, "_sell_reason", "UNKNOWN")

        self._sell_position(symbol, exit_price, exit_reason)
        self._transition(ScannerState.SCANNING)

    # ──── COOLDOWN State ────

    def _do_cooldown(self) -> None:
        cooldown = self._config.get("strategy.cooldown_seconds", 60)
        logger.info(f"Cooldown: {cooldown}s before next scan...")
        self._wait(cooldown)
        self._transition(ScannerState.SCANNING)

    # ──── Position Monitor (fast check thread) ────

    def _position_monitor_loop(self) -> None:
        """Separate thread that checks positions every 5 seconds.
        Uses single bulk API call for ALL prices instead of per-symbol calls.
        Critical for anti-liquidation: server-side SL handles sub-second protection,
        this loop handles trailing, signal exit, and emergency close."""
        check_interval = 5  # 5 seconds — server SL covers fast protection
        logger.info("Position monitor thread started")
        while self._running:
            try:
                if not self._position_mgr.has_position:
                    self._stop_event.wait(timeout=check_interval)
                    continue

                # Don't interfere while buying/selling on UI
                if self._state == ScannerState.BUYING:
                    self._stop_event.wait(timeout=check_interval)
                    continue

                # Single bulk API call: fetch ALL ticker prices at once
                try:
                    all_tickers = self._rest.get_all_ticker_prices()
                    price_map = {t["symbol"]: float(t["price"])
                                 for t in all_tickers if float(t.get("price", 0)) > 0}
                except Exception:
                    self._stop_event.wait(timeout=check_interval)
                    continue

                for symbol in list(self._position_mgr.get_held_symbols()):
                    # Skip symbols currently being sold by main thread
                    if symbol in self._selling_symbols:
                        continue

                    current_price = price_map.get(symbol, 0)
                    if current_price <= 0:
                        continue

                    exit_reason = self._position_mgr.check_position(
                        symbol, current_price)

                    if exit_reason == self._position_mgr.EXIT_PARTIAL_TP:
                        pos_obj = self._position_mgr.get_position(symbol)
                        if pos_obj and pos_obj.entry_mode == "SYSTEM_F":
                            self._execute_partial_tp_system_f(symbol, current_price)
                        else:
                            self._execute_partial_tp(symbol, current_price)
                        continue  # Don't full-close, position stays open

                    if exit_reason != "HOLD":
                        # Check if we're in backoff period for this symbol
                        retry_info = self._close_retries.get(symbol)
                        if retry_info and time.time() < retry_info.get("next_retry", 0):
                            continue  # Skip until backoff expires

                        # === HYBRID TRAILING: Renewal disabled ===
                        if exit_reason == self._position_mgr.EXIT_TRAILING_RENEW:
                            exit_reason = self._position_mgr.EXIT_TRAILING

                        logger.warning(
                            f"[MONITOR] Exit signal for {symbol}: "
                            f"{exit_reason} @ {current_price:.6f}")
                        # Only do software close if not in BUYING state
                        if self._state != ScannerState.BUYING:
                            try:
                                self._sell_position(symbol, current_price, exit_reason)
                                logger.info(f"[MONITOR] {symbol} closed successfully, "
                                            f"slots: {self._position_mgr.position_count}/"
                                            f"{self._position_mgr.max_positions}")
                            except Exception as sell_err:
                                logger.error(f"[MONITOR] Error closing {symbol}: {sell_err}")

                self._stop_event.wait(timeout=check_interval)
            except Exception as e:
                logger.error(f"Position monitor error: {e}")
                import traceback
                logger.error(f"Position monitor traceback: {traceback.format_exc()}")
                self._stop_event.wait(timeout=5)

    # ──── Hybrid Trailing Evaluation ────

    def _evaluate_trailing_renew(self, symbol: str, current_price: float) -> bool:
        """Trailing stop tetiklendi ama kapatmadan önce değerlendir:
        1. Bu coinin şu anki sinyali hala güçlü mü?
        2. Dışarıda bu coinden daha iyi fırsat var mı?

        True → trailing yenile (kapat değil)
        False → kapat (normal trailing stop)
        """
        try:
            pos = self._position_mgr.get_position(symbol)
            if not pos:
                return False

            strat = self._config.get("strategy", {})
            min_buy_score = strat.get("min_buy_score", 55)

            # 1. Bu coinin güncel kline'larını çek ve skoru hesapla
            tf = self._tf_selector.get_timeframe(symbol) if \
                self._config.get("strategy", {}).get("dynamic_timeframe", True) else \
                self._config.get("indicators.kline_interval", "5m")
            limit = self._config.get("strategy.kline_limit", 200)

            klines = self._rest.get_klines(symbol, tf, limit=limit)
            if klines is None or klines.empty:
                logger.info(f"[HYBRID] {symbol} kline çekilemedi, trailing kapatılıyor")
                return False

            # Score this symbol
            coin_result = self._scorer.score_symbol(symbol, klines)

            # Check: sinyal hala aynı yönde ve güçlü mü?
            score = coin_result.score
            direction_match = (
                (pos.side == OrderSide.BUY_LONG and coin_result.direction == "LONG") or
                (pos.side == OrderSide.SELL_SHORT and coin_result.direction == "SHORT")
            )

            if not direction_match:
                logger.info(f"[HYBRID] {symbol} sinyal yön değiştirdi "
                            f"({coin_result.direction}), trailing kapatılıyor")
                return False

            if not coin_result.eligible:
                logger.info(f"[HYBRID] {symbol} artık eligible değil "
                            f"({coin_result.reject_reason}), trailing kapatılıyor")
                return False

            abs_score = abs(score)
            if abs_score < min_buy_score:
                logger.info(f"[HYBRID] {symbol} skor düşük ({abs_score:.1f} < {min_buy_score}), "
                            f"trailing kapatılıyor")
                return False

            # 2. Dışarıda daha iyi fırsat var mı? (Basit kontrol: son scan sonuçlarından)
            # Eğer son scanda bu coinden daha yüksek skorlu eligible coin varsa → kapat
            # (ama sadece o coin için slot boşsa önemli, 4/4 doluysa zaten açamayız)
            # Şimdilik: sinyal güçlüyse yenile, basit tut
            # İleride _last_scan_results ile karşılaştırma eklenebilir

            # 3. Sinyal güçlü → trailing'i yenile!
            new_atr = coin_result.atr if coin_result.atr > 0 else pos.atr_at_entry

            self._position_mgr.renew_trailing(symbol, current_price, new_atr)

            # 4. Binance'deki SL emrini güncelle (yeni sanal girişe göre)
            # KIRMIZI KURAL 3: Server emirleri (SL + trailing) pozisyon açıkken
            # DEĞİŞTİRİLMEZ. İlk konulan emirler pozisyon kapanana kadar durur.

            return True

        except Exception as e:
            logger.error(f"[HYBRID] {symbol} değerlendirme hatası: {e}")
            import traceback
            logger.error(f"[HYBRID] traceback: {traceback.format_exc()}")
            return False  # hata durumunda güvenli taraf: kapat

    # ──── API Position Sync ────

    def _sync_api_positions(self) -> None:
        """On startup, sync any existing API positions into the position manager.
        This way the program tracks positions that were opened before restart.
        Önce kaydedilmiş state okunur → entry_mode, entry_bb_width gibi bilgiler korunur."""
        if not self._order_executor or not hasattr(self._order_executor, 'get_open_positions'):
            return

        # Kaydedilmiş state'i oku (shutdown sırasında kaydedilmiş)
        saved_state = self._position_mgr.load_state()

        try:
            api_positions = self._order_executor.get_open_positions()
            if api_positions is None:
                logger.warning("API position sync failed — could not read positions")
                return
            if not api_positions:
                logger.info("No existing API positions to sync")
                return

            for p in api_positions:
                symbol = p.get("symbol", "")
                amt = float(p.get("positionAmt", 0))
                entry_price = float(p.get("entryPrice", 0))
                leverage = int(p.get("leverage", 1))
                margin = float(p.get("isolatedWallet", 0))

                if amt == 0 or entry_price == 0:
                    continue

                side = OrderSide.BUY_LONG if amt > 0 else OrderSide.SELL_SHORT
                size = abs(amt)

                # Get ATR + indicators for this symbol (score recovery)
                atr = 0.0
                entry_score = 0.0
                entry_confluence = 0.0
                entry_adx = 0.0
                entry_rsi = 50.0
                entry_regime = "SYNCED"
                entry_regime_confidence = 0.0
                entry_bb_width = 0.0
                try:
                    interval = self._config.get("strategy.kline_interval",
                               self._config.get("indicators.kline_interval", "5m"))
                    kline_limit = self._config.get("strategy.kline_limit", 200)
                    klines = self._rest.get_klines(symbol, interval, limit=kline_limit)
                    if klines is not None and len(klines) > 50:
                        from indicators.indicator_engine import IndicatorEngine
                        eng = IndicatorEngine(self._config)
                        indicators = eng.compute_all(klines)
                        atr = indicators.get("ATR", 0)
                        entry_adx = indicators.get("ADX", 0)
                        entry_rsi = indicators.get("RSI", 50)
                        logger.info(f"Sync ATR for {symbol}: {atr:.8f} "
                                    f"({atr/entry_price*100:.3f}%) tf={interval}")

                        # Recover entry score via scorer
                        try:
                            scan_result = self._scorer.score_symbol(
                                symbol, klines)
                            if scan_result:
                                entry_score = scan_result.score
                                entry_confluence = (
                                    scan_result.confluence.get("score", 0)
                                    if hasattr(scan_result.confluence, 'get')
                                    else 0
                                )
                                regime_info = scan_result.regime or {}
                                entry_regime = f"SYNCED:{regime_info.get('regime', '')}"
                                entry_regime_confidence = regime_info.get(
                                    "confidence", 0)
                                entry_bb_width = regime_info.get("bb_width", 0)
                                logger.info(
                                    f"Sync score for {symbol}: "
                                    f"score={entry_score:+.1f} "
                                    f"conf={entry_confluence:.1f} "
                                    f"adx={entry_adx:.1f} "
                                    f"rsi={entry_rsi:.1f}")
                        except Exception as e:
                            logger.warning(
                                f"Score recovery failed for {symbol}: {e}")
                    else:
                        logger.warning(f"Not enough klines for {symbol} ATR "
                                       f"(got {len(klines) if klines else 0})")
                except Exception as e:
                    logger.warning(f"ATR calculation failed for sync {symbol}: {e}")

                # Kaydedilmiş state varsa oradan oku (entry_mode, entry_bb_width vb.)
                prev = saved_state.get(symbol, {})
                if prev:
                    sync_entry_mode = prev.get("entry_mode", "")
                    # State'ten korunacak alanlar (scanner hesaplamalarından daha güvenilir)
                    if prev.get("entry_bb_width", 0) > 0:
                        entry_bb_width = prev["entry_bb_width"]
                    if prev.get("entry_regime", ""):
                        entry_regime = prev["entry_regime"]
                    if prev.get("entry_regime_confidence", 0) > 0:
                        entry_regime_confidence = prev["entry_regime_confidence"]
                    if prev.get("entry_score", 0) != 0:
                        entry_score = prev["entry_score"]
                    logger.info(f"[SYNC] {symbol}: state'ten yüklendi → "
                                f"entry_mode={sync_entry_mode}, "
                                f"bb_width={entry_bb_width:.3f}, "
                                f"regime={entry_regime}")
                else:
                    # State yoksa aktif sisteme göre tahmin et
                    sync_entry_mode = ""
                    if self._config.get("system_n.enabled", False):
                        sync_entry_mode = "SYSTEM_N"
                    elif self._config.get("system_m.enabled", False):
                        sync_entry_mode = "SYSTEM_M"
                    elif self._config.get("system_j.enabled", False):
                        sync_entry_mode = "SYSTEM_J"
                    elif self._config.get("system_i.enabled", False):
                        sync_entry_mode = "SYSTEM_I"
                    elif self._config.get("system_b.enabled", False):
                        sync_entry_mode = "SYSTEM_B"

                # SL/TP override: saved state varsa veya System I ise kendi degerlerini kullan
                sync_sl_override = 0.0
                sync_tp_override = 0.0
                if prev and sync_entry_mode == "SYSTEM_I":
                    # State'ten SL/TP fiyatlarini koru
                    sync_sl_override = prev.get("initial_sl", 0.0)
                    sync_tp_override = prev.get("initial_tp", 0.0)
                elif sync_entry_mode == "SYSTEM_I" and entry_bb_width > 0:
                    # State yok ama entry_bb_width (SL%) var → hesapla
                    sl_pct_frac = entry_bb_width / 100
                    tp_pct_frac = entry_bb_width / 100  # en az SL kadar TP
                    if side == OrderSide.BUY_LONG:
                        sync_sl_override = entry_price * (1 - sl_pct_frac)
                        sync_tp_override = entry_price * (1 + tp_pct_frac)
                    else:
                        sync_sl_override = entry_price * (1 + sl_pct_frac)
                        sync_tp_override = entry_price * (1 - tp_pct_frac)

                # Open position in manager
                self._position_mgr.open_position(
                    symbol, side, entry_price, size, atr,
                    leverage=leverage,
                    margin_usdt=margin,
                    entry_regime=entry_regime,
                    entry_score=entry_score,
                    entry_confluence=entry_confluence,
                    entry_adx=entry_adx,
                    entry_rsi=entry_rsi,
                    entry_regime_confidence=entry_regime_confidence,
                    entry_bb_width=entry_bb_width,
                    entry_mode=sync_entry_mode,
                    initial_sl_override=sync_sl_override,
                    initial_tp_override=sync_tp_override,
                )
                logger.info(f"Synced API position: {symbol} {side.value} "
                            f"qty={size} entry={entry_price} lev={leverage}x "
                            f"margin={margin:.2f} "
                            f"score={entry_score:+.1f}")

                # Binance'de bu sembol için zaten emir var mı kontrol et
                # Varsa dokunma — verify döngüsü eksikleri düzeltir
                # Yoksa (gerçekten korumasız) → emir koy
                logger.info(f"[SYNC] {symbol}: SL/trailing emir kontrolü yapılıyor...")
                try:
                    existing = self._rest.get_symbol_open_orders_combined(symbol) \
                        if hasattr(self, '_rest') and self._rest else None
                    if existing is None and self._order_executor and hasattr(self._order_executor, '_rest'):
                        existing = self._order_executor._rest.get_symbol_open_orders_combined(symbol)
                    has_orders = existing is not None and len(existing) > 0
                    if has_orders:
                        logger.info(f"[SYNC] {symbol}: {len(existing)} mevcut emir var — "
                                    f"dokunmuyorum (verify düzeltir)")
                    else:
                        pos_obj = self._position_mgr.get_position(symbol)
                        if pos_obj and atr > 0:
                            # Entry mode'a gore dogru fonksiyonu cagir
                            if sync_entry_mode in ("SYSTEM_M", "SYSTEM_N"):
                                logger.info(f"[SYNC] {symbol}: {sync_entry_mode} sinyal bazlı — "
                                            f"SL/trailing konmayacak")
                            elif sync_entry_mode == "SYSTEM_I":
                                logger.info(f"[SYNC] {symbol}: System I repair delegated to verify")
                                # System I emirlerini verify'e birak — dogru G bazli hesaplama yapar
                            else:
                                self._place_initial_trailing(symbol, pos_obj, entry_price, atr)
                except Exception as e:
                    logger.warning(f"[SYNC] {symbol}: SL/trailing placement failed: {e}")

            logger.info(f"Synced {len(api_positions)} API position(s)")

            # İlk verify döngüsü eksik emirleri tekrar kontrol edecek
            self._last_order_verify_time = 0.0  # hemen verify tetikle

        except Exception as e:
            logger.error(f"Failed to sync API positions: {e}")

    def _cleanup_orphan_orders(self, api_positions: list) -> None:
        """Cancel open orders for symbols that have NO open position.
        These are orphan SL/trailing stop orders left from previously closed positions."""
        if not self._order_executor or not hasattr(self._order_executor, '_rest'):
            return

        rest = self._order_executor._rest
        # Symbols that actually have open positions
        position_symbols = set()
        for p in api_positions:
            amt = float(p.get("positionAmt", 0))
            if amt != 0:
                position_symbols.add(p.get("symbol", ""))

        try:
            # Get ALL open orders across all symbols
            all_orders = rest.get_open_orders()  # no symbol = all
            algo_orders = rest.get_algo_open_orders()  # algo/conditional orders

            # Find symbols with orders but no position
            order_symbols = set()
            for o in (all_orders or []):
                order_symbols.add(o.get("symbol", ""))
            for o in (algo_orders or []):
                order_symbols.add(o.get("symbol", ""))

            orphan_symbols = order_symbols - position_symbols
            if not orphan_symbols:
                logger.info("[STARTUP] No orphan orders found")
                return

            for symbol in orphan_symbols:
                try:
                    result = rest.cancel_all_orders(symbol)
                    logger.warning(f"[STARTUP] Cancelled orphan orders for {symbol} "
                                   f"(no open position): {result}")
                except Exception as e:
                    logger.warning(f"[STARTUP] Failed to cancel orphan orders for {symbol}: {e}")

            logger.info(f"[STARTUP] Cleaned up orphan orders for {len(orphan_symbols)} symbol(s): "
                        f"{', '.join(orphan_symbols)}")

        except Exception as e:
            logger.warning(f"[STARTUP] Orphan order cleanup failed: {e}")

    def _update_synced_tp_sl(self) -> None:
        """Update TP/SL orders for synced positions using fee-aware calculations."""
        if not self._order_executor or not hasattr(self._order_executor, 'update_tp_sl'):
            return

        battle_mode = self._config.get("strategy.battle_mode", False)

        for symbol, pos in self._position_mgr.get_all_positions().items():
            if pos.leverage <= 1:
                continue

            lev = pos.leverage
            strat_cfg = self._config.get("strategy", {})
            liq_factor = strat_cfg.get("liq_factor", 70) / 100.0
            liq_pct = (1.0 / lev) * liq_factor

            entry_side = "BUY" if pos.side == OrderSide.BUY_LONG else "SELL"

            sl_liq_pct2 = strat_cfg.get("sl_liq_percent", 50) / 100.0
            tp_liq_mult2 = strat_cfg.get("tp_liq_multiplier", 3.0)

            if battle_mode:
                # Battle mode: emergency SL only, NO TP
                em_liq_pct = strat_cfg.get("emergency_liq_percent", 70) / 100.0
                sl_price_pct = liq_pct * em_liq_pct
                sl_roi = round(sl_price_pct * lev * 100, 1)
                try:
                    self._order_executor.update_tp_sl(
                        symbol=symbol,
                        entry_side=entry_side,
                        qty=pos.size,
                        entry_price=pos.entry_price,
                        leverage=lev,
                        tp_roi_pct=None,
                        sl_roi_pct=sl_roi,
                    )
                    logger.info(f"Battle mode: SL only for {symbol}: SL_ROI={sl_roi}% (no TP)")
                except Exception as e:
                    logger.warning(f"Failed to update SL for {symbol}: {e}")
            else:
                # SL only if sl_enabled
                sl_roi = None
                if strat_cfg.get("sl_enabled", True):
                    sl_price_pct = liq_pct * sl_liq_pct2
                    sl_roi = round(sl_price_pct * lev * 100, 1)

                # Only send TP to Binance if tp_enabled in config
                tp_roi = None
                if strat_cfg.get("tp_enabled", False):
                    tp_price_pct = liq_pct * tp_liq_mult2
                    tp_roi = round(tp_price_pct * lev * 100, 1)

                if sl_roi is not None or tp_roi is not None:
                    try:
                        self._order_executor.update_tp_sl(
                            symbol=symbol,
                            entry_side=entry_side,
                            qty=pos.size,
                            entry_price=pos.entry_price,
                            leverage=lev,
                            tp_roi_pct=tp_roi,
                            sl_roi_pct=sl_roi,
                        )
                        sl_str = f"SL_ROI={sl_roi}%" if sl_roi else "no SL"
                        tp_str = f" TP_ROI={tp_roi}%" if tp_roi else " (no TP)"
                        logger.info(f"Updated {symbol}: {sl_str}{tp_str}")
                    except Exception as e:
                        logger.warning(f"Failed to update TP/SL for {symbol}: {e}")
                else:
                    # sl_enabled=False ve tp_enabled=False — software SL/TP yok
                    # DİKKAT: cancel_all_orders ÇAĞIRMA — server SL + trailing korunmalı!
                    logger.info(f"[SL+TP DISABLED] {symbol}: software SL/TP kapalı, "
                                f"server SL + trailing korumaya devam ediyor")

    # ──── Server-side Trailing Stop ────
    #
    # Pozisyon açılışında: SL + TRAILING_STOP_MARKET hemen gönderilir
    # Sonraki döngülerde: değişen koşullara göre güncellenir/silinir/yenisi konur
    #
    # Mimari: Çift katmanlı koruma
    #   Katman 1: Yazılımsal trailing (akıllı — sinyal, renew, confluence)
    #   Katman 2: Server-side trailing (güvenlik ağı — program çökerse)
    #
    # Yazılım her 30 saniyede server emrini günceller:
    #   - İlk kez trailing aktif olunca → server'a gönder
    #   - Trailing renew olunca → eski emri sil, yenisini gönder
    #   - Sinyal güçlüyse → callback'i genişlet (daha sabırlı)
    #   - Sinyal zayıflarsa → callback'i daralt (daha hızlı kar al)
    #   - Pozisyon kapanınca → server emrini temizle

    def _place_initial_trailing(self, symbol: str, pos, entry_price: float,
                                atr: float) -> None:
        """Pozisyon açılır açılmaz Binance'e SL + trailing emir gönderir.

        Katman 1: 2×ATR STOP_MARKET (crash koruması — server SL)
        Katman 2: TRAILING_STOP_MARKET: 4×ATR'de aktif, 1×ATR geri gelme (kar alma).

        ÖNEMLİ: Önce mevcut emirleri temizler (duplikasyon önleme).
        """
        try:
            rest = self._order_executor._rest
            pp = self._order_executor._get_price_precision(symbol)

            # KIRMIZI KURAL 3: Mevcut emirler SİLİNMEZ.
            # Yeni pozisyon açılışında zaten emir olmamalı.
            strat = self._config.get("strategy", {})
            activate_mult = strat.get("trailing_atr_activate_mult", 3.0)
            distance_mult = strat.get("trailing_atr_distance_mult", 1.0)

            is_long = pos.side == OrderSide.BUY_LONG
            close_side = "SELL" if is_long else "BUY"

            # ATR=0 fallback: entry fiyatının %2'si
            if atr <= 0 and entry_price > 0:
                atr = entry_price * 0.02
                logger.warning(f"[INITIAL TRAILING] {symbol}: ATR=0, "
                               f"fallback ATR=%2 of entry = {atr:.8f}")

            atr_pct = atr / entry_price * 100 if entry_price > 0 and atr > 0 else 0

            # === REGIME/MODE OVERRIDES ===
            entry_mode = getattr(pos, 'entry_mode', 'TREND')
            entry_regime = getattr(pos, 'entry_regime', '')  # HER ZAMAN tanımla

            if entry_mode == "MEAN_REVERSION" and strat.get("mr_trailing_enabled", True):
                activate_mult = strat.get("mr_trailing_activate_atr", 1.5)
                distance_mult = strat.get("mr_trailing_callback_atr", 0.5)
                logger.info(f"[MR TRAILING] {symbol}: MR override → "
                            f"trail={activate_mult}/{distance_mult}xATR")
            elif strat.get("adx_regime_enabled", False) and entry_regime in (
                    "RANGING", "WEAK_TREND", "STRONG_TREND"):
                prefix = {
                    "RANGING": "adx_regime_ranging",
                    "WEAK_TREND": "adx_regime_weak",
                    "STRONG_TREND": "adx_regime_strong",
                }[entry_regime]
                activate_mult = strat.get(f"{prefix}_trail_activate_atr", activate_mult)
                distance_mult = strat.get(f"{prefix}_trail_callback_atr", distance_mult)
                logger.info(f"[ADX REGIME] {symbol}: {entry_regime} → "
                            f"SL={strat.get(f'{prefix}_sl_atr', 2.0)}xATR, "
                            f"trail={activate_mult}/{distance_mult}xATR")

            # === SERVER SL: 2×ATR STOP_MARKET (crash koruması — HER ZAMAN aktif) ===
            # Not: sl_enabled sadece software SL'yi kontrol eder, server SL her zaman gönderilir
            sl_atr_mult = strat.get("server_sl_atr_mult", 2.0)
            if strat.get("adx_regime_enabled", False) and entry_regime in (
                    "RANGING", "WEAK_TREND", "STRONG_TREND"):
                prefix = {"RANGING": "adx_regime_ranging",
                          "WEAK_TREND": "adx_regime_weak",
                          "STRONG_TREND": "adx_regime_strong"}[entry_regime]
                sl_atr_mult = strat.get(f"{prefix}_sl_atr", sl_atr_mult)

            sl_placed = False
            sl_price = 0.0
            if atr > 0 and entry_price > 0:
                if is_long:
                    sl_price = round(entry_price - (atr * sl_atr_mult), pp)
                else:
                    sl_price = round(entry_price + (atr * sl_atr_mult), pp)
                try:
                    rest.place_order(
                        symbol=symbol,
                        side=close_side,
                        order_type="STOP_MARKET",
                        quantity=pos.size,
                        stop_price=sl_price,
                        reduce_only=True,
                    )
                    sl_placed = True
                    logger.info(f"[SERVER SL] {symbol}: {sl_atr_mult}xATR SL @ {sl_price} "
                                f"({'long' if is_long else 'short'}, entry={entry_price})")
                except Exception as sl_err:
                    logger.error(f"[SERVER SL] {symbol}: SL placement FAILED: {sl_err}")
            else:
                logger.warning(f"[NO SERVER SL] {symbol}: ATR={atr}, entry={entry_price} — cannot place")

            # === TRAILING_STOP_MARKET: 3×ATR tetik, 1×ATR callback ===
            trailing_placed = False
            if atr > 0 and entry_price > 0:
                callback_pct = (atr * distance_mult) / entry_price * 100
            else:
                callback_pct = 1.0
            callback_pct = max(0.1, min(5.0, round(callback_pct, 1)))

            if is_long:
                activation_price = round(entry_price + (atr * activate_mult), pp)
            else:
                activation_price = round(entry_price - (atr * activate_mult), pp)

            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="TRAILING_STOP_MARKET",
                    quantity=pos.size,
                    stop_price=activation_price,
                    callback_rate=callback_pct,
                    reduce_only=True,
                )
                trailing_placed = True
            except Exception as trail_err:
                logger.error(f"[SERVER TRAILING] {symbol}: Trailing placement FAILED: {trail_err}")

            self._server_trailing[symbol] = {
                "callback_pct": callback_pct,
                "activation_price": activation_price,
                "sl_price": sl_price if sl_placed else 0,
                "timestamp": time.time(),
                "renewal_count": 0,
            }

            activate_pct = atr_pct * activate_mult
            activate_roi = activate_pct * pos.leverage

            logger.info(f"[SERVER TRAILING] {symbol}: "
                        f"aktivasyon={activation_price:.{pp}f} "
                        f"({activate_mult}xATR = %{activate_pct:.2f}, "
                        f"ROI %{activate_roi:.0f}) | "
                        f"callback={callback_pct:.1f}% ({distance_mult}xATR) | "
                        f"SL={'OK' if sl_placed else 'FAIL'} "
                        f"Trail={'OK' if trailing_placed else 'FAIL'}")

            # Eksik emir varsa erken verify tetikle
            if not sl_placed or not trailing_placed:
                self._last_order_verify_time = 0.0
                logger.warning(f"[INITIAL TRAILING] {symbol}: eksik emir var "
                               f"(SL={'OK' if sl_placed else 'FAIL'}, "
                               f"Trail={'OK' if trailing_placed else 'FAIL'}) "
                               f"— erken verify tetiklendi")

        except Exception as e:
            logger.error(f"CRITICAL: Initial server orders FAILED for {symbol}: {e}")
            import traceback
            logger.error(f"Server order traceback: {traceback.format_exc()}")
            # Retry once after 2 seconds — both SL AND trailing
            try:
                time.sleep(2)
                logger.info(f"[RETRY] Retrying server orders for {symbol}...")
                rest = self._order_executor._rest
                pp = self._order_executor._get_price_precision(symbol)
                strat = self._config.get("strategy", {})
                is_long = pos.side == OrderSide.BUY_LONG
                close_side = "SELL" if is_long else "BUY"
                sl_placed = False
                trailing_placed = False

                if atr > 0 and entry_price > 0:
                    # --- Retry SL ---
                    sl_atr_mult = strat.get("server_sl_atr_mult", 2.0)
                    if is_long:
                        sl_price = round(entry_price - (atr * sl_atr_mult), pp)
                    else:
                        sl_price = round(entry_price + (atr * sl_atr_mult), pp)
                    try:
                        rest.place_order(
                            symbol=symbol, side=close_side,
                            order_type="STOP_MARKET",
                            quantity=pos.size, stop_price=sl_price,
                            reduce_only=True,
                        )
                        sl_placed = True
                        logger.info(f"[RETRY OK] SL placed for {symbol} @ {sl_price}")
                    except Exception as sl_err:
                        logger.error(f"[RETRY] SL failed for {symbol}: {sl_err}")

                    # --- Retry Trailing ---
                    activate_mult = strat.get("trailing_atr_activate_mult", 3.0)
                    distance_mult = strat.get("trailing_atr_distance_mult", 1.0)
                    callback_pct = (atr * distance_mult) / entry_price * 100
                    callback_pct = max(0.1, min(5.0, round(callback_pct, 1)))
                    if is_long:
                        activation_price = round(entry_price + (atr * activate_mult), pp)
                    else:
                        activation_price = round(entry_price - (atr * activate_mult), pp)
                    try:
                        rest.place_order(
                            symbol=symbol, side=close_side,
                            order_type="TRAILING_STOP_MARKET",
                            quantity=pos.size,
                            stop_price=activation_price,
                            callback_rate=callback_pct,
                            reduce_only=True,
                        )
                        trailing_placed = True
                        logger.info(f"[RETRY OK] Trailing placed for {symbol} "
                                    f"activation={activation_price} callback={callback_pct}%")
                    except Exception as trail_err:
                        logger.error(f"[RETRY] Trailing failed for {symbol}: {trail_err}")

                    if sl_placed or trailing_placed:
                        self._server_trailing[symbol] = {
                            "callback_pct": callback_pct if trailing_placed else 1.0,
                            "activation_price": activation_price if trailing_placed else 0,
                            "sl_price": sl_price if sl_placed else 0,
                            "timestamp": time.time(),
                            "renewal_count": 0,
                        }

                if not sl_placed and not trailing_placed:
                    raise Exception("Both SL and trailing retry failed")

                # Partial success — force early verify to fill the gap
                if not sl_placed or not trailing_placed:
                    self._last_order_verify_time = 0.0
                    logger.warning(f"[RETRY] {symbol}: partial success "
                                   f"(SL={'OK' if sl_placed else 'FAIL'}, "
                                   f"trail={'OK' if trailing_placed else 'FAIL'}) "
                                   f"— early verify triggered")

            except Exception as retry_err:
                logger.critical(f"RETRY ALSO FAILED for {symbol}: {retry_err} "
                                f"— closing unprotected position!")
                try:
                    rest.place_order(
                        symbol=symbol, side=close_side,
                        order_type="MARKET",
                        close_position=True,
                    )
                    logger.info(f"[EMERGENCY CLOSE] {symbol} closed — no SL protection")
                    self._event_bus.publish(EventType.LOG_MESSAGE, {
                        "level": "CRITICAL",
                        "message": f"{symbol}: Server SL gonderilemedi, pozisyon KAPATILDI! Hata: {e}",
                    })
                    return
                except Exception as close_err:
                    logger.critical(f"EMERGENCY CLOSE FAILED for {symbol}: {close_err} "
                                    f"— MANUAL ACTION REQUIRED!")
            # Notify user via event bus
            self._event_bus.publish(EventType.LOG_MESSAGE, {
                "level": "CRITICAL",
                "message": f"DIKKAT: {symbol} icin server SL/trailing gonderilemedi! "
                           f"Pozisyon KORUMASIZ olabilir. Hata: {e}",
            })

    def _calc_trailing_callback(self, pos, current_price: float,
                                 confluence: dict = None) -> float:
        """Calculate trailing callback rate = sabit 1×ATR.
        Sinyal gücüne göre dinamik daraltma/genişletme KAPATILDI.
        Fiyat 1×ATR geri çekilmeden trailing tetiklemez.
        Returns callback % (0.1 to 5.0) for Binance TRAILING_STOP_MARKET."""
        atr = pos.atr_at_entry
        strat = self._config.get("strategy", {})
        distance_mult = strat.get("trailing_atr_distance_mult", 1.0)

        # Sabit callback = 1×ATR distance (sinyal gücüne göre değişmez)
        if atr > 0 and current_price > 0:
            callback = (atr * distance_mult) / current_price * 100
        else:
            callback = 1.0

        # Clamp to Binance limits (0.1% - 5.0%)
        return max(0.1, min(5.0, round(callback, 1)))

    # KIRMIZI KURAL 3: _sync_server_trailing, _send_server_trailing, _remove_server_trailing
    # fonksiyonları KALDIRILDI. Pozisyon açıkken server emirleri DEĞİŞTİRİLMEZ.
    # İlk konulan SL + trailing pozisyon kapanana kadar durur.
    # Eksik emir varsa _verify_server_orders → _place_missing_orders ile sadece eksik eklenir.

    # ──── Helpers ────

    def _wait(self, seconds: float) -> None:
        """Wait while checking if still running. Uses Event for efficient sleep."""
        self._stop_event.wait(timeout=seconds)

    # ──── Getters (for GUI) ────

    @property
    def state(self) -> ScannerState:
        return self._state

    @property
    def scan_count(self) -> int:
        return self._scan_count

    def get_scan_results(self) -> list[ScanResult]:
        return self._last_scan_results

    def get_mr_scan_results(self) -> list:
        """Return last Mean Reversion scan results (for GUI)."""
        return self._last_mr_results

    def get_candidate(self) -> ScanResult:
        return self._last_candidate

    def get_position_info(self) -> dict:
        return self._position_mgr.get_position_info()

    def get_all_positions(self) -> list[dict]:
        positions = self._position_mgr.get_all_positions_info()
        # Inject live market context (funding rate, OI, order book)
        for p in positions:
            sym = p.get("symbol", "")
            ctx = self._market_context.get(sym, {})
            p["funding_rate"] = ctx.get("funding_rate", 0.0)
            p["oi_change_pct"] = ctx.get("oi_change_pct", 0.0)
            p["ob_imbalance"] = ctx.get("ob_imbalance", 0.0)
            p["ob_thin_book"] = ctx.get("ob_thin_book", False)
        return positions

    def get_held_indicators(self) -> dict[str, dict]:
        """Get current indicator snapshots for held positions (for GUI)."""
        return dict(self._held_indicators)

    def get_last_trade(self) -> dict:
        return self._last_trade_result

    def get_position_manager(self) -> PositionManager:
        return self._position_mgr

    def get_banned_symbols(self) -> dict[str, dict]:
        """Get all banned/cooldown symbols with remaining time.
        Returns: {symbol: {"type": "cooldown"|"daily_ban", "remaining_s": float, "reason": str}}"""
        now = time.time()
        result = {}

        # Loss cooldown symbols
        for sym, ts in self._loss_cooldown_symbols.items():
            remaining = self._loss_cooldown_seconds - (now - ts)
            if remaining > 0:
                result[sym] = {
                    "type": "cooldown",
                    "remaining_s": remaining,
                    "reason": f"zarar cooldown ({remaining / 60:.0f}dk)",
                }

        # Daily ban symbols
        strat = self._config.get("strategy", {})
        limit = strat.get("coin_daily_loss_limit", 0)
        ban_hours = strat.get("coin_daily_ban_hours", 24)
        if limit > 0:
            cutoff = now - (ban_hours * 3600)
            for sym, timestamps in self._coin_loss_history.items():
                recent = [t for t in timestamps if t > cutoff]
                if len(recent) >= limit:
                    remaining_h = ban_hours - (now - recent[0]) / 3600
                    remaining_s = remaining_h * 3600
                    if remaining_s > 0:
                        result[sym] = {
                            "type": "daily_ban",
                            "remaining_s": remaining_s,
                            "reason": f"{len(recent)} zarar/{ban_hours}s ban ({remaining_h:.1f}s kaldi)",
                        }

        return result

    def get_system_m_results(self) -> list:
        """Return last System M (AlphaTrend) scan results (for GUI)."""
        return list(self._last_system_m_results)

    def get_system_m_decisions(self) -> list[dict]:
        """Return System M trade decision log (for GUI)."""
        return list(self._system_m_decisions)

    def _log_m_decision(self, symbol: str, signal: str, action: str,
                        detail: str = "", price: float = 0.0) -> None:
        """System M karar log'una kayıt ekle."""
        import time as _time
        entry = {
            "time": _time.time(),
            "symbol": symbol,
            "signal": signal,
            "action": action,
            "detail": detail,
            "price": price,
        }
        self._system_m_decisions.append(entry)
        if len(self._system_m_decisions) > 500:
            self._system_m_decisions = self._system_m_decisions[-500:]

    def get_system_n_results(self) -> list:
        """Return last System N scan results (for GUI).

        Her sonuca ban/cooldown durumu eklenir:
          ban_status: "" | "BAN" | "COOLDOWN"
          ban_detail: "" | açıklama metni (sebep + kalan süre)
        """
        results = list(self._last_system_n_results)
        if not results:
            return results

        now = time.time()
        sn_cfg = self._config.get("system_n", {})
        opt = sn_cfg.get("optional_features", {})

        ban_enabled = opt.get("coin_ban_enabled", False)
        ban_limit = opt.get("coin_daily_loss_limit", 3)
        ban_hours = opt.get("coin_daily_ban_hours", 8)

        cooldown_enabled = opt.get("loss_cooldown_enabled", False)
        cooldown_s = opt.get("loss_cooldown_seconds", 600)

        enriched = []
        for r in results:
            sym = getattr(r, "symbol", "") or (r.get("symbol", "") if isinstance(r, dict) else "")
            ban_status = ""
            ban_detail = ""

            # 1. Coin ban kontrolü
            if ban_enabled and ban_limit > 0 and sym in self._coin_loss_history:
                cutoff = now - (ban_hours * 3600)
                recent = [t for t in self._coin_loss_history[sym] if t > cutoff]
                if len(recent) >= ban_limit:
                    remaining_h = ban_hours - (now - recent[0]) / 3600
                    ban_status = "BAN"
                    ban_detail = (f"{len(recent)} zarar/{ban_hours}h "
                                  f"({remaining_h:.1f}h kaldi)")

            # 2. Loss cooldown kontrolü (ban yoksa)
            if not ban_status and cooldown_enabled and sym in self._loss_cooldown_symbols:
                elapsed = now - self._loss_cooldown_symbols[sym]
                remaining = cooldown_s - elapsed
                if remaining > 0:
                    ban_status = "COOLDOWN"
                    ban_detail = f"zarar sonrasi bekleme ({remaining:.0f}s kaldi)"

            # Dataclass'ı dict'e çevir + ban bilgisi ekle
            if isinstance(r, dict):
                row = dict(r)
            else:
                row = {k: getattr(r, k, None) for k in r.__dataclass_fields__}
            row["ban_status"] = ban_status
            row["ban_detail"] = ban_detail
            enriched.append(row)

        return enriched

    def get_system_n_decisions(self) -> list[dict]:
        """Return System N trade decision log (for GUI)."""
        return list(self._system_n_decisions)

    def _log_n_decision(self, symbol: str, signal: str, action: str,
                        detail: str = "", price: float = 0.0) -> None:
        """System N karar log'una kayıt ekle."""
        import time as _time
        entry = {
            "time": _time.time(),
            "symbol": symbol,
            "signal": signal,
            "action": action,
            "detail": detail,
            "price": price,
        }
        self._system_n_decisions.append(entry)
        if len(self._system_n_decisions) > 500:
            self._system_n_decisions = self._system_n_decisions[-500:]

    def get_system_b_results(self) -> list:
        """Return last System B scan results (for GUI)."""
        return self._last_system_b_results

    def get_system_d_results(self) -> list:
        """Return last System D scan results (for GUI)."""
        return self._last_system_d_results

    def get_system_e_results(self) -> list:
        """Return last System E scan results (for GUI)."""
        return self._last_system_e_results

    def get_system_f_results(self) -> list:
        """Return last System F (Son Kursun) scan results (for GUI)."""
        return self._last_system_f_results

    def place_breakeven_tp_all(self) -> list[dict]:
        """Tüm açık pozisyonlara breakeven TP emri gönder.

        TP fiyatı = entry_price + fee + spread + slippage (LONG)
                  = entry_price - fee - spread - slippage (SHORT)

        Mevcut SL emirlerine DOKUNMAZ. Sadece TAKE_PROFIT_MARKET ekler.
        Eğer zaten TP emri varsa, önce onu iptal edip yenisini gönderir.

        Returns: [{symbol, side, entry, tp_price, status, error}, ...]
        """
        results = []
        rest = self._rest
        if not rest:
            return [{"error": "REST client yok"}]

        positions = self._position_mgr.get_all_positions()
        if not positions:
            return [{"error": "Açık pozisyon yok"}]

        se = self._config.get("system_e", {})
        strat = self._config.get("strategy", {})

        # Fee hesabı: round-trip fee (giriş + çıkış)
        fee_rate = se.get("fee_rate", strat.get("fee_pct", 0.10) / 100)
        # fee_rate genellikle tek yön (0.0004 = %0.04), round-trip = 2x
        fee_roundtrip = fee_rate * 2  # giriş + çıkış

        # Spread tahmini: fee'nin yarısı kadar
        spread_pct = fee_roundtrip * 0.5

        # Slippage: sabit tahmin
        slippage_pct = 0.0003  # %0.03

        # Toplam maliyet: fee + spread + slippage
        total_cost_pct = fee_roundtrip + spread_pct + slippage_pct

        logger.info(f"[Breakeven TP] Maliyet: fee={fee_roundtrip*100:.4f}% "
                    f"+ spread={spread_pct*100:.4f}% + slip={slippage_pct*100:.4f}% "
                    f"= toplam {total_cost_pct*100:.4f}%")

        for symbol, pos in positions.items():
            entry_price = pos.entry_price
            pp = 2  # price precision

            if self._symbol_info_cache:
                try:
                    si = self._symbol_info_cache.get(symbol)
                    if si:
                        pp = si.price_precision
                except Exception:
                    pass

            # Breakeven TP: maliyet + minimum kâr tampon (%0.01 ekstra)
            buffer_pct = 0.0001  # %0.01 minimum kâr tamponu
            tp_offset = total_cost_pct + buffer_pct

            if pos.side == OrderSide.BUY_LONG:
                tp_price = round(entry_price * (1 + tp_offset), pp)
                close_side = "SELL"
            else:
                tp_price = round(entry_price * (1 - tp_offset), pp)
                close_side = "BUY"

            # Mevcut TP emirlerini iptal et (SL'ye DOKUNMA)
            try:
                # Algo emirleri kontrol et (TAKE_PROFIT_MARKET)
                algo_orders = rest.get_algo_open_orders(symbol) or []
                for o in algo_orders:
                    if o.get("type") == "TAKE_PROFIT_MARKET":
                        algo_id = o.get("algoId")
                        if algo_id:
                            try:
                                rest._signed_delete("/fapi/v1/algoOrder",
                                                    {"algoId": algo_id})
                                logger.info(f"[Breakeven TP] {symbol}: eski TP iptal (algoId={algo_id})")
                            except Exception as e:
                                logger.warning(f"[Breakeven TP] {symbol}: TP iptal hata: {e}")

                # Regular TP emirleri kontrol et
                regular_orders = rest.get_open_orders(symbol=symbol) or []
                for o in regular_orders:
                    if o.get("type") == "TAKE_PROFIT_MARKET":
                        oid = o.get("orderId")
                        if oid:
                            try:
                                rest._signed_delete("/fapi/v1/order",
                                                    {"symbol": symbol, "orderId": oid})
                                logger.info(f"[Breakeven TP] {symbol}: eski TP iptal (orderId={oid})")
                            except Exception as e:
                                logger.warning(f"[Breakeven TP] {symbol}: TP iptal hata: {e}")
            except Exception as e:
                logger.warning(f"[Breakeven TP] {symbol}: TP iptal kontrol hata: {e}")

            # Yeni TP emri gönder
            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=pos.size,
                    stop_price=tp_price,
                    reduce_only=True,
                )
                status = "OK"
                logger.info(f"[Breakeven TP] {symbol}: TAKE_PROFIT_MARKET @ {tp_price} "
                            f"(entry={entry_price}, offset={tp_offset*100:.4f}%)")
            except Exception as e:
                status = f"HATA: {e}"
                logger.error(f"[Breakeven TP] {symbol}: FAILED: {e}")

            results.append({
                "symbol": symbol,
                "side": pos.side.value,
                "entry_price": entry_price,
                "tp_price": tp_price,
                "offset_pct": tp_offset * 100,
                "status": status,
            })

        return results

    @property
    def is_running(self) -> bool:
        return self._running

    # ══════════════════════════════════════════════════════════════════
    # ════  SYSTEM B — Dalga Analizi Tarama & Alım  ══════════════════
    # ══════════════════════════════════════════════════════════════════

    def _do_scanning_system_b(self) -> None:
        """System B tarama döngüsü: MTF veri çek → rejim → zigzag → skor → alım."""

        # Step 0: Check pending limit orders
        if self._pending_limits:
            self._check_pending_limits()

        # Step A: Check held positions
        if self._position_mgr.has_position:
            self._check_held_positions()

        self._scan_count += 1
        sb = self._config.get("system_b", {})
        max_pos = sb.get("max_pozisyon", 6)
        self._position_mgr._max_positions = max_pos

        logger.info(f"[SysB] Scan #{self._scan_count} starting... "
                    f"[positions: {self._position_mgr.position_count}/{max_pos}]")

        # 1. Symbol universe
        coin_sayisi = sb.get("coin_sayisi", 50)
        self._universe._top_n = coin_sayisi
        symbols = self._universe.refresh()
        if not symbols:
            logger.warning("[SysB] No symbols to scan")
            self._stop_event.wait(timeout=10)
            return

        # 2. Fetch klines: macro (1h) + micro (5m)
        buyuk_tf = sb.get("buyuk_tf", "1h")
        buyuk_mum = sb.get("buyuk_tf_mum", 168)
        kucuk_tf = sb.get("kucuk_tf", "5m")
        kucuk_mum = sb.get("kucuk_tf_mum", 288)

        klines_macro_map = self._fetcher.fetch_batch(
            symbols, buyuk_tf, buyuk_mum)
        klines_micro_map = self._fetcher.fetch_batch(
            symbols, kucuk_tf, kucuk_mum)

        # 3. Funding rates (1 batch call)
        market_ctx = self._fetch_funding_rates(list(klines_micro_map.keys()))

        # 4. Score all symbols
        results = self._system_b_scanner.score_batch(
            list(klines_micro_map.keys()),
            klines_macro_map, klines_micro_map, market_ctx)

        # 5. Rejim hysteresis: 3 ardışık aynı okuma gerekli
        teyit_n = sb.get("rejim_degisim_teyit", 3)
        for r in results:
            sym = r.symbol
            regime_str = r.regime.regime
            if sym not in self._system_b_regime_history:
                self._system_b_regime_history[sym] = []
            history = self._system_b_regime_history[sym]
            history.append(regime_str)
            # Son N okuma aynı değilse → UNDECIDED
            if len(history) >= teyit_n:
                recent = history[-teyit_n:]
                if len(set(recent)) != 1:
                    r.regime.regime = "UNDECIDED"
                    r.eligible = False
                    if not r.reject_reason:
                        r.reject_reason = "hysteresis_pending"
            elif len(history) < teyit_n:
                # Bootstrap: ilk okumada teyit=1 (belgede belirtilmiş)
                if len(history) < 1:
                    r.regime.regime = "UNDECIDED"
                    r.eligible = False
                    if not r.reject_reason:
                        r.reject_reason = "hysteresis_bootstrap"
            # History uzamasın
            if len(history) > teyit_n * 2:
                self._system_b_regime_history[sym] = history[-teyit_n:]

        self._last_system_b_results = results
        eligible = [r for r in results if r.eligible]

        # Log top 5
        for i, r in enumerate(results[:5]):
            logger.info(f"  [SysB] #{i+1} {r.symbol}: score={r.score:+.1f} "
                        f"dir={r.direction} regime={r.regime.regime} "
                        f"G={r.G:.3f}% I={r.I:.3f}% "
                        f"entry={r.entry.score}/3 wp={r.wave_position:.0%} "
                        f"lev={r.leverage}x "
                        f"eligible={r.eligible} reject={r.reject_reason or '-'}")

        logger.info(f"[SysB] Results: {len(results)} total, {len(eligible)} eligible")

        # Publish for GUI
        self._event_bus.publish(EventType.SCANNER_UPDATE, {
            "scan_count": self._scan_count,
            "total_symbols": len(symbols),
            "scored": len(results),
            "eligible": len(eligible),
            "mr_scored": 0,
            "mr_eligible": 0,
            "positions": self._position_mgr.position_count,
            "max_positions": max_pos,
            "system_b": True,
            "top_5": [
                {"symbol": r.symbol, "score": r.score, "direction": r.direction,
                 "regime": r.regime.regime, "G": r.G, "I": r.I}
                for r in results[:5]
            ],
            "candidate": eligible[0].symbol if eligible else None,
        })

        # Close-only mode
        if self._config.get("strategy.close_only", False):
            if self._position_mgr.has_position:
                logger.info(f"[SysB] Close-only: monitoring {self._position_mgr.position_count} pos")
            self._wait(sb.get("scan_interval_seconds", 300))
            return

        # 6. Filter candidates and buy
        now = time.time()
        # Cleanup expired cooldowns
        self._loss_cooldown_symbols = {
            s: t for s, t in self._loss_cooldown_symbols.items()
            if now - t < (sb.get("loss_cooldown_dakika", 30) * 60)
        }
        self._failed_symbols = {
            s: t for s, t in self._failed_symbols.items()
            if now - t < self._failed_cooldown
        }

        max_ayni_yon = sb.get("max_ayni_yon", 4)
        candidates = []
        for r in eligible:
            if r.symbol in self._failed_symbols:
                continue
            if r.symbol in self._loss_cooldown_symbols:
                continue
            # Coin daily ban kontrolü (System A ile aynı kural)
            coin_ok, coin_reason = self._check_coin_daily_ban(r.symbol)
            if not coin_ok:
                continue
            if self._position_mgr.is_holding(r.symbol):
                continue
            if r.symbol in self._pending_limits:
                continue

            # Yön dengesi
            dir_count = self._count_direction(r.direction)
            if dir_count >= max_ayni_yon:
                continue

            candidates.append(r)

        bought_any = False
        if candidates and self._position_mgr.has_capacity:
            # Balance kontrolü
            real_balance = 0.0
            if self._order_executor and hasattr(self._order_executor, "get_balance"):
                try:
                    real_balance = self._order_executor.get_balance()
                except Exception:
                    pass
            if real_balance > 0 and real_balance < 0.30:
                logger.info(f"[SysB] Balance too low ({real_balance:.2f}$)")
                self._wait(60)
                return

            for cand in candidates:
                total_occupied = self._position_mgr.position_count + len(self._pending_limits)
                if total_occupied >= max_pos:
                    break
                if not self._check_trade_frequency():
                    break

                logger.info(f"[SysB] Candidate: {cand.symbol} score={cand.score:+.1f} "
                            f"dir={cand.direction} G={cand.G:.3f}% lev={cand.leverage}x "
                            f"entry_type={cand.entry_type} regime={cand.regime.regime}")

                if self._do_buying_system_b(cand):
                    bought_any = True

        if bought_any:
            self._wait(5)
        else:
            # System B: 5dk tarama aralığı (strateji belgesinde belirtilmiş)
            scan_interval = sb.get("scan_interval_seconds", 300)
            self._wait(scan_interval)

    def _count_direction(self, direction: str) -> int:
        """Belirli yöndeki açık pozisyon sayısı."""
        count = 0
        target_side = OrderSide.BUY_LONG if direction == "LONG" else OrderSide.SELL_SHORT
        for pos in self._position_mgr.get_all_positions().values():
            if pos.side == target_side:
                count += 1
        return count

    def _do_buying_system_b(self, cand: 'SystemBScanResult') -> bool:
        """System B pozisyon açma: G bazlı kaldıraç, SL, trailing."""
        symbol = cand.symbol
        direction = cand.direction
        side = OrderSide.BUY_LONG if direction == "LONG" else OrderSide.SELL_SHORT
        sb = self._config.get("system_b", {})

        # 1. Fresh price
        price = cand.price
        try:
            ticker = self._rest.get_ticker_price(symbol)
            price = float(ticker.get("price", price))
        except Exception:
            pass

        # 2. Leverage
        leverage = cand.leverage
        lev_enabled = self._config.get("leverage.enabled", False)
        if not lev_enabled:
            leverage = 1

        # Binance max leverage kontrolü
        if leverage > 1:
            try:
                margin_est = 5.0  # tahmini margin
                available_max = self._rest.get_max_leverage(symbol, margin_est * leverage)
                if available_max < leverage:
                    leverage = available_max
                if leverage < sb.get("min_kaldirac", 2):
                    logger.info(f"[SysB] {symbol} max lev {leverage}x < min, skipping")
                    self._failed_symbols[symbol] = time.time()
                    return False
            except Exception as e:
                logger.warning(f"[SysB] {symbol} max leverage check failed: {e}")

        # 3. Position sizing: bakiye / 12
        real_balance = 0.0
        if self._order_executor and hasattr(self._order_executor, "get_balance"):
            try:
                real_balance = self._order_executor.get_balance()
            except Exception:
                pass

        divider = sb.get("portfoy_bolen", 12)
        locked_margin = self._position_mgr.get_total_margin()
        wallet = real_balance + locked_margin
        if wallet <= 0:
            wallet = self._config.get("risk.initial_balance", 5.0)

        if wallet >= 12.0:
            margin_usdt = round(wallet / divider, 2)
        elif wallet >= 4.0:
            margin_usdt = 1.0
        else:
            margin_usdt = round(wallet / 4, 2)

        if margin_usdt < 0.30:
            logger.warning(f"[SysB] Margin too low: {margin_usdt}$ (wallet={wallet:.2f}$)")
            return False
        if real_balance > 0 and margin_usdt > real_balance * 0.95:
            margin_usdt = round(real_balance * 0.95, 2)

        logger.info(f"[SysB] {symbol} sizing: wallet={wallet:.2f}$ → "
                    f"margin={margin_usdt}$ × {leverage}x")

        # 4. Set leverage + margin type on Binance
        if leverage > 1:
            try:
                self._rest.set_margin_type(symbol, "ISOLATED")
            except Exception:
                pass
            try:
                self._rest.set_leverage(symbol, leverage)
            except Exception as e:
                logger.warning(f"[SysB] set_leverage failed: {e}")

        # 5. Qty precision
        qty_precision = 3
        min_notional = 5.0
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    qty_precision = si.quantity_precision
                    min_notional = si.min_notional
            except Exception:
                pass

        notional_usdt = margin_usdt * leverage
        if notional_usdt < min_notional:
            needed = min_notional / leverage * 1.05
            if needed > margin_usdt * 2.0:
                logger.info(f"[SysB] {symbol} min notional needs {needed:.2f}$ > 2x target, skip")
                self._failed_symbols[symbol] = time.time()
                return False
            margin_usdt = round(needed, 2)
            notional_usdt = margin_usdt * leverage

        size_qty = round(notional_usdt / price, qty_precision) if price > 0 else 0

        # 6. SL/TP hesaplama
        G = cand.G
        is_ranging = cand.regime.regime in ("RANGING", "WEAK_RANGING")

        if is_ranging:
            # Ranging: sabit SL/TP (bant bazlı)
            sl_price = cand.ranging_sl
            tp_price = cand.ranging_tp
            sl_pct = abs(price - sl_price) / price * 100 if price > 0 else 3.0
        else:
            # Trend: G bazlı SL, trailing kullanılır
            sl_carpan = sb.get("sl_carpan", 1.5)
            sl_pct = sl_carpan * G + sb.get("slippage_buffer", 0.1)
            if side == OrderSide.BUY_LONG:
                sl_price = price * (1 - sl_pct / 100)
            else:
                sl_price = price * (1 + sl_pct / 100)
            tp_price = 0.0  # trailing kullanılır, sabit TP yok (clamped trailing hariç)
            if cand.ranging_tp > 0 and cand.trailing_callback_pct == 0:
                # Trailing kullanılamadı → sabit TP
                tp_price = cand.ranging_tp

        # 6b. Min SL mesafesi: fee+spread koruması
        fee_rate = sb.get("fee_rate", 0.0004)
        fee_roundtrip = fee_rate * 2 * 100  # % cinsinden
        spread_est = fee_roundtrip * 0.5
        min_sl_pct = 3.0 * (fee_roundtrip + spread_est)
        if sl_pct < min_sl_pct:
            logger.warning(f"[SysB] {symbol} SL too tight "
                           f"(SL={sl_pct:.3f}% < min={min_sl_pct:.3f}% "
                           f"= 3×(fee+spread)). Skipping.")
            self._failed_symbols[symbol] = time.time()
            return False

        # 7. Execute order
        entry_type = cand.entry_type
        use_market = sb.get("hemen_gir_market", True)

        if entry_type == "MARKET_ENTER" and use_market:
            order_type = OrderType.MARKET
        else:
            # Limit emir
            order_type = OrderType.LIMIT
            buffer_pct = sb.get("limit_buffer_yuzde", 0.05) / 100
            if side == OrderSide.BUY_LONG:
                price = price * (1 - buffer_pct)
            else:
                price = price * (1 + buffer_pct)

        try:
            if not self._order_executor:
                logger.error("[SysB] No order executor")
                return False

            self._order_executor.execute_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                size=size_qty,
                price=price if order_type == "LIMIT" else None,
                leverage=leverage,
                sl_percent=0,  # SL handled by _place_initial_trailing_system_b
                tp_percent=0,  # TP handled separately
            )
        except Exception as e:
            logger.error(f"[SysB] Order failed for {symbol}: {e}")
            self._failed_symbols[symbol] = time.time()
            return False

        # 8. Open position in manager
        pos = self._position_mgr.open_position(
            symbol=symbol,
            side=side,
            price=price,
            size=size_qty,
            atr=cand.atr,
            leverage=leverage,
            margin_usdt=margin_usdt,
            timeframe=sb.get("kucuk_tf", "5m"),
            entry_score=abs(cand.score),
            entry_confluence=0.0,
            entry_adx=0.0,
            entry_rsi=cand.rsi,
            entry_regime=cand.regime.regime,
            entry_regime_confidence=cand.regime.confidence,
            entry_mode="SYSTEM_B",
        )

        if pos:
            # Store System B specific data on position
            pos.entry_bb_width = cand.G  # G değerini bb_width alanında sakla (reuse)
            self._trade_timestamps.append(time.time())

            # 9. Place SL + trailing/TP orders on Binance
            self._place_initial_trailing_system_b(
                symbol, pos, price, cand)

            logger.info(f"[SysB] ✓ Opened {direction} {symbol} @ {price:.6f} "
                        f"lev={leverage}x margin={margin_usdt}$ "
                        f"G={G:.3f}% SL={sl_pct:.2f}% "
                        f"regime={cand.regime.regime}")

            self._event_bus.publish(EventType.ORDER_PLACED, {
                "symbol": symbol,
                "side": direction,
                "price": price,
                "size": size_qty,
                "leverage": leverage,
                "system": "B",
            })
            return True

        return False

    def _place_initial_trailing_system_b(self, symbol: str, pos,
                                         entry_price: float,
                                         cand: 'SystemBScanResult') -> None:
        """System B: G bazlı SL + trailing emir gönder."""
        try:
            rest = self._order_executor._rest
            pp = self._order_executor._get_price_precision(symbol)
        except Exception as e:
            logger.error(f"[SysB] Cannot access REST client: {e}")
            return

        sb = self._config.get("system_b", {})
        is_long = pos.side == OrderSide.BUY_LONG
        close_side = "SELL" if is_long else "BUY"
        G = cand.G
        is_ranging = cand.regime.regime in ("RANGING", "WEAK_RANGING")

        # ── SERVER SL ──
        sl_carpan = sb.get("sl_carpan", 1.5)
        min_sl_offset = entry_price * (sl_carpan * G / 100) if G > 0 else entry_price * 0.01

        if is_ranging:
            sl_price = cand.ranging_sl
            # Minimum SL mesafesi koruması (ranging SL çok yakın olabilir)
            if is_long:
                max_sl = entry_price - min_sl_offset
                sl_price = min(sl_price, max_sl)
            else:
                min_sl = entry_price + min_sl_offset
                sl_price = max(sl_price, min_sl)
            sl_price = round(sl_price, pp)
        else:
            sl_offset = entry_price * (sl_carpan * G / 100)
            if is_long:
                sl_price = round(entry_price - sl_offset, pp)
            else:
                sl_price = round(entry_price + sl_offset, pp)

        sl_dist_pct = abs(entry_price - sl_price) / entry_price * 100

        try:
            rest.place_order(
                symbol=symbol,
                side=close_side,
                order_type="STOP_MARKET",
                quantity=pos.size,
                stop_price=sl_price,
                reduce_only=True,
            )
            logger.info(f"[SysB SL] {symbol}: SL @ {sl_price} "
                        f"(mesafe={sl_dist_pct:.2f}%, {sl_carpan}×G)")
        except Exception as e:
            logger.error(f"[SysB SL] {symbol}: FAILED: {e}")

        if is_ranging:
            # ── RANGING: SABİT TP (trailing yok) ──
            tp_price = round(cand.ranging_tp, pp)
            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=pos.size,
                    stop_price=tp_price,
                    reduce_only=True,
                )
                logger.info(f"[SysB TP] {symbol}: TP @ {tp_price} (ranging)")
            except Exception as e:
                logger.error(f"[SysB TP] {symbol}: FAILED: {e}")
        else:
            # ── TREND: TRAILING_STOP_MARKET ──
            if cand.trailing_callback_pct > 0:
                tetik_carpan = sb.get("tetik_carpan", 2.5)
                activation_offset = entry_price * (tetik_carpan * G / 100)
                if is_long:
                    activation_price = round(entry_price + activation_offset, pp)
                else:
                    activation_price = round(entry_price - activation_offset, pp)

                callback_pct = max(0.1, min(5.0, round(cand.trailing_callback_pct, 1)))

                try:
                    rest.place_order(
                        symbol=symbol,
                        side=close_side,
                        order_type="TRAILING_STOP_MARKET",
                        quantity=pos.size,
                        stop_price=activation_price,
                        callback_rate=callback_pct,
                        reduce_only=True,
                    )
                    logger.info(f"[SysB TRAIL] {symbol}: trigger={activation_price} "
                                f"callback={callback_pct}%")
                    self._server_trailing[symbol] = {
                        "callback_pct": callback_pct,
                        "activation_price": activation_price,
                        "timestamp": time.time(),
                    }
                except Exception as e:
                    logger.error(f"[SysB TRAIL] {symbol}: FAILED: {e}")
            elif cand.ranging_tp > 0:
                # Trailing kullanılamadı → sabit TP (I × 0.8)
                tp_price = round(cand.ranging_tp, pp)
                try:
                    rest.place_order(
                        symbol=symbol,
                        side=close_side,
                        order_type="TAKE_PROFIT_MARKET",
                        quantity=pos.size,
                        stop_price=tp_price,
                        reduce_only=True,
                    )
                    logger.info(f"[SysB TP] {symbol}: fixed TP @ {tp_price} "
                                f"(trailing unavailable)")
                except Exception as e:
                    logger.error(f"[SysB TP] {symbol}: FAILED: {e}")

    # ══════════════════════════════════════════════════════════════════
    # ════  SYSTEM D — Sıralı Coin Analiz & Trade  ════════════════════
    # ══════════════════════════════════════════════════════════════════

    def _do_scanning_system_d(self) -> None:
        """System D tarama döngüsü: Top N coin → MTF analiz → sıralı alım."""

        # Step 0: Check pending limit orders
        if self._pending_limits:
            self._check_pending_limits()

        # Step A: Check held positions
        if self._position_mgr.has_position:
            self._check_held_positions()

        self._scan_count += 1
        sd = self._config.get("system_d", {})
        max_pos = sd.get("max_pozisyon", 12)
        self._position_mgr._max_positions = max_pos

        logger.info(f"[SysD] Scan #{self._scan_count} starting... "
                    f"[positions: {self._position_mgr.position_count}/{max_pos}]")

        # 1. Symbol universe (hacim sıralı top N)
        coin_sayisi = sd.get("coin_sayisi", 50)
        self._universe._top_n = coin_sayisi
        symbols = self._universe.refresh()
        if not symbols:
            logger.warning("[SysD] No symbols to scan")
            self._stop_event.wait(timeout=10)
            return

        # 2. Zoom Diyafram: kademeli TF fetch (API tasarrufu)
        # Adım 1: Zoom aday TF'leri çek (5m, 15m, 30m, 1h — hızlı dirsek tespiti)
        # Adım 2: Seçilen optimal + mid + macro TF'leri çek
        from scanner.system_d_scanner import ZOOM_TF_LADDER
        mum_sayisi = sd.get("mikro_tf_mum", 200)

        # Zoom + yön TF'leri: 6 TF × N coin (makul API yükü)
        fetch_tfs = [
            ("5m", 5), ("15m", 15), ("30m", 30), ("1h", 60),  # zoom adayları
            ("4h", 240), ("1d", 1440),                          # yön büyük TF
        ]

        klines_all_tf: dict[str, dict[str, list]] = {sym: {} for sym in symbols}
        for tf_name, tf_min in fetch_tfs:
            tf_map = self._fetcher.fetch_batch(symbols, tf_name, mum_sayisi)
            for sym, klines in tf_map.items():
                # DataFrame → list of lists (system_d_scanner list bekliyor)
                if hasattr(klines, 'values'):
                    klines_all_tf[sym][tf_name] = klines.values.tolist()
                else:
                    klines_all_tf[sym][tf_name] = klines

        # 3. Funding rates (1 batch call)
        market_ctx = self._fetch_funding_rates(symbols)

        # 4. Volume map (for display)
        volume_map = {}
        try:
            tickers = self._rest.get_24h_ticker()
            for t in tickers:
                s = t.get("symbol", "")
                if s in klines_all_tf:
                    volume_map[s] = float(t.get("quoteVolume", 0))
        except Exception:
            pass

        # 5. Score all symbols (zoom diyafram ile)
        results = self._system_d_scanner.score_batch(
            symbols, klines_all_tf, market_ctx, volume_map)

        self._last_system_d_results = results
        eligible = [r for r in results if r.eligible]

        # Log top 5
        for i, r in enumerate(results[:5]):
            z = r.zoom
            logger.info(f"  [SysD] #{r.rank} {r.symbol}: score={r.score:+.1f} "
                        f"dir={r.direction} regime={r.regime} "
                        f"zoom={z.optimal_tf}(mid={z.mid_tf},mac={z.macro_tf}) "
                        f"G={r.leverage_calc.G:.3f}% SL={r.sl_pct:.2f}% "
                        f"lev={r.leverage}x "
                        f"eligible={r.eligible} reject={r.reject_reason or '-'}")

        logger.info(f"[SysD] Results: {len(results)} total, {len(eligible)} eligible")

        # Publish for GUI
        self._event_bus.publish(EventType.SCANNER_UPDATE, {
            "scan_count": self._scan_count,
            "total_symbols": len(symbols),
            "scored": len(results),
            "eligible": len(eligible),
            "mr_scored": 0,
            "mr_eligible": 0,
            "positions": self._position_mgr.position_count,
            "max_positions": max_pos,
            "system_d": True,
            "top_5": [
                {"symbol": r.symbol, "score": r.score, "direction": r.direction,
                 "regime": r.regime, "G": r.leverage_calc.G}
                for r in results[:5]
            ],
            "candidate": eligible[0].symbol if eligible else None,
        })

        # Close-only mode
        if self._config.get("strategy.close_only", False):
            if self._position_mgr.has_position:
                logger.info(f"[SysD] Close-only: monitoring {self._position_mgr.position_count} pos")
            self._wait(sd.get("scan_interval_seconds", 30))
            return

        # 6. Filter candidates and buy (sıralı, hacim öncelikli)
        now = time.time()
        self._failed_symbols = {
            s: t for s, t in self._failed_symbols.items()
            if now - t < self._failed_cooldown
        }

        # Cooldown filtresi
        cooldown_enabled = sd.get("cooldown_enabled", False)
        cooldown_secs = sd.get("cooldown_seconds", 600)
        if cooldown_enabled:
            self._loss_cooldown_symbols = {
                s: t for s, t in self._loss_cooldown_symbols.items()
                if now - t < cooldown_secs
            }

        # Yön dengesi: mevcut pozisyon sayıları
        yon_denge = sd.get("yon_denge_enabled", True)
        yon_oran = sd.get("yon_denge_oran", "2-1")
        max_ayni_yon = sd.get("max_ayni_yon", 8)
        try:
            yon_parts = yon_oran.split("-")
            yon_majority = int(yon_parts[0])
            yon_minority = int(yon_parts[1])
        except (ValueError, IndexError):
            yon_majority, yon_minority = 2, 1

        candidates = []
        for r in eligible:
            if r.symbol in self._failed_symbols:
                continue
            if self._position_mgr.is_holding(r.symbol):
                continue
            if r.symbol in self._pending_limits:
                continue
            # Cooldown kontrolü
            if cooldown_enabled and r.symbol in self._loss_cooldown_symbols:
                continue
            # Coin daily ban kontrolü (System A ile aynı kural)
            coin_ok, coin_reason = self._check_coin_daily_ban(r.symbol)
            if not coin_ok:
                continue
            # Yön dengesi kontrolü
            if yon_denge:
                dir_count = self._count_direction(r.direction)
                if dir_count >= max_ayni_yon:
                    continue
                # Oran kontrolü: majority <= X * (floor(minority / Y) + 1)
                opp_dir = "SHORT" if r.direction == "LONG" else "LONG"
                opp_count = self._count_direction(opp_dir)
                if dir_count >= yon_majority * (opp_count // yon_minority + 1):
                    continue
            candidates.append(r)

        bought_any = False
        if candidates and self._position_mgr.has_capacity:
            # Balance kontrolü
            real_balance = 0.0
            if self._order_executor and hasattr(self._order_executor, "get_balance"):
                try:
                    real_balance = self._order_executor.get_balance()
                except Exception:
                    pass
            if real_balance > 0 and real_balance < 0.30:
                logger.info(f"[SysD] Balance too low ({real_balance:.2f}$)")
                self._wait(60)
                return

            for cand in candidates:
                total_occupied = self._position_mgr.position_count + len(self._pending_limits)
                if total_occupied >= max_pos:
                    break
                if not self._check_trade_frequency():
                    break

                logger.info(f"[SysD] Candidate: {cand.symbol} score={cand.score:+.1f} "
                            f"dir={cand.direction} G={cand.leverage_calc.G:.3f}% "
                            f"lev={cand.leverage}x regime={cand.regime}")

                if self._do_buying_system_d(cand):
                    bought_any = True

        if bought_any:
            self._wait(5)
        else:
            scan_interval = sd.get("scan_interval_seconds", 30)
            self._wait(scan_interval)

    def _do_buying_system_d(self, cand: 'SystemDScanResult') -> bool:
        """System D pozisyon açma: G bazlı kaldıraç, limit emir."""
        symbol = cand.symbol
        direction = cand.direction
        side = OrderSide.BUY_LONG if direction == "LONG" else OrderSide.SELL_SHORT
        sd = self._config.get("system_d", {})

        # 1. Fresh price
        price = cand.price
        try:
            ticker = self._rest.get_ticker_price(symbol)
            price = float(ticker.get("price", price))
        except Exception:
            pass

        # 2. Leverage
        leverage = cand.leverage
        lev_enabled = self._config.get("leverage.enabled", False)
        if not lev_enabled:
            leverage = 1

        # Binance max leverage kontrolü
        if leverage > 1:
            try:
                margin_est = 5.0
                available_max = self._rest.get_max_leverage(symbol, margin_est * leverage)
                if available_max < leverage:
                    leverage = available_max
                if leverage < sd.get("min_kaldirac", 2):
                    logger.info(f"[SysD] {symbol} max lev {leverage}x < min, skipping")
                    self._failed_symbols[symbol] = time.time()
                    return False
            except Exception as e:
                logger.warning(f"[SysD] {symbol} max leverage check failed: {e}")

        # 3. Position sizing: bakiye / portfoy_bolen
        real_balance = 0.0
        if self._order_executor and hasattr(self._order_executor, "get_balance"):
            try:
                real_balance = self._order_executor.get_balance()
            except Exception:
                pass

        divider = sd.get("portfoy_bolen", 12)
        locked_margin = self._position_mgr.get_total_margin()
        wallet = real_balance + locked_margin
        if wallet <= 0:
            wallet = self._config.get("risk.initial_balance", 5.0)

        if wallet >= 12.0:
            margin_usdt = round(wallet / divider, 2)
        elif wallet >= 4.0:
            margin_usdt = 1.0
        else:
            margin_usdt = round(wallet / 4, 2)

        if margin_usdt < 0.30:
            logger.warning(f"[SysD] Margin too low: {margin_usdt}$ (wallet={wallet:.2f}$)")
            return False
        if real_balance > 0 and margin_usdt > real_balance * 0.95:
            margin_usdt = round(real_balance * 0.95, 2)

        logger.info(f"[SysD] {symbol} sizing: wallet={wallet:.2f}$ → "
                    f"margin={margin_usdt}$ × {leverage}x")

        # 4. Set leverage + margin type on Binance
        if leverage > 1:
            try:
                self._rest.set_margin_type(symbol, "ISOLATED")
            except Exception:
                pass
            try:
                self._rest.set_leverage(symbol, leverage)
            except Exception as e:
                logger.warning(f"[SysD] set_leverage failed: {e}")

        # 5. Qty precision + tick size
        qty_precision = 3
        min_notional = 5.0
        si = None
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    qty_precision = si.quantity_precision
                    min_notional = si.min_notional
            except Exception:
                pass

        notional_usdt = margin_usdt * leverage
        if notional_usdt < min_notional:
            needed = min_notional / leverage * 1.05
            if needed > margin_usdt * 2.0:
                logger.info(f"[SysD] {symbol} min notional needs {needed:.2f}$ > 2x target, skip")
                self._failed_symbols[symbol] = time.time()
                return False
            margin_usdt = round(needed, 2)
            notional_usdt = margin_usdt * leverage

        size_qty = round(notional_usdt / price, qty_precision) if price > 0 else 0

        # 6. Entry: Limit emir (0.1 × ATR offset) — tick size ile yuvarlama
        atr_offset = sd.get("limit_atr_offset", 0.1)
        atr = cand.atr
        offset_amount = atr * atr_offset

        if side == OrderSide.BUY_LONG:
            limit_price = price - offset_amount
        else:
            limit_price = price + offset_amount

        # Tick size validation (Binance -4014 hatası önlenir)
        if si:
            limit_price = si.validate_price(limit_price)
        else:
            limit_price = round(limit_price, 4)

        timeout_s = sd.get("limit_timeout_seconds", 60)

        # 6b. Min SL mesafesi: fee+spread koruması
        fee_rate = sd.get("fee_rate", 0.0004)
        fee_roundtrip = fee_rate * 2 * 100  # % cinsinden
        spread_est = fee_roundtrip * 0.5
        min_sl_pct = 3.0 * (fee_roundtrip + spread_est)
        if cand.sl_pct < min_sl_pct:
            logger.warning(f"[SysD] {symbol} SL too tight "
                           f"(SL={cand.sl_pct:.3f}% < min={min_sl_pct:.3f}% "
                           f"= 3×(fee+spread)). Skipping.")
            self._failed_symbols[symbol] = time.time()
            return False

        # 7. SL hesaplama (tick size ile yuvarlama)
        sl_pct = cand.sl_pct
        if side == OrderSide.BUY_LONG:
            sl_price = limit_price * (1 - sl_pct / 100)
        else:
            sl_price = limit_price * (1 + sl_pct / 100)
        if si:
            sl_price = si.validate_price(sl_price)
        else:
            sl_price = round(sl_price, 4)

        try:
            if not self._order_executor:
                logger.error("[SysD] No order executor")
                return False

            self._order_executor.execute_order(
                symbol=symbol,
                side=side,
                order_type=OrderType.LIMIT,
                size=size_qty,
                price=limit_price,
                leverage=leverage,
                sl_percent=0,  # SL handled by server-side orders
                tp_percent=0,
            )
        except Exception as e:
            logger.error(f"[SysD] Order failed for {symbol}: {e}")
            self._failed_symbols[symbol] = time.time()
            return False

        # 8. Track as pending limit
        self._pending_limits[symbol] = {
            "order_id": None,
            "limit_price": limit_price,
            "side": side,
            "size": size_qty,
            "atr": atr,
            "candidate": cand,
            "leverage": leverage,
            "margin_usdt": margin_usdt,
            "placed_time": time.time(),
            "timeout": timeout_s,
            "qty_precision": qty_precision,
            "entry_mode": "SYSTEM_D",
        }

        logger.info(f"[SysD] Limit order placed: {symbol} {direction} "
                    f"@ {limit_price} (offset={offset_amount:.6f}) "
                    f"size={size_qty} lev={leverage}x SL={sl_pct:.2f}% "
                    f"timeout={timeout_s}s")

        # Record trade timestamp
        self._trade_timestamps.append(time.time())

        return True

    def _open_position_system_d(self, symbol: str, cand: 'SystemDScanResult',
                                side: OrderSide, price: float,
                                size_qty: float, leverage: int,
                                margin_usdt: float) -> None:
        """System D pozisyonu aç ve server-side emirleri yerleştir."""
        sd = self._config.get("system_d", {})

        pos = self._position_mgr.open_position(
            symbol=symbol,
            side=side,
            price=price,
            size=size_qty,
            atr=cand.atr,
            leverage=leverage,
            margin_usdt=margin_usdt,
            timeframe=sd.get("mikro_tf", "5m"),
            entry_score=abs(cand.score),
            entry_confluence=0.0,
            entry_adx=cand.regime_result.adx if cand.regime_result else 0,
            entry_rsi=cand.direction_result.micro.rsi_value if cand.direction_result and cand.direction_result.micro else 50,
            entry_regime=cand.regime,
            entry_regime_confidence=0.0,
            entry_mode="SYSTEM_D",
            entry_bb_width=cand.leverage_calc.G,  # G değeri → SL override için
        )

        if not pos:
            logger.error(f"[SysD] Failed to open position for {symbol}")
            return

        # Server-side SL emri
        self._place_initial_orders_system_d(symbol, pos, cand, sd)

    def _place_initial_orders_system_d(self, symbol: str, pos,
                                        cand: 'SystemDScanResult',
                                        sd: dict) -> None:
        """Server-side SL + trailing/TP emirleri yerleştir."""
        rest = self._rest
        if not rest:
            return

        pp = 2  # price precision
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    pp = si.price_precision
            except Exception:
                pass

        close_side = "SELL" if pos.side == OrderSide.BUY_LONG else "BUY"
        entry_price = pos.entry_price

        # 1. SL emri (STOP_MARKET)
        sl_pct = cand.sl_pct
        if pos.side == OrderSide.BUY_LONG:
            sl_price = round(entry_price * (1 - sl_pct / 100), pp)
        else:
            sl_price = round(entry_price * (1 + sl_pct / 100), pp)

        try:
            rest.place_order(
                symbol=symbol,
                side=close_side,
                order_type="STOP_MARKET",
                quantity=pos.size,
                stop_price=sl_price,
                reduce_only=True,
            )
            logger.info(f"[SysD SL] {symbol}: STOP_MARKET @ {sl_price} "
                        f"(SL={sl_pct:.2f}%)")
        except Exception as e:
            logger.error(f"[SysD SL] {symbol}: FAILED: {e}")

        # 2. Rejime göre trailing veya sabit TP
        if cand.regime == "TREND" and cand.trailing_callback_pct > 0:
            # Trailing stop
            callback_pct = round(cand.trailing_callback_pct, 2)
            callback_pct = max(0.1, min(callback_pct, 5.0))

            activation_price = None
            if cand.trailing_trigger_pct > 0:
                if pos.side == OrderSide.BUY_LONG:
                    activation_price = round(entry_price * (1 + cand.trailing_trigger_pct / 100), pp)
                else:
                    activation_price = round(entry_price * (1 - cand.trailing_trigger_pct / 100), pp)

            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="TRAILING_STOP_MARKET",
                    quantity=pos.size,
                    callback_rate=callback_pct,
                    stop_price=activation_price,
                    reduce_only=True,
                )
                logger.info(f"[SysD Trail] {symbol}: callback={callback_pct}% "
                            f"activation={activation_price}")
            except Exception as e:
                logger.error(f"[SysD Trail] {symbol}: FAILED: {e}")
        elif cand.tp_pct > 0:
            # Sabit TP (RANGING)
            if pos.side == OrderSide.BUY_LONG:
                tp_price = round(entry_price * (1 + cand.tp_pct / 100), pp)
            else:
                tp_price = round(entry_price * (1 - cand.tp_pct / 100), pp)

            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=pos.size,
                    stop_price=tp_price,
                    reduce_only=True,
                )
                logger.info(f"[SysD TP] {symbol}: TAKE_PROFIT @ {tp_price} "
                            f"(TP={cand.tp_pct:.2f}%)")
            except Exception as e:
                logger.error(f"[SysD TP] {symbol}: FAILED: {e}")

    def _on_limit_filled_system_d(self, symbol: str, info: dict,
                                   fill_price: float) -> None:
        """Handle System D limit order fill: open position + server orders."""
        cand = info["candidate"]

        self._open_position_system_d(
            symbol, cand, info["side"], fill_price,
            info["size"], info["leverage"], info["margin_usdt"])

        if self._risk_manager:
            self._risk_manager.record_order(
                info["size"], fill_price,
                margin_usdt=info["margin_usdt"],
            )

        if self._order_logger:
            self._order_logger.log_order(
                symbol=symbol, side=info["side"].value, order_type="Limit",
                price=fill_price, size=info["size"],
                notional_usdt=info["size"] * fill_price,
                status="filled",
                trigger_source=f"system_d:{cand.score:+.0f}",
            )

        self._event_bus.publish(EventType.ORDER_PLACED, {
            "symbol": symbol, "side": info["side"].value,
            "size": info["size"], "price": fill_price,
            "order_type": "SYSTEM_D_LIMIT_FILLED",
        })

    def _place_missing_orders_system_d(self, symbol, pos, rest, pp,
                                        is_long, close_side,
                                        entry_price, actual_qty,
                                        entry_regime,
                                        need_sl, need_trailing):
        """System D: G bazlı eksik emir onarımı.

        G değeri pos.entry_bb_width alanında saklanır.
        Rejime göre: TREND → trailing, RANGING → sabit TP.
        """
        sd = self._config.get("system_d", {})
        G = getattr(pos, 'entry_bb_width', 0)
        if G <= 0:
            G = 0.5  # fallback

        sl_placed = False
        trail_placed = False

        # SL çarpanı: rejime göre
        if entry_regime in ("TREND",):
            sl_mult = sd.get("sl_carpan_trend", 1.5)
        else:
            sl_mult = sd.get("sl_carpan_ranging", 2.0)

        sl_pct = G * sl_mult

        # ── SL emri ──
        if need_sl:
            if is_long:
                sl_price = round(entry_price * (1 - sl_pct / 100), pp)
            else:
                sl_price = round(entry_price * (1 + sl_pct / 100), pp)
            try:
                result = rest.place_order(
                    symbol=symbol, side=close_side,
                    order_type="STOP_MARKET",
                    quantity=actual_qty, stop_price=sl_price,
                    reduce_only=True,
                )
                sl_placed = True
                logger.info(f"[SysD REPAIR] {symbol}: SL @ {sl_price} "
                            f"(G={G:.3f}%, {sl_mult}xG={sl_pct:.3f}%)")
            except Exception as e:
                logger.error(f"[SysD REPAIR] {symbol}: SL FAILED: {e}")

        # ── Trailing veya TP emri ──
        if need_trailing:
            if entry_regime in ("TREND",):
                # Trailing stop
                tetik_mult = sd.get("trailing_tetik_g_carpan", 2.0)
                mesafe_mult = sd.get("trailing_mesafe_g_carpan", 0.5)
                callback_pct = G * mesafe_mult
                callback_pct = max(0.1, min(callback_pct, 5.0))
                callback_pct = round(callback_pct, 2)

                tetik_pct = G * tetik_mult
                if is_long:
                    activation_price = round(entry_price * (1 + tetik_pct / 100), pp)
                else:
                    activation_price = round(entry_price * (1 - tetik_pct / 100), pp)

                try:
                    rest.place_order(
                        symbol=symbol, side=close_side,
                        order_type="TRAILING_STOP_MARKET",
                        quantity=actual_qty,
                        callback_rate=callback_pct,
                        stop_price=activation_price,
                        reduce_only=True,
                    )
                    trail_placed = True
                    logger.info(f"[SysD REPAIR] {symbol}: Trailing "
                                f"callback={callback_pct}% activation={activation_price}")
                except Exception as e:
                    logger.error(f"[SysD REPAIR] {symbol}: Trailing FAILED: {e}")
            else:
                # Ranging: sabit TP
                tp_mult = sd.get("ranging_tp_g_carpan", 3.0)
                tp_pct = G * tp_mult
                if is_long:
                    tp_price = round(entry_price * (1 + tp_pct / 100), pp)
                else:
                    tp_price = round(entry_price * (1 - tp_pct / 100), pp)

                try:
                    rest.place_order(
                        symbol=symbol, side=close_side,
                        order_type="TAKE_PROFIT_MARKET",
                        quantity=actual_qty,
                        stop_price=tp_price,
                        reduce_only=True,
                    )
                    trail_placed = True
                    logger.info(f"[SysD REPAIR] {symbol}: TP @ {tp_price} "
                                f"({tp_mult}xG={tp_pct:.3f}%)")
                except Exception as e:
                    logger.error(f"[SysD REPAIR] {symbol}: TP FAILED: {e}")

        return sl_placed, trail_placed

    # ══════════════════════════════════════════════════════════════════
    # ════  SYSTEM E — Yüksek Kaldıraç Yön Kesinliği  ═════════════════
    # ══════════════════════════════════════════════════════════════════

    def _do_scanning_system_e(self) -> None:
        """System E tarama döngüsü: Top N coin → 5 TF sinyal uyumu → max kaldıraç alım."""

        # Step 0: Check pending limit orders
        if self._pending_limits:
            self._check_pending_limits()

        # Step A: Check held positions
        if self._position_mgr.has_position:
            self._check_held_positions()

        self._scan_count += 1
        se = self._config.get("system_e", {})
        max_pos = se.get("max_pozisyon", 12)
        self._position_mgr._max_positions = max_pos

        logger.info(f"[SysE] Scan #{self._scan_count} starting... "
                    f"[positions: {self._position_mgr.position_count}/{max_pos}]")

        # 1. Symbol universe (hacim sıralı top N)
        coin_sayisi = se.get("coin_sayisi", 50)
        self._universe._top_n = coin_sayisi
        symbols = self._universe.refresh()
        if not symbols:
            logger.warning("[SysE] No symbols to scan")
            self._stop_event.wait(timeout=10)
            return

        # 2. 5 TF kline çek: 5m, 15m, 1h, 4h, 1d
        mum_sayisi = se.get("mum_sayisi", 200)
        fetch_tfs = [
            ("5m", 5), ("15m", 15), ("1h", 60), ("4h", 240), ("1d", 1440),
        ]

        klines_all_tf: dict[str, dict[str, list]] = {sym: {} for sym in symbols}
        for tf_name, tf_min in fetch_tfs:
            tf_map = self._fetcher.fetch_batch(symbols, tf_name, mum_sayisi)
            for sym, klines in tf_map.items():
                if hasattr(klines, 'values'):
                    klines_all_tf[sym][tf_name] = klines.values.tolist()
                else:
                    klines_all_tf[sym][tf_name] = klines

        # 3. Funding rates (1 batch call)
        market_ctx = self._fetch_funding_rates(symbols)

        # 4. Volume map
        volume_map = {}
        try:
            tickers = self._rest.get_24h_ticker()
            for t in tickers:
                s = t.get("symbol", "")
                if s in klines_all_tf:
                    volume_map[s] = float(t.get("quoteVolume", 0))
        except Exception:
            pass

        # 5. Score all symbols
        results = self._system_e_scanner.score_batch(
            symbols, klines_all_tf, market_ctx, volume_map)

        self._last_system_e_results = results
        eligible = [r for r in results if r.eligible]

        # Log top 5
        for i, r in enumerate(results[:5]):
            logger.info(f"  [SysE] #{r.rank} {r.symbol}: score={r.score:+.1f} "
                        f"dir={r.direction} uyum={r.aligned_count}/{r.total_tfs} "
                        f"guc={r.direction_strength:.2f} lev={r.leverage}x "
                        f"eligible={r.eligible} reject={r.reject_reason or '-'}")

        logger.info(f"[SysE] Results: {len(results)} total, {len(eligible)} eligible")

        # Publish for GUI
        self._event_bus.publish(EventType.SCANNER_UPDATE, {
            "scan_count": self._scan_count,
            "total_symbols": len(symbols),
            "scored": len(results),
            "eligible": len(eligible),
            "mr_scored": 0,
            "mr_eligible": 0,
            "positions": self._position_mgr.position_count,
            "max_positions": max_pos,
            "system_e": True,
            "candidate": eligible[0].symbol if eligible else None,
        })

        # Close-only mode
        if self._config.get("strategy.close_only", False):
            if self._position_mgr.has_position:
                logger.info(f"[SysE] Close-only: monitoring {self._position_mgr.position_count} pos")
            self._wait(se.get("scan_interval_seconds", 30))
            return

        # 6. Filter candidates and buy
        now = time.time()
        self._failed_symbols = {
            s: t for s, t in self._failed_symbols.items()
            if now - t < self._failed_cooldown
        }

        # Yön dengesi
        yon_denge = se.get("yon_denge_enabled", True)
        yon_oran = se.get("yon_denge_oran", "2-1")
        max_ayni_yon = se.get("max_ayni_yon", 8)
        try:
            yon_parts = yon_oran.split("-")
            yon_majority = int(yon_parts[0])
            yon_minority = int(yon_parts[1])
        except (ValueError, IndexError):
            yon_majority, yon_minority = 2, 1

        candidates = []
        for r in eligible:
            if r.symbol in self._failed_symbols:
                continue
            if self._position_mgr.is_holding(r.symbol):
                continue
            if r.symbol in self._pending_limits:
                continue
            # Coin daily ban
            coin_ok, coin_reason = self._check_coin_daily_ban(r.symbol)
            if not coin_ok:
                continue
            # Yön dengesi
            if yon_denge:
                dir_count = self._count_direction(r.direction)
                if dir_count >= max_ayni_yon:
                    continue
                opp_dir = "SHORT" if r.direction == "LONG" else "LONG"
                opp_count = self._count_direction(opp_dir)
                if dir_count >= yon_majority * (opp_count // yon_minority + 1):
                    continue
            candidates.append(r)

        bought_any = False
        if candidates and self._position_mgr.has_capacity:
            real_balance = 0.0
            if self._order_executor and hasattr(self._order_executor, "get_balance"):
                try:
                    real_balance = self._order_executor.get_balance()
                except Exception:
                    pass
            if real_balance > 0 and real_balance < 0.30:
                logger.info(f"[SysE] Balance too low ({real_balance:.2f}$)")
                self._wait(60)
                return

            for cand in candidates:
                total_occupied = self._position_mgr.position_count + len(self._pending_limits)
                if total_occupied >= max_pos:
                    break
                if not self._check_trade_frequency():
                    break

                logger.info(f"[SysE] Candidate: {cand.symbol} score={cand.score:+.1f} "
                            f"dir={cand.direction} uyum={cand.aligned_count}/{cand.total_tfs} "
                            f"lev={cand.leverage}x")

                if self._do_buying_system_e(cand):
                    bought_any = True

        if bought_any:
            self._wait(5)
        else:
            scan_interval = se.get("scan_interval_seconds", 30)
            self._wait(scan_interval)

    def _do_buying_system_e(self, cand: 'SystemEScanResult') -> bool:
        """System E pozisyon açma: max kaldıraç, market giriş, emergency SL + trailing."""
        symbol = cand.symbol
        direction = cand.direction
        side = OrderSide.BUY_LONG if direction == "LONG" else OrderSide.SELL_SHORT
        se = self._config.get("system_e", {})

        # 1. Fresh price
        price = cand.price
        try:
            ticker = self._rest.get_ticker_price(symbol)
            price = float(ticker.get("price", price))
        except Exception:
            pass

        # 2. Max leverage (Binance limiti ile)
        leverage = cand.leverage
        lev_enabled = self._config.get("leverage.enabled", False)
        if not lev_enabled:
            leverage = 1

        if leverage > 1:
            try:
                margin_est = 5.0
                available_max = self._rest.get_max_leverage(symbol, margin_est * leverage)
                if available_max < leverage:
                    leverage = available_max
                if leverage < 2:
                    logger.info(f"[SysE] {symbol} max lev {leverage}x too low, skipping")
                    self._failed_symbols[symbol] = time.time()
                    return False
            except Exception as e:
                logger.warning(f"[SysE] {symbol} max leverage check failed: {e}")

        # 3. Position sizing: bakiye / portfoy_bolen (System A kuralları)
        real_balance = 0.0
        if self._order_executor and hasattr(self._order_executor, "get_balance"):
            try:
                real_balance = self._order_executor.get_balance()
            except Exception:
                pass

        divider = se.get("portfoy_bolen", 12)
        locked_margin = self._position_mgr.get_total_margin()
        wallet = real_balance + locked_margin
        if wallet <= 0:
            wallet = self._config.get("risk.initial_balance", 5.0)

        if wallet >= 12.0:
            margin_usdt = round(wallet / divider, 2)
        elif wallet >= 4.0:
            margin_usdt = 1.0
        else:
            margin_usdt = round(wallet / 4, 2)

        if margin_usdt < 0.30:
            logger.warning(f"[SysE] Margin too low: {margin_usdt}$")
            return False
        if real_balance > 0 and margin_usdt > real_balance * 0.95:
            margin_usdt = round(real_balance * 0.95, 2)

        logger.info(f"[SysE] {symbol} sizing: wallet={wallet:.2f}$ → "
                    f"margin={margin_usdt}$ × {leverage}x")

        # 4. Set leverage + margin type
        if leverage > 1:
            try:
                self._rest.set_margin_type(symbol, "ISOLATED")
            except Exception:
                pass
            try:
                self._rest.set_leverage(symbol, leverage)
            except Exception as e:
                logger.warning(f"[SysE] set_leverage failed: {e}")

        # 5. Qty precision
        qty_precision = 3
        min_notional = 5.0
        si = None
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    qty_precision = si.quantity_precision
                    min_notional = si.min_notional
            except Exception:
                pass

        notional_usdt = margin_usdt * leverage
        if notional_usdt < min_notional:
            needed = min_notional / leverage * 1.05
            if needed > margin_usdt * 2.0:
                logger.info(f"[SysE] {symbol} min notional needs {needed:.2f}$, skip")
                self._failed_symbols[symbol] = time.time()
                return False
            margin_usdt = round(needed, 2)
            notional_usdt = margin_usdt * leverage

        size_qty = round(notional_usdt / price, qty_precision) if price > 0 else 0

        # 6. MARKET giriş (hız önemli — limit bekleme yok)
        try:
            if not self._order_executor:
                logger.error("[SysE] No order executor")
                return False

            self._order_executor.execute_order(
                symbol=symbol,
                side=side,
                order_type=OrderType.MARKET,
                size=size_qty,
                price=price,
                leverage=leverage,
                sl_percent=0,  # SL yok, emergency server-side
                tp_percent=0,
            )
        except Exception as e:
            logger.error(f"[SysE] Order failed for {symbol}: {e}")
            self._failed_symbols[symbol] = time.time()
            return False

        # 7. Pozisyonu aç ve server emirlerini yerleştir
        self._open_position_system_e(
            symbol, cand, side, price, size_qty, leverage, margin_usdt)

        if self._risk_manager:
            self._risk_manager.record_order(
                size_qty, price, margin_usdt=margin_usdt)

        if self._order_logger:
            self._order_logger.log_order(
                symbol=symbol, side=side.value, order_type="Market",
                price=price, size=size_qty,
                notional_usdt=size_qty * price,
                status="filled",
                trigger_source=f"system_e:{cand.score:+.0f}",
            )

        self._trade_timestamps.append(time.time())
        return True

    def _open_position_system_e(self, symbol: str, cand: 'SystemEScanResult',
                                side: OrderSide, price: float,
                                size_qty: float, leverage: int,
                                margin_usdt: float) -> None:
        """System E pozisyonu aç ve server-side emirleri yerleştir."""
        se = self._config.get("system_e", {})

        # RSI from 5m TF
        rsi_val = 50.0
        for sig in cand.tf_signals:
            if sig.timeframe == "5m":
                rsi_val = sig.rsi_value
                break

        # ADX from 1h TF
        adx_val = 0.0
        for sig in cand.tf_signals:
            if sig.timeframe == "1h":
                adx_val = sig.adx_value
                break

        pos = self._position_mgr.open_position(
            symbol=symbol,
            side=side,
            price=price,
            size=size_qty,
            atr=cand.atr,
            leverage=leverage,
            margin_usdt=margin_usdt,
            timeframe="5m",
            entry_score=abs(cand.score),
            entry_confluence=0.0,
            entry_adx=adx_val,
            entry_rsi=rsi_val,
            entry_regime="",
            entry_regime_confidence=0.0,
            entry_mode="SYSTEM_E",
            entry_bb_width=0.0,
        )

        if not pos:
            logger.error(f"[SysE] Failed to open position for {symbol}")
            return

        # Server-side emirleri yerleştir
        self._place_initial_orders_system_e(symbol, pos, cand, se)

    def _place_initial_orders_system_e(self, symbol: str, pos,
                                        cand: 'SystemEScanResult',
                                        se: dict) -> None:
        """Server-side Emergency SL + Trailing Stop emirleri.

        GARANTİLİ: her iki emir de başarıyla gönderilmeli.
        - Emergency başarısız → pozisyon kapatılır (korunmasız kalınamaz)
        - Trailing başarısız → 3 deneme, hâlâ başarısız → uyarı (emergency korur)
        Placement sonrası Binance API ile doğrulama yapılır.
        """
        rest = self._rest
        if not rest:
            logger.error(f"[SysE] {symbol}: REST client yok, pozisyon kapatılıyor!")
            self._emergency_close_system_e(symbol, pos, "no_rest_client")
            return

        pp = 2
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    pp = si.price_precision
            except Exception:
                pass

        close_side = "SELL" if pos.side == OrderSide.BUY_LONG else "BUY"
        entry_price = pos.entry_price
        max_retries = 3

        # ═══ 1. Emergency STOP_MARKET (likidasyon %80) — ZORUNLU ═══
        emergency_pct = cand.emergency_sl_pct / 100.0
        if pos.side == OrderSide.BUY_LONG:
            emergency_price = round(entry_price * (1 - emergency_pct), pp)
        else:
            emergency_price = round(entry_price * (1 + emergency_pct), pp)

        emergency_placed = False
        for attempt in range(1, max_retries + 1):
            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="STOP_MARKET",
                    quantity=pos.size,
                    stop_price=emergency_price,
                    reduce_only=True,
                )
                emergency_placed = True
                logger.info(f"[SysE EMRG] {symbol}: STOP_MARKET @ {emergency_price} "
                            f"(emergency={cand.emergency_sl_pct:.2f}%) [attempt {attempt}]")
                break
            except Exception as e:
                logger.error(f"[SysE EMRG] {symbol}: attempt {attempt}/{max_retries} FAILED: {e}")
                if attempt < max_retries:
                    time.sleep(1)  # kısa bekleme, sonra tekrar dene

        if not emergency_placed:
            # Emergency SL gönderilemedi → pozisyonu kapat (korunmasız kalınamaz)
            logger.error(f"[SysE EMRG] {symbol}: {max_retries} deneme başarısız! "
                         f"Pozisyon korunmasız → KAPATILIYOR")
            self._emergency_close_system_e(symbol, pos, "emergency_sl_failed")
            return

        # ═══ 2. TRAILING_STOP_MARKET (ROI→fiyat dönüşümü scanner'da yapıldı) ═══
        # cand.trailing_trigger_pct = ROI%/leverage (fiyat yüzdesi)
        # cand.trailing_callback_pct = ROI%/leverage (fiyat yüzdesi, 0.1-5.0 arası)
        trailing_trigger = cand.trailing_trigger_pct   # zaten fiyat %'si
        trailing_callback = cand.trailing_callback_pct  # zaten fiyat %'si

        # Binance callback sınırı: 0.1% - 5.0% (scanner'da zaten clamp edildi)
        callback_pct = round(max(0.1, min(trailing_callback, 5.0)), 2)

        se_cfg = self._config.get("system_e", {})
        roi_trigger = se_cfg.get("trailing_tetik_pct", 50.0)
        roi_callback = se_cfg.get("trailing_callback_pct", 10.0)

        if pos.side == OrderSide.BUY_LONG:
            activation_price = round(entry_price * (1 + trailing_trigger / 100), pp)
        else:
            activation_price = round(entry_price * (1 - trailing_trigger / 100), pp)

        trailing_placed = False
        for attempt in range(1, max_retries + 1):
            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="TRAILING_STOP_MARKET",
                    quantity=pos.size,
                    callback_rate=callback_pct,
                    stop_price=activation_price,
                    reduce_only=True,
                )
                trailing_placed = True
                logger.info(f"[SysE Trail] {symbol}: ROI trigger={roi_trigger}% "
                            f"→ fiyat={trailing_trigger:.4f}% "
                            f"ROI callback={roi_callback}% → fiyat={callback_pct}% "
                            f"activation={activation_price} [attempt {attempt}]")
                break
            except Exception as e:
                logger.error(f"[SysE Trail] {symbol}: attempt {attempt}/{max_retries} FAILED: {e}")
                if attempt < max_retries:
                    time.sleep(1)

        if not trailing_placed:
            logger.error(f"[SysE Trail] {symbol}: {max_retries} deneme başarısız! "
                         f"Emergency SL aktif ama trailing yok — pozisyon kapatılıyor")
            self._emergency_close_system_e(symbol, pos, "trailing_failed")
            return

        # ═══ 3. Doğrulama: Binance'de emirlerin varlığını kontrol et ═══
        try:
            time.sleep(0.5)  # API'nin emirleri işlemesi için kısa bekleme
            open_orders = rest.get_open_orders(symbol=symbol) or []

            has_stop = any(
                o.get("type") == "STOP_MARKET" and o.get("status") in ("NEW",)
                for o in open_orders
            )
            has_trailing = any(
                o.get("type") == "TRAILING_STOP_MARKET" and o.get("status") in ("NEW",)
                for o in open_orders
            )

            if not has_stop:
                logger.error(f"[SysE VERIFY] {symbol}: STOP_MARKET doğrulanamadı! "
                             f"Tekrar gönderiliyor...")
                try:
                    rest.place_order(
                        symbol=symbol, side=close_side,
                        order_type="STOP_MARKET", quantity=pos.size,
                        stop_price=emergency_price, reduce_only=True,
                    )
                    logger.info(f"[SysE VERIFY] {symbol}: STOP_MARKET tekrar gönderildi")
                except Exception as e:
                    logger.error(f"[SysE VERIFY] {symbol}: STOP_MARKET tekrar BAŞARISIZ: {e}")
                    self._emergency_close_system_e(symbol, pos, "verify_emergency_failed")
                    return

            if not has_trailing:
                logger.error(f"[SysE VERIFY] {symbol}: TRAILING_STOP_MARKET doğrulanamadı! "
                             f"Tekrar gönderiliyor...")
                try:
                    rest.place_order(
                        symbol=symbol, side=close_side,
                        order_type="TRAILING_STOP_MARKET", quantity=pos.size,
                        callback_rate=callback_pct, stop_price=activation_price,
                        reduce_only=True,
                    )
                    logger.info(f"[SysE VERIFY] {symbol}: TRAILING tekrar gönderildi")
                except Exception as e:
                    logger.error(f"[SysE VERIFY] {symbol}: TRAILING tekrar BAŞARISIZ: {e}")
                    self._emergency_close_system_e(symbol, pos, "verify_trailing_failed")
                    return

            logger.info(f"[SysE OK] {symbol}: Emergency SL + Trailing Stop DOĞRULANDI "
                        f"(STOP={has_stop}, TRAIL={has_trailing})")

        except Exception as e:
            logger.warning(f"[SysE VERIFY] {symbol}: Doğrulama hatası (emirler "
                           f"muhtemelen yerinde): {e}")

    def _emergency_close_system_e(self, symbol: str, pos, reason: str) -> None:
        """System E: server emirleri gönderilemediğinde pozisyonu hemen kapat."""
        logger.error(f"[SysE CLOSE] {symbol}: Korunmasız pozisyon kapatılıyor "
                     f"(sebep: {reason})")
        try:
            self._rest.cancel_all_orders(symbol)
        except Exception:
            pass

        # Güncel fiyatı al
        close_price = pos.entry_price
        try:
            ticker = self._rest.get_ticker_price(symbol)
            close_price = float(ticker.get("price", close_price))
        except Exception:
            pass

        try:
            close_side = OrderSide.SELL_SHORT if pos.side == OrderSide.BUY_LONG else OrderSide.BUY_LONG
            if self._order_executor:
                self._order_executor.execute_order(
                    symbol=symbol,
                    side=close_side,
                    order_type=OrderType.MARKET,
                    size=pos.size,
                    price=close_price,
                    leverage=pos.leverage,
                    sl_percent=0,
                    tp_percent=0,
                )
            self._position_mgr.close_position(
                symbol, exit_price=close_price,
                reason=f"system_e_{reason}")
            logger.info(f"[SysE CLOSE] {symbol}: Pozisyon kapatıldı @ {close_price}")
        except Exception as e:
            logger.error(f"[SysE CLOSE] {symbol}: Kapatma BAŞARISIZ: {e}")

    def _on_limit_filled_system_e(self, symbol: str, info: dict,
                                   fill_price: float) -> None:
        """Handle System E limit order fill: open position + server orders."""
        cand = info["candidate"]

        self._open_position_system_e(
            symbol, cand, info["side"], fill_price,
            info["size"], info["leverage"], info["margin_usdt"])

        if self._risk_manager:
            self._risk_manager.record_order(
                info["size"], fill_price, margin_usdt=info["margin_usdt"])

        if self._order_logger:
            self._order_logger.log_order(
                symbol=symbol, side=info["side"].value, order_type="Limit",
                price=fill_price, size=info["size"],
                notional_usdt=info["size"] * fill_price,
                status="filled",
                trigger_source=f"system_e:{cand.score:+.0f}",
            )

    # ══════════════════════════════════════════════════════════════════════
    # ═══ SYSTEM F — SON KURSUN (Last Bullet)                          ═══
    # ══════════════════════════════════════════════════════════════════════

    def _do_scanning_system_f(self) -> None:
        """System F iki katmanli tarama: TAM TARAMA (periyodik) + HIZLI TARAMA (surekli)."""

        # Step 0: Check pending limit orders
        if self._pending_limits:
            self._check_pending_limits()

        # Step A: Check held positions
        if self._position_mgr.has_position:
            self._check_held_positions()

        self._scan_count += 1
        sf = self._config.get("system_f", {})
        max_pos = sf.get("max_pozisyon", 1)
        self._position_mgr._max_positions = max_pos

        # Tam tarama mi, hizli tarama mi?
        now = time.time()
        full_scan_interval = sf.get("full_scan_interval_seconds", 300)
        needs_full_scan = (
            now - self._sf_last_full_scan_time >= full_scan_interval
            or not self._sf_shortlist
        )

        if needs_full_scan:
            logger.info(f"[SysF] TAM TARAMA #{self._scan_count} starting... "
                        f"[positions: {self._position_mgr.position_count}/{max_pos}]")
            results, eligible = self._do_full_scan_system_f(sf)
            self._sf_last_full_scan_time = now
            scan_type = "FULL"
        else:
            logger.info(f"[SysF] HIZLI TARAMA #{self._scan_count} "
                        f"[shortlist: {len(self._sf_shortlist)} coin] "
                        f"[positions: {self._position_mgr.position_count}/{max_pos}]")
            results, eligible = self._do_fast_scan_system_f(sf)
            scan_type = "FAST"

        # Log top 5
        for i, r in enumerate(results[:5]):
            logger.info(f"  [SysF] #{r.rank} {r.symbol}: skor={r.composite_score:.0f} "
                        f"dir={r.direction} uyum={r.aligned_count}/{r.total_tfs} "
                        f"lev={r.smart_leverage}x EV={r.ev_pct:+.1f}% "
                        f"P(w)={r.p_win:.0f}% av={r.av_sinifi or '-'} "
                        f"eligible={r.eligible} reject={r.reject_reason or '-'}")

        logger.info(f"[SysF] {scan_type}: {len(results)} scored, {len(eligible)} eligible, "
                    f"shortlist={len(self._sf_shortlist)}")

        # Publish for GUI
        self._event_bus.publish(EventType.SCANNER_UPDATE, {
            "scan_count": self._scan_count,
            "total_symbols": len(self._sf_cached_symbols) if self._sf_cached_symbols else len(results),
            "scored": len(results),
            "eligible": len(eligible),
            "mr_scored": 0,
            "mr_eligible": 0,
            "positions": self._position_mgr.position_count,
            "max_positions": max_pos,
            "system_f": True,
            "candidate": eligible[0].symbol if eligible else None,
        })

        # Close-only mode
        if self._config.get("strategy.close_only", False):
            if self._position_mgr.has_position:
                logger.info(f"[SysF] Close-only: monitoring {self._position_mgr.position_count} pos")
            self._wait(sf.get("scan_interval_seconds", 10))
            return

        # Filter candidates and buy
        self._sf_try_buy(eligible, sf, max_pos)

        if scan_type == "FULL":
            self._wait(5)
        else:
            self._wait(sf.get("scan_interval_seconds", 10))

    def _do_full_scan_system_f(self, sf: dict) -> tuple[list, list]:
        """TAM TARAMA: 50 coin × 6 TF + OB + BTC beta → shortlist olustur."""
        # 1. Symbol universe
        coin_sayisi = sf.get("coin_sayisi", 50)
        self._universe._top_n = coin_sayisi
        symbols = self._universe.refresh()
        if not symbols:
            logger.warning("[SysF] No symbols to scan")
            return [], []
        self._sf_cached_symbols = symbols

        # 2. TF kline cek (config'den direction_tfs + 1m tetik)
        mum_sayisi = sf.get("mum_sayisi", 200)
        _tf_min_map = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
                       "1h": 60, "2h": 120, "4h": 240, "8h": 480, "12h": 720, "1d": 1440}
        cfg_dir_tfs = sf.get("direction_tfs", ["5m", "1h", "4h"])
        fetch_tf_names = ["1m"] + [tf for tf in cfg_dir_tfs if tf != "1m"]
        fetch_tfs = [(tf, _tf_min_map.get(tf, 5)) for tf in fetch_tf_names]
        klines_all_tf: dict[str, dict[str, list]] = {sym: {} for sym in symbols}
        for tf_name, tf_min in fetch_tfs:
            tf_map = self._fetcher.fetch_batch(symbols, tf_name, mum_sayisi)
            for sym, klines in tf_map.items():
                if hasattr(klines, 'values'):
                    klines_all_tf[sym][tf_name] = klines.values.tolist()
                else:
                    klines_all_tf[sym][tf_name] = klines

        # 3. Funding rates
        market_ctx = self._fetch_funding_rates(symbols)

        # 4. Volume map
        volume_map = {}
        try:
            tickers = self._rest.get_24h_ticker()
            for t in tickers:
                s = t.get("symbol", "")
                if s in klines_all_tf:
                    volume_map[s] = float(t.get("quoteVolume", 0))
        except Exception:
            pass

        # 5. Preliminary scoring → top 15 icin OB cek
        ob_map = {}
        preliminary = self._system_f_scanner.score_batch(
            symbols, klines_all_tf, market_ctx, volume_map,
            btc_direction="FLAT")
        top_15 = [r.symbol for r in preliminary[:15]]
        for sym in top_15:
            try:
                depth = self._rest.get_depth(sym, limit=50)
                if depth:
                    vol = volume_map.get(sym, 0)
                    ob_result = self._ob_analyzer.analyze(depth, volume_24h=vol)
                    ob_map[sym] = ob_result
            except Exception:
                pass

        # 6. BTC beta
        beta_map = {}
        try:
            self._btc_corr.refresh()
            for sym in top_15:
                beta_map[sym] = self._btc_corr.get_beta(sym)
        except Exception:
            pass

        # 6b. BTC direction
        btc_direction = "FLAT"
        try:
            btc_klines = self._fetcher.fetch_single("BTCUSDT", "1h", 30)
            if btc_klines is not None:
                if hasattr(btc_klines, 'values'):
                    btc_klines = btc_klines.values.tolist()
                if len(btc_klines) >= 25:
                    import numpy as _np
                    btc_closes = _np.array([float(k[4]) for k in btc_klines])
                    k9 = 2.0 / 10
                    k21 = 2.0 / 22
                    ema9 = float(btc_closes[0])
                    ema21 = float(btc_closes[0])
                    for v in btc_closes[1:]:
                        ema9 = v * k9 + ema9 * (1 - k9)
                        ema21 = v * k21 + ema21 * (1 - k21)
                    gap_pct = (ema9 - ema21) / float(btc_closes[-1]) * 100
                    if gap_pct > 0.05:
                        btc_direction = "LONG"
                    elif gap_pct < -0.05:
                        btc_direction = "SHORT"
                    logger.info(f"[SysF] BTC direction: {btc_direction} (gap={gap_pct:.3f}%)")
        except Exception as e:
            logger.warning(f"[SysF] BTC direction check failed: {e}")

        # 7. Final scoring
        results = self._system_f_scanner.score_batch(
            symbols, klines_all_tf, market_ctx, volume_map, ob_map, beta_map,
            btc_direction=btc_direction)

        # 8. Cache verilerini sakla (15m haric — hizli taramada taze cekilir)
        self._sf_cached_klines = {}
        for sym in symbols:
            cached_tf = {}
            sym_data = klines_all_tf.get(sym, {})
            for tf in ("1h", "4h", "1d"):
                if tf in sym_data:
                    cached_tf[tf] = sym_data[tf]
            if cached_tf:
                self._sf_cached_klines[sym] = cached_tf
        self._sf_cached_market_ctx = market_ctx
        self._sf_cached_ob_map = ob_map
        self._sf_cached_beta_map = beta_map
        self._sf_cached_btc_direction = btc_direction
        self._sf_cached_volume_map = volume_map

        # 9. Shortlist olustur
        self._sf_shortlist = self._build_sf_shortlist(results, sf)
        self._sf_last_full_results = list(results)
        self._last_system_f_results = results

        eligible = [r for r in results if r.eligible]
        return results, eligible

    def _do_fast_scan_system_f(self, sf: dict) -> tuple[list, list]:
        """HIZLI TARAMA: sadece shortlist coinler × 1m + 5m, cache'li ust TF verileriyle."""
        shortlist = self._sf_shortlist
        if not shortlist:
            return self._sf_last_full_results, []

        mum_sayisi = sf.get("mum_sayisi", 200)

        # Taze cek: 1m (tetik) + direction TF'lerden kisa olanlar
        cfg_dir_tfs = sf.get("direction_tfs", ["5m", "1h", "4h"])
        _tf_min_map = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
                       "1h": 60, "2h": 120, "4h": 240, "8h": 480, "12h": 720, "1d": 1440}
        # 30dk ve altindaki TF'ler taze cekilir, ustundekiler cache'den gelir
        fresh_tfs = ["1m"] + [tf for tf in cfg_dir_tfs
                              if _tf_min_map.get(tf, 5) <= 30 and tf != "1m"]
        cached_tfs = [tf for tf in cfg_dir_tfs if _tf_min_map.get(tf, 5) > 30]

        klines_all_tf: dict[str, dict[str, list]] = {}
        for tf_name in fresh_tfs:
            tf_map = self._fetcher.fetch_batch(shortlist, tf_name, mum_sayisi)
            for sym, klines in tf_map.items():
                if sym not in klines_all_tf:
                    klines_all_tf[sym] = {}
                if hasattr(klines, 'values'):
                    klines_all_tf[sym][tf_name] = klines.values.tolist()
                else:
                    klines_all_tf[sym][tf_name] = klines

        # Cache'li ust TF verilerini birlestir (yavas degisir)
        for sym in shortlist:
            if sym not in klines_all_tf:
                klines_all_tf[sym] = {}
            cached = self._sf_cached_klines.get(sym, {})
            for tf in cached_tfs:
                if tf in cached:
                    klines_all_tf[sym][tf] = cached[tf]

        # Shortlist icin OB guncelle (hizli — az coin)
        ob_map = dict(self._sf_cached_ob_map)
        if sf.get("fast_scan_ob_refresh", True):
            for sym in shortlist:
                try:
                    depth = self._rest.get_depth(sym, limit=50)
                    if depth:
                        vol = self._sf_cached_volume_map.get(sym, 0)
                        ob_result = self._ob_analyzer.analyze(depth, volume_24h=vol)
                        ob_map[sym] = ob_result
                except Exception:
                    pass

        # BTC yonu taze cek (tek API call — btc_ reject'li coinler icin kritik)
        btc_direction = self._sf_cached_btc_direction
        try:
            btc_klines = self._fetcher.fetch_single("BTCUSDT", "1h", 30)
            if btc_klines is not None:
                if hasattr(btc_klines, 'values'):
                    btc_klines = btc_klines.values.tolist()
                if len(btc_klines) >= 25:
                    import numpy as _np
                    btc_closes = _np.array([float(k[4]) for k in btc_klines])
                    k9 = 2.0 / 10
                    k21 = 2.0 / 22
                    ema9 = float(btc_closes[0])
                    ema21 = float(btc_closes[0])
                    for v in btc_closes[1:]:
                        ema9 = v * k9 + ema9 * (1 - k9)
                        ema21 = v * k21 + ema21 * (1 - k21)
                    gap_pct = (ema9 - ema21) / float(btc_closes[-1]) * 100
                    if gap_pct > 0.05:
                        btc_direction = "LONG"
                    elif gap_pct < -0.05:
                        btc_direction = "SHORT"
                    else:
                        btc_direction = "FLAT"
                    self._sf_cached_btc_direction = btc_direction
        except Exception:
            pass

        # Skorla
        fast_results = self._system_f_scanner.score_batch(
            shortlist, klines_all_tf,
            self._sf_cached_market_ctx,
            self._sf_cached_volume_map,
            ob_map,
            self._sf_cached_beta_map,
            btc_direction=btc_direction)

        # GUI icin: tam sonuclarin uzerine hizli sonuclari yaz
        result_map = {r.symbol: r for r in self._sf_last_full_results}
        for r in fast_results:
            result_map[r.symbol] = r
        merged = sorted(result_map.values(),
                        key=lambda r: (not r.eligible, -r.composite_score))

        self._last_system_f_results = merged
        eligible = [r for r in merged if r.eligible]
        return merged, eligible

    def _build_sf_shortlist(self, results: list, sf: dict) -> list[str]:
        """Tam tarama sonuclarindan hizli tarama icin aday listesi olustur.

        Kriter: eligible + 4+/5 uyum + hizli degisen sebeple reddedilen + yuksek skor.
        """
        max_shortlist = sf.get("shortlist_size", 10)
        fast_prefixes = (
            "no_spike", "score_", "ev_", "p_sl_",
            "weak_", "vol_filter_", "thin_book", "spread_",
            "wall_", "ob_against_", "btc_", "mixed_", "beta_",
        )
        shortlist = []

        for r in results:
            if r.eligible:
                shortlist.append(r.symbol)
            elif r.aligned_count >= 4 and r.direction != "SKIP":
                shortlist.append(r.symbol)
            elif any(r.reject_reason.startswith(p) for p in fast_prefixes):
                shortlist.append(r.symbol)
            elif r.composite_score >= 70:
                shortlist.append(r.symbol)

            if len(shortlist) >= max_shortlist:
                break

        logger.info(f"[SysF] Shortlist: {len(shortlist)} coin — {shortlist}")
        return shortlist

    def _sf_try_buy(self, eligible: list, sf: dict, max_pos: int) -> None:
        """Eligible adaylardan en iyisini sec ve al."""
        now = time.time()
        self._failed_symbols = {
            s: t for s, t in self._failed_symbols.items()
            if now - t < self._failed_cooldown
        }

        candidates = []
        for r in eligible:
            if r.symbol in self._failed_symbols:
                continue
            if self._position_mgr.is_holding(r.symbol):
                continue
            if r.symbol in self._pending_limits:
                continue
            coin_ok, coin_reason = self._check_coin_daily_ban(r.symbol)
            if not coin_ok:
                continue
            candidates.append(r)

        if not candidates or not self._position_mgr.has_capacity:
            return

        real_balance = 0.0
        if self._order_executor and hasattr(self._order_executor, "get_balance"):
            try:
                real_balance = self._order_executor.get_balance()
            except Exception:
                pass
        if real_balance > 0 and real_balance < 0.10:
            logger.info(f"[SysF] Balance too low ({real_balance:.4f}$)")
            return

        cand = candidates[0]
        total_occupied = self._position_mgr.position_count + len(self._pending_limits)
        if total_occupied < max_pos:
            if self._check_trade_frequency():
                logger.info(f"[SysF] SON KURSUN: {cand.symbol} skor={cand.composite_score:.0f} "
                            f"dir={cand.direction} lev={cand.smart_leverage}x "
                            f"EV={cand.ev_pct:+.1f}% P(w)={cand.p_win:.0f}% "
                            f"TP={cand.dynamic_tp_roi:.1f}% av={cand.av_sinifi}")
                self._do_buying_system_f(cand)

    def _do_buying_system_f(self, cand: 'SystemFScanResult') -> bool:
        """System F pozisyon acma: akilli kaldirac, market giris, SL + trailing."""
        symbol = cand.symbol
        direction = cand.direction
        side = OrderSide.BUY_LONG if direction == "LONG" else OrderSide.SELL_SHORT
        sf = self._config.get("system_f", {})

        # 1. Fresh price
        price = cand.price
        try:
            ticker = self._rest.get_ticker_price(symbol)
            price = float(ticker.get("price", price))
        except Exception:
            pass

        # 2. Smart leverage
        leverage = cand.smart_leverage
        lev_enabled = self._config.get("leverage.enabled", False)
        if not lev_enabled:
            leverage = 1

        if leverage > 1:
            try:
                margin_est = 5.0
                available_max = self._rest.get_max_leverage(symbol, margin_est * leverage)
                if available_max < leverage:
                    leverage = available_max
                if leverage < 2:
                    logger.info(f"[SysF] {symbol} max lev {leverage}x too low, skipping")
                    self._failed_symbols[symbol] = time.time()
                    return False
            except Exception as e:
                logger.warning(f"[SysF] {symbol} max leverage check failed: {e}")

        # 3. Position sizing: ALL-IN (tek pozisyon — bakiyenin %95'i)
        real_balance = 0.0
        if self._order_executor and hasattr(self._order_executor, "get_balance"):
            try:
                real_balance = self._order_executor.get_balance()
            except Exception:
                pass

        locked_margin = self._position_mgr.get_total_margin()
        wallet = real_balance + locked_margin
        if wallet <= 0:
            wallet = self._config.get("risk.initial_balance", 0.49)

        # All-in: bakiyenin %95'i (kucuk tampon birak)
        margin_usdt = round(real_balance * 0.95, 4) if real_balance > 0 else round(wallet * 0.95, 4)

        if margin_usdt < 0.10:
            logger.warning(f"[SysF] Margin too low: {margin_usdt}$")
            return False

        logger.info(f"[SysF] {symbol} sizing: wallet={wallet:.4f}$ → "
                    f"margin={margin_usdt:.4f}$ x {leverage}x")

        # 4. Set leverage + margin type
        if leverage > 1:
            try:
                self._rest.set_margin_type(symbol, "ISOLATED")
            except Exception:
                pass
            try:
                self._rest.set_leverage(symbol, leverage)
            except Exception as e:
                logger.warning(f"[SysF] set_leverage failed: {e}")

        # 5. Qty precision
        qty_precision = 3
        min_notional = 5.0
        si = None
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    qty_precision = si.quantity_precision
                    min_notional = si.min_notional
            except Exception:
                pass

        notional_usdt = margin_usdt * leverage
        if notional_usdt < min_notional:
            needed = min_notional / leverage * 1.05
            if needed > margin_usdt * 2.0:
                logger.info(f"[SysF] {symbol} min notional needs {needed:.4f}$, skip")
                self._failed_symbols[symbol] = time.time()
                return False
            margin_usdt = round(needed, 4)
            notional_usdt = margin_usdt * leverage

        size_qty = round(notional_usdt / price, qty_precision) if price > 0 else 0

        # 6. MARKET giris (hiz kritik — limit bekleme yok)
        try:
            if not self._order_executor:
                logger.error("[SysF] No order executor")
                return False

            self._order_executor.execute_order(
                symbol=symbol,
                side=side,
                order_type=OrderType.MARKET,
                size=size_qty,
                price=price,
                leverage=leverage,
                sl_percent=0,  # SL server-side ayri gonderilecek
                tp_percent=0,
            )
        except Exception as e:
            logger.error(f"[SysF] Order failed for {symbol}: {e}")
            self._failed_symbols[symbol] = time.time()
            return False

        # 7. Pozisyonu ac ve server emirlerini yerlestir
        self._open_position_system_f(
            symbol, cand, side, price, size_qty, leverage, margin_usdt)

        if self._risk_manager:
            self._risk_manager.record_order(
                size_qty, price, margin_usdt=margin_usdt)

        if self._order_logger:
            self._order_logger.log_order(
                symbol=symbol, side=side.value, order_type="Market",
                price=price, size=size_qty,
                notional_usdt=size_qty * price,
                status="filled",
                trigger_source=f"system_f:{cand.composite_score:.0f}",
            )

        self._trade_timestamps.append(time.time())
        return True

    def _open_position_system_f(self, symbol: str, cand: 'SystemFScanResult',
                                 side: OrderSide, price: float,
                                 size_qty: float, leverage: int,
                                 margin_usdt: float) -> None:
        """System F pozisyonu ac ve server-side emirleri yerlestir."""
        sf = self._config.get("system_f", {})

        # RSI from 5m TF
        rsi_val = 50.0
        for sig in cand.tf_signals:
            if sig.timeframe == "5m":
                rsi_val = sig.rsi_value
                break

        # ADX from 1h TF
        adx_val = 0.0
        for sig in cand.tf_signals:
            if sig.timeframe == "1h":
                adx_val = sig.adx_value
                break

        # Dynamic TP price hesapla (software yedek: avg_fwd × mult)
        software_tp_mult = sf.get("software_tp_mult", 2.0)
        software_tp_pct = cand.dynamic_tp_pct * software_tp_mult
        if cand.direction == "LONG":
            dynamic_tp_price = price * (1 + software_tp_pct / 100)
        else:
            dynamic_tp_price = price * (1 - software_tp_pct / 100)

        pos = self._position_mgr.open_position(
            symbol=symbol,
            side=side,
            price=price,
            size=size_qty,
            atr=cand.atr,
            leverage=leverage,
            margin_usdt=margin_usdt,
            timeframe="5m",
            entry_score=cand.composite_score,
            entry_confluence=0.0,
            entry_adx=adx_val,
            entry_rsi=rsi_val,
            entry_regime=f"SYS_F_{cand.av_sinifi}",
            entry_regime_confidence=cand.direction_strength,
            entry_mode="SYSTEM_F",
            entry_bb_width=cand.sl_pct,
            mr_tp_price=dynamic_tp_price,
        )

        if not pos:
            logger.error(f"[SysF] Failed to open position for {symbol}")
            return

        # Server-side emirleri yerlestir
        self._place_initial_orders_system_f(symbol, pos, cand, sf)

    def _place_initial_orders_system_f(self, symbol: str, pos,
                                         cand: 'SystemFScanResult',
                                         sf: dict) -> None:
        """Server-side SL (1.5xATR) + Emergency + Trailing emirleri.

        GARANTILI: SL ve emergency basarili olmali.
        - SL basarisiz → pozisyon kapatilir
        - Emergency basarisiz → uyari (SL korur)
        - Trailing basarisiz → uyari (SL + emergency korur)
        """
        rest = self._rest
        if not rest:
            logger.error(f"[SysF] {symbol}: REST client yok, pozisyon kapatiliyor!")
            self._emergency_close_system_f(symbol, pos, "no_rest_client")
            return

        pp = 2
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    pp = si.price_precision
            except Exception:
                pass

        close_side = "SELL" if pos.side == OrderSide.BUY_LONG else "BUY"
        entry_price = pos.entry_price
        max_retries = 3

        # ═══ 1. STOP_MARKET (SL: 1.5xATR + fee) — ZORUNLU ═══
        sl_pct = cand.sl_pct / 100.0
        if pos.side == OrderSide.BUY_LONG:
            sl_price = round(entry_price * (1 - sl_pct), pp)
        else:
            sl_price = round(entry_price * (1 + sl_pct), pp)

        sl_placed = False
        for attempt in range(1, max_retries + 1):
            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="STOP_MARKET",
                    quantity=pos.size,
                    stop_price=sl_price,
                    reduce_only=True,
                )
                sl_placed = True
                logger.info(f"[SysF SL] {symbol}: STOP_MARKET @ {sl_price} "
                            f"(SL={cand.sl_pct:.2f}%) [attempt {attempt}]")
                break
            except Exception as e:
                logger.error(f"[SysF SL] {symbol}: attempt {attempt}/{max_retries} FAILED: {e}")
                if attempt < max_retries:
                    time.sleep(1)

        if not sl_placed:
            logger.error(f"[SysF SL] {symbol}: SL gonderilemedi! Pozisyon KAPATILIYOR")
            self._emergency_close_system_f(symbol, pos, "sl_failed")
            return

        # ═══ 2. STOP_MARKET (Emergency: likidasyon %80) — YEDEK ═══
        emergency_pct = cand.emergency_sl_pct / 100.0
        if pos.side == OrderSide.BUY_LONG:
            emergency_price = round(entry_price * (1 - emergency_pct), pp)
        else:
            emergency_price = round(entry_price * (1 + emergency_pct), pp)

        for attempt in range(1, max_retries + 1):
            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="STOP_MARKET",
                    quantity=pos.size,
                    stop_price=emergency_price,
                    reduce_only=True,
                )
                logger.info(f"[SysF EMRG] {symbol}: STOP_MARKET @ {emergency_price} "
                            f"(emergency={cand.emergency_sl_pct:.2f}%) [attempt {attempt}]")
                break
            except Exception as e:
                logger.error(f"[SysF EMRG] {symbol}: attempt {attempt}/{max_retries} FAILED: {e}")
                if attempt < max_retries:
                    time.sleep(1)

        # ═══ 3. TRAILING_STOP_MARKET (dinamik TP + swing trailing) ═══
        trailing_trigger = cand.trailing_trigger_pct   # swing forward avg (fiyat %)
        trailing_callback = cand.trailing_callback_pct  # swing retrace × 0.8 (fiyat %)
        callback_pct = round(max(0.1, min(trailing_callback, 5.0)), 2)

        if pos.side == OrderSide.BUY_LONG:
            activation_price = round(entry_price * (1 + trailing_trigger / 100), pp)
        else:
            activation_price = round(entry_price * (1 - trailing_trigger / 100), pp)

        for attempt in range(1, max_retries + 1):
            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="TRAILING_STOP_MARKET",
                    quantity=pos.size,
                    callback_rate=callback_pct,
                    stop_price=activation_price,
                    reduce_only=True,
                )
                logger.info(f"[SysF Trail] {symbol}: swing-TP fiyat={trailing_trigger:.3f}% "
                            f"(ROI={cand.dynamic_tp_roi:.1f}%) callback={callback_pct}% "
                            f"activation={activation_price} av={cand.av_sinifi} "
                            f"[attempt {attempt}]")
                break
            except Exception as e:
                logger.error(f"[SysF Trail] {symbol}: attempt {attempt}/{max_retries} FAILED: {e}")
                if attempt < max_retries:
                    time.sleep(1)

        # ═══ 4. Dogrulama ═══
        try:
            time.sleep(0.5)
            open_orders = rest.get_open_orders(symbol=symbol) or []

            has_stop = any(
                o.get("type") == "STOP_MARKET" and o.get("status") in ("NEW",)
                for o in open_orders
            )
            if not has_stop:
                logger.error(f"[SysF VERIFY] {symbol}: STOP_MARKET dogrulanamadi!")
                try:
                    rest.place_order(
                        symbol=symbol, side=close_side,
                        order_type="STOP_MARKET", quantity=pos.size,
                        stop_price=sl_price, reduce_only=True,
                    )
                except Exception as e:
                    logger.error(f"[SysF VERIFY] {symbol}: SL tekrar BASARISIZ: {e}")
                    self._emergency_close_system_f(symbol, pos, "verify_sl_failed")
                    return

            logger.info(f"[SysF OK] {symbol}: SL + Emergency + Trailing DOGRULANDI")

        except Exception as e:
            logger.warning(f"[SysF VERIFY] {symbol}: Dogrulama hatasi: {e}")

    def _execute_partial_tp_system_f(self, symbol: str, current_price: float) -> None:
        """System F partial TP: %50 kapat + server emirlerini guncelle.

        Generic _execute_partial_tp'den fark:
        1. close_pct'yi system_f config'inden okur (strategy'den degil)
        2. Partial close sonrasi server emirlerini (SL + emergency + trailing)
           iptal edip kalan miktar icin yeniden gonderir
        """
        pos = self._position_mgr.get_position(symbol)
        if not pos:
            return

        sf = self._config.get("system_f", {})
        close_pct = sf.get("stage1_close_pct", 0.5)

        # Qty precision
        qty_precision = 3
        if self._symbol_info_cache:
            try:
                sym_info = self._symbol_info_cache.get(symbol)
                if sym_info:
                    qty_precision = sym_info.quantity_precision
            except Exception:
                pass

        close_size = round(pos.size * close_pct, qty_precision)
        if close_size <= 0:
            logger.warning(f"[SysF PARTIAL] {symbol}: close_size=0, skipping")
            pos.partial_tp_taken = False
            return

        remaining = round(pos.size - close_size, qty_precision)
        if remaining <= 0:
            # Kalan yok, tamamen kapat
            logger.info(f"[SysF PARTIAL] {symbol}: remaining=0, full close")
            self._sell_position(symbol, current_price, "system_f_full_tp")
            return

        # 1. Pozisyonun yarisini market emirle kapat
        close_side = (OrderSide.SELL_SHORT if pos.side == OrderSide.BUY_LONG
                      else OrderSide.BUY_LONG)

        success = False
        if self._order_executor:
            try:
                if hasattr(self._order_executor, "close_position"):
                    success = self._order_executor.close_position(
                        symbol, pos.side, close_size,
                        limit_exit=False, limit_offset_pct=0.0)
                else:
                    success = self._order_executor.execute_order(
                        symbol=symbol, side=close_side,
                        order_type=OrderType.MARKET,
                        size=close_size, reduce_only=True,
                        qty_precision=qty_precision,
                    )
            except Exception as e:
                logger.error(f"[SysF PARTIAL] {symbol}: order failed: {e}")

        if not success:
            pos.partial_tp_taken = False
            logger.warning(f"[SysF PARTIAL] {symbol}: close failed, will retry")
            return

        # 2. Pozisyon boyutunu guncelle
        self._position_mgr.update_position_size(symbol, remaining)

        pnl_pct = self._position_mgr._get_pnl_pct(pos, current_price)
        logger.info(f"[SysF PARTIAL] {symbol}: Stage 1 kapatildi — "
                    f"{close_pct*100:.0f}% ({close_size} qty) @ ROI={pnl_pct:+.1f}%, "
                    f"kalan {remaining} qty")

        if self._order_logger:
            self._order_logger.log_order(
                symbol=symbol, side=close_side.value,
                order_type="Market",
                price=current_price, size=close_size,
                notional_usdt=close_size * current_price,
                status="filled",
                trigger_source="system_f_partial_tp",
            )

        # 3. Server emirlerini iptal et ve kalan miktar icin yeniden gonder
        rest = self._rest
        if not rest:
            logger.warning(f"[SysF PARTIAL] {symbol}: REST yok, server emirleri guncellenemedi")
            return

        try:
            rest.cancel_all_orders(symbol)
            logger.info(f"[SysF PARTIAL] {symbol}: eski server emirleri iptal edildi")
        except Exception as e:
            logger.error(f"[SysF PARTIAL] {symbol}: cancel_all failed: {e}")

        # 4. Kalan miktar icin yeni SL + trailing gonder
        pp = 2
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    pp = si.price_precision
            except Exception:
                pass

        close_side_str = "SELL" if pos.side == OrderSide.BUY_LONG else "BUY"
        entry_price = pos.entry_price

        # SL: orijinal SL fiyatini koru ama kalan miktar ile
        sl_pct_val = pos.entry_bb_width / 100.0  # entry_bb_width = sl_pct
        if sl_pct_val > 0:
            if pos.side == OrderSide.BUY_LONG:
                sl_price = round(entry_price * (1 - sl_pct_val), pp)
            else:
                sl_price = round(entry_price * (1 + sl_pct_val), pp)

            try:
                rest.place_order(
                    symbol=symbol, side=close_side_str,
                    order_type="STOP_MARKET", quantity=remaining,
                    stop_price=sl_price, reduce_only=True,
                )
                logger.info(f"[SysF PARTIAL] {symbol}: yeni SL @ {sl_price} "
                            f"(qty={remaining})")
            except Exception as e:
                logger.error(f"[SysF PARTIAL] {symbol}: yeni SL FAILED: {e}")

        # Trailing: mevcut kardan itibaren daha siki trailing
        trailing_callback_roi = sf.get("stage2_callback_roi_pct", 5.0)
        lev = pos.leverage if pos.leverage > 0 else 1
        callback_price_pct = trailing_callback_roi / lev
        callback_pct = round(max(0.1, min(callback_price_pct, 5.0)), 2)

        # Activation: simdi zaten karda, mevcut fiyattan itibaren aktif olsun
        try:
            rest.place_order(
                symbol=symbol, side=close_side_str,
                order_type="TRAILING_STOP_MARKET", quantity=remaining,
                callback_rate=callback_pct,
                stop_price=round(current_price, pp),
                reduce_only=True,
            )
            logger.info(f"[SysF PARTIAL] {symbol}: yeni trailing — "
                        f"callback={callback_pct}% (ROI {trailing_callback_roi}%) "
                        f"activation=current_price={current_price}")
        except Exception as e:
            logger.error(f"[SysF PARTIAL] {symbol}: yeni trailing FAILED: {e}")

    def _emergency_close_system_f(self, symbol: str, pos, reason: str) -> None:
        """System F: server emirleri gonderilemediginde pozisyonu hemen kapat."""
        logger.error(f"[SysF CLOSE] {symbol}: Korunmasiz pozisyon kapatiliyor "
                     f"(sebep: {reason})")
        try:
            self._rest.cancel_all_orders(symbol)
        except Exception:
            pass

        # Guncel fiyati al
        current_price = pos.entry_price
        try:
            ticker = self._rest.get_ticker_price(symbol)
            current_price = float(ticker.get("price", current_price))
        except Exception:
            pass

        # Market emirle kapat
        close_side = (OrderSide.SELL_SHORT if pos.side == OrderSide.BUY_LONG
                      else OrderSide.BUY_LONG)
        try:
            if self._order_executor:
                if hasattr(self._order_executor, "close_position"):
                    self._order_executor.close_position(
                        symbol, pos.side, pos.size,
                        limit_exit=False, limit_offset_pct=0.0)
                else:
                    self._order_executor.execute_order(
                        symbol=symbol, side=close_side,
                        order_type=OrderType.MARKET,
                        size=pos.size, price=current_price,
                        leverage=pos.leverage,
                        sl_percent=0, tp_percent=0,
                    )
        except Exception as e:
            logger.error(f"[SysF CLOSE] {symbol}: Kapatma BASARISIZ: {e}")

        # Remove position
        self._position_mgr.close_position(
            symbol, current_price,
            reason=f"system_f_{reason}")

    # ══════════════════════════════════════════════════════════════════════
    # ═══ SYSTEM G — PER-COIN OPTIMIZED TRADING                         ═══
    # ══════════════════════════════════════════════════════════════════════

    def _do_scanning_system_g(self) -> None:
        """System G: sinyal tara + eligible coinler icin async optimizasyon."""
        if self._pending_limits:
            self._check_pending_limits()
        if self._position_mgr.has_position:
            self._check_held_positions()

        self._scan_count += 1
        sg = self._config.get("system_g", {})
        pos_cfg = sg.get("position", {})
        max_pos = pos_cfg.get("max_pozisyon", 12)
        self._position_mgr._max_positions = max_pos

        # Full scan vs fast scan
        now = time.time()
        full_interval = sg.get("full_scan_interval_seconds", 300)
        needs_full = (now - self._sg_last_full_scan_time >= full_interval
                      or not self._sg_shortlist)

        if needs_full:
            logger.info(f"[SysG] TAM TARAMA #{self._scan_count} "
                        f"[positions: {self._position_mgr.position_count}/{max_pos}]")
            results, eligible = self._do_full_scan_system_g(sg)
            self._sg_last_full_scan_time = now
        else:
            logger.info(f"[SysG] HIZLI TARAMA #{self._scan_count} "
                        f"[shortlist: {len(self._sg_shortlist)}] "
                        f"[positions: {self._position_mgr.position_count}/{max_pos}]")
            results, eligible = self._do_fast_scan_system_g(sg)

        self._last_system_g_results = results

        # Log top 5
        for r in results[:5]:
            logger.info(f"  [SysG] #{r.rank} {r.symbol}: dir={r.direction} "
                        f"uyum={r.aligned_count}/{r.total_tfs} "
                        f"opt={r.opt_status} "
                        f"lev={r.smart_leverage}x "
                        f"bt_roi={r.backtest_roi:+.1f}% "
                        f"eligible={r.eligible} reject={r.reject_reason or '-'}")

        logger.info(f"[SysG] {len(results)} scored, {len(eligible)} eligible")

        # Publish for GUI
        self._event_bus.publish(EventType.SCANNER_UPDATE, {
            "scan_count": self._scan_count,
            "total_symbols": len(self._sg_cached_symbols) if self._sg_cached_symbols else len(results),
            "scored": len(results),
            "eligible": len(eligible),
            "mr_scored": 0, "mr_eligible": 0,
            "positions": self._position_mgr.position_count,
            "max_positions": max_pos,
            "system_g": True,
            "candidate": eligible[0].symbol if eligible else None,
        })

        if self._config.get("strategy.close_only", False):
            self._wait(sg.get("scan_interval_seconds", 10))
            return

        # Try to buy best candidate
        self._sg_try_buy(eligible, sg, max_pos)
        self._wait(sg.get("scan_interval_seconds", 10))

    def _do_full_scan_system_g(self, sg: dict) -> tuple[list, list]:
        """Full scan: fetch all TFs + submit optimizations for eligible coins."""
        coin_sayisi = sg.get("coin_sayisi", 50)
        self._universe._top_n = coin_sayisi
        symbols = self._universe.refresh()
        if not symbols:
            return [], []
        self._sg_cached_symbols = symbols

        # Fetch klines
        mum_sayisi = sg.get("mum_sayisi", 200)
        cfg_dir_tfs = sg.get("direction_tfs", ["5m", "1h", "4h"])
        _tf_min_map = {"1m": 1, "5m": 5, "15m": 15, "30m": 30,
                       "1h": 60, "2h": 120, "4h": 240, "1d": 1440}
        fetch_tfs = [(tf, _tf_min_map.get(tf, 5)) for tf in cfg_dir_tfs]

        klines_all_tf: dict[str, dict[str, list]] = {sym: {} for sym in symbols}
        for tf_name, tf_min in fetch_tfs:
            tf_map = self._fetcher.fetch_batch(symbols, tf_name, mum_sayisi)
            for sym, klines in tf_map.items():
                if hasattr(klines, 'values'):
                    klines_all_tf[sym][tf_name] = klines.values.tolist()
                else:
                    klines_all_tf[sym][tf_name] = klines

        # Cache klines for fast scan
        self._sg_cached_klines = klines_all_tf

        # Funding rates
        market_ctx = self._fetch_funding_rates(symbols)

        # Volume map
        volume_map = {}
        try:
            tickers = self._rest.get_24h_ticker()
            for t in tickers:
                s = t.get("symbol", "")
                if s in klines_all_tf:
                    volume_map[s] = float(t.get("quoteVolume", 0))
        except Exception:
            pass
        self._sg_cached_volume_map = volume_map

        # BTC direction
        btc_direction = "FLAT"
        try:
            btc_klines = self._fetcher.fetch_single("BTCUSDT", "1h", 30)
            if btc_klines is not None:
                if hasattr(btc_klines, 'values'):
                    btc_klines = btc_klines.values.tolist()
                if len(btc_klines) >= 25:
                    from scanner.system_g_scanner import SystemGScanner
                    btc_ind = self._system_g_scanner._analyze_tf(btc_klines, "1h", sg)
                    btc_direction = btc_ind.strict_direction
        except Exception:
            pass
        self._sg_cached_btc_direction = btc_direction

        # Score all
        results = self._system_g_scanner.score_batch(
            symbols, klines_all_tf, market_ctx, volume_map,
            {}, {}, btc_direction)

        # For eligible coins: check optimization cache or submit async
        eligible = []
        shortlist = []
        for r in results:
            if not r.eligible:
                continue

            shortlist.append(r.symbol)

            # Check optimization cache
            cache = self._system_g_scanner.get_cached(r.symbol)
            if cache and cache.direction == r.direction:
                r.opt_status = "CACHED"
                r.opt_result = cache.best
                r.smart_leverage = cache.best.combo.leverage
                r.tp_pct = cache.best.combo.tp_pct
                r.sl_pct = cache.best.combo.sl_pct
                r.sl_mode = cache.best.combo.sl_mode
                r.backtest_roi = cache.best.total_roi
                r.backtest_wr = cache.best.win_rate
                r.backtest_liq_rate = cache.best.liq_rate
                r.composite_score = cache.best.score
                if cache.best.total_roi > 0:
                    eligible.append(r)
                else:
                    r.reject_reason = f"bt_roi_neg_{cache.best.total_roi:.0f}"
            else:
                # Submit async optimization — need 30d of 5m data
                r.opt_status = "PENDING"
                opt_days = sg.get("optimization", {}).get("days_back", 30)
                try:
                    from backtest.data_fetcher import fetch_klines
                    from datetime import datetime, timedelta, timezone
                    now_dt = datetime.now(timezone.utc)
                    end_ms = int(now_dt.timestamp() * 1000)
                    start_ms = int((now_dt - timedelta(days=opt_days)).timestamp() * 1000)
                    warmup_ms = 200 * 5 * 60000
                    kl_5m_30d = fetch_klines(r.symbol, "5m", start_ms - warmup_ms, end_ms)
                    if kl_5m_30d and len(kl_5m_30d) >= 200:
                        self._system_g_scanner.submit_optimization(
                            r.symbol, r.direction, kl_5m_30d)
                except Exception as e:
                    logger.error(f"[SysG] Failed to fetch 5m data for {r.symbol}: {e}")
                    r.opt_status = "FAILED"

        self._sg_shortlist = shortlist[:10]

        eligible.sort(key=lambda r: -r.composite_score)
        return results, eligible

    def _do_fast_scan_system_g(self, sg: dict) -> tuple[list, list]:
        """Fast scan: check shortlist + check pending optimizations."""
        shortlist = self._sg_shortlist
        if not shortlist:
            return self._last_system_g_results, []

        mum_sayisi = sg.get("mum_sayisi", 200)
        cfg_dir_tfs = sg.get("direction_tfs", ["5m", "1h", "4h"])
        _tf_min_map = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
        fresh_tfs = [tf for tf in cfg_dir_tfs if _tf_min_map.get(tf, 5) <= 30]
        cached_tfs = [tf for tf in cfg_dir_tfs if _tf_min_map.get(tf, 5) > 30]

        klines_all_tf: dict[str, dict[str, list]] = {}
        for tf_name in fresh_tfs:
            tf_map = self._fetcher.fetch_batch(shortlist, tf_name, mum_sayisi)
            for sym, klines in tf_map.items():
                if sym not in klines_all_tf:
                    klines_all_tf[sym] = {}
                if hasattr(klines, 'values'):
                    klines_all_tf[sym][tf_name] = klines.values.tolist()
                else:
                    klines_all_tf[sym][tf_name] = klines

        for sym in shortlist:
            if sym not in klines_all_tf:
                klines_all_tf[sym] = {}
            cached = self._sg_cached_klines.get(sym, {})
            for tf in cached_tfs:
                if tf in cached:
                    klines_all_tf[sym][tf] = cached[tf]

        results = self._system_g_scanner.score_batch(
            shortlist, klines_all_tf, {}, self._sg_cached_volume_map,
            {}, {}, self._sg_cached_btc_direction)

        # Check pending optimizations
        eligible = []
        for r in results:
            if not r.eligible:
                continue

            # Check if optimization completed
            opt = self._system_g_scanner.check_optimization(r.symbol)
            if opt:
                r.opt_status = "FRESH"
                r.opt_result = opt
                r.smart_leverage = opt.combo.leverage
                r.tp_pct = opt.combo.tp_pct
                r.sl_pct = opt.combo.sl_pct
                r.sl_mode = opt.combo.sl_mode
                r.backtest_roi = opt.total_roi
                r.backtest_wr = opt.win_rate
                r.backtest_liq_rate = opt.liq_rate
                r.composite_score = opt.score
                if opt.total_roi > 0:
                    eligible.append(r)
            else:
                cache = self._system_g_scanner.get_cached(r.symbol)
                if cache and cache.direction == r.direction and cache.best.total_roi > 0:
                    r.opt_status = "CACHED"
                    r.opt_result = cache.best
                    r.smart_leverage = cache.best.combo.leverage
                    r.tp_pct = cache.best.combo.tp_pct
                    r.sl_pct = cache.best.combo.sl_pct
                    r.sl_mode = cache.best.combo.sl_mode
                    r.backtest_roi = cache.best.total_roi
                    r.backtest_wr = cache.best.win_rate
                    r.composite_score = cache.best.score
                    eligible.append(r)
                else:
                    r.opt_status = "PENDING"

        eligible.sort(key=lambda r: -r.composite_score)
        return results, eligible

    def _sg_try_buy(self, eligible: list, sg: dict, max_pos: int) -> None:
        """Select best candidate and buy."""
        now = time.time()
        self._failed_symbols = {
            s: t for s, t in self._failed_symbols.items()
            if now - t < self._failed_cooldown
        }

        pos_cfg = sg.get("position", {})
        max_per_coin = pos_cfg.get("max_per_coin", 1)
        cooldown_min = pos_cfg.get("cooldown_after_liq_minutes", 60)

        candidates = []
        for r in eligible:
            if r.symbol in self._failed_symbols:
                continue
            if self._position_mgr.is_holding(r.symbol):
                continue
            if r.symbol in self._pending_limits:
                continue
            if r.opt_status not in ("CACHED", "FRESH"):
                continue
            if r.backtest_roi <= 0:
                continue
            candidates.append(r)

        if not candidates or not self._position_mgr.has_capacity:
            return

        # Balance check
        real_balance = 0.0
        if self._order_executor and hasattr(self._order_executor, "get_balance"):
            try:
                real_balance = self._order_executor.get_balance()
            except Exception:
                pass
        if real_balance > 0 and real_balance < 0.10:
            logger.info(f"[SysG] Balance too low ({real_balance:.4f}$)")
            return

        cand = candidates[0]
        total_occupied = self._position_mgr.position_count + len(self._pending_limits)
        if total_occupied < max_pos:
            if self._check_trade_frequency():
                sl_str = "YOK" if cand.sl_mode == "no_sl" else f"{cand.sl_pct}%"
                logger.info(f"[SysG] GIRIS: {cand.symbol} {cand.direction} "
                            f"lev={cand.smart_leverage}x TP={cand.tp_pct}% SL={sl_str} "
                            f"bt_roi={cand.backtest_roi:+.1f}% bt_wr={cand.backtest_wr:.0f}% "
                            f"score={cand.composite_score:.1f}")
                self._do_buying_system_g(cand)

    def _do_buying_system_g(self, cand: SystemGScanResult) -> bool:
        """System G pozisyon acma: optimized leverage, market giris."""
        symbol = cand.symbol
        direction = cand.direction
        side = OrderSide.BUY_LONG if direction == "LONG" else OrderSide.SELL_SHORT
        sg = self._config.get("system_g", {})
        pos_cfg = sg.get("position", {})

        # Fresh price
        price = cand.price
        try:
            ticker = self._rest.get_ticker_price(symbol)
            price = float(ticker.get("price", price))
        except Exception:
            pass

        # Optimized leverage
        leverage = cand.smart_leverage
        lev_enabled = self._config.get("leverage.enabled", False)
        if not lev_enabled:
            leverage = 1

        if leverage > 1:
            try:
                margin_est = 5.0
                available_max = self._rest.get_max_leverage(symbol, margin_est * leverage)
                if available_max < leverage:
                    leverage = available_max
                if leverage < 2:
                    logger.info(f"[SysG] {symbol} max lev {leverage}x too low, skip")
                    self._failed_symbols[symbol] = time.time()
                    return False
            except Exception as e:
                logger.warning(f"[SysG] {symbol} max leverage check failed: {e}")

        # Position sizing: wallet / divider
        real_balance = 0.0
        if self._order_executor and hasattr(self._order_executor, "get_balance"):
            try:
                real_balance = self._order_executor.get_balance()
            except Exception:
                pass

        locked_margin = self._position_mgr.get_total_margin()
        wallet = real_balance + locked_margin
        if wallet <= 0:
            wallet = self._config.get("risk.initial_balance", 0.49)

        divider = pos_cfg.get("portfolio_divider", 12)
        margin_usdt = round(wallet / divider, 4)

        if margin_usdt < 0.10:
            logger.warning(f"[SysG] Margin too low: {margin_usdt}$")
            return False

        logger.info(f"[SysG] {symbol} sizing: wallet={wallet:.4f}$ / {divider} = "
                    f"{margin_usdt:.4f}$ x {leverage}x")

        # Set leverage + margin type
        if leverage > 1:
            try:
                self._rest.set_margin_type(symbol, "ISOLATED")
            except Exception:
                pass
            try:
                self._rest.set_leverage(symbol, leverage)
            except Exception as e:
                logger.warning(f"[SysG] set_leverage failed: {e}")

        # Qty precision
        qty_precision = 3
        min_notional = 5.0
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    qty_precision = si.quantity_precision
                    min_notional = si.min_notional
            except Exception:
                pass

        notional_usdt = margin_usdt * leverage
        if notional_usdt < min_notional:
            needed = min_notional / leverage * 1.05
            if needed > margin_usdt * 2.0:
                self._failed_symbols[symbol] = time.time()
                return False
            margin_usdt = round(needed, 4)
            notional_usdt = margin_usdt * leverage

        size_qty = round(notional_usdt / price, qty_precision) if price > 0 else 0

        # MARKET entry
        try:
            if not self._order_executor:
                return False
            self._order_executor.execute_order(
                symbol=symbol, side=side,
                order_type=OrderType.MARKET,
                size=size_qty, price=price,
                leverage=leverage, sl_percent=0, tp_percent=0,
            )
        except Exception as e:
            logger.error(f"[SysG] Order failed for {symbol}: {e}")
            self._failed_symbols[symbol] = time.time()
            return False

        # Open position
        pos = self._position_mgr.open_position(
            symbol=symbol, side=side, price=price,
            size=size_qty, atr=cand.atr_pct * price / 100 if cand.atr_pct > 0 else 0,
            leverage=leverage, margin_usdt=margin_usdt,
            timeframe="5m",
            entry_score=cand.composite_score,
            entry_confluence=0.0,
            entry_adx=0.0,
            entry_rsi=50.0,
            entry_regime=f"SYS_G_{cand.sl_mode}",
            entry_regime_confidence=cand.direction_strength,
            entry_mode="SYSTEM_G",
            entry_bb_width=cand.sl_pct,  # store SL% here
            mr_tp_price=0.0,
        )

        if not pos:
            logger.error(f"[SysG] Failed to open position for {symbol}")
            return False

        # Server-side orders
        self._place_initial_orders_system_g(symbol, pos, cand, sg)

        if self._risk_manager:
            self._risk_manager.record_order(size_qty, price, margin_usdt=margin_usdt)

        self._trade_timestamps.append(time.time())
        return True

    def _place_initial_orders_system_g(self, symbol: str, pos,
                                         cand: SystemGScanResult,
                                         sg: dict) -> None:
        """Server-side SL + trailing/TP emirleri (place_order kullanır)."""
        try:
            rest = self._order_executor._rest
            pp = self._order_executor._get_price_precision(symbol)

            direction = cand.direction
            price = pos.entry_price
            size_qty = pos.size
            leverage = pos.leverage
            is_long = direction == "LONG"
            close_side = "SELL" if is_long else "BUY"

            # === SL: STOP_MARKET ===
            sl_placed = False
            sl_price = 0.0
            if cand.sl_pct > 0:
                if is_long:
                    sl_price = round(price * (1 - cand.sl_pct / 100), pp)
                else:
                    sl_price = round(price * (1 + cand.sl_pct / 100), pp)
                try:
                    rest.place_order(
                        symbol=symbol,
                        side=close_side,
                        order_type="STOP_MARKET",
                        quantity=size_qty,
                        stop_price=sl_price,
                        reduce_only=True,
                    )
                    sl_placed = True
                    logger.info(f"[SysG] SL: {sl_price} ({cand.sl_pct}%)")
                except Exception as e:
                    logger.error(f"[SysG] SL order failed: {e}")

            # === TRAILING_STOP_MARKET ===
            trailing_placed = False
            if cand.tp_pct > 0:
                # Trailing activation: TP mesafesinde aktifleşir
                if is_long:
                    activation_price = round(price * (1 + cand.tp_pct / 100), pp)
                else:
                    activation_price = round(price * (1 - cand.tp_pct / 100), pp)

                # Callback: SL% kullan (geri gelme mesafesi)
                callback_pct = max(0.1, min(5.0, round(cand.sl_pct, 1)))

                try:
                    rest.place_order(
                        symbol=symbol,
                        side=close_side,
                        order_type="TRAILING_STOP_MARKET",
                        quantity=size_qty,
                        stop_price=activation_price,
                        callback_rate=callback_pct,
                        reduce_only=True,
                    )
                    trailing_placed = True
                except Exception as e:
                    logger.error(f"[SysG] Trailing order failed: {e}")

                logger.info(f"[SysG] Trailing: aktivasyon={activation_price} "
                            f"({cand.tp_pct}%) callback={callback_pct}% | "
                            f"SL={'OK' if sl_placed else 'FAIL'} "
                            f"Trail={'OK' if trailing_placed else 'FAIL'}")

            # Server trailing state kaydet
            self._server_trailing[symbol] = {
                "callback_pct": callback_pct if cand.tp_pct > 0 else 1.0,
                "activation_price": activation_price if cand.tp_pct > 0 else 0,
                "sl_price": sl_price if sl_placed else 0,
                "timestamp": time.time(),
                "renewal_count": 0,
            }

        except Exception as e:
            logger.error(f"[SysG] _place_initial_orders failed: {e}")

    # ═══════════════════════════════════════════════════════════════════
    # SYSTEM H — Hibrit Sistem (A + B + D + F)
    # ═══════════════════════════════════════════════════════════════════

    def _do_scanning_system_h(self) -> None:
        """System H tarama döngüsü:
        Faz 1: Sabit TF'de 50 coin skorla (A gibi)
        Faz 2: Finalist coinler için Zoom Diyafram → G → kaldıraç
        Faz 3: ER+Hurst rejim tespiti
        Faz 4: P(win)/EV skor çarpanı
        """
        from scanner.system_h_scanner import ZOOM_TF_LADDER

        # Step 0: Check pending limit orders
        if self._pending_limits:
            self._check_pending_limits()

        # Step A: Check held positions
        if self._position_mgr.has_position:
            self._check_held_positions()

        self._scan_count += 1
        sh = self._config.get("system_h", {})
        strat = self._config.get("strategy", {})
        max_pos = sh.get("max_positions", strat.get("max_positions", 12))
        self._position_mgr._max_positions = max_pos

        logger.info(f"[SysH] Scan #{self._scan_count} starting... "
                    f"[positions: {self._position_mgr.position_count}/{max_pos}]")

        # ── FAZ 1: Sabit TF'de tüm coinleri skorla (A gibi) ──

        # 1. Symbol universe
        coin_sayisi = sh.get("coin_sayisi", 50)
        self._universe._top_n = coin_sayisi
        symbols = self._universe.refresh()
        if not symbols:
            logger.warning("[SysH] No symbols to scan")
            self._stop_event.wait(timeout=10)
            return

        # 1b. Cache temizliği (bellek sızıntısı önleme)
        self._system_h_scanner.cleanup_caches(active_symbols=set(symbols))

        # 2. Kline fetch — sabit TF (skorlama için)
        scan_tf = sh.get("scan_timeframe", strat.get("kline_interval", "5m"))
        kline_limit = sh.get("kline_limit", strat.get("kline_limit", 200))
        klines_map = self._fetcher.fetch_batch(symbols, scan_tf, kline_limit)

        # 3. Funding rates
        market_ctx = self._fetch_funding_rates(symbols)

        # 4. Ticker data
        ticker_data = {}
        try:
            tickers = self._rest.get_24h_ticker()
            for t in tickers:
                s = t.get("symbol", "")
                if s in klines_map:
                    ticker_data[s] = {
                        "volume_24h": float(t.get("quoteVolume", 0)),
                        "price_change_pct": float(t.get("priceChangePercent", 0)),
                    }
        except Exception:
            pass

        # 5. BTC correlation check
        btc_ok = True
        if strat.get("btc_correlation_enabled", False):
            try:
                self._btc_corr.update()
            except Exception:
                pass

        # 6. Orderbook analysis for top symbols (pre-scoring)
        ob_map = {}
        try:
            pre_score_count = min(15, len(symbols))
            for sym in symbols[:pre_score_count]:
                depth = self._rest.get_depth(sym, limit=20)
                if depth:
                    analysis = self._ob_analyzer.analyze(depth)
                    ob_map[sym] = analysis
        except Exception:
            pass

        # Build market context map with OB data
        ctx_map = {}
        for sym in symbols:
            ctx = market_ctx.get(sym, {})
            if sym in ob_map:
                ctx.update(ob_map[sym])
            if ctx:
                ctx_map[sym] = ctx

        # 7. Score all symbols (Faz 1 — sabit TF, sıralama amaçlı)
        results = self._system_h_scanner.score_batch(klines_map, ticker_data, ctx_map)
        self._last_system_h_results = results

        eligible = [r for r in results if r.eligible]

        # Log top 5
        for r in results[:5]:
            logger.info(f"  [SysH F1] {r.symbol}: score={r.score:+.1f} "
                        f"dir={r.direction} rsi={r.rsi:.0f} adx={r.adx:.0f} "
                        f"eligible={r.eligible} reject={r.reject_reason or '-'}")

        logger.info(f"[SysH] Faz 1: {len(results)} total, {len(eligible)} eligible")

        # Publish for GUI
        self._event_bus.publish(EventType.SCANNER_UPDATE, {
            "scan_count": self._scan_count,
            "total_symbols": len(symbols),
            "scored": len(results),
            "eligible": len(eligible),
            "mr_scored": 0,
            "mr_eligible": 0,
            "positions": self._position_mgr.position_count,
            "max_positions": max_pos,
            "system_h": True,
            "candidate": eligible[0].symbol if eligible else None,
        })

        # Close-only mode
        if strat.get("close_only", False):
            self._wait(sh.get("scan_interval_seconds", 30))
            return

        # ── FAZ 2+3+4: Finalist coinler için Zoom + Rejim + EV ──

        now = time.time()
        self._failed_symbols = {
            s: t for s, t in self._failed_symbols.items()
            if now - t < self._failed_cooldown
        }
        self._loss_cooldown_symbols = {
            s: t for s, t in self._loss_cooldown_symbols.items()
            if now - t < self._loss_cooldown_seconds
        }

        # Direction balance
        yon_denge = sh.get("direction_balance_enabled",
                           strat.get("direction_balance_enabled", True))
        yon_oran = sh.get("direction_balance_ratio",
                          strat.get("direction_balance_ratio", "2-1"))
        try:
            yon_parts = yon_oran.split("-")
            yon_majority, yon_minority = int(yon_parts[0]), int(yon_parts[1])
        except (ValueError, IndexError):
            yon_majority, yon_minority = 2, 1

        # Filter candidates (pre-zoom)
        candidates = []
        for r in eligible:
            if r.symbol in self._failed_symbols:
                continue
            if self._position_mgr.is_holding(r.symbol):
                continue
            if r.symbol in self._pending_limits:
                continue
            if r.symbol in self._loss_cooldown_symbols:
                continue
            coin_ok, _ = self._check_coin_daily_ban(r.symbol)
            if not coin_ok:
                continue
            if yon_denge:
                dir_count = self._count_direction(r.direction)
                max_ayni = sh.get("max_same_direction", 8)
                if dir_count >= max_ayni:
                    continue
                opp_dir = "SHORT" if r.direction == "LONG" else "LONG"
                opp_count = self._count_direction(opp_dir)
                if dir_count >= yon_majority * (opp_count // yon_minority + 1):
                    continue
            candidates.append(r)

        # Top N finalist → Zoom Diyafram uygula
        max_finalists = sh.get("max_finalists", 5)
        finalists = candidates[:max_finalists]

        # BTC yön bilgisini hesapla (beta filtresi için)
        btc_direction = "FLAT"
        btc_returns = None
        if sh.get("btc_beta_filter_enabled", False):
            try:
                btc_kl = self._fetcher.fetch_batch(["BTCUSDT"], "1h", 30)
                btc_klines = btc_kl.get("BTCUSDT")
                if btc_klines is not None and len(btc_klines) >= 20:
                    if hasattr(btc_klines, 'values'):
                        btc_closes = [float(r[4]) for r in btc_klines.values.tolist()]
                    else:
                        btc_closes = [float(k[4]) for k in btc_klines]
                    if btc_closes[-1] > btc_closes[-10]:
                        btc_direction = "LONG"
                    elif btc_closes[-1] < btc_closes[-10]:
                        btc_direction = "SHORT"
                    # BTC returns hesapla (beta için)
                    import numpy as _np
                    _btc_arr = _np.array(btc_closes)
                    btc_returns = _np.diff(_btc_arr) / _btc_arr[:-1]
            except Exception:
                pass
            # Per-coin gerçek beta hesapla (Pearson korelasyon bazlı)
            for cand in finalists:
                cand.btc_direction = btc_direction
                cand.btc_beta = 0.0  # varsayılan: korelasyon yok (filtre tetiklemez)
                if btc_returns is not None and len(btc_returns) >= 10:
                    try:
                        coin_kl = klines_map.get(cand.symbol)
                        if coin_kl is not None and len(coin_kl) >= len(btc_returns) + 1:
                            if hasattr(coin_kl, 'values'):
                                coin_closes = [float(r[4]) for r in coin_kl.values.tolist()]
                            else:
                                coin_closes = [float(k[4]) for k in coin_kl]
                            # Son N+1 kapanıştan N return hesapla (BTC ile aynı uzunluk)
                            n = len(btc_returns)
                            coin_closes_tail = coin_closes[-(n + 1):]
                            import numpy as _np
                            _coin_arr = _np.array(coin_closes_tail)
                            coin_returns = _np.diff(_coin_arr) / _coin_arr[:-1]
                            if len(coin_returns) == len(btc_returns):
                                # Beta = Cov(coin, btc) / Var(btc)
                                btc_var = _np.var(btc_returns)
                                if btc_var > 1e-20:
                                    cov = _np.cov(coin_returns, btc_returns)[0][1]
                                    cand.btc_beta = round(float(cov / btc_var), 3)
                    except Exception:
                        pass

        if finalists:
            # Zoom TF'leri fetch et (sadece finalist coinler için — API tasarrufu)
            mum_sayisi = sh.get("zoom_kline_limit", 200)
            finalist_symbols = [f.symbol for f in finalists]

            klines_zoom_all: dict[str, dict[str, list]] = {sym: {} for sym in finalist_symbols}
            for tf_name, tf_min in ZOOM_TF_LADDER:
                tf_map = self._fetcher.fetch_batch(finalist_symbols, tf_name, mum_sayisi)
                for sym, klines in tf_map.items():
                    if hasattr(klines, 'values'):
                        klines_zoom_all[sym][tf_name] = klines.values.tolist()
                    else:
                        klines_zoom_all[sym][tf_name] = klines

            for cand in finalists:
                sym = cand.symbol
                klines_by_tf = klines_zoom_all.get(sym, {})

                if not klines_by_tf:
                    continue

                # Faz 2: Zoom Diyafram → G → kaldıraç/SL/TP
                self._system_h_scanner.enrich_with_zoom(cand, klines_by_tf)

                if not cand.eligible:
                    logger.info(f"  [SysH F2] {sym}: REJECTED after zoom: {cand.reject_reason}")
                    continue

                # Faz 2.5a: Climax filtresi (opsiyonel — F'den)
                if sh.get("climax_filter_enabled", False):
                    kl_zoom_climax = klines_by_tf.get(cand.zoom.optimal_tf, [])
                    if self._system_h_scanner.check_volume_climax(
                            kl_zoom_climax, cand.direction, sh):
                        cand.eligible = False
                        cand.reject_reason = "volume_climax"
                        cand.climax_detected = True
                        logger.info(f"  [SysH F2.5] {sym}: REJECTED climax detected")
                        continue

                # Faz 2.5b: BTC Beta filtresi (opsiyonel — F'den)
                if sh.get("btc_beta_filter_enabled", False):
                    btc_beta_val = cand.btc_beta
                    btc_dir_val = cand.btc_direction
                    if self._system_h_scanner.check_btc_beta_conflict(
                            cand.direction, btc_beta_val, btc_dir_val, sh):
                        cand.eligible = False
                        cand.reject_reason = f"btc_beta_{btc_dir_val}"
                        logger.info(f"  [SysH F2.5] {sym}: REJECTED btc_beta "
                                    f"conflict (beta={btc_beta_val:.2f} btc={btc_dir_val})")
                        continue

                # Faz 2.5c: Per-coin optimizer (opsiyonel — G'den)
                if sh.get("optimizer_enabled", False):
                    opt_cache = self._system_h_scanner.get_opt_cached(sym)
                    if opt_cache:
                        self._system_h_scanner.apply_optimizer_result(cand, opt_cache.best)
                        cand.opt_status = "CACHED"
                    else:
                        opt_done = self._system_h_scanner.check_optimization(sym)
                        if opt_done:
                            self._system_h_scanner.apply_optimizer_result(cand, opt_done)
                            cand.opt_status = "FRESH"
                        else:
                            kl_5m_opt = klines_by_tf.get("5m", [])
                            if kl_5m_opt and len(kl_5m_opt) >= 200:
                                self._system_h_scanner.submit_optimization(
                                    sym, cand.direction, kl_5m_opt)
                                cand.opt_status = "PENDING"
                            else:
                                cand.opt_status = "NONE"

                # Faz 3: ER+Hurst rejim tespiti
                zoom = cand.zoom
                kl_macro = klines_by_tf.get(zoom.macro_tf, [])
                kl_micro = klines_by_tf.get(zoom.optimal_tf, [])

                # Convert lists to DataFrames for ER+Hurst functions
                if kl_macro and isinstance(kl_macro, list):
                    import pandas as _pd
                    cols = ["open_time", "open", "high", "low", "close", "volume"]
                    if len(kl_macro[0]) > 6:
                        cols += [f"c{i}" for i in range(6, len(kl_macro[0]))]
                    kl_macro_df = _pd.DataFrame(kl_macro, columns=cols[:len(kl_macro[0])])
                    for c in ["open", "high", "low", "close", "volume"]:
                        if c in kl_macro_df.columns:
                            kl_macro_df[c] = kl_macro_df[c].astype(float)
                else:
                    kl_macro_df = kl_macro

                if kl_micro and isinstance(kl_micro, list):
                    import pandas as _pd
                    cols = ["open_time", "open", "high", "low", "close", "volume"]
                    if len(kl_micro[0]) > 6:
                        cols += [f"c{i}" for i in range(6, len(kl_micro[0]))]
                    kl_micro_df = _pd.DataFrame(kl_micro, columns=cols[:len(kl_micro[0])])
                    for c in ["open", "high", "low", "close", "volume"]:
                        if c in kl_micro_df.columns:
                            kl_micro_df[c] = kl_micro_df[c].astype(float)
                else:
                    kl_micro_df = kl_micro

                regime_h = self._system_h_scanner.compute_regime_er_hurst(
                    sym, kl_macro_df, kl_micro_df)
                cand.regime_h = regime_h
                cand.regime_zone = self._system_h_scanner.regime_to_zone(regime_h)

                # Rejim UNDECIDED ise, GRAY zone olarak devam et (eleme yapma)
                logger.info(f"  [SysH F3] {sym}: ER+Hurst regime={regime_h.regime} "
                            f"zone={cand.regime_zone} er_macro={regime_h.er_macro:.3f} "
                            f"er_micro={regime_h.er_micro:.3f} hurst={regime_h.hurst:.3f}")

                # Faz 3.5: Kaldıraç ve exit params'ı doğru rejimle yeniden hesapla
                # (Phase 2'de regime_zone boştu → TREND varsayılmıştı)
                self._system_h_scanner.recalc_after_regime(cand, klines_by_tf)
                if not cand.eligible:
                    logger.info(f"  [SysH F3.5] {sym}: REJECTED after regime recalc: {cand.reject_reason}")
                    continue

                # Faz 4: P(win)/EV (Zoom TF verilerinden — bedava)
                kl_zoom_tf = klines_by_tf.get(zoom.optimal_tf, [])
                self._system_h_scanner.compute_probability(cand, kl_zoom_tf)

                prob = cand.probability

                # Faz 5: H-specific final skor (A'nın 4 bileşeni yerine 5 bileşen)
                self._system_h_scanner.compute_final_score(cand)

                logger.info(f"  [SysH F4-5] {sym}: P(win)={prob.p_win:.2f} "
                            f"EV={prob.ev_pct:.1f}% "
                            f"final_score={cand.score:+.1f} "
                            f"G={cand.G:.3f}% lev={cand.leverage}x "
                            f"SL={cand.sl_pct:.2f}% trail={cand.trailing_trigger_pct:.2f}% "
                            f"opt={cand.opt_status}")

        # ── BUYING: en iyi finalistten satın al ──

        # Re-sort finalists by updated score
        buy_candidates = [c for c in finalists if c.eligible and c.G > 0]
        buy_candidates.sort(key=lambda c: abs(c.score), reverse=True)

        bought_any = False
        if buy_candidates and self._position_mgr.has_capacity:
            # Balance check
            real_balance = 0.0
            if self._order_executor and hasattr(self._order_executor, "get_balance"):
                try:
                    real_balance = self._order_executor.get_balance()
                except Exception:
                    pass
            if real_balance > 0 and real_balance < 0.30:
                logger.info(f"[SysH] Balance too low ({real_balance:.2f}$)")
                self._wait(60)
                return

            min_score = sh.get("min_buy_score", strat.get("min_buy_score", 55))

            for cand in buy_candidates:
                total_occupied = self._position_mgr.position_count + len(self._pending_limits)
                if total_occupied >= max_pos:
                    break
                if not self._check_trade_frequency():
                    break
                if abs(cand.score) < min_score:
                    break

                logger.info(f"[SysH] BUY candidate: {cand.symbol} score={cand.score:+.1f} "
                            f"dir={cand.direction} G={cand.G:.3f}% lev={cand.leverage}x "
                            f"SL={cand.sl_pct:.2f}% zone={cand.regime_zone}")

                if self._do_buying_system_h(cand):
                    bought_any = True

        if bought_any:
            self._wait(5)
        else:
            self._wait(sh.get("scan_interval_seconds",
                               strat.get("scan_interval_seconds", 30)))

    def _do_buying_system_h(self, cand: 'SystemHScanResult') -> bool:
        """System H pozisyon açma: G bazlı kaldıraç, limit emir (ATR offset)."""
        symbol = cand.symbol
        direction = cand.direction
        side = OrderSide.BUY_LONG if direction == "LONG" else OrderSide.SELL_SHORT
        sh = self._config.get("system_h", {})
        strat = self._config.get("strategy", {})

        # 1. Fresh price
        price = cand.price
        try:
            ticker = self._rest.get_ticker_price(symbol)
            price = float(ticker.get("price", price))
        except Exception:
            pass

        # 2. Leverage (G bazlı, user max ile sınırlı)
        leverage = cand.leverage
        lev_enabled = self._config.get("leverage.enabled", False)
        if not lev_enabled:
            leverage = 1

        # Binance max leverage kontrolü
        if leverage > 1:
            try:
                margin_est = 5.0
                available_max = self._rest.get_max_leverage(symbol, margin_est * leverage)
                if available_max < leverage:
                    leverage = available_max
                if leverage < sh.get("min_leverage", 2):
                    logger.info(f"[SysH] {symbol} max lev {leverage}x < min, skipping")
                    self._failed_symbols[symbol] = time.time()
                    return False
            except Exception as e:
                logger.warning(f"[SysH] {symbol} max leverage check failed: {e}")

        # 3. Position sizing: bakiye / portfoy_bolen (A'dan)
        real_balance = 0.0
        if self._order_executor and hasattr(self._order_executor, "get_balance"):
            try:
                real_balance = self._order_executor.get_balance()
            except Exception:
                pass

        divider = sh.get("portfolio_divider", strat.get("portfolio_divider", 12))
        locked_margin = self._position_mgr.get_total_margin()
        wallet = real_balance + locked_margin
        if wallet <= 0:
            wallet = self._config.get("risk.initial_balance", 5.0)

        if wallet >= 12.0:
            margin_usdt = round(wallet / divider, 2)
        elif wallet >= 4.0:
            margin_usdt = 1.0
        else:
            margin_usdt = round(wallet / 4, 2)

        if margin_usdt < 0.30:
            logger.warning(f"[SysH] Margin too low: {margin_usdt}$ (wallet={wallet:.2f}$)")
            return False
        if real_balance > 0 and margin_usdt > real_balance * 0.95:
            margin_usdt = round(real_balance * 0.95, 2)

        logger.info(f"[SysH] {symbol} sizing: wallet={wallet:.2f}$ → "
                    f"margin={margin_usdt}$ × {leverage}x")

        # 4. Set leverage + margin type
        if leverage > 1:
            try:
                self._rest.set_margin_type(symbol, "ISOLATED")
            except Exception:
                pass
            try:
                self._rest.set_leverage(symbol, leverage)
            except Exception as e:
                logger.warning(f"[SysH] set_leverage failed: {e}")

        # 5. Qty precision + tick size
        qty_precision = 3
        min_notional = 5.0
        si = None
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    qty_precision = si.quantity_precision
                    min_notional = si.min_notional
            except Exception:
                pass

        notional_usdt = margin_usdt * leverage
        if notional_usdt < min_notional:
            needed = min_notional / leverage * 1.05
            if needed > margin_usdt * 2.0:
                logger.info(f"[SysH] {symbol} min notional needs {needed:.2f}$ > 2x target, skip")
                self._failed_symbols[symbol] = time.time()
                return False
            margin_usdt = round(needed, 2)
            notional_usdt = margin_usdt * leverage

        size_qty = round(notional_usdt / price, qty_precision) if price > 0 else 0

        # 6. Entry: Limit emir (ATR offset — kısa vadeli gürültü için ATR kullanılır)
        atr_offset = sh.get("limit_atr_offset", strat.get("limit_atr_offset", 0.15))
        atr = cand.atr
        offset_amount = atr * atr_offset

        if side == OrderSide.BUY_LONG:
            limit_price = price - offset_amount
        else:
            limit_price = price + offset_amount

        # Tick size validation
        if si:
            limit_price = si.validate_price(limit_price)
        else:
            limit_price = round(limit_price, 4)

        timeout_s = sh.get("limit_timeout_seconds",
                           strat.get("limit_timeout_seconds", 300))

        # 6b. Min SL mesafesi kontrolü (fee+spread koruması)
        fee_rate = sh.get("fee_rate", 0.0004)
        fee_roundtrip = fee_rate * 2 * 100
        spread_est = fee_roundtrip * 0.5
        min_sl_pct = 3.0 * (fee_roundtrip + spread_est)
        if cand.sl_pct < min_sl_pct:
            logger.warning(f"[SysH] {symbol} SL too tight "
                           f"(SL={cand.sl_pct:.3f}% < min={min_sl_pct:.3f}%). Skipping.")
            self._failed_symbols[symbol] = time.time()
            return False

        # 7. Execute order
        try:
            if not self._order_executor:
                logger.error("[SysH] No order executor")
                return False

            self._order_executor.execute_order(
                symbol=symbol,
                side=side,
                order_type=OrderType.LIMIT,
                size=size_qty,
                price=limit_price,
                leverage=leverage,
                sl_percent=0,
                tp_percent=0,
            )
        except Exception as e:
            logger.error(f"[SysH] Order failed for {symbol}: {e}")
            self._failed_symbols[symbol] = time.time()
            return False

        # 8. Track as pending limit
        self._pending_limits[symbol] = {
            "order_id": None,
            "limit_price": limit_price,
            "side": side,
            "direction": direction,
            "size": size_qty,
            "atr": atr,
            "candidate": cand,
            "leverage": leverage,
            "lev_enabled": lev_enabled,
            "margin_usdt": margin_usdt,
            "placed_time": time.time(),
            "timeout": timeout_s,
            "qty_precision": qty_precision,
            "entry_mode": "SYSTEM_H",
        }

        logger.info(f"[SysH] Limit order placed: {symbol} {direction} "
                    f"@ {limit_price} (offset={offset_amount:.6f}) "
                    f"size={size_qty} lev={leverage}x G={cand.G:.3f}% "
                    f"SL={cand.sl_pct:.2f}% timeout={timeout_s}s")

        self._trade_timestamps.append(time.time())
        return True

    def _on_limit_filled_system_h(self, symbol: str, info: dict,
                                   fill_price: float) -> None:
        """System H limit order fill: pozisyon aç + server emirleri yerleştir."""
        cand = info["candidate"]

        self._open_position_system_h(
            symbol, cand, info["side"], fill_price,
            info["size"], info["leverage"], info["margin_usdt"])

        if self._risk_manager:
            self._risk_manager.record_order(
                info["size"], fill_price,
                margin_usdt=info["margin_usdt"],
            )

        if self._order_logger:
            self._order_logger.log_order(
                symbol=symbol, side=info["side"].value, order_type="Limit",
                price=fill_price, size=info["size"],
                notional_usdt=info["size"] * fill_price,
                status="filled",
                trigger_source=f"system_h:{cand.score:+.0f}",
            )

        self._event_bus.publish(EventType.ORDER_PLACED, {
            "symbol": symbol, "side": info["side"].value,
            "size": info["size"], "price": fill_price,
            "order_type": "SYSTEM_H_LIMIT_FILLED",
        })

    def _open_position_system_h(self, symbol: str, cand: 'SystemHScanResult',
                                side: OrderSide, price: float,
                                size_qty: float, leverage: int,
                                margin_usdt: float) -> None:
        """System H pozisyonu aç ve server-side emirleri yerleştir."""
        sh = self._config.get("system_h", {})

        pos = self._position_mgr.open_position(
            symbol=symbol,
            side=side,
            price=price,
            size=size_qty,
            atr=cand.atr,
            leverage=leverage,
            margin_usdt=margin_usdt,
            timeframe=cand.zoom.optimal_tf if cand.zoom else "5m",
            entry_score=abs(cand.score),
            entry_confluence=cand.confluence.get("score", 0) if hasattr(cand.confluence, 'get') else 0,
            entry_adx=cand.adx,
            entry_rsi=cand.rsi,
            entry_regime=cand.regime_zone or "",
            entry_regime_confidence=cand.regime_h.confidence if cand.regime_h else 0,
            entry_mode="SYSTEM_H",
            entry_bb_width=cand.G,  # G değeri → SL/trailing override için
        )

        if not pos:
            logger.error(f"[SysH] Failed to open position for {symbol}")
            return

        self._place_initial_orders_system_h(symbol, pos, cand, sh)

    def _place_initial_orders_system_h(self, symbol: str, pos,
                                        cand: 'SystemHScanResult',
                                        sh: dict) -> None:
        """Server-side SL + trailing/TP emirleri (G bazlı)."""
        rest = self._rest
        if not rest:
            return

        pp = 2
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    pp = si.price_precision
            except Exception:
                pass

        close_side = "SELL" if pos.side == OrderSide.BUY_LONG else "BUY"
        entry_price = pos.entry_price

        # 1. SL emri (STOP_MARKET) — G bazlı
        sl_pct = cand.sl_pct
        if pos.side == OrderSide.BUY_LONG:
            sl_price = round(entry_price * (1 - sl_pct / 100), pp)
        else:
            sl_price = round(entry_price * (1 + sl_pct / 100), pp)

        try:
            rest.place_order(
                symbol=symbol,
                side=close_side,
                order_type="STOP_MARKET",
                quantity=pos.size,
                stop_price=sl_price,
                reduce_only=True,
            )
            logger.info(f"[SysH SL] {symbol}: STOP_MARKET @ {sl_price} "
                        f"(SL={sl_pct:.2f}%, G={cand.G:.3f}%)")
        except Exception as e:
            logger.error(f"[SysH SL] {symbol}: FAILED: {e}")

        # 2. Rejime göre trailing veya sabit TP
        zone = cand.regime_zone or "TRENDING"

        if zone == "TRENDING" and cand.trailing_callback_pct > 0:
            # TREND: trailing stop (G bazlı)
            callback_pct = round(cand.trailing_callback_pct, 2)
            callback_pct = max(0.1, min(callback_pct, 5.0))

            activation_price = None
            if cand.trailing_trigger_pct > 0:
                if pos.side == OrderSide.BUY_LONG:
                    activation_price = round(entry_price * (1 + cand.trailing_trigger_pct / 100), pp)
                else:
                    activation_price = round(entry_price * (1 - cand.trailing_trigger_pct / 100), pp)

            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="TRAILING_STOP_MARKET",
                    quantity=pos.size,
                    callback_rate=callback_pct,
                    stop_price=activation_price,
                    reduce_only=True,
                )
                logger.info(f"[SysH Trail] {symbol}: callback={callback_pct}% "
                            f"activation={activation_price} "
                            f"(trigger={cand.trailing_trigger_pct:.2f}%×G)")
                self._server_trailing[symbol] = {
                    "callback_pct": callback_pct,
                    "timestamp": time.time(),
                    "activation_price": activation_price,
                }
            except Exception as e:
                logger.error(f"[SysH Trail] {symbol}: FAILED: {e}")

        elif zone == "GRAY" and cand.tp_pct > 0:
            # GRAY: önce sabit TP + sonra yedek trailing (TP'nin üstünde)
            if pos.side == OrderSide.BUY_LONG:
                tp_price = round(entry_price * (1 + cand.tp_pct / 100), pp)
            else:
                tp_price = round(entry_price * (1 - cand.tp_pct / 100), pp)
            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=pos.size,
                    stop_price=tp_price,
                    reduce_only=True,
                )
                logger.info(f"[SysH TP-GRAY] {symbol}: TAKE_PROFIT @ {tp_price} "
                            f"(TP={cand.tp_pct:.2f}%, zone=GRAY)")
            except Exception as e:
                logger.error(f"[SysH TP-GRAY] {symbol}: FAILED: {e}")

            # Yedek trailing: TP geçilirse bonus kâr yakalamak için
            if cand.trailing_callback_pct > 0 and cand.trailing_trigger_pct > 0:
                callback_pct = round(cand.trailing_callback_pct, 2)
                callback_pct = max(0.1, min(callback_pct, 5.0))
                if pos.side == OrderSide.BUY_LONG:
                    trail_act = round(entry_price * (1 + cand.trailing_trigger_pct / 100), pp)
                else:
                    trail_act = round(entry_price * (1 - cand.trailing_trigger_pct / 100), pp)
                try:
                    rest.place_order(
                        symbol=symbol,
                        side=close_side,
                        order_type="TRAILING_STOP_MARKET",
                        quantity=pos.size,
                        callback_rate=callback_pct,
                        stop_price=trail_act,
                        reduce_only=True,
                    )
                    logger.info(f"[SysH Trail-GRAY] {symbol}: backup trailing "
                                f"callback={callback_pct}% activation={trail_act}")
                    self._server_trailing[symbol] = {
                        "callback_pct": callback_pct,
                        "timestamp": time.time(),
                        "activation_price": trail_act,
                    }
                except Exception as e:
                    logger.error(f"[SysH Trail-GRAY] {symbol}: FAILED: {e}")

        elif cand.tp_pct > 0:
            # Sabit TP (RANGING rejim)
            if pos.side == OrderSide.BUY_LONG:
                tp_price = round(entry_price * (1 + cand.tp_pct / 100), pp)
            else:
                tp_price = round(entry_price * (1 - cand.tp_pct / 100), pp)

            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=pos.size,
                    stop_price=tp_price,
                    reduce_only=True,
                )
                logger.info(f"[SysH TP] {symbol}: TAKE_PROFIT @ {tp_price} "
                            f"(TP={cand.tp_pct:.2f}%)")
            except Exception as e:
                logger.error(f"[SysH TP] {symbol}: FAILED: {e}")

    def _place_missing_orders_system_h(self, symbol, pos, rest, pp,
                                        is_long, close_side,
                                        entry_price, actual_qty,
                                        entry_regime,
                                        need_sl, need_trailing):
        """System H: G bazlı eksik emir onarımı.

        G değeri pos.entry_bb_width alanında saklanır.
        Rejime göre: TRENDING → trailing, RANGING → sabit TP.
        """
        sh = self._config.get("system_h", {})
        G = getattr(pos, 'entry_bb_width', 0)
        if G <= 0:
            G = 0.5  # fallback

        sl_placed = False
        trail_placed = False

        # SL çarpanı: rejime göre
        if entry_regime in ("TRENDING",):
            sl_mult = sh.get("sl_mult_trend", 1.5)
        else:
            sl_mult = sh.get("sl_mult_ranging", 2.0)

        sl_pct = G * sl_mult

        # ── SL emri ──
        if need_sl:
            if is_long:
                sl_price = round(entry_price * (1 - sl_pct / 100), pp)
            else:
                sl_price = round(entry_price * (1 + sl_pct / 100), pp)
            try:
                rest.place_order(
                    symbol=symbol, side=close_side,
                    order_type="STOP_MARKET",
                    quantity=actual_qty, stop_price=sl_price,
                    reduce_only=True,
                )
                sl_placed = True
                logger.info(f"[SysH REPAIR] {symbol}: SL @ {sl_price} "
                            f"(G={G:.3f}%, {sl_mult}xG={sl_pct:.3f}%)")
            except Exception as e:
                logger.error(f"[SysH REPAIR] {symbol}: SL FAILED: {e}")

        # ── Trailing veya TP emri ──
        if need_trailing:
            if entry_regime in ("TRENDING", "GRAY"):
                # Trailing stop (G bazlı)
                tetik_mult = sh.get("trailing_trigger_g_mult", 2.5)
                mesafe_mult = sh.get("trailing_callback_g_mult", 0.5)
                callback_pct = G * mesafe_mult
                callback_pct = max(0.1, min(callback_pct, 5.0))
                callback_pct = round(callback_pct, 2)

                tetik_pct = G * tetik_mult
                if is_long:
                    activation_price = round(entry_price * (1 + tetik_pct / 100), pp)
                else:
                    activation_price = round(entry_price * (1 - tetik_pct / 100), pp)

                try:
                    rest.place_order(
                        symbol=symbol, side=close_side,
                        order_type="TRAILING_STOP_MARKET",
                        quantity=actual_qty,
                        callback_rate=callback_pct,
                        stop_price=activation_price,
                        reduce_only=True,
                    )
                    trail_placed = True
                    logger.info(f"[SysH REPAIR] {symbol}: Trailing "
                                f"callback={callback_pct}% activation={activation_price}")
                except Exception as e:
                    logger.error(f"[SysH REPAIR] {symbol}: Trailing FAILED: {e}")
            else:
                # Ranging: sabit TP
                tp_mult = sh.get("ranging_tp_g_mult", 3.0)
                tp_pct = G * tp_mult
                if is_long:
                    tp_price = round(entry_price * (1 + tp_pct / 100), pp)
                else:
                    tp_price = round(entry_price * (1 - tp_pct / 100), pp)

                try:
                    rest.place_order(
                        symbol=symbol, side=close_side,
                        order_type="TAKE_PROFIT_MARKET",
                        quantity=actual_qty,
                        stop_price=tp_price,
                        reduce_only=True,
                    )
                    trail_placed = True
                    logger.info(f"[SysH REPAIR] {symbol}: TP @ {tp_price} "
                                f"({tp_mult}xG={tp_pct:.3f}%)")
                except Exception as e:
                    logger.error(f"[SysH REPAIR] {symbol}: TP FAILED: {e}")

        return sl_placed, trail_placed

    # ═══════════════════════════════════════════════════════════════════
    # SYSTEM I — Unified Trading System
    # ═══════════════════════════════════════════════════════════════════

    def _do_scanning_system_i(self) -> None:
        """System I unified scanner — two-phase: prefilter (60s) + deep analysis (120s)."""
        from scanner.system_i_scanner import ZOOM_TF_LADDER as SI_ZOOM_TF_LADDER

        # Step 0: Check pending limit orders
        if self._pending_limits:
            self._check_pending_limits()

        # Step A: Check held positions
        if self._position_mgr.has_position:
            self._check_held_positions()

        self._scan_count += 1
        si_cfg = self._config.get("system_i", {})
        strat = self._config.get("strategy", {})
        pos_cfg = si_cfg.get("position_sizing", {})
        max_pos = pos_cfg.get("max_positions", strat.get("max_positions", 12))
        self._position_mgr._max_positions = max_pos

        logger.info(f"[SysI] Scan #{self._scan_count} starting... "
                    f"[positions: {self._position_mgr.position_count}/{max_pos}]")

        # Initialize scanner if needed
        if self._system_i_scanner is None:
            self._system_i_scanner = SystemIScanner(self._config)

        scanner = self._system_i_scanner

        # 1. Symbol universe
        coin_sayisi = si_cfg.get("coin_sayisi", 50)
        self._universe._top_n = coin_sayisi
        symbols = self._universe.refresh()
        if not symbols:
            logger.warning("[SysI] No symbols to scan")
            self._stop_event.wait(timeout=10)
            return

        # 2. Kline fetch — 5m for prefilter
        kline_limit = si_cfg.get("kline_limit", 200)
        klines_5m_map = self._fetcher.fetch_batch(symbols, "5m", kline_limit)

        # 3. Funding rates
        market_ctx = self._fetch_funding_rates(symbols)

        # 4. Ticker data for volume + volume_ratio
        volume_map = {}
        ticker_map = {}
        try:
            tickers = self._rest.get_24h_ticker()
            # Median volume hesapla (volume_ratio için referans)
            all_volumes = []
            ticker_map = {}
            for t in tickers:
                s = t.get("symbol", "")
                vol = float(t.get("quoteVolume", 0))
                ticker_map[s] = t
                if s in klines_5m_map and vol > 0:
                    volume_map[s] = vol
                    all_volumes.append(vol)
            median_vol = sorted(all_volumes)[len(all_volumes) // 2] if all_volumes else 1.0

            # market_ctx'e volume_ratio ekle
            for sym in symbols:
                if sym not in market_ctx:
                    market_ctx[sym] = {"funding_rate": 0.0, "oi_change_pct": 0.0}
                vol = volume_map.get(sym, 0.0)
                market_ctx[sym]["volume_ratio"] = vol / median_vol if median_vol > 0 else 1.0
        except Exception:
            pass

        # 4b. OI + depth verisi (pre-filter sonrası top adaylar için doldurulacak)
        # Pre-filter'da spread/depth yoksa filtreler geçer (veri yokken bloklamaz)

        # 5. Phase 1: Pre-filter
        prefilter_input = []
        for sym in symbols:
            kl = klines_5m_map.get(sym)
            if kl is None:
                continue
            if hasattr(kl, 'values'):
                kl = kl.values.tolist()
            prefilter_input.append({
                "symbol": sym,
                "klines_5m": kl,
                "market_ctx": market_ctx.get(sym, {}),
                "volume_24h": volume_map.get(sym, 0.0),
            })

        prefiltered = scanner.prefilter_symbols(prefilter_input)
        eligible_pre = [r for r in prefiltered if r.eligible]

        logger.info(f"[SysI] Prefilter: {len(prefiltered)} total, {len(eligible_pre)} eligible")

        # 6. Phase 2: Deep analysis (only if interval elapsed)
        now = time.time()
        deep_interval = si_cfg.get("deep_analysis_interval_seconds", 120)
        if now - self._si_last_deep_scan_time >= deep_interval:
            self._si_last_deep_scan_time = now

            # Tüm eligible coinler deep analize girer
            deep_n = si_cfg.get("deep_analysis_top_n", 999)
            top_candidates = sorted(eligible_pre, key=lambda r: r.volume_24h, reverse=True)[:deep_n]

            # Spike detection: fiyat değişimi + hacim kontrolü (thin-book sahte spike'ları elenir)
            spike_enabled = si_cfg.get("spike_detection_enabled", True)
            spike_min_pct = si_cfg.get("spike_min_change_pct", 1.5)
            if spike_enabled and ticker_map:
                existing_syms = {c.symbol for c in top_candidates}
                # Median hacim hesapla — düşük hacimli sahte spike'ları ele
                all_vols = sorted([float(ticker_map.get(s, {}).get("quoteVolume", 0))
                                   for s in symbols if ticker_map.get(s)])
                median_vol = all_vols[len(all_vols) // 2] if all_vols else 0
                for sym in symbols:
                    if sym in existing_syms:
                        continue
                    t = ticker_map.get(sym, {})
                    change_pct = abs(float(t.get("priceChangePercent", 0)))
                    sym_vol = float(t.get("quoteVolume", 0))
                    if change_pct >= spike_min_pct and sym_vol >= median_vol:
                        # Pre-filter'dan bu coin'in sonucunu bul (eligible olmasa da)
                        spike_r = next((r for r in prefiltered if r.symbol == sym), None)
                        if spike_r:
                            top_candidates.append(spike_r)
                            logger.info(f"[SysI] Spike detected: {sym} "
                                        f"change={change_pct:+.1f}% vol=${sym_vol/1e6:.1f}M "
                                        f"(pre-filter: {spike_r.reject_reason or 'eligible'})")

            logger.info(f"[SysI] Deep analysis: {len(top_candidates)} coins "
                        f"({len(eligible_pre)} eligible + spike)")

            # Deep adaylar için OI + depth verisi çek (spread, wall, thin_book)
            deep_syms = [c.symbol for c in top_candidates]
            self._fetch_oi_depth(deep_syms, market_ctx)

            # Batch fetch: TF bazlı toplu çekim (coin bazlı değil)
            from scanner.system_i_scanner import ZOOM_KLINE_LIMITS as SI_KLINE_LIMITS
            deep_sym_list = [c.symbol for c in top_candidates]
            all_klines = {}  # {symbol: {tf: klines}}
            for sym in deep_sym_list:
                all_klines[sym] = {}

            for tf_name, _ in SI_ZOOM_TF_LADDER:
                tf_limit = SI_KLINE_LIMITS.get(tf_name, kline_limit)
                tf_map = self._fetcher.fetch_batch(deep_sym_list, tf_name, tf_limit)
                for sym in deep_sym_list:
                    kl = tf_map.get(sym)
                    if kl is not None:
                        if hasattr(kl, 'values'):
                            all_klines[sym][tf_name] = kl.values.tolist()
                        else:
                            all_klines[sym][tf_name] = kl

            results = []
            for cand in top_candidates:
                sym = cand.symbol
                klines_by_tf = all_klines.get(sym, {})
                ctx = market_ctx.get(sym, {})
                result = scanner.deep_analyze(sym, klines_by_tf, ctx)
                results.append(result)

            self._last_system_i_results = results

            # Log top 5
            for r in self._last_system_i_results[:5]:
                logger.info(f"  [SysI F2] {r.symbol}: score={r.score:+.1f} "
                            f"dir={r.direction} pool={r.pool} "
                            f"G={r.G:.3f}% lev={r.leverage}x "
                            f"eligible={r.eligible} reject={r.reject_reason or '-'}")

        # Publish for GUI
        self._event_bus.publish(EventType.SCANNER_UPDATE, {
            "scan_count": self._scan_count,
            "total_symbols": len(symbols),
            "scored": len(prefiltered),
            "eligible": len(eligible_pre),
            "mr_scored": 0,
            "mr_eligible": 0,
            "positions": self._position_mgr.position_count,
            "max_positions": max_pos,
            "system_i": True,
            "candidate": eligible_pre[0].symbol if eligible_pre else None,
        })
        self._event_bus.publish("system_i_results", self._last_system_i_results)

        # Close-only mode
        if strat.get("close_only", False):
            self._wait(si_cfg.get("scan_interval_seconds", 30))
            return

        # 7. Buying: attempt to open positions
        self._do_buying_system_i()

        # Wait: pozisyon varken daha sık tara (çıkış sinyalleri için)
        if self._position_mgr.position_count > 0:
            self._wait(si_cfg.get("scan_interval_seconds", 30))
        else:
            self._wait(si_cfg.get("scan_interval_seconds", 60))

    def _do_buying_system_i(self) -> None:
        """Attempt to open position for best System I candidate."""
        si_cfg = self._config.get("system_i", {})
        strat = self._config.get("strategy", {})
        results = self._last_system_i_results
        if not results:
            logger.debug("[SysI-Buy] No results, skipping")
            return

        # Filter eligible, sort by score
        min_score = si_cfg.get("min_buy_score", 48)
        eligible = [r for r in results if r.eligible and r.score >= min_score and r.G > 0]
        logger.info(f"[SysI-Buy] results={len(results)}, min_score={min_score}, "
                    f"eligible={len(eligible)}")
        if not eligible:
            # Debug: neden boş?
            e_count = sum(1 for r in results if r.eligible)
            s_count = sum(1 for r in results if r.eligible and r.score >= min_score)
            g_count = sum(1 for r in results if r.eligible and r.score >= min_score and r.G > 0)
            logger.info(f"[SysI-Buy] EMPTY: eligible_flag={e_count}, "
                        f"score_pass={s_count}, g_pass={g_count}")
            return
        eligible.sort(key=lambda r: abs(r.score), reverse=True)

        # Check position limits
        pos_cfg = si_cfg.get("position_sizing", {})
        max_pos = pos_cfg.get("max_positions", strat.get("max_positions", 12))
        mr_max = pos_cfg.get("mr_max_positions", 2)
        trend_max = pos_cfg.get("trend_max_positions", 10)
        max_same_dir = pos_cfg.get("max_same_direction", 8)

        # Direction balance config
        yon_denge = si_cfg.get("direction_balance_enabled",
                               strat.get("direction_balance_enabled", True))
        yon_oran = si_cfg.get("direction_balance_ratio",
                              strat.get("direction_balance_ratio", "2-1"))
        try:
            yon_parts = yon_oran.split("-")
            yon_majority, yon_minority = int(yon_parts[0]), int(yon_parts[1])
        except (ValueError, IndexError):
            yon_majority, yon_minority = 2, 1

        # Clean expired cooldowns/bans
        now = time.time()
        self._failed_symbols = {
            s: t for s, t in self._failed_symbols.items()
            if now - t < self._failed_cooldown
        }
        self._loss_cooldown_symbols = {
            s: t for s, t in self._loss_cooldown_symbols.items()
            if now - t < self._loss_cooldown_seconds
        }

        # Balance check
        real_balance = 0.0
        if self._order_executor and hasattr(self._order_executor, "get_balance"):
            try:
                real_balance = self._order_executor.get_balance()
            except Exception:
                pass
        if real_balance > 0 and real_balance < 0.30:
            logger.info(f"[SysI-Buy] Balance too low ({real_balance:.2f}$)")
            return

        logger.info(f"[SysI-Buy] balance={real_balance:.2f}$, "
                    f"positions={self._position_mgr.position_count}/{max_pos}, "
                    f"mr_max={mr_max}, trend_max={trend_max}, "
                    f"pending_limits={len(self._pending_limits)}, "
                    f"top candidate: {eligible[0].symbol} score={eligible[0].score:.1f}")

        current_positions = self._position_mgr.get_all_positions()
        trend_count = sum(1 for p in current_positions.values()
                         if getattr(p, 'entry_regime', '') in ('TRENDING', 'GRAY'))
        mr_count = sum(1 for p in current_positions.values()
                      if getattr(p, 'entry_regime', '') == 'RANGING')

        for candidate in eligible:
            sym = candidate.symbol
            direction = candidate.direction

            # Total occupied check
            total_occupied = self._position_mgr.position_count + len(self._pending_limits)
            if total_occupied >= max_pos:
                break

            if not self._check_trade_frequency():
                break

            # Per-coin checks
            if self._position_mgr.is_holding(sym):
                continue
            if sym in self._pending_limits:
                continue
            if sym in self._failed_symbols:
                continue
            if sym in self._loss_cooldown_symbols:
                continue
            coin_ok, _ = self._check_coin_daily_ban(sym)
            if not coin_ok:
                continue

            # Pool limits
            pool = candidate.pool
            if pool == "RANGING" and mr_count >= mr_max:
                continue
            if pool in ("TREND", "GRAY") and trend_count >= trend_max:
                continue

            # Direction balance
            if yon_denge:
                dir_count = self._count_direction(direction)
                if dir_count >= max_same_dir:
                    continue
                opp_dir = "SHORT" if direction == "LONG" else "LONG"
                opp_count = self._count_direction(opp_dir)
                if dir_count >= yon_majority * (opp_count // yon_minority + 1):
                    continue

            logger.info(f"[SysI] BUY candidate: {sym} score={candidate.score:+.1f} "
                        f"dir={direction} G={candidate.G:.3f}% lev={candidate.leverage}x "
                        f"SL={candidate.sl_pct:.2f}% pool={pool}")

            if self._do_buying_system_i_single(candidate):
                break  # One position per scan cycle

    def _do_buying_system_i_single(self, cand: 'SystemIScanResult') -> bool:
        """System I pozisyon açma: G bazlı kaldıraç, limit veya market emir."""
        symbol = cand.symbol
        direction = cand.direction
        side = OrderSide.BUY_LONG if direction == "LONG" else OrderSide.SELL_SHORT
        si_cfg = self._config.get("system_i", {})
        strat = self._config.get("strategy", {})

        # 1. Fresh price
        price = cand.price
        try:
            ticker = self._rest.get_ticker_price(symbol)
            price = float(ticker.get("price", price))
        except Exception:
            pass

        # 2. Leverage (G bazlı, user max ile sınırlı)
        leverage = cand.leverage
        lev_enabled = self._config.get("leverage.enabled", False)
        if not lev_enabled:
            leverage = 1

        # Binance max leverage kontrolü
        if leverage > 1:
            try:
                margin_est = 5.0
                available_max = self._rest.get_max_leverage(symbol, margin_est * leverage)
                if available_max < leverage:
                    leverage = available_max
                min_lev = si_cfg.get("leverage", {}).get("min_leverage", 2)
                if leverage < min_lev:
                    logger.info(f"[SysI] {symbol} max lev {leverage}x < min {min_lev}, skipping")
                    self._failed_symbols[symbol] = time.time()
                    return False
            except Exception as e:
                logger.warning(f"[SysI] {symbol} max leverage check failed: {e}")

        # 3. Position sizing
        real_balance = 0.0
        if self._order_executor and hasattr(self._order_executor, "get_balance"):
            try:
                real_balance = self._order_executor.get_balance()
            except Exception:
                pass

        locked_margin = self._position_mgr.get_total_margin()
        wallet = real_balance + locked_margin
        if wallet <= 0:
            wallet = self._config.get("risk.initial_balance", 5.0)

        margin_usdt = self._system_i_scanner.calculate_position_size(wallet)
        if margin_usdt < 0.30:
            logger.warning(f"[SysI] Margin too low: {margin_usdt}$ (wallet={wallet:.2f}$)")
            return False
        if real_balance > 0 and margin_usdt > real_balance * 0.95:
            margin_usdt = round(real_balance * 0.95, 2)

        logger.info(f"[SysI] {symbol} sizing: wallet={wallet:.2f}$ → "
                    f"margin={margin_usdt}$ × {leverage}x")

        # 4. Set leverage + margin type
        if leverage > 1:
            try:
                self._rest.set_margin_type(symbol, "ISOLATED")
            except Exception:
                pass
            try:
                self._rest.set_leverage(symbol, leverage)
            except Exception as e:
                logger.warning(f"[SysI] set_leverage failed: {e}")

        # 5. Qty precision + tick size
        qty_precision = 3
        min_notional = 5.0
        si = None
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    qty_precision = si.quantity_precision
                    min_notional = si.min_notional
            except Exception:
                pass

        notional_usdt = margin_usdt * leverage
        if notional_usdt < min_notional:
            needed = min_notional / leverage * 1.05
            if needed > margin_usdt * 2.0:
                logger.info(f"[SysI] {symbol} min notional needs {needed:.2f}$ > 2x target, skip")
                self._failed_symbols[symbol] = time.time()
                return False
            margin_usdt = round(needed, 2)
            notional_usdt = margin_usdt * leverage

        size_qty = round(notional_usdt / price, qty_precision) if price > 0 else 0

        # 6. Min SL mesafesi kontrolü
        fee_rate = si_cfg.get("leverage", {}).get("fee_pct", 0.08) / 100
        fee_roundtrip = fee_rate * 2 * 100
        spread_est = fee_roundtrip * 0.5
        min_sl_pct = 3.0 * (fee_roundtrip + spread_est)
        if cand.sl_pct < min_sl_pct:
            logger.warning(f"[SysI] {symbol} SL too tight "
                           f"(SL={cand.sl_pct:.3f}% < min={min_sl_pct:.3f}%). Skipping.")
            self._failed_symbols[symbol] = time.time()
            return False

        # 7. Entry: limit or market based on regime
        # Scanner'ın hesapladığı entry_type ve entry_price kullan
        entry_cfg = si_cfg.get("entry", {})
        use_limit = cand.entry_type in ("limit", "limit_wave", "limit_g") and cand.entry_price > 0

        if use_limit:
            # Scanner BB bazlı (RANGING) veya ATR bazlı (TREND) fiyat hesapladı
            limit_price = cand.entry_price
            # Güncel fiyata göre düzelt (scanner fiyatı eski olabilir)
            if cand.atr > 0:
                max_drift = cand.atr * 0.5  # fiyat çok kaymışsa limit mantıksız
                if abs(price - limit_price) > max_drift:
                    # Fiyat çok kaydı, güncel fiyata göre yeniden hesapla
                    atr_offset = entry_cfg.get("limit_atr_offset", 0.1)
                    if side == OrderSide.BUY_LONG:
                        limit_price = price - cand.atr * atr_offset
                    else:
                        limit_price = price + cand.atr * atr_offset

            # Tick size validation
            if si:
                limit_price = si.validate_price(limit_price)
            else:
                limit_price = round(limit_price, 4)

            timeout_s = entry_cfg.get("limit_timeout_seconds", 300)

            # Execute limit order
            try:
                if not self._order_executor:
                    logger.error("[SysI] No order executor")
                    return False

                self._order_executor.execute_order(
                    symbol=symbol,
                    side=side,
                    order_type=OrderType.LIMIT,
                    size=size_qty,
                    price=limit_price,
                    leverage=leverage,
                    sl_percent=0,
                    tp_percent=0,
                )
            except Exception as e:
                logger.error(f"[SysI] Limit order failed for {symbol}: {e}")
                self._failed_symbols[symbol] = time.time()
                return False

            # Track as pending limit
            offset_amount = abs(price - limit_price)
            self._pending_limits[symbol] = {
                "order_id": None,
                "limit_price": limit_price,
                "side": side,
                "direction": direction,
                "size": size_qty,
                "atr": cand.atr,
                "candidate": cand,
                "leverage": leverage,
                "lev_enabled": lev_enabled,
                "margin_usdt": margin_usdt,
                "placed_time": time.time(),
                "timeout": timeout_s,
                "qty_precision": qty_precision,
                "entry_mode": "SYSTEM_I",
            }

            logger.info(f"[SysI] Limit order placed: {symbol} {direction} "
                        f"@ {limit_price} (offset={offset_amount:.6f}) "
                        f"size={size_qty} lev={leverage}x G={cand.G:.3f}% "
                        f"SL={cand.sl_pct:.2f}% timeout={timeout_s}s")
        else:
            # Market entry
            try:
                if not self._order_executor:
                    logger.error("[SysI] No order executor")
                    return False

                self._order_executor.execute_order(
                    symbol=symbol,
                    side=side,
                    order_type=OrderType.MARKET,
                    size=size_qty,
                    leverage=leverage,
                    sl_percent=0,
                    tp_percent=0,
                )
            except Exception as e:
                logger.error(f"[SysI] Market order failed for {symbol}: {e}")
                self._failed_symbols[symbol] = time.time()
                return False

            # Open position immediately
            self._open_position_system_i(
                symbol, cand, side, price, size_qty, leverage, margin_usdt)

            if self._risk_manager:
                self._risk_manager.record_order(
                    size_qty, price, margin_usdt=margin_usdt)

            logger.info(f"[SysI] Market order placed: {symbol} {direction} "
                        f"@ {price} size={size_qty} lev={leverage}x "
                        f"G={cand.G:.3f}% SL={cand.sl_pct:.2f}%")

        self._trade_timestamps.append(time.time())
        return True

    def _on_limit_filled_system_i(self, symbol: str, info: dict,
                                   fill_price: float) -> None:
        """System I limit order fill: pozisyon aç + server emirleri yerleştir."""
        cand = info["candidate"]

        self._open_position_system_i(
            symbol, cand, info["side"], fill_price,
            info["size"], info["leverage"], info["margin_usdt"])

        if self._risk_manager:
            self._risk_manager.record_order(
                info["size"], fill_price,
                margin_usdt=info["margin_usdt"],
            )

        if self._order_logger:
            self._order_logger.log_order(
                symbol=symbol, side=info["side"].value, order_type="Limit",
                price=fill_price, size=info["size"],
                notional_usdt=info["size"] * fill_price,
                status="filled",
                trigger_source=f"system_i:{cand.score:+.0f}",
            )

        self._event_bus.publish(EventType.ORDER_PLACED, {
            "symbol": symbol, "side": info["side"].value,
            "size": info["size"], "price": fill_price,
            "order_type": "SYSTEM_I_LIMIT_FILLED",
        })

    def _open_position_system_i(self, symbol: str, cand: 'SystemIScanResult',
                                side: OrderSide, price: float,
                                size_qty: float, leverage: int,
                                margin_usdt: float) -> None:
        """System I pozisyonu aç ve server-side emirleri yerleştir."""
        si_cfg = self._config.get("system_i", {})

        # System I kendi SL/TP fiyatlarini hesaplar — position_manager'a override gecer
        # Boylece System A formullerine bulasmaz
        sl_pct = cand.sl_pct
        tp_pct = cand.tp_pct if cand.tp_pct > 0 else cand.trailing_trigger_pct
        if tp_pct <= 0:
            tp_pct = cand.G * 2.5  # fallback
        if side == OrderSide.BUY_LONG:
            sl_price = price * (1 - sl_pct / 100)
            tp_price = price * (1 + tp_pct / 100)
        else:
            sl_price = price * (1 + sl_pct / 100)
            tp_price = price * (1 - tp_pct / 100)

        pos = self._position_mgr.open_position(
            symbol=symbol,
            side=side,
            price=price,
            size=size_qty,
            atr=cand.atr,
            leverage=leverage,
            margin_usdt=margin_usdt,
            timeframe=cand.zoom.yon_tf if cand.zoom else "5m",
            entry_score=abs(cand.score),
            entry_confluence=0,
            entry_adx=0,
            entry_rsi=cand.rsi,
            entry_regime=cand.regime.regime or "",
            entry_regime_confidence=cand.regime.confidence if cand.regime else 0,
            entry_mode="SYSTEM_I",
            entry_bb_width=cand.sl_pct,
            initial_sl_override=sl_price,
            initial_tp_override=tp_price,
        )

        if not pos:
            logger.error(f"[SysI] Failed to open position for {symbol}")
            return

        self._place_initial_orders_system_i(symbol, pos, cand, si_cfg)

    def _place_initial_orders_system_i(self, symbol: str, pos,
                                        cand: 'SystemIScanResult',
                                        si_cfg: dict) -> None:
        """Server-side SL + trailing/TP emirleri (G bazlı)."""
        rest = self._rest
        if not rest:
            return

        pp = 2
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    pp = si.price_precision
            except Exception:
                pass

        close_side = "SELL" if pos.side == OrderSide.BUY_LONG else "BUY"
        entry_price = pos.entry_price

        # 1. SL emri (STOP_MARKET) — G bazlı
        sl_pct = cand.sl_pct
        if pos.side == OrderSide.BUY_LONG:
            sl_price = round(entry_price * (1 - sl_pct / 100), pp)
        else:
            sl_price = round(entry_price * (1 + sl_pct / 100), pp)

        try:
            rest.place_order(
                symbol=symbol,
                side=close_side,
                order_type="STOP_MARKET",
                quantity=pos.size,
                stop_price=sl_price,
                reduce_only=True,
            )
            logger.info(f"[SysI SL] {symbol}: STOP_MARKET @ {sl_price} "
                        f"(SL={sl_pct:.2f}%, G={cand.G:.3f}%)")
        except Exception as e:
            logger.error(f"[SysI SL] {symbol}: FAILED: {e}")

        # 2. Rejime göre trailing veya sabit TP
        regime = cand.regime.regime if cand.regime else "TRENDING"
        pool = cand.pool

        if pool in ("TREND", "GRAY") and cand.trailing_callback_pct > 0:
            # TREND/GRAY: trailing stop (G bazlı)
            callback_pct = round(cand.trailing_callback_pct, 2)
            callback_pct = max(0.1, min(callback_pct, 5.0))

            activation_price = None
            if cand.trailing_trigger_pct > 0:
                if pos.side == OrderSide.BUY_LONG:
                    activation_price = round(entry_price * (1 + cand.trailing_trigger_pct / 100), pp)
                else:
                    activation_price = round(entry_price * (1 - cand.trailing_trigger_pct / 100), pp)

            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="TRAILING_STOP_MARKET",
                    quantity=pos.size,
                    callback_rate=callback_pct,
                    stop_price=activation_price,
                    reduce_only=True,
                )
                logger.info(f"[SysI Trail] {symbol}: callback={callback_pct}% "
                            f"activation={activation_price} "
                            f"(trigger={cand.trailing_trigger_pct:.2f}%)")
                self._server_trailing[symbol] = {
                    "callback_pct": callback_pct,
                    "timestamp": time.time(),
                    "activation_price": activation_price,
                }
            except Exception as e:
                logger.error(f"[SysI Trail] {symbol}: FAILED: {e}")

            # GRAY: ek sabit TP (trailing'den önce)
            if pool == "GRAY" and cand.tp_pct > 0:
                if pos.side == OrderSide.BUY_LONG:
                    tp_price = round(entry_price * (1 + cand.tp_pct / 100), pp)
                else:
                    tp_price = round(entry_price * (1 - cand.tp_pct / 100), pp)
                try:
                    rest.place_order(
                        symbol=symbol,
                        side=close_side,
                        order_type="TAKE_PROFIT_MARKET",
                        quantity=pos.size,
                        stop_price=tp_price,
                        reduce_only=True,
                    )
                    logger.info(f"[SysI TP-GRAY] {symbol}: TAKE_PROFIT @ {tp_price} "
                                f"(TP={cand.tp_pct:.2f}%, pool=GRAY)")
                except Exception as e:
                    logger.error(f"[SysI TP-GRAY] {symbol}: FAILED: {e}")

        elif cand.tp_pct > 0:
            # RANGING: Sabit TP
            if pos.side == OrderSide.BUY_LONG:
                tp_price = round(entry_price * (1 + cand.tp_pct / 100), pp)
            else:
                tp_price = round(entry_price * (1 - cand.tp_pct / 100), pp)

            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=pos.size,
                    stop_price=tp_price,
                    reduce_only=True,
                )
                logger.info(f"[SysI TP] {symbol}: TAKE_PROFIT @ {tp_price} "
                            f"(TP={cand.tp_pct:.2f}%)")
            except Exception as e:
                logger.error(f"[SysI TP] {symbol}: FAILED: {e}")

    def _place_missing_orders_system_i(self, symbol, pos, rest, pp,
                                        is_long, close_side,
                                        entry_price, actual_qty,
                                        entry_regime,
                                        need_sl, need_trailing):
        """System I: eksik emir onarımı.

        NOT: entry_bb_width artık doğrudan SL% saklar (G değil).
        G'ye ihtiyaç olan yerlerde SL%'den geri hesaplanır.
        """
        sl_placed = False
        trail_placed = False
        si_cfg = self._config.get("system_i", {})
        lev_cfg = si_cfg.get("leverage", {})

        sl_pct_stored = getattr(pos, 'entry_bb_width', 0)  # SL% (fee dahil)
        if sl_pct_stored <= 0:
            return sl_placed, trail_placed

        # G'yi SL%'den geri hesapla: SL% = sl_mult * G + fee_total
        fee_pct_cfg = lev_cfg.get("fee_pct", 0.08)
        slippage_pct_cfg = lev_cfg.get("slippage_pct", 0.04)
        fee_total = fee_pct_cfg + slippage_pct_cfg

        eff_regime = entry_regime
        if entry_regime == "GRAY":
            conf = getattr(pos, 'entry_regime_confidence', 0)
            eff_regime = "TRENDING" if conf > 0.5 else "RANGING"
        if eff_regime in ("TRENDING",):
            sl_mult = lev_cfg.get("trend_sl_g_mult", 1.5)
        else:
            sl_mult = lev_cfg.get("ranging_sl_g_mult", 2.0)

        G_approx = max(0.01, (sl_pct_stored - fee_total) / sl_mult)

        # SL onarımı — entry_bb_width doğrudan SL%, tekrar çarpmaya gerek yok
        if need_sl:
            sl_pct = sl_pct_stored / 100  # fraction'a çevir
            if is_long:
                sl_price = round(entry_price * (1 - sl_pct), pp)
            else:
                sl_price = round(entry_price * (1 + sl_pct), pp)
            try:
                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="STOP_MARKET",
                    quantity=actual_qty,
                    stop_price=sl_price,
                    reduce_only=True,
                )
                sl_placed = True
                logger.info(f"[SysI REPAIR] {symbol}: SL @ {sl_price} "
                            f"(SL%={sl_pct_stored:.3f}%, G≈{G_approx:.3f}%)")
            except Exception as e:
                logger.error(f"[SysI REPAIR] {symbol}: SL FAILED: {e}")

        # Trailing/TP onarımı — G_approx kullanarak (SL%'den geri hesaplanmış)
        if need_trailing:
            tp_cfg = si_cfg.get("tp", {})
            if eff_regime in ("TRENDING",):
                # Trailing — G bazlı hesapla
                trail_trigger_pct = tp_cfg.get("trailing_trigger_g_mult", 2.5) * G_approx / 100
                trail_callback_pct = tp_cfg.get("trailing_callback_g_mult", 0.5) * G_approx / 100
                callback_pct = max(0.1, min(round(trail_callback_pct * 100, 2), 5.0))
                if is_long:
                    act_price = round(entry_price * (1 + trail_trigger_pct), pp)
                else:
                    act_price = round(entry_price * (1 - trail_trigger_pct), pp)
                try:
                    rest.place_order(
                        symbol=symbol,
                        side=close_side,
                        order_type="TRAILING_STOP_MARKET",
                        quantity=actual_qty,
                        callback_rate=callback_pct,
                        stop_price=act_price,
                        reduce_only=True,
                    )
                    trail_placed = True
                    logger.info(f"[SysI REPAIR] {symbol}: trailing callback={callback_pct}% "
                                f"activation={act_price} (G≈{G_approx:.3f}%)")
                except Exception as e:
                    logger.error(f"[SysI REPAIR] {symbol}: trailing FAILED: {e}")
            else:
                # RANGING: sabit TP — G bazlı, fee-aware
                tp_mult = tp_cfg.get("ranging_tp_g_mult", 2.0)
                tp_pct_raw = tp_mult * G_approx + fee_total  # fee dahil
                # R:R koruması: TP asla SL'den küçük olmamalı
                if tp_pct_raw < sl_pct_stored:
                    tp_pct_raw = sl_pct_stored
                tp_pct = tp_pct_raw / 100
                if is_long:
                    tp_price = round(entry_price * (1 + tp_pct), pp)
                else:
                    tp_price = round(entry_price * (1 - tp_pct), pp)
                try:
                    rest.place_order(
                        symbol=symbol,
                        side=close_side,
                        order_type="TAKE_PROFIT_MARKET",
                        quantity=actual_qty,
                        stop_price=tp_price,
                        reduce_only=True,
                    )
                    trail_placed = True
                    logger.info(f"[SysI REPAIR] {symbol}: TP @ {tp_price} "
                                f"({tp_mult}xG≈{G_approx:.3f}%)")
                except Exception as e:
                    logger.error(f"[SysI REPAIR] {symbol}: TP FAILED: {e}")

        return sl_placed, trail_placed

    def get_system_i_results(self) -> list:
        """GUI erişimi için son System I tarama sonuçları."""
        return list(self._last_system_i_results)

    def get_system_h_results(self) -> list:
        """GUI erişimi için son System H tarama sonuçları."""
        return self._last_system_h_results

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SYSTEM J — Maximum Leverage First (3 turlu döngüsel tarama)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _do_scanning_system_j(self) -> None:
        """System J: 3 turlu döngüsel tarama — max kaldirac → G-bazli → zoom dirsek."""
        from scanner.system_j_scanner import TF_LADDER as SJ_TF_LADDER, ZOOM_TF_LADDER as SJ_ZOOM

        # Step 0: Check pending limit orders
        if self._pending_limits:
            self._check_pending_limits()

        # Step A: Check held positions
        if self._position_mgr.has_position:
            self._check_held_positions()

        self._scan_count += 1
        sj_cfg = self._config.get("system_j", {})
        strat = self._config.get("strategy", {})
        pos_cfg = sj_cfg.get("position", {})
        max_pos = pos_cfg.get("max_positions", strat.get("max_positions", 12))
        self._position_mgr._max_positions = max_pos

        logger.info(f"[SysJ] Scan #{self._scan_count} starting... "
                    f"[positions: {self._position_mgr.position_count}/{max_pos}]")

        # Initialize scanner if needed
        if self._system_j_scanner is None:
            self._system_j_scanner = SystemJScanner(self._config)

        scanner = self._system_j_scanner

        # 1. Symbol universe
        coin_sayisi = sj_cfg.get("coin_sayisi", 50)
        max_spikes = sj_cfg.get("max_spikes", 20)
        self._universe._top_n = coin_sayisi
        self._universe._max_spikes = max_spikes
        symbols = self._universe.refresh()
        if not symbols:
            logger.warning("[SysJ] No symbols found")
            self._wait(30)
            return

        logger.info(f"[SysJ] Universe: {len(symbols)} coins")

        # 2. Load leverage brackets (eksik coinler için incremental)
        missing_bracket = [s for s in symbols if s not in scanner._leverage_brackets]
        if missing_bracket:
            scanner.load_leverage_brackets(self._rest, symbols)

        # 3. Lazy kline fetch — turlar ilerledikçe sadece ihtiyaç olan TF çekilir
        #    Tur 1: 6 trade TF (1m-1h) + confirm TF (ihtiyaç halinde)
        #    Tur 2: aynı 6 TF (cache'den), yeni çekim yok
        #    Tur 3: genişletilmiş TF'ler (2h-1d) sadece kalan coinler için
        #    Toplam: ~300-400 çağrı (750 yerine)
        from scanner.system_j_scanner import CONFIRM_TF_MAP as SJ_CONFIRM_MAP
        klines_cache = {}  # {(symbol, tf): klines}  — scan boyunca cache
        kline_limit = sj_cfg.get("kline_limit", 200)

        def _get_klines(sym, tf):
            """Cache'li kline çekimi."""
            key = (sym, tf)
            if key in klines_cache:
                return klines_cache[key]
            try:
                kl = self._rest.get_klines(sym, tf, limit=kline_limit)
                if kl is not None and len(kl) > 0:
                    klines_cache[key] = kl
                    return kl
            except Exception:
                pass
            klines_cache[key] = None
            return None

        def _get_klines_by_tf(sym, tf_list):
            """Birden fazla TF için dict oluştur."""
            result = {}
            for tf in tf_list:
                kl = _get_klines(sym, tf)
                if kl is not None:
                    result[tf] = kl
            return result

        # Funding rates (1 batch API call)
        funding_rates = self._fetch_funding_rates(symbols)

        # Market context — ticker price batch yerine universe'deki data kullan
        market_ctx_map = {}
        ticker_data = getattr(self._universe, '_ticker_data', {})

        # Volume ratio: medyan hacme göre oran (ek API çağrısı yok)
        all_volumes = []
        for sym in symbols:
            td = ticker_data.get(sym, {})
            vol = float(td.get("volume_24h", 0)) if td else 0
            if vol > 0:
                all_volumes.append(vol)
        median_vol = sorted(all_volumes)[len(all_volumes) // 2] if all_volumes else 1.0

        for sym in symbols:
            td = ticker_data.get(sym, {})
            price = float(td.get("price", 0)) if td else 0
            vol_24h = float(td.get("volume_24h", 0)) if td else 0
            vol_ratio = vol_24h / median_vol if median_vol > 0 else 1.0
            fr_data = funding_rates.get(sym, {})
            fr = fr_data.get("funding_rate", 0) if isinstance(fr_data, dict) else fr_data
            market_ctx_map[sym] = {
                "price": price,
                "funding_rate": fr,
                "spread_pct": 0,       # depth API gerektirir, şimdilik filtre atlanır
                "depth_usd": 0,        # depth API gerektirir, şimdilik filtre atlanır
                "volume_ratio": vol_ratio,
                "volume_24h": vol_24h,
            }

        # 4. Held symbols
        held_symbols = set()
        with self._position_mgr._lock:
            held_symbols = set(self._position_mgr._positions.keys())
        held_symbols.update(self._pending_limits.keys())
        slots = max_pos - len(held_symbols)

        available = [s for s in symbols if s not in held_symbols]
        results_by_sym = {}    # {symbol: best result} — duplikasyon önler
        pass2_candidates = []  # Tur 1'de G eşiği sağlanamadı
        pass3_candidates = []  # Tur 2'de de bulunamadı

        # ── TUR 1/2 TF listesi: trade TF + confirm TF (yön tespiti için gerekli) ──
        trade_tfs = list(SJ_TF_LADDER)
        confirm_tfs = set()
        for tf in trade_tfs:
            ctf = SJ_CONFIRM_MAP.get(tf)
            if ctf:
                confirm_tfs.add(ctf)
        all_scan_tfs = trade_tfs + [tf for tf in confirm_tfs if tf not in trade_tfs]

        # ── TUR 1: TÜM coinleri tara (en iyi coin seçmek için) ──
        for sym in available:
            klines_by_tf = _get_klines_by_tf(sym, all_scan_tfs)
            ctx = market_ctx_map.get(sym, {})
            r = scanner.scan_pass1(sym, klines_by_tf, ctx)
            if r and r.eligible:
                results_by_sym[sym] = r
                logger.info(f"[SysJ Tur1] {sym}: {r.trade_tf} Lev={r.leverage}x "
                            f"G={r.G:.3f}% {r.direction} EV={r.ev_pct:.1f}%")
            elif r:
                results_by_sym[sym] = r
                # Kalıcı redler Tur 2'ye gitmez (GRAY_ZONE artık yok — Hurst çözüyor)
                if r.reject_reason in ("UNDECIDED_REGIME", "NO_DIRECTION", "NEGATIVE_EV",
                                       "LOW_RR_RANGING"):
                    pass
                else:
                    pass2_candidates.append(sym)
            else:
                pass2_candidates.append(sym)

        # ── TUR 2: G-bazlı kaldıraç (cache'den gelir) ──
        for sym in pass2_candidates:
            klines_by_tf = _get_klines_by_tf(sym, all_scan_tfs)
            ctx = market_ctx_map.get(sym, {})
            r = scanner.scan_pass2(sym, klines_by_tf, ctx)
            if r and r.eligible:
                results_by_sym[sym] = r  # Daha iyi sonuç: üzerine yaz
                logger.info(f"[SysJ Tur2] {sym}: {r.trade_tf} Lev={r.leverage}x "
                            f"G={r.G:.3f}% {r.direction} EV={r.ev_pct:.1f}%")
            elif r:
                results_by_sym[sym] = r
                pass3_candidates.append(sym)
            else:
                pass3_candidates.append(sym)

        # ── TUR 3: Genişletilmiş TF'ler, sadece kalan coinler (lazy) ──
        zoom_tfs = [tf for tf, _ in SJ_ZOOM]
        for sym in pass3_candidates:
            klines_by_tf = _get_klines_by_tf(sym, zoom_tfs)
            ctx = market_ctx_map.get(sym, {})
            r = scanner.scan_pass3(sym, klines_by_tf, ctx)
            if r:
                results_by_sym[sym] = r
                if r.eligible:
                    logger.info(f"[SysJ Tur3] {sym}: {r.trade_tf} Lev={r.leverage}x "
                                f"G={r.G:.3f}% {r.direction} EV={r.ev_pct:.1f}%")

        # Dict → list, sırala: eligible olanlar önce, skora göre azalan
        all_results = list(results_by_sym.values())
        all_results.sort(key=lambda r: (-r.eligible, -r.score))
        eligible_count = sum(1 for r in all_results if r.eligible)

        # Reject nedeni dağılımı (debug)
        reject_dist: dict[str, int] = {}
        for r in all_results:
            if not r.eligible and r.reject_reason:
                reject_dist[r.reject_reason] = reject_dist.get(r.reject_reason, 0) + 1
        reject_str = " | ".join(f"{k}={v}" for k, v in sorted(reject_dist.items(), key=lambda x: -x[1]))

        logger.info(f"[SysJ] Scan tamamlandi: {eligible_count} eligible / "
                    f"{len(all_results)} sonuc / {len(symbols)} coin, "
                    f"API calls: ~{len(klines_cache)}")
        if reject_str:
            logger.info(f"[SysJ] Reject dagilimi: {reject_str}")
        results = all_results

        self._last_system_j_results = results

        # Publish results for GUI
        self._event_bus.publish(EventType.SCANNER_UPDATE, {
            "scan_count": self._scan_count,
            "results": results,
            "source": "system_j",
        })

        # 5. Buying
        close_only = strat.get("close_only", False)
        if not close_only and slots > 0:
            self._do_buying_system_j()

        # 6. Wait
        interval = sj_cfg.get("scan_interval_seconds", 60)
        if self._position_mgr.has_position:
            interval = min(interval, 30)
        self._wait(interval)

    def _do_buying_system_j(self) -> None:
        """System J pozisyon açma — eligible sonuçlardan en iyilerini seç."""
        results = self._last_system_j_results
        if not results:
            return

        sj_cfg = self._config.get("system_j", {})
        pos_cfg = sj_cfg.get("position", {})
        min_score = sj_cfg.get("min_buy_score", 48)
        max_pos = pos_cfg.get("max_positions", 12)
        max_same_dir = pos_cfg.get("max_same_direction", 8)
        max_per_coin = pos_cfg.get("max_per_coin", 1)

        # Current position counts
        with self._position_mgr._lock:
            current_count = len(self._position_mgr._positions)
            long_count = sum(1 for p in self._position_mgr._positions.values()
                             if p.side == OrderSide.BUY_LONG)
            short_count = current_count - long_count

        if current_count >= max_pos:
            return

        # Direction balance
        dir_balance = pos_cfg.get("direction_balance_enabled", True)
        dir_ratio = pos_cfg.get("direction_balance_ratio", "2-1")
        try:
            parts = dir_ratio.split("-")
            majority = int(parts[0])
            minority = int(parts[1])
        except Exception:
            majority, minority = 2, 1

        # Filter and sort
        candidates = [r for r in results
                      if r.eligible and r.score >= min_score and r.G > 0]
        candidates.sort(key=lambda r: -r.score)

        for cand in candidates:
            if current_count >= max_pos:
                break

            sym = cand.symbol

            # Skip if already holding
            with self._position_mgr._lock:
                if sym in self._position_mgr._positions:
                    continue
            # Skip pending limits
            if sym in self._pending_limits:
                continue
            # Skip failed/banned/cooldown
            if sym in self._failed_symbols and time.time() - self._failed_symbols[sym] < self._failed_cooldown:
                continue
            if sym in self._loss_cooldown_symbols:
                cooldown = pos_cfg.get("loss_cooldown_seconds", 600)
                if time.time() - self._loss_cooldown_symbols[sym] < cooldown:
                    continue
            # Coin daily ban kontrolü
            coin_ok, coin_reason = self._check_coin_daily_ban(sym)
            if not coin_ok:
                logger.info(f"[SysJ] {sym} skipped ({coin_reason})")
                continue

            # Direction balance check
            side = OrderSide.BUY_LONG if cand.direction == "LONG" else OrderSide.SELL_SHORT
            if dir_balance:
                if side == OrderSide.BUY_LONG and long_count >= max_same_dir:
                    continue
                if side == OrderSide.SELL_SHORT and short_count >= max_same_dir:
                    continue
                dir_count = long_count if side == OrderSide.BUY_LONG else short_count
                opp_count = short_count if side == OrderSide.BUY_LONG else long_count
                if dir_count >= majority * (opp_count // minority + 1):
                    continue

            # Open position
            success = self._do_buying_system_j_single(cand)
            if success:
                current_count += 1
                if side == OrderSide.BUY_LONG:
                    long_count += 1
                else:
                    short_count += 1
                break  # Scan döngüsü başına 1 pozisyon

    def _do_buying_system_j_single(self, cand: 'SystemJScanResult') -> bool:
        """System J tek pozisyon açma."""
        symbol = cand.symbol
        direction = cand.direction
        side = OrderSide.BUY_LONG if direction == "LONG" else OrderSide.SELL_SHORT
        sj_cfg = self._config.get("system_j", {})

        # 1. Fresh price
        price = cand.price
        try:
            ticker = self._rest.get_ticker_price(symbol)
            price = float(ticker.get("price", price))
        except Exception:
            pass

        # 2. Leverage
        leverage = cand.leverage
        lev_enabled = self._config.get("leverage.enabled", False)
        if not lev_enabled:
            leverage = 1

        if leverage > 1:
            try:
                margin_est = 5.0
                available_max = self._rest.get_max_leverage(symbol, margin_est * leverage)
                if available_max < leverage:
                    leverage = available_max
                min_lev = sj_cfg.get("leverage", {}).get("min_leverage", 2)
                if leverage < min_lev:
                    logger.info(f"[SysJ] {symbol} max lev {leverage}x < min {min_lev}, skip")
                    self._failed_symbols[symbol] = time.time()
                    return False
            except Exception as e:
                logger.warning(f"[SysJ] {symbol} leverage check failed: {e}")

        # 3. Position sizing
        real_balance = 0.0
        if self._order_executor and hasattr(self._order_executor, "get_balance"):
            real_balance = self._order_executor.get_balance()
        locked_margin = 0.0
        with self._position_mgr._lock:
            for pos in self._position_mgr._positions.values():
                locked_margin += pos.margin_usdt
        wallet = real_balance + locked_margin
        if wallet <= 0:
            wallet = self._config.get("risk.initial_balance", 4.0)

        scanner = self._system_j_scanner
        margin_usdt = scanner.calculate_position_size(wallet)
        margin_usdt = min(margin_usdt, real_balance * 0.95)
        if margin_usdt < 1.0:
            logger.info(f"[SysJ] {symbol}: margin {margin_usdt:.2f} < 1.0, skip")
            return False

        # 4. Set leverage & margin type
        try:
            self._rest.set_margin_type(symbol, "ISOLATED")
        except Exception:
            pass
        try:
            self._rest.set_leverage(symbol, leverage)
        except Exception as e:
            logger.warning(f"[SysJ] {symbol} set_leverage({leverage}) failed: {e}")

        # 5. Qty calculation
        qty = margin_usdt * leverage / price if price > 0 else 0
        if qty <= 0:
            return False

        # Precision
        pp = 2
        qp = 3
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    pp = si.price_precision
                    qp = si.quantity_precision
            except Exception:
                pass

        qty = round(qty, qp)
        if qty <= 0:
            return False

        # Min notional check
        notional = qty * price
        if notional < 5.0:
            old_qty = qty
            qty = round(5.5 / price, qp)
            margin_usdt = qty * price / leverage
            logger.debug(f"[SysJ] {symbol}: notional bump {old_qty}->{qty}")

        # 6. SL tight check
        fee_rt = (sj_cfg.get("leverage", {}).get("fee_pct", 0.08) +
                  sj_cfg.get("leverage", {}).get("slippage_pct", 0.04))
        if cand.sl_pct < 3.0 * fee_rt:
            logger.info(f"[SysJ] {symbol}: SL too tight ({cand.sl_pct:.3f}% < {3*fee_rt:.3f}%)")
            return False

        # 7. Entry
        order_side = "BUY" if side == OrderSide.BUY_LONG else "SELL"

        if cand.entry_type == "limit" and cand.entry_price > 0:
            # Limit order
            limit_price = round(cand.entry_price, pp)
            # Drift check
            drift = abs(price - limit_price) / price
            atr_pct = cand.atr_pct / 100.0 if cand.atr_pct > 0 else 0.01
            if drift > 1.5 * atr_pct:
                # Too much drift, use market
                logger.info(f"[SysJ] {symbol}: limit drift {drift:.3f} > 1.5*ATR, market fallback")
                limit_price = None
            else:
                try:
                    result = self._rest.place_order(
                        symbol=symbol, side=order_side,
                        order_type="LIMIT",
                        quantity=qty, price=limit_price,
                        time_in_force="GTC",
                    )
                    order_id = result.get("orderId", 0)
                    logger.info(f"[SysJ LIMIT] {symbol} {direction} @ {limit_price} "
                                f"qty={qty} lev={leverage}x orderId={order_id}")

                    timeout = sj_cfg.get("entry", {}).get("limit_timeout_seconds", 300)
                    self._pending_limits[symbol] = {
                        "order_id": order_id,
                        "limit_price": limit_price,
                        "side": side,
                        "direction": direction,
                        "size": qty,
                        "leverage": leverage,
                        "margin_usdt": margin_usdt,
                        "atr": cand.atr,
                        "entry_mode": "SYSTEM_J",
                        "placed_at": time.time(),
                        "timeout": timeout,
                        "cand": cand,
                    }
                    return True
                except Exception as e:
                    logger.error(f"[SysJ LIMIT] {symbol}: FAILED: {e}")
                    limit_price = None  # fallback to market

        # Market order
        try:
            result = self._rest.place_order(
                symbol=symbol, side=order_side,
                order_type="MARKET",
                quantity=qty,
            )
            fill_price = float(result.get("avgPrice", price))
            logger.info(f"[SysJ MARKET] {symbol} {direction} @ {fill_price} "
                        f"qty={qty} lev={leverage}x G={cand.G:.3f}%")

            self._open_position_system_j(symbol, cand, side, fill_price,
                                         qty, leverage, margin_usdt)

            if self._order_logger:
                self._order_logger.log_order("SYSTEM_J", symbol, direction,
                                             fill_price, qty, leverage, cand.score)
            return True
        except Exception as e:
            logger.error(f"[SysJ MARKET] {symbol}: FAILED: {e}")
            self._failed_symbols[symbol] = time.time()
            return False

    def _on_limit_filled_system_j(self, symbol: str, fill_price: float,
                                   pending_info: dict) -> None:
        """System J limit emir dolunca çağrılır."""
        cand = pending_info.get("cand")
        if not cand:
            logger.warning(f"[SysJ] limit filled for {symbol} but no cand info")
            return

        side = pending_info["side"]
        size = pending_info["size"]
        leverage = pending_info["leverage"]
        margin_usdt = pending_info["margin_usdt"]

        self._open_position_system_j(symbol, cand, side, fill_price,
                                     size, leverage, margin_usdt)

        if self._order_logger:
            self._order_logger.log_order("SYSTEM_J_LIMIT", symbol,
                                         pending_info["direction"],
                                         fill_price, size, leverage,
                                         cand.score if cand else 0)

        self._event_bus.publish(EventType.ORDER_PLACED, {
            "symbol": symbol, "type": "SYSTEM_J_LIMIT_FILLED",
            "price": fill_price, "leverage": leverage,
        })

    def _open_position_system_j(self, symbol: str, cand: 'SystemJScanResult',
                                side: OrderSide, price: float,
                                size_qty: float, leverage: int,
                                margin_usdt: float) -> None:
        """System J pozisyonu aç ve server-side emirleri yerleştir."""
        sj_cfg = self._config.get("system_j", {})

        sl_pct = cand.sl_pct
        tp_pct = cand.tp_pct if cand.tp_pct > 0 else cand.trailing_trigger_pct
        if tp_pct <= 0:
            tp_pct = cand.G * 2.5

        if side == OrderSide.BUY_LONG:
            sl_price = price * (1 - sl_pct / 100)
            tp_price = price * (1 + tp_pct / 100)
        else:
            sl_price = price * (1 + sl_pct / 100)
            tp_price = price * (1 - tp_pct / 100)

        pos = self._position_mgr.open_position(
            symbol=symbol,
            side=side,
            price=price,
            size=size_qty,
            atr=cand.atr,
            leverage=leverage,
            margin_usdt=margin_usdt,
            timeframe=cand.trade_tf or "5m",
            entry_score=abs(cand.score),
            entry_confluence=0,
            entry_adx=0,
            entry_rsi=cand.rsi,
            entry_regime=cand.regime.regime if cand.regime else "",
            entry_regime_confidence=cand.regime.confidence if cand.regime else 0,
            entry_mode="SYSTEM_J",
            entry_bb_width=cand.sl_pct,
            initial_sl_override=sl_price,
            initial_tp_override=tp_price,
        )

        if not pos:
            logger.error(f"[SysJ] Failed to open position for {symbol}")
            return

        self._place_initial_orders_system_j(symbol, pos, cand, sj_cfg)

    def _place_initial_orders_system_j(self, symbol: str, pos,
                                        cand: 'SystemJScanResult',
                                        sj_cfg: dict) -> None:
        """Server-side SL + trailing/TP emirleri (G bazlı)."""
        rest = self._rest
        if not rest:
            return

        pp = 2
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    pp = si.price_precision
            except Exception:
                pass

        close_side = "SELL" if pos.side == OrderSide.BUY_LONG else "BUY"
        entry_price = pos.entry_price

        # 1. SL emri (STOP_MARKET)
        sl_pct = cand.sl_pct
        if pos.side == OrderSide.BUY_LONG:
            sl_price = round(entry_price * (1 - sl_pct / 100), pp)
        else:
            sl_price = round(entry_price * (1 + sl_pct / 100), pp)

        try:
            rest.place_order(
                symbol=symbol, side=close_side,
                order_type="STOP_MARKET",
                quantity=pos.size,
                stop_price=sl_price,
                reduce_only=True,
            )
            logger.info(f"[SysJ SL] {symbol}: STOP_MARKET @ {sl_price} "
                        f"(SL={sl_pct:.2f}%, G={cand.G:.3f}%)")
        except Exception as e:
            logger.error(f"[SysJ SL] {symbol}: FAILED: {e}")

        # 2. Trailing veya sabit TP
        # Elliott aktifken: her zaman trailing (reaktif çıkış)
        # Elliott kapalıyken: rejime göre (eski mantık)
        if cand.trailing_callback_pct > 0:
            # Trailing stop (Elliott aktif → her zaman, eski → sadece TREND)
            callback_pct = round(cand.trailing_callback_pct, 2)
            callback_pct = max(0.1, min(callback_pct, 5.0))

            if pos.side == OrderSide.BUY_LONG:
                activate_price = round(entry_price * (1 + cand.trailing_trigger_pct / 100), pp)
            else:
                activate_price = round(entry_price * (1 - cand.trailing_trigger_pct / 100), pp)

            try:
                rest.place_order(
                    symbol=symbol, side=close_side,
                    order_type="TRAILING_STOP_MARKET",
                    quantity=pos.size,
                    stop_price=activate_price,
                    callback_rate=callback_pct,
                    reduce_only=True,
                )
                logger.info(f"[SysJ TRAIL] {symbol}: trailing callback={callback_pct:.2f}% "
                            f"activate={activate_price} elliott={cand.elliott_pattern or 'off'}")

                self._server_trailing[symbol] = {
                    "callback_pct": callback_pct,
                    "activate_price": activate_price,
                    "last_update": time.time(),
                }
            except Exception as e:
                logger.error(f"[SysJ TRAIL] {symbol}: FAILED: {e}")
        else:
            # Trailing yok (callback çok sıkı): sabit TP
            tp_pct = cand.tp_pct
            if tp_pct <= 0:
                tp_pct = cand.G * 2.0

            if pos.side == OrderSide.BUY_LONG:
                tp_price = round(entry_price * (1 + tp_pct / 100), pp)
            else:
                tp_price = round(entry_price * (1 - tp_pct / 100), pp)

            try:
                rest.place_order(
                    symbol=symbol, side=close_side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=pos.size,
                    stop_price=tp_price,
                    reduce_only=True,
                )
                logger.info(f"[SysJ TP] {symbol}: TP @ {tp_price} "
                            f"(TP={tp_pct:.2f}%)")
            except Exception as e:
                logger.error(f"[SysJ TP] {symbol}: FAILED: {e}")

    def _place_missing_orders_system_j(self, symbol: str, pos) -> tuple:
        """System J: Eksik SL/trailing/TP emirlerini yeniden koy."""
        sj_cfg = self._config.get("system_j", {})
        lev_cfg = sj_cfg.get("leverage", {})
        tp_cfg = sj_cfg.get("tp", {})

        sl_pct_stored = getattr(pos, 'entry_bb_width', 0)
        if sl_pct_stored <= 0:
            return (False, False)

        fee_total = lev_cfg.get("fee_pct", 0.08) + lev_cfg.get("slippage_pct", 0.04)
        sl_mult = lev_cfg.get("sl_g_mult", 1.5)
        G_approx = max(0.01, (sl_pct_stored - fee_total) / sl_mult)

        rest = self._rest
        if not rest:
            return (False, False)

        pp = 2
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    pp = si.price_precision
            except Exception:
                pass

        close_side = "SELL" if pos.side == OrderSide.BUY_LONG else "BUY"
        sl_placed = False
        trail_placed = False

        # SL
        if pos.side == OrderSide.BUY_LONG:
            sl_price = round(pos.entry_price * (1 - sl_pct_stored / 100), pp)
        else:
            sl_price = round(pos.entry_price * (1 + sl_pct_stored / 100), pp)
        try:
            rest.place_order(
                symbol=symbol, side=close_side,
                order_type="STOP_MARKET",
                quantity=pos.size,
                stop_price=sl_price,
                reduce_only=True,
            )
            sl_placed = True
            logger.info(f"[SysJ REPAIR SL] {symbol}: @ {sl_price}")
        except Exception as e:
            logger.error(f"[SysJ REPAIR SL] {symbol}: FAILED: {e}")

        # Trailing/TP — Elliott aktifken her zaman trailing dene
        elliott_cfg = sj_cfg.get("elliott", {})
        elliott_on = elliott_cfg.get("enabled", True)

        if elliott_on:
            # Reaktif trailing parametreleri
            cb_mult = elliott_cfg.get("trail_callback_g_mult", 0.3)
            trig_mult = elliott_cfg.get("trail_trigger_g_mult", 1.0)
        else:
            # Eski rejim bazlı parametreler
            entry_regime = getattr(pos, 'entry_regime', '')
            if entry_regime in ("TRENDING", "TREND"):
                cb_mult = tp_cfg.get("trailing_callback_g_mult", 0.5)
                trig_mult = tp_cfg.get("trailing_trigger_g_mult", 2.5)
            else:
                # RANGING: sabit TP
                tp_mult = tp_cfg.get("ranging_tp_g_mult", 2.0)
                tp_pct = tp_mult * G_approx
                if pos.side == OrderSide.BUY_LONG:
                    tp_price = round(pos.entry_price * (1 + tp_pct / 100), pp)
                else:
                    tp_price = round(pos.entry_price * (1 - tp_pct / 100), pp)
                try:
                    rest.place_order(
                        symbol=symbol, side=close_side,
                        order_type="TAKE_PROFIT_MARKET",
                        quantity=pos.size,
                        stop_price=tp_price,
                        reduce_only=True,
                    )
                    trail_placed = True
                    logger.info(f"[SysJ REPAIR TP] {symbol}: @ {tp_price}")
                except Exception as e:
                    logger.error(f"[SysJ REPAIR TP] {symbol}: FAILED: {e}")
                return (sl_placed, trail_placed)

        cb_raw = cb_mult * G_approx
        if cb_raw >= 0.10:
            cb = max(0.1, min(cb_raw, 5.0))
            if pos.side == OrderSide.BUY_LONG:
                act = round(pos.entry_price * (1 + trig_mult * G_approx / 100), pp)
            else:
                act = round(pos.entry_price * (1 - trig_mult * G_approx / 100), pp)
            try:
                rest.place_order(
                    symbol=symbol, side=close_side,
                    order_type="TRAILING_STOP_MARKET",
                    quantity=pos.size,
                    stop_price=act,
                    callback_rate=round(cb, 2),
                    reduce_only=True,
                )
                trail_placed = True
                logger.info(f"[SysJ REPAIR TRAIL] {symbol}: cb={cb:.2f}%")
            except Exception as e:
                logger.error(f"[SysJ REPAIR TRAIL] {symbol}: FAILED: {e}")
        else:
            tp_pct = trig_mult * G_approx
            if pos.side == OrderSide.BUY_LONG:
                tp_price = round(pos.entry_price * (1 + tp_pct / 100), pp)
            else:
                tp_price = round(pos.entry_price * (1 - tp_pct / 100), pp)
            try:
                rest.place_order(
                    symbol=symbol, side=close_side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=pos.size,
                    stop_price=tp_price,
                    reduce_only=True,
                )
                trail_placed = True
                logger.info(f"[SysJ REPAIR TP-FALLBACK] {symbol}: @ {tp_price} (trailing too tight)")
            except Exception as e:
                logger.error(f"[SysJ REPAIR TP-FALLBACK] {symbol}: FAILED: {e}")

        return (sl_placed, trail_placed)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  SYSTEM M — AlphaTrend PRO
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _do_scanning_system_m(self) -> None:
        """System M: AlphaTrend PRO tarama — sabit 5m TF, sinyal bazlı."""

        # Step 0: Check pending limits
        if self._pending_limits:
            self._check_pending_limits()

        # Step A: Check held positions (external close detection)
        if self._position_mgr.has_position:
            self._check_held_positions_system_m()

        self._scan_count += 1
        sm_cfg = self._config.get("system_m", {})
        pos_cfg = sm_cfg.get("position", {})
        filter_cfg = sm_cfg.get("filters", {})
        max_pos = pos_cfg.get("max_positions", 12)
        self._position_mgr._max_positions = max_pos

        logger.info(f"[SysM] Scan #{self._scan_count} starting... "
                    f"[positions: {self._position_mgr.position_count}/{max_pos}]")

        # Initialize scanner if needed
        if self._system_m_scanner is None:
            self._system_m_scanner = SystemMScanner(self._config)
            # Reconstruct state from open SYSTEM_M positions (restart protection)
            held = self._position_mgr.get_all_positions()
            self._system_m_scanner.reconstruct_state_from_positions(held)

        scanner = self._system_m_scanner
        tf = sm_cfg.get("timeframe", "5m")
        kline_limit = sm_cfg.get("kline_limit", 300)

        # 1. Symbol universe
        coin_mode = sm_cfg.get("coin_mode", "top_n")
        coin_list = sm_cfg.get("coin_list", [])
        symbols = []

        if coin_mode == "manual" and coin_list:
            symbols = [s.strip().upper() for s in coin_list if s.strip()]

        if not symbols:
            coin_sayisi = sm_cfg.get("coin_sayisi", 50)
            self._universe._top_n = coin_sayisi
            self._universe._max_spikes = 0
            # System M'nin kendi min volume eşiğini Universe'e geçir
            min_vol = filter_cfg.get("min_volume_24h_usdt", 5_000_000)
            self._universe._min_volume = min_vol
            symbols = self._universe.refresh()

        if not symbols:
            logger.warning("[SysM] No symbols found")
            self._wait(30)
            return

        logger.info(f"[SysM] Universe: {len(symbols)} coins, TF: {tf}, mode: {coin_mode}")

        # 2. Fetch klines and scan each symbol
        results: list = []
        buy_signals: list = []
        sell_signals: list = []

        # Kline volume filtresi: 5m TF'de 288 bar ≈ 24 saat
        min_vol_usdt = filter_cfg.get("min_volume_24h_usdt", 5_000_000)
        vol_check_bars = 288  # 24h / 5m = 288 bar

        for sym in symbols:
            try:
                klines_raw = self._rest.get_klines(sym, tf, limit=kline_limit)
                # get_klines returns DataFrame — convert to list of lists
                if isinstance(klines_raw, pd.DataFrame):
                    if klines_raw.empty or len(klines_raw) < 50:
                        self._log_m_decision(sym, "—", "VERİ_YOK",
                                             f"kline < 50 bar", 0)
                        continue
                    klines = klines_raw[["open", "high", "low", "close", "volume"]].values.tolist()
                    # Prepend timestamp as index 0 (analyze_symbol expects [ts, open, high, low, close, volume, ...])
                    klines = [[0, row[0], row[1], row[2], row[3], row[4]] for row in klines]
                else:
                    if not klines_raw or len(klines_raw) < 50:
                        self._log_m_decision(sym, "—", "VERİ_YOK",
                                             f"kline < 50 bar", 0)
                        continue
                    klines = klines_raw

                # Katman C: Kline volume'den ~24h hacim kontrolü (ek güvenlik)
                if min_vol_usdt > 0:
                    recent = klines[-vol_check_bars:] if len(klines) >= vol_check_bars else klines
                    try:
                        vol_sum = sum(
                            float(k[5]) * float(k[4])  # volume × close ≈ quote volume
                            for k in recent
                        )
                    except (IndexError, ValueError):
                        vol_sum = 0
                    if vol_sum < min_vol_usdt:
                        self._log_m_decision(
                            sym, "—", "SİNYAL_YOK",
                            f"düşük hacim: ~${vol_sum/1e6:.1f}M < ${min_vol_usdt/1e6:.0f}M eşik",
                            0)
                        continue

                result = scanner.analyze_symbol(sym, klines)
                results.append(result)

                if result.eligible:
                    if result.signal == "BUY":
                        buy_signals.append(result)
                    elif result.signal == "SELL":
                        sell_signals.append(result)
                else:
                    # Neden sinyal yok — filtre detayları
                    reasons = []
                    if not result.adx_static_ok:
                        reasons.append(f"ADX({result.adx:.1f})<eşik")
                    if not result.adx_dynamic_ok:
                        reasons.append("ADX_dyn✗")
                    if not result.slope_ok:
                        reasons.append("slope✗")
                    if result.reject_reason and result.reject_reason != "no_signal":
                        reasons.append(result.reject_reason)
                    if not reasons:
                        reasons.append("crossover yok")
                    trend = "▲" if result.trend_color == "green" else "▼"
                    self._log_m_decision(
                        sym, "—", "SİNYAL_YOK",
                        f"{trend} ADX:{result.adx:.1f} RSI:{result.rsi:.0f} | {', '.join(reasons)}",
                        result.price)

            except Exception as e:
                logger.debug(f"[SysM] {sym}: scan error: {e}")
                self._log_m_decision(sym, "—", "HATA", str(e)[:80], 0)

        # 3. Store results for GUI
        self._last_system_m_results = results

        # 3b. Funding Rate filtresi (sadece futures modda — spot'ta FR yok)
        trading_mode = sm_cfg.get("trading_mode", "spot")
        fr_max = filter_cfg.get("funding_rate_max", 0.001)

        if trading_mode != "spot" and fr_max > 0 and (buy_signals or sell_signals):
            # Batch API: tek çağrıda tüm coinlerin FR'sini çek
            fr_symbols = list({s.symbol for s in buy_signals + sell_signals})
            fr_ctx = self._fetch_funding_rates(fr_symbols)

            filtered_buy = []
            for sig in buy_signals:
                fr = fr_ctx.get(sig.symbol, {}).get("funding_rate", 0.0)
                if fr > fr_max:
                    # Aşırı pozitif FR → long kalabalık, long girme
                    self._log_m_decision(
                        sig.symbol, "BUY", "ATLA",
                        f"FR filtresi: FR={fr*100:+.4f}% > +{fr_max*100:.2f}% "
                        f"(kalabalık long, contrarian red)",
                        sig.price)
                else:
                    filtered_buy.append(sig)

            filtered_sell = []
            for sig in sell_signals:
                fr = fr_ctx.get(sig.symbol, {}).get("funding_rate", 0.0)
                if fr < -fr_max:
                    # Aşırı negatif FR → short kalabalık, short girme
                    self._log_m_decision(
                        sig.symbol, "SELL", "ATLA",
                        f"FR filtresi: FR={fr*100:+.4f}% < -{fr_max*100:.2f}% "
                        f"(kalabalık short, contrarian red)",
                        sig.price)
                else:
                    filtered_sell.append(sig)

            fr_rejected = (len(buy_signals) - len(filtered_buy) +
                           len(sell_signals) - len(filtered_sell))
            if fr_rejected > 0:
                logger.info(f"[SysM] FR filtresi: {fr_rejected} sinyal reddedildi "
                            f"(eşik: ±{fr_max*100:.2f}%)")
            buy_signals = filtered_buy
            sell_signals = filtered_sell

        logger.info(f"[SysM] Scan complete: {len(results)} coins, "
                    f"BUY: {len(buy_signals)}, SELL: {len(sell_signals)}")

        # Log scan summary
        self._log_m_decision(
            "—", "TARAMA", "ÖZET",
            f"{len(results)} coin tarandı | BUY: {len(buy_signals)} | SELL: {len(sell_signals)}")

        # 4. Execute trades
        self._do_trading_system_m(buy_signals, sell_signals)

        # 6. Wait for next scan
        interval = sm_cfg.get("scan_interval_seconds", 300)
        self._wait(interval)

    def _do_trading_system_m(self, buy_signals: list, sell_signals: list) -> None:
        """System M trade execution — modlara göre BUY/SELL işle."""
        sm_cfg = self._config.get("system_m", {})
        short_enabled = sm_cfg.get("short_enabled", False)
        reverse_enabled = sm_cfg.get("reverse_enabled", False)
        pos_cfg = sm_cfg.get("position", {})
        max_pos = pos_cfg.get("max_positions", 12)

        held = self._position_mgr.get_all_positions()
        held_m = {sym: pos for sym, pos in held.items()
                  if pos.entry_mode == "SYSTEM_M"}

        # ── BÖLGE UYUMSUZLUĞU: kaçırılmış sinyal tespiti ──
        # Restart/kopukluk sırasında kaçırılan sinyaller nedeniyle
        # pozisyon yanlış bölgede kalabilir. Tespit et ve düzelt.
        all_results = self._last_system_m_results or []
        result_map = {r.symbol: r for r in all_results}

        for sym, pos in list(held_m.items()):
            scan_r = result_map.get(sym)
            if not scan_r:
                continue

            is_long = pos.side == OrderSide.BUY_LONG
            # Doğrudan AT karşılaştırması (trend_color fallback'i flat trend'de
            # yanlış pozitif üretebilir). at_now == at_2 → ikisi de False → aksiyon yok.
            in_short_zone = scan_r.alpha_trend < scan_r.alpha_trend_2
            in_long_zone = scan_r.alpha_trend > scan_r.alpha_trend_2

            # LONG pozisyon SHORT bölgesinde — kaçırılmış SELL sinyali
            if is_long and in_short_zone:
                logger.warning(f"[SysM ZONE] {sym}: LONG pozisyon SHORT bölgesinde — "
                               f"kaçırılmış SELL sinyali (AT={scan_r.alpha_trend:.6f} "
                               f"< AT[2]={scan_r.alpha_trend_2:.6f})")
                if reverse_enabled and short_enabled:
                    ok = self._do_reverse_system_m(sym, pos, scan_r, "SHORT")
                    self._log_m_decision(sym, "SELL", "ZONE_REVERSE→SHORT" if ok else "ZONE_REVERSE_BAŞARISIZ",
                                         f"LONG→SHORT (kaçırılmış sinyal)", scan_r.price)
                else:
                    ok = self._do_close_system_m(sym, pos, "ZONE_MISMATCH_SELL")
                    self._log_m_decision(sym, "SELL", "ZONE_KAPAT" if ok else "ZONE_KAPAT_BAŞARISIZ",
                                         f"LONG kapatıldı (yanlış bölge)", scan_r.price)
                held = self._position_mgr.get_all_positions()
                held_m = {s: p for s, p in held.items() if p.entry_mode == "SYSTEM_M"}

            # SHORT pozisyon LONG bölgesinde — kaçırılmış BUY sinyali
            elif not is_long and in_long_zone:
                logger.warning(f"[SysM ZONE] {sym}: SHORT pozisyon LONG bölgesinde — "
                               f"kaçırılmış BUY sinyali (AT={scan_r.alpha_trend:.6f} "
                               f"> AT[2]={scan_r.alpha_trend_2:.6f})")
                if reverse_enabled and short_enabled:
                    ok = self._do_reverse_system_m(sym, pos, scan_r, "LONG")
                    self._log_m_decision(sym, "BUY", "ZONE_REVERSE→LONG" if ok else "ZONE_REVERSE_BAŞARISIZ",
                                         f"SHORT→LONG (kaçırılmış sinyal)", scan_r.price)
                elif short_enabled:
                    ok = self._do_close_system_m(sym, pos, "ZONE_MISMATCH_BUY")
                    self._log_m_decision(sym, "BUY", "ZONE_KAPAT" if ok else "ZONE_KAPAT_BAŞARISIZ",
                                         f"SHORT kapatıldı (yanlış bölge)", scan_r.price)
                held = self._position_mgr.get_all_positions()
                held_m = {s: p for s, p in held.items() if p.entry_mode == "SYSTEM_M"}

        # ── SELL sinyallerini işle (önce çıkış) ──
        for sig in sell_signals:
            sym = sig.symbol
            pos = held_m.get(sym)

            if not pos:
                if short_enabled:
                    current_count = self._position_mgr.position_count
                    if current_count < max_pos:
                        ok = self._do_open_system_m(sig, "SHORT")
                        self._log_m_decision(sym, "SELL", "SHORT_AÇ" if ok else "SHORT_BAŞARISIZ",
                                             f"poz yok, short açılıyor", sig.price)
                        held = self._position_mgr.get_all_positions()
                        held_m = {s: p for s, p in held.items()
                                  if p.entry_mode == "SYSTEM_M"}
                    else:
                        self._log_m_decision(sym, "SELL", "ATLA",
                                             f"max pozisyon dolu ({current_count}/{max_pos})", sig.price)
                else:
                    self._log_m_decision(sym, "SELL", "ATLA",
                                         "short kapalı, pozisyon yok", sig.price)
                continue

            if pos.side == OrderSide.BUY_LONG:
                if reverse_enabled and short_enabled:
                    ok = self._do_reverse_system_m(sym, pos, sig, "SHORT")
                    self._log_m_decision(sym, "SELL", "REVERSE→SHORT" if ok else "REVERSE_BAŞARISIZ",
                                         "LONG→SHORT çeviriliyor", sig.price)
                else:
                    ok = self._do_close_system_m(sym, pos, "SELL_SIGNAL")
                    self._log_m_decision(sym, "SELL", "KAPAT" if ok else "KAPAT_BAŞARISIZ",
                                         "LONG pozisyon kapatılıyor", sig.price)
                held = self._position_mgr.get_all_positions()
                held_m = {s: p for s, p in held.items()
                          if p.entry_mode == "SYSTEM_M"}

        # ── BUY sinyallerini işle ──
        for sig in buy_signals:
            sym = sig.symbol
            pos = held_m.get(sym)

            if not pos:
                current_count = self._position_mgr.position_count
                if current_count >= max_pos:
                    self._log_m_decision(sym, "BUY", "ATLA",
                                         f"max pozisyon dolu ({current_count}/{max_pos})", sig.price)
                    continue
                ok = self._do_open_system_m(sig, "LONG")
                self._log_m_decision(sym, "BUY", "LONG_AÇ" if ok else "LONG_BAŞARISIZ",
                                     "yeni LONG açılıyor", sig.price)
                held = self._position_mgr.get_all_positions()
                held_m = {s: p for s, p in held.items()
                          if p.entry_mode == "SYSTEM_M"}
                continue

            if pos.side == OrderSide.SELL_SHORT:
                if reverse_enabled and short_enabled:
                    ok = self._do_reverse_system_m(sym, pos, sig, "LONG")
                    self._log_m_decision(sym, "BUY", "REVERSE→LONG" if ok else "REVERSE_BAŞARISIZ",
                                         "SHORT→LONG çeviriliyor", sig.price)
                elif short_enabled:
                    ok = self._do_close_system_m(sym, pos, "BUY_SIGNAL")
                    self._log_m_decision(sym, "BUY", "KAPAT" if ok else "KAPAT_BAŞARISIZ",
                                         "SHORT pozisyon kapatılıyor", sig.price)
                held = self._position_mgr.get_all_positions()
                held_m = {s: p for s, p in held.items()
                          if p.entry_mode == "SYSTEM_M"}

    def _do_open_system_m(self, sig, direction: str) -> bool:
        """System M pozisyon aç (LONG veya SHORT)."""
        sm_cfg = self._config.get("system_m", {})
        pos_cfg = sm_cfg.get("position", {})
        symbol = sig.symbol
        side = OrderSide.BUY_LONG if direction == "LONG" else OrderSide.SELL_SHORT
        order_side = "BUY" if direction == "LONG" else "SELL"

        price = sig.price
        try:
            ticker = self._rest.get_ticker_price(symbol)
            price = float(ticker.get("price", price))
        except Exception:
            pass

        # Leverage
        trading_mode = sm_cfg.get("trading_mode", "spot")
        if trading_mode == "spot" or (not sm_cfg.get("short_enabled", False) and direction == "SHORT"):
            leverage = 1
        else:
            leverage = sm_cfg.get("leverage", 1)
            max_lev = sm_cfg.get("max_leverage", 20)
            leverage = min(leverage, max_lev)

        # Önceki sistemlerden kalan orphan emirleri temizle (SL/TP/trailing)
        try:
            self._rest.cancel_all_orders(symbol)
        except Exception:
            pass

        # HER ZAMAN Binance'te leverage set et (onceki ayar farkli olabilir)
        try:
            self._rest.set_margin_type(symbol, "ISOLATED")
        except Exception as e:
            if "-4046" not in str(e):
                logger.warning(f"[SysM] {symbol} set_margin_type failed: {e}")
        try:
            self._rest.set_leverage(symbol, max(leverage, 1))
        except Exception as e:
            logger.warning(f"[SysM] {symbol} set_leverage({leverage}) failed: {e}")

        # Position sizing
        real_balance = 0.0
        if self._order_executor and hasattr(self._order_executor, "get_balance"):
            real_balance = self._order_executor.get_balance()

        # Erken bakiye kontrolü: Binance min notional 5 USDT
        min_required = 5.0 / max(leverage, 1)
        if real_balance < min_required:
            logger.debug(f"[SysM] {symbol}: bakiye yetersiz "
                         f"(available={real_balance:.2f} < min_required={min_required:.2f}), skip")
            return False

        locked_margin = 0.0
        with self._position_mgr._lock:
            for p in self._position_mgr._positions.values():
                locked_margin += p.margin_usdt
        wallet = real_balance + locked_margin
        if wallet <= 0:
            wallet = self._config.get("risk.initial_balance", 4.0)

        scanner = self._system_m_scanner
        margin_usdt = scanner.calculate_position_size(wallet)
        margin_usdt = min(margin_usdt, real_balance * 0.90) if real_balance > 0 else margin_usdt
        min_pos_usd = pos_cfg.get("min_position_usd", 1.0)
        if margin_usdt < min_pos_usd:
            logger.info(f"[SysM] {symbol}: margin {margin_usdt:.2f} < {min_pos_usd}, skip "
                        f"(available={real_balance:.2f}, wallet={wallet:.2f})")
            return False

        # Qty
        qty = margin_usdt * leverage / price if price > 0 else 0
        if qty <= 0:
            return False

        pp, qp = 2, 3
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    pp = si.price_precision
                    qp = si.quantity_precision
            except Exception:
                pass

        qty = round(qty, qp)
        if qty <= 0:
            return False

        notional = qty * price
        if notional < 5.0:
            qty = round(5.5 / price, qp)
            notional = qty * price
            margin_usdt = qty * price / leverage

        # Son kontrol: gerçek bakiyeyi aşma
        required_margin = notional / max(leverage, 1)
        if required_margin > real_balance * 0.90:
            logger.info(f"[SysM] {symbol}: margin yetersiz "
                        f"(gerekli={required_margin:.2f} > available={real_balance * 0.90:.2f}), skip")
            return False

        # Market order
        try:
            result = self._rest.place_order(
                symbol=symbol, side=order_side,
                order_type="MARKET",
                quantity=qty,
            )
            fill_price = float(result.get("avgPrice", price))
            logger.info(f"[SysM MARKET] {symbol} {direction} @ {fill_price} "
                        f"qty={qty} lev={leverage}x")

            pos = self._position_mgr.open_position(
                symbol=symbol, side=side, price=fill_price,
                size=qty, atr=sig.atr, leverage=leverage,
                margin_usdt=margin_usdt,
                timeframe=sm_cfg.get("timeframe", "5m"),
                entry_score=0, entry_mode="SYSTEM_M",
                initial_sl_override=0, initial_tp_override=0,
            )

            if not pos:
                logger.error(f"[SysM] Failed to open position for {symbol}")
                return False

            # Hemen state kaydet (crash koruması)
            try:
                self._position_mgr.save_state()
            except Exception:
                pass

            if self._order_logger:
                self._order_logger.log_order(
                    symbol=symbol, side=order_side, order_type="MARKET",
                    price=fill_price, size=qty,
                    notional_usdt=qty * fill_price,
                    status="filled",
                    trigger_source=f"system_m:{direction}",
                )
            return True

        except Exception as e:
            logger.error(f"[SysM MARKET] {symbol} {direction}: FAILED: {e} "
                         f"(qty={qty}, notional={qty * price:.2f}, "
                         f"available={real_balance:.2f})")
            self._failed_symbols[symbol] = time.time()
            return False

    def _do_close_system_m(self, symbol: str, pos, reason: str) -> bool:
        """System M pozisyon kapat."""
        close_side = "SELL" if pos.side == OrderSide.BUY_LONG else "BUY"
        try:
            try:
                self._rest.cancel_all_orders(symbol)
            except Exception:
                pass

            result = self._rest.place_order(
                symbol=symbol, side=close_side,
                order_type="MARKET",
                quantity=pos.size,
                reduce_only=True,
            )
            exit_price = float(result.get("avgPrice", pos.entry_price))
            logger.info(f"[SysM CLOSE] {symbol} {reason} @ {exit_price}")

            trade = self._position_mgr.close_position(symbol, exit_price, reason)

            # Hemen state kaydet (crash koruması)
            try:
                self._position_mgr.save_state()
            except Exception:
                pass

            if self._order_logger and trade:
                from datetime import datetime as dt
                entry_t = trade.get("entry_time", 0)
                open_time = dt.fromtimestamp(entry_t).isoformat() if entry_t else ""
                fee_pct = self._config.get("strategy.fee_pct", 0.10) / 100.0
                fee_usdt = trade.get("notional_usdt", 0) * fee_pct
                self._order_logger.log_trade(
                    open_time=open_time,
                    close_time=dt.now().isoformat(),
                    symbol=symbol,
                    side=trade.get("side", ""),
                    leverage=trade.get("leverage", 1),
                    margin_usdt=trade.get("margin_usdt", 0),
                    notional_usdt=trade.get("notional_usdt", 0),
                    entry_price=trade.get("entry_price", 0),
                    exit_price=exit_price,
                    size=trade.get("size", 0),
                    pnl_usdt=trade.get("pnl_usdt", 0),
                    pnl_percent=trade.get("pnl_percent", 0),
                    roi_percent=trade.get("roi_percent", 0),
                    fee_usdt=fee_usdt,
                    exit_reason=reason,
                    hold_seconds=trade.get("hold_seconds", 0),
                    highest_price=trade.get("highest_price", 0),
                    lowest_price=trade.get("lowest_price", 0),
                    initial_sl=trade.get("initial_sl", 0),
                    initial_tp=trade.get("initial_tp", 0),
                    atr_at_entry=trade.get("atr_at_entry", 0),
                    timeframe=trade.get("timeframe", ""),
                    entry_score=trade.get("entry_score", 0),
                    entry_confluence=trade.get("entry_confluence", 0),
                    entry_adx=trade.get("entry_adx", 0),
                    entry_rsi=trade.get("entry_rsi", 0),
                    entry_regime=trade.get("entry_regime", ""),
                    entry_regime_confidence=trade.get("entry_regime_confidence", 0),
                    entry_bb_width=trade.get("entry_bb_width", 0),
                )
            return True
        except Exception as e:
            logger.error(f"[SysM CLOSE] {symbol}: FAILED: {e}")
            return False

    def _do_reverse_system_m(self, symbol: str, pos, sig,
                              new_direction: str) -> bool:
        """System M reverse: mevcut pozisyonu kapat + ters yönde aç.

        Binance trick: pos.size + new_qty ile tek emirde reverse.
        """
        sm_cfg = self._config.get("system_m", {})
        close_side = "SELL" if pos.side == OrderSide.BUY_LONG else "BUY"
        new_side = OrderSide.BUY_LONG if new_direction == "LONG" else OrderSide.SELL_SHORT

        real_balance = 0.0
        if self._order_executor and hasattr(self._order_executor, "get_balance"):
            real_balance = self._order_executor.get_balance()
        locked_margin = 0.0
        with self._position_mgr._lock:
            for p in self._position_mgr._positions.values():
                locked_margin += p.margin_usdt
        wallet = real_balance + locked_margin
        if wallet <= 0:
            wallet = self._config.get("risk.initial_balance", 4.0)

        scanner = self._system_m_scanner
        new_margin = scanner.calculate_position_size(wallet)
        new_margin = min(new_margin, real_balance * 0.90) if real_balance > 0 else new_margin

        price = sig.price
        try:
            ticker = self._rest.get_ticker_price(symbol)
            price = float(ticker.get("price", price))
        except Exception:
            pass

        leverage = sm_cfg.get("leverage", 1)
        max_lev = sm_cfg.get("max_leverage", 20)
        leverage = min(leverage, max_lev)
        if sm_cfg.get("trading_mode", "spot") == "spot":
            leverage = 1

        new_qty = new_margin * leverage / price if price > 0 else 0

        qp = 3
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    qp = si.quantity_precision
            except Exception:
                pass

        new_qty = round(new_qty, qp)
        if new_qty <= 0:
            return self._do_close_system_m(symbol, pos, f"REVERSE_{new_direction}_FAIL")

        new_notional = new_qty * price
        if new_notional < 5.0:
            new_qty = round(5.5 / price, qp)
            new_notional = new_qty * price

        # Reverse'te mevcut pos kapanıp margin serbest kalır ama yine de kontrol
        # PnL-aware: isolated margin'de serbest kalan = margin + unrealized_pnl
        if pos.entry_price > 0 and price > 0:
            if pos.side == OrderSide.BUY_LONG:
                _pnl_ratio = (price - pos.entry_price) / pos.entry_price
            else:
                _pnl_ratio = (pos.entry_price - price) / pos.entry_price
            _unrealized = pos.margin_usdt * pos.leverage * _pnl_ratio
            freed_margin = max(pos.margin_usdt + _unrealized, 0)
        else:
            freed_margin = pos.margin_usdt
        available_after = real_balance + freed_margin
        required_margin = new_notional / max(leverage, 1)
        if required_margin > available_after * 0.90:
            logger.warning(f"[SysM REVERSE] {symbol}: margin yetersiz "
                           f"(gerekli={required_margin:.2f}, "
                           f"available={real_balance:.2f}+freed={freed_margin:.2f}="
                           f"{available_after:.2f}), sadece close yapılıyor")
            return self._do_close_system_m(symbol, pos, f"REVERSE_{new_direction}_MARGIN")

        reverse_qty = round(pos.size + new_qty, qp)

        try:
            try:
                self._rest.cancel_all_orders(symbol)
            except Exception:
                pass

            result = self._rest.place_order(
                symbol=symbol, side=close_side,
                order_type="MARKET",
                quantity=reverse_qty,
            )
            fill_price = float(result.get("avgPrice", price))
            logger.info(f"[SysM REVERSE] {symbol}: "
                        f"{'LONG→SHORT' if new_direction == 'SHORT' else 'SHORT→LONG'} "
                        f"@ {fill_price} reverse_qty={reverse_qty}")

            trade = self._position_mgr.close_position(symbol, fill_price,
                                                       f"REVERSE_{new_direction}")

            if self._order_logger and trade:
                from datetime import datetime as dt
                entry_t = trade.get("entry_time", 0)
                open_time = dt.fromtimestamp(entry_t).isoformat() if entry_t else ""
                fee_pct = self._config.get("strategy.fee_pct", 0.10) / 100.0
                fee_usdt = trade.get("notional_usdt", 0) * fee_pct
                self._order_logger.log_trade(
                    open_time=open_time,
                    close_time=dt.now().isoformat(),
                    symbol=symbol,
                    side=trade.get("side", ""),
                    leverage=trade.get("leverage", 1),
                    margin_usdt=trade.get("margin_usdt", 0),
                    notional_usdt=trade.get("notional_usdt", 0),
                    entry_price=trade.get("entry_price", 0),
                    exit_price=fill_price,
                    size=trade.get("size", 0),
                    pnl_usdt=trade.get("pnl_usdt", 0),
                    pnl_percent=trade.get("pnl_percent", 0),
                    roi_percent=trade.get("roi_percent", 0),
                    fee_usdt=fee_usdt,
                    exit_reason=f"REVERSE_{new_direction}",
                    hold_seconds=trade.get("hold_seconds", 0),
                    highest_price=trade.get("highest_price", 0),
                    lowest_price=trade.get("lowest_price", 0),
                    initial_sl=trade.get("initial_sl", 0),
                    initial_tp=trade.get("initial_tp", 0),
                    atr_at_entry=trade.get("atr_at_entry", 0),
                    timeframe=trade.get("timeframe", ""),
                    entry_score=trade.get("entry_score", 0),
                    entry_confluence=trade.get("entry_confluence", 0),
                    entry_adx=trade.get("entry_adx", 0),
                    entry_rsi=trade.get("entry_rsi", 0),
                    entry_regime=trade.get("entry_regime", ""),
                    entry_regime_confidence=trade.get("entry_regime_confidence", 0),
                    entry_bb_width=trade.get("entry_bb_width", 0),
                )

            new_pos = self._position_mgr.open_position(
                symbol=symbol, side=new_side, price=fill_price,
                size=new_qty, atr=sig.atr, leverage=leverage,
                margin_usdt=new_margin,
                timeframe=sm_cfg.get("timeframe", "5m"),
                entry_score=0, entry_mode="SYSTEM_M",
                initial_sl_override=0, initial_tp_override=0,
            )

            if not new_pos:
                logger.error(f"[SysM REVERSE] {symbol}: new pos failed")

            # Hemen state kaydet (crash koruması)
            try:
                self._position_mgr.save_state()
            except Exception:
                pass

            return True

        except Exception as e:
            logger.error(f"[SysM REVERSE] {symbol}: FAILED: {e} "
                         f"(reverse_qty={reverse_qty}, "
                         f"available={real_balance:.2f})")
            return self._do_close_system_m(symbol, pos, f"REVERSE_{new_direction}_ERR")

    def _check_held_positions_system_m(self) -> None:
        """System M pozisyon kontrolü — sadece external close tespiti.
        Sinyal bazlı çıkış _do_trading_system_m'de yapılır."""
        self._detect_external_closes()


    # ══════════════════════════════════════════════════════════════════��
    #  SYSTEM N — AlphaTrend PRO v2 (System M kopya — gelistirme icin)
    # ═══════════════════════════════════════════════════════════════════

    def _do_scanning_system_n(self) -> None:
        """System N: AlphaTrend PRO v2 — dinamik TF + G-bazlı kaldıraç."""

        # Step 0: Check pending limits
        if self._pending_limits:
            self._check_pending_limits()

        # Step A: Check held positions (external close detection)
        if self._position_mgr.has_position:
            self._check_held_positions_system_n()

        self._scan_count += 1
        self._system_n_scan_count += 1
        self._system_n_scan_start_time = time.time()
        sm_cfg = self._config.get("system_n", {})
        pos_cfg = sm_cfg.get("position", {})
        filter_cfg = sm_cfg.get("filters", {})
        max_pos = pos_cfg.get("max_positions", 12)
        self._position_mgr._max_positions = max_pos

        logger.info(f"[SysN] Scan #{self._system_n_scan_count} starting... "
                    f"[positions: {self._position_mgr.position_count}/{max_pos}]")

        # Initialize scanner if needed
        if self._system_n_scanner is None:
            self._system_n_scanner = SystemNScanner(self._config)
            # Reconstruct state from open SYSTEM_N positions (restart protection)
            held = self._position_mgr.get_all_positions()
            self._system_n_scanner.reconstruct_state_from_positions(held)

        scanner = self._system_n_scanner
        # Optimize cache'i yenile (24 saatten eskiyse)
        scanner.reload_if_stale(max_age_hours=24.0)

        default_tf = sm_cfg.get("timeframe", "5m")
        kline_limit = sm_cfg.get("kline_limit", 300)

        # 1. Symbol universe — optimize cache + hacim sıralı birleştir
        coin_mode = sm_cfg.get("coin_mode", "top_n")
        coin_list = sm_cfg.get("coin_list", [])
        symbols = []

        if coin_mode == "manual" and coin_list:
            symbols = [s.strip().upper() for s in coin_list if s.strip()]

        if not symbols:
            coin_sayisi = sm_cfg.get("coin_sayisi", 50)
            self._universe._top_n = coin_sayisi
            self._universe._max_spikes = 0
            min_vol = filter_cfg.get("min_volume_24h_usdt", 5_000_000)
            self._universe._min_volume = min_vol
            universe_syms = self._universe.refresh()

            # Optimize cache'deki kârlı coinleri de ekle (universe'de olmasa bile)
            optimized = set(scanner.get_optimized_symbols())
            universe_set = set(universe_syms)
            symbols = universe_syms + [s for s in optimized if s not in universe_set]

        if not symbols:
            logger.warning("[SysN] No symbols found")
            self._wait(30)
            return

        opt_count = sum(1 for s in symbols if s in scanner._optimize_cache)
        logger.info(f"[SysN] Universe: {len(symbols)} coins "
                    f"({opt_count} optimized), default TF: {default_tf}")

        # 2. Fetch klines and scan each symbol
        results: list = []
        buy_signals: list = []
        sell_signals: list = []

        min_vol_usdt = filter_cfg.get("min_volume_24h_usdt", 5_000_000)

        for sym in symbols:
            try:
                # Coin başına optimal TF (optimize cache'den)
                coin_params = scanner.get_coin_params(sym)
                tf = coin_params["tf"]

                # TF'ye göre volume check bar sayısı (kline_limit'i aşamaz)
                tf_mins = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
                mins = tf_mins.get(tf, 5)
                vol_check_bars_ideal = int(1440 / mins)  # 24h bar sayısı
                vol_check_bars = min(max(vol_check_bars_ideal, 50), kline_limit)

                klines_raw = self._rest.get_klines(sym, tf, limit=kline_limit)
                # get_klines returns DataFrame — convert to list of lists
                if isinstance(klines_raw, pd.DataFrame):
                    if klines_raw.empty or len(klines_raw) < 50:
                        self._log_n_decision(sym, "—", "VERİ_YOK",
                                             f"kline < 50 bar ({tf})", 0)
                        continue
                    # quote_volume varsa kullan (doğru 24h hacim), yoksa volume ile devam
                    if "quote_volume" in klines_raw.columns:
                        klines = klines_raw[["open", "high", "low", "close", "volume", "quote_volume"]].values.tolist()
                        klines = [[0, row[0], row[1], row[2], row[3], row[4], row[5]] for row in klines]
                    else:
                        klines = klines_raw[["open", "high", "low", "close", "volume"]].values.tolist()
                        klines = [[0, row[0], row[1], row[2], row[3], row[4]] for row in klines]
                else:
                    if not klines_raw or len(klines_raw) < 50:
                        self._log_n_decision(sym, "—", "VERİ_YOK",
                                             f"kline < 50 bar ({tf})", 0)
                        continue
                    klines = klines_raw

                # Volume kontrolü (kısmi veri varsa 24h'e ölçekle)
                if min_vol_usdt > 0:
                    actual_bars = min(vol_check_bars, len(klines))
                    recent = klines[-actual_bars:]
                    try:
                        # quote_volume varsa doğrudan kullan (idx 6), yoksa volume×close tahmin
                        has_quote_vol = len(recent[0]) > 6
                        if has_quote_vol:
                            vol_sum = sum(float(k[6]) for k in recent)
                        else:
                            vol_sum = sum(float(k[5]) * float(k[4]) for k in recent)
                        # Kısmi veri varsa 24h'e oranla (1m 300 mum = 5 saat → ×4.8)
                        if actual_bars < vol_check_bars_ideal:
                            vol_sum = vol_sum * (vol_check_bars_ideal / actual_bars)
                    except (IndexError, ValueError):
                        vol_sum = 0
                    if vol_sum < min_vol_usdt:
                        self._log_n_decision(
                            sym, "—", "SİNYAL_YOK",
                            f"düşük hacim: ~${vol_sum/1e6:.1f}M < ${min_vol_usdt/1e6:.0f}M eşik",
                            0)
                        continue

                result = scanner.analyze_symbol(sym, klines)
                results.append(result)

                if result.eligible:
                    if result.signal == "BUY":
                        buy_signals.append(result)
                    elif result.signal == "SELL":
                        sell_signals.append(result)
                else:
                    # Neden sinyal yok — filtre detayları
                    reasons = []
                    if not result.adx_static_ok:
                        reasons.append(f"ADX({result.adx:.1f})<eşik")
                    if not result.adx_dynamic_ok:
                        reasons.append("ADX_dyn✗")
                    if not result.slope_ok:
                        reasons.append("slope✗")
                    if result.reject_reason and result.reject_reason != "no_signal":
                        reasons.append(result.reject_reason)
                    if not reasons:
                        reasons.append("crossover yok")
                    trend = "▲" if result.trend_color == "green" else "▼"
                    self._log_n_decision(
                        sym, "—", "SİNYAL_YOK",
                        f"{trend} ADX:{result.adx:.1f} RSI:{result.rsi:.0f} | {', '.join(reasons)}",
                        result.price)

            except Exception as e:
                logger.debug(f"[SysN] {sym}: scan error: {e}")
                self._log_n_decision(sym, "—", "HATA", str(e)[:80], 0)

        # 3. Store results for GUI
        self._last_system_n_results = results

        # 3b. Funding Rate filtresi (sadece futures modda — spot'ta FR yok)
        trading_mode = sm_cfg.get("trading_mode", "spot")
        fr_max = filter_cfg.get("funding_rate_max", 0.001)

        if trading_mode != "spot" and fr_max > 0 and (buy_signals or sell_signals):
            # Batch API: tek çağrıda tüm coinlerin FR'sini çek
            fr_symbols = list({s.symbol for s in buy_signals + sell_signals})
            fr_ctx = self._fetch_funding_rates(fr_symbols)

            filtered_buy = []
            for sig in buy_signals:
                fr = fr_ctx.get(sig.symbol, {}).get("funding_rate", 0.0)
                if fr > fr_max:
                    # Aşırı pozitif FR → long kalabalık, long girme
                    self._log_n_decision(
                        sig.symbol, "BUY", "ATLA",
                        f"FR filtresi: FR={fr*100:+.4f}% > +{fr_max*100:.2f}% "
                        f"(kalabalık long, contrarian red)",
                        sig.price)
                else:
                    filtered_buy.append(sig)

            filtered_sell = []
            for sig in sell_signals:
                fr = fr_ctx.get(sig.symbol, {}).get("funding_rate", 0.0)
                if fr < -fr_max:
                    # Aşırı negatif FR → short kalabalık, short girme
                    self._log_n_decision(
                        sig.symbol, "SELL", "ATLA",
                        f"FR filtresi: FR={fr*100:+.4f}% < -{fr_max*100:.2f}% "
                        f"(kalabalık short, contrarian red)",
                        sig.price)
                else:
                    filtered_sell.append(sig)

            fr_rejected = (len(buy_signals) - len(filtered_buy) +
                           len(sell_signals) - len(filtered_sell))
            if fr_rejected > 0:
                logger.info(f"[SysN] FR filtresi: {fr_rejected} sinyal reddedildi "
                            f"(eşik: ±{fr_max*100:.2f}%)")
            buy_signals = filtered_buy
            sell_signals = filtered_sell

        logger.info(f"[SysN] Scan complete: {len(results)} coins, "
                    f"BUY: {len(buy_signals)}, SELL: {len(sell_signals)}")

        # Log scan summary
        self._log_n_decision(
            "—", "TARAMA", "ÖZET",
            f"{len(results)} coin tarandı | BUY: {len(buy_signals)} | SELL: {len(sell_signals)}")

        # 4. Execute trades
        self._do_trading_system_n(buy_signals, sell_signals)

        # 6. Wait for next scan
        interval = sm_cfg.get("scan_interval_seconds", 300)
        self._wait(interval)

    def _do_trading_system_n(self, buy_signals: list, sell_signals: list) -> None:
        """System N trade execution — modlara göre BUY/SELL işle."""
        sm_cfg = self._config.get("system_n", {})
        short_enabled = sm_cfg.get("short_enabled", False)
        reverse_enabled = sm_cfg.get("reverse_enabled", False)
        pos_cfg = sm_cfg.get("position", {})
        max_pos = pos_cfg.get("max_positions", 12)
        sn_opt = sm_cfg.get("optional_features", {})

        held = self._position_mgr.get_all_positions()
        held_m = {sym: pos for sym, pos in held.items()
                  if pos.entry_mode == "SYSTEM_N"}

        # ── Loss protection: expired cooldown temizliği ──
        now = time.time()
        if sn_opt.get("loss_cooldown_enabled", False):
            cooldown_s = sn_opt.get("loss_cooldown_seconds", 600)
            self._loss_cooldown_symbols = {
                s: t for s, t in self._loss_cooldown_symbols.items()
                if now - t < cooldown_s
            }

        # ── BÖLGE UYUMSUZLUĞU: kaçırılmış sinyal tespiti ──
        # Restart/kopukluk sırasında kaçırılan sinyaller nedeniyle
        # pozisyon yanlış bölgede kalabilir. Tespit et ve düzelt.
        # İlk taramada zone check atla — reconstruct sonrası yanlış
        # algılama riski (trend direction henüz oturmamış olabilir)
        all_results = self._last_system_n_results or []
        result_map = {r.symbol: r for r in all_results}
        _zone_check_skip = self._system_n_scan_count <= 1

        for sym, pos in list(held_m.items()):
            if _zone_check_skip:
                break  # İlk taramada zone kontrolü atla
            scan_r = result_map.get(sym)
            if not scan_r:
                continue

            is_long = pos.side == OrderSide.BUY_LONG
            # Doğrudan AT karşılaştırması (trend_color fallback'i flat trend'de
            # yanlış pozitif üretebilir). at_now == at_2 → ikisi de False → aksiyon yok.
            in_short_zone = scan_r.alpha_trend < scan_r.alpha_trend_2
            in_long_zone = scan_r.alpha_trend > scan_r.alpha_trend_2

            # LONG pozisyon SHORT bölgesinde — kaçırılmış SELL sinyali
            if is_long and in_short_zone:
                logger.warning(f"[SysN ZONE] {sym}: LONG pozisyon SHORT bölgesinde — "
                               f"kaçırılmış SELL sinyali (AT={scan_r.alpha_trend:.6f} "
                               f"< AT[2]={scan_r.alpha_trend_2:.6f})")
                if reverse_enabled and short_enabled:
                    ok = self._do_reverse_system_n(sym, pos, scan_r, "SHORT")
                    self._log_n_decision(sym, "SELL", "ZONE_REVERSE→SHORT" if ok else "ZONE_REVERSE_BAŞARISIZ",
                                         f"LONG→SHORT (kaçırılmış sinyal)", scan_r.price)
                else:
                    ok = self._do_close_system_n(sym, pos, "ZONE_MISMATCH_SELL")
                    self._log_n_decision(sym, "SELL", "ZONE_KAPAT" if ok else "ZONE_KAPAT_BAŞARISIZ",
                                         f"LONG kapatıldı (yanlış bölge)", scan_r.price)
                held = self._position_mgr.get_all_positions()
                held_m = {s: p for s, p in held.items() if p.entry_mode == "SYSTEM_N"}

            # SHORT pozisyon LONG bölgesinde — kaçırılmış BUY sinyali
            elif not is_long and in_long_zone:
                logger.warning(f"[SysN ZONE] {sym}: SHORT pozisyon LONG bölgesinde — "
                               f"kaçırılmış BUY sinyali (AT={scan_r.alpha_trend:.6f} "
                               f"> AT[2]={scan_r.alpha_trend_2:.6f})")
                if reverse_enabled and short_enabled:
                    ok = self._do_reverse_system_n(sym, pos, scan_r, "LONG")
                    self._log_n_decision(sym, "BUY", "ZONE_REVERSE→LONG" if ok else "ZONE_REVERSE_BAŞARISIZ",
                                         f"SHORT→LONG (kaçırılmış sinyal)", scan_r.price)
                elif short_enabled:
                    ok = self._do_close_system_n(sym, pos, "ZONE_MISMATCH_BUY")
                    self._log_n_decision(sym, "BUY", "ZONE_KAPAT" if ok else "ZONE_KAPAT_BAŞARISIZ",
                                         f"SHORT kapatıldı (yanlış bölge)", scan_r.price)
                else:
                    # short_enabled=False ama SHORT pozisyon var — yetim bırakma
                    ok = self._do_close_system_n(sym, pos, "ZONE_MISMATCH_SHORT_DISABLED")
                    self._log_n_decision(sym, "BUY", "ZONE_KAPAT" if ok else "ZONE_KAPAT_BAŞARISIZ",
                                         f"SHORT kapatıldı (yanlış bölge, short devre dışı)", scan_r.price)
                held = self._position_mgr.get_all_positions()
                held_m = {s: p for s, p in held.items() if p.entry_mode == "SYSTEM_N"}

        # ── Yön limitleri ──
        max_same_dir = pos_cfg.get("max_same_direction", 8)
        dir_balance_on = pos_cfg.get("direction_balance_enabled", False)
        dir_ratio_str = pos_cfg.get("direction_balance_ratio", "2-1")

        def _count_directions() -> tuple[int, int]:
            """Held System N pozisyonlarındaki LONG ve SHORT sayısı."""
            longs = sum(1 for p in held_m.values() if p.side == OrderSide.BUY_LONG)
            shorts = sum(1 for p in held_m.values() if p.side == OrderSide.SELL_SHORT)
            return longs, shorts

        def _direction_allowed(direction: str) -> tuple[bool, str]:
            """Yön limitlerine göre yeni pozisyona izin var mı."""
            longs, shorts = _count_directions()
            count = longs if direction == "LONG" else shorts
            if count >= max_same_dir:
                return False, f"max aynı yön ({count}/{max_same_dir})"
            if dir_balance_on:
                try:
                    parts = dir_ratio_str.split("-")
                    majority_r, minority_r = int(parts[0]), int(parts[1])
                except (ValueError, IndexError):
                    majority_r, minority_r = 2, 1
                majority = max(longs, shorts)
                minority = min(longs, shorts)
                new_majority = (longs + 1 if direction == "LONG" and longs >= shorts
                                else shorts + 1 if direction == "SHORT" and shorts >= longs
                                else majority)
                limit = majority_r * (minority // minority_r + 1)
                if new_majority > limit:
                    return False, f"yön dengesi aşıldı ({longs}L/{shorts}S, oran {dir_ratio_str})"
            return True, ""

        # ── Loss protection: yeni pozisyon açma kontrolü ──
        def _can_open_new(sym: str, signal_type: str) -> tuple[bool, str]:
            """Cooldown + ban + eski sinyal kontrolü. True=açılabilir."""
            # 1. Loss cooldown
            if sn_opt.get("loss_cooldown_enabled", False):
                cooldown_s = sn_opt.get("loss_cooldown_seconds", 600)
                if sym in self._loss_cooldown_symbols:
                    elapsed = now - self._loss_cooldown_symbols[sym]
                    remaining = cooldown_s - elapsed
                    if remaining > 0:
                        return False, f"loss cooldown ({remaining:.0f}s kaldi)"
                    # Cooldown yeni bitti — sinyal cooldown döneminde mi üretildi?
                    # Scan başlangıcı cooldown bitiş zamanından SONRA olmalı
                    cooldown_end = self._loss_cooldown_symbols[sym] + cooldown_s
                    if self._system_n_scan_start_time < cooldown_end:
                        return False, f"eski sinyal (cooldown sirasinda uretildi, yeni scan bekleniyor)"

            # 2. Coin daily ban
            if sn_opt.get("coin_ban_enabled", False):
                ban_ok, ban_reason = self._check_coin_daily_ban_system_n(sym)
                if not ban_ok:
                    return False, ban_reason

            return True, ""

        # ── SELL sinyallerini işle (önce çıkış) ──
        for sig in sell_signals:
            sym = sig.symbol
            pos = held_m.get(sym)

            if not pos:
                # Yeni SHORT açma — cooldown + ban kontrolü
                if short_enabled:
                    can_open, block_reason = _can_open_new(sym, "SELL")
                    if not can_open:
                        self._log_n_decision(sym, "SELL", "ATLA", block_reason, sig.price)
                        continue
                    current_count = len(held_m)
                    if current_count >= max_pos:
                        self._log_n_decision(sym, "SELL", "ATLA",
                                             f"max pozisyon dolu ({current_count}/{max_pos})", sig.price)
                    else:
                        allowed, reason = _direction_allowed("SHORT")
                        if not allowed:
                            self._log_n_decision(sym, "SELL", "ATLA", reason, sig.price)
                        else:
                            ok = self._do_open_system_n(sig, "SHORT")
                            self._log_n_decision(sym, "SELL", "SHORT_AÇ" if ok else "SHORT_BAŞARISIZ",
                                                 f"poz yok, short açılıyor", sig.price)
                            held = self._position_mgr.get_all_positions()
                            held_m = {s: p for s, p in held.items()
                                      if p.entry_mode == "SYSTEM_N"}
                else:
                    self._log_n_decision(sym, "SELL", "ATLA",
                                         "short kapalı, pozisyon yok", sig.price)
                continue

            if pos.side == OrderSide.BUY_LONG:
                # Çıkış/reverse — close HER ZAMAN serbest
                if reverse_enabled and short_enabled:
                    # Reverse kontrolü: ardışık reverse limiti
                    rev_ok, rev_reason = self._check_reverse_allowed_system_n(sym)
                    if not rev_ok:
                        # Reverse engellendi → sadece close yap
                        logger.info(f"[SysN] {sym}: {rev_reason} → reverse yerine close")
                        ok = self._do_close_system_n(sym, pos, "SELL_SIGNAL_REV_LIMIT")
                        self._log_n_decision(sym, "SELL", "KAPAT" if ok else "KAPAT_BAŞARISIZ",
                                             f"reverse limiti: {rev_reason}", sig.price)
                    else:
                        ok = self._do_reverse_system_n(sym, pos, sig, "SHORT")
                        self._log_n_decision(sym, "SELL", "REVERSE→SHORT" if ok else "REVERSE_BAŞARISIZ",
                                             "LONG→SHORT çeviriliyor", sig.price)
                else:
                    ok = self._do_close_system_n(sym, pos, "SELL_SIGNAL")
                    self._log_n_decision(sym, "SELL", "KAPAT" if ok else "KAPAT_BAŞARISIZ",
                                         "LONG pozisyon kapatılıyor", sig.price)
                held = self._position_mgr.get_all_positions()
                held_m = {s: p for s, p in held.items()
                          if p.entry_mode == "SYSTEM_N"}

        # ── BUY sinyallerini işle ──
        for sig in buy_signals:
            sym = sig.symbol
            pos = held_m.get(sym)

            if not pos:
                # Yeni LONG açma — cooldown + ban kontrolü
                can_open, block_reason = _can_open_new(sym, "BUY")
                if not can_open:
                    self._log_n_decision(sym, "BUY", "ATLA", block_reason, sig.price)
                    continue
                current_count = len(held_m)
                if current_count >= max_pos:
                    self._log_n_decision(sym, "BUY", "ATLA",
                                         f"max pozisyon dolu ({current_count}/{max_pos})", sig.price)
                    continue
                allowed, reason = _direction_allowed("LONG")
                if not allowed:
                    self._log_n_decision(sym, "BUY", "ATLA", reason, sig.price)
                    continue
                ok = self._do_open_system_n(sig, "LONG")
                self._log_n_decision(sym, "BUY", "LONG_AÇ" if ok else "LONG_BAŞARISIZ",
                                     "yeni LONG açılıyor", sig.price)
                held = self._position_mgr.get_all_positions()
                held_m = {s: p for s, p in held.items()
                          if p.entry_mode == "SYSTEM_N"}
                continue

            if pos.side == OrderSide.SELL_SHORT:
                # Çıkış/reverse — close HER ZAMAN serbest
                if reverse_enabled and short_enabled:
                    # Reverse kontrolü: ardışık reverse limiti
                    rev_ok, rev_reason = self._check_reverse_allowed_system_n(sym)
                    if not rev_ok:
                        logger.info(f"[SysN] {sym}: {rev_reason} → reverse yerine close")
                        ok = self._do_close_system_n(sym, pos, "BUY_SIGNAL_REV_LIMIT")
                        self._log_n_decision(sym, "BUY", "KAPAT" if ok else "KAPAT_BAŞARISIZ",
                                             f"reverse limiti: {rev_reason}", sig.price)
                    else:
                        ok = self._do_reverse_system_n(sym, pos, sig, "LONG")
                        self._log_n_decision(sym, "BUY", "REVERSE→LONG" if ok else "REVERSE_BAŞARISIZ",
                                             "SHORT→LONG çeviriliyor", sig.price)
                elif short_enabled:
                    ok = self._do_close_system_n(sym, pos, "BUY_SIGNAL")
                    self._log_n_decision(sym, "BUY", "KAPAT" if ok else "KAPAT_BAŞARISIZ",
                                         "SHORT pozisyon kapatılıyor", sig.price)
                else:
                    # short_enabled=False ama SHORT pozisyon var (config değişti/sync)
                    # Close HER ZAMAN serbest — pozisyonu yetim bırakma
                    ok = self._do_close_system_n(sym, pos, "BUY_SIGNAL_SHORT_DISABLED")
                    self._log_n_decision(sym, "BUY", "KAPAT" if ok else "KAPAT_BAŞARISIZ",
                                         "SHORT kapatılıyor (short devre dışı)", sig.price)
                held = self._position_mgr.get_all_positions()
                held_m = {s: p for s, p in held.items()
                          if p.entry_mode == "SYSTEM_N"}

    def _do_open_system_n(self, sig, direction: str) -> bool:
        """System N pozisyon aç — G-bazlı dinamik kaldıraç."""
        sm_cfg = self._config.get("system_n", {})
        pos_cfg = sm_cfg.get("position", {})
        symbol = sig.symbol
        side = OrderSide.BUY_LONG if direction == "LONG" else OrderSide.SELL_SHORT
        order_side = "BUY" if direction == "LONG" else "SELL"

        price = sig.price
        try:
            ticker = self._rest.get_ticker_price(symbol)
            price = float(ticker.get("price", price))
        except Exception:
            pass

        # Spot modda short açılamaz — erken çıkış
        trading_mode = sm_cfg.get("trading_mode", "spot")
        if trading_mode == "spot" and direction == "SHORT":
            logger.debug(f"[SysN] {symbol}: spot modda SHORT açılamaz, skip")
            return False

        # Başka sistemin pozisyonu varsa açma (SL/TP emirleri silinir)
        other_pos = self._position_mgr.get_position(symbol)
        if other_pos and other_pos.entry_mode != "SYSTEM_N":
            logger.warning(f"[SysN] {symbol}: başka sistem pozisyonu var "
                           f"({other_pos.entry_mode}), açma atlanıyor")
            return False

        # G-bazlı dinamik kaldıraç (optimize cache'den)
        if trading_mode == "spot":
            leverage = 1
        else:
            scanner = self._system_n_scanner
            coin_params = scanner.get_coin_params(symbol) if scanner else {}
            g_leverage = coin_params.get("max_leverage", 1)
            max_lev = sm_cfg.get("max_leverage", 125)
            leverage = max(1, min(g_leverage, max_lev))
            logger.info(f"[SysN] {symbol}: G-bazlı kaldıraç={leverage}x "
                        f"(G={coin_params.get('G', 0):.3f}%, "
                        f"TF={coin_params.get('tf', '?')}, "
                        f"WR={coin_params.get('wr', 0):.0f}%)")

        # Orphan emirleri temizle (bu sembolde sadece System N pozisyonu olabilir)
        try:
            self._rest.cancel_all_orders(symbol)
        except Exception:
            pass

        # HER ZAMAN Binance'te leverage set et (onceki ayar farkli olabilir)
        try:
            self._rest.set_margin_type(symbol, "ISOLATED")
        except Exception as e:
            if "-4046" not in str(e):
                logger.warning(f"[SysN] {symbol} set_margin_type failed: {e}")
        try:
            self._rest.set_leverage(symbol, max(leverage, 1))
        except Exception as e:
            logger.warning(f"[SysN] {symbol} set_leverage({leverage}) failed: {e}")

        # Position sizing
        real_balance = 0.0
        if self._order_executor and hasattr(self._order_executor, "get_balance"):
            real_balance = self._order_executor.get_balance()

        # Erken bakiye kontrolü: Binance min notional 5 USDT + buffer
        notional_buffer_pct = pos_cfg.get("min_notional_buffer_pct", 20)
        min_notional = 5.0 * (1 + notional_buffer_pct / 100.0)
        min_required = min_notional / max(leverage, 1)
        if real_balance < min_required:
            logger.debug(f"[SysN] {symbol}: bakiye yetersiz "
                         f"(available={real_balance:.2f} < min_required={min_required:.2f} "
                         f"[notional={min_notional:.1f}]), skip")
            return False

        locked_margin = self._position_mgr.get_total_locked_margin()
        wallet = real_balance + locked_margin
        if wallet <= 0:
            wallet = self._config.get("risk.initial_balance", 4.0)

        # Coin bazlı min notional oku (Binance exchangeInfo)
        coin_min_notional = 5.0
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si and hasattr(si, 'min_notional') and si.min_notional > 0:
                    coin_min_notional = si.min_notional
            except Exception:
                pass

        scanner = self._system_n_scanner
        margin_usdt = scanner.calculate_position_size(
            wallet, leverage, coin_min_notional, available_balance=real_balance)
        margin_usdt = min(margin_usdt, real_balance * 0.90) if real_balance > 0 else margin_usdt
        min_pos_usd = pos_cfg.get("min_position_usd", 1.0)
        if margin_usdt < min_pos_usd:
            logger.info(f"[SysN] {symbol}: margin {margin_usdt:.2f} < {min_pos_usd}, skip "
                        f"(available={real_balance:.2f}, wallet={wallet:.2f})")
            return False

        # Coin bazlı min notional (Binance exchangeInfo'dan)
        coin_min_notional = 5.0
        pp, qp = 2, 3
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    pp = si.price_precision
                    qp = si.quantity_precision
                    if hasattr(si, 'min_notional') and si.min_notional > 0:
                        coin_min_notional = si.min_notional
            except Exception:
                pass

        # Buffer ekle (%20 varsayılan)
        buffer_pct = pos_cfg.get("min_notional_buffer_pct", 20)
        effective_min_notional = coin_min_notional * (1 + buffer_pct / 100.0)

        # Qty
        qty = margin_usdt * leverage / price if price > 0 else 0
        if qty <= 0:
            return False

        qty = round(qty, qp)
        if qty <= 0:
            return False

        # Min notional kontrolü: coin bazlı Binance minimum + buffer
        notional = qty * price
        if notional < effective_min_notional:
            qty = round((effective_min_notional * 1.02) / price, qp)
            notional = qty * price
            margin_usdt = notional / leverage
            logger.info(f"[SysN] {symbol}: notional {notional:.2f}$ adjusted "
                        f"(coin min_notional={coin_min_notional}$, "
                        f"effective={effective_min_notional:.2f}$)")

        # Son kontrol: gerçek bakiyeyi aşma
        required_margin = notional / max(leverage, 1)
        if required_margin > real_balance * 0.90:
            logger.info(f"[SysN] {symbol}: margin yetersiz "
                        f"(gerekli={required_margin:.2f} > available={real_balance * 0.90:.2f}), skip")
            return False

        # Market order
        try:
            result = self._rest.place_order(
                symbol=symbol, side=order_side,
                order_type="MARKET",
                quantity=qty,
            )
            fill_price = float(result.get("avgPrice", price))
            logger.info(f"[SysN MARKET] {symbol} {direction} @ {fill_price} "
                        f"qty={qty} lev={leverage}x")

            pos = self._position_mgr.open_position(
                symbol=symbol, side=side, price=fill_price,
                size=qty, atr=sig.atr, leverage=leverage,
                margin_usdt=margin_usdt,
                timeframe=sm_cfg.get("timeframe", "5m"),
                entry_score=0, entry_mode="SYSTEM_N",
                initial_sl_override=0, initial_tp_override=0,
            )

            if not pos:
                logger.error(f"[SysN] Failed to open position for {symbol}")
                return False

            # Opsiyonel SL yerleştir
            self._place_sl_system_n(symbol, pos, sig, leverage, pp)

            # Hemen state kaydet (crash koruması)
            try:
                self._position_mgr.save_state()
            except Exception:
                pass

            if self._order_logger:
                self._order_logger.log_order(
                    symbol=symbol, side=order_side, order_type="MARKET",
                    price=fill_price, size=qty,
                    notional_usdt=qty * fill_price,
                    status="filled",
                    trigger_source=f"system_n:{direction}",
                )
            return True

        except Exception as e:
            logger.error(f"[SysN MARKET] {symbol} {direction}: FAILED: {e} "
                         f"(qty={qty}, notional={qty * price:.2f}, "
                         f"available={real_balance:.2f})")
            self._failed_symbols[symbol] = time.time()
            return False

    def _place_sl_system_n(self, symbol: str, pos, sig, leverage: int,
                            price_precision: int = 2) -> None:
        """System N opsiyonel SL yerleştirme (config: system_n.sl).

        Modlar:
            g_based:   G × sl_g_mult + fee → SL%
            atr_based: ATR × sl_atr_mult / fiyat → SL%
            fixed_pct: sabit yüzde
        """
        sl_cfg = self._config.get("system_n.sl", {})
        if not sl_cfg.get("enabled", False):
            return

        mode = sl_cfg.get("mode", "g_based")
        entry_price = pos.entry_price
        if entry_price <= 0:
            return

        sl_pct = 0.0

        if mode == "g_based":
            scanner = self._system_n_scanner
            coin_params = scanner.get_coin_params(symbol) if scanner else {}
            cached_sl = coin_params.get("sl_pct", 0)
            if cached_sl > 0:
                sl_pct = cached_sl
            else:
                G = coin_params.get("G", 0)
                g_mult = sl_cfg.get("g_mult", 1.5)
                fee = sl_cfg.get("fee_total_pct", 0.12)
                sl_pct = G * g_mult + fee
        elif mode == "atr_based":
            atr_mult = sl_cfg.get("atr_mult", 2.0)
            atr = getattr(sig, "atr", 0) or 0
            if atr > 0:
                sl_pct = (atr / entry_price * 100) * atr_mult
        elif mode == "fixed_pct":
            sl_pct = sl_cfg.get("fixed_pct", 5.0)

        if sl_pct <= 0:
            logger.warning(f"[SysN SL] {symbol}: SL hesaplanamadı (mod={mode}, sl_pct=0)")
            return

        # SL fiyat hesapla
        pp = price_precision
        is_long = pos.side == OrderSide.BUY_LONG
        if is_long:
            sl_price = round(entry_price * (1 - sl_pct / 100), pp)
        else:
            sl_price = round(entry_price * (1 + sl_pct / 100), pp)

        if sl_price <= 0:
            return

        # Server-side SL gönder
        if sl_cfg.get("server_side", True):
            close_side = "SELL" if is_long else "BUY"
            try:
                self._rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="STOP_MARKET",
                    quantity=pos.size,
                    stop_price=sl_price,
                    reduce_only=True,
                )
                logger.info(f"[SysN SL] {symbol}: {mode} SL @ {sl_price} "
                            f"(SL%={sl_pct:.3f}%, lev={leverage}x, "
                            f"ROI%={sl_pct * leverage:.1f}%)")
            except Exception as e:
                logger.error(f"[SysN SL] {symbol}: SL placement FAILED: {e}")

        # Position'a SL kaydet (software yedek için)
        self._position_mgr.update_stop_loss(symbol, sl_price)

    def _do_close_system_n(self, symbol: str, pos, reason: str) -> bool:
        """System N pozisyon kapat."""
        close_side = "SELL" if pos.side == OrderSide.BUY_LONG else "BUY"
        try:
            try:
                self._rest.cancel_all_orders(symbol)
            except Exception:
                pass

            result = self._rest.place_order(
                symbol=symbol, side=close_side,
                order_type="MARKET",
                quantity=pos.size,
                reduce_only=True,
            )
            exit_price = float(result.get("avgPrice", pos.entry_price))
            logger.info(f"[SysN CLOSE] {symbol} {reason} @ {exit_price}")

            trade = self._position_mgr.close_position(symbol, exit_price, reason)

            # ── Loss protection: zarar kaydı ──
            if trade:
                pnl_usdt = trade.get("pnl_usdt", 0)
                if pnl_usdt < 0:
                    sn_opt = self._config.get("system_n", {}).get("optional_features", {})
                    if sn_opt.get("loss_cooldown_enabled", False):
                        cooldown_s = sn_opt.get("loss_cooldown_seconds", 600)
                        self._loss_cooldown_symbols[symbol] = time.time()
                        logger.info(f"[SysN COOLDOWN] {symbol}: {cooldown_s}s re-entry yasagi "
                                    f"(PnL={pnl_usdt:+.4f} USDT, reason={reason})")
                    if sn_opt.get("coin_ban_enabled", False):
                        self._record_coin_loss(symbol)
                        logger.info(f"[SysN BAN KAYDI] {symbol}: ban sayaci artti "
                                    f"(PnL={pnl_usdt:+.4f} USDT)")

            # Hemen state kaydet (crash koruması)
            try:
                self._position_mgr.save_state()
            except Exception:
                pass

            if self._order_logger and trade:
                from datetime import datetime as dt
                entry_t = trade.get("entry_time", 0)
                open_time = dt.fromtimestamp(entry_t).isoformat() if entry_t else ""
                fee_pct = self._config.get("strategy.fee_pct", 0.10) / 100.0
                fee_usdt = trade.get("notional_usdt", 0) * fee_pct
                self._order_logger.log_trade(
                    open_time=open_time,
                    close_time=dt.now().isoformat(),
                    symbol=symbol,
                    side=trade.get("side", ""),
                    leverage=trade.get("leverage", 1),
                    margin_usdt=trade.get("margin_usdt", 0),
                    notional_usdt=trade.get("notional_usdt", 0),
                    entry_price=trade.get("entry_price", 0),
                    exit_price=exit_price,
                    size=trade.get("size", 0),
                    pnl_usdt=trade.get("pnl_usdt", 0),
                    pnl_percent=trade.get("pnl_percent", 0),
                    roi_percent=trade.get("roi_percent", 0),
                    fee_usdt=fee_usdt,
                    exit_reason=reason,
                    hold_seconds=trade.get("hold_seconds", 0),
                    highest_price=trade.get("highest_price", 0),
                    lowest_price=trade.get("lowest_price", 0),
                    initial_sl=trade.get("initial_sl", 0),
                    initial_tp=trade.get("initial_tp", 0),
                    atr_at_entry=trade.get("atr_at_entry", 0),
                    timeframe=trade.get("timeframe", ""),
                    entry_score=trade.get("entry_score", 0),
                    entry_confluence=trade.get("entry_confluence", 0),
                    entry_adx=trade.get("entry_adx", 0),
                    entry_rsi=trade.get("entry_rsi", 0),
                    entry_regime=trade.get("entry_regime", ""),
                    entry_regime_confidence=trade.get("entry_regime_confidence", 0),
                    entry_bb_width=trade.get("entry_bb_width", 0),
                )
            return True
        except Exception as e:
            logger.error(f"[SysN CLOSE] {symbol}: FAILED: {e}")
            return False

    def _do_reverse_system_n(self, symbol: str, pos, sig,
                              new_direction: str) -> bool:
        """System N reverse: mevcut pozisyonu kapat + ters yönde aç (2 ayrı emir)."""
        sm_cfg = self._config.get("system_n", {})
        pos_cfg = sm_cfg.get("position", {})
        close_side = "SELL" if pos.side == OrderSide.BUY_LONG else "BUY"
        new_side = OrderSide.BUY_LONG if new_direction == "LONG" else OrderSide.SELL_SHORT

        real_balance = 0.0
        if self._order_executor and hasattr(self._order_executor, "get_balance"):
            real_balance = self._order_executor.get_balance()
        locked_margin = self._position_mgr.get_total_locked_margin()
        wallet = real_balance + locked_margin
        if wallet <= 0:
            wallet = self._config.get("risk.initial_balance", 4.0)

        # Güncel fiyatı al (close öncesi — reverse timing gap minimize)
        price = sig.price
        try:
            ticker = self._rest.get_ticker_price(symbol)
            price = float(ticker.get("price", price))
        except Exception:
            pass

        # G-bazlı dinamik kaldıraç (_do_open_system_n ile tutarlı)
        scanner = self._system_n_scanner
        trading_mode = sm_cfg.get("trading_mode", "spot")
        if trading_mode == "spot":
            leverage = 1
        else:
            coin_params = scanner.get_coin_params(symbol) if scanner else {}
            g_leverage = coin_params.get("max_leverage", 1)
            max_lev = sm_cfg.get("max_leverage", 125)
            leverage = max(1, min(g_leverage, max_lev))
            logger.info(f"[SysN REVERSE] {symbol}: G-bazlı kaldıraç={leverage}x "
                        f"(G={coin_params.get('G', 0):.3f}%)")

        # Reverse sizing mode: "full" = eski pozisyon miktarı, "fresh" = portföy kurallarıyla yeniden hesapla
        reverse_sizing = sm_cfg.get("reverse_sizing", "fresh")
        if reverse_sizing == "full":
            # Eski pozisyonun aynı notional'ı ile ters aç
            new_qty = pos.size
            new_margin = pos.margin_usdt
        else:
            # Portföy kurallarına göre yeniden hesapla (1/12, min_notional, hybrid)
            coin_min_notional = 5.0
            if self._symbol_info_cache:
                try:
                    si = self._symbol_info_cache.get(symbol)
                    if si and hasattr(si, 'min_notional') and si.min_notional > 0:
                        coin_min_notional = si.min_notional
                except Exception:
                    pass
            new_margin = scanner.calculate_position_size(wallet, leverage, coin_min_notional)
            new_margin = min(new_margin, real_balance * 0.90) if real_balance > 0 else new_margin
            new_qty = new_margin * leverage / price if price > 0 else 0

        qp = 3
        if self._symbol_info_cache:
            try:
                si = self._symbol_info_cache.get(symbol)
                if si:
                    qp = si.quantity_precision
            except Exception:
                pass

        new_qty = round(new_qty, qp)
        if new_qty <= 0:
            return self._do_close_system_n(symbol, pos, f"REVERSE_{new_direction}_FAIL")

        # Min notional kontrolü: Binance 5 USDT + buffer%
        notional_buffer_pct = pos_cfg.get("min_notional_buffer_pct", 20)
        min_notional = 5.0 * (1 + notional_buffer_pct / 100.0)
        new_notional = new_qty * price
        if new_notional < min_notional:
            new_qty = round((min_notional * 1.02) / price, qp)
            new_notional = new_qty * price

        # Reverse'te mevcut pos kapanıp margin serbest kalır ama yine de kontrol
        # PnL-aware: isolated margin'de serbest kalan = margin + unrealized_pnl
        if pos.entry_price > 0 and price > 0:
            if pos.side == OrderSide.BUY_LONG:
                _pnl_ratio = (price - pos.entry_price) / pos.entry_price
            else:
                _pnl_ratio = (pos.entry_price - price) / pos.entry_price
            _unrealized = pos.margin_usdt * pos.leverage * _pnl_ratio
            freed_margin = max(pos.margin_usdt + _unrealized, 0)
        else:
            freed_margin = pos.margin_usdt
        available_after = real_balance + freed_margin
        required_margin = new_notional / max(leverage, 1)
        if required_margin > available_after * 0.90:
            logger.warning(f"[SysN REVERSE] {symbol}: margin yetersiz "
                           f"(gerekli={required_margin:.2f}, "
                           f"available={real_balance:.2f}+freed={freed_margin:.2f}="
                           f"{available_after:.2f}), sadece close yapılıyor")
            return self._do_close_system_n(symbol, pos, f"REVERSE_{new_direction}_MARGIN")

        try:
            # 1. Önce mevcut emirleri iptal et
            try:
                self._rest.cancel_all_orders(symbol)
            except Exception:
                pass

            # 2. Eski pozisyonu kapat (leverage değişmeden)
            close_result = self._rest.place_order(
                symbol=symbol, side=close_side,
                order_type="MARKET",
                quantity=pos.size,
                reduce_only=True,
            )
            fill_price = float(close_result.get("avgPrice", price))
            logger.info(f"[SysN REVERSE] {symbol}: "
                        f"{'LONG→SHORT' if new_direction == 'SHORT' else 'SHORT→LONG'} "
                        f"CLOSE @ {fill_price} qty={pos.size}")

            trade = self._position_mgr.close_position(symbol, fill_price,
                                                       f"REVERSE_{new_direction}")

            # ── Loss protection: reverse zarar kaydı ──
            if trade:
                pnl_usdt = trade.get("pnl_usdt", 0)
                if pnl_usdt < 0:
                    sn_opt = self._config.get("system_n", {}).get("optional_features", {})
                    if sn_opt.get("loss_cooldown_enabled", False):
                        self._loss_cooldown_symbols[symbol] = time.time()
                        logger.info(f"[SysN REVERSE COOLDOWN] {symbol}: cooldown set "
                                    f"(PnL={pnl_usdt:+.4f} USDT)")
                    if sn_opt.get("coin_ban_enabled", False):
                        self._record_coin_loss(symbol)
                        logger.info(f"[SysN REVERSE BAN] {symbol}: ban sayaci artti")
            # Ardışık reverse kaydı (PnL fark etmez)
            self._record_reverse_system_n(symbol)

            if self._order_logger and trade:
                from datetime import datetime as dt
                entry_t = trade.get("entry_time", 0)
                open_time = dt.fromtimestamp(entry_t).isoformat() if entry_t else ""
                fee_pct = self._config.get("strategy.fee_pct", 0.10) / 100.0
                fee_usdt = trade.get("notional_usdt", 0) * fee_pct
                self._order_logger.log_trade(
                    open_time=open_time,
                    close_time=dt.now().isoformat(),
                    symbol=symbol,
                    side=trade.get("side", ""),
                    leverage=trade.get("leverage", 1),
                    margin_usdt=trade.get("margin_usdt", 0),
                    notional_usdt=trade.get("notional_usdt", 0),
                    entry_price=trade.get("entry_price", 0),
                    exit_price=fill_price,
                    size=trade.get("size", 0),
                    pnl_usdt=trade.get("pnl_usdt", 0),
                    pnl_percent=trade.get("pnl_percent", 0),
                    roi_percent=trade.get("roi_percent", 0),
                    fee_usdt=fee_usdt,
                    exit_reason=f"REVERSE_{new_direction}",
                    hold_seconds=trade.get("hold_seconds", 0),
                    highest_price=trade.get("highest_price", 0),
                    lowest_price=trade.get("lowest_price", 0),
                    initial_sl=trade.get("initial_sl", 0),
                    initial_tp=trade.get("initial_tp", 0),
                    atr_at_entry=trade.get("atr_at_entry", 0),
                    timeframe=trade.get("timeframe", ""),
                    entry_score=trade.get("entry_score", 0),
                    entry_confluence=trade.get("entry_confluence", 0),
                    entry_adx=trade.get("entry_adx", 0),
                    entry_rsi=trade.get("entry_rsi", 0),
                    entry_regime=trade.get("entry_regime", ""),
                    entry_regime_confidence=trade.get("entry_regime_confidence", 0),
                    entry_bb_width=trade.get("entry_bb_width", 0),
                )

            # 3. Leverage'ı güncelle + hemen yeni yönde aç (gap minimize)
            try:
                self._rest.set_leverage(symbol, max(leverage, 1))
            except Exception as e:
                logger.warning(f"[SysN REVERSE] {symbol} set_leverage({leverage}) failed: {e}")

            # 4. Yeni yönde pozisyon aç — ek ticker çekme yok (timing gap minimize)
            new_order_side = "BUY" if new_direction == "LONG" else "SELL"
            open_price = fill_price  # close fill'den devam — ek API gecikme yok

            open_result = self._rest.place_order(
                symbol=symbol, side=new_order_side,
                order_type="MARKET",
                quantity=new_qty,
            )
            new_fill_price = float(open_result.get("avgPrice", open_price))
            logger.info(f"[SysN REVERSE] {symbol}: OPEN {new_direction} "
                        f"@ {new_fill_price} qty={new_qty} lev={leverage}x")

            new_pos = self._position_mgr.open_position(
                symbol=symbol, side=new_side, price=new_fill_price,
                size=new_qty, atr=sig.atr, leverage=leverage,
                margin_usdt=new_margin,
                timeframe=sm_cfg.get("timeframe", "5m"),
                entry_score=0, entry_mode="SYSTEM_N",
                initial_sl_override=0, initial_tp_override=0,
            )

            if not new_pos:
                logger.error(f"[SysN REVERSE] {symbol}: new pos failed")
            else:
                # Opsiyonel SL yerleştir
                pp = 2
                if self._symbol_info_cache:
                    try:
                        si = self._symbol_info_cache.get(symbol)
                        if si:
                            pp = si.price_precision
                    except Exception:
                        pass
                self._place_sl_system_n(symbol, new_pos, sig, leverage, pp)

            # Hemen state kaydet (crash koruması)
            try:
                self._position_mgr.save_state()
            except Exception:
                pass

            return True

        except Exception as e:
            logger.error(f"[SysN REVERSE] {symbol}: FAILED: {e} "
                         f"(new_qty={new_qty}, "
                         f"available={real_balance:.2f})")
            return self._do_close_system_n(symbol, pos, f"REVERSE_{new_direction}_ERR")

    def _check_held_positions_system_n(self) -> None:
        """System N pozisyon kontrolü — sadece external close tespiti.
        Sinyal bazlı çıkış _do_trading_system_n'de yapılır."""
        self._detect_external_closes()
