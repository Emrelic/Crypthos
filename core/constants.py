from enum import Enum


class OrderSide(Enum):
    BUY_LONG = "Buy/Long"
    SELL_SHORT = "Sell/Short"


class OrderType(Enum):
    LIMIT = "Limit"
    MARKET = "Market"
    STOP_LIMIT = "Stop Limit"


class ConditionOperator(Enum):
    LESS_THAN = "<"
    LESS_EQUAL = "<="
    GREATER_THAN = ">"
    GREATER_EQUAL = ">="
    EQUAL = "=="
    CROSSES_ABOVE = "crosses_above"
    CROSSES_BELOW = "crosses_below"


class IndicatorType(Enum):
    RSI = "RSI"
    SMA = "SMA"
    EMA = "EMA"
    MACD_LINE = "MACD_Line"
    MACD_SIGNAL = "MACD_Signal"
    MACD_HISTOGRAM = "MACD_Histogram"
    FUNDING_RATE = "Funding_Rate"
    PRICE = "Price"
    MARK_PRICE = "Mark_Price"


class EventType:
    PRICE_UPDATE = "price_update"
    KLINE_UPDATE = "kline_update"
    FUNDING_UPDATE = "funding_update"
    INDICATOR_UPDATE = "indicator_update"
    ORDER_PLACED = "order_placed"
    ORDER_FAILED = "order_failed"
    STRATEGY_SIGNAL = "strategy_signal"
    KILL_SWITCH = "kill_switch"
    PAIR_CHANGED = "pair_changed"
    CONNECTION_STATUS = "connection_status"
    LOG_MESSAGE = "log_message"
    RISK_UPDATE = "risk_update"
    ANALYSIS_UPDATE = "analysis_update"
    REGIME_CHANGE = "regime_change"
    # Scanner events
    SCANNER_STATE_CHANGE = "scanner_state_change"
    SCANNER_UPDATE = "scanner_update"
    SCANNER_CANDIDATE = "scanner_candidate"
    POSITION_OPENED = "position_opened"
    POSITION_UPDATE = "position_update"
    POSITION_CLOSED = "position_closed"
    TRADE_RESULT = "trade_result"


class ScannerState(Enum):
    IDLE = "IDLE"
    SCANNING = "SCANNING"
    BUYING = "BUYING"
    HOLDING = "HOLDING"
    SELLING = "SELLING"
    COOLDOWN = "COOLDOWN"


BINANCE_FUTURES_REST = "https://fapi.binance.com"
BINANCE_FUTURES_WS = "wss://fstream.binance.com/ws"


class BinanceUI:
    WINDOW_TITLE = "Binance"
    WINDOW_CLASS = "Chrome_WidgetWin_1"
    RENDER_CLASS = "Chrome_RenderWidgetHostHWND"
