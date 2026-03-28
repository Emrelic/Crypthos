# System I - Unified Trading Strategy
# Version: 1.0 (Draft)
# Date: 2026-03-27
# Status: DESIGN PHASE - Istisare devam ediyor

---

## 0. Felsefe

Onceki 8 sistemin (A-H) deneyimlerinden cikan en iyi yapilar tek bir tutarli sistemde birlestirilir.
- G dalga boyu (ATR degil) tum risk hesaplarinin temeli
- Zoom diyafram ile coin bazli optimal timeframe
- ER/Hurst ile rejim tespiti (ADX yerine)
- P(win)/EV ile istatistiksel dogrulama
- Fee-aware tum hesaplamalar
- Ayarlanabilir her parametre, varsayilan degerler bu belgede

---

## 1. Aday Secimi

### 1.1 Evren
- Binance Futures USDT-M, top 50 coin (24h islem hacmine gore)
- Guncelleme: Her scan dongusunde (varsayilan 60s)

### 1.2 Hard Filtreler (aday eleme)
| Filtre | Kosul | Ayarlanabilir | Varsayilan |
|--------|-------|---------------|------------|
| Funding Rate | abs(FR) < esik | funding_rate_max | 0.1% |
| Orderbook | spread < esik | max_spread_pct | 0.05% |
| Thin Book | depth > min | min_depth_usd | 50000 |
| Volume | vol_ratio >= esik | min_volume_ratio | 1.5 |
| Min Dalga | wave_count >= n | min_wave_count | 3 |
| Wall Blocking | imbalance < esik | max_wall_imbalance | 0.3 |

### 1.3 BTC Korelasyonu (opsiyonel)
```json
{
  "btc_correlation": {
    "enabled": true,
    "beta_threshold": 0.5,
    "lookback_hours": 24,
    "action": "block"
  }
}
```
- action: "block" = girme, "reduce" = kaldirac x0.5, "warn" = sadece uyar

---

## 2. Zoom Diyafram - Timeframe ve G Belirleme (v2)

### 2.1 Algoritma
Her aday coin icin Binance'in destekledigi TUM 14 TF'de zigzag analizi:
```
TF listesi: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w

Her TF icin (TF'ye gore dinamik mum sayisi):
  Kisa TF (1m-5m):   1000-1500 mum (1-5 gun veri)
  Orta TF (15m-2h):  300-500 mum   (5-25 gun veri)
  Uzun TF (4h+):     200 mum       (33+ gun veri)

  1. Zigzag swing tespiti (N=10)
  2. G = ortalama geri dalga boyu (%)
  3. I = ortalama ileri dalga boyu (%)
  4. BW = geri dalga sayisi (guvenilirlik olcusu)
  5. G/TF orani = (G artis%) / (TF artis%) — verimlilik olcusu
```

### 2.2 Minimum Dalga Filtresi
```
BW (geri dalga sayisi) >= zoom_min_backward_waves (varsayilan: 10)
Yetersiz dalga olan TF'ler degerlendirme disi birakilir.
Bu, kisa TF'lerde yetersiz veriyle yanlis karar almayi onler.
```

### 2.3 Optimal TF Secimi (Alttan Yukari Tarama)
```
Mantik: "Kaldiraci koruyarak TF'yi uzat, G patladigi anda dur"

1. En kucuk guvenilir TF'den basla (max kaldirac, min G)
2. Bir ust TF'ye gec, G/TF oranina bak:
   - G AZALDIYSA:    Bedava TF uzatma! Devam et.     (oran < 0)
   - oran < 0.60:    Verimli gecis, G az artti. Devam.
   - oran < 0.80:    Kabul edilebilir. Devam.
   - oran >= 0.80:   VERIMSIZ! G patladi. DUR!
3. Durdugu yerin bir onceki TF = OPTIMAL TF (yon_tf)

G/TF orani nedir:
  TF %100 artinca G %kadar artti?
  oran = 0.15 -> TF 2 katina cikti, G sadece %15 artti (COK VERIMLI)
  oran = 0.50 -> TF 2 katina cikti, G %50 artti (NORMAL)
  oran = 1.00 -> TF 2 katina cikti, G de 2 katina cikti (VERIMSIZ)
  oran > 1.00 -> G, TF'den hizli artiyor (COK VERIMSIZ)
```

