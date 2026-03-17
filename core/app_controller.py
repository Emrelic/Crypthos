"""Central orchestrator - connects all subsystems, routes events, manages lifecycle."""
from loguru import logger
from core.event_bus import EventBus
from core.config_manager import ConfigManager
from core.constants import EventType, OrderSide, OrderType


class AppController:
    """Central orchestrator. GUI only talks to this controller."""

    def __init__(self, config: ConfigManager, event_bus: EventBus):
        self.config = config
        self.event_bus = event_bus

        # Subsystems (set during initialization)
        self.market_service = None
        self.order_executor = None
        self.risk_manager = None
        self.kill_switch = None
        self.order_logger = None
        self.indicator_engine = None
        self.strategy_engine = None
        self.binance_app = None
        self.pair_switcher = None
        self.scanner = None

        self._current_price: dict[str, float] = {}
        self._current_symbol = config.get("active_symbol", "DOGEUSDT")

        # Subscribe to events
        self.event_bus.subscribe(EventType.PRICE_UPDATE, self._on_price_update)
        self.event_bus.subscribe(EventType.STRATEGY_SIGNAL, self._on_strategy_signal)
        self.event_bus.subscribe(EventType.KILL_SWITCH, self._on_kill_switch)

    # ──── Setters ────

    def set_market_service(self, service) -> None:
        self.market_service = service

    def set_order_executor(self, executor) -> None:
        self.order_executor = executor

    def set_risk_manager(self, manager) -> None:
        self.risk_manager = manager

    def set_kill_switch(self, ks) -> None:
        self.kill_switch = ks

    def set_order_logger(self, ol) -> None:
        self.order_logger = ol

    def set_indicator_engine(self, ie) -> None:
        self.indicator_engine = ie

    def set_strategy_engine(self, se) -> None:
        self.strategy_engine = se

    def set_binance_app(self, app) -> None:
        self.binance_app = app

    def set_pair_switcher(self, ps) -> None:
        self.pair_switcher = ps

    def set_scanner(self, scanner) -> None:
        self.scanner = scanner

    # ──── Market Data ────

    def get_current_price(self, symbol: str = None) -> float:
        symbol = symbol or self._current_symbol
        return self._current_price.get(symbol, 0.0)

    def get_current_symbol(self) -> str:
        return self._current_symbol

    def get_watched_symbols(self) -> list[str]:
        return self.config.get("watched_symbols", ["DOGEUSDT"])

    def get_indicator_values(self) -> dict:
        if self.indicator_engine:
            return self.indicator_engine.get_all_values()
        return {}

    def get_funding_rate(self, symbol: str = None) -> dict:
        if self.market_service:
            return self.market_service.get_funding_rate(symbol or self._current_symbol)
        return {}

    # ──── Analysis (from strategy engine) ────

    def get_confluence(self) -> dict:
        if self.strategy_engine:
            return self.strategy_engine.get_confluence()
        return {}

    def get_regime(self) -> dict:
        if self.strategy_engine:
            return self.strategy_engine.get_regime()
        return {}

    def get_divergences(self) -> list:
        if self.strategy_engine:
            return self.strategy_engine.get_divergences()
        return []

    # ──── Risk Management ────

    def get_risk_stats(self) -> dict:
        if self.risk_manager:
            return self.risk_manager.get_risk_stats()
        return {}

    def get_atr_stops(self, side: str = "BUY") -> dict:
        """Get ATR-based TP/SL for current price."""
        if not self.risk_manager:
            return {}
        vals = self.get_indicator_values()
        atr = vals.get("ATR", 0)
        price = self.get_current_price()
        if atr <= 0 or price <= 0:
            return {}
        return {
            "sl_price": self.risk_manager.calculate_atr_stop(price, atr, side),
            "tp_price": self.risk_manager.calculate_atr_tp(price, atr, side),
            "sl_percent": round(self.risk_manager.calculate_sl_percent(price, atr, side), 2),
            "tp_percent": round(self.risk_manager.calculate_tp_percent(price, atr, side), 2),
        }

    def get_kelly_size(self) -> float:
        """Get Kelly Criterion recommended position size in USDT."""
        if not self.risk_manager:
            return 0.0
        balance = self.risk_manager._current_balance
        price = self.get_current_price()
        return self.risk_manager.kelly_position_size(balance, price)

    # ──── Order Execution ────

    def place_order(self, symbol: str, side: OrderSide, order_type: OrderType,
                    price: float = None, size: float = None,
                    tp_percent: float = None, sl_percent: float = None,
                    reduce_only: bool = False, trigger_source: str = "manual") -> bool:
        if self.risk_manager and self.risk_manager.is_killed:
            logger.warning("Kill switch active - order blocked")
            return False

        current_price = self.get_current_price(symbol)

        if self.risk_manager:
            valid, reason = self.risk_manager.validate_order(
                size=size, price=price or current_price, symbol=symbol
            )
            if not valid:
                logger.warning(f"Order rejected by risk manager: {reason}")
                if self.order_logger:
                    self.order_logger.log_order(
                        symbol=symbol, side=side.value, order_type=order_type.value,
                        price=price or current_price, size=size,
                        tp_percent=tp_percent, sl_percent=sl_percent,
                        notional_usdt=(size or 0) * (price or current_price),
                        status="rejected_risk", error_message=reason,
                        trigger_source=trigger_source,
                    )
                return False

        if self.order_executor:
            success = self.order_executor.execute_order(
                symbol=symbol, side=side, order_type=order_type,
                price=price, size=size,
                tp_percent=tp_percent, sl_percent=sl_percent,
                reduce_only=reduce_only,
            )
            if self.order_logger:
                self.order_logger.log_order(
                    symbol=symbol, side=side.value, order_type=order_type.value,
                    price=price or current_price, size=size,
                    tp_percent=tp_percent, sl_percent=sl_percent,
                    notional_usdt=(size or 0) * (price or current_price),
                    status="placed" if success else "failed",
                    trigger_source=trigger_source,
                )
            if success and self.risk_manager:
                self.risk_manager.record_order(size, price or current_price)
            return success
        return False

    def switch_pair(self, symbol: str) -> bool:
        if self.pair_switcher:
            old = self._current_symbol
            success = self.pair_switcher.switch_to(symbol)
            if success:
                self._current_symbol = symbol
                self.config.set("active_symbol", symbol)
                self.event_bus.publish(EventType.PAIR_CHANGED, {
                    "old_symbol": old, "new_symbol": symbol,
                })
                if self.market_service:
                    self.market_service.switch_symbol(symbol)
                logger.info(f"Pair switched: {old} -> {symbol}")
            return success
        return False

    # ──── Strategy ────

    def start_strategy_engine(self) -> None:
        if self.strategy_engine:
            self.strategy_engine.start()

    def stop_strategy_engine(self) -> None:
        if self.strategy_engine:
            self.strategy_engine.stop()

    # ──── Scanner ────

    def start_scanner(self) -> None:
        if self.scanner:
            self.scanner.start()

    def stop_scanner(self) -> None:
        if self.scanner:
            self.scanner.stop()

    def get_scanner_state(self) -> str:
        if self.scanner:
            return self.scanner.state.value
        return "IDLE"

    def get_scan_results(self) -> list:
        if self.scanner:
            return self.scanner.get_scan_results()
        return []

    def get_mr_scan_results(self) -> list:
        """Return Mean Reversion scan results."""
        if self.scanner:
            return self.scanner.get_mr_scan_results()
        return []

    def get_scanner_candidate(self):
        if self.scanner:
            return self.scanner.get_candidate()
        return None

    def get_scanner_position(self) -> dict:
        if self.scanner:
            return self.scanner.get_position_info()
        return {}

    def get_all_scanner_positions(self) -> list[dict]:
        if self.scanner:
            return self.scanner.get_all_positions()
        return []

    def get_held_indicators(self) -> dict[str, dict]:
        if self.scanner:
            return self.scanner.get_held_indicators()
        return {}

    def get_last_trade(self) -> dict:
        if self.scanner:
            return self.scanner.get_last_trade()
        return {}

    def get_scanner_scan_count(self) -> int:
        if self.scanner:
            return self.scanner.scan_count
        return 0

    def get_banned_symbols(self) -> dict[str, dict]:
        if self.scanner:
            return self.scanner.get_banned_symbols()
        return {}

    def get_system_alerts(self) -> list[dict]:
        """Get active system alerts (pauses, limits, bans).
        Returns: [{"level": "error"|"warning"|"info", "message": str}]"""
        alerts = []
        if self.risk_manager:
            stats = self.risk_manager.get_risk_stats()
            max_consec = self.risk_manager._max_consecutive_before_pause
            consec = stats.get("consecutive_losses", 0)
            if consec >= max_consec:
                alerts.append({
                    "level": "error",
                    "message": f"DURDURULDU: {consec} ardisik zarar (limit: {max_consec})",
                })
            elif consec >= max_consec - 2:
                alerts.append({
                    "level": "warning",
                    "message": f"DIKKAT: {consec}/{max_consec} ardisik zarar",
                })

            daily_loss = stats.get("daily_loss", 0)
            daily_limit = self.config.get("risk.daily_loss_limit_usdt", 5.0)
            if daily_loss >= daily_limit:
                alerts.append({
                    "level": "error",
                    "message": f"Gunluk zarar limiti: {daily_loss:.2f}/{daily_limit:.2f} USDT",
                })

            dd = stats.get("drawdown_pct", 0)
            max_dd = self.config.get("risk.max_drawdown_percent", 30.0)
            if dd >= max_dd:
                alerts.append({
                    "level": "error",
                    "message": f"Max drawdown: %{dd:.1f} (limit: %{max_dd:.0f})",
                })

            if stats.get("killed", False):
                alerts.append({
                    "level": "error",
                    "message": "KILL SWITCH AKTIF — tum emirler engellendi",
                })

        # Banned symbol count
        bans = self.get_banned_symbols()
        if bans:
            cooldowns = sum(1 for v in bans.values() if v["type"] == "cooldown")
            daily_bans = sum(1 for v in bans.values() if v["type"] == "daily_ban")
            parts = []
            if cooldowns:
                parts.append(f"{cooldowns} cooldown")
            if daily_bans:
                parts.append(f"{daily_bans} ban")
            alerts.append({
                "level": "info",
                "message": f"Banli coin: {', '.join(parts)} ({len(bans)} toplam)",
            })

        return alerts

    def reset_consecutive_losses(self) -> None:
        if self.risk_manager:
            self.risk_manager.reset_consecutive_losses()

    # ──── Safety ────

    def activate_kill_switch(self) -> None:
        if self.risk_manager:
            self.risk_manager.activate_kill_switch()
        if self.strategy_engine:
            self.strategy_engine.stop()
        logger.critical("KILL SWITCH ACTIVATED")

    def deactivate_kill_switch(self) -> None:
        if self.risk_manager:
            self.risk_manager.deactivate_kill_switch()
        logger.info("Kill switch deactivated")

    # ──── Event Handlers ────

    def _on_price_update(self, data: dict) -> None:
        symbol = data.get("symbol", self._current_symbol)
        price = data.get("price", 0.0)
        self._current_price[symbol] = price

    def _on_strategy_signal(self, data: dict) -> None:
        params = data.get("params", {})
        strategy_name = data.get("strategy_name", "unknown")
        logger.info(f"Strategy signal from '{strategy_name}': {params}")
        self.place_order(
            symbol=params.get("symbol", self._current_symbol),
            side=params.get("side", OrderSide.BUY_LONG),
            order_type=params.get("order_type", OrderType.MARKET),
            price=params.get("price"),
            size=params.get("size"),
            tp_percent=params.get("tp_percent"),
            sl_percent=params.get("sl_percent"),
            trigger_source=f"strategy:{strategy_name}",
        )

    def _on_kill_switch(self, data: dict) -> None:
        self.activate_kill_switch()

    # ──── Lifecycle ────

    def start(self) -> None:
        if self.market_service:
            self.market_service.start(self._current_symbol)
        if self.binance_app:
            self.binance_app.connect()
        logger.info("AppController started")

    def shutdown(self) -> None:
        if self.strategy_engine:
            self.strategy_engine.stop()
        if self.market_service:
            self.market_service.stop()
        if self.order_logger:
            self.order_logger.close()
        self.event_bus.shutdown()
        self.config.save()
        logger.info("AppController shut down")
