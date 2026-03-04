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

        # 2. Batch fetch klines
        interval = self._config.get("indicators.kline_interval", "1m")
        limit = self._config.get("scanner.kline_limit_scan", 200)
        klines_map = self._fetcher.fetch_batch(symbols, interval, limit)

        # 3. Score all symbols
        results = self._scorer.score_batch(klines_map, self._universe.get_all_tickers())
        self._last_scan_results = results

        # 4. Find best eligible candidate
        now = time.time()
        self._failed_symbols = {
            s: t for s, t in self._failed_symbols.items()
            if now - t < self._failed_cooldown
        }

        eligible = [r for r in results if r.eligible]
        min_score = self._config.get("scanner.min_buy_score", 60)

        candidate = None
        for r in eligible:
            if abs(r.score) >= min_score:
                # Skip recently failed
                if r.symbol in self._failed_symbols:
                    logger.debug(f"Skipping {r.symbol} (recently failed)")
                    continue
                # Skip already held
                if self._position_mgr.is_holding(r.symbol):
                    continue
                candidate = r
                break

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
            "candidate": candidate.symbol if candidate else None,
        })

        if candidate and self._position_mgr.has_capacity:
            self._last_candidate = candidate
            logger.info(f"Candidate found: {candidate.symbol} "
                        f"score={candidate.score:+.1f} dir={candidate.direction} "
                        f"regime={candidate.regime.get('regime')} "
                        f"confluence={candidate.confluence.get('score', 0):+.1f}")
            self._transition(ScannerState.BUYING)
        else:
            if not self._position_mgr.has_capacity:
                logger.info(f"Max positions reached ({self._position_mgr.position_count}/"
                            f"{self._position_mgr.max_positions}). Monitoring only.")
            scan_interval = self._config.get("scanner.scan_interval_seconds", 60)
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

            # Full indicator analysis for held position
            confluence = {}
            regime = {}
            divergences = []
            try:
                interval = self._config.get("indicators.kline_interval", "1m")
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
        """Check all held positions for exit signals. Sell any that need closing."""
        for symbol in list(self._position_mgr.get_held_symbols()):
            try:
                ticker = self._rest.get_ticker_price(symbol)
                current_price = float(ticker.get("price", 0))
            except Exception:
                continue

            # Quick check with just price
            exit_reason = self._position_mgr.check_position(
                symbol, current_price)

            if exit_reason != "HOLD":
                logger.info(f"Exit signal for {symbol}: {exit_reason} @ {current_price:.6f}")
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
            # Switch to the symbol first
            if self._pair_switcher:
                self._pair_switcher.switch_to(symbol)
                time.sleep(2)

            success = self._order_executor.execute_order(
                symbol=symbol, side=close_side, order_type=OrderType.MARKET,
                size=pos.size, reduce_only=True,
                qty_precision=3,
            )

        if success:
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
            logger.error(f"Failed to close {symbol}, will retry next cycle")

    # ──── BUYING State ────

    def _do_buying(self) -> None:
        """Switch pair and place order, then return to SCANNING."""
        candidate = self._last_candidate
        if not candidate:
            self._transition(ScannerState.SCANNING)
            return

        symbol = candidate.symbol
        price = candidate.price
        atr = candidate.atr
        direction = candidate.direction

        side = OrderSide.BUY_LONG if direction == "LONG" else OrderSide.SELL_SHORT

        # 0. Ensure Binance Desktop is visible and ready
        if self._binance_app:
            ready = self._binance_app.ensure_visible(min_elements=300)
            if not ready:
                logger.warning("Binance Desktop not ready, retrying in 30s")
                time.sleep(30)
                self._transition(ScannerState.SCANNING)
                return

        # 1. Switch pair on Binance Desktop
        if self._pair_switcher:
            logger.info(f"Switching to {symbol}...")
            success = self._pair_switcher.switch_to(symbol)
            if not success:
                logger.error(f"Failed to switch to {symbol}, skipping for {self._failed_cooldown}s")
                self._failed_symbols[symbol] = time.time()
                self._transition(ScannerState.SCANNING)
                return
            time.sleep(2)

        # 2. Switch market data service
        if self._market_service:
            self._market_service.switch_symbol(symbol)
            time.sleep(1)

        # 3. Get fresh price
        try:
            ticker = self._rest.get_ticker_price(symbol)
            price = float(ticker.get("price", price))
        except Exception:
            pass

        # 4. Determine leverage mode
        lev_enabled = self._config.get("leverage.enabled", False)
        leverage = None
        margin_usdt = None
        qty_precision = 3

        if lev_enabled:
            min_lev = self._config.get("leverage.min_leverage", 10)
            max_lev = self._config.get("leverage.max_leverage", 125)

            # Read REAL available balance from Binance UI
            real_balance = 0.0
            if self._binance_app:
                try:
                    real_balance = self._binance_app.read_available_balance()
                except Exception as e:
                    logger.warning(f"Could not read UI balance: {e}")

            # Position sizing mode: "percentage" or "fixed"
            sizing_mode = self._config.get("leverage.position_sizing", "fixed")
            if sizing_mode == "percentage":
                portfolio_pct = self._config.get("leverage.portfolio_percent", 25)
                # Use real balance from UI if available, else fallback to config
                if real_balance > 0:
                    available = real_balance
                else:
                    balance = self._config.get("risk.initial_balance", 5.0)
                    if self._risk_manager:
                        balance = self._risk_manager._current_balance
                    used_margin = self._position_mgr.get_total_margin()
                    available = balance - used_margin

                # Calculate: portfolio % of available balance
                margin_usdt = round(available * portfolio_pct / 100.0, 2)

                # Minimum 1$ margin rule
                if margin_usdt < 1.0:
                    if available >= 1.0:
                        margin_usdt = 1.0
                    else:
                        logger.warning(f"Balance too low: {available:.2f}$ "
                                       f"(need at least 1$)")
                        self._transition(ScannerState.SCANNING)
                        return

                # Don't exceed what's actually available
                if real_balance > 0 and margin_usdt > real_balance * 0.95:
                    margin_usdt = round(real_balance * 0.95, 2)

                logger.info(f"Position sizing: {portfolio_pct}% of "
                            f"{available:.2f}$ = {margin_usdt}$ margin")
            else:
                margin_usdt = self._config.get("leverage.margin_usdt", 1.0)
                max_pos = self._config.get("leverage.max_position_usdt", 50.0)
                if margin_usdt > max_pos:
                    margin_usdt = max_pos
                # Don't exceed real available balance
                if real_balance > 0 and margin_usdt > real_balance * 0.95:
                    margin_usdt = round(real_balance * 0.95, 2)
                    logger.info(f"Margin capped to {margin_usdt}$ (avbl={real_balance}$)")

            # Get qty precision from symbol info cache
            qty_precision = 3
            if self._symbol_info_cache:
                try:
                    sym_info = self._symbol_info_cache.get(symbol)
                    if sym_info:
                        qty_precision = sym_info.quantity_precision
                except Exception as e:
                    logger.warning(f"SymbolInfo fetch failed for {symbol}: {e}")

            # Read actual max leverage from Binance UI
            ui_max = 0
            if self._binance_app:
                try:
                    ui_max = self._binance_app.get_ui_max_leverage()
                    logger.info(f"{symbol} UI max leverage: {ui_max}x")
                except Exception as e:
                    logger.warning(f"Failed to read UI leverage for {symbol}: {e}")

            if ui_max > 0:
                available_max = ui_max
            else:
                # Fallback: read current leverage button value
                btn_lev = 0
                if self._binance_app:
                    try:
                        btn = self._binance_app.get_leverage_button()
                        match = re.search(r"(\d+)x", btn.element_info.name or "")
                        if match:
                            btn_lev = int(match.group(1))
                    except Exception:
                        pass
                if btn_lev > 0:
                    available_max = btn_lev
                    logger.info(f"Using current leverage button: {btn_lev}x")
                else:
                    available_max = max_lev
                    logger.warning(f"Could not read UI max leverage, using config: {max_lev}x")

            if available_max < min_lev:
                logger.warning(f"{symbol} max leverage {available_max}x < "
                               f"min required {min_lev}x, skipping")
                self._failed_symbols[symbol] = time.time()
                self._transition(ScannerState.SCANNING)
                return

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
                    self._transition(ScannerState.SCANNING)
                    return

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
                    self._transition(ScannerState.SCANNING)
                    return

            # TP/SL as ROI% for Binance UI
            sl_price_pct = self._config.get("leverage.sl_percent", 0.7)
            tp_price_pct = self._config.get("leverage.tp_percent", 1.5)
            sl_roi = round(sl_price_pct * leverage, 1)
            tp_roi = round(tp_price_pct * leverage, 1)

            logger.info(f"LEVERAGE: {leverage}x margin={margin_usdt}$ "
                        f"notional={notional_usdt:.1f}$ qty={size_qty} "
                        f"SL_ROI={sl_roi}% TP_ROI={tp_roi}%")
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

        # 5. Validate order
        if self._risk_manager:
            valid, reason = self._risk_manager.validate_order(
                size_qty, price, symbol,
                margin_usdt=margin_usdt if lev_enabled else None,
                leverage=leverage if lev_enabled else None,
            )
            if not valid:
                logger.warning(f"Order rejected: {reason}")
                self._failed_symbols[symbol] = time.time()
                self._transition(ScannerState.SCANNING)
                return

        # 6. Execute order
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
                self._transition(ScannerState.SCANNING)
                return

        # 7. Open position tracking
        self._position_mgr.open_position(
            symbol, side, price, size_qty, atr,
            leverage=leverage if lev_enabled else 1,
            margin_usdt=margin_usdt if lev_enabled else 0.0,
        )

        if self._risk_manager:
            self._risk_manager.record_order(
                size_qty, price,
                margin_usdt=margin_usdt if lev_enabled else None,
            )

        # Go back to scanning (can open more positions)
        self._transition(ScannerState.SCANNING)

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
        cooldown = self._config.get("scanner.cooldown_after_sell_seconds", 10)
        logger.info(f"Cooldown: {cooldown}s before next scan...")
        self._wait(cooldown)
        self._transition(ScannerState.SCANNING)

    # ──── Position Monitor (fast check thread) ────

    def _position_monitor_loop(self) -> None:
        """Separate thread that checks positions every 3 seconds.
        This is critical for high-leverage positions where liquidation
        can happen faster than the scan cycle."""
        check_interval = 2  # seconds (fast for high leverage)
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
