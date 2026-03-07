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
            top_n=config.get("scanner.max_symbols_to_scan", 50),
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

        # Scan results (for GUI)
        self._last_scan_results: list[ScanResult] = []
        self._last_candidate: ScanResult = None
        self._scan_count = 0
        self._last_trade_result: dict = {}
        self._failed_symbols: dict[str, float] = {}  # symbol -> fail timestamp
        self._failed_cooldown = 300  # skip failed symbols for 5 minutes

        # Anti-churning: track trade frequency
        self._trade_timestamps: list[float] = []
        self._max_trades_per_hour = config.get("scanner.max_trades_per_hour", 12)

        # Close retry tracking: symbol -> {"count": int, "next_retry": float}
        self._close_retries: dict[str, dict] = {}
        self._max_close_retries = 5

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

        focus_mode = self._config.get("scanner.focus_mode", False)

        # FOCUS MODE: if we have a position, don't scan — just monitor
        if focus_mode and self._position_mgr.has_position:
            self._do_focus_monitoring()
            return

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
        limit = self._config.get("scanner.kline_limit_scan", 200)
        klines_map = self._fetcher.fetch_batch(
            symbols, default_interval, limit,
            symbol_intervals=symbol_intervals if dynamic_tf else None,
        )

        # 4. Score all symbols
        results = self._scorer.score_batch(klines_map, self._universe.get_all_tickers())
        self._last_scan_results = results

        # 4. Find best eligible candidate
        now = time.time()
        self._failed_symbols = {
            s: t for s, t in self._failed_symbols.items()
            if now - t < self._failed_cooldown
        }

        eligible = [r for r in results if r.eligible]
        min_score = self._config.get("scanner.min_buy_score", 70)

        # Log top 5 scores for debugging (eligible or not)
        for i, r in enumerate(results[:5]):
            logger.info(f"  #{i+1} {r.symbol}: score={r.score:+.1f} "
                        f"dir={r.direction} eligible={r.eligible} "
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
                if self._position_mgr.is_holding(r.symbol):
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

        close_only = self._config.get("scanner.close_only", False)
        if close_only:
            if self._position_mgr.has_position:
                logger.info(f"Close-only mode: monitoring {self._position_mgr.position_count} position(s), no new trades")
            else:
                logger.info("Close-only mode: no positions left, waiting...")
            scan_interval = self._config.get("scanner.scan_interval_seconds", 30)
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
            if real_balance > 0 and real_balance < 0.90:
                logger.info(f"Available balance too low ({real_balance:.2f}$), waiting for positions to close")
                scan_interval = self._config.get("scanner.scan_interval_seconds", 30)
                self._wait(scan_interval)
                return

            # Buy as many candidates as we have capacity for
            bought_any = False
            for candidate in candidates:
                if not self._position_mgr.has_capacity:
                    break
                if not self._check_trade_frequency():
                    break

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
                    break  # likely margin issue, stop trying

            # Short wait then scan again if still have capacity
            if self._position_mgr.has_capacity and bought_any:
                self._wait(5)
            else:
                scan_interval = self._config.get("scanner.scan_interval_seconds", 30)
                self._wait(scan_interval)
        else:
            if not self._position_mgr.has_capacity:
                logger.info(f"Max positions reached ({self._position_mgr.position_count}/"
                            f"{self._position_mgr.max_positions}). Monitoring only.")
            scan_interval = self._config.get("scanner.scan_interval_seconds", 30)
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
            confluence = {}
            regime = {}
            divergences = []
            try:
                pos = self._position_mgr._positions.get(symbol)
                interval = pos.timeframe if pos else self._config.get("indicators.kline_interval", "1m")
                klines = self._rest.get_klines(symbol, interval, limit=200)
                if klines and len(klines) > 50:
                    self._hold_engine.update(klines)
                    indicators = self._hold_engine.get_all_values()
                    confluence = self._hold_confluence.score(indicators)
                    regime = self._hold_regime.detect(indicators)
                    divergences = self._hold_divergence.scan(indicators)
            except Exception as e:
                logger.debug(f"Focus analysis error for {symbol}: {e}")

            exit_reason = self._position_mgr.check_position(
                symbol, current_price,
                confluence=confluence,
                regime=regime,
                divergences=divergences,
            )

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

    # ──── Check Held Positions ────

    def _check_held_positions(self) -> None:
        """Check all held positions for exit signals with full indicator analysis."""
        for symbol in list(self._position_mgr.get_held_symbols()):
            try:
                ticker = self._rest.get_ticker_price(symbol)
                current_price = float(ticker.get("price", 0))
            except Exception:
                continue

            # Full indicator analysis using position's timeframe
            confluence = {}
            divergences = []
            try:
                pos = self._position_mgr._positions.get(symbol)
                interval = pos.timeframe if pos else "1m"
                klines = self._rest.get_klines(symbol, interval, limit=200)
                if klines is not None and len(klines) > 50:
                    self._hold_engine.update(klines)
                    indicators = self._hold_engine.get_all_values()
                    confluence = self._hold_confluence.score(indicators)
                    divergences = self._hold_divergence.scan(indicators)
            except Exception:
                pass

            exit_reason = self._position_mgr.check_position(
                symbol, current_price,
                confluence=confluence,
                divergences=divergences,
            )

            if exit_reason != "HOLD":
                pos = self._position_mgr._positions.get(symbol)
                tf_str = f" tf={pos.timeframe}" if pos else ""
                logger.info(f"Exit signal for {symbol}: {exit_reason} @ {current_price:.6f}{tf_str}")
                self._sell_position(symbol, current_price, exit_reason)

    def _sell_position(self, symbol: str, exit_price: float, reason: str) -> None:
        """Sell a specific position."""
        pos = self._position_mgr._positions.get(symbol)
        if not pos:
            return

        close_side = (OrderSide.SELL_SHORT if pos.side == OrderSide.BUY_LONG
                      else OrderSide.BUY_LONG)

        logger.info(f"Closing {symbol}: {close_side.value} {pos.size}")

        success = True
        if self._order_executor:
            # API mode: close directly, no pair switching needed
            if hasattr(self._order_executor, "close_position"):
                success = self._order_executor.close_position(
                    symbol, pos.side, pos.size)
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

            self._event_bus.publish(EventType.TRADE_RESULT, result)
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

    def _do_buying(self) -> None:
        """Place order via API (legacy state machine entry), then return to SCANNING."""
        if not self._last_candidate:
            self._transition(ScannerState.SCANNING)
            return
        if not self._check_trade_frequency():
            self._transition(ScannerState.SCANNING)
            scan_interval = self._config.get("scanner.scan_interval_seconds", 30)
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

        # 2. Determine leverage mode
        lev_enabled = self._config.get("leverage.enabled", False)
        leverage = None
        margin_usdt = None
        qty_precision = 3

        if lev_enabled:
            min_lev = self._config.get("leverage.min_leverage", 10)
            max_lev = self._config.get("leverage.max_leverage", 125)

            # Read balance from API (or executor)
            real_balance = 0.0
            if self._order_executor and hasattr(self._order_executor, "get_balance"):
                try:
                    real_balance = self._order_executor.get_balance()
                except Exception as e:
                    logger.warning(f"Could not read API balance: {e}")

            # Position sizing mode: "percentage" or "fixed"
            sizing_mode = self._config.get("leverage.position_sizing", "fixed")
            if sizing_mode == "percentage":
                if real_balance > 0:
                    available = real_balance
                else:
                    balance = self._config.get("risk.initial_balance", 5.0)
                    if self._risk_manager:
                        balance = self._risk_manager._current_balance
                    used_margin = self._position_mgr.get_total_margin()
                    available = balance - used_margin

                # Emre Ortalama: portfolio_divider (1/N of balance)
                # Below 12 USDT: dynamic divider = floor(balance), min 1 USDT per position
                # Above 12 USDT: use configured divider (default 12)
                divider = self._config.get("strategy.portfolio_divider", 0)
                if divider > 0:
                    if available < 12.0:
                        divider = max(1, int(available))
                    margin_usdt = round(available / divider, 2)
                    sizing_label = f"1/{divider}"
                else:
                    portfolio_pct = self._config.get("leverage.portfolio_percent", 25)
                    margin_usdt = round(available * portfolio_pct / 100.0, 2)
                    sizing_label = f"{portfolio_pct}%"

                # Minimum 1$ margin rule
                if margin_usdt < 1.0:
                    if available >= 0.90:
                        margin_usdt = 1.0
                    else:
                        logger.warning(f"Balance too low: {available:.2f}$ "
                                       f"(need at least 0.90$)")
                        return False

                if real_balance > 0 and margin_usdt > real_balance * 0.95:
                    margin_usdt = round(real_balance * 0.95, 2)

                logger.info(f"Position sizing: {sizing_label} of "
                            f"{available:.2f}$ = {margin_usdt}$ margin")
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

            if notional_usdt < min_notional and price > 0:
                needed_margin = min_notional / leverage * 1.05
                max_allowed = self._config.get("risk.max_single_order_usdt", 50.0)
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
            # Katman 1: Server SL at 50% of liq distance
            # Katman 2: Emergency software close at 80% (in position_manager)
            strat = self._config.get("strategy", {})
            liq_factor = strat.get("liq_factor", 70) / 100.0
            liq_pct = (1.0 / leverage) * liq_factor
            sl_liq_pct = strat.get("sl_liq_percent", 50) / 100.0
            tp_liq_mult = strat.get("tp_liq_multiplier", 3.0)
            sl_price_pct = liq_pct * sl_liq_pct
            tp_price_pct = liq_pct * tp_liq_mult
            sl_roi = round(sl_price_pct * leverage * 100, 1)
            tp_roi = round(tp_price_pct * leverage * 100, 1)

            logger.info(f"LEVERAGE: {leverage}x margin={margin_usdt}$ "
                        f"notional={notional_usdt:.1f}$ qty={size_qty} "
                        f"SL={sl_price_pct*100:.2f}%(ROI-{sl_roi}%) "
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

        # 4. Execute order
        lev_str = f" LEV={leverage}x" if leverage else ""
        logger.info(f"Placing order: {side.value} {size_qty} {symbol} @ {price:.6f}"
                    f"{lev_str} TP_ROI={tp_roi:.1f}% SL_ROI={sl_roi:.1f}%")

        if self._order_executor:
            success = self._order_executor.execute_order(
                symbol=symbol, side=side, order_type=OrderType.MARKET,
                size=size_qty, tp_percent=tp_roi, sl_percent=sl_roi,
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
        self._position_mgr.open_position(
            symbol, side, price, size_qty, atr,
            leverage=leverage if lev_enabled else 1,
            margin_usdt=margin_usdt if lev_enabled else 0.0,
            timeframe=pos_tf,
        )

        if self._risk_manager:
            self._risk_manager.record_order(
                size_qty, price,
                margin_usdt=margin_usdt if lev_enabled else None,
            )

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
        cooldown = self._config.get("scanner.cooldown_after_sell_seconds", 120)
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
                    try:
                        ticker = self._rest.get_ticker_price(symbol)
                        current_price = float(ticker.get("price", 0))
                        if current_price <= 0:
                            continue
                    except Exception:
                        continue

                    exit_reason = self._position_mgr.check_position(
                        symbol, current_price)

                    if exit_reason != "HOLD":
                        # Check if we're in backoff period for this symbol
                        retry_info = self._close_retries.get(symbol)
                        if retry_info and time.time() < retry_info.get("next_retry", 0):
                            continue  # Skip until backoff expires

                        logger.warning(
                            f"[MONITOR] Exit signal for {symbol}: "
                            f"{exit_reason} @ {current_price:.6f}")
                        # Only do software close if not in BUYING state
                        if self._state != ScannerState.BUYING:
                            self._sell_position(symbol, current_price, exit_reason)

                time.sleep(check_interval)
            except Exception as e:
                logger.error(f"Position monitor error: {e}")
                time.sleep(5)

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

                # Get ATR for this symbol
                atr = 0.0
                try:
                    interval = self._config.get("indicators.kline_interval", "1m")
                    klines = self._rest.get_klines(symbol, interval, limit=50)
                    if klines is not None and len(klines) > 14:
                        from indicators.indicator_engine import IndicatorEngine
                        eng = IndicatorEngine(self._config)
                        indicators = eng.compute_all(klines)
                        atr = indicators.get("ATR", 0)
                except Exception:
                    pass

                # Open position in manager (will set SL/TP/trailing)
                self._position_mgr.open_position(
                    symbol, side, entry_price, size, atr,
                    leverage=leverage,
                    margin_usdt=margin,
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

        battle_mode = self._config.get("scanner.battle_mode", False)

        for symbol, pos in self._position_mgr._positions.items():
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
                tp_price_pct = liq_pct * tp_liq_mult2
                sl_roi = round(sl_price_pct * lev * 100, 1)
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
                    logger.info(f"Updated TP/SL for {symbol}: SL_ROI={sl_roi}% TP_ROI={tp_roi}%")
                except Exception as e:
                    logger.warning(f"Failed to update TP/SL for {symbol}: {e}")

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
        return self._position_mgr.get_all_positions_info()

    def get_last_trade(self) -> dict:
        return self._last_trade_result

    def get_position_manager(self) -> PositionManager:
        return self._position_mgr

    @property
    def is_running(self) -> bool:
        return self._running
