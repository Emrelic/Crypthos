# System B Strateji Belgesi v6.0
# Dalga Analizi & Salınım Ticareti — G Bazlı Tek N Sistemi
# Tarih: 2026-03-21

---

## 1. Genel Amaç

Portföyü günlük %5-15 getiri ile büyütmek (iyimser senaryoda daha fazla).
- Portföy 1/12'ye bölünerek pozisyon açılır (canlı bakiye üzerinden)
- Win rate hedefi: %50
- Risk:Reward: min 1:1.33 (G bazlı, coin'den coin'e değişir)
- En hacimli 50 coin taranır
- Ya System A ya System B çalışır (aynı anda ikisi çalışmaz)
- Sistem iki yönlü: LONG ve SHORT

---

## 2. Veri Toplama — Çoklu Zaman Dilimi (MTF)

### Büyük TF: Makro Trend Tespiti
```
buyuk_tf = "1h"              # 1 saatlik mumlar
buyuk_tf_mum = 168           # 7 günlük veri
Amaç: Genel piyasa rejimi (trend mi ranging mi)
```

### Küçük TF: Giriş Zamanlaması
```
kucuk_tf = "5m"              # 5 dakikalık mumlar
kucuk_tf_mum = 288           # 1 günlük veri (ayarlanabilir)
Amaç: Swing tespiti, giriş zamanlaması, entry teyidi
```

### Her Coin İçin Çekilen Veri
```
1. 168 × 1h mum → ER_makro (makro rejim)
2. 288 × 5m mum → ER_mikro + Hurst + Zigzag + RSI + Volume
```

### API Yükü
```
Her 5dk'da: 50 coin × 2 çağrı = 100 API call
Binance ağırlık: ~200/dakika (limit 1200) → sorun yok
```

---

## 3. Trend / Ranging Tespiti — Çoklu Zaman Dilimi

### Katman 1: Makro ER (1h, 168 mum = 7 gün)
```
ER_makro = |son kapanış - ilk açılış| / Σ|close(i) - close(i-1)|

ER_makro < 0.15   → Makro RANGING
ER_makro 0.15-0.35 → Makro GEÇİŞ
ER_makro > 0.35   → Makro TREND
```

### Katman 2: Mikro ER (5m, 288 mum = 1 gün)
```
ER_mikro < 0.2   → Mikro RANGING
ER_mikro 0.2-0.4  → Mikro GEÇİŞ
ER_mikro > 0.4   → Mikro TREND
```

### Katman 3: Hurst Exponent (5m, 288 mum)
```
R/S analizi, n = 16, 32, 64, 128 → log-log regresyon → H

H < 0.45 → RANGING, H 0.45-0.55 → BELİRSİZ, H > 0.55 → TREND

⚠ Hurst karar verici değil, loglama amaçlı. MTF uyuşması yeterli.
```

### MTF Karar Matrisi — 4 Kural
```
Kural 1: Makro + Mikro AYNI → o rejim (tam güven)
Kural 2: Biri GEÇİŞ, diğeri net → diğerinin ZAYIF versiyonu (×0.5)
Kural 3: Makro + Mikro ÇELİŞİYOR → KARARSIZ (işlem yok)
Kural 4: İkisi de GEÇİŞ → KARARSIZ (işlem yok)
```

### Rejim Hysteresis
```
Rejim değişikliği: ardışık 3 okuma (15dk) aynı kategoride olmalı.
Bootstrap: ilk okumada hysteresis=1, sonra 3'e döner.
```

### Trend Yönü Belirleme
```
Makro yön: 1h, ilk açılış vs son kapanış (7 gün)
Mikro yön: 5m, son 72 mumun açılış vs kapanış (6 saat)

Aynı yön → YÖN TEYİTLİ
Farklı yön → kaldıraç × 0.5
```

---

## 4. Zigzag Swing Tespiti — Tek N Sistemi

### Temel Prensip
```
Tek N değeri kullanılır. SL, trailing, R:R hepsi aynı dalga ölçeğinden.
Çoklu N karmaşıklığı ve ölçek uyumsuzluğu ortadan kalkar.

swing_n = 10 (config, ayarlanabilir)
5dk × 10 = 50dk pencere
```

### Swing High/Low
```
Swing High: high(i) > tüm high(i-N..i-1) VE high(i) > tüm high(i+1..i+N)
Swing Low:  low(i)  < tüm low(i-N..i-1)  VE low(i)  < tüm low(i+1..i+N)
```