### 2.4 Ust Timeframe Turetme
```
Yon TF     = Zoom sonucu (optimal TF)
Teyit TF   = Yon TF x confirm_tf_multiplier (varsayilan: 12)
Giris TF   = Yon TF / entry_tf_divisor (varsayilan: 3)

Binance TF mapping: en yakin gecerli TF'ye yuvarla (tf_rounding ayari)
```

### 2.5 Binance TF Listesi
```
1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w
```

### 2.6 Timeframe Config
```json
{
  "timeframe": {
    "tf_count": 2,
    "confirm_tf_multiplier": 12,
    "entry_tf_mode": "auto",
    "entry_tf_divisor": 3,
    "entry_tf_manual": "5m",
    "tf_rounding": "up",
    "mid_multiplier": 4,
    "zoom_min_tf": "1m",
    "zoom_max_tf": "1w",
    "zoom_min_backward_waves": 10,
    "zoom_g_tf_efficient": 0.60,
    "zoom_g_tf_inefficient": 0.80
  }
}
```

### 2.7 Gercek Veri Ornekleri (2026-03-28)
```
BTCUSDT:  yon_tf=15m  G=1.56%  Lev=14x  BW=12  (3m->5m->15m verimli, 30m'de patladi)
ETHUSDT:  yon_tf=3m   G=0.88%  Lev=25x  BW=26  (1m->3m verimli, 5m'de patladi)
XRPUSDT:  yon_tf=30m  G=2.64%  Lev=8x   BW=12  (1m->3m->5m->15m->30m verimli, 1h'de patladi)
DOGEUSDT: yon_tf=1h   G=5.96%  Lev=3x   BW=13  (1m->3m->5m->15m->30m->1h devam, 1h kabul)
SOLUSDT:  yon_tf=1h   G=5.97%  Lev=3x   BW=11  (1m->3m->5m->30m->1h devam, 1h kabul)
AVAXUSDT: yon_tf=15m  G=1.79%  Lev=12x  BW=12  (1m->3m->5m->15m verimli, 30m'de patladi)
ADAUSDT:  yon_tf=3m   G=1.18%  Lev=19x  BW=13  (1m->3m verimli, 5m'de patladi)
```
```
- tf_count=2: Yon TF + Teyit TF
- tf_count=3: Yon TF + Mid TF (x mid_multiplier) + Teyit TF (x confirm_tf_multiplier)

---

## 3. Yon Belirleme - 3 Indikator x N Timeframe

### 3.1 Indikatorler (her TF icin)
| Indikator | Parametreler | LONG | SHORT | Notr |
|-----------|-------------|------|-------|------|
| EMA 9/21 | gap >= 0.05% | EMA9 > EMA21 | EMA9 < EMA21 | gap < 0.05% |
| MACD 8/17/9 | histogram + momentum | hist > 0 AND artan | hist < 0 AND azalan | duz |
| RSI 14 | 55/45 esik | RSI > 55 | RSI < 45 | 45-55 arasi |

### 3.2 TF Oylama
```
Her TF: 3 indikator -> +1 (LONG), -1 (SHORT), 0 (notr)
TF yonu = toplam / 3
  >= +0.33 -> LONG
  <= -0.33 -> SHORT
  diger -> FLAT
```

### 3.3 Coklu TF Hizalama
```
tf_count=2:
  Yon TF + Teyit TF ayni yon -> sinyal VAR
  Farkli -> sinyal YOK

tf_count=3:
  3/3 ayni yon -> guclu sinyal
  2/3 ayni yon -> zayif sinyal (kaldirac x0.7)
  1/3 veya 0/3 -> sinyal YOK
```

### 3.4 Giris TF Kullanimi
Giris TF yonu ana yonle uyumlu oldugunda giris yapilir.
Giris TF'de EMA cross veya RSI donusu beklenir (hassas giris noktasi).

---

## 4. Rejim Tespiti - Trend vs Ranging

### 4.1 Birincil: Efficiency Ratio (ER)
```
ER = |net_fiyat_degisimi| / toplam_|bar_degisimleri|
Hesaplama TF: Yon TF (en dogal veri)

