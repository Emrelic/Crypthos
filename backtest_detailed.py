"""System N Detayli Filtre Backtest Raporu.

Mevcut durum vs filtreli durum karsilastirmasi.
Her filtre paketi icin: engellenen karlar, engellenen zararlar,
yuzdelik etkiler, LONG/SHORT ayri, rejim ayri, exit reason ayri.
"""

import json
import sys
import os

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from collections import defaultdict


def load_trades():
    with open("data/system_n_analysis.json", "r") as f:
        data = json.load(f)
    return data["trades"]


# ─── Filtre Tanimlari ──────────────────────────────────────────

FILTERS = {
    "RANGING_REJECT": {
        "ad": "SYNCED:RANGING rejimi reddi",
        "aciklama": "SYNCED:RANGING rejiminde hic trade acma",
        "fn": lambda t: t["regime"] != "SYNCED:RANGING",
    },
    "MACD_ALIGN": {
        "ad": "MACD histogram yon uyumu",
        "aciklama": "LONG: histogram > 0, SHORT: histogram < 0",
        "fn": lambda t: (t["side"] == "Buy/Long" and t["macd_histogram"] > 0) or
                        (t["side"] == "Sell/Short" and t["macd_histogram"] < 0),
    },
    "RSI_ALIGN": {
        "ad": "RSI yon uyumu",
        "aciklama": "LONG: RSI > 40, SHORT: RSI < 60",
        "fn": lambda t: (t["side"] == "Buy/Long" and t["rsi"] > 40) or
                        (t["side"] == "Sell/Short" and t["rsi"] < 60),
    },
    "ER_02": {
        "ad": "Efficiency Ratio > 0.2",
        "aciklama": "ER < 0.2 olan coinleri ele (random walk)",
        "fn": lambda t: t["er"] > 0.2,
    },
    "ER_03": {
        "ad": "Efficiency Ratio > 0.3",
        "aciklama": "ER < 0.3 olan coinleri ele (daha siki)",
        "fn": lambda t: t["er"] > 0.3,
    },
    "OBV_ALIGN": {
        "ad": "OBV yon uyumu",
        "aciklama": "LONG: OBV > SMA20, SHORT: OBV < SMA20",
        "fn": lambda t: (t["side"] == "Buy/Long" and t["obv_above_sma"]) or
                        (t["side"] == "Sell/Short" and not t["obv_above_sma"]),
    },
    "ADX_LT35": {
        "ad": "ADX < 35",
        "aciklama": "Asiri guclu trend (ADX>35) filtreleme",
        "fn": lambda t: t["adx"] < 35,
    },
    "BB_POS": {
        "ad": "BB Position 0.15-0.85",
        "aciklama": "Bollinger band kenarlarinda islem acma",
        "fn": lambda t: 0.15 < t["bb_position"] < 0.85,
    },
    "EMA_ALIGN": {
        "ad": "EMA trend hizalanmasi",
        "aciklama": "LONG: EMA9>21>50 (bullish), SHORT: EMA9<21<50 (bearish)",
        "fn": lambda t: (t["side"] == "Buy/Long" and t["bullish_aligned"]) or
                        (t["side"] == "Sell/Short" and t["bearish_aligned"]),
    },
}

PACKAGES = {
    "PAKET A": {
        "ad": "MACD + RSI + ER>0.2",
        "filtreler": ["RANGING_REJECT", "MACD_ALIGN", "RSI_ALIGN", "ER_02"],
    },
    "PAKET B": {
        "ad": "MACD + RSI + ER>0.3",
        "filtreler": ["RANGING_REJECT", "MACD_ALIGN", "RSI_ALIGN", "ER_03"],
    },
    "PAKET C": {
        "ad": "Sadece MACD + RSI",
        "filtreler": ["RANGING_REJECT", "MACD_ALIGN", "RSI_ALIGN"],
    },
    "PAKET D": {
        "ad": "MACD + RSI + ER>0.2 + OBV",
        "filtreler": ["RANGING_REJECT", "MACD_ALIGN", "RSI_ALIGN", "ER_02", "OBV_ALIGN"],
    },
    "PAKET E": {
        "ad": "ER>0.2 + MACD (RSI'siz)",
        "filtreler": ["RANGING_REJECT", "MACD_ALIGN", "ER_02"],
    },
    "PAKET F": {
        "ad": "MACD + RSI + ER>0.2 + ADX<35",
        "filtreler": ["RANGING_REJECT", "MACD_ALIGN", "RSI_ALIGN", "ER_02", "ADX_LT35"],
    },
    "PAKET G": {
        "ad": "EMA Aligned + RSI + ER>0.2",
        "filtreler": ["RANGING_REJECT", "EMA_ALIGN", "RSI_ALIGN", "ER_02"],
    },
}