### G Hesaplama (Geri Dalga Boyu)
```
Swing noktaları kronolojik sıralanır:
  SL1 → SH1 → SL2 → SH2 → SL3 → ...

İleri dalgalar (trend yönünde): SL→SH (yükseliş) veya SH→SL (düşüş)
Geri dalgalar (trend tersine): SH→SL (yükseliş) veya SL→SH (düşüş)

G = geri dalga ortalaması (%)
I = ileri dalga ortalaması (%)

Yüzdesel: dalga% = |bitiş - başlangıç| / başlangıç × 100
```

### Minimum Filtreler
```
En az 2 ileri + 2 geri tamamlanmış dalga olmalı.
G < %0.1 → ATLA (fee tuzağı)
Tamamlanmamış son dalga sayılmaz.
```

### Dalga Tutarlılık Filtresi (CV)
```
ileri_CV = std(ileri dalgalar) / mean(ileri dalgalar)
geri_CV  = std(geri dalgalar) / mean(geri dalgalar)
CV = max(ileri_CV, geri_CV)

CV < 0.3  → TAM GÜVEN
CV 0.3-0.6 → kaldıraç × 0.7
CV > 0.6  → BU COİN'İ ATLA
```

---

## 5. G Bazlı Trend Modu — SL / Trailing / Kaldıraç

### Tüm Hesaplamalar G'den Türer
```
G = geri dalga boyu ortalaması (%)
I = ileri dalga boyu ortalaması (%)

SL       = sl_carpan × G         (varsayılan: 1.5)
Tetik    = tetik_carpan × G      (varsayılan: 2.5)
Trail    = trail_carpan × G      (varsayılan: 0.5)

Tek kaynak (G), tek ölçek, hiçbir uyumsuzluk yok.
```

### Kontroller
```
Trail < Tetik: 0.5G < 2.5G ✓ (her zaman, çarpanlar doğru olduğu sürece)
Trail < SL:    0.5G < 1.5G ✓ (her zaman)

Min kâr = Tetik - Trail = 2.5G - 0.5G = 2.0G
Min R:R = SL : min_kâr = 1.5G : 2.0G = 1:1.33 ✓

Fiyat daha ileri giderse (trailing koşturur):
  3G kâr → R:R = 1.5:2.5 = 1:1.67
  4G kâr → R:R = 1.5:3.5 = 1:2.33
  5G kâr → R:R = 1.5:4.5 = 1:3.00
```

### Sayısal Örnek (G = %2)
```
SL    = 1.5 × 2 = %3
Tetik = 2.5 × 2 = %5
Trail = 0.5 × 2 = %1

SL güvenliği: normal geri dalga = %2, SL = %3 → %50 tampon ✓
Tetik ulaşılabilirliği:
  Güçlü trend (I=%3, net/cycle=%1): %5/%1 = 5 cycle ≈ 8 saat ✓
  Orta trend (I=%2.5, net=%0.5): %5/%0.5 = 10 cycle ≈ 17 saat — zor
  Zayıf trend: ulaşılamaz → SL kapanır (doğru davranış)

Trailing sonrası:
  Tetik noktasında (%5 kâr): ilk geri çekilme %1'den fazla → kapanır
  Kâr = %5 - %1 = %4 → R:R = 3:4 = 1:1.33 minimum ✓
```

### R:R Filtresi
```
beklenen_kar = I × 0.8 - Trail  (ileri dalganın %80'i hedef, trail düşülür)
risk = SL = 1.5 × G

beklenen_RR = risk : beklenen_kar
beklenen_RR < 1:1.3 → bu coin'i ATLA

Örnek: G=%2, I=%3
  beklenen_kar = 3×0.8 - 1 = %1.4
  risk = %3
  R:R = 3:1.4 = 1:0.47 → ATLA!

Örnek: G=%2, I=%5
  beklenen_kar = 5×0.8 - 1 = %3
  risk = %3
  R:R = 3:3 = 1:1.0 → ATLA (1.3 altında)

Örnek: G=%1, I=%4
  beklenen_kar = 4×0.8 - 0.5 = %2.7
  risk = %1.5
  R:R = 1.5:2.7 = 1:1.8 ✓ GEÇİYOR
```

