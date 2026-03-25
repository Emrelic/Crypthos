"""BacktestEngine — core backtest orchestrator for System F.

Reusable from both CLI and GUI. Supports progress callbacks and cancellation.
"""
import bisect
import numpy as np
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from backtest.indicators import (
    ema_val, ema_series, macd_line_series, rsi_val,
    atr_val, adx_val, detect_zigzag,
)
from backtest.data_fetcher import fetch_klines, get_top_symbols
from backtest.simulator import simulate_position


# ════════════════════════ Data Classes ════════════════════════

# Default System F parameters
DEFAULT_SF_PARAMS = {
    "ema_fast": 9, "ema_slow": 21,
    "ema_gap_min_pct": 0.05, "ema_gap_stale_pct": 0.02,
    "macd_fast": 8, "macd_slow": 17, "macd_signal": 9,
    "macd_momentum_required": True,
    "rsi_periyot": 14, "rsi_long_esik": 60, "rsi_short_esik": 40,
    "adx_periyot": 14, "adx_trend_esik": 20,
    "volume_ma_periyot": 20,
    "min_sinyal_gucu": 0.6,
    "vol_tf_min_count": 3, "vol_tf_threshold": 1.5,
    "max_funding_rate": 0.001,
    "swing_n": 10,
    "swing_safety_mult": 1.2, "swing_liq_mult": 2.5,
    "liq_carpani": 0.7, "max_kaldirac": 125,
    "sl_atr_mult": 1.5, "fee_rate": 0.0004,
    "emergency_liq_pct": 80,
    "swing_percentile": 90,
    "p_sl_max_pct": 10.0, "ev_min_pct": 15.0,
    "vol_spike_current_mult": 2.5, "vol_spike_avg3_mult": 2.0,
    "volume_spike_required": True,
    "min_skor": 85, "max_btc_beta": 2.0, "btc_beta_threshold": 0.5,
    "trailing_tp_callback_pct": 0.3, "software_tp_mult": 2.0,
}

DIRECTION_TFS = ["5m", "15m", "1h", "4h", "1d"]
TF_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


@dataclass
class BacktestConfig:
    days_back: int = 30
    check_interval_min: int = 15
    top_coins: int = 15
    lookback: int = 200
    min_tf_uyum: int = 4
    system_params: dict = field(default_factory=lambda: dict(DEFAULT_SF_PARAMS))


@dataclass
class BacktestTrade:
    time_ms: int = 0
    symbol: str = ""
    direction: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    leverage: int = 1
    sl_pct: float = 0.0
    exit_reason: str = ""
    bars_held: int = 0
    hold_str: str = ""
    roi_net: float = 0.0
    score: float = 0.0
    ev_pct: float = 0.0
    p_win: float = 0.0
    strength: float = 0.0
    avg_fwd: float = 0.0
    avg_ret: float = 0.0


@dataclass
class BacktestResult:
    trades: list = field(default_factory=list)
    reject_stats: dict = field(default_factory=dict)
    total_checks: int = 0
    config: BacktestConfig = field(default_factory=BacktestConfig)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def win_count(self) -> int:
        return sum(1 for t in self.trades if t.roi_net > 0)

    @property
    def loss_count(self) -> int:
        return self.total_trades - self.win_count

    @property
    def win_rate(self) -> float:
        return (self.win_count / self.total_trades * 100) if self.trades else 0.0

    @property
    def total_roi(self) -> float:
        return sum(t.roi_net for t in self.trades)

    @property
    def avg_roi(self) -> float:
        return (self.total_roi / self.total_trades) if self.trades else 0.0


# ════════════════════════ TF Analysis ════════════════════════

