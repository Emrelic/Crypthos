import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from loguru import logger
from core.config_manager import ConfigManager
from core.event_bus import EventBus
from core.app_controller import AppController
from market.market_data_service import MarketDataService
from automation.binance_app import BinanceApp
from automation.order_executor import OrderExecutor
from automation.api_order_executor import ApiOrderExecutor
from automation.pair_switcher import PairSwitcher
from indicators.indicator_engine import IndicatorEngine
from strategy.strategy_engine import StrategyEngine
from safety.risk_manager import RiskManager
from safety.kill_switch import KillSwitch
from safety.order_logger import OrderLogger
from market.binance_rest import BinanceRestClient
from market.symbol_info import SymbolInfoCache
from scanner.state_machine import ScannerStateMachine
from gui.main_window import MainWindow

# Configure logging
logger.remove()
logger.add(sys.stderr, level="INFO")
logger.add("data/crypthos.log", rotation="10 MB", retention="7 days", level="DEBUG")


def main():
    logger.info("Crypthos Trading Bot starting...")

    # Initialize core
    config = ConfigManager("config.json")
    event_bus = EventBus()

    # Controller
    controller = AppController(config, event_bus)

    # Market data
    market_service = MarketDataService(config, event_bus)
    controller.set_market_service(market_service)

    # Indicators
    indicator_engine = IndicatorEngine(config)
    controller.set_indicator_engine(indicator_engine)

    # Safety (before strategy engine so it can be wired)
    risk_manager = RiskManager(config, event_bus)
    controller.set_risk_manager(risk_manager)

    # Strategy engine (with analysis modules)
    strategy_engine = StrategyEngine(indicator_engine, event_bus,
                                     config.get("strategy_eval_interval_seconds", 5))
    strategy_engine.set_market_data_provider(market_service)
    strategy_engine.set_risk_manager(risk_manager)
    strategy_engine.load_strategies()
    controller.set_strategy_engine(strategy_engine)

    kill_switch = KillSwitch(event_bus, config.get("hotkeys.kill_switch", "ctrl+shift+k"))
    controller.set_kill_switch(kill_switch)

    order_logger = OrderLogger("data/crypthos.db")
    controller.set_order_logger(order_logger)
    config.set_order_logger(order_logger)  # Config change tracking

    # Trading mode: API or UI automation
    use_api = config.get("trading.use_api", False)
    api_key = config.get_api_key()
    api_secret = config.get_api_secret()

    if use_api and api_key and api_secret:
        # API mode — no Binance Desktop needed, works in background
        logger.info("Trading mode: API (background)")
        rest_client = BinanceRestClient(api_key=api_key, api_secret=api_secret)

        # Test API connection
        api_executor = ApiOrderExecutor(rest_client, event_bus)
        if not api_executor.test_connection():
            logger.error("API connection failed! Check .env keys. "
                         "Falling back to UI mode.")
            use_api = False

    if use_api and api_key and api_secret:
        # Sync risk manager with real API balance and reset drawdown
        try:
            real_bal = api_executor.get_balance()
            if real_bal > 0:
                risk_manager.update_balance(real_bal)
                risk_manager.reset_drawdown()
                risk_manager.reset_consecutive_losses()
                logger.info(f"Risk manager synced with API balance: {real_bal:.2f} USDT "
                            f"(drawdown reset, fresh start)")
        except Exception as e:
            logger.warning(f"Could not sync risk manager balance: {e}")

        symbol_info_cache = SymbolInfoCache(rest_client)
        scanner = ScannerStateMachine(config, event_bus, rest_client,
                                      symbol_info_cache=symbol_info_cache)
        scanner.set_order_executor(api_executor)
        scanner.set_risk_manager(risk_manager)
        scanner.set_order_logger(order_logger)
        controller.set_scanner(scanner)

        # In API mode, skip Binance Desktop entirely — no window needed
        logger.info("Binance Desktop not needed in API mode, skipping UI automation")
    else:
        # Legacy UI automation mode
        logger.info("Trading mode: UI automation (Binance Desktop required)")
        rest_client = BinanceRestClient()

        binance_app = BinanceApp()
        controller.set_binance_app(binance_app)

        order_executor = OrderExecutor(binance_app, event_bus)
        controller.set_order_executor(order_executor)

        pair_switcher = PairSwitcher(binance_app, event_bus)
        controller.set_pair_switcher(pair_switcher)

        symbol_info_cache = SymbolInfoCache(rest_client)
        scanner = ScannerStateMachine(config, event_bus, rest_client,
                                      symbol_info_cache=symbol_info_cache)
        scanner.set_order_executor(order_executor)
        scanner.set_pair_switcher(pair_switcher)
        scanner.set_market_service(market_service)
        scanner.set_risk_manager(risk_manager)
        scanner.set_binance_app(binance_app)
        scanner.set_order_logger(order_logger)
        controller.set_scanner(scanner)

    # Start services
    controller.start()

    # Auto-start scanner if enabled
    if config.get("scanner.auto_start", True):
        scanner.start()
        logger.info("Scanner auto-started")

    # Register kill switch hotkey
    kill_switch.register()

    logger.info("All systems initialized. Launching GUI...")

    # Launch GUI (blocking mainloop)
    app = MainWindow(controller)
    app.mainloop()

    logger.info("Crypthos Trading Bot stopped.")


if __name__ == "__main__":
    main()
