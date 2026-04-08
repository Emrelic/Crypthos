# System J - Maximum Leverage First Strategy
# Version: 1.0
# Date: 2026-04-01
# Status: IMPLEMENTATION

---

## 0. Felsefe

Amac: En yuksek minimum saglikli kaldiracta hizli islem yapmak.
- Kaldirac-odakli: max kaldiractan basla, G'yi saglayan TF'yi bul (TERS zoom)
- G dalga boyu tum risk hesaplarinin temeli (SL, TP, trailing)
- 3 turlu tarama: (1) max kaldirac, (2) G-bazli kaldirac, (3) zoom dirsek
- %50+ P(win) ile en az 1:2.5 R:R hedefi → pozitif EV
- Fee-aware tum hesaplamalar
- Basit ve hizli: gereksiz karmasiklik yok

---

## 1. Aday Secimi

### 1.1 Evren
- Binance Futures USDT-M, top X coin (24h islem hacmine gore)
- Spike coin YOK (basitlik icin)
- Varsayilan: X = 50
- Guncelleme: Her scan dongusunde

### 1.2 Config
```json
{
  "coin_sayisi": 50,
  "scan_interval_seconds": 60
}
```

### 1.3 Hard Filtreler (aday eleme)
| Filtre | Kosul | Config Key | Varsayilan |
|--------|-------|------------|------------|
| Funding Rate | abs(FR) < esik | funding_rate_max | 0.03% |
| Spread | spread < esik | max_spread_pct | 0.05% |
| Thin Book | depth > min | min_depth_usd | 100000 |
| Volume | vol_ratio >= esik | min_volume_ratio | 0.5 |
| Min Dalga | wave_count >= n | min_wave_count | 3 |

---

## 2. Kaldirac ve G Hesabi — Leverage-First Yaklasimi

### 2.1 Binance Leverage Bracket
```
GET /fapi/v1/leverageBracket → maintMarginRate (bakim marji orani)

Pratik likidasyon mesafesi:
  liq_dist = (1 / leverage) - maintMarginRate
  NOT: %70 sabit carpan YERINE gercek Binance bakım marjı kullanilir

Ornek (BTCUSDT, 125x bracket, maintMarginRate=0.4%):
  liq_dist = (1/125) - 0.004 = 0.8% - 0.4% = 0.4%
```

### 2.2 SL ve G Esigi Hesabi
```
SL = liq_dist / sl_divisor        (sl_divisor varsayilan: 2)
G_esik = SL / sl_g_mult           (sl_g_mult varsayilan: 1.5)

Ornek (125x, liq_dist=0.4%):
  SL = 0.4% / 2 = 0.20%
  G_esik = 0.20% / 1.5 = 0.133%

Yani: 125x kaldiracta islem yapabilmek icin G <= 0.133% olmali
```

### 2.3 G-den Geriye Kaldirac Hesabi (Tur 2)
```
Verilen G degerinden max kaldirac:
  SL = G * sl_g_mult + fee_total
  liq_dist = SL * sl_divisor
  teorik_liq = liq_dist + maintMarginRate
  max_leverage = floor(1 / teorik_liq)
  max_leverage = min(max_leverage, binance_max_leverage)
```

### 2.4 Config
```json
{
  "leverage": {
    "sl_divisor": 2,
    "sl_g_mult": 1.5,
    "fee_pct": 0.08,
    "slippage_pct": 0.04,
    "min_leverage": 2
  }
}
```

---

## 3. Uc Turlu Tarama Sistemi

### Tur 1 — Max Kaldirac Taramasi
```
Her coin icin:
  1. Binance max kaldirac al (leverageBracket API)
  2. maintMarginRate al
  3. G_esik hesapla (Section 2.2)
  4. TF merdiveni: 1m → 3m → 5m → 15m → 30m → 1h
  5. Her TF'de zigzag (N=5) → G hesapla
  6. G <= G_esik olan ILK TF = optimal TF
  7. Bulunursa: rejim + yon + EV kontrol → uygunsa GIRIS
  8. Bulunamazsa: coin Tur 2'ye kalir
```

