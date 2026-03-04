import json
import os
import copy
from loguru import logger


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
    "strategy_eval_interval_seconds": 5,
    "ui_refresh_ms": 1000,
    "log_level": "INFO",
}


class ConfigManager:
    def __init__(self, path: str = "config.json"):
        self._path = path
        self._config: dict = copy.deepcopy(DEFAULT_CONFIG)
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

    def save(self) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
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

    @property
    def config(self) -> dict:
        return self._config

    def _deep_merge(self, base: dict, override: dict) -> None:
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._deep_merge(base[k], v)
            else:
                base[k] = v
