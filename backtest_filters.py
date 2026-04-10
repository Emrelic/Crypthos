"""System N Filtre Backtest — Onerilen filtrelerin geriye donuk tam analizi.

Her trade icin: girilir miydi / engellenir miydi + PnL etkisi.
"""

import json
import sys
import os

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def load_trades():
    with open("data/system_n_analysis.json", "r") as f:
        data = json.load(f)
    return data["trades"]


# ─── Filtre Tanimlari ──────────────────────────────────────────

def filter_ranging_reject(t):
    """SYNCED:RANGING rejimini reddet."""
    return t["regime"] != "SYNCED:RANGING"


def filter_macd_alignment(t):
    """MACD histogram yon uyumu: LONG hist>0, SHORT hist<0."""
    if t["side"] == "Buy/Long":
        return t["macd_histogram"] > 0
    elif t["side"] == "Sell/Short":
        return t["macd_histogram"] < 0
    return True


def filter_rsi_alignment(t):
    """RSI yon uyumu: LONG RSI>40, SHORT RSI<60."""
    if t["side"] == "Buy/Long":
        return t["rsi"] > 40
    elif t["side"] == "Sell/Short":
        return t["rsi"] < 60
    return True


def filter_er_min(t, threshold=0.2):
    """Efficiency Ratio minimum esik."""
    return t["er"] > threshold


def filter_obv_alignment(t):
    """OBV yon uyumu: LONG OBV>SMA, SHORT OBV<SMA."""
    if t["side"] == "Buy/Long":
        return t["obv_above_sma"]
    elif t["side"] == "Sell/Short":
        return not t["obv_above_sma"]
    return True


# ─── Filtre Paketleri ──────────────────────────────────────────

FILTER_PACKAGES = {
    "PAKET A: MACD + RSI + ER>0.2": [
        ("RANGING reject", filter_ranging_reject),
        ("MACD alignment", filter_macd_alignment),
        ("RSI alignment", filter_rsi_alignment),
        ("ER > 0.2", lambda t: filter_er_min(t, 0.2)),
    ],
    "PAKET B: MACD + RSI + ER>0.3": [
        ("RANGING reject", filter_ranging_reject),
        ("MACD alignment", filter_macd_alignment),
        ("RSI alignment", filter_rsi_alignment),
        ("ER > 0.3", lambda t: filter_er_min(t, 0.3)),
    ],
    "PAKET C: Sadece MACD + RSI (ER'siz)": [
        ("RANGING reject", filter_ranging_reject),
        ("MACD alignment", filter_macd_alignment),
        ("RSI alignment", filter_rsi_alignment),
    ],
    "PAKET D: MACD + RSI + ER>0.2 + OBV": [
        ("RANGING reject", filter_ranging_reject),
        ("MACD alignment", filter_macd_alignment),
        ("RSI alignment", filter_rsi_alignment),
        ("ER > 0.2", lambda t: filter_er_min(t, 0.2)),
        ("OBV alignment", filter_obv_alignment),
    ],
    "PAKET E: Sadece ER>0.2 + MACD": [
        ("RANGING reject", filter_ranging_reject),
        ("MACD alignment", filter_macd_alignment),
        ("ER > 0.2", lambda t: filter_er_min(t, 0.2)),
    ],
}