### Tur 2 — G-Bazli Kaldirac Taramasi
```
Tur 1'de girilemeyenler icin:
  1. Her TF'de hesaplanan G'den max kaldiraci hesapla (Section 2.3)
  2. En iyi (en yuksek kaldirac) TF'yi sec
  3. O kaldiracta: rejim + yon + EV kontrol → uygunsa GIRIS
  4. Bulunamazsa: coin Tur 3'e kalir
```

### Tur 3 — Zoom Dirsek Taramasi (Fallback)
```
Hala bos slot varsa:
  1. System I tarzı alttan yukari G/TF verimlilik taramasi
  2. Dirsek noktasi = optimal TF
  3. O TF'nin G'sinden kaldirac hesapla (Section 2.3)
  4. Rejim + yon + EV kontrol → uygunsa GIRIS
```

### 3.1 Tarama Sirasi (DONGUSEL)
```
Turlar sirasiyla ve DONGUSEL calisir:
  Tur 1 → 50 coin max kaldiracla tara → slot dolana kadar
  Tur 2 → kalan coinler G-bazli kaldiracla → slot dolana kadar
  Tur 3 → kalan coinler zoom dirsekle → slot dolana kadar
  → Tur 1'e DON (yeni scan cycle baslat)

Her scan cycle (varsayilan 60s):
  Tur 1 → Tur 2 → Tur 3 → bekleme → Tur 1 → ...
  Slotlar dolunca sadece mevcut pozisyonlari izle
```

---

## 4. TF Merdiveni ve Zigzag

### 4.1 TF Listesi
```
1m, 3m, 5m, 15m, 30m, 1h
NOT: 10m Binance'de yok, 15m'e atlanir
```

### 4.2 Zigzag Parametreleri
```
swing_n = 5  (her iki yanda 5 mum = swing noktasi)
kline_limit = 200  (200 mum cekilir)
min_wave_count = 3  (en az 3 dalga olmali)
```

### 4.3 G Hesabi
```
Zigzag swing'lerden geri dalga boylarini hesapla:
  G = ortalama(geri_dalga_boyları_pct)
  I = ortalama(ileri_dalga_boyları_pct)
  CV = std(tum_dalgalar) / mean(tum_dalgalar)
```

---

## 5. Rejim Tespiti — ER + Hurst Dual-Vote

### 5.1 ER (Efficiency Ratio) — Kesin Bolgeler
```
Rolling ER: Son W mum'luk pencerelerle (W=20)
Medyan: Son 10 rolling ER'nin medyani

ER > 0.25 → TRENDING (kesin, ER tek basina yeter)
ER < 0.08 → RANGING  (kesin, ER tek basina yeter)
```

### 5.2 Gray Zone → Hurst Hakem (ER 0.08-0.25)
```
ER belirsiz bolgedeyken Hurst Exponent devreye girer:
  R/S analizi ile zaman serisinin hafizasi olculur (min 128 mum)

  H > 0.55 → TRENDING (persistent seri, confidence max 0.7)
  H < 0.45 → RANGING  (mean-reverting seri, confidence max 0.7)
  H 0.45-0.55 → ER tiebreaker:
    ER > midpoint (0.165) → TRENDING (confidence 0.3)
    ER <= midpoint        → RANGING  (confidence 0.3)

Hicbir coin atilmaz — gray zone tamamen cozulur.
Gray'den cozulen coinlerin confidence dusuk olur → skor dogal olarak cezalandirir.
GUI'de: "TRENDING(H=0.62)" veya "RANGING(H=0.38)" olarak gorulur.
```

### 5.3 200 Mum Rejim Analizi
```
N=5 ile her 10 mumda ~1 dalga
200 mumda ~20 dalga → yeterli istatistik
ER ve Hurst bu 200 mum uzerinden hesaplanir
```

