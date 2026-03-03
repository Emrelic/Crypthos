import sqlite3
import threading
import os
from datetime import datetime
from loguru import logger


class OrderLogger:
    def __init__(self, db_path: str = "data/crypthos.db"):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    price REAL,
                    size REAL,
                    tp_percent REAL,
                    sl_percent REAL,
                    notional_usdt REAL,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    trigger_source TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL
                )
            """)
            self._conn.commit()

    def log_order(self, symbol: str, side: str, order_type: str,
                  price: float, size: float, tp_percent: float = None,
                  sl_percent: float = None, notional_usdt: float = None,
                  status: str = "placed", error_message: str = None,
                  trigger_source: str = "manual") -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO orders
                   (timestamp, symbol, side, order_type, price, size,
                    tp_percent, sl_percent, notional_usdt, status,
                    error_message, trigger_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().isoformat(),
                    symbol, side, order_type, price, size,
                    tp_percent, sl_percent, notional_usdt,
                    status, error_message, trigger_source,
                ),
            )
            self._conn.commit()
        logger.info(f"Order logged: {side} {size} {symbol} @ {price} [{status}]")

    def log_event(self, level: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (timestamp, level, message) VALUES (?, ?, ?)",
                (datetime.now().isoformat(), level, message),
            )
            self._conn.commit()

    def get_recent_orders(self, limit: int = 50) -> list[dict]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_orders_by_symbol(self, symbol: str, limit: int = 50) -> list[dict]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM orders WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                (symbol, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def close(self) -> None:
        self._conn.close()