ER > 0.35 -> TRENDING
ER < 0.20 -> RANGING
ER 0.20-0.35 -> GRAY ZONE
```

### 4.2 Gray Zone Karari: ER + Hurst + TF Hizalama
```
Gray zone (ER 0.20-0.35):

Hurst > 0.55 AND 3/3 TF hizalama -> TREND gibi davran (kaldirac x0.7)
Hurst < 0.45 AND 2/3 TF hizalama -> RANGING gibi davran (kaldirac x0.7)
Hurst 0.45-0.55:
  3/3 TF hizalama -> dusuk kaldiracla TREND (x0.5)
  2/3 TF hizalama -> dusuk kaldiracla RANGING (x0.5)
  1/3 veya 0/3 -> ISLEM YAPMA
```

### 4.3 Hurst Exponent (teyit)
```
Hesaplama: Yon TF verisi uzerinde
H > 0.55 -> TRENDING (persistent)
H < 0.45 -> RANGING (mean-reverting)
H 0.45-0.55 -> BELIRSIZ
```

### 4.4 Rejim Hysteresis
- Rejim degisimi icin 2 ardisik ayni okuma gerekli (varsayilan)
- Bootstrap: ilk okuma hemen kabul edilir
- Config: `regime_hysteresis_count: 2`

### 4.5 3/3 TF Hizalama + Rejim Iliskisi
```
TRENDING + 3/3 hizalama = KESIN TREND (tam giris)
TRENDING + 2/3 hizalama = TREND PULLBACK (bekle veya dusuk kaldirac)
RANGING + 2/3 hizalama = MR FIRSATI (cogunluk yonune gir)
RANGING + 3/3 hizalama = DIKKAT: momentum var, breakout riski
GRAY ZONE = Hurst'e bak (Section 4.2)
```

---

## 5. Kaldirac Hesabi

### 5.1 G Bazli Formul
```
SL = 1.5 x G (TREND) veya 2.0 x G (RANGING)
Pratik Likidasyon = 3 x G (TREND) veya 4 x G (RANGING)
Teorik Likidasyon = (Pratik_Liq + 0.08%) / 0.7
Kaldirac = 100 / Teorik_Likidasyon
Kaldirac = max(min_leverage, min(kaldirac, max_leverage))
```

### 5.2 Kaldirac Carpanlari
| Durum | Carpan | Aciklama |
|-------|--------|----------|
| Tam sinyal (3/3 TF) | x1.0 | Normal |
| Zayif sinyal (2/3 TF) | x0.7 | Dusuk guven |
| Gray zone (Hurst teyitli) | x0.7 | Belirsiz rejim |
| Gray zone (Hurst belirsiz) | x0.5 | Cok belirsiz |
| BTC ters korelasyon | x0.5 | Riskli (action=reduce ise) |
| Yuksek CV (dalga tutarsizligi) | x0.7 | CV > 0.4 |

### 5.3 Fee-Aware Duzeltme
```
fee_pct = 0.08%  (round-trip: 0.04% x 2)
slippage_pct = fee_pct x 0.5
Tum SL hesaplarinda: SL_net = SL_raw + fee_pct + slippage_pct
```

### 5.4 Config
```json
{
  "leverage": {
    "min_leverage": 2,
    "max_leverage": 125,
    "trend_sl_g_mult": 1.5,
    "trend_liq_g_mult": 3.0,
    "ranging_sl_g_mult": 2.0,
    "ranging_liq_g_mult": 4.0,
    "liq_safety_factor": 0.7,
    "fee_pct": 0.08,
    "slippage_pct": 0.04,
    "cv_threshold": 0.4,
    "cv_multiplier": 0.7,
    "weak_signal_multiplier": 0.7,
    "gray_zone_confirmed_mult": 0.7,
    "gray_zone_uncertain_mult": 0.5
  }
}
```

---

## 6. Giris Stratejisi

### 6.1 TREND Modu
- Giris tipi: Market (hizli yakalama)
- Giris TF'de EMA cross veya RSI donusu teyidi ile
- Alternatif: Limit giris (limit_entry_enabled=true ise, 0.1xATR offset)

### 6.2 RANGING Modu
- Giris tipi: Limit emir (BB bant yakinina)
- Offset: 0.1 x ATR (varsayilan)
- Timeout: 300s (5dk, dolmazsa iptal)
- Recheck: dolunca sinyal tekrar kontrol

### 6.3 Giris TF Hassasiyeti
```
Giris TF'de aranan kosullar:
  TREND: EMA cross yonu + MACD histogram artisi + RSI > 55 (LONG) veya < 45 (SHORT)
  RANGING: RSI asiri bolge + volume exhaustion (vol_ratio < 0.8) + BB bant yakinligi
