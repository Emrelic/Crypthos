"""Strategy Engine - evaluates strategies with confluence scoring,
market regime detection, and divergence analysis."""
import json
import os
import threading
import time
from loguru import logger
from core.event_bus import EventBus
from core.constants import EventType
from indicators.indicator_engine import IndicatorEngine
from strategy.strategy import Strategy
from analysis.confluence import ConfluenceScorer
from analysis.market_regime import MarketRegimeDetector
from analysis.divergence import DivergenceDetector


class StrategyEngine:
    """Background thread evaluating all strategies against market data.

    Enhanced with:
    - Confluence scoring across all indicators
    - Market regime detection (adapts weights)
    - Divergence detection (early reversal signals)
    """

    def __init__(self, indicator_engine: IndicatorEngine, event_bus: EventBus,
                 eval_interval: int = 5):
        self._indicator_engine = indicator_engine
        self._event_bus = event_bus
        self._strategies: dict[str, Strategy] = {}
        self._running = False
        self._thread = None
        self._eval_interval = eval_interval
        self._market_data_provider = None

        # Analysis modules
        self._confluence = ConfluenceScorer(threshold=4.0)
        self._regime_detector = MarketRegimeDetector()
        self._divergence_detector = DivergenceDetector(lookback=20)

        # Last analysis results (for GUI display)
        self._last_confluence: dict = {}
        self._last_regime: dict = {}
        self._last_divergences: list = []
        self._risk_manager = None

    def set_market_data_provider(self, provider) -> None:
        self._market_data_provider = provider

    def set_risk_manager(self, rm) -> None:
        self._risk_manager = rm

    def add_strategy(self, strategy: Strategy) -> None:
        self._strategies[strategy.name] = strategy

    def remove_strategy(self, name: str) -> None:
        self._strategies.pop(name, None)

    def get_strategy(self, name: str) -> Strategy:
        return self._strategies.get(name)

    def get_all_strategies(self) -> list[Strategy]:
        return list(self._strategies.values())

    # ──── Analysis Getters (for GUI) ────

    def get_confluence(self) -> dict:
        return self._last_confluence

    def get_regime(self) -> dict:
        return self._last_regime

    def get_divergences(self) -> list:
        return self._last_divergences

    # ──── Engine Control ────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._eval_loop, daemon=True)
        self._thread.start()
        logger.info("Strategy engine started")

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("Strategy engine stopped")

    def _eval_loop(self) -> None:
        while self._running:
            try:
                self._evaluate_all()
            except Exception as e:
                logger.error(f"Strategy eval error: {e}")
            time.sleep(self._eval_interval)

    def _evaluate_all(self) -> None:
        if not self._market_data_provider:
            return

        # Use current symbol for analysis
        symbol = None
        for strategy in self._strategies.values():
            if strategy.enabled:
                symbol = strategy.symbol
                break

        if not symbol:
            return

        try:
            # Get kline data and compute all indicators
            klines = self._market_data_provider.get_klines(symbol)
            if klines is None or klines.empty:
                return

            indicator_values = self._indicator_engine.compute_all(klines)

            # 1) Detect market regime
            regime = self._regime_detector.detect(indicator_values)
            self._last_regime = regime

            # 2) Calculate confluence score with regime-adjusted weights
            regime_weights = regime.get("indicator_weights", {})
            confluence = self._confluence.score(indicator_values, regime_weights)
            self._last_confluence = confluence

            # 3) Detect divergences
            divergences = self._detect_divergences(klines, indicator_values)
            self._last_divergences = divergences

            # Publish analysis update for GUI
            self._event_bus.publish(EventType.ANALYSIS_UPDATE, {
                "confluence": confluence,
                "regime": regime,
                "divergences": divergences,
                "indicator_values": indicator_values,
            })

            # Build enriched market data
            funding = self._market_data_provider.get_funding_rate(symbol)
            market_data = {
                "price": self._market_data_provider.get_price(symbol),
                "mark_price": funding.get("mark_price", 0),
                "funding_rate": funding.get("funding_rate", 0),
                "symbol": symbol,
                "confluence_score": confluence.get("score", 0),
                "confluence_signal": confluence.get("signal", "NEUTRAL"),
                "regime": regime.get("regime", "UNKNOWN"),
                "divergences": divergences,
            }

            # Evaluate each strategy
            for strategy in self._strategies.values():
                if not strategy.enabled:
                    continue
                if strategy.symbol != symbol:
                    continue

                try:
                    actions = strategy.evaluate(indicator_values, market_data)

                    for action in actions:
                        # Use ATR-based stops if risk manager available
                        atr = indicator_values.get("ATR", 0)
                        price = market_data["price"]
                        size = action.calculate_size(price)
                        order_price = action.calculate_price(price)

                        tp_pct = action.tp_percent
                        sl_pct = action.sl_percent

                        if self._risk_manager and atr > 0:
                            side_str = action.side.value
                            # Dynamic ATR stops override fixed percentages
                            if not tp_pct:
                                tp_pct = self._risk_manager.calculate_tp_percent(
                                    price, atr, side_str
                                )
                            if not sl_pct:
                                sl_pct = self._risk_manager.calculate_sl_percent(
                                    price, atr, side_str
                                )

                        self._event_bus.publish(EventType.STRATEGY_SIGNAL, {
                            "strategy_name": strategy.name,
                            "params": {
                                "symbol": symbol,
                                "side": action.side,
                                "order_type": action.order_type,
                                "price": order_price,
                                "size": size,
                                "tp_percent": tp_pct,
                                "sl_percent": sl_pct,
                            },
                        })
                        logger.info(
                            f"Strategy '{strategy.name}' triggered: "
                            f"{action.side.value} {size} {symbol} "
                            f"[regime={regime.get('regime')}, "
                            f"confluence={confluence.get('score', 0):.1f}]"
                        )

                except Exception as e:
                    logger.error(f"Strategy '{strategy.name}' eval error: {e}")

        except Exception as e:
            logger.error(f"Strategy evaluation error: {e}")

    def _detect_divergences(self, klines, indicator_values: dict) -> list:
        """Run divergence detection on key indicators."""
        indicator_series = {}

        # Get series from indicator engine for divergence detection
        for name in ["RSI", "CCI", "MFI", "OBV"]:
            ind = self._indicator_engine.get_indicator(name)
            if ind and hasattr(ind, "_series") and ind._series is not None:
                indicator_series[name] = ind._series

        if not indicator_series:
            return []

        return self._divergence_detector.detect_all(klines, indicator_series)

    # ──── Persistence ────

    def save_strategies(self, path: str = "strategies.json") -> None:
        data = [s.to_dict() for s in self._strategies.values()]
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Strategy save error: {e}")

    def load_strategies(self, path: str = "strategies.json") -> None:
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                strategy = Strategy.from_dict(item)
                self._strategies[strategy.name] = strategy
            logger.info(f"Loaded {len(data)} strategies from {path}")
        except Exception as e:
            logger.error(f"Strategy load error: {e}")

    @property
    def is_running(self) -> bool:
        return self._running
