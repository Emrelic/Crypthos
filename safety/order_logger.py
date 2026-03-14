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
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS config_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    change_source TEXT DEFAULT 'manual',
                    summary TEXT DEFAULT ''
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS config_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL,
                    field_path TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (snapshot_id) REFERENCES config_snapshots(id)
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    open_time TEXT NOT NULL,
                    close_time TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    leverage INTEGER DEFAULT 1,
                    margin_usdt REAL DEFAULT 0,
                    notional_usdt REAL DEFAULT 0,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    size REAL NOT NULL,
                    pnl_usdt REAL DEFAULT 0,
                    pnl_percent REAL DEFAULT 0,
                    roi_percent REAL DEFAULT 0,
                    fee_usdt REAL DEFAULT 0,
                    exit_reason TEXT,
                    hold_seconds REAL DEFAULT 0,
                    highest_price REAL DEFAULT 0,
                    lowest_price REAL DEFAULT 0,
                    initial_sl REAL DEFAULT 0,
                    initial_tp REAL DEFAULT 0,
                    atr_at_entry REAL DEFAULT 0,
                    timeframe TEXT,
                    entry_score REAL DEFAULT 0,
                    entry_confluence REAL DEFAULT 0,
                    entry_adx REAL DEFAULT 0,
                    entry_rsi REAL DEFAULT 0,
                    entry_regime TEXT DEFAULT '',
                    entry_regime_confidence REAL DEFAULT 0,
                    entry_bb_width REAL DEFAULT 0
                )
            """)
            self._conn.commit()
            # Migrate: add regime columns if missing
            self._migrate_regime_columns()
            self._migrate_trade_columns()

    def _migrate_regime_columns(self) -> None:
        try:
            cursor = self._conn.execute("PRAGMA table_info(trades)")
            cols = {row[1] for row in cursor.fetchall()}
            for col, typ, default in [
                ("entry_regime", "TEXT", "''"),
                ("entry_regime_confidence", "REAL", "0"),
                ("entry_bb_width", "REAL", "0"),
            ]:
                if col not in cols:
                    self._conn.execute(
                        f"ALTER TABLE trades ADD COLUMN {col} {typ} DEFAULT {default}")
            self._conn.commit()
        except Exception as e:
            logger.debug(f"Regime column migration: {e}")

    def _migrate_trade_columns(self) -> None:
        """Add new columns for funding fee and config snapshot tracking."""
        try:
            cursor = self._conn.execute("PRAGMA table_info(trades)")
            cols = {row[1] for row in cursor.fetchall()}
            for col, typ, default in [
                ("funding_fee_usdt", "REAL", "0"),
                ("config_snapshot_id", "INTEGER", "0"),
            ]:
                if col not in cols:
                    self._conn.execute(
                        f"ALTER TABLE trades ADD COLUMN {col} {typ} DEFAULT {default}")
            self._conn.commit()
        except Exception as e:
            logger.debug(f"Trade column migration: {e}")

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

    def log_trade(self, open_time: str, close_time: str, symbol: str,
                  side: str, leverage: int = 1, margin_usdt: float = 0,
                  notional_usdt: float = 0, entry_price: float = 0,
                  exit_price: float = 0, size: float = 0,
                  pnl_usdt: float = 0, pnl_percent: float = 0,
                  roi_percent: float = 0, fee_usdt: float = 0,
                  exit_reason: str = "", hold_seconds: float = 0,
                  highest_price: float = 0, lowest_price: float = 0,
                  initial_sl: float = 0, initial_tp: float = 0,
                  atr_at_entry: float = 0, timeframe: str = "",
                  entry_score: float = 0, entry_confluence: float = 0,
                  entry_adx: float = 0, entry_rsi: float = 0,
                  entry_regime: str = "", entry_regime_confidence: float = 0,
                  entry_bb_width: float = 0,
                  funding_fee_usdt: float = 0,
                  config_snapshot_id: int = 0) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO trades
                   (open_time, close_time, symbol, side, leverage, margin_usdt,
                    notional_usdt, entry_price, exit_price, size,
                    pnl_usdt, pnl_percent, roi_percent, fee_usdt,
                    exit_reason, hold_seconds, highest_price, lowest_price,
                    initial_sl, initial_tp, atr_at_entry, timeframe,
                    entry_score, entry_confluence, entry_adx, entry_rsi,
                    entry_regime, entry_regime_confidence, entry_bb_width,
                    funding_fee_usdt, config_snapshot_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (open_time, close_time, symbol, side, leverage, margin_usdt,
                 notional_usdt, entry_price, exit_price, size,
                 pnl_usdt, pnl_percent, roi_percent, fee_usdt,
                 exit_reason, hold_seconds, highest_price, lowest_price,
                 initial_sl, initial_tp, atr_at_entry, timeframe,
                 entry_score, entry_confluence, entry_adx, entry_rsi,
                 entry_regime, entry_regime_confidence, entry_bb_width,
                 funding_fee_usdt, config_snapshot_id),
            )
            self._conn.commit()
        logger.info(f"Trade logged: {side} {symbol} PnL={pnl_usdt:+.4f} ({exit_reason}) [{entry_regime}]")

    def get_trades_between(self, start: str, end: str) -> list[dict]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM trades WHERE close_time >= ? AND close_time <= ? "
                "ORDER BY close_time DESC",
                (start, end),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_all_trades(self, limit: int = 500) -> list[dict]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM trades ORDER BY close_time DESC LIMIT ?", (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_orders_by_symbol(self, symbol: str, limit: int = 50) -> list[dict]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM orders WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                (symbol, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def import_from_binance(self, rest_client, start_ms: int = 0, end_ms: int = 0) -> int:
        """Import historical trades from Binance income history into trades table.
        Groups REALIZED_PNL + COMMISSION events by symbol+time window.
        Returns number of trades imported."""
        from collections import defaultdict

        income = rest_client.get_income_history(
            start_time=start_ms, end_time=end_ms, limit=1000)
        if not income:
            return 0

        # Group by symbol + time window (5 min = 300000 ms)
        symbol_events = defaultdict(list)
        for item in income:
            typ = item.get("incomeType", "")
            if typ in ("REALIZED_PNL", "COMMISSION", "INSURANCE_CLEAR"):
                symbol_events[item.get("symbol", "")].append(item)

        trades_imported = 0
        for sym, evts in symbol_events.items():
            evts.sort(key=lambda x: x.get("time", 0))
            groups = []
            current = []
            for e in evts:
                if current and e["time"] - current[-1]["time"] > 300000:
                    groups.append(current)
                    current = [e]
                else:
                    current.append(e)
            if current:
                groups.append(current)

            for group in groups:
                pnl = sum(float(x.get("income", 0)) for x in group
                          if x.get("incomeType") == "REALIZED_PNL")
                fee = sum(float(x.get("income", 0)) for x in group
                          if x.get("incomeType") == "COMMISSION")
                liq = sum(float(x.get("income", 0)) for x in group
                          if x.get("incomeType") == "INSURANCE_CLEAR")

                if pnl == 0 and liq == 0:
                    continue

                t_ms = group[0].get("time", 0)
                close_time = datetime.fromtimestamp(t_ms / 1000).isoformat()

                # Check if already imported (avoid duplicates)
                with self._lock:
                    cursor = self._conn.execute(
                        "SELECT id FROM trades WHERE symbol=? AND close_time=?",
                        (sym, close_time))
                    if cursor.fetchone():
                        continue

                exit_reason = "LIQUIDATION" if liq < 0 else "external_close"
                net_pnl = pnl + fee + liq  # fee is negative

                self.log_trade(
                    open_time=close_time,  # approximate (no exact entry time from income)
                    close_time=close_time,
                    symbol=sym,
                    side="unknown",
                    leverage=0,
                    margin_usdt=0,
                    notional_usdt=0,
                    entry_price=0,
                    exit_price=0,
                    size=0,
                    pnl_usdt=round(pnl, 6),
                    pnl_percent=0,
                    roi_percent=0,
                    fee_usdt=round(abs(fee), 6),
                    exit_reason=exit_reason,
                    hold_seconds=0,
                )
                trades_imported += 1

        logger.info(f"Imported {trades_imported} trades from Binance income history")
        return trades_imported

    def log_config_change(self, old_config: dict, new_config: dict,
                          change_source: str = "manual") -> int:
        """Compare old and new config, log snapshot + individual field changes.
        Returns snapshot_id."""
        import json
        changes = self._diff_config(old_config, new_config)
        if not changes:
            return 0  # No changes

        summary = ", ".join(f"{path}: {old}->{new}" for path, old, new in changes[:5])
        if len(changes) > 5:
            summary += f" (+{len(changes)-5} more)"

        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO config_snapshots (timestamp, config_json, change_source, summary) VALUES (?, ?, ?, ?)",
                (datetime.now().isoformat(), json.dumps(new_config, ensure_ascii=False), change_source, summary)
            )
            snapshot_id = cursor.lastrowid

            for field_path, old_val, new_val in changes:
                self._conn.execute(
                    "INSERT INTO config_changes (snapshot_id, field_path, old_value, new_value, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (snapshot_id, field_path, str(old_val), str(new_val), datetime.now().isoformat())
                )
            self._conn.commit()

        logger.info(f"Config change logged (snapshot #{snapshot_id}): {len(changes)} fields changed [{change_source}]")
        return snapshot_id

    def _diff_config(self, old: dict, new: dict, prefix: str = "") -> list:
        """Recursively diff two config dicts. Returns list of (path, old_val, new_val)."""
        changes = []
        all_keys = set(list(old.keys()) + list(new.keys()))
        for key in all_keys:
            path = f"{prefix}.{key}" if prefix else key
            old_val = old.get(key)
            new_val = new.get(key)
            if isinstance(old_val, dict) and isinstance(new_val, dict):
                changes.extend(self._diff_config(old_val, new_val, path))
            elif old_val != new_val:
                changes.append((path, old_val, new_val))
        return changes

    def get_config_snapshots(self, limit: int = 50) -> list:
        """Get recent config snapshots."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM config_snapshots ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_config_changes(self, snapshot_id: int) -> list:
        """Get changes for a specific snapshot."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM config_changes WHERE snapshot_id = ? ORDER BY id", (snapshot_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_current_snapshot_id(self) -> int:
        """Get the ID of the most recent config snapshot."""
        with self._lock:
            cursor = self._conn.execute("SELECT MAX(id) FROM config_snapshots")
            row = cursor.fetchone()
            return row[0] if row and row[0] else 0

    def get_trades_by_config(self, snapshot_id: int) -> list:
        """Get all trades that were opened during a specific config period."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM trades WHERE config_snapshot_id = ? ORDER BY close_time DESC",
                (snapshot_id,))
            return [dict(row) for row in cursor.fetchall()]

    def close(self) -> None:
        self._conn.close()
