"""Scanner Dry-Run Test - scans top 50 cryptos, scores them, shows results."""
import time
from core.config_manager import ConfigManager
from market.binance_rest import BinanceRestClient
from scanner.symbol_universe import SymbolUniverse
from scanner.batch_fetcher import BatchKlineFetcher
from scanner.scanner_scorer import ScannerScorer

print("=" * 75)
print("  CRYPTHOS KRIPTO TARAYICI - DRY RUN TEST")
print("=" * 75)

config = ConfigManager()
rest = BinanceRestClient()

# 1. Symbol Universe
print("\n[1] Sembol listesi olusturuluyor (top 50 hacim)...")
t0 = time.time()
universe = SymbolUniverse(rest, top_n=50, min_volume_usdt=5_000_000)
symbols = universe.refresh()
t1 = time.time()
print(f"   {len(symbols)} sembol bulundu ({t1-t0:.1f}s)")
print(f"   Ilk 10: {symbols[:10]}")

# 2. Batch Kline Fetch
print(f"\n[2] {len(symbols)} sembol icin 15m kline cekiliyor (paralel)...")
t0 = time.time()
fetcher = BatchKlineFetcher(rest, max_workers=10, requests_per_second=3.5)
klines_map = fetcher.fetch_batch(symbols, "15m", 200)
t1 = time.time()
print(f"   {len(klines_map)}/{len(symbols)} basarili ({t1-t0:.1f}s)")

# 3. Score all symbols
print(f"\n[3] {len(klines_map)} sembol skorlaniyor (30 indikator + analiz)...")
t0 = time.time()
scorer = ScannerScorer(config)
results = scorer.score_batch(klines_map, universe.get_all_tickers())
t1 = time.time()
print(f"   Skorlama tamamlandi ({t1-t0:.1f}s)")

# 4. Show results
eligible = [r for r in results if r.eligible]
long_signals = [r for r in eligible if r.direction == "LONG"]
short_signals = [r for r in eligible if r.direction == "SHORT"]

print(f"\n   Toplam: {len(results)} sembol")
print(f"   Eligible: {len(eligible)} ({len(long_signals)} LONG, {len(short_signals)} SHORT)")

print(f"\n{'='*75}")
print(f"  {'#':>3} {'Sembol':<12} {'Skor':>6} {'Yon':<6} {'Rejim':<10} "
      f"{'Confluence':>10} {'RSI':>5} {'ADX':>5} {'ATR%':>5} {'Durum':<15}")
print(f"{'='*75}")

for i, r in enumerate(results[:30]):
    marker = ">>>" if r.eligible and abs(r.score) >= 60 else "   "
    regime = r.regime.get("regime", "?")[:8]
    conf = r.confluence.get("score", 0)
    status = "ELIGIBLE" if r.eligible else r.reject_reason[:14]
    print(f"{marker}{i+1:>3} {r.symbol:<12} {r.score:>+6.0f} {r.direction:<6} "
          f"{regime:<10} {conf:>+10.1f} {r.rsi:>5.0f} {r.adx:>5.0f} "
          f"{r.atr_percent:>5.1f} {status:<15}")

# 5. Best candidate
print(f"\n{'='*75}")
best = None
for r in eligible:
    if abs(r.score) >= 60:
        best = r
        break

if best:
    print(f"  EN IYI ADAY: {best.symbol}")
    print(f"  Skor: {best.score:+.1f} | Yon: {best.direction}")
    print(f"  Rejim: {best.regime.get('regime')} ({best.regime.get('trend_direction')})")
    print(f"  Confluence: {best.confluence.get('score', 0):+.1f} -> {best.confluence.get('signal')}")
    print(f"  RSI: {best.rsi:.1f} | ADX: {best.adx:.1f} | ATR%: {best.atr_percent:.2f}")
    print(f"  Fiyat: {best.price:.6f} | 24h hacim: ${best.volume_24h:,.0f}")
    if best.divergences:
        for d in best.divergences:
            print(f"  Diverjans: {d['type']} ({d['indicator']})")
    print(f"\n  Confluence detay:")
    for k, v in best.confluence.get("details", {}).items():
        bar = ("+" * int(abs(v)*4)) if v > 0 else ("-" * int(abs(v)*4)) if v < 0 else "."
        print(f"    {k:18s}: {v:+5.1f}  {bar}")
else:
    print("  ADAY YOK - hicbir sembol >= 60 skor almadi")
    if eligible:
        top = eligible[0]
        print(f"  En yakin: {top.symbol} skor={top.score:+.1f}")

print(f"{'='*75}")
print("DRY RUN TAMAMLANDI")