### Emir Yapısı (Trend)
```
Pozisyon açılınca 2 emir:
  Emir 1: STOP_MARKET → SL = giriş fiyatından 1.5G uzaklıkta
  Emir 2: TRAILING_STOP_MARKET
    activationPrice = giriş fiyatı ± 2.5G
    callbackRate = 0.5G (%)

Binance kısıtları:
  callbackRate: clamp(0.5G, 0.1, 5.0)
  Eğer clamp sonrası trail ≥ tetik → TRAILING YERINE SABİT TP:
    TP = I × 0.8 (ileri dalganın %80'i)
```

### Kaldıraç (Fee-Aware)
```
SL% = 1.5 × G + slippage_buffer (%0.1)

K = 35 / (SL% + 0.08)

Tablo (slippage = %0.1):
  G = %0.5 → SL% = %0.85 → K = 37x
  G = %1   → SL% = %1.6  → K = 20x
  G = %2   → SL% = %3.1  → K = 11x
  G = %3   → SL% = %4.6  → K = 7x
  G = %5   → SL% = %7.6  → K = 4x

Sınırlar: K > Binance max → cap, K < 2 → işlem açma
```

### Kaldıraç Çarpanları
```
Zayıf rejim güveni (ZAYIF TRD/RNG):     × 0.5
Trend yönü çelişkisi (makro ≠ mikro):   × 0.5
Orta dalga tutarlılığı (CV 0.3-0.6):    × 0.7
Giriş teyit skoru = 1:                  × 0.7
Funding rate karşıt yön (|FR|>%0.05):   × 0.7
Çarpanlar birleşir. Sonuç K < 2 → işlem açma.
```

---

## 6. Giriş Teyit Sistemi (Entry Confirmation)

En az 2/3 teyit gerekli:

### Teyit 1: RSI Momentum
```
RSI(14), 5m mumlar
LONG: RSI < 40 (trend) / RSI < 35 (ranging)
SHORT: RSI > 60 (trend) / RSI > 65 (ranging)
```

### Teyit 2: Volume
```
Volume MA = son 20 mum ortalaması
Tükenme: son 3 mum ort vol < MA × 0.8 → teyit
Climax: son mum vol > MA × 1.5 + dönüş yönü mum → teyit
```

### Teyit 3: Dönüş Mumu
```
LONG: close > open (yeşil), bonus: alt fitil uzun (hammer)
SHORT: close < open (kırmızı), bonus: üst fitil uzun (shooting star)
```

### Skor
```
Skor ≥ 2 → GİRİŞ ONAYLI
Skor = 1 → GİRİŞ ONAYLI, kaldıraç × 0.7
Skor = 0 → BEKLE
```

---

## 7. Teyitsiz Swing Tahmini

```
Son teyitli swing (N=10): SH = 103.1 (mum 270)
G (geri dalga ort) = %2.79
Fiyat (mum 288) = 100.5
Düşüş = %2.52, dalga yüzdesi = %2.52/%2.79 = %90.3

Dalga pozisyon kararları (trend, yükseliş):
  < %30 → BEKLE
  %30-%60 → LİMİT HAZIRLA (skor ≥ 1)
  %60-%90 → LİMİT GİR (skor ≥ 2)
  > %90 → HEMEN GİR (skor ≥ 2)
```

---

## 8. Limit Emir Yönetimi

```
LONG limit: fiyat × (1 - limit_buffer%)
SHORT limit: fiyat × (1 + limit_buffer%)
"HEMEN GİR" = market emir

Timeout: 15dk → iptal
Kısmi dolum: ≥%50 kabul, <%50 iptal+kapat
Bekleyen limitler slot kaplar (6 slot toplam)
```

---

## 9. RANGING Modu Stratejisi

### Bant Tespiti
```
864 fiyat noktası (high, low, close) sıralanır:
  taban = %20 percentile, tavan = %80 percentile
  bant_yuzde = (tavan - taban) / taban × 100
  bant_yuzde < %0.3 → ATLA
```

### Filtreler
```
Funding: |FR| > %0.1 → ATLA, karşıt yön > %0.05 → kaldıraç × 0.7
Spread: spread > SL% × 0.1 → ATLA
Breakout: son 5 mum bant dışı → yeni pozisyon AÇMA, 3 mum bant içi bekle
```

### Giriş
```
LONG: fiyat ≤ %20 seviye + skor ≥ 2 → al
SHORT: fiyat ≥ %80 seviye + skor ≥ 2 → sat

Aynı coin'de max 1 pozisyon.
Ardışık: long kapat → short aç → short kapat → long aç mümkün.
```