def apply_filters(trades, filter_keys):
    """Filtreleri uygula, gecen ve elenen trade'leri dondur."""
    passed = []
    rejected = []
    for t in trades:
        reject_reason = None
        for fk in filter_keys:
            if not FILTERS[fk]["fn"](t):
                reject_reason = fk
                break
        if reject_reason:
            rejected.append((t, reject_reason))
        else:
            passed.append(t)
    return passed, rejected


def fmt_pnl(v):
    return f"{v:+.4f}"


def fmt_pct(v):
    return f"{v:+.1f}%"


def print_header(title):
    print(f"\n{'=' * 100}")
    print(f"  {title}")
    print(f"{'=' * 100}")


def print_subheader(title):
    print(f"\n  --- {title} ---")


def stats(trades):
    """Temel istatistikler."""
    total = len(trades)
    if total == 0:
        return {"total": 0, "wins": 0, "losses": 0, "wr": 0, "net": 0,
                "gross_profit": 0, "gross_loss": 0, "avg_win": 0, "avg_loss": 0,
                "max_win": 0, "max_loss": 0, "avg_hold_w": 0, "avg_hold_l": 0,
                "avg_lev": 0, "profit_factor": 0}
    wins = [t for t in trades if t["pnl_usdt"] > 0]
    losses = [t for t in trades if t["pnl_usdt"] <= 0]
    gp = sum(t["pnl_usdt"] for t in wins)
    gl = sum(t["pnl_usdt"] for t in losses)
    return {
        "total": total,
        "wins": len(wins),
        "losses": len(losses),
        "wr": len(wins) / total * 100,
        "net": gp + gl,
        "gross_profit": gp,
        "gross_loss": gl,
        "avg_win": gp / len(wins) if wins else 0,
        "avg_loss": gl / len(losses) if losses else 0,
        "max_win": max(t["pnl_usdt"] for t in wins) if wins else 0,
        "max_loss": min(t["pnl_usdt"] for t in losses) if losses else 0,
        "avg_hold_w": sum(t["hold_seconds"] for t in wins) / len(wins) if wins else 0,
        "avg_hold_l": sum(t["hold_seconds"] for t in losses) / len(losses) if losses else 0,
        "avg_lev": sum(t["leverage"] for t in trades) / total,
        "profit_factor": gp / abs(gl) if gl != 0 else 999,
        "avg_roi_w": sum(t["roi_pct"] for t in wins) / len(wins) if wins else 0,
        "avg_roi_l": sum(t["roi_pct"] for t in losses) / len(losses) if losses else 0,
    }


def print_stats_block(s, label=""):
    if label:
        print(f"\n  [{label}]")
    print(f"    Toplam Trade     : {s['total']}")
    print(f"    Kazanan          : {s['wins']}  ({s['wr']:.1f}%)")
    print(f"    Kaybeden         : {s['losses']}  ({100-s['wr']:.1f}%)")
    print(f"    Net PnL          : {fmt_pnl(s['net'])} USDT")
    print(f"    Brut Kar         : {fmt_pnl(s['gross_profit'])} USDT")
    print(f"    Brut Zarar       : {fmt_pnl(s['gross_loss'])} USDT")
    print(f"    Profit Factor    : {s['profit_factor']:.2f}")
    print(f"    Ort. Kazanc      : {fmt_pnl(s['avg_win'])} USDT  (ROI: {s.get('avg_roi_w',0):+.1f}%)")
    print(f"    Ort. Kayip       : {fmt_pnl(s['avg_loss'])} USDT  (ROI: {s.get('avg_roi_l',0):+.1f}%)")
    print(f"    Max Kazanc       : {fmt_pnl(s['max_win'])} USDT")
    print(f"    Max Kayip        : {fmt_pnl(s['max_loss'])} USDT")
    print(f"    Ort. Hold (Win)  : {s['avg_hold_w']:.0f}s ({s['avg_hold_w']/60:.1f}dk)")
    print(f"    Ort. Hold (Loss) : {s['avg_hold_l']:.0f}s ({s['avg_hold_l']/60:.1f}dk)")
    print(f"    Ort. Kaldirac    : {s['avg_lev']:.1f}x")