```

### 6.4 Config
```json
{
  "entry": {
    "trend_entry_type": "market",
    "ranging_entry_type": "limit",
    "limit_atr_offset": 0.1,
    "limit_timeout_seconds": 300,
    "limit_recheck_signal": true,
    "entry_tf_rsi_confirm": true,
    "entry_tf_ema_confirm": true
  }
}
```

---

## 7. Stop Loss

### 7.1 Her Zaman Var
SL her zaman konulur. E sistemindeki "SL yok" yaklasimi reddedildi (likidasyon riski).

### 7.2 Hesaplama
```
TREND: SL = 1.5 x G + fee + slippage
RANGING: SL = 2.0 x G + fee + slippage (daha genis, bounce icin alan)
```

### 7.3 Server-Side
- Pozisyon acilir acilmaz STOP_MARKET emri gonderilir
- Her 30s guncelleme (trailing SL ilerledikce)

---

## 8. Kar Alma (TP) ve Cikis

### 8.1 TREND Modu - Trailing Stop
```
Trailing tetik: 2.5 x G (kar bu seviyeye ulasinca trailing baslar)
Trailing callback: 0.5 x G (fiyat geri cekilirse tetiklenir)
Server-side: TRAILING_STOP_MARKET emri
```

### 8.2 RANGING Modu - Sabit TP
```
TP hedef: BB middle (birincil) veya 2.0 x G (alternatif)
Tam pozisyon kapatilir (partial yok)
```

### 8.3 Kademeli TP (opsiyonel, trend icin)
```
tp_mode = "ladder" ise:
  TP1 = 2.0 x G -> pozisyonun %30'unu kapat
  TP2 = 3.5 x G -> pozisyonun %30'unu kapat
  TP3 = trailing -> kalan %40

tp_mode = "single" ise:
  Tek TP = tp_single_g_mult x G (varsayilan 2.5G)

tp_mode = "trailing_only" ise:
  Sadece trailing, sabit TP yok

tp_mode = "ev_optimized" ise:
  P(win)/EV'den en iyi G carpanini hesapla
```

### 8.4 ROI Bazli TP (opsiyonel)
```
roi_based_tp_enabled = true ise:
  ROI >= roi_tp_pct -> cik (varsayilan: %50 ROI)
  Bu, kaldirac ne olursa olsun sabit ROI hedefi
```

### 8.5 Config
```json
{
  "tp": {
    "trend_tp_mode": "trailing_only",
    "ranging_tp_mode": "single",
    "trailing_trigger_g_mult": 2.5,
    "trailing_callback_g_mult": 0.5,
    "ranging_tp_target": "bb_middle",
    "ranging_tp_g_mult": 2.0,
    "ladder_enabled": false,
    "ladder_tp1_g_mult": 2.0,
    "ladder_tp1_close_pct": 30,
    "ladder_tp2_g_mult": 3.5,
    "ladder_tp2_close_pct": 30,
    "ladder_tp3_mode": "trailing",
    "roi_based_tp_enabled": false,
    "roi_based_tp_pct": 50.0,
    "single_tp_g_mult": 2.5
  }
}
```

---

## 9. Cikis Onceliklendirme (7+1 Seviye)

A sisteminden miras, G bazli uyarlama:

| Oncelik | Cikis Tipi | Kosul | Devre Disi Olabilir mi |
|---------|-----------|-------|----------------------|
| 0 | EMERGENCY | %80 likidasyon mesafesi | HAYIR (her zaman aktif) |
| 1 | HARD SL | 1.5G (trend) / 2G (ranging) | HAYIR |
| 2 | SIGNAL EXIT | Confluence reversal (kar:-4, zarar:-8) | Evet |
| 3 | TP HEDEF | G bazli veya BB middle (yeni!) | Evet |
| 4 | TRAILING | 2.5G tetik, 0.5G callback | Evet |
| 5 | PARTIAL TP | Kademeli kar alma | Evet |
| 6 | DIVERGENCE | RSI/OBV sapma | Evet (varsayilan kapali) |
| 7 | REGIME SHIFT | Rejim degisimi | Evet |
| 8 | TIME LIMIT | Max sure | Evet (varsayilan 8h) |

---

## 10. P(win) / EV Istatistiksel Dogrulama

### 10.1 Hesaplama (F sisteminden)
```
Yon TF'deki swing verilerinden:
  forward_pcts = yon ile ayni yondeki dalgalar
  retrace_pcts = yon tersindeki dalgalar