### Emir Yapısı
```
LONG:  SL = taban (%0), TP = %60 seviyesi
SHORT: SL = tavan (%100), TP = %40 seviyesi
Trailing YOK, sabit TP. Brüt R:R = 1:2.
```

### Kaldıraç
```
G_ranging = bant_yuzde × 0.20 (giriş → SL mesafesi)
SL% = G_ranging + slippage_buffer
K = 35 / (SL% + 0.08)
Aynı çarpanlar uygulanır.
```

---

## 10. Pozisyon Boyutu — Sabit 1/12

```
pozisyon_margin = canli_bakiye / 12

Risk yaklaşıklığı: K×(SL%+0.08) ≈ %35
Trade başına risk ≈ bakiye × %2.9

max_pozisyon = 6 (bakiye/2 kullanımda, bakiye/2 serbest)
```

---

## 11. Yön Diversifikasyonu

```
max_ayni_yon = 4
6 pozisyondan max 4 aynı yönde (korelasyon koruması).
```

---

## 12. Rejim Değişikliği (Açık Pozisyon)

```
Hysteresis (3 okuma) sonrası:
  TREND → RANGING: trailing iptal → sabit TP + SL güncelle
  RANGING → TREND: TP iptal → trailing + SL güncelle
  → KARARSIZ: kârdaysa kapat, zardaysa SL sıkılaştır
```

---

## 13. Risk Yönetimi

```
SL sonrası: 30dk cooldown (aynı coin)
Ardışık 2 SL: gün sonuna kadar ban (aynı coin)
Kârlı çıkış: cooldown yok
Günlük kayıp limiti: KAPALI (config'den açılabilir, %20)
Aynı coin'de max 1 pozisyon

Pozisyon kontrolü (her 5dk):
  futures_position_information() + futures_get_open_orders()
  Kapanan pozisyon → kâr/zarar → cooldown kararı
```

---

## 14. Tarama Döngüsü

```
Her 5 dakikada bir:

1. Top 50 coin (hacim sırasına göre)

2. Her coin:
   a. Ban/cooldown kontrolü
   b. Yön kontrolü (max_ayni_yon)
   c. 168×1h mum → ER_makro
   d. 288×5m mum → ER_mikro + Hurst
   e. MTF 4 kural → rejim kararı
   f. Hysteresis teyit (3 okuma)
   g. KARARSIZ → atla
   h. Zigzag swing (N=10) → G ve I hesapla
   i. Dalga sayısı (min 2+2) + G > %0.1
   j. CV filtresi (> 0.6 → atla)
   k. Spread filtresi + funding filtresi
   l. Ranging: bant genişliği + breakout kontrolü
   m. Trend yönü teyiti (makro vs mikro)
   n. R:R kontrolü (< 1:1.3 → atla)
   o. Kaldıraç hesapla × çarpanlar (< 2 → atla)
   p. Teyitsiz swing tahmini → dalga pozisyonu
   q. Entry confirmation (RSI + Vol + PA, skor ≥ 2)
   r. Pozisyon boyutu (bakiye/12)
   s. Pozisyon aç + SL + trailing/TP emirleri

3. Açık pozisyon kontrolü:
   a. Pozisyon durumu polling
   b. Rejim değişikliği → emir güncelle
   c. SL/TP/trailing tetiklendi → cooldown
   d. (Opsiyonel) günlük kayıp kontrolü

4. 5dk bekle, tekrarla
```

---

## 15. Günlük Hedef Matematik

```
R ≈ bakiye × %2.9 (trade başına SL kaybı)

İyimser (%50 WR, R:R=1:2.3, 8 trade):
  4×2.3R - 4×1R = +5.2R → %15

Gerçekçi (%45 WR, R:R=1:1.5, 6 trade):
  2.7×1.5R - 3.3×1R = +0.75R → %2.2

Kötü (%40 WR, çoğu SL, 4 trade):
  1.6×1.3R - 2.4×1R = -0.32R → %-0.9

Hedef: günlük %2-5 ortalama, iyi günlerde %10-15
Trade sayısı: 4-8 (filtreler çoğunu eler)
```

---

## 16. Ayarlanabilir Parametreler (config)

