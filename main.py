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
from automation.pair_switcher import PairSwitcher
from indicators.indicator_engine import IndicatorEngine
from strategy.strategy_engine import StrategyEngine
from safety.risk_manager import RiskManager
from safety.kill_switch import KillSwitch
from safety.order_logger import OrderLogger
from market.binance_rest import BinanceRestClient
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

    # UI Automation (connect to Binance Desktop)
    binance_app = BinanceApp()
    controller.set_binance_app(binance_app)

    order_executor = OrderExecutor(binance_app, event_bus)
    controller.set_order_executor(order_executor)

    pair_switcher = PairSwitcher(binance_app, event_bus)
    controller.set_pair_switcher(pair_switcher)

    # Scanner (crypto screener state machine)
    rest_client = BinanceRestClient()
    scanner = ScannerStateMachine(config, event_bus, rest_client)
    scanner.set_order_executor(order_executor)
    scanner.set_pair_switcher(pair_switcher)
    scanner.set_market_service(market_service)
    scanner.set_risk_manager(risk_manager)
    controller.set_scanner(scanner)

    # Start services
    controller.start()

    # Register kill switch hotkey
    kill_switch.register()

    logger.info("All systems initialized. Launching GUI...")

    # Launch GUI (blocking mainloop)
    app = MainWindow(controller)
    app.mainloop()

    logger.info("Crypthos Trading Bot stopped.")


if __name__ == "__main__":
    main()