P(win_cycle) = count(forward >= TP%) / total_forward
P(loss_cycle) = count(retrace >= SL%) / total_retrace
P(win) = P(win_cycle) / (P(win_cycle) + P(loss_cycle))
P(loss) = 1 - P(win)

EV% = P(win) x TP x kaldirac - P(loss) x SL x kaldirac - fee_ROI
```

### 10.2 Kullanim
- Hard gate degil, soft multiplier (varsayilan)
- EV > 0: Skor x (1 + EV/100), max 1.3x bonus
- EV < -10: Skor x (1 + EV/100), min 0.7x ceza
- Opsiyonel hard gate: `ev_hard_gate_enabled: true, ev_min: 0`

---

## 11. Backtest Optimizer (G Sisteminden)

### 11.1 Rol
```json
{
  "backtest_optimizer": {
    "enabled": true,
    "role": "advisor",
    "gatekeeper_min_score": 0.3,
    "gatekeeper_action": "block",
    "cache_hours": 4,
    "combos": 240,
    "show_in_table": true,
    "auto_run": false
  }
}
```
- "advisor": Tabloda gosterilir, karar etkilemez. Tikla -> detay popup.
- "gatekeeper": Son teyidci. Skor < min ise giris engellenir/kaldirac dusurulur.

### 11.2 Combo Matrix
- Kaldirac: [25, 50, 75, 100, 125, 150]
- TP: [0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 2.5, 3.0] %
- SL: ["no_sl", "0.5", "0.7", "1.0", "1.5"] %
- Toplam: 6 x 8 x 5 = 240 kombinasyon

### 11.3 Scoring
```
Score = 0.35xROI_norm + 0.25xWR - 0.20xDD_penalty - 0.15xLIQ_penalty + 0.05xTC_bonus
```

---

## 12. Pozisyon Yonetimi

### 12.1 Boyutlandirma
```python
def calculate_position_size(balance, config):
    min_pos = config.min_position_usd       # varsayilan: 1.0
    min_div = config.min_divider             # varsayilan: 4
    max_div = config.max_divider             # varsayilan: 12

    # Dinamik divider: bakiye kucukken az bolme, buyukken cok bolme
    divider = max(min_div, min(balance / min_pos, max_div))
    position_size = balance / divider

    # min_position kontrolu
    position_size = max(min_pos, position_size)

    return position_size