```json
{
  "system_b": {
    "enabled": false,

    "buyuk_tf": "1h",
    "buyuk_tf_mum": 168,
    "kucuk_tf": "5m",
    "kucuk_tf_mum": 288,
    "coin_sayisi": 50,

    "swing_n": 10,
    "min_dalga_sayisi": 2,
    "min_dalga_boyu": 0.1,

    "er_makro_ranging": 0.15,
    "er_makro_trending": 0.35,
    "er_mikro_ranging": 0.2,
    "er_mikro_trending": 0.4,
    "hurst_ranging_esik": 0.45,
    "hurst_trending_esik": 0.55,
    "rejim_degisim_teyit": 3,
    "yakin_yon_mum_sayisi": 72,

    "rsi_periyot": 14,
    "rsi_long_esik": 40,
    "rsi_short_esik": 60,
    "rsi_ranging_long_esik": 35,
    "rsi_ranging_short_esik": 65,
    "volume_ma_periyot": 20,
    "volume_azalma_carpani": 0.8,
    "volume_climax_carpani": 1.5,
    "min_entry_skor": 2,

    "sl_carpan": 1.5,
    "tetik_carpan": 2.5,
    "trail_carpan": 0.5,
    "slippage_buffer": 0.1,
    "trailing_min_callback": 0.1,
    "trailing_max_callback": 5.0,
    "min_rr_oran": 1.3,

    "zayif_kaldirac_carpani": 0.5,
    "yon_celiskisi_carpani": 0.5,
    "cv_orta_carpani": 0.7,
    "cv_max_esik": 0.6,
    "entry_tek_teyit_carpani": 0.7,
    "funding_carpani": 0.7,

    "ranging_bant_alt_percentile": 20,
    "ranging_bant_ust_percentile": 80,
    "ranging_long_giris_seviye": 0.20,
    "ranging_long_cikis_seviye": 0.60,
    "ranging_short_giris_seviye": 0.80,
    "ranging_short_cikis_seviye": 0.40,
    "min_bant_genisligi": 0.3,
    "breakout_mum_sayisi": 5,
    "breakout_teyit_mumlar": 3,

    "pratik_liq_faktoru": 0.70,
    "fee_rate": 0.0004,
    "max_funding_rate": 0.001,
    "funding_uyari_esik": 0.0005,
    "max_spread_sl_oran": 0.1,

    "portfoy_bolen": 12,
    "max_pozisyon": 6,
    "min_kaldirac": 2,
    "max_ayni_yon": 4,

    "limit_buffer_yuzde": 0.05,
    "limit_timeout_dakika": 15,
    "hemen_gir_market": true,
    "min_dolum_orani": 0.5,

    "loss_cooldown_dakika": 30,
    "profit_cooldown_dakika": 0,
    "max_ardisik_kayip": 2,
    "gunluk_kayip_limiti_enabled": false,
    "max_gunluk_kayip_yuzde": 20,

    "dalga_pozisyon_bekle": 0.30,
    "dalga_pozisyon_limit": 0.60,
    "dalga_pozisyon_gir": 0.90
  }
}
```

---

## 17. System A / System B Geçişi

- GUI'de A ve B sekmeleri
- Ya A ya B açılır (aynı anda çalışmaz)
- Geçişte açık pozisyonlar kapatılmalı
- Tüm config GUI'den B grubu ayarları olarak erişilebilir

---

## 18. Değişiklik Geçmişi

### v1.0-v5.1 (2026-03-20)
- 10 bölge → zigzag → fee-aware → senaryo düzeltmeleri → MTF → entry teyit
- Çoklu N (N=10 giriş, N=20/25 SL) → ölçek uyumsuzluğu sorunları

### v6.0 (2026-03-21) — G Bazlı Tek N Sistemi
- KRİTİK YENİDEN TASARIM: Çoklu N kaldırıldı → Tek N (swing_n=10)
- G bazlı sistem: SL=1.5G, Tetik=2.5G, Trail=0.5G (her şey G'den türer)
- N=10 vs N=20 ölçek uyumsuzluğu tamamen çözüldü
- R:R min 1:1.33 garanti (1.5G : 2.0G)
- Config sadeleşti: swing_n_giris + swing_n_sl → tek swing_n
- sl_carpan, tetik_carpan, trail_carpan → kullanıcı istediği dengeyi kurar
- Pozisyon boyutu: eşit risk → sabit 1/12 (System A ile tutarlı)
- Günlük kayıp limiti: varsayılan KAPALI
- Aynı coin'de max 1 pozisyon kuralı eklendi
- R:R filtresi: min 1:1.3 (I×0.8 - trail > 1.3 × SL)
- 50+ config parametresi, tümü GUI'den ayarlanabilir