def print_trade_table(trades_with_reason, title, max_rows=None, show_reason=True):
    """Trade listesini tablo olarak yazdir."""
    if not trades_with_reason:
        print(f"\n  {title}: (yok)")
        return

    print(f"\n  {title} ({len(trades_with_reason)} adet):")

    if show_reason:
        hdr = f"    {'#':>3}  {'Sembol':<14} {'Yon':<11} {'PnL USDT':>10} {'ROI%':>8} {'PnL%':>7} {'Lev':>4} {'Hold':>8} {'Cikis Nedeni':<18} {'Engel Filtre':<20} {'RSI':>5} {'ADX':>5} {'ER':>5} {'MACD_H':>10} {'MFI':>5} {'BB_P':>5} {'V.Rat':>5}"
    else:
        hdr = f"    {'#':>3}  {'Sembol':<14} {'Yon':<11} {'PnL USDT':>10} {'ROI%':>8} {'PnL%':>7} {'Lev':>4} {'Hold':>8} {'Cikis Nedeni':<18} {'RSI':>5} {'ADX':>5} {'ER':>5} {'MACD_H':>10} {'MFI':>5} {'BB_P':>5} {'V.Rat':>5}"
    print(hdr)
    print(f"    {'-' * (len(hdr) - 4)}")

    items = trades_with_reason[:max_rows] if max_rows else trades_with_reason
    for i, item in enumerate(items):
        if isinstance(item, tuple):
            t, reason = item
            reason_name = FILTERS[reason]["ad"][:18] if reason in FILTERS else reason[:18]
        else:
            t = item
            reason_name = ""

        hold_str = f"{t['hold_seconds']/60:.0f}dk" if t['hold_seconds'] > 60 else f"{t['hold_seconds']:.0f}s"

        if show_reason:
            print(f"    {i+1:>3}  {t['symbol']:<14} {t['side']:<11} {t['pnl_usdt']:>+10.4f} {t['roi_pct']:>+7.1f}% {t['pnl_pct']:>+6.1f}% {t['leverage']:>4} {hold_str:>8} {t['exit_reason']:<18} {reason_name:<20} {t['rsi']:>5.1f} {t['adx']:>5.1f} {t['er']:>5.3f} {t['macd_histogram']:>+10.6f} {t['mfi']:>5.1f} {t['bb_position']:>5.3f} {t['volume_ratio']:>5.2f}")
        else:
            print(f"    {i+1:>3}  {t['symbol']:<14} {t['side']:<11} {t['pnl_usdt']:>+10.4f} {t['roi_pct']:>+7.1f}% {t['pnl_pct']:>+6.1f}% {t['leverage']:>4} {hold_str:>8} {t['exit_reason']:<18} {t['rsi']:>5.1f} {t['adx']:>5.1f} {t['er']:>5.3f} {t['macd_histogram']:>+10.6f} {t['mfi']:>5.1f} {t['bb_position']:>5.3f} {t['volume_ratio']:>5.2f}")

    if max_rows and len(trades_with_reason) > max_rows:
        remaining = trades_with_reason[max_rows:]
        rem_pnl = sum((t[0] if isinstance(t, tuple) else t)["pnl_usdt"] for t in remaining)
        print(f"    ... ve {len(remaining)} trade daha (toplam: {fmt_pnl(rem_pnl)} USDT)")