def _analyze_tf(closes: np.ndarray, volumes: np.ndarray,
                highs: np.ndarray, lows: np.ndarray,
                sf: dict) -> tuple:
    """Single TF 3/3 analysis. Returns (direction, confidence, vol_ratio, adx)."""
    if len(closes) < 30:
        return "FLAT", 0.0, 0.0, 0.0

    price = float(closes[-1])
    if price <= 0:
        return "FLAT", 0.0, 0.0, 0.0

    # EMA
    ef = ema_val(closes, sf["ema_fast"])
    es = ema_val(closes, sf["ema_slow"])
    gap = (ef - es) / price * 100
    ev = 1 if gap > sf["ema_gap_min_pct"] else (-1 if gap < -sf["ema_gap_min_pct"] else 0)

    # MACD
    ms = macd_line_series(closes, sf["macd_fast"], sf["macd_slow"])
    ss = ema_series(ms, sf["macd_signal"])
    hs = ms - ss
    mv = 0
    if len(hs) >= 3:
        h1, h2, h3 = float(hs[-3]), float(hs[-2]), float(hs[-1])
        if sf.get("macd_momentum_required", True):
            if h3 > 0 and h1 < h2 < h3:
                mv = 1
            elif h3 < 0 and h1 > h2 > h3:
                mv = -1
        else:
            mv = 1 if h3 > 0 else (-1 if h3 < 0 else 0)

    # RSI
    r = rsi_val(closes, sf["rsi_periyot"])
    rv = 1 if r > sf["rsi_long_esik"] else (-1 if r < sf["rsi_short_esik"] else 0)

    # Direction
    if ev > 0 and mv > 0 and rv > 0:
        direction = "LONG"
    elif ev < 0 and mv < 0 and rv < 0:
        direction = "SHORT"
    else:
        direction = "FLAT"

    # ADX
    adx = adx_val(highs, lows, closes, sf.get("adx_periyot", 14))

    # Volume ratio
    vp = sf["volume_ma_periyot"]
    vol_ratio = 0.0
    if len(volumes) >= vp + 1:
        vm = float(np.mean(volumes[-(vp + 1):-1]))
        if vm > 0:
            vol_ratio = float(volumes[-1]) / vm

    # Confidence
    conf = 0.0
    if direction != "FLAT":
        ab = min(adx / 50, 1.0) * 0.2 if adx > 20 else 0
        vb = 0.1 if vol_ratio >= sf["vol_tf_threshold"] else 0
        gb = min(abs(gap) / 0.2, 1.0) * 0.1
        conf = min(0.6 + ab + vb + gb, 1.0)

    return direction, conf, vol_ratio, adx


