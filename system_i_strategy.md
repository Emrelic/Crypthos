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
- Binance Futures USDT-M, top X coin (24h islem hacmine gore)
- Y adet spike coin eklenir (son 5dk'da hacim patlamasi yasayan coinler)
- Toplam aday havuzu: X + Y coin
- Guncelleme: Her scan dongusunde (varsayilan 60s)

### 1.1.1 Spike Coin Tespiti
```
Spike kosulu (her ikisi birden saglanmali):
  1. |fiyat degisimi| >= spike_min_price_change (varsayilan: 1.5%)
  2. 24h hacim >= tum adaylarin median hacmi (thin-book sahte spike'lari elenir)
Spike havuzundan en yuksek |fiyat degisimi|'ne sahip Y coin secilir.
Spike coinler normal havuzla birlestirilir (tekrar eden coinler elenir).
```

### 1.2 Config
```json
{
  "universe": {
    "top_coin_count": 50,
    "spike_coin_count": 20,
    "spike_volume_ratio": 3.0,
    "spike_min_price_change": 1.5,
    "spike_lookback_minutes": 5
  }
}
```

### 1.3 Hard Filtreler (aday eleme)
| Filtre | Kosul | Ayarlanabilir | Varsayilan |
|--------|-------|---------------|------------|
| Funding Rate | abs(FR) < esik | funding_rate_max | 0.03% |
| Orderbook | spread < esik | max_spread_pct | 0.05% |
| Thin Book | depth > min | min_depth_usd | 100000 |
| Volume | vol_ratio >= esik | min_volume_ratio | 1.2 |
| Min Dalga | wave_count >= n | min_wave_count | 3 |
| Wall Blocking | imbalance < esik | max_wall_imbalance | 0.4 |

### 1.4 BTC Korelasyonu (opsiyonel)
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

Her TF icin N adet mum cekilir (G hesaplama mum sayisi):
  Kisa TF (1m-5m):   1000-1500 mum (1-5 gun veri)
  Orta TF (15m-2h):  300-500 mum   (5-25 gun veri)
  Uzun TF (4h+):     200 mum       (33+ gun veri)

NOT: Mum sayisi (N) config'den ayarlanabilir. Daha fazla mum = daha guvenilir
G ortalamasi, ama daha fazla API yuklenmesi. M parametresi ile geriye donuk
cekilecek ham mum sayisi belirlenir (ornegin M=1500 mum cekilir, N=1000'i
hesaplamada kullanilir, fark baslangic zigzag tespiti icin gereklidir).

Her TF icin hesaplama:
  1. M adet mum cek (ham veri)
  2. Zigzag swing tespiti (N=10 periyot)
  3. Ileri dalga boylari (I_pcts) ve geri dalga boylari (G_pcts) ayri hesapla
  4. G = ortalama geri dalga boyu (%)
  5. I = ortalama ileri dalga boyu (%)
  6. BW = geri dalga sayisi (guvenilirlik olcusu)
  7. FW = ileri dalga sayisi
  8. G/TF orani = (G artis%) / (TF artis%) — verimlilik olcusu
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
    "zoom_g_tf_inefficient": 0.80,
    "candle_count_short_tf": 1500,
    "candle_count_mid_tf": 500,
    "candle_count_long_tf": 200,
    "raw_candle_extra": 500
  }
}
```
- `candle_count_*`: G hesaplamasi icin kullanilan mum sayisi (N)
- `raw_candle_extra`: Ham cekim M = N + extra (zigzag baslangic payı)

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

## 3. Rejim Tespiti - Trend vs Ranging (ZOOM'DAN SONRA ILK ADIM)

> **ONEMLI:** Rejim, yondan ONCE belirlenir. Cunku rejim, yonun nasil
> hesaplanacagini dogrudan etkiler:
> - TREND rejiminde yon = momentum yonu (EMA, MACD)
> - RANGING rejiminde yon = TERS yon (RSI asiri bolge → ortalamaya donus)
> Eger yon rejimden once hesaplanirsa, ranging coinlerde yanlis yone girilir.

### 3.1 Birincil: Rolling Efficiency Ratio (ER)
```
ER = |net_fiyat_degisimi| / toplam_|bar_degisimleri|

