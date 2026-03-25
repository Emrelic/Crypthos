# System H — Hibrit Strateji Belgesi (v2.0)

## 1. Felsefe

System H, tum sistemlerin en iyi ozelliklerini tek bir catida toplayan hibrit sistemdir.

- **A'dan:** Composite skor (on-skorlama), 11 hard filtre, gray zone oylama, risk yonetimi
- **B'den:** Zigzag dalga analizi (G/I), ER+Hurst rejim tespiti, hysteresis
- **D'den:** Zoom Diyafram (optimal TF secimi), G bazli kaldirac hesaplama
- **F'den:** P(win)/EV istatistiksel giris kapisi, climax filtresi, BTC beta filtresi
- **G'den:** Per-coin 240 combo mini-backtest optimizer (async, 4 saat cache)

Temel ilke: **ATR kisa vadeli gurultu olcer, G gercek fiyat hareketi olcer.** SL/TP/trailing gibi swing bazli kararlar G'den, limit giris offset gibi kisa vadeli isler ATR'den turetilir.

## 2. 4 Fazli Akis

```
Faz 1: SKORLAMA (A'dan — on-siralama)
  50 coin → sabit TF (5m) → indikatorler → A'nin 4-bilesenli skoru
  11 hard filtre: FR, OB, thin book, wall, confluence, RSI, MACD, volume, gray zone
  Cikti: sirali coin listesi (siralama amacli — final skor Faz 5'te)

Faz 2: ZOOM DIYAFRAM (D'den)
  Top 5 finalist → 9 TF'de zigzag → G hesapla → dirsek noktasi
  G → SL = 1.5×G (trend) veya 2×G (ranging)
  G → Kaldirac = min(user_max, 100 / teorik_liq)
  G → Trailing = 2.5G tetik, 0.5G mesafe (trend) veya BB/3G TP (ranging)
  Cikti: her finalist icin kaldirac, SL, TP, trailing

Faz 2.5a: CLIMAX FİLTRESİ (F'den — opsiyonel)
  Son mum hacim >= 2.5×MA VE son 3 ort >= 2×MA → climax
  Climax mumunun yonu trade yonune ters ise → reject (tepe/dip yakalama onlenir)
  Config: climax_filter_enabled (default: false)

Faz 2.5b: BTC BETA FİLTRESİ (F'den — opsiyonel)
  |beta| > threshold (0.5) VE BTC yonu trade yonune ters → reject
  Yuksek korelasyonlu coinler BTC'ye karsi acilmaz
  Config: btc_beta_filter_enabled (default: false)

Faz 2.5c: PER-COIN OPTİMİZER (G'den — opsiyonel)
  6 leverage × 8 TP × 5 SL = 240 combo mini-backtest
  Async (ThreadPool), 4 saat cache, non-blocking
  Blend: %60 G bazli + %40 optimizer (liq_rate<%30 ise)
  Config: optimizer_enabled (default: false)

Faz 3: REJIM TESPITI (B'den)
  ER makro + ER mikro + Hurst → MTF 4 kural matrisi
  Hysteresis: rejim degismesi icin 3 ardisik ayni okuma gerekli
  Cikti: TREND / RANGING / WEAK_TREND / WEAK_RANGING / UNDECIDED

Faz 4: P(win)/EV (F'den)
  Zoom TF swing verilerinden (bedava — Faz 2'nin yan urunu)
  P(win) = P(forward >= TP) / P(win + loss)
  EV = P(win) × TP × lev - P(loss) × SL × lev
  EV Hard Gate (opsiyonel): ev_pct < min_ev_pct → reject
  Config: ev_hard_gate_enabled (default: false), min_ev_pct (default: 5.0)

Faz 5: H-SPECIFIC FINAL SKOR (yeni — v2.0)
  Faz 1'deki A skoru yerine 5-bilesenli H-ozel skor:
  1. direction_strength  %30 — confluence/momentum kalitesi
  2. ev_quality          %25 — P(win)/EV istatistiksel kalitesi
  3. regime_clarity      %20 — ER+Hurst rejim netligi
  4. market_context      %15 — sentiment (FR, OI, OB)
  5. wave_quality        %10 — dalga tutarliligi (wave count, CV)
  Config: score_weights blogu ile agirliklar ayarlanabilir
```

## 3. Kaldirac Hesaplama

G bazli, user max ile sinirli:

```
Trend:
  SL = 1.5 × G
  Pratik liq = SL × 3.0
  Teorik liq = (pratik + fee + slippage) / 0.7
  Kaldirac = 100 / teorik_liq
  Final = min(user_max, kaldirac)

Ranging:
  SL = 2.0 × G
  Pratik liq = SL × 4.0
  ... ayni formul
```

Ters G hesabi: kaldirac Binance max'i asarsa, G efektif olarak buyutulur → SL genisler.