def analyze_package(trades, pkg_key, pkg_info):
    """Bir filtre paketi icin tam analiz."""
    filter_keys = pkg_info["filtreler"]
    passed, rejected = apply_filters(trades, filter_keys)

    s_orig = stats(trades)
    s_new = stats(passed)

    # Elenenler
    rej_wins = [(t, r) for t, r in rejected if t["pnl_usdt"] > 0]
    rej_losses = [(t, r) for t, r in rejected if t["pnl_usdt"] <= 0]
    rej_wins_pnl = sum(t["pnl_usdt"] for t, _ in rej_wins)
    rej_losses_pnl = sum(t["pnl_usdt"] for t, _ in rej_losses)

    # Buyukten kucuge sirala
    rej_wins_sorted = sorted(rej_wins, key=lambda x: x[0]["pnl_usdt"], reverse=True)
    rej_losses_sorted = sorted(rej_losses, key=lambda x: x[0]["pnl_usdt"])

    print_header(f"{pkg_key}: {pkg_info['ad']}")

    # Hangi filtreler uygulaniyor
    print(f"\n  Uygulanan Filtreler:")
    for fk in filter_keys:
        f = FILTERS[fk]
        print(f"    [{fk}] {f['ad']}: {f['aciklama']}")

    # ═══════════ KARSILASTIRMA TABLOSU ═══════════
    print_subheader("ONCESI / SONRASI KARSILASTIRMASI")

    metrics = [
        ("Toplam Trade", s_orig["total"], s_new["total"]),
        ("Kazanan", s_orig["wins"], s_new["wins"]),
        ("Kaybeden", s_orig["losses"], s_new["losses"]),
        ("Win Rate", f"{s_orig['wr']:.1f}%", f"{s_new['wr']:.1f}%"),
        ("Net PnL", f"{s_orig['net']:+.4f}", f"{s_new['net']:+.4f}"),
        ("Brut Kar", f"{s_orig['gross_profit']:+.4f}", f"{s_new['gross_profit']:+.4f}"),
        ("Brut Zarar", f"{s_orig['gross_loss']:+.4f}", f"{s_new['gross_loss']:+.4f}"),
        ("Profit Factor", f"{s_orig['profit_factor']:.2f}", f"{s_new['profit_factor']:.2f}"),
        ("Ort. Kazanc", f"{s_orig['avg_win']:+.4f}", f"{s_new['avg_win']:+.4f}"),
        ("Ort. Kayip", f"{s_orig['avg_loss']:+.4f}", f"{s_new['avg_loss']:+.4f}"),
        ("Max Kazanc", f"{s_orig['max_win']:+.4f}", f"{s_new['max_win']:+.4f}"),
        ("Max Kayip", f"{s_orig['max_loss']:+.4f}", f"{s_new['max_loss']:+.4f}"),
        ("Ort. Kaldirac", f"{s_orig['avg_lev']:.1f}x", f"{s_new['avg_lev']:.1f}x"),
    ]

    print(f"\n    {'Metrik':<20} {'ONCESI':>15} {'SONRASI':>15} {'DEGISIM':>15}")
    print(f"    {'-' * 65}")
    for name, before, after in metrics:
        # Degisim hesapla
        try:
            b = float(str(before).replace('%', '').replace('x', '').replace('+', ''))
            a = float(str(after).replace('%', '').replace('x', '').replace('+', ''))
            if '%' in str(before):
                diff = f"{a-b:+.1f}pp"
            elif 'x' in str(before):
                diff = f"{a-b:+.1f}x"
            else:
                diff = f"{a-b:+.4f}"
        except:
            diff = "-"
        print(f"    {name:<20} {str(before):>15} {str(after):>15} {diff:>15}")

    # ═══════════ YUZDELIK ETKI ═══════════
    print_subheader("YUZDELIK ETKI OZETI")
    pnl_change = s_new["net"] - s_orig["net"]
    pnl_change_pct = (pnl_change / abs(s_orig["net"]) * 100) if s_orig["net"] != 0 else 0
    trade_reduction = (1 - len(passed) / len(trades)) * 100
    loss_reduction = (1 - s_new["losses"] / s_orig["losses"]) * 100 if s_orig["losses"] > 0 else 0
    win_reduction = (1 - s_new["wins"] / s_orig["wins"]) * 100 if s_orig["wins"] > 0 else 0
    zarar_reduction = (1 - abs(s_new["gross_loss"]) / abs(s_orig["gross_loss"])) * 100 if s_orig["gross_loss"] != 0 else 0
    kar_reduction = (1 - s_new["gross_profit"] / s_orig["gross_profit"]) * 100 if s_orig["gross_profit"] > 0 else 0

    print(f"    Trade azalma       : %{trade_reduction:.1f} ({s_orig['total']} => {s_new['total']})")
    print(f"    Win Rate artisi    : {s_new['wr'] - s_orig['wr']:+.1f}pp ({s_orig['wr']:.1f}% => {s_new['wr']:.1f}%)")
    print(f"    Net PnL degisimi   : {fmt_pnl(pnl_change)} USDT ({pnl_change_pct:+.1f}%)")
    print(f"    Zarar azalma       : %{zarar_reduction:.1f} ({fmt_pnl(s_orig['gross_loss'])} => {fmt_pnl(s_new['gross_loss'])})")
    print(f"    Kar azalma         : %{kar_reduction:.1f} ({fmt_pnl(s_orig['gross_profit'])} => {fmt_pnl(s_new['gross_profit'])})")
    print(f"    Loss sayisi azalma : %{loss_reduction:.1f} ({s_orig['losses']} => {s_new['losses']})")
    print(f"    Win sayisi azalma  : %{win_reduction:.1f} ({s_orig['wins']} => {s_new['wins']})")
    print(f"    Profit Factor      : {s_orig['profit_factor']:.2f} => {s_new['profit_factor']:.2f}")
    # Engellenen zarar / kaybedilen kar orani
    if rej_wins_pnl > 0:
        ratio = abs(rej_losses_pnl) / rej_wins_pnl
        print(f"    Engel Zarar/Kar    : {ratio:.2f}x (her 1 USDT kaybedilen kara {ratio:.2f} USDT zarar engeli)")
    print(f"    Net Kazanc         : {fmt_pnl(abs(rej_losses_pnl) - rej_wins_pnl)} USDT")

    # ═══════════ FILTRE BAZINDA KIRILIM ═══════════
    print_subheader("FILTRE BAZINDA KIRILIM")

    filter_breakdown = defaultdict(lambda: {"wins": [], "losses": []})
    for t, reason in rejected:
        if t["pnl_usdt"] > 0:
            filter_breakdown[reason]["wins"].append(t)
        else:
            filter_breakdown[reason]["losses"].append(t)

    print(f"\n    {'Filtre':<25} {'Elenen':>6} {'Loss':>5} {'Win':>5} {'Eng.Zarar':>12} {'Kayb.Kar':>12} {'NET':>12} {'Loss%':>7} {'Win%':>7}")
    print(f"    {'-' * 95}")
    for fk in filter_keys:
        fb = filter_breakdown.get(fk, {"wins": [], "losses": []})
        el = len(fb["losses"])
        ew = len(fb["wins"])
        ez = sum(t["pnl_usdt"] for t in fb["losses"])
        ek = sum(t["pnl_usdt"] for t in fb["wins"])
        net = abs(ez) - ek
        lp = el / s_orig["losses"] * 100 if s_orig["losses"] > 0 else 0
        wp = ew / s_orig["wins"] * 100 if s_orig["wins"] > 0 else 0
        print(f"    {FILTERS[fk]['ad']:<25} {el+ew:>6} {el:>5} {ew:>5} {ez:>+12.4f} {ek:>+12.4f} {net:>+12.4f} {lp:>6.1f}% {wp:>6.1f}%")

    total_el = len(rej_losses)
    total_ew = len(rej_wins)
    print(f"    {'TOPLAM':<25} {total_el+total_ew:>6} {total_el:>5} {total_ew:>5} {rej_losses_pnl:>+12.4f} {rej_wins_pnl:>+12.4f} {abs(rej_losses_pnl)-rej_wins_pnl:>+12.4f} {total_el/s_orig['losses']*100:>6.1f}% {total_ew/s_orig['wins']*100:>6.1f}%")

    # ═══════════ ENGELLENEN ZARARLAR ═══════════
    print_trade_table(rej_losses_sorted, "ENGELLENEN ZARARLI POZISYONLAR", max_rows=25)

    # Ozet istatistik
    if rej_losses:
        print(f"\n    Engellenen Zarar Ozet:")
        print(f"      Toplam       : {len(rej_losses)} trade, {fmt_pnl(rej_losses_pnl)} USDT")
        print(f"      Ortalama     : {rej_losses_pnl/len(rej_losses):+.4f} USDT/trade")
        print(f"      En buyuk     : {min(t['pnl_usdt'] for t,_ in rej_losses):+.4f} USDT")
        print(f"      Ort. ROI     : {sum(t['roi_pct'] for t,_ in rej_losses)/len(rej_losses):+.1f}%")
        # Exit reason dagilimi
        exit_counts = defaultdict(int)
        for t, _ in rej_losses:
            exit_counts[t["exit_reason"]] += 1
        print(f"      Exit dagilim : {dict(exit_counts)}")

    # ═══════════ KAYBEDILEN KARLAR ═══════════
    print_trade_table(rej_wins_sorted, "KAYBEDILEN KARLI POZISYONLAR", max_rows=25)

    if rej_wins:
        print(f"\n    Kaybedilen Kar Ozet:")
        print(f"      Toplam       : {len(rej_wins)} trade, {fmt_pnl(rej_wins_pnl)} USDT")
        print(f"      Ortalama     : {rej_wins_pnl/len(rej_wins):+.4f} USDT/trade")
        print(f"      En buyuk     : {max(t['pnl_usdt'] for t,_ in rej_wins):+.4f} USDT")
        print(f"      Ort. ROI     : {sum(t['roi_pct'] for t,_ in rej_wins)/len(rej_wins):+.1f}%")
        # Kac tanesi kucuk kar (<0.1 USDT)
        small_wins = [t for t, _ in rej_wins if t["pnl_usdt"] < 0.1]
        big_wins = [t for t, _ in rej_wins if t["pnl_usdt"] >= 0.1]
        print(f"      Kucuk kar (<0.1 USDT): {len(small_wins)} trade, {sum(t['pnl_usdt'] for t in small_wins):+.4f} USDT")
        print(f"      Buyuk kar (>=0.1 USDT): {len(big_wins)} trade, {sum(t['pnl_usdt'] for t in big_wins):+.4f} USDT")

    # ═══════════ FILTREDEN GECEN AMA ZARARDA ═══════════
    remaining_losses = sorted([t for t in passed if t["pnl_usdt"] <= 0], key=lambda x: x["pnl_usdt"])
    print_trade_table(remaining_losses, "FILTREDEN GECEN AMA HALA ZARARDA", max_rows=15, show_reason=False)

    if remaining_losses:
        print(f"\n    Kalan Zarar Ozet: {len(remaining_losses)} trade, {sum(t['pnl_usdt'] for t in remaining_losses):+.4f} USDT")

    # ═══════════ LONG / SHORT AYRI ═══════════
    print_subheader("LONG vs SHORT DETAY")

    for direction in ["Buy/Long", "Sell/Short"]:
        dir_all = [t for t in trades if t["side"] == direction]
        dir_passed = [t for t in passed if t["side"] == direction]
        dir_rej = [(t, r) for t, r in rejected if t["side"] == direction]

        s_dir_orig = stats(dir_all)
        s_dir_new = stats(dir_passed)

        dir_rej_w = sum(t["pnl_usdt"] for t, _ in dir_rej if t["pnl_usdt"] > 0)
        dir_rej_l = sum(t["pnl_usdt"] for t, _ in dir_rej if t["pnl_usdt"] <= 0)
        dir_rej_wn = sum(1 for t, _ in dir_rej if t["pnl_usdt"] > 0)
        dir_rej_ln = sum(1 for t, _ in dir_rej if t["pnl_usdt"] <= 0)

        label = "LONG" if "Long" in direction else "SHORT"
        print(f"\n    {label}:")
        print(f"      Onceki  : {s_dir_orig['total']:>3} trade, WR={s_dir_orig['wr']:>5.1f}%, Net={s_dir_orig['net']:>+8.4f}, PF={s_dir_orig['profit_factor']:.2f}")
        print(f"      Sonra   : {s_dir_new['total']:>3} trade, WR={s_dir_new['wr']:>5.1f}%, Net={s_dir_new['net']:>+8.4f}, PF={s_dir_new['profit_factor']:.2f}")
        print(f"      Elenen  : {dir_rej_ln+dir_rej_wn} ({dir_rej_ln} loss + {dir_rej_wn} win)")
        print(f"      Eng.Zar : {dir_rej_l:>+8.4f} USDT | Kayb.Kar: {dir_rej_w:>+8.4f} USDT | NET: {abs(dir_rej_l)-dir_rej_w:>+8.4f}")
        print(f"      WR artis: {s_dir_new['wr']-s_dir_orig['wr']:>+.1f}pp")

    # ═══════════ REJIM BAZLI ═══════════
    print_subheader("REJIM BAZLI DETAY")

    regimes = sorted(set(t["regime"] for t in trades))
    print(f"\n    {'Rejim':<25} {'Eski':>5} {'WR':>6} {'Net':>10}  =>  {'Yeni':>5} {'WR':>6} {'Net':>10} {'Elenen':>7} {'Eng.Zar':>10} {'Kayb.Kar':>10}")
    print(f"    {'-' * 115}")
    for regime in regimes:
        r_all = [t for t in trades if t["regime"] == regime]
        r_passed = [t for t in passed if t["regime"] == regime]
        r_rej = [(t, r) for t, r in rejected if t["regime"] == regime]
        if len(r_all) < 2:
            continue

        s_r = stats(r_all)
        s_rn = stats(r_passed)
        rez = sum(t["pnl_usdt"] for t, _ in r_rej if t["pnl_usdt"] <= 0)
        rek = sum(t["pnl_usdt"] for t, _ in r_rej if t["pnl_usdt"] > 0)
        print(f"    {regime:<25} {s_r['total']:>5} {s_r['wr']:>5.1f}% {s_r['net']:>+10.4f}  =>  {s_rn['total']:>5} {s_rn['wr']:>5.1f}% {s_rn['net']:>+10.4f} {len(r_rej):>7} {rez:>+10.4f} {rek:>+10.4f}")

    # ═══════════ EXIT REASON BAZLI ═══════════
    print_subheader("EXIT REASON BAZLI DETAY")

    reasons = ["STOP_LOSS", "external_close", "TRAILING_STOP", "REVERSE_LONG", "REVERSE_SHORT"]
    print(f"\n    {'Exit Reason':<20} {'Eski':>5} {'W':>3} {'L':>3} {'Net':>10}  =>  {'Yeni':>5} {'W':>3} {'L':>3} {'Net':>10} {'Elenen':>7}")
    print(f"    {'-' * 85}")
    for reason in reasons:
        e_all = [t for t in trades if t["exit_reason"] == reason]
        e_passed = [t for t in passed if t["exit_reason"] == reason]
        if not e_all:
            continue
        s_e = stats(e_all)
        s_en = stats(e_passed)
        elenen = len(e_all) - len(e_passed)
        print(f"    {reason:<20} {s_e['total']:>5} {s_e['wins']:>3} {s_e['losses']:>3} {s_e['net']:>+10.4f}  =>  {s_en['total']:>5} {s_en['wins']:>3} {s_en['losses']:>3} {s_en['net']:>+10.4f} {elenen:>7}")

    return s_new