ONEMLI: ER tum seri uzerinden degil, KISA PENCERE ile hesaplanir.
Kripto piyasasinda uzun pencere (200-500 mum) ER'yi daima ~0'a ceker
cunku her mum geri-ileri saliniyor. Kisa pencere gercek rejimi yakalar.

Yontem: Rolling ER
  1. Son W mum'luk pencerelerle ER hesapla (W = er_window, varsayilan: 20)
  2. Son N rolling ER'nin medyanini al (N = er_median_count, varsayilan: 10)
  3. Bu medyan = o anki rejim sinyali

Esikler (50 coin x 4 TF gercek piyasa verisinden kalibre):
  ER > 0.25 -> TRENDING   (yaklasik P75 — coinlerin ust %25'i)
  ER < 0.08 -> RANGING    (yaklasik P25 — coinlerin alt %25'i)
  ER 0.08-0.25 -> GRAY ZONE

NOT: Eski esikler (0.35/0.20) tum coinleri %100 RANGING siniflandiriyordu.
500 mum uzerinden hesaplanan ER'nin P90'i bile 0.14'tu.
```

### 3.2 Hurst Exponent (teyit) — Gelistirilmis
```
Hesaplama: Yon TF verisi uzerinde, R/S analizi
Eski: 4 chunk size [16,32,64,128], non-overlapping, gürültülü
Yeni: 11 chunk size [8,12,16,24,32,48,64,96,128,192,256], overlapping (%50)
Avantaj: daha stabil (düsük std, dar range), regresyon güvenilir

Esikler (kripto Hurst dagilimi genelde 0.55-0.65 arasinda yogunlasir):
  H > 0.60 -> TRENDING (persistent)
  H < 0.50 -> RANGING (mean-reverting)
  H 0.50-0.60 -> BELIRSIZ

NOT: Eski esikler (0.55/0.45) ile neredeyse her coin TRENDING cikiyordu
cunku kripto Hurst medyani ~0.59. Yeni esikler bunu duzeltiyor.
```

### 3.3 Gray Zone Karari: ER + Hurst
```
Gray zone (ER 0.08-0.25):

Hurst > 0.60 -> TREND gibi davran (kaldirac x0.7)
Hurst < 0.50 -> RANGING gibi davran (kaldirac x0.7)
Hurst 0.50-0.60 -> COK BELIRSIZ
  3/3 TF hizalama varsa -> dusuk kaldiracla TREND (x0.5)
  2/3 TF hizalama varsa -> dusuk kaldiracla RANGING (x0.5)
  1/3 veya 0/3 -> ISLEM YAPMA
```

### 3.4 Rejim Hysteresis
- Rejim degisimi icin 2 ardisik ayni okuma gerekli (varsayilan)
- Bootstrap: ilk okuma hemen kabul edilir
- Config: `regime_hysteresis_count: 2`

---

## 4. Yon Belirleme - Rejime Bagli Yon Tespiti

> Yon tespiti rejimden SONRA yapilir. Rejim, yon hesaplama mantigi belirler.

### 4.1 Indikatorler (her TF icin)
| Indikator | Parametreler | LONG | SHORT | Notr |
|-----------|-------------|------|-------|------|
| EMA 9/21 | gap >= 0.05% | EMA9 > EMA21 | EMA9 < EMA21 | gap < 0.05% |
| MACD 8/17/9 | histogram + momentum | hist > 0 AND artan | hist < 0 AND azalan | duz |
| RSI 14 | 55/45 esik | RSI > 55 | RSI < 45 | 45-55 arasi |

### 4.2 TREND Rejiminde Yon Tespiti
```
Momentum tabanli: fiyat nereye gidiyorsa oraya girilir.

Her TF: 3 indikator -> +1 (LONG), -1 (SHORT), 0 (notr)
TF yonu = toplam / 3
  >= +0.33 -> LONG
  <= -0.33 -> SHORT
  diger -> FLAT
```

### 4.3 RANGING Rejiminde Yon Tespiti
```
Ters mantik: fiyat neredeyse TERS yone girilir (ortalamaya donus).

RSI > 70 -> SHORT (asiri alim, dusus beklenir)
RSI < 30 -> LONG (asiri satim, yukselis beklenir)
RSI 30-70 -> BB bant yakınligi kontrol et:
  Fiyat > BB Upper x 0.95 -> SHORT
  Fiyat < BB Lower x 1.05 -> LONG
  Ortada -> SINYAL YOK (bekleme)
```

### 4.4 Coklu TF Hizalama
```
tf_count=2:
  Yon TF + Teyit TF ayni yon -> sinyal VAR
  Farkli -> sinyal YOK

tf_count=3:
  3/3 ayni yon -> guclu sinyal
  2/3 ayni yon -> zayif sinyal (kaldirac x0.7)
  1/3 veya 0/3 -> sinyal YOK
```

### 4.5 Rejim + TF Hizalama Iliskisi
```
TRENDING + 3/3 hizalama = KESIN TREND (tam giris)
TRENDING + 2/3 hizalama = TREND PULLBACK (bekle veya dusuk kaldirac)
RANGING + 2/3 hizalama = MR FIRSATI (cogunluk yonune ters gir)
RANGING + 3/3 hizalama = DIKKAT: momentum var, breakout riski → GIRME
GRAY ZONE = Hurst'e bak (Section 3.3)
```

### 4.6 Giris TF Kullanimi
Giris TF yonu ana yonle uyumlu oldugunda giris yapilir.
Giris TF'de EMA cross veya RSI donusu beklenir (hassas giris noktasi).

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

## 10. P(win) / EV Istatistiksel Dogrulama ve SL/TP Optimizasyonu

### 10.1 Temel Mantik
```
Piyasa "iki ileri bir geri" (veya "uc ileri bir geri") hareket eder.
Bu demektir ki:
  - SL noktasina ulasmak: 1 geri dalga yeterli (1-1.5G)
  - TP noktasina ulasmak: birden fazla ileri dalga gerekli (3-4G)

Dolayisiyla P(win) her zaman %50'nin ALTINDA olacaktir.
Ama kazanc/kayip orani (R:R) bunu telafi etmelidir.

Hedef: %40-%50 arasi kazanc orani ile R:R >= 1:2 saglayarak
       uzun vadede pozitif EV elde etmek.
```

### 10.2 Ileri ve Geri Dalga Analizi
```
Yon TF'deki zigzag swing verilerinden (yön belirlendikten sonra):

  forward_pcts = yon ile ayni yondeki dalga boylari (%)
  retrace_pcts = yon tersindeki dalga boylari (%)

Her SL ve TP adayi icin:
  P(SL_hit) = count(retrace >= SL%) / total_retrace
    → geri dalgalarin kaci SL noktasina ulasir?
  P(TP_hit) = count(forward >= TP%) / total_forward
    → ileri dalgalarin kaci TP noktasina ulasir?

KRITIK: TP'ye ulasmak icin birden fazla ileri dalga gerekebilir.
  P(TP_cumulative) = 1 - (1 - P(TP_single))^expected_waves
  expected_waves = ortalama ileri dalga sayisi (SL'ye yakalanmadan once)
```

### 10.3 SL/TP Optimizasyonu (ev_optimized modu)
```
tp_mode = "ev_optimized" ise:

1. SL aday seti olustur: [1.0G, 1.25G, 1.5G, 1.75G, 2.0G]
2. TP aday seti olustur: [1.5G, 2.0G, 2.5G, 3.0G, 3.5G, 4.0G, 4.5G, 5.0G]
3. Her SL x TP kombinasyonu icin:
   a. P(win) hesapla: ileri dalgalarin TP'ye ulasma olasiligi
   b. P(loss) hesapla: geri dalgalarin SL'ye ulasma olasiligi
   c. P(win_adj) = P(win) / (P(win) + P(loss))
   d. EV = P(win_adj) x TP x kaldirac - (1-P(win_adj)) x SL x kaldirac - fee
   e. R:R = TP / SL
4. En yuksek EV'yi veren SL/TP secilir
5. Esitlik varsa: daha yuksek R:R tercih edilir
6. EV < 0 olan tum kombinasyonlar → islem YAPMA

Ornek (gercek veriden):
  SL=1.5G, TP=3.0G: P(win)=42%, R:R=1:2.0, EV=+0.36G ✓
  SL=1.5G, TP=4.0G: P(win)=33%, R:R=1:2.7, EV=+0.32G ✓
  SL=1.0G, TP=2.5G: P(win)=48%, R:R=1:2.5, EV=+0.40G ✓✓ (en iyi)
  SL=2.0G, TP=2.0G: P(win)=55%, R:R=1:1.0, EV=+0.10G (zayif)
```

### 10.4 Minimum R:R Filtresi
```
ev_min_rr = 1.5 (varsayilan)
R:R < ev_min_rr olan kombinasyonlar elenir (EV pozitif olsa bile).
Cunku dusuk R:R, komisyon ve slippage'dan dolayi pratikte negatife donebilir.
```

### 10.5 Kullanim Modlari
```
ev_mode = "soft":     (varsayilan)
  EV > 0: Skor x (1 + EV/100), max 1.3x bonus
  EV < -10: Skor x (1 + EV/100), min 0.7x ceza
  SL/TP degismez (Section 7/8'deki sabit degerler kullanilir)

ev_mode = "optimizer":
  SL ve TP noktalarini EV'den hesapla (Section 10.3)
  Section 7/8'deki sabit degerler yerine optimize edilmis degerler kullanilir

ev_mode = "gate":
  Hard gate: EV < ev_min_threshold → islem engelle
  SL/TP degismez
```

### 10.6 Config
```json
{
  "ev": {
    "ev_mode": "optimizer",
    "ev_min_threshold": 0.0,
    "ev_min_rr": 1.5,
    "ev_sl_candidates": [1.0, 1.25, 1.5, 1.75, 2.0],
    "ev_tp_candidates": [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0],
    "ev_max_bonus": 1.3,
    "ev_min_penalty": 0.7,
    "ev_cumulative_waves": true
  }
}
```

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
ER 0.20-0.35 -> Gray zone kararina gore (Section 3.3)
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

### 15.1 Iki Asamali Tarama (DOGRU PIPELINE SIRASI)
```
Faz 1 - Hizli Pre-Filtre (her 60s):
  1. Top X coin + Y spike coin cek (1+1 API call)
  2. Hard filtreler uygula (FR, OB, volume) - cache'li
  3. Sonuc: ~15-20 aday

Faz 2 - Derin Analiz (sadece adaylar icin, her 120s):
  Pipeline sirasi (HER ADIM BIR ONCEKINE BAGIMLI):

  1. ZOOM: Her TF icin mum cek → zigzag → G hesapla
     → Dirsek noktasi bul → Optimal TF sec
     Sonuc: coin basina yon_tf + G degeri

  2. REJIM: Optimal TF'de ER + Hurst hesapla
     → TREND / RANGING / GRAY ZONE karar ver
     Sonuc: coin basina rejim

  3. YON: Rejime gore yon belirle
     → TREND: momentum tabanlı (EMA, MACD, RSI)
     → RANGING: ters mantik (RSI asiri bolge, BB bant)
     → MTF teyidi (Yon TF + Teyit TF hizalama)
     Sonuc: coin basina LONG / SHORT / FLAT

  4. SL/TP: Yon + Rejim + G'den SL/TP hesapla
     → ev_mode=optimizer ise: P(win)/EV optimizasyonu
     → ev_mode=soft ise: sabit G carpanlari + EV soft bonus/ceza
     Sonuc: coin basina SL%, TP%, kaldirac

  5. SKORLAMA: Tum verileri birlestir → final skor
     → Siralanmis final listesi

Neden bu sira zorunlu:
  - TF olmadan G hesaplanamaz
  - G olmadan rejim belirlenemez (ER, TF verisine bagli)
  - Rejim olmadan yon belirlenemez (TREND vs RANGING farkli mantik)
  - Yon olmadan SL/TP belirlenemez (ileri/geri dalga yonden turetilir)
  - SL olmadan kaldirac belirlenemez
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
    "spike_coin_count": 5,
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
- v1.1 (2026-03-29): Pipeline sirasi duzeltmesi (Rejim→Yon), EV optimizer detayi, spike coin, mum sayisi config
- v1.0 (2026-03-27): Ilk tasarim - istisare sonucu