```

Ornekler:
| Bakiye | Divider | Pozisyon |
|--------|---------|----------|
| 4$ | 4 | 1.00$ |
| 5$ | 5 | 1.00$ |
| 8$ | 8 | 1.00$ |
| 12$ | 12 | 1.00$ |
| 24$ | 12 | 2.00$ |
| 120$ | 12 | 10.00$ |

### 12.2 Limitler
```json
{
  "position_sizing": {
    "min_position_usd": 1.0,
    "min_divider": 4,
    "max_divider": 12,
    "max_positions": 12,
    "sizing_mode": "and",
    "max_portfolio_pct": 8.33,
    "mr_max_positions": 2,
    "trend_max_positions": 10,
    "max_same_direction": 8,
    "max_per_coin": 1
  }
}
```

### 12.3 Yon Dengesi
```
direction_balance_enabled: true
direction_balance_ratio: "2-1"  (max 8 long 4 short veya tersi)
```

### 12.4 Coin Ban & Cooldown
```
coin_ban_enabled: true
coin_daily_loss_limit: 3
coin_daily_ban_hours: 24
loss_cooldown_enabled: true
loss_cooldown_seconds: 600  (10 dakika)
```

---

## 13. Dual Pool - TREND & RANGING

### 13.1 Havuz Ayirimi
```
ER > 0.35 -> TREND havuzu (max 10 slot)
ER < 0.20 -> RANGING (MR) havuzu (max 2 slot)
ER 0.20-0.35 -> Gray zone kararina gore (Section 4.2)
```

### 13.2 Farkli Stratejiler
| Parametre | TREND | RANGING |
|-----------|-------|---------|
| Giris | Market | Limit (BB bant) |
| SL | 1.5G | 2.0G |
| TP | Trailing (2.5G/0.5G) | BB middle (sabit) |
| Kaldirac | Normal | Normal (ranging SL daha genis -> daha dusuk kaldirac) |
| Min sinyal | 3/3 TF | 2/3 TF (cogunluk yonu) |

### 13.3 Rejim Gecisi
Pozisyon acikken rejim degisirse:
- RANGING -> TREND: TP'yi trailing'e cevir, SL'yi 1.5G'ye dar
- TREND -> RANGING: Trailing'i kapat, BB middle TP koy
- Config: `regime_switch_exit_enabled` ile tamamen cikilabilir

---

## 14. Server-Side Order Guvenligi

### 14.1 Pozisyon Acilisinda
```
1. STOP_MARKET (SL): G bazli, fee-aware
2. TRAILING_STOP_MARKET (trend): tetik=2.5G, callback=0.5G
   veya TAKE_PROFIT_MARKET (ranging): BB middle