def main():
    trades = load_trades()
    s_orig = stats(trades)

    print_header("SYSTEM N DETAYLI FILTRE BACKTEST RAPORU")
    print(f"  Tarih: 2026-04-10 | Toplam Trade: {len(trades)} | Donem: ~10 gun")

    # ═══════════ MEVCUT DURUM ═══════════
    print_header("BOLUM 1: MEVCUT DURUM (FILTRE YOK)")
    print_stats_block(s_orig, "Tum System N Trade'leri")

    # LONG/SHORT ayri
    for direction in ["Buy/Long", "Sell/Short"]:
        d = [t for t in trades if t["side"] == direction]
        s_d = stats(d)
        label = "LONG" if "Long" in direction else "SHORT"
        print_stats_block(s_d, f"Sadece {label}")

    # Rejim bazli ozet
    print_subheader("REJIM BAZLI MEVCUT DURUM")
    regimes = sorted(set(t["regime"] for t in trades))
    print(f"\n    {'Rejim':<25} {'Trade':>6} {'Win':>5} {'Loss':>5} {'WR%':>7} {'Net PnL':>12} {'Brut Kar':>12} {'Brut Zarar':>12} {'PF':>6}")
    print(f"    {'-' * 95}")
    for regime in regimes:
        r = [t for t in trades if t["regime"] == regime]
        if len(r) < 2:
            continue
        s_r = stats(r)
        print(f"    {regime:<25} {s_r['total']:>6} {s_r['wins']:>5} {s_r['losses']:>5} {s_r['wr']:>6.1f}% {s_r['net']:>+12.4f} {s_r['gross_profit']:>+12.4f} {s_r['gross_loss']:>+12.4f} {s_r['profit_factor']:>6.2f}")

    # Exit reason bazli ozet
    print_subheader("EXIT REASON BAZLI MEVCUT DURUM")
    reasons = sorted(set(t["exit_reason"] for t in trades))
    print(f"\n    {'Exit Reason':<22} {'Trade':>6} {'Win':>5} {'Loss':>5} {'WR%':>7} {'Net PnL':>12} {'Ort. PnL':>10}")
    print(f"    {'-' * 75}")
    for reason in reasons:
        r = [t for t in trades if t["exit_reason"] == reason]
        if not r:
            continue
        s_r = stats(r)
        print(f"    {reason:<22} {s_r['total']:>6} {s_r['wins']:>5} {s_r['losses']:>5} {s_r['wr']:>6.1f}% {s_r['net']:>+12.4f} {s_r['net']/s_r['total']:>+10.4f}")

    # ═══════════ HER PAKET ICIN ANALIZ ═══════════
    pkg_results = {}
    for pkg_key, pkg_info in PACKAGES.items():
        s_new = analyze_package(trades, pkg_key, pkg_info)
        pkg_results[pkg_key] = s_new

    # ═══════════ NIHAI OZET KARSILASTIRMA ═══════════
    print_header("BOLUM 3: NIHAI OZET KARSILASTIRMA")

    print(f"\n    {'Paket':<35} {'Trade':>6} {'Win':>5} {'Loss':>5} {'WR%':>7} {'Net PnL':>10} {'PF':>6} {'Zar.Azl%':>9} {'Kar.Azl%':>9} {'PnL Deg.':>10}")
    print(f"    {'-' * 107}")
    print(f"    {'MEVCUT (filtre yok)':<35} {s_orig['total']:>6} {s_orig['wins']:>5} {s_orig['losses']:>5} {s_orig['wr']:>6.1f}% {s_orig['net']:>+10.4f} {s_orig['profit_factor']:>6.2f} {'---':>9} {'---':>9} {'---':>10}")

    for pkg_key, s_new in pkg_results.items():
        zarar_azl = (1 - abs(s_new["gross_loss"]) / abs(s_orig["gross_loss"])) * 100 if s_orig["gross_loss"] != 0 else 0
        kar_azl = (1 - s_new["gross_profit"] / s_orig["gross_profit"]) * 100 if s_orig["gross_profit"] > 0 else 0
        pnl_deg = s_new["net"] - s_orig["net"]
        label = f"{pkg_key}: {PACKAGES[pkg_key]['ad']}"[:35]
        print(f"    {label:<35} {s_new['total']:>6} {s_new['wins']:>5} {s_new['losses']:>5} {s_new['wr']:>6.1f}% {s_new['net']:>+10.4f} {s_new['profit_factor']:>6.2f} {zarar_azl:>8.1f}% {kar_azl:>8.1f}% {pnl_deg:>+10.4f}")

    # Tavsiye
    print_header("BOLUM 4: TAVSIYE")
    print("""
  En iyi paketlerin degerlendirmesi:

  PAKET A (MACD + RSI + ER>0.2):
    + En yuksek net PnL iyilesmesi
    + WR %39.5 => %63.9 (+24.4pp)
    + 55 SL trade'in 50'sini engelliyor
    + Trade basina ortalama kar artiyor
    - Trade sayisi %76 azaliyor (301 => 72)
    - COSUSDT +9.88 gibi buyuk kazanclar kaybediliyor
    SONUC: Kaliteli trade secimi, az ama isabetli

  PAKET B (MACD + RSI + ER>0.3):
    + En yuksek WR: %72.7
    + Sadece 15 loss kaliyor
    - Cok agresif filtreleme, 55 trade
    - Bazi iyi trade'ler de eleniyor (NOMUSDT +2.43)
    SONUC: Cok muhafazakar, kucuk hesaplarda iyi

  PAKET C (Sadece MACD + RSI):
    + Dengeli: 120 trade, %50.8 WR
    + Daha fazla trade firsat
    - ER filtresi olmadan random walk trade'ler kacirilmiyor
    SONUC: Orta yol, trade sayisi onemli ise

  PAKET F (MACD + RSI + ER>0.2 + ADX<35):
    + ADX aşırı trend filtresi ekliyor
    - Paket A'ya gore ek fayda sinirli
    SONUC: ADX<35 marjinal iyilestirme

  ONERILEN: PAKET A (MACD + RSI + ER>0.2)
    - En yuksek net PnL artisi
    - Makul trade sayisi
    - SL trade'lerin buyuk cogunlugunu engelliyor
    - Profit Factor ciddi artis
""")


if __name__ == "__main__":
    main()