### 5.4 Config
```json
{
  "regime": {
    "er_trending": 0.25,
    "er_ranging": 0.08,
    "er_window": 20,
    "er_median_count": 10,
    "gray_zone_skip": false,
    "hurst_trending": 0.55,
    "hurst_ranging": 0.45
  }
}
```

---

## 6. Yon Belirleme — Rejime Bagli

### 6.1 TRENDING Rejimde
```
Momentum tabanli (fiyat nereye gidiyorsa oraya):
  EMA fast/slow: config'den (varsayilan 9/21) — EMA_fast > EMA_slow → LONG
  MACD 8/17/9: histogram > 0 → LONG, < 0 → SHORT (binary oy, fraksiyonel degil)
  RSI 14: > rsi_long (55) → LONG, < rsi_short (45) → SHORT

3 indikatorun cogunlugu → yon (binary oylarla: en az 2 ayin yon)
En az 2/3 ayni yon → sinyal VAR
1/3 veya 0/3 → sinyal YOK
```

### 6.2 RANGING Rejimde
```
Ters mantik (ortalamaya donus):
  RSI > 70 → SHORT (asiri alim)
  RSI < 30 → LONG (asiri satim)
  BB bant yakinligi:
    Fiyat > BB Upper * 0.95 → SHORT
    Fiyat < BB Lower * 1.05 → LONG
  Ortada → SINYAL YOK
```

### 6.3 Teyit TF Eslemeleri (SABIT)
```
Islem TF → Teyit TF
1m       → 30m
3m       → 1h
5m       → 2h
15m      → 6h
30m      → 12h
1h       → 1d
```

### 6.4 Teyit Kurali
```
TRENDING: Islem TF yonu + Teyit TF yonu AYNI olmali (momentum teyidi)
  Farkli ise → SINYAL YOK

RANGING: Teyit TF GEREKMEZ (sadece islem TF sinyali yeterli)
  Sebep: Mean reversion mantigi trend teyidiyle celisir.
  RSI<30 (LONG, oversold) iken ust TF genelde DOWN gosterir →
  teyit aramak ranging sinyallerinin hepsini engeller.
```

---

## 7. P(win)/EV Hesabi ve SL/TP Optimizasyonu

### 7.1 Dalga Simulasyonu
```
Islem TF'deki zigzag dalga verisinden:
  forward_pcts: trend yonundeki dalga boyları
  retrace_pcts: ters yondeki dalga boyları

SL icin: P(SL_hit) = count(retrace >= SL%) / total_retraces
TP icin: P(TP_hit) = count(forward >= TP%) / total_forwards
```

### 7.2 Hedef Oranlar
```
Minimum P(win) = %35 (config: min_p_win=0.35)
  → v1.0'da %40 idi, v1.1'de gevsetildi
  → EV > 0 ve R:R >= 2.0 zaten yeterli koruma saglar
  → Ek guvenlik agi olarak %35 esik korunuyor

Minimum R:R = 1:2.0 (onceki: 1:2.5)
  → 2.5 fee yapisiyla yuksek kaldiraci imkansiz kiliyordu
  → 75x: 30 kombodan 2→4'e, 50x: 5→9'a cikiyor

EV = P(win) * net_TP * leverage - P(loss) * net_SL * leverage
  net_TP = tp_mult * G - fee_total  (fee dusulmus kar)
  net_SL = sl_mult * G + fee_total  (fee eklenmis zarar)
  NOT: Fee zaten TP/SL icinde, ayri fee_roi YOK (cift sayma olur)

EV > 0 → GIRIS YAPILABILIR
EV <= 0 → GIRME
```

### 7.3 SL/TP Optimizasyonu
```
SL adaylari: [1.0G, 1.25G, 1.5G, 1.75G, 2.0G]
TP adaylari: [2.0G, 2.5G, 3.0G, 3.5G, 4.0G, 5.0G]

Her SL x TP kombinasyonu icin:
  1. P(win), P(loss) hesapla
  2. EV hesapla (fee-aware)
  3. R:R kontrol (min 2.0)
  4. En yuksek EV'li kombo sec

Ek kisit: secilen SL, Tur 1/2'deki liq_dist / sl_divisor'u ASMAMALI
```