```

### 14.2 Periyodik Guncelleme
- Her 30s: trailing callback guncelle (confluence gucune gore)
- Her 30s: SL ilerlet (trailing SL, sadece ileri, geri degil)
- Eksik emir tespiti: crash sonrasi otomatik yeniden yerlesim

---

## 15. Scanner Loop Performans Ayari

### 15.1 Iki Asamali Tarama
```
Faz 1 - Hizli Pre-Filtre (her 60s):
  - Top 50 coin cek (1 API call)
  - Hard filtreler uygula (FR, OB, volume) - cache'li
  - Basit 3-indikator yon kontrolu (5m veri, zaten cache'de)
  - Sonuc: ~15-20 aday

Faz 2 - Derin Analiz (sadece adaylar icin, her 120s):
  - Zoom diyafram (9 TF x aday sayisi API call, batch)
  - ER/Hurst hesaplama
  - P(win)/EV
  - MTF yon teyidi
  - Sonuc: Siralanmis final listesi
```

### 15.2 API Optimizasyonu
```
- Kline cache: 5m TF -> 60s TTL, 1h TF -> 300s TTL, 1d -> 3600s TTL
- Batch requests: max 5 paralel API call
- Rate limit: Binance 1200 req/min -> max 800 kullan (guvenlik payi)
- Funding rate: tek batch call (tum coinler)
- Orderbook: sadece final adaylar icin (top 10)
```

### 15.3 Config
```json
{
  "scanner": {
    "scan_interval_seconds": 60,
    "deep_analysis_interval_seconds": 120,
    "prefilter_top_n": 50,
    "deep_analysis_top_n": 15,
    "api_max_parallel": 5,
    "api_rate_limit_pct": 66,
    "kline_cache_ttl_5m": 60,
    "kline_cache_ttl_1h": 300,
    "kline_cache_ttl_1d": 3600,
    "orderbook_depth": 50,
    "orderbook_only_finals": true
  }
}
```

---

## 16. Opsiyonel Yapilar (Ac/Kapa)

```json
{
  "optional_features": {
    "signal_exit_enabled": true,
    "emergency_exit_enabled": true,
    "divergence_exit_enabled": false,
    "regime_switch_exit_enabled": true,
    "time_limit_enabled": true,
    "time_limit_hours": 8,
    "partial_tp_enabled": false,
    "btc_correlation_enabled": true,
    "orderbook_filter_enabled": true,
    "funding_rate_filter_enabled": true,
    "volume_filter_enabled": true,
    "backtest_optimizer_enabled": true,
    "mean_reversion_pool_enabled": true,
    "limit_entry_enabled": true,
    "direction_balance_enabled": true,
    "coin_ban_enabled": true,
    "loss_cooldown_enabled": true,
    "ev_validation_enabled": true,
    "hurst_confirmation_enabled": true,
    "gray_zone_trading_enabled": true
  }
}
```

---

## 17. GUI Tasarimi

### 17.1 Ana Ekran Yapisi
```
+-----------------------------------------------+
|  TREND HAVUZU (ER > 0.35)                     |
|  +-----+------+-----+-----+-----+----+------+ |
|  |Coin | Yon  |Score| Lev |  G  | TF | EV   | |
|  +-----+------+-----+-----+-----+----+------+ |
|  |ETHUS| LONG | 82  | 15x |1.2% |15m | +12% | |
|  +-----+------+-----+-----+-----+----+------+ |
|  | > Aktif Trend Pozisyonlari                 | |
|  | BTCUSDT LONG +2.3% 20x trailing aktif      | |
+-----------------------------------------------+
|  RANGING HAVUZU (ER < 0.20)                   |
|  +-----+------+-----+-----+-----+----+------+ |
|  |Coin | Yon  |Score| Lev |  G  |BB% | EV   | |
|  +-----+------+-----+-----+-----+----+------+ |
|  |XRPUS|SHORT | 71  | 10x |0.8% |82% | +8%  | |
|  +-----+------+-----+-----+-----+----+------+ |
|  | > Aktif MR Pozisyonlari                    | |
|  | SOLUSDT SHORT +0.8% BB mid TP bekliyor     | |
+-----------------------------------------------+
```

### 17.2 Tablo Sutunlari
TREND: Coin, Yon, Score, Kaldirac, G%, TF, ER, Hurst, EV%, FR, OB, Opt
RANGING: Coin, Yon, Score, Kaldirac, G%, BB%, TF, ER, EV%, FR
Pozisyon: Coin, Yon, Giris, Guncel, ROI%, SL%, TP%, Sure, Durum

---

## 18. Sinyal Cikis Sistemi (A'dan Miras)

### 18.1 Confluence Reversal
```
Karda: confluence <= signal_exit_threshold (-4) -> cik
Zararda: confluence <= signal_deep_exit_threshold (-8) -> cik
signal_only_in_profit: false (zararda da sinyal cikisi aktif)
signal_min_hold_seconds: 180 (ilk 3dk cikis yok)
```

### 18.2 Neden Onemli
Bir 3:1 kazanc, uc 1:1 kaybi karsilar.
Sinyal bozuldugunda beklemek yerine hemen cikmak, uzun vadede pozitif EV saglar.

---

## 19. Acik Kalan Konular / Gelecek Iyilestirmeler

- [ ] Partial TP: Kucuk portfoylerde (< 50$) Binance min order size sorunu
- [ ] Rejim gecisi: Pozisyon acikken strateji degisimi detaylari
- [ ] Multi-coin korelasyon: Ayni sektorden max N coin limiti
- [ ] Volatilite filtresi: Ani volatilite patlamalarinda giris engelleme
- [ ] Machine learning: Skor agirliklarini tarihsel veriden ogrenme

---

## 20. Onceki Sistemlerden Alinan Yapilar

| Yapi | Kaynak Sistem | Kullanim |
|------|--------------|----------|
| G dalga hesabi | B, D | Tum risk hesaplarinin temeli |
| Zoom diyafram | D | Optimal TF secimi |
| ER/Hurst rejim | B, H | Trend vs Ranging tespiti |
| P(win)/EV | F | Istatistiksel dogrulama |
| Fee-aware SL | A | 3 yerde tutarli formul |
| Server-side orders | A, H | STOP_MARKET + TRAILING |
| 7 seviye cikis | A | Onceliklendirmeli cikis kaskadi |
| Backtest optimizer | G | Advisor/gatekeeper rolu |
| Dual pool | A | TREND + MR ayri havuzlar |
| Sinyal cikis | A | Confluence reversal |
| Pozisyon yonetimi | A | Yon dengesi, ban, cooldown |
| BTC korelasyon | F, H | Beta filtresi |
| Orderbook analiz | A | Thin book, wall, imbalance |

---

## VERSIYON GECMISI
- v1.0 (2026-03-27): Ilk tasarim - istisare sonucu
