"""TF Heatmap Engine — compute per-timeframe indicator votes across time.

Fetches klines for a single symbol across 10 timeframes and computes
EMA, MACD, RSI votes (+1/0/-1) at each candle. Uses memory-efficient
candle-resolution storage with bisect-based lookup for minute resolution.

Supports progress callbacks and cancellation (same pattern as BacktestEngine).
"""
import bisect
import numpy as np
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from backtest.indicators import ema_series, macd_line_series, rsi_val
from backtest.data_fetcher import fetch_klines


# ════════════════════════ Constants ════════════════════════

# Binance Futures supported intervals we use (10 timeframes)
HEATMAP_TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d"]

# Minutes per timeframe for candle count calculations
TF_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240,
    "8h": 480, "12h": 720, "1d": 1440,
}

# Indicator parameters
EMA_FAST = 9
EMA_SLOW = 21
EMA_GAP_THRESHOLD = 0.0005  # 0.05%
MACD_FAST = 8
MACD_SLOW = 17
MACD_SIGNAL = 9
RSI_PERIOD = 14
RSI_LONG = 60
RSI_SHORT = 40


# ════════════════════════ Data Classes ════════════════════════

@dataclass
class TFIndicatorData:
    """Per-TF indicator data at candle resolution."""
    timeframe: str
    timestamps: list  # candle open timestamps (ms), sorted ascending
    ema_votes: list   # +1 (AL), 0 (NOTR), -1 (SAT) per candle
    macd_votes: list  # +1 (AL), 0 (NOTR), -1 (SAT) per candle
    rsi_votes: list   # +1 (AL), 0 (NOTR), -1 (SAT) per candle


@dataclass
class HeatmapData:
    """Complete heatmap result. Memory-efficient: stores candle-resolution
    data and uses bisect for minute-level lookups."""
    symbol: str
    start_ms: int
    end_ms: int
    timeframes: list  # ["1m", "5m", ...]
    tf_data: dict     # tf_name -> TFIndicatorData

    def get_at(self, minute_ms: int, tf: str) -> dict:
        """Get indicator state at a specific minute for a specific TF.

        Uses bisect to find the last candle that opened at or before minute_ms.
        Returns dict with keys: ema, macd, rsi, align.
        """
        if tf not in self.tf_data:
            return {"ema": 0, "macd": 0, "rsi": 0, "align": 0}

        td = self.tf_data[tf]
        if not td.timestamps:
            return {"ema": 0, "macd": 0, "rsi": 0, "align": 0}

        # bisect_right gives insertion point; subtract 1 to get last candle <= minute_ms
        idx = bisect.bisect_right(td.timestamps, minute_ms) - 1
        if idx < 0:
            return {"ema": 0, "macd": 0, "rsi": 0, "align": 0}

        ema_v = td.ema_votes[idx]
        macd_v = td.macd_votes[idx]
        rsi_v = td.rsi_votes[idx]

        # Alignment: count how many non-zero votes agree
        votes = [ema_v, macd_v, rsi_v]
        non_zero = [v for v in votes if v != 0]
        if not non_zero:
            align = 0
        else:
            # Count max agreement among non-zero votes
            pos = sum(1 for v in votes if v > 0)
            neg = sum(1 for v in votes if v < 0)
            align = max(pos, neg)

        return {"ema": ema_v, "macd": macd_v, "rsi": rsi_v, "align": align}

    def get_minute_range(self) -> list:
        """Return 1-minute spaced timestamps (ms) from start to end."""
        step = 60_000  # 1 minute in ms
        result = []
        ts = self.start_ms
        while ts <= self.end_ms:
            result.append(ts)
            ts += step
        return result

    def get_alignment_at(self, minute_ms: int) -> dict:
        """Get alignment summary across all TFs at a given minute.

        Returns dict with:
          - per_tf: {tf: {ema, macd, rsi, align}}
          - total_long: count of TFs voting long (align >= 2 and majority positive)
          - total_short: count of TFs voting short (align >= 2 and majority negative)
          - net_score: total_long - total_short
        """
        per_tf = {}
        total_long = 0
        total_short = 0
        for tf in self.timeframes:
            state = self.get_at(minute_ms, tf)
            per_tf[tf] = state
            if state["align"] >= 2:
                pos = sum(1 for k in ("ema", "macd", "rsi") if state[k] > 0)
                neg = sum(1 for k in ("ema", "macd", "rsi") if state[k] < 0)
                if pos > neg:
                    total_long += 1
                elif neg > pos:
                    total_short += 1
        return {
            "per_tf": per_tf,
            "total_long": total_long,
            "total_short": total_short,
            "net_score": total_long - total_short,
        }