### 7.4 Config
```json
{
  "ev": {
    "min_p_win": 0.35,       // %35 minimum (EV optimizasyonunda kontrol edilir)
    "min_rr": 2.0,
    "ev_min_threshold": 0.0,
    "sl_candidates": [1.0, 1.25, 1.5, 1.75, 2.0],
    "tp_candidates": [2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
  }
}
```

---

## 8. Giris Stratejisi — Dalga Pozisyonu Tahmini

### 8.1 Dalga Pozisyonu
```
Son swing noktasindan itibaren fiyat nerede:
  wave_position = |fiyat - son_swing| / G

LONG giris:
  Fiyat dip yakininda (wave_pos < 0.3) → MARKET emir (dipteyiz)
  Fiyat ortada (0.3-0.7) → LIMIT emir (dibin yarisini hedefle)
  Fiyat tepede (> 0.7) → BEKLEME (geri cekilmeyi bekle)

SHORT giris:
  Fiyat tepe yakininda (wave_pos < 0.3) → MARKET emir
  Fiyat ortada → LIMIT emir (tepeyi hedefle)
  Fiyat dipte → BEKLEME
```

### 8.2 Limit Emir Hesabi
```
LONG limit fiyati:
  son_tepe = son SH (swing high) fiyati
  beklenen_dip = son_tepe * (1 - G/100)
  limit_fiyat = beklenen_dip + (G/100 * 0.5 * son_tepe)  # dibin yarisi
  = son_tepe * (1 - G/200)

SHORT limit fiyati:
  son_dip = son SL (swing low) fiyati
  beklenen_tepe = son_dip * (1 + G/100)
  limit_fiyat = beklenen_tepe - (G/100 * 0.5 * son_dip)
  = son_dip * (1 + G/200)

Timeout: 300s (dolmazsa iptal, sonraki coine gec)
```

### 8.3 Config
```json
{
  "entry": {
    "wave_dip_threshold": 0.3,
    "wave_mid_threshold": 0.7,
    "limit_g_offset_ratio": 0.5,
    "limit_timeout_seconds": 300
  }
}
```

---

## 9. Stop Loss

### 9.1 Hesaplama
```
TREND: SL = sl_g_mult * G + fee_total  (varsayilan 1.5G + 0.12%)
RANGING: SL = sl_g_mult * G + fee_total (ayni formul)
fee_total = fee_pct + slippage_pct = 0.08% + 0.04% = 0.12%

ONEMLI: SL, liq_dist / sl_divisor'u ASMAMALI
  SL = min(G_based_SL, liq_dist / sl_divisor)
```

### 9.2 Server-Side
```
STOP_MARKET emri → pozisyon acilinca hemen gonderilir
```

---

## 10. Kar Alim ve Cikis

### 10.1 TRENDING — Trailing Stop
```
Trailing tetik: trailing_trigger_g_mult * G (varsayilan 2.5G)
Trailing mesafe: trailing_callback_g_mult * G (varsayilan 0.5G)
Server: TRAILING_STOP_MARKET emri

Callback < 0.15% ise (yuksek kaldirac, kucuk G):
  Trailing cok siki → sabit TP'ye gec (trigger_mult * G)
  Sebep: Binance min callback 0.1%, G'ye oranla cok buyuk kalir → erken cikis

TP hedefleri fee ICERMEZ (Binance fee'yi marjdan duşer, TP brut fiyat)
```

### 10.2 RANGING — Sabit TP
```
TP hedef: BB middle (SMA20)
Alternatif: ranging_tp_g_mult * G (varsayilan 2.0G)
Server: TAKE_PROFIT_MARKET emri
```

### 10.3 EV-Optimized Override
```
Eger EV optimizasyonu daha iyi SL/TP bulduysa:
  O degerleri kullan (Section 7.3)
```

