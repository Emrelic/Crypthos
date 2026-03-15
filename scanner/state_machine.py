"""Scanner State Machine - orchestrates the SCANNING->BUYING->SELLING cycle.
Single thread, sequential state transitions.
Supports up to max_positions concurrent positions."""
import re
import time
import threading
from loguru import logger
from core.config_manager import ConfigManager
from core.event_bus import EventBus
from core.constants import (EventType, ScannerState, OrderSide, OrderType)
from market.binance_rest import BinanceRestClient
from scanner.symbol_universe import SymbolUniverse
from scanner.batch_fetcher import BatchKlineFetcher
from scanner.scanner_scorer import ScannerScorer, ScanResult
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
        self._position_mgr = PositionManager(config, event_bus)
        self._tf_selector = TimeframeSelector(rest_client, config=config)
        self._ob_analyzer = OrderBookAnalyzer()
        self._btc_corr = BTCCorrelationEngine(rest_client, config)

        # Holding-phase analysis (separate engine for held symbols)
        self._hold_engine = IndicatorEngine(config)
        self._hold_confluence = ConfluenceScorer(threshold=4.0)
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
        while self._running:
            try:
                if self._state == ScannerState.SCANNING:
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
                    time.sleep(1)
            except Exception as e:
                logger.error(f"Scanner error in {self._state.value}: {e}")
                time.sleep(5)

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
            time.sleep(10)
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

        self._last_scan_results = results

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

        eligible = [r for r in results if r.eligible]
        min_score = self._config.get("strategy.min_buy_score", 65)

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

        # Publish scan results for GUI
        self._event_bus.publish(EventType.SCANNER_UPDATE, {
            "scan_count": self._scan_count,
            "total_symbols": len(symbols),
            "scored": len(results),
            "eligible": len(eligible),
            "positions": self._position_mgr.position_count,
            "max_positions": self._position_mgr.max_positions,
            "top_5": [
                {"symbol": r.symbol, "score": r.score, "direction": r.direction,
                 "regime": r.regime.get("regime", "?"),
                 "confluence": r.confluence.get("score", 0)}
                for r in results[:5]
            ],
            "candidate": candidates[0].symbol if candidates else None,
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

                cand_tf = symbol_intervals.get(candidate.symbol, default_interval) if dynamic_tf else default_interval
                logger.info(f"Candidate found: {candidate.symbol} "
                            f"score={candidate.score:+.1f} dir={candidate.direction} "
                            f"regime={candidate.regime.get('regime')} "
                            f"confluence={candidate.confluence.get('score', 0):+.1f} "
                            f"tf={cand_tf}")
                self._last_candidate = candidate
                if self._do_buying_inline():
                    bought_any = True
                else:
                    continue  # try next candidate (might be coin-specific issue)

            # Short wait then scan again if still have capacity
            if self._position_mgr.has_capacity and bought_any:
                self._wait(5)
            else:
                scan_interval = self._config.get("strategy.scan_interval_seconds", 30)
                self._wait(scan_interval)
        else:
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

            # Server trailing dynamic update (config flag ile kontrol)
            if self._config.get("strategy", {}).get("server_trailing_dynamic_update", False):
                pos_now = self._position_mgr.get_position(symbol)
                if pos_now:
                    self._sync_server_trailing(symbol, pos_now, current_price, confluence)

            if exit_reason == self._position_mgr.EXIT_PARTIAL_TP:
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
        Called after preliminary scoring so only top candidates get detailed data."""
        ticker_data = self._universe.get_all_tickers()
        for sym in symbols:
            if sym not in ctx:
                ctx[sym] = {"funding_rate": 0.0, "oi_change_pct": 0.0}

            # OI change = (latest OI - oldest OI) / oldest OI * 100
            try:
                oi_hist = self._rest.get_open_interest_hist(sym, period="5m", limit=6)
                if oi_hist and len(oi_hist) >= 2:
                    oldest_val = float(oi_hist[0].get("sumOpenInterestValue", 0))
                    latest_val = float(oi_hist[-1].get("sumOpenInterestValue", 0))
                    if oldest_val > 0:
                        oi_chg = ((latest_val - oldest_val) / oldest_val) * 100
                        ctx[sym]["oi_change_pct"] = round(oi_chg, 2)
            except Exception:
                pass

            # Order Book depth (20 levels each side)
            try:
                depth = self._rest.get_depth(sym, limit=20)
                vol_24h = ticker_data.get(sym, {}).get("volume_24h", 0)
                ob = self._ob_analyzer.analyze(depth, volume_24h=vol_24h)
                ctx[sym]["ob_imbalance"] = ob.get("weighted_imbalance", 0.0)
                ctx[sym]["ob_wall_signal"] = ob.get("wall_signal", "NONE")
                ctx[sym]["ob_liquidity"] = ob.get("liquidity_score", 0.0)
                ctx[sym]["ob_thin_book"] = ob.get("thin_book", False)
                # Wall strength in seconds (for timeframe-relative filtering)
                ask_wall = ob.get("ask_wall")
                bid_wall = ob.get("bid_wall")
                ctx[sym]["ob_wall_seconds"] = 0.0
                if ask_wall and ob.get("wall_signal") == "UP_BLOCKED":
                    ctx[sym]["ob_wall_seconds"] = ask_wall.get("wall_seconds", 9999.0)
                elif bid_wall and ob.get("wall_signal") == "DOWN_BLOCKED":
                    ctx[sym]["ob_wall_seconds"] = bid_wall.get("wall_seconds", 9999.0)
                # Total depth pressure in seconds
                ctx[sym]["ob_ask_depth_seconds"] = ob.get("ask_depth_seconds", 0.0)
                ctx[sym]["ob_bid_depth_seconds"] = ob.get("bid_depth_seconds", 0.0)
            except Exception:
                pass  # Order book is optional, don't block scan

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
        mtf_confluence = ConfluenceScorer(threshold=4.0)

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

    # ──── Pending Limit Order Management ────

    def _check_pending_limits(self) -> None:
        """Check all pending limit orders for fill or timeout.
        Called every scan cycle."""
        if not self._pending_limits:
            return

        now = time.time()
        filled = []
        expired = []

        for symbol, info in list(self._pending_limits.items()):
            elapsed = now - info["placed_time"]

            # Check if order is filled via API
            try:
                orders = self._rest.get_open_orders(symbol)
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

        regime_info = candidate.regime or {}
        self._position_mgr.open_position(
            symbol, info["side"], fill_price, info["size"], info["atr"],
            leverage=info["leverage"],
            margin_usdt=info["margin_usdt"],
            timeframe=pos_tf,
            entry_score=candidate.score,
            entry_confluence=candidate.confluence.get("score", 0),
            entry_adx=candidate.adx,
            entry_rsi=candidate.rsi,
            entry_regime=regime_info.get("regime", ""),
            entry_regime_confidence=regime_info.get("confidence", 0),
            entry_bb_width=regime_info.get("bb_width", 0),
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

        # Place initial server-side trailing stop
        if info["lev_enabled"] and self._order_executor and hasattr(self._order_executor, '_rest'):
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

        # Market fallback: if signal still valid, enter with market order
        strat = self._config.get("strategy", {})
        fallback_enabled = strat.get("market_fallback_on_limit_timeout", True)

        if fallback_enabled and self._order_executor:
            candidate = info.get("candidate")
            if candidate and candidate.eligible and abs(candidate.score) >= strat.get("min_buy_score", 70):
                logger.info(f"Limit expired but signal still strong ({candidate.score:+.1f}), "
                            f"falling back to MARKET order: {symbol}")
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
                        # Re-use the filled handler logic to open position tracking
                        # Put info back temporarily for _on_limit_filled to process
                        info["market_fallback"] = True
                        self._pending_limits[symbol] = info
                        self._on_limit_filled(symbol)

                        if self._order_logger:
                            self._order_logger.log_order(
                                symbol=symbol, side=info["side"].value,
                                order_type="Market",
                                price=candidate.price, size=info["size"],
                                notional_usdt=info["size"] * candidate.price,
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

        for symbol in list(self._position_mgr.get_held_symbols()):
            # Skip symbols currently being sold by monitor thread
            if symbol in self._selling_symbols:
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

            exit_reason = self._position_mgr.check_position(
                symbol, current_price,
                confluence=confluence,
                divergences=divergences,
            )

            # Server trailing dynamic update (config flag ile kontrol)
            if self._config.get("strategy", {}).get("server_trailing_dynamic_update", False):
                pos_now = self._position_mgr.get_position(symbol)
                if pos_now:
                    self._sync_server_trailing(symbol, pos_now, current_price, confluence)

            if exit_reason == self._position_mgr.EXIT_PARTIAL_TP:
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
            api_symbols = {p.get("symbol", "") for p in api_positions}

            # Find positions we think are open but Binance says closed
            closed_externally = held_symbols - api_symbols

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

                logger.warning(f"[EXTERNAL CLOSE] {symbol} kapatilmis "
                               f"(manuel/SL/TP/trailing) — dahili takipten siliniyor. "
                               f"Giris={pos.entry_price:.6f} Cikis={exit_price:.6f}")

                # Clean up internal state
                self._held_indicators.pop(symbol, None)
                self._server_trailing.pop(symbol, None)
                self._close_retries.pop(symbol, None)

                result = self._position_mgr.close_position(
                    symbol, exit_price, "external_close")
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
                        self._loss_cooldown_symbols[symbol] = time.time()
                        self._record_coin_loss(symbol)
                        logger.info(f"[LOSS COOLDOWN+BAN] {symbol}: {self._loss_cooldown_seconds}s re-entry yasagi "
                                    f"(PnL={pnl_usdt:+.4f} USDT, external_close)")

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
                        fee_pct = 0.001
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

        except Exception as e:
            logger.debug(f"External close detection error: {e}")

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
                fee_pct = 0.001  # 0.1% round-trip (maker+taker)
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
        if len(self._trade_timestamps) >= self._max_trades_per_hour:
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
                # Unrealized profits don't count until position is closed.
                # Example: 9$ free + 3 positions × 1$ margin = 12$ portfolio
                # When one closes at +1$ profit: 11$ free + 2×1$ = 13$
                divider = self._config.get("strategy.portfolio_divider", 0)
                if divider > 0:
                    locked_margin = self._position_mgr.get_total_margin()
                    wallet = real_balance + locked_margin
                    if wallet <= 0:
                        balance = self._config.get("risk.initial_balance", 5.0)
                        if self._risk_manager:
                            balance = self._risk_manager._current_balance
                        wallet = balance

                    if wallet < 12.0:
                        divider = max(1, int(wallet))
                    margin_usdt = round(wallet / divider, 2)
                    sizing_label = f"1/{divider} of {wallet:.2f}$"

                    logger.info(f"Portfolio: {wallet:.2f}$ "
                                f"(free={real_balance:.2f}$ + locked={locked_margin:.2f}$)")
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
                # Don't let min_qty blow up position size beyond 2x target
                if needed_margin > original_margin * 2.0:
                    logger.warning(f"{symbol} min qty {min_qty} needs "
                                   f"{needed_margin:.2f}$ margin (target was "
                                   f"{original_margin:.2f}$, >2x), skipping")
                    self._failed_symbols[symbol] = time.time()
                    return False
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
            # Fee ROI = round-trip %0.1 × leverage
            # Slippage tahmini = fee × 0.5
            # SL ROI = hedef_ROI - fee_ROI - slippage_ROI
            fee_pct = 0.001  # round-trip fee %0.1
            fee_roi = fee_pct * leverage * 100  # fee as % of margin
            slippage_roi = fee_roi * 0.5  # estimated slippage
            raw_sl_roi = liq_pct * sl_liq_pct * leverage * 100  # hedef toplam kayıp
            net_sl_roi = max(raw_sl_roi - fee_roi - slippage_roi, fee_roi)  # fee düşülmüş SL
            sl_price_pct = net_sl_roi / (leverage * 100)  # geri fiyat mesafesine çevir

            sl_roi = round(net_sl_roi, 1)

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
        self._position_mgr.open_position(
            symbol, side, price, size_qty, atr,
            leverage=leverage if lev_enabled else 1,
            margin_usdt=margin_usdt if lev_enabled else 0.0,
            timeframe=pos_tf,
            entry_score=candidate.score,
            entry_confluence=candidate.confluence.get("score", 0),
            entry_adx=candidate.adx,
            entry_rsi=candidate.rsi,
            entry_regime=regime_info.get("regime", ""),
            entry_regime_confidence=regime_info.get("confidence", 0),
            entry_bb_width=regime_info.get("bb_width", 0),
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
        if lev_enabled and self._order_executor and hasattr(self._order_executor, '_rest'):
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
        """Separate thread that checks positions every 1 second.
        Critical for anti-liquidation: detects emergency close level
        before Binance can liquidate."""
        check_interval = 1  # 1 second — anti-liquidation speed
        logger.info("Position monitor thread started")
        while self._running:
            try:
                if not self._position_mgr.has_position:
                    time.sleep(check_interval)
                    continue

                # Don't interfere while buying/selling on UI
                if self._state == ScannerState.BUYING:
                    time.sleep(1)
                    continue

                for symbol in list(self._position_mgr.get_held_symbols()):
                    # Skip symbols currently being sold by main thread
                    if symbol in self._selling_symbols:
                        continue

                    try:
                        ticker = self._rest.get_ticker_price(symbol)
                        current_price = float(ticker.get("price", 0))
                        if current_price <= 0:
                            continue
                    except Exception:
                        continue

                    exit_reason = self._position_mgr.check_position(
                        symbol, current_price)

                    if exit_reason == self._position_mgr.EXIT_PARTIAL_TP:
                        self._execute_partial_tp(symbol, current_price)
                        continue  # Don't full-close, position stays open

                    if exit_reason != "HOLD":
                        # Check if we're in backoff period for this symbol
                        retry_info = self._close_retries.get(symbol)
                        if retry_info and time.time() < retry_info.get("next_retry", 0):
                            continue  # Skip until backoff expires

                        # === HYBRID TRAILING: Re-evaluate before closing ===
                        if exit_reason == self._position_mgr.EXIT_TRAILING_RENEW:
                            should_renew = self._evaluate_trailing_renew(
                                symbol, current_price)
                            if should_renew:
                                continue  # Trailing renewed, don't close
                            else:
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

                time.sleep(check_interval)
            except Exception as e:
                logger.error(f"Position monitor error: {e}")
                import traceback
                logger.error(f"Position monitor traceback: {traceback.format_exc()}")
                time.sleep(5)

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
            if self._order_executor:
                try:
                    updated_pos = self._position_mgr.get_position(symbol)
                    if updated_pos:
                        strat = self._config.get("strategy", {})
                        tp_price = updated_pos.initial_tp if strat.get("tp_enabled", False) else None
                        # Renew: cancel all → SL + trailing yeniden koy
                        callback = self._calc_trailing_callback(updated_pos, current_price)
                        self._send_server_trailing(symbol, updated_pos, current_price, callback)
                        logger.info(f"[HYBRID] {symbol} Binance SL + trailing yeniden kondu: "
                                    f"yeni SL={updated_pos.initial_sl:.6f}")
                except Exception as e:
                    logger.error(f"[HYBRID] {symbol} SL güncelleme hatası: {e}")

            return True

        except Exception as e:
            logger.error(f"[HYBRID] {symbol} değerlendirme hatası: {e}")
            import traceback
            logger.error(f"[HYBRID] traceback: {traceback.format_exc()}")
            return False  # hata durumunda güvenli taraf: kapat

    # ──── API Position Sync ────

    def _sync_api_positions(self) -> None:
        """On startup, sync any existing API positions into the position manager.
        This way the program tracks positions that were opened before restart."""
        if not self._order_executor or not hasattr(self._order_executor, 'get_open_positions'):
            return

        try:
            api_positions = self._order_executor.get_open_positions()
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

                # Get ATR for this symbol (use strategy timeframe, enough data)
                atr = 0.0
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
                        logger.info(f"Sync ATR for {symbol}: {atr:.8f} "
                                    f"({atr/entry_price*100:.3f}%) tf={interval}")
                    else:
                        logger.warning(f"Not enough klines for {symbol} ATR "
                                       f"(got {len(klines) if klines else 0})")
                except Exception as e:
                    logger.warning(f"ATR calculation failed for sync {symbol}: {e}")

                # Open position in manager (will set SL/TP/trailing)
                self._position_mgr.open_position(
                    symbol, side, entry_price, size, atr,
                    leverage=leverage,
                    margin_usdt=margin,
                    entry_regime="SYNCED",
                )
                logger.info(f"Synced API position: {symbol} {side.value} "
                            f"qty={size} entry={entry_price} lev={leverage}x "
                            f"margin={margin:.2f}")

            logger.info(f"Synced {len(api_positions)} API position(s)")

            # Update TP/SL for synced positions with fee-aware values
            self._update_synced_tp_sl()

        except Exception as e:
            logger.error(f"Failed to sync API positions: {e}")

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
                sl_price_pct = liq_pct * sl_liq_pct2
                sl_roi = round(sl_price_pct * lev * 100, 1)

                # Only send TP to Binance if tp_enabled in config
                tp_roi = None
                if strat_cfg.get("tp_enabled", False):
                    tp_price_pct = liq_pct * tp_liq_mult2
                    tp_roi = round(tp_price_pct * lev * 100, 1)

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
                    tp_str = f" TP_ROI={tp_roi}%" if tp_roi else " (no TP)"
                    logger.info(f"Updated SL for {symbol}: SL_ROI={sl_roi}%{tp_str}")
                except Exception as e:
                    logger.warning(f"Failed to update TP/SL for {symbol}: {e}")

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
        """
        try:
            rest = self._order_executor._rest
            pp = self._order_executor._get_price_precision(symbol)
            strat = self._config.get("strategy", {})
            activate_mult = strat.get("trailing_atr_activate_mult", 4.0)
            distance_mult = strat.get("trailing_atr_distance_mult", 1.0)

            is_long = pos.side == OrderSide.BUY_LONG
            close_side = "SELL" if is_long else "BUY"

            atr_pct = atr / entry_price * 100 if entry_price > 0 and atr > 0 else 0

            # === SERVER SL: 2×ATR STOP_MARKET (crash koruması) ===
            sl_atr_mult = strat.get("server_sl_atr_mult", 2.0)
            if atr > 0 and entry_price > 0:
                if is_long:
                    sl_price = round(entry_price - (atr * sl_atr_mult), pp)
                else:
                    sl_price = round(entry_price + (atr * sl_atr_mult), pp)

                rest.place_order(
                    symbol=symbol,
                    side=close_side,
                    order_type="STOP_MARKET",
                    quantity=pos.size,
                    stop_price=sl_price,
                )
                logger.info(f"[SERVER SL] {symbol}: {sl_atr_mult}xATR SL @ {sl_price} "
                            f"({'long' if is_long else 'short'}, entry={entry_price})")
            else:
                logger.info(f"[NO SERVER SL] {symbol}: ATR=0, emergency exit korur")

            # === TRAILING_STOP_MARKET: 4×ATR'de aktif, 1×ATR callback ===
            if atr > 0 and entry_price > 0:
                callback_pct = (atr * distance_mult) / entry_price * 100
            else:
                callback_pct = 1.0
            callback_pct = max(0.1, min(5.0, round(callback_pct, 1)))

            if is_long:
                activation_price = round(entry_price + (atr * activate_mult), pp)
            else:
                activation_price = round(entry_price - (atr * activate_mult), pp)

            rest.place_order(
                symbol=symbol,
                side=close_side,
                order_type="TRAILING_STOP_MARKET",
                quantity=pos.size,
                stop_price=activation_price,
                callback_rate=callback_pct,
            )

            self._server_trailing[symbol] = {
                "callback_pct": callback_pct,
                "activation_price": activation_price,
                "sl_price": 0,  # No server SL
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
                        f"SL=YOK (emergency korur)")

        except Exception as e:
            logger.warning(f"Initial server orders failed for {symbol}: {e}")

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

    def _sync_server_trailing(self, symbol: str, pos, current_price: float,
                               confluence: dict = None) -> None:
        """SADECE trailing renew olduğunda server emirlerini günceller.
        Artık her 30s çağrılmıyor — sadece renew_trailing sonrası kullanılır.

        Mantık:
        - Server'da HER ZAMAN trailing var (pozisyon açılışında konuyor)
        - Yazılımsal trailing aktif olunca → callback sinyal gücüne göre ayarlanır
        - Trailing renew olunca → server emri güncellenir (yeni callback)
        - Sinyal güçlüyse → callback genişler (sabırlı)
        - Sinyal zayıfsa → callback daralır (hızlı kar al)
        """
        if not self._order_executor or not hasattr(self._order_executor, '_rest'):
            return

        strat = self._config.get("strategy", {})
        if not strat.get("trailing_enabled", True):
            return

        existing = self._server_trailing.get(symbol)
        if not existing:
            # Server trailing yok — pozisyon sync'den gelmis olabilir, hemen koy
            base_callback = self._calc_trailing_callback(pos, current_price, None)
            self._send_server_trailing(symbol, pos, current_price, base_callback)
            return

        new_callback = self._calc_trailing_callback(pos, current_price, confluence)
        old_callback = existing.get("callback_pct", 0)
        old_renewal = existing.get("renewal_count", 0)

        # Update if: callback changed significantly OR trailing was renewed
        needs_update = (abs(new_callback - old_callback) >= 0.2 or
                        pos.trailing_renewal_count != old_renewal)

        if needs_update:
            reason = ""
            if pos.trailing_renewal_count != old_renewal:
                reason = f" (renew #{pos.trailing_renewal_count})"

            logger.info(f"[SERVER TRAILING] {symbol}: "
                        f"callback {old_callback:.1f}% -> {new_callback:.1f}%{reason}")
            self._send_server_trailing(symbol, pos, current_price, new_callback)

    def _send_server_trailing(self, symbol: str, pos, current_price: float,
                               callback_pct: float) -> None:
        """Place or replace TRAILING_STOP_MARKET + SL on Binance.
        Sadece trailing renew olduğunda çağrılır.
        cancel_all_orders → SL + trailing yeniden konur (SL silinmesin diye)."""
        try:
            rest = self._order_executor._rest
            pp = self._order_executor._get_price_precision(symbol)
            is_long = pos.side == OrderSide.BUY_LONG
            close_side = "SELL" if is_long else "BUY"
            strat = self._config.get("strategy", {})
            atr = pos.atr_at_entry
            activate_mult = strat.get("trailing_atr_activate_mult", 4.0)
            ref_price = pos.virtual_entry_price if pos.virtual_entry_price > 0 else pos.entry_price

            # Trailing activation: her zaman 4×ATR ileride (current_price DEĞİL)
            if is_long:
                activation_price = round(ref_price + (atr * activate_mult), pp)
            else:
                activation_price = round(ref_price - (atr * activate_mult), pp)

            # Cancel all → re-place both SL + trailing
            cancel_result = rest.cancel_all_orders(symbol)
            if cancel_result.get("errors"):
                logger.warning(f"Server order cancel issues for {symbol}: {cancel_result['errors']}")

            # 1. Re-place SERVER SL (2×ATR) — cancel_all sildi, geri koy
            sl_atr_mult = strat.get("server_sl_atr_mult", 2.0)
            if atr > 0 and pos.entry_price > 0:
                if is_long:
                    sl_price = round(pos.entry_price - (atr * sl_atr_mult), pp)
                else:
                    sl_price = round(pos.entry_price + (atr * sl_atr_mult), pp)
                rest.place_order(
                    symbol=symbol, side=close_side,
                    order_type="STOP_MARKET",
                    quantity=pos.size, stop_price=sl_price,
                )

            # 2. Re-place TRAILING_STOP_MARKET
            rest.place_order(
                symbol=symbol, side=close_side,
                order_type="TRAILING_STOP_MARKET",
                quantity=pos.size,
                stop_price=activation_price,
                callback_rate=callback_pct,
            )

            self._server_trailing[symbol] = {
                "callback_pct": callback_pct,
                "activation_price": activation_price,
                "timestamp": time.time(),
                "renewal_count": pos.trailing_renewal_count,
            }

            logger.info(f"[SERVER TRAILING] {symbol}: "
                        f"callback={callback_pct:.1f}% "
                        f"activation={activation_price} "
                        f"SL={sl_price if atr > 0 else 'N/A'} "
                        f"(renew — SL + trailing yeniden kondu)")

        except Exception as e:
            logger.warning(f"Server trailing failed for {symbol}: {e} "
                           f"(software trailing devam ediyor)")

    def _remove_server_trailing(self, symbol: str, pos) -> None:
        """Remove server-side trailing stop (trailing was renewed/deactivated).
        Re-places only the SL order."""
        try:
            rest = self._order_executor._rest
            pp = self._order_executor._get_price_precision(symbol)
            is_long = pos.side == OrderSide.BUY_LONG
            close_side = "SELL" if is_long else "BUY"

            # Cancel all (removes trailing + old SL)
            cancel_result = rest.cancel_all_orders(symbol)
            if cancel_result.get("errors"):
                logger.warning(f"Trailing stop cancel issues for {symbol}: {cancel_result['errors']}")

            # Re-place SL only (2×ATR from entry)
            strat = self._config.get("strategy", {})
            atr = pos.atr_at_entry
            sl_mult = strat.get("server_sl_atr_mult", 2.0)
            ref_price = pos.virtual_entry_price if pos.virtual_entry_price > 0 else pos.entry_price

            if atr > 0:
                if is_long:
                    sl_price = round(ref_price - (atr * sl_mult), pp)
                else:
                    sl_price = round(ref_price + (atr * sl_mult), pp)
            else:
                # Fallback: fee-aware liq-based SL if ATR unknown
                lev = pos.leverage
                liq_factor = strat.get("liq_factor", 70) / 100.0
                liq_pct = (1.0 / lev) * liq_factor
                sl_liq_pct = strat.get("sl_liq_percent", 50) / 100.0
                fee_pct = 0.001
                fee_roi = fee_pct * lev * 100
                slippage_roi = fee_roi * 0.5
                raw_sl_roi = liq_pct * sl_liq_pct * lev * 100
                net_sl_roi = max(raw_sl_roi - fee_roi - slippage_roi, fee_roi)
                sl_price_pct = net_sl_roi / (lev * 100)
                if is_long:
                    sl_price = round(pos.entry_price * (1 - sl_price_pct), pp)
                else:
                    sl_price = round(pos.entry_price * (1 + sl_price_pct), pp)

            rest.place_order(
                symbol=symbol, side=close_side,
                order_type="STOP_MARKET",
                stop_price=sl_price,
                close_position=True,
            )

            self._server_trailing.pop(symbol, None)
            logger.info(f"[SERVER TRAILING] {symbol}: server trailing kaldirildi, "
                        f"sadece SL aktif @ {sl_price}")

        except Exception as e:
            logger.warning(f"Remove server trailing failed for {symbol}: {e}")

    # ──── Helpers ────

    def _wait(self, seconds: float) -> None:
        """Wait while checking if still running."""
        end = time.time() + seconds
        while self._running and time.time() < end:
            time.sleep(0.5)

    # ──── Getters (for GUI) ────

    @property
    def state(self) -> ScannerState:
        return self._state

    @property
    def scan_count(self) -> int:
        return self._scan_count

    def get_scan_results(self) -> list[ScanResult]:
        return self._last_scan_results

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

    @property
    def is_running(self) -> bool:
        return self._running