def _full_analysis(klines_by_tf: dict, sf: dict, min_aligned: int = 5,
                   funding_rate: float = 0.0,
                   btc_direction: str = "FLAT",
                   btc_beta: float = 0.8) -> tuple:
    """Full System F analysis. Returns (eligible, result_dict_or_reject_reason)."""

    # 1. TF signals
    tf_results = []
    for tf in DIRECTION_TFS:
        kl = klines_by_tf.get(tf, [])
        if not kl or len(kl) < 30:
            continue
        c = np.array([float(k[4]) for k in kl])
        v = np.array([float(k[5]) for k in kl])
        h = np.array([float(k[2]) for k in kl])
        l = np.array([float(k[3]) for k in kl])
        d, conf, vr, adx = _analyze_tf(c, v, h, l, sf)
        tf_results.append({"tf": tf, "dir": d, "conf": conf,
                           "vol_ratio": vr, "adx": adx})

    if len(tf_results) < min_aligned:
        return False, f"tf_data_{len(tf_results)}/{min_aligned}"

    # 2. Direction alignment
    long_c = sum(1 for t in tf_results if t["dir"] == "LONG")
    short_c = sum(1 for t in tf_results if t["dir"] == "SHORT")
    flat_c = sum(1 for t in tf_results if t["dir"] == "FLAT")

    if long_c >= min_aligned and short_c == 0:
        direction = "LONG"
        aligned = long_c
    elif short_c >= min_aligned and long_c == 0:
        direction = "SHORT"
        aligned = short_c
    else:
        return False, f"align_{long_c}L_{short_c}S_{flat_c}F"

    # Direction strength
    aligned_tfs = [t for t in tf_results if t["dir"] == direction]
    strength = sum(t["conf"] for t in aligned_tfs) / len(aligned_tfs)
    if strength < sf["min_sinyal_gucu"]:
        return False, f"weak_{strength:.2f}"

    # 3. Volume hard filter
    vol_passing = sum(1 for t in tf_results
                      if t["vol_ratio"] >= sf["vol_tf_threshold"])
    if vol_passing < sf["vol_tf_min_count"]:
        return False, f"vol_filter_{vol_passing}/{sf['vol_tf_min_count']}"

    # 4. Funding rate
    max_fr = sf["max_funding_rate"]
    if funding_rate > max_fr and direction == "LONG":
        return False, "high_fr_long"
    if funding_rate < -max_fr and direction == "SHORT":
        return False, "high_fr_short"

    # 5. ATR & Price (5m)
    kl5 = klines_by_tf.get("5m", [])
    if not kl5 or len(kl5) < 30:
        return False, "no_5m"
    c5 = np.array([float(k[4]) for k in kl5])
    h5 = np.array([float(k[2]) for k in kl5])
    l5 = np.array([float(k[3]) for k in kl5])
    price = float(c5[-1])
    atr = atr_val(h5, l5, c5, 14)
    atr_pct = (atr / price * 100) if price > 0 else 0
    if atr_pct <= 0:
        return False, "zero_atr"

    # 6. Orderbook — SKIP (no historical data)

    # 7. BTC alignment
    if (abs(btc_beta) > sf["btc_beta_threshold"]
            and btc_direction not in ("FLAT", "")):
        if direction != btc_direction:
            return False, f"btc_{btc_direction}"

    # 8. Swing analysis (15m primary, 5m fallback)
    swing_n = sf["swing_n"]
    swing_ok = False
    fwd_pcts = ret_pcts = []
    avg_fwd = avg_ret = p90_ret = 0.0

    for stf in ["15m", "5m"]:
        skl = klines_by_tf.get(stf, [])
        if not skl or len(skl) < swing_n * 2 + 10:
            continue
        sh = [float(k[2]) for k in skl]
        sl_list = [float(k[3]) for k in skl]
        sc = [float(k[4]) for k in skl]
        swings = detect_zigzag(sh, sl_list, sc, swing_n)
        if len(swings) < 3:
            continue

        f_pcts, r_pcts = [], []
        for i in range(1, len(swings)):
            pt, ct = swings[i - 1][2], swings[i][2]
            wp = abs(swings[i][1] - swings[i - 1][1]) / price * 100
            if direction == "LONG":
                if pt == 'L' and ct == 'H':
                    f_pcts.append(wp)
                elif pt == 'H' and ct == 'L':
                    r_pcts.append(wp)
            else:
                if pt == 'H' and ct == 'L':
                    f_pcts.append(wp)
                elif pt == 'L' and ct == 'H':
                    r_pcts.append(wp)

        if len(f_pcts) >= 3 and len(r_pcts) >= 3:
            fwd_pcts = f_pcts
            ret_pcts = r_pcts
            avg_fwd = sum(f_pcts) / len(f_pcts)
            avg_ret = sum(r_pcts) / len(r_pcts)
            sorted_ret = sorted(r_pcts)
            p90_ret = sorted_ret[min(int(len(sorted_ret) * 0.9),
                                     len(sorted_ret) - 1)]
            swing_ok = True
            break

    if not swing_ok:
        return False, "no_swings"

    # 9. Smart leverage
    atr_sl = sf["sl_atr_mult"] * atr_pct
    swing_sl = p90_ret * sf["swing_safety_mult"]
    base_sl = max(atr_sl, swing_sl)
    fee_pct = sf["fee_rate"] * 200
    sl_pct = base_sl + fee_pct

    liq_dist = sl_pct * sf["swing_liq_mult"]
    if liq_dist > 0:
        smart_lev = int((sf["liq_carpani"] * 100) / liq_dist)
    else:
        smart_lev = 1
    smart_lev = max(2, min(smart_lev, sf["max_kaldirac"]))

    # Emergency SL
    real_liq = (1.0 / smart_lev) * sf["liq_carpani"] * 100
    emergency_pct = real_liq * (sf["emergency_liq_pct"] / 100.0)

    # 10. Dynamic TP + Trailing
    dynamic_tp_pct = avg_fwd
    dynamic_tp_roi = avg_fwd * smart_lev
    trailing_trigger = avg_fwd
    trailing_callback = max(0.1, min(sf["trailing_tp_callback_pct"], 1.0))
    target_roi = avg_fwd * sf["software_tp_mult"] * smart_lev

    # 11. Fee
    fee_roi = round(sf["fee_rate"] * 200 * smart_lev, 2)

    # 12. P(win), P(SL), EV
    tp_hits = sum(1 for f in fwd_pcts if f >= dynamic_tp_pct)
    p_fwd = tp_hits / len(fwd_pcts) if fwd_pcts else 0
    sl_hits = sum(1 for r in ret_pcts if r >= sl_pct)
    p_ret = sl_hits / len(ret_pcts) if ret_pcts else 0

    p_win_c = p_fwd
    p_loss_c = (1 - p_fwd) * p_ret
    denom = p_win_c + p_loss_c
    if denom > 0:
        p_win = p_win_c / denom
        p_loss = p_loss_c / denom
    else:
        p_win, p_loss = 0.3, 0.3

    tp_roi_net = dynamic_tp_roi - fee_roi
    sl_roi_net = sl_pct * smart_lev + fee_roi
    ev_pct = round(p_win * tp_roi_net - p_loss * sl_roi_net, 2)

    if p_loss > sf["p_sl_max_pct"] / 100:
        return False, f"p_sl_{p_loss * 100:.0f}%|ev={ev_pct:.1f}%"
    if ev_pct < sf["ev_min_pct"]:
        return False, f"ev_{ev_pct:.1f}%|p_w={p_win * 100:.0f}%"

    # 13. Volume spike (5m proxy)
    if sf.get("volume_spike_required", True):
        vols = np.array([float(k[5]) for k in kl5])
        vma_p = sf["volume_ma_periyot"]
        spike = False
        vol_ratio_1m = 0.0
        if len(vols) >= vma_p + 3:
            vma = float(np.mean(vols[-(vma_p + 3):-3]))
            if vma > 0:
                vc = float(vols[-1])
                va3 = float(np.mean(vols[-3:]))
                vol_ratio_1m = vc / vma
                spike = (vol_ratio_1m >= 2.0) and (va3 / vma >= 1.5)
                if spike:
                    co, cc = float(kl5[-1][1]), float(kl5[-1][4])
                    if direction == "LONG" and cc <= co:
                        spike = False
                    elif direction == "SHORT" and cc >= co:
                        spike = False
        if not spike:
            return False, f"no_spike_{vol_ratio_1m:.1f}x"

    # 14. Composite score
    score = 0.0
    score += strength * 35.0
    if ev_pct > 0:
        score += min(ev_pct / 50, 1.0) * 25.0
    score += min(p_win, 1.0) * 20.0
    avg_vr = sum(t["vol_ratio"] for t in tf_results) / len(tf_results)
    score += min(avg_vr / 5.0, 1.0) * 10.0
    adx_1h = next((t["adx"] for t in tf_results if t["tf"] == "1h"), 0)
    if adx_1h > sf["adx_trend_esik"]:
        score += min((adx_1h - sf["adx_trend_esik"]) / 30, 1.0) * 5.0
    if direction == "LONG" and funding_rate <= 0:
        score += 5.0
    elif direction == "SHORT" and funding_rate >= 0:
        score += 5.0
    elif abs(funding_rate) < 0.0003:
        score += 2.5
    score = round(min(score, 100), 1)

    if score < sf["min_skor"]:
        return False, f"score_{score:.0f}"

    # 15. BTC beta excess
    if abs(btc_beta) > sf["max_btc_beta"]:
        return False, f"beta_{btc_beta:.1f}"

    # ELIGIBLE
    return True, {
        "direction": direction, "aligned": aligned, "price": price,
        "sl_pct": sl_pct, "smart_lev": smart_lev,
        "emergency_pct": emergency_pct,
        "trailing_trigger": trailing_trigger,
        "trailing_callback": trailing_callback,
        "dynamic_tp_pct": dynamic_tp_pct, "dynamic_tp_roi": dynamic_tp_roi,
        "target_roi": target_roi, "fee_roi": fee_roi,
        "ev_pct": ev_pct, "p_win": p_win * 100, "p_loss": p_loss * 100,
        "score": score, "strength": strength, "atr_pct": atr_pct,
        "avg_fwd": avg_fwd, "avg_ret": avg_ret, "p90_ret": p90_ret,
        "tf_details": tf_results,
    }


