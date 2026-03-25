"""Shared indicator functions for backtesting.

All functions match System F scanner logic exactly.
"""
import numpy as np


def ema_val(data: np.ndarray, period: int) -> float:
    if len(data) < period:
        return float(np.mean(data)) if len(data) > 0 else 0.0
    k = 2.0 / (period + 1)
    e = float(data[0])
    for v in data[1:]:
        e = v * k + e * (1 - k)
    return e


def ema_series(data: np.ndarray, period: int) -> np.ndarray:
    if len(data) == 0:
        return np.array([0.0])
    k = 2.0 / (period + 1)
    r = np.empty(len(data))
    r[0] = float(data[0])
    for i in range(1, len(data)):
        r[i] = float(data[i]) * k + r[i - 1] * (1 - k)
    return r


def macd_line_series(closes: np.ndarray, fast: int, slow: int) -> np.ndarray:
    if len(closes) < slow:
        return np.array([0.0])
    kf, ks = 2.0 / (fast + 1), 2.0 / (slow + 1)
    ef = es = float(closes[0])
    s = []
    for v in closes:
        ef = v * kf + ef * (1 - kf)
        es = v * ks + es * (1 - ks)
        s.append(ef - es)
    return np.array(s)


def rsi_val(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    d = np.diff(closes)
    g = np.where(d > 0, d, 0.0)
    l = np.where(d < 0, -d, 0.0)
    ag = np.mean(g[:period])
    al = np.mean(l[:period])
    for i in range(period, len(g)):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def atr_val(highs: np.ndarray, lows: np.ndarray,
            closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    tr = []
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i],
                      abs(highs[i] - closes[i - 1]),
                      abs(lows[i] - closes[i - 1])))
    if not tr:
        return 0.0
    if len(tr) < period:
        return float(np.mean(tr))
    a = float(np.mean(tr[:period]))
    for i in range(period, len(tr)):
        a = (a * (period - 1) + tr[i]) / period
    return a


def adx_val(highs: np.ndarray, lows: np.ndarray,
            closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period * 2:
        return 0.0
    pdm, mdm, trl = [], [], []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        pdm.append(up if up > down and up > 0 else 0.0)
        mdm.append(down if down > up and down > 0 else 0.0)
        trl.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    if len(trl) < period:
        return 0.0
    at = sum(trl[:period])
    ps = sum(pdm[:period])
    ms = sum(mdm[:period])
    dx = []
    for i in range(period, len(trl)):
        at = at - at / period + trl[i]
        ps = ps - ps / period + pdm[i]
        ms = ms - ms / period + mdm[i]
        if at == 0:
            continue
        pdi = 100 * ps / at
        mdi = 100 * ms / at
        s = pdi + mdi
        if s == 0:
            continue
        dx.append(100 * abs(pdi - mdi) / s)
    if not dx:
        return 0.0
    if len(dx) < period:
        return float(np.mean(dx))
    a = float(np.mean(dx[:period]))
    for i in range(period, len(dx)):
        a = (a * (period - 1) + dx[i]) / period
    return a


def detect_zigzag(highs: list, lows: list, closes: list,
                  n: int = 10) -> list:
    """Zigzag swing detection. Returns [(index, price, 'H'/'L'), ...]"""
    if len(closes) < n * 2:
        return []
    swings = []
    i = n
    while i < len(closes) - n:
        is_h = all(highs[i] >= highs[i - j] for j in range(1, n + 1))
        is_h = is_h and all(
            highs[i] >= highs[i + j]
            for j in range(1, min(n + 1, len(closes) - i)))
        is_l = all(lows[i] <= lows[i - j] for j in range(1, n + 1))
        is_l = is_l and all(
            lows[i] <= lows[i + j]
            for j in range(1, min(n + 1, len(closes) - i)))

        if is_h and is_l:
            if swings and swings[-1][2] == 'L':
                swings.append((i, highs[i], 'H'))
            else:
                swings.append((i, lows[i], 'L'))
        elif is_h:
            if not swings or swings[-1][2] != 'H':
                swings.append((i, highs[i], 'H'))
            elif highs[i] > swings[-1][1]:
                swings[-1] = (i, highs[i], 'H')
        elif is_l:
            if not swings or swings[-1][2] != 'L':
                swings.append((i, lows[i], 'L'))
            elif lows[i] < swings[-1][1]:
                swings[-1] = (i, lows[i], 'L')
        i += 1
    return swings
