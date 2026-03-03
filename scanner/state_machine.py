"""Scanner State Machine - orchestrates the SCANNING->BUYING->HOLDING->SELLING cycle.
Single thread, sequential state transitions."""
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
    """Main scanner loop: IDLE -> SCANNING -> BUYING -> HOLDING -> SELLING -> loop.

    Single thread manages all state transitions.
    Uses REST API for scanning, WebSocket for holding.
    """

    def __init__(self, config: ConfigManager, event_bus: EventBus,
                 rest_client: BinanceRestClient):
        self._config = config
        self._event_bus = event_bus
        self._rest = rest_client

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

        # Holding-phase analysis (separate engine for held symbol)
        self._hold_engine = IndicatorEngine(config)
        self._hold_confluence = ConfluenceScorer(threshold=4.0)
        self._hold_regime = MarketRegimeDetector()
        self._hold_divergence = DivergenceDetector(lookback=20)

        # External components (set by controller)
        self._order_executor = None
        self._pair_switcher = None
        self._market_service = None
        self._risk_manager = None

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

    # ──── Control ────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._main_loop, daemon=True,
                                        name="ScannerStateMachine")
        self._thread.start()
        self._transition(ScannerState.SCANNING)
        logger.info("Scanner started")

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
                    self._do_holding()
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
        """Scan top 50 symbols, score each, pick best candidate."""
        self._scan_count += 1
        logger.info(f"Scan #{self._scan_count} starting...")

        # 1. Get symbol universe
        symbols = self._universe.refresh()
        if not symbols:
            logger.warning("No symbols to scan")
            time.sleep(10)
            return

        # 2. Batch fetch klines
        interval = self._config.get("scanner.kline_interval", "15m")
        limit = self._config.get("scanner.kline_limit_scan", 200)
        klines_map = self._fetcher.fetch_batch(symbols, interval, limit)

        # 3. Score all symbols
        results = self._scorer.score_batch(klines_map, self._universe.get_all_tickers())
        self._last_scan_results = results

        # 4. Find best eligible candidate (skip recently failed symbols)
        now = time.time()
        # Clean up expired failures
        self._failed_symbols = {
            s: t for s, t in self._failed_symbols.items()
            if now - t < self._failed_cooldown
        }

        eligible = [r for r in results if r.eligible]
        min_score = self._config.get("scanner.min_buy_score", 60)

        candidate = None
        for r in eligible:
            if abs(r.score) >= min_score:
                if r.symbol in self._failed_symbols:
                    logger.debug(f"Skipping {r.symbol} (recently failed)")
                    continue
                candidate = r
                break  # Already sorted by abs(score) desc

        # Publish scan results for GUI
        self._event_bus.publish(EventType.SCANNER_UPDATE, {
            "scan_count": self._scan_count,
            "total_symbols": len(symbols),
            "scored": len(results),
            "eligible": len(eligible),
            "top_5": [
                {"symbol": r.symbol, "score": r.score, "direction": r.direction,
                 "regime": r.regime.get("regime", "?"),
                 "confluence": r.confluence.get("score", 0)}
                for r in results[:5]
            ],
            "candidate": candidate.symbol if candidate else None,
        })

        if candidate:
            self._last_candidate = candidate
            logger.info(f"Candidate found: {candidate.symbol} "
                        f"score={candidate.score:+.1f} dir={candidate.direction} "
                        f"regime={candidate.regime.get('regime')} "
                        f"confluence={candidate.confluence.get('score', 0):+.1f}")
            self._transition(ScannerState.BUYING)
        else:
            # No candidate, wait and scan again
            scan_interval = self._config.get("scanner.scan_interval_seconds", 60)
            if eligible:
                best = eligible[0]
                logger.info(f"No candidate above threshold (best: "
                            f"{best.symbol}={best.score:+.1f}). "
                            f"Next scan in {scan_interval}s")
            else:
                logger.info(f"No eligible symbols. Next scan in {scan_interval}s")
            self._wait(scan_interval)

    # ──── BUYING State ────

    def _do_buying(self) -> None:
        """Switch pair and place order."""
        candidate = self._last_candidate
        if not candidate:
            self._transition(ScannerState.SCANNING)
            return

        symbol = candidate.symbol
        price = candidate.price
        atr = candidate.atr
        direction = candidate.direction

        side = OrderSide.BUY_LONG if direction == "LONG" else OrderSide.SELL_SHORT

        # 1. Switch pair on Binance Desktop
        if self._pair_switcher:
            logger.info(f"Switching to {symbol}...")
            success = self._pair_switcher.switch_to(symbol)
            if not success:
                logger.error(f"Failed to switch to {symbol}, skipping for {self._failed_cooldown}s")
                self._failed_symbols[symbol] = time.time()
                self._transition(ScannerState.SCANNING)
                return
            time.sleep(2)  # Wait for UI to settle

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

        # 4. Calculate position size
        if self._risk_manager:
            size_usdt = self._risk_manager.kelly_position_size(
                self._risk_manager._current_balance, price, atr
            )
            # Enforce minimum
            min_usdt = self._config.get("scanner.min_order_usdt", 5.0)
            size_usdt = max(size_usdt, min_usdt)
        else:
            size_usdt = self._config.get("scanner.default_order_usdt", 5.0)

        size_qty = round(size_usdt / price, 2) if price > 0 else 0

        # 5. Calculate TP/SL
        sl_pct = (atr * 2.0 / price * 100) if price > 0 else 2.0
        tp_pct = (atr * 4.0 / price * 100) if price > 0 else 5.0

        # 6. Validate order
        if self._risk_manager:
            valid, reason = self._risk_manager.validate_order(size_qty, price, symbol)
            if not valid:
                logger.warning(f"Order rejected: {reason}")
                self._transition(ScannerState.SCANNING)
                return

        # 7. Execute order
        logger.info(f"Placing order: {side.value} {size_qty} {symbol} @ {price:.6f} "
                    f"TP={tp_pct:.1f}% SL={sl_pct:.1f}%")

        if self._order_executor:
            success = self._order_executor.execute_order(
                symbol=symbol, side=side, order_type=OrderType.MARKET,
                size=size_qty, tp_percent=tp_pct, sl_percent=sl_pct,
            )
            if not success:
                logger.error("Order execution failed")
                self._transition(ScannerState.SCANNING)
                return

        # 8. Open position tracking
        self._position_mgr.open_position(symbol, side, price, size_qty, atr)

        if self._risk_manager:
            self._risk_manager.record_order(size_qty, price)

        self._transition(ScannerState.HOLDING)

    # ──── HOLDING State ────

    def _do_holding(self) -> None:
        """Monitor position, check exit signals every eval interval."""
        if not self._position_mgr.has_position:
            self._transition(ScannerState.SCANNING)
            return

        pos = self._position_mgr.position
        symbol = pos.symbol
        eval_interval = self._config.get("scanner.hold_eval_interval", 5)

        # Get current data
        try:
            # Fresh price
            ticker = self._rest.get_ticker_price(symbol)
            current_price = float(ticker.get("price", 0))
        except Exception:
            time.sleep(eval_interval)
            return

        # Get klines for indicator analysis (every eval)
        try:
            klines = self._rest.get_klines(symbol, "15m", 200)
            indicators = self._hold_engine.compute_all(klines)
            regime = self._hold_regime.detect(indicators)
            confluence = self._hold_confluence.score(
                indicators, regime.get("indicator_weights", {})
            )
            # Divergence
            ind_series = {}
            for name in ["RSI", "CCI", "MFI", "OBV"]:
                ind = self._hold_engine.get_indicator(name)
                if ind and ind._series is not None:
                    ind_series[name] = ind._series
            divergences = self._hold_divergence.detect_all(klines, ind_series)
        except Exception as e:
            logger.debug(f"Holding analysis error: {e}")
            indicators = {}
            confluence = {}
            regime = {}
            divergences = []

        # Check exit signals
        exit_reason = self._position_mgr.update(
            current_price, indicators, confluence, regime, divergences
        )

        if exit_reason != "HOLD":
            logger.info(f"Exit signal: {exit_reason} @ {current_price:.6f}")
            self._sell_price = current_price
            self._sell_reason = exit_reason
            self._transition(ScannerState.SELLING)
        else:
            self._wait(eval_interval)

    # ──── SELLING State ────

    def _do_selling(self) -> None:
        """Close the position."""
        if not self._position_mgr.has_position:
            self._transition(ScannerState.SCANNING)
            return

        pos = self._position_mgr.position
        symbol = pos.symbol
        exit_price = getattr(self, "_sell_price", pos.entry_price)
        exit_reason = getattr(self, "_sell_reason", "UNKNOWN")

        # Close via opposite order
        close_side = (OrderSide.SELL_SHORT if pos.side == OrderSide.BUY_LONG
                      else OrderSide.BUY_LONG)

        logger.info(f"Closing position: {close_side.value} {pos.size} {symbol}")

        if self._order_executor:
            success = self._order_executor.execute_order(
                symbol=symbol, side=close_side, order_type=OrderType.MARKET,
                size=pos.size, reduce_only=True,
            )
            if not success:
                logger.error("Close order failed, retrying in 5s...")
                time.sleep(5)
                return  # Stay in SELLING state, retry

        # Record trade result
        result = self._position_mgr.close_position(exit_price, exit_reason)
        self._last_trade_result = result

        if self._risk_manager:
            pnl = result.get("pnl_usdt", 0)
            self._risk_manager.record_trade_result(pnl)

        self._event_bus.publish(EventType.TRADE_RESULT, result)
        self._transition(ScannerState.COOLDOWN)

    # ──── COOLDOWN State ────

    def _do_cooldown(self) -> None:
        cooldown = self._config.get("scanner.cooldown_after_sell_seconds", 10)
        logger.info(f"Cooldown: {cooldown}s before next scan...")
        self._wait(cooldown)
        self._transition(ScannerState.SCANNING)

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

    def get_last_trade(self) -> dict:
        return self._last_trade_result

    def get_position_manager(self) -> PositionManager:
        return self._position_mgr

    @property
    def is_running(self) -> bool:
        return self._running