## 4. SL / TP / Trailing

| Karar | Formul | Kaynak |
|-------|--------|--------|
| SL (trend) | 1.5 × G + fee + slippage | G bazli |
| SL (ranging) | 2.0 × G + fee + slippage | G bazli |
| Trailing tetik | 2.5 × G | G bazli |
| Trailing mesafe | 0.5 × G (min 0.1%, max 5.0%) | G bazli |
| TP (ranging) | min(3×G, BB karsi bant) | G + BB |
| Limit giris offset | 0.15 × ATR | ATR bazli |
| Emergency | %80 liq mesafesi | Kaldirac bazli |

## 5. Rejim Tespiti (ER + Hurst)

ADX yerine ER (Efficiency Ratio) + Hurst Exponent kullanilir:

| Metrik | Ranging | Gecis | Trending |
|--------|---------|-------|----------|
| ER makro | < 0.15 | 0.15-0.35 | > 0.35 |
| ER mikro | < 0.20 | 0.20-0.40 | > 0.40 |
| Hurst | < 0.45 | 0.45-0.55 | > 0.55 |

MTF 4 Kural Matrisi:
- Ikisi ayni → o rejim (tam guven)
- Biri gecis, digeri net → zayif versiyon
- Celisiyor → UNDECIDED (GRAY zone)

Hysteresis: 3 ardisik ayni okuma olmadan rejim degismez.

## 6. P(win) / EV

Zoom TF'deki swing verilerinden hesaplanir:

```
forward_pcts: yon bazli ileri dalgalar (%)
retrace_pcts: yon bazli geri dalgalar (%)

P(win_cycle) = count(forward >= TP) / len(forward)
P(loss_cycle) = (1 - P(win_cycle)) × count(retrace >= SL) / len(retrace)

P(win) = P(win_cycle) / (P(win_cycle) + P(loss_cycle))
EV = P(win) × TP × leverage - P(loss) × SL × leverage
```

EV **hard filtre degil**, skor carpani:
- EV > 0 → skor × (1 + EV/100), max 1.3x
- EV < -10 → skor × (1 + EV/100), min 0.7x

## 7. Pozisyon Yonetimi

- Bakiye / 12 per pozisyon (A'dan)
- Max 12 pozisyon
- Min 1 USDT margin
- Yon dengesi: 2:1 (varsayilan)
- Loss cooldown: son zarar sonrasi 10dk (A'dan)
- Coin daily ban: 3 zarar → 24 saat ban (A'dan)
- Direction balance: majority <= X × (floor(minority/Y) + 1)

## 8. Giris Kurallari

1. Faz 1'de composite skor >= min_buy_score (55)
2. 11 hard filtreden gecmeli
3. Faz 2'de Zoom gecerli G > 0.01 ve dalga sayisi >= 4
4. CV < 1.5 (dalga tutarliligi)
5. Kaldirac >= min_leverage (2)
6. Fee+spread koruması: SL > 3× roundtrip fee
7. Limit emir: 0.15×ATR offset, 300sn timeout, market fallback

## 9. Cikis Kurallari

Server-side (Binance):
- STOP_MARKET: G bazli SL
- TRAILING_STOP_MARKET: 2.5G tetik, 0.5G mesafe (trend)
- TAKE_PROFIT_MARKET: BB veya 3G (ranging)

Software-side (yedek):
- Emergency anti-liquidation (%80 liq)
- Software SL (server SL dolmazsa)
- Signal exit: confluence reversal (A'dan)
- Time limit: 8 saat, karda kapat, zararda server'a birak

## 10. Server Order Repair

Crash sonrasi eksik emirler otomatik onarilir:
- SL eksikse → G bazli STOP_MARKET yeniden yerlestirilir
- Trailing/TP eksikse → rejime gore yeniden yerlestirilir

## 11. Config Parametreleri

```json
{
  "system_h": {
    "enabled": false,
    "coin_sayisi": 50,
    "scan_timeframe": "5m",
    "max_finalists": 5,
    "max_positions": 12,
    "min_buy_score": 55,
    "portfolio_divider": 12,
    "swing_n": 10,
    "min_wave_count": 4,
    "max_cv": 1.5,
    "min_leverage": 2,
    "sl_mult_trend": 1.5,
    "sl_mult_ranging": 2.0,
    "liq_mult_trend": 3.0,
    "liq_mult_ranging": 4.0,
    "trailing_trigger_g_mult": 2.5,
    "trailing_callback_g_mult": 0.5,
    "ranging_tp_g_mult": 3.0,
    "er_macro_ranging": 0.15,
    "er_macro_trending": 0.35,
    "hurst_ranging": 0.45,
    "hurst_trending": 0.55,
    "regime_hysteresis": 3
  }
}
```

## 12. Oncelik

H > G > F > E > D > B > A

System H aktif edilince diger tum sistemler devre disi kalir.