### 10.4 Config
```json
{
  "tp": {
    "trailing_trigger_g_mult": 2.5,
    "trailing_callback_g_mult": 0.5,
    "ranging_tp_target": "bb_middle",
    "ranging_tp_g_mult": 2.0
  }
}
```

---

## 11. Cikis Onceligi (5 Seviye — Basitlestirilmis)

| Oncelik | Cikis Tipi | Kosul | Kapatilabilir? |
|---------|-----------|-------|----------------|
| 0 | EMERGENCY | %80 liq mesafesi | HAYIR |
| 1 | STOP LOSS | G-bazli SL | HAYIR |
| 2 | TP / TRAILING | TP veya trailing | HAYIR |
| 3 | SIGNAL EXIT | Confluence reversal | EVET |
| 4 | TIME LIMIT | Max sure (8h) | EVET |

---

## 12. Pozisyon Yonetimi

### 12.1 Boyutlandirma
```
divider = max(min_divider, min(balance / min_position_usd, max_divider))
position_size = balance / divider
position_size = max(min_position_usd, position_size)
```

### 12.2 Limitler
```json
{
  "position": {
    "min_position_usd": 1.0,
    "min_divider": 4,
    "max_divider": 12,
    "max_positions": 12,
    "max_same_direction": 8,
    "max_per_coin": 1,
    "direction_balance_enabled": true,
    "direction_balance_ratio": "2-1",
    "loss_cooldown_seconds": 600,
    "coin_daily_loss_limit": 3,
    "coin_daily_ban_hours": 8
  }
}
```

---

## 13. Skorlama (Basit)

```
score = (
    direction_strength * 0.30 +
    ev_quality * 0.30 +
    regime_clarity * 0.20 +
    wave_quality * 0.20
) * 100

direction_strength: TF hizalama orani × ort sinyal gucu
ev_quality: normalize(ev_pct, -10, +30)
regime_clarity: ER kenar mesafesi (gray zone'dan uzaklik)
wave_quality: min(wave_count/10, 1) × (1 - min(cv/2, 1))
```

---

## 14. Server-Side Emir Guvenligi

```
Pozisyon acilinca:
  1. STOP_MARKET (SL): G-bazli, fee-aware
  2. TRENDING: TRAILING_STOP_MARKET (tetik + callback)
     RANGING: TAKE_PROFIT_MARKET (BB middle veya G-bazli)

Her 30s: Eksik emir kontrolu + yeniden gonderme
```

---

## 15. Opsiyonel Ozellikler

```json
{
  "optional": {
    "signal_exit_enabled": true,
    "signal_exit_threshold": -4.0,
    "signal_deep_exit_threshold": -8.0,
    "signal_min_hold_seconds": 180,
    "time_limit_enabled": true,
    "time_limit_hours": 8,
    "emergency_exit_enabled": true,
    "direction_balance_enabled": true,
    "coin_ban_enabled": true,
    "loss_cooldown_enabled": true
  }
}
```

---

## 16. Matematik Ozeti

```
Max kaldirac = L
Teorik liq = 1/L = %X
Pratik liq = 1/L - maintMarginRate
SL = pratik_liq / sl_divisor
G_esik = SL / sl_g_mult

Hedef:
  P(win) filtresi YOK (EV>0 + R:R yeterli koruma)
  R:R >= 1:2.0
  EV = P(win)*net_TP*L - P(loss)*net_SL*L > 0
  (fee zaten TP/SL icinde, ayri fee*L YOK)

Basabas formulleri (EV=0, fee haric saf matematik):
  R:R=2.0'da breakeven P(win) = 1/(1+2.0) = %33.3
  R:R=2.5'da breakeven P(win) = 1/(1+2.5) = %28.6
  R:R=3.0'da breakeven P(win) = 1/(1+3.0) = %25.0
  NOT: Sistem min R:R=2.0 + EV>0 → tum senaryolarda pozitif EV
```