# ════════════════════════ RSI Series Helper ════════════════════════

def _rsi_series(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute RSI for every candle (Wilder smoothing). Returns array of same
    length as closes, with 50.0 for insufficient data."""
    n = len(closes)
    result = np.full(n, 50.0)
    if n < period + 1:
        return result

    d = np.diff(closes)
    gains = np.where(d > 0, d, 0.0)
    losses = np.where(d < 0, -d, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(d)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            result[i + 1] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    # Fill the period-th candle
    if avg_loss == 0:
        result[period] = 100.0
    else:
        ag0 = np.mean(gains[:period])
        al0 = np.mean(losses[:period])
        result[period] = 100.0 - 100.0 / (1.0 + ag0 / al0) if al0 != 0 else 100.0

    return result


# ════════════════════════ Vote Computation ════════════════════════

def _compute_votes(closes: np.ndarray) -> tuple:
    """Compute EMA, MACD, RSI votes for each candle.

    Returns (ema_votes, macd_votes, rsi_votes) as lists of int.
    """
    n = len(closes)
    if n == 0:
        return [], [], []

    # --- EMA 9/21 ---
    ema9 = ema_series(closes, EMA_FAST)
    ema21 = ema_series(closes, EMA_SLOW)
    ema_votes = []
    for i in range(n):
        if ema21[i] == 0:
            ema_votes.append(0)
            continue
        gap_pct = (ema9[i] - ema21[i]) / ema21[i]
        if gap_pct > EMA_GAP_THRESHOLD:
            ema_votes.append(1)
        elif gap_pct < -EMA_GAP_THRESHOLD:
            ema_votes.append(-1)
        else:
            ema_votes.append(0)

    # --- MACD 8/17/9 ---
    macd_line = macd_line_series(closes, MACD_FAST, MACD_SLOW)
    # Signal line = EMA of MACD line
    signal_line = ema_series(macd_line, MACD_SIGNAL)
    histogram = macd_line - signal_line

    macd_votes = []
    # Pad if macd_line is shorter than closes (shouldn't happen with current impl)
    hist_len = len(histogram)
    for i in range(n):
        if i >= hist_len:
            macd_votes.append(0)
            continue
        h = histogram[i]
        # Momentum: histogram increasing or decreasing
        if i > 0 and i < hist_len:
            h_prev = histogram[i - 1]
            momentum_up = h > h_prev
            momentum_down = h < h_prev
        else:
            momentum_up = False
            momentum_down = False

        if h > 0 and momentum_up:
            macd_votes.append(1)
        elif h < 0 and momentum_down:
            macd_votes.append(-1)
        else:
            macd_votes.append(0)

    # --- RSI 14 ---
    rsi_arr = _rsi_series(closes, RSI_PERIOD)
    rsi_votes = []
    for i in range(n):
        r = rsi_arr[i]
        if r > RSI_LONG:
            rsi_votes.append(1)
        elif r < RSI_SHORT:
            rsi_votes.append(-1)
        else:
            rsi_votes.append(0)

    return ema_votes, macd_votes, rsi_votes


# ════════════════════════ Engine ════════════════════════

class TFHeatmapEngine:
    """Heatmap calculation engine. Thread-safe for GUI usage."""

    def __init__(self, on_progress: Optional[Callable[[str, float], None]] = None):
        self._on_progress = on_progress
        self._cancelled = False
        self._progress_msg = ""
        self._progress_pct = 0.0

    @property
    def progress_msg(self) -> str:
        return self._progress_msg

    @property
    def progress_pct(self) -> float:
        return self._progress_pct

    def cancel(self):
        """Request cancellation. Safe to call from any thread."""
        self._cancelled = True

    def _emit(self, msg: str, pct: float):
        self._progress_msg = msg
        self._progress_pct = pct
        if self._on_progress:
            self._on_progress(msg, pct)

    def compute(self, symbol: str = "BTCUSDT", days_back: int = 30) -> Optional[HeatmapData]:
        """Fetch data and compute all indicator votes for all timeframes.

        Call from a background thread. Returns HeatmapData or None if cancelled.
        """
        self._cancelled = False
        now = datetime.now(timezone.utc)
        end_ms = int(now.timestamp() * 1000)
        start_ms = int((now - timedelta(days=days_back)).timestamp() * 1000)

        tf_count = len(HEATMAP_TIMEFRAMES)
        tf_results = {}

        for ti, tf in enumerate(HEATMAP_TIMEFRAMES):
            if self._cancelled:
                return None

            pct = (ti / tf_count) * 0.9  # 0.0-0.9 for fetching + computing
            self._emit(f"{symbol} {tf} veri aliniyor...", pct)

            # Determine how many candles we need (add warmup for indicators)
            warmup_candles = max(EMA_SLOW, MACD_SLOW + MACD_SIGNAL, RSI_PERIOD + 1) + 10
            warmup_ms = warmup_candles * TF_MINUTES[tf] * 60_000
            fetch_start = start_ms - warmup_ms

            # Fetch klines
            klines = fetch_klines(symbol, tf, fetch_start, end_ms)
            if self._cancelled:
                return None

            if not klines or len(klines) < warmup_candles:
                self._emit(f"{symbol} {tf}: yetersiz veri ({len(klines) if klines else 0} mum)", pct)
                # Store empty data
                tf_results[tf] = TFIndicatorData(
                    timeframe=tf, timestamps=[], ema_votes=[], macd_votes=[], rsi_votes=[],
                )
                continue

            # Parse klines: [open_time, open, high, low, close, volume, ...]
            timestamps = [int(k[0]) for k in klines]
            closes = np.array([float(k[4]) for k in klines])

            # Compute votes for all candles
            ema_v, macd_v, rsi_v = _compute_votes(closes)

            # Trim warmup: only keep candles within [start_ms, end_ms]
            trim_idx = bisect.bisect_left(timestamps, start_ms)
            # Keep one candle before start_ms for forward-fill accuracy
            if trim_idx > 0:
                trim_idx -= 1

            tf_results[tf] = TFIndicatorData(
                timeframe=tf,
                timestamps=timestamps[trim_idx:],
                ema_votes=ema_v[trim_idx:],
                macd_votes=macd_v[trim_idx:],
                rsi_votes=rsi_v[trim_idx:],
            )

            self._emit(f"{symbol} {tf} tamamlandi ({len(timestamps) - trim_idx} mum)", pct + 0.05)
            time.sleep(0.05)  # Rate limit courtesy

        if self._cancelled:
            return None

        self._emit("Heatmap hazirlaniyor...", 0.95)

        # Determine actual data range from 1m timestamps
        if "1m" in tf_results and tf_results["1m"].timestamps:
            actual_start = max(start_ms, tf_results["1m"].timestamps[0])
            actual_end = tf_results["1m"].timestamps[-1]
        else:
            actual_start = start_ms
            actual_end = end_ms

        result = HeatmapData(
            symbol=symbol,
            start_ms=actual_start,
            end_ms=actual_end,
            timeframes=list(HEATMAP_TIMEFRAMES),
            tf_data=tf_results,
        )

        self._emit("Tamamlandi.", 1.0)
        return result
