"""Position simulation for backtesting."""


def simulate_position(direction: str, entry_price: float,
                      sl_pct: float, emergency_pct: float,
                      trailing_trigger_pct: float, trailing_callback_pct: float,
                      smart_lev: int, fee_rate: float,
                      forward_klines_5m: list) -> tuple:
    """Simulate a position forward using 5m candles.

    Returns: (exit_reason, exit_price, bars_held, roi_net, roi_gross)
    """
    if not forward_klines_5m:
        return "no_data", entry_price, 0, 0.0, 0.0

    if direction == "LONG":
        sl_price = entry_price * (1 - sl_pct / 100)
        emg_price = entry_price * (1 - emergency_pct / 100)
    else:
        sl_price = entry_price * (1 + sl_pct / 100)
        emg_price = entry_price * (1 + emergency_pct / 100)

    trailing_active = False
    peak_price = entry_price
    time_limit_bars = 8 * 12  # 8h = 96 bars (5m)
    fee_total = fee_rate * 200 * smart_lev

    for i, k in enumerate(forward_klines_5m):
        high = float(k[2])
        low = float(k[3])
        close = float(k[4])

        if direction == "LONG":
            if low <= emg_price:
                pnl = (emg_price - entry_price) / entry_price * 100 * smart_lev
                return "EMERGENCY", emg_price, i + 1, pnl - fee_total, pnl
            if low <= sl_price:
                pnl = (sl_price - entry_price) / entry_price * 100 * smart_lev
                return "SL", sl_price, i + 1, pnl - fee_total, pnl
            if high > peak_price:
                peak_price = high
            move_pct = (peak_price - entry_price) / entry_price * 100
            if move_pct >= trailing_trigger_pct:
                trailing_active = True
            if trailing_active:
                retrace = (peak_price - low) / peak_price * 100
                if retrace >= trailing_callback_pct:
                    exit_p = peak_price * (1 - trailing_callback_pct / 100)
                    pnl = (exit_p - entry_price) / entry_price * 100 * smart_lev
                    return "TRAILING", exit_p, i + 1, pnl - fee_total, pnl
        else:
            if high >= emg_price:
                pnl = (entry_price - emg_price) / entry_price * 100 * smart_lev
                return "EMERGENCY", emg_price, i + 1, pnl - fee_total, pnl
            if high >= sl_price:
                pnl = (entry_price - sl_price) / entry_price * 100 * smart_lev
                return "SL", sl_price, i + 1, pnl - fee_total, pnl
            if low < peak_price:
                peak_price = low
            move_pct = (entry_price - peak_price) / entry_price * 100
            if move_pct >= trailing_trigger_pct:
                trailing_active = True
            if trailing_active:
                retrace = (high - peak_price) / peak_price * 100
                if retrace >= trailing_callback_pct:
                    exit_p = peak_price * (1 + trailing_callback_pct / 100)
                    pnl = (entry_price - exit_p) / entry_price * 100 * smart_lev
                    return "TRAILING", exit_p, i + 1, pnl - fee_total, pnl

        if i + 1 >= time_limit_bars:
            if direction == "LONG":
                pnl = (close - entry_price) / entry_price * 100 * smart_lev
            else:
                pnl = (entry_price - close) / entry_price * 100 * smart_lev
            return "TIME_LIMIT", close, i + 1, pnl - fee_total, pnl

    close = float(forward_klines_5m[-1][4])
    if direction == "LONG":
        pnl = (close - entry_price) / entry_price * 100 * smart_lev
    else:
        pnl = (entry_price - close) / entry_price * 100 * smart_lev
    return "DATA_END", close, len(forward_klines_5m), pnl - fee_total, pnl
