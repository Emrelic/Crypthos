import json
import os
import copy
from loguru import logger

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv optional, can use env vars directly


DEFAULT_CONFIG = {
    "active_symbol": "DOGEUSDT",
    "watched_symbols": ["DOGEUSDT", "BTCUSDT", "ETHUSDT"],
    "hotkeys": {
        "buy_long": "ctrl+shift+b",
        "sell_short": "ctrl+shift+s",
        "kill_switch": "ctrl+shift+k",
        "quick_close": "ctrl+shift+x",
    },
    "risk": {
        "max_position_usdt": 100.0,
        "max_single_order_usdt": 50.0,
        "confirm_above_usdt": 20.0,
        "default_tp_percent": 5.0,
        "default_sl_percent": 2.0,
        "initial_balance": 15.0,
        "daily_loss_limit_usdt": 5.0,
        "max_drawdown_percent": 20.0,
        "max_consecutive_losses": 5,
        "default_position_fraction": 0.02,
    },
    "leverage": {
        "enabled": True,
        "mode": "isolated",
        "margin_usdt": 1.0,
        "min_leverage": 50,
        "max_leverage": 125,
        "sl_percent": 0.7,
        "tp_percent": 1.5,
        "trailing_activation_pct": 0.5,
        "trailing_distance_pct": 0.3,
        "max_hold_minutes": 60,
        "min_hold_seconds": 30,
    },
    "indicators": {
        "rsi_period": 14,
        "ma_fast": 20,
        "ma_slow": 200,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "kline_interval": "15m",
        "kline_limit": 500,
    },
    "strategy": {
        "mode": "standard",
        "preset": "dengeli",
        "min_leverage": 1,
        "max_leverage": 20,
        "max_positions": 6,
        "portfolio_percent": 8,
        "portfolio_divider": 12,
        "fee_pct": 0.10,
        "server_sl_atr_mult": 2.0,
        "sl_enabled": True,
        "emergency_enabled": True,
        "trailing_enabled": True,
        "tp_enabled": False,
        "signal_exit_enabled": True,
        "battle_mode": False,
        "trailing_atr_activate_mult": 3.0,
        "trailing_atr_distance_mult": 1.0,
        "time_limit_minutes": 480,
        "cooldown_seconds": 60,
        "loss_cooldown_seconds": 3600,
        "adx_regime_enabled": False,
        "mean_reversion_enabled": False,
    },
    "strategy_eval_interval_seconds": 5,
    "ui_refresh_ms": 1000,
    "log_level": "INFO",
}


class ConfigManager:
    def __init__(self, path: str = "config.json"):
        self._path = path
        self._config: dict = copy.deepcopy(DEFAULT_CONFIG)
        self._previous_config: dict = {}  # for change tracking
        self._order_logger = None  # set externally after init
        self.load()

    def load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._deep_merge(self._config, loaded)
                logger.info(f"Config loaded from {self._path}")
            except Exception as e:
                logger.warning(f"Config load error, using defaults: {e}")
        else:
            self.save()
            logger.info(f"Default config created at {self._path}")

    def set_order_logger(self, order_logger) -> None:
        """Set order logger for config change tracking."""
        self._order_logger = order_logger
        # Take initial snapshot
        if order_logger:
            self._previous_config = copy.deepcopy(self._config)
            order_logger.log_config_change({}, self._config, change_source="startup")

    def save(self, change_source: str = "manual") -> None:
        """Save config and log changes to DB."""
        # Log config changes if tracker is set
        if self._order_logger and self._previous_config:
            try:
                self._order_logger.log_config_change(
                    self._previous_config, self._config, change_source)
            except Exception as e:
                logger.debug(f"Config change logging failed: {e}")

        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
            self._previous_config = copy.deepcopy(self._config)
        except Exception as e:
            logger.error(f"Config save error: {e}")

    def get(self, key: str, default=None):
        """Dot-notation access: get('risk.max_position_usdt')"""
        keys = key.split(".")
        val = self._config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    def set(self, key: str, value) -> None:
        keys = key.split(".")
        d = self._config
        for k in keys[:-1]:
            if k not in d or not isinstance(d[k], dict):
                d[k] = {}
            d = d[k]
        d[keys[-1]] = value

    def get_api_key(self) -> str:
        return os.environ.get("BINANCE_API_KEY", "")

    def get_api_secret(self) -> str:
        return os.environ.get("BINANCE_API_SECRET", "")

    @property
    def config(self) -> dict:
        return self._config

    def _deep_merge(self, base: dict, override: dict) -> None:
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._deep_merge(base[k], v)
            else:
                base[k] = v