def main():
    trades = load_trades()
    total = len(trades)

    print("=" * 90)
    print("  SYSTEM N FILTRE BACKTEST — 301 Trade Geriye Donuk Analiz")
    print("=" * 90)

    # Mevcut durum
    orig_wins = [t for t in trades if t["pnl_usdt"] > 0]
    orig_losses = [t for t in trades if t["pnl_usdt"] <= 0]
    orig_net = sum(t["pnl_usdt"] for t in trades)
    orig_wr = len(orig_wins) / total * 100

    print(f"\n  MEVCUT DURUM:")
    print(f"    Trade: {total} | Win: {len(orig_wins)} | Loss: {len(orig_losses)} | WR: {orig_wr:.1f}%")
    print(f"    Net PnL: {orig_net:+.4f} USDT")
    print(f"    Toplam Kar: {sum(t['pnl_usdt'] for t in orig_wins):+.4f} | Toplam Zarar: {sum(t['pnl_usdt'] for t in orig_losses):+.4f}")

    # ═══════════════════════════════════════════════════════════
    #  HER PAKET ICIN BACKTEST
    # ═══════════════════════════════════════════════════════════

    for pkg_name, filters in FILTER_PACKAGES.items():
        print(f"\n{'=' * 90}")
        print(f"  {pkg_name}")
        print(f"{'=' * 90}")

        # Her trade icin hangi filtreden elendigi
        passed = []
        rejected = []

        for t in trades:
            reject_reason = None
            for fname, ffn in filters:
                if not ffn(t):
                    reject_reason = fname
                    break
            if reject_reason:
                rejected.append((t, reject_reason))
            else:
                passed.append(t)

        p_wins = [t for t in passed if t["pnl_usdt"] > 0]
        p_losses = [t for t in passed if t["pnl_usdt"] <= 0]
        p_net = sum(t["pnl_usdt"] for t in passed)
        p_wr = len(p_wins) / len(passed) * 100 if passed else 0

        r_wins = [(t, r) for t, r in rejected if t["pnl_usdt"] > 0]
        r_losses = [(t, r) for t, r in rejected if t["pnl_usdt"] <= 0]

        print(f"\n  SONUC:")
        print(f"    Gecen: {len(passed)} trade | Win: {len(p_wins)} | Loss: {len(p_losses)} | WR: {p_wr:.1f}% (eski: {orig_wr:.1f}%)")
        print(f"    Net PnL: {p_net:+.4f} USDT (eski: {orig_net:+.4f})")
        print(f"    Elenen: {len(rejected)} trade ({len(r_losses)} loss + {len(r_wins)} win)")
        print(f"    Engellenen zarar: {sum(t['pnl_usdt'] for t, _ in r_losses):+.4f} USDT")
        print(f"    Kaybedilen kar:   {sum(t['pnl_usdt'] for t, _ in r_wins):+.4f} USDT")
        net_gain = abs(sum(t['pnl_usdt'] for t, _ in r_losses)) - sum(t['pnl_usdt'] for t, _ in r_wins)
        print(f"    NET KAZANC:       {net_gain:+.4f} USDT (engellenen zarar - kaybedilen kar)")

        # Filtre bazinda kirilim
        print(f"\n  FILTRE BAZINDA KIRILIM:")
        filter_stats = {}
        for t, reason in rejected:
            if reason not in filter_stats:
                filter_stats[reason] = {"wins": [], "losses": []}
            if t["pnl_usdt"] > 0:
                filter_stats[reason]["wins"].append(t)
            else:
                filter_stats[reason]["losses"].append(t)

        print(f"    {'Filtre':<22} {'Elenen':>6} {'Loss':>6} {'Win':>5} {'Eng.Zarar':>12} {'Kayb.Kar':>12}")
        print(f"    {'-'*63}")
        for fname, stats in filter_stats.items():
            e_loss = sum(t["pnl_usdt"] for t in stats["losses"])
            e_win = sum(t["pnl_usdt"] for t in stats["wins"])
            print(f"    {fname:<22} {len(stats['wins'])+len(stats['losses']):>6} {len(stats['losses']):>6} {len(stats['wins']):>5} {e_loss:>+12.4f} {e_win:>+12.4f}")

        # ─── ENGELLENEN ZARARLAR (buyukten kucuge) ───
        print(f"\n  ENGELLENEN ZARARLAR (en buyuk 20):")
        r_losses_sorted = sorted(r_losses, key=lambda x: x[0]["pnl_usdt"])
        print(f"    {'#':<3} {'Sembol':<15} {'Yon':<12} {'PnL':>10} {'ROI%':>8} {'Cikis':>18} {'Filtre':<20} {'RSI':>6} {'ADX':>6} {'ER':>6} {'MACD_H':>10}")
        print(f"    {'-'*115}")
        for i, (t, reason) in enumerate(r_losses_sorted[:20]):
            print(f"    {i+1:<3} {t['symbol']:<15} {t['side']:<12} {t['pnl_usdt']:>+10.4f} {t['roi_pct']:>+7.1f}% {t['exit_reason']:>18} {reason:<20} {t['rsi']:>6.1f} {t['adx']:>6.1f} {t['er']:>6.3f} {t['macd_histogram']:>+10.6f}")

        # ─── KAYBEDILEN KARLAR (buyukten kucuge) ───
        print(f"\n  KAYBEDILEN KARLAR (tumu):")
        r_wins_sorted = sorted(r_wins, key=lambda x: x[0]["pnl_usdt"], reverse=True)
        print(f"    {'#':<3} {'Sembol':<15} {'Yon':<12} {'PnL':>10} {'ROI%':>8} {'Cikis':>18} {'Filtre':<20} {'RSI':>6} {'ADX':>6} {'ER':>6} {'MACD_H':>10}")
        print(f"    {'-'*115}")
        for i, (t, reason) in enumerate(r_wins_sorted):
            print(f"    {i+1:<3} {t['symbol']:<15} {t['side']:<12} {t['pnl_usdt']:>+10.4f} {t['roi_pct']:>+7.1f}% {t['exit_reason']:>18} {reason:<20} {t['rsi']:>6.1f} {t['adx']:>6.1f} {t['er']:>6.3f} {t['macd_histogram']:>+10.6f}")

        # ─── HALA ZARAR EDEN TRADE'LER ───
        print(f"\n  FILTREDEN GECEN AMA HALA ZARAR EDEN (en buyuk 10):")
        remaining_losses = sorted(p_losses, key=lambda x: x["pnl_usdt"])[:10]
        print(f"    {'#':<3} {'Sembol':<15} {'Yon':<12} {'PnL':>10} {'ROI%':>8} {'Cikis':>18} {'RSI':>6} {'ADX':>6} {'ER':>6} {'MACD_H':>10} {'BB_Pos':>7}")
        print(f"    {'-'*105}")
        for i, t in enumerate(remaining_losses):
            print(f"    {i+1:<3} {t['symbol']:<15} {t['side']:<12} {t['pnl_usdt']:>+10.4f} {t['roi_pct']:>+7.1f}% {t['exit_reason']:>18} {t['rsi']:>6.1f} {t['adx']:>6.1f} {t['er']:>6.3f} {t['macd_histogram']:>+10.6f} {t['bb_position']:>7.3f}")

    # ═══════════════════════════════════════════════════════════
    #  OZET KARSILASTIRMA TABLOSU
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'=' * 90}")
    print(f"  OZET KARSILASTIRMA")
    print(f"{'=' * 90}")

    print(f"\n  {'Paket':<40} {'Trade':>6} {'Win':>5} {'Loss':>5} {'WR%':>7} {'Net PnL':>10} {'Eng.Zarar':>10} {'Kayb.Kar':>10} {'NET+':>10}")
    print(f"  {'-'*103}")
    print(f"  {'MEVCUT (filtre yok)':<40} {total:>6} {len(orig_wins):>5} {len(orig_losses):>5} {orig_wr:>6.1f}% {orig_net:>+10.4f} {'---':>10} {'---':>10} {'---':>10}")

    for pkg_name, filters in FILTER_PACKAGES.items():
        passed = []
        rejected_losses_pnl = 0
        rejected_wins_pnl = 0
        for t in trades:
            ok = True
            for fname, ffn in filters:
                if not ffn(t):
                    ok = False
                    break
            if ok:
                passed.append(t)
            else:
                if t["pnl_usdt"] > 0:
                    rejected_wins_pnl += t["pnl_usdt"]
                else:
                    rejected_losses_pnl += t["pnl_usdt"]

        p_w = sum(1 for t in passed if t["pnl_usdt"] > 0)
        p_l = sum(1 for t in passed if t["pnl_usdt"] <= 0)
        p_net = sum(t["pnl_usdt"] for t in passed)
        p_wr = p_w / len(passed) * 100 if passed else 0
        net_gain = abs(rejected_losses_pnl) - rejected_wins_pnl
        short_name = pkg_name.split(":")[0] + ":" + pkg_name.split(":")[1][:25] if ":" in pkg_name else pkg_name[:40]
        print(f"  {short_name:<40} {len(passed):>6} {p_w:>5} {p_l:>5} {p_wr:>6.1f}% {p_net:>+10.4f} {rejected_losses_pnl:>+10.4f} {rejected_wins_pnl:>+10.4f} {net_gain:>+10.4f}")

    # ═══════════════════════════════════════════════════════════
    #  LONG/SHORT AYRI BACKTEST (en iyi paket)
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'=' * 90}")
    print(f"  PAKET A DETAY — LONG vs SHORT AYRI")
    print(f"{'=' * 90}")

    best_filters = FILTER_PACKAGES["PAKET A: MACD + RSI + ER>0.2"]

    for direction in ["Buy/Long", "Sell/Short"]:
        dir_trades = [t for t in trades if t["side"] == direction]
        dir_passed = []
        dir_rejected = []
        for t in dir_trades:
            ok = True
            for fname, ffn in best_filters:
                if not ffn(t):
                    ok = False
                    break
            if ok:
                dir_passed.append(t)
            else:
                dir_rejected.append(t)

        d_total = len(dir_trades)
        d_wins_orig = sum(1 for t in dir_trades if t["pnl_usdt"] > 0)
        d_net_orig = sum(t["pnl_usdt"] for t in dir_trades)

        p_w = sum(1 for t in dir_passed if t["pnl_usdt"] > 0)
        p_l = sum(1 for t in dir_passed if t["pnl_usdt"] <= 0)
        p_net = sum(t["pnl_usdt"] for t in dir_passed)

        r_w = sum(1 for t in dir_rejected if t["pnl_usdt"] > 0)
        r_l = sum(1 for t in dir_rejected if t["pnl_usdt"] <= 0)
        eng_zarar = sum(t["pnl_usdt"] for t in dir_rejected if t["pnl_usdt"] <= 0)
        kayb_kar = sum(t["pnl_usdt"] for t in dir_rejected if t["pnl_usdt"] > 0)

        print(f"\n  {direction}:")
        print(f"    Onceki: {d_total} trade, WR={d_wins_orig/d_total*100:.1f}%, Net={d_net_orig:+.4f}")
        print(f"    Sonra:  {len(dir_passed)} trade, WR={p_w/len(dir_passed)*100 if dir_passed else 0:.1f}%, Net={p_net:+.4f}")
        print(f"    Elenen: {len(dir_rejected)} ({r_l} loss + {r_w} win)")
        print(f"    Engellenen zarar: {eng_zarar:+.4f} | Kaybedilen kar: {kayb_kar:+.4f}")

    # ═══════════════════════════════════════════════════════════
    #  REJIM BAZLI BACKTEST (en iyi paket)
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'=' * 90}")
    print(f"  PAKET A DETAY — REJIM BAZLI")
    print(f"{'=' * 90}")

    for regime in sorted(set(t["regime"] for t in trades)):
        r_trades = [t for t in trades if t["regime"] == regime]
        if len(r_trades) < 2:
            continue

        r_passed = []
        for t in r_trades:
            ok = True
            for fname, ffn in best_filters:
                if not ffn(t):
                    ok = False
                    break
            if ok:
                r_passed.append(t)

        r_total = len(r_trades)
        r_wins_orig = sum(1 for t in r_trades if t["pnl_usdt"] > 0)
        r_net_orig = sum(t["pnl_usdt"] for t in r_trades)

        p_w = sum(1 for t in r_passed if t["pnl_usdt"] > 0)
        p_l = len(r_passed) - p_w
        p_net = sum(t["pnl_usdt"] for t in r_passed)
        p_wr = p_w / len(r_passed) * 100 if r_passed else 0

        print(f"  {regime:<25} Onceki: {r_total:>3} trade WR={r_wins_orig/r_total*100:>5.1f}% Net={r_net_orig:>+8.4f}  =>  Sonra: {len(r_passed):>3} trade WR={p_wr:>5.1f}% Net={p_net:>+8.4f}")

    # ═══════════════════════════════════════════════════════════
    #  EXIT REASON BAZLI BACKTEST
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'=' * 90}")
    print(f"  PAKET A DETAY — EXIT REASON BAZLI")
    print(f"{'=' * 90}")

    for reason in ["STOP_LOSS", "external_close", "TRAILING_STOP", "REVERSE_LONG", "REVERSE_SHORT"]:
        e_trades = [t for t in trades if t["exit_reason"] == reason]
        if not e_trades:
            continue

        e_passed = []
        for t in e_trades:
            ok = True
            for fname, ffn in best_filters:
                if not ffn(t):
                    ok = False
                    break
            if ok:
                e_passed.append(t)

        e_total = len(e_trades)
        e_wins_orig = sum(1 for t in e_trades if t["pnl_usdt"] > 0)
        e_net_orig = sum(t["pnl_usdt"] for t in e_trades)

        p_w = sum(1 for t in e_passed if t["pnl_usdt"] > 0)
        p_net = sum(t["pnl_usdt"] for t in e_passed)
        elenen = e_total - len(e_passed)

        print(f"  {reason:<20} Onceki: {e_total:>3} trade Net={e_net_orig:>+8.4f}  =>  Sonra: {len(e_passed):>3} (elenen: {elenen}) Net={p_net:>+8.4f}")


if __name__ == "__main__":
    main()