# ════════════════════════ Engine ════════════════════════

class BacktestEngine:
    """Core backtest orchestrator. Thread-safe for GUI usage."""

    def __init__(self, config: BacktestConfig,
                 on_progress: Optional[Callable[[str, float], None]] = None):
        self._config = config
        self._on_progress = on_progress
        self._cancelled = False
        self._result: Optional[BacktestResult] = None
        self._progress_msg = ""
        self._progress_pct = 0.0

    @property
    def result(self) -> Optional[BacktestResult]:
        return self._result

    @property
    def progress_msg(self) -> str:
        return self._progress_msg

    @property
    def progress_pct(self) -> float:
        return self._progress_pct

    def cancel(self):
        self._cancelled = True

    def _emit(self, msg: str, pct: float):
        self._progress_msg = msg
        self._progress_pct = pct
        if self._on_progress:
            self._on_progress(msg, pct)

    def run(self) -> BacktestResult:
        """Run full backtest. Call from background thread."""
        cfg = self._config
        sf = cfg.system_params
        now = datetime.now(timezone.utc)
        end_ms = int(now.timestamp() * 1000)
        start_ms = int((now - timedelta(days=cfg.days_back)).timestamp() * 1000)

        # 1. Get top symbols
        self._emit("Coin listesi aliniyor...", 0.0)
        symbols = get_top_symbols(cfg.top_coins)
        if "BTCUSDT" not in symbols:
            symbols.insert(0, "BTCUSDT")
        if self._cancelled:
            return self._empty_result()

        # 2. Fetch data
        all_data = {}
        total_fetches = len(symbols) * len(DIRECTION_TFS)
        fetch_done = 0
        for sym in symbols:
            if self._cancelled:
                return self._empty_result()
            all_data[sym] = {}
            for tf in DIRECTION_TFS:
                tf_min = TF_MINUTES[tf]
                warmup = cfg.lookback * tf_min * 60 * 1000
                all_data[sym][tf] = fetch_klines(
                    sym, tf, start_ms - warmup, end_ms)
                fetch_done += 1
                pct = 0.3 * fetch_done / total_fetches
                self._emit(f"Veri: {sym} {tf} ({fetch_done}/{total_fetches})",
                           pct)
                time.sleep(0.06)

        # 3. Pre-index timestamps
        sym_tf_ts = {}
        for sym in symbols:
            for tf in DIRECTION_TFS:
                sym_tf_ts[(sym, tf)] = [
                    int(k[0]) for k in all_data[sym].get(tf, [])]

        # 4. BTC direction lookup
        btc_1h = all_data.get("BTCUSDT", {}).get("1h", [])
        btc_dir_by_ts = {}
        if btc_1h:
            for i in range(cfg.lookback, len(btc_1h)):
                window = btc_1h[max(0, i - cfg.lookback):i]
                c = np.array([float(k[4]) for k in window])
                v = np.array([float(k[5]) for k in window])
                h = np.array([float(k[2]) for k in window])
                l = np.array([float(k[3]) for k in window])
                d, _, _, _ = _analyze_tf(c, v, h, l, sf)
                btc_dir_by_ts[int(btc_1h[i][0])] = d
        btc_ts_sorted = sorted(btc_dir_by_ts.keys())

        def get_btc_dir(ts):
            idx = bisect.bisect_right(btc_ts_sorted, ts) - 1
            return btc_dir_by_ts[btc_ts_sorted[idx]] if idx >= 0 else "FLAT"

        # 5. Rolling window analysis
        check_ms = cfg.check_interval_min * 60 * 1000
        check_time = start_ms
        total_checks = 0
        total_expected = int((end_ms - start_ms) / check_ms)

        signals = []
        reject_stats = {}

        self._emit("Tarama basliyor...", 0.3)

        while check_time <= end_ms:
            if self._cancelled:
                return self._empty_result()
            total_checks += 1
            btc_dir = get_btc_dir(check_time)

            for sym in symbols:
                klines_window = {}
                skip = False
                for tf in DIRECTION_TFS:
                    ts_list = sym_tf_ts.get((sym, tf), [])
                    idx = bisect.bisect_left(ts_list, check_time)
                    if idx < cfg.lookback:
                        skip = True
                        break
                    klines_window[tf] = all_data[sym][tf][idx - cfg.lookback:idx]

                if skip:
                    continue

                eligible, result = _full_analysis(
                    klines_window, sf, min_aligned=cfg.min_tf_uyum,
                    funding_rate=0.0,
                    btc_direction=btc_dir, btc_beta=0.8)

                if eligible:
                    signals.append({
                        "time": check_time, "symbol": sym, **result})
                else:
                    reason = result.split("|")[0] if isinstance(result, str) else "?"
                    rkey = reason.split("_")[0] if "_" in reason else reason
                    reject_stats[rkey] = reject_stats.get(rkey, 0) + 1

            if total_checks % 100 == 0:
                pct = 0.3 + 0.6 * total_checks / max(total_expected, 1)
                self._emit(
                    f"Tarama: {total_checks}/{total_expected} "
                    f"({len(signals)} sinyal)", min(pct, 0.9))

            check_time += check_ms

        # 6. Position simulation
        self._emit("Pozisyon simulasyonu...", 0.9)
        trades = []
        for sig in signals:
            sym = sig["symbol"]
            entry_time = sig["time"]
            fwd_klines = [k for k in all_data[sym].get("5m", [])
                          if int(k[0]) >= entry_time]

            exit_reason, exit_price, bars, roi_net, _ = simulate_position(
                sig["direction"], sig["price"], sig["sl_pct"],
                sig["emergency_pct"], sig["trailing_trigger"],
                sig["trailing_callback"], sig["smart_lev"],
                sf["fee_rate"], fwd_klines)

            hold_min = bars * 5
            if hold_min >= 60:
                hold_str = f"{hold_min // 60}s {hold_min % 60}dk"
            else:
                hold_str = f"{hold_min}dk"

            trades.append(BacktestTrade(
                time_ms=entry_time, symbol=sym,
                direction=sig["direction"],
                entry_price=sig["price"], exit_price=exit_price,
                leverage=sig["smart_lev"], sl_pct=sig["sl_pct"],
                exit_reason=exit_reason, bars_held=bars,
                hold_str=hold_str, roi_net=round(roi_net, 2),
                score=sig["score"], ev_pct=sig["ev_pct"],
                p_win=sig["p_win"], strength=sig["strength"],
                avg_fwd=sig["avg_fwd"], avg_ret=sig["avg_ret"],
            ))

        self._result = BacktestResult(
            trades=trades, reject_stats=reject_stats,
            total_checks=total_checks, config=cfg)
        self._emit("Tamamlandi!", 1.0)
        return self._result

    def _empty_result(self) -> BacktestResult:
        self._result = BacktestResult(config=self._config)
        self._emit("Iptal edildi.", 0.0)
        return self._result
