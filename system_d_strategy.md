# System D — Sıralı Coin Analiz & Trade Stratejisi (v2.0)

## 1. Genel Bakış
Top 50 coin (24h hacme göre), en yüksek hacimden başlayarak sırayla değerlendirilir.
Her coin için **Zoom Diyafram** ile optimal timeframe otomatik seçilir.
13 karar noktası belirlenir, uygun olanlar trade edilir.

## 2. Coin Seçimi
- Binance Futures'tan 24h USDT hacmine göre top 50 coin alınır
- En yüksek hacimli coinden başlanır, sırayla analiz edilir
- Her coin bağımsız değerlendirilir, sonraki coine geçilir

## 2.5. Zoom Diyafram — Dinamik TF Seçimi

### Konsept:
TF büyüdükçe G genelde büyür, ama her TF'de aynı hızda büyümez.
**Dirsek noktası**: TF artarken G'nin en az arttığı (veya azaldığı) nokta.

### Algoritma:
1. Tüm TF'lerde (5m → 1d) zigzag G hesapla
2. Verimlilik = TF_dakika / G_yüzde (yüksek = iyi)
3. G artış hızı = (G_sonraki - G_önceki) / (TF_sonraki - TF_önceki)
4. **Seçim**: Kaldıraç >= min_kaldirac olan TF'ler arasında:
   - G'nin azaldığı nokta varsa (negatif artış hızı) → dirsek!
   - Yoksa en yüksek verimliliği seç

### Dinamik TF Türetme (×12 kuralı):
- **Mikro TF** = Zoom'dan bulunan dirsek noktası (G ölçümü, giriş/çıkış sinyali)
- **Orta TF** = Mikro × 12 → en yakın Binance TF (rejim tespiti)
- **Makro TF** = Orta × 12 → en yakın Binance TF (yön tespiti)

### Örnek 1 (dirsek = 5m):
- Mikro: 5m, Orta: 60m → **1h**, Makro: 720m → **12h**

### Örnek 2 (dirsek = 1h):
- Mikro: 1h, Orta: 720m → **12h**, Makro: 8640m ≈ 6 gün → **1w**

### Zoom Tablosu Örneği:
| TF | G% | Kaldıraç | Verimlilik | Artış Hızı |
|----|-----|----------|------------|------------|
| 5m | 0.25 | 30x | 20 | - |
| 15m | 0.20 | 38x | 75 | **-0.005** (azalıyor!) |
| 30m | 0.35 | 22x | 86 | +0.010 |
| 1h | 0.45 | 17x | 133 | +0.003 |

→ 15m **dirsek** (G azalıyor): Mikro=15m, Orta=15×12=180m→**4h**, Makro=4h×12=2880m→**1d**

## 3. Yön Belirleme — Multi-Timeframe Oylama

### 3 Katman (dinamik TF'lerle):
| Katman | Timeframe | Ağırlık | Rol |
|--------|-----------|---------|-----|
| Makro  | Zoom × 16-24 | %50 | Ana trend |
| Orta   | Zoom × 4-6   | %30 | Swing yönü |
| Mikro  | Zoom optimal | %20 | Anlık momentum |

### İki Mod (opsiyonel):
- **Ağırlıklı oylama** (varsayılan): Skor = makro×0.5 + orta×0.3 + mikro×0.2
- **Mutabakat modu** (yon_mutabakat_modu=true): 3/3 veya 2/3 katman aynı yönde olmalı

### Her katmanda 3 indikatör:
1. **EMA 9/21 kesişimi**: EMA9 > EMA21 → +1 (LONG), EMA9 < EMA21 → -1 (SHORT)
2. **MACD histogram**: > 0 → +1, < 0 → -1
3. **RSI 50 çizgisi**: RSI > 50 → +1, RSI < 50 → -1

### Katman skoru: 3 indikatörün ortalaması (-1 ile +1 arası)

### Toplam skor:
```
toplam = makro_skor × 0.50 + orta_skor × 0.30 + mikro_skor × 0.20
```

### Karar:
- toplam > +0.1 → **LONG**
- toplam < -0.1 → **SHORT**
- -0.1 ile +0.1 arası → **SKIP** (belirsiz, coini atla)

### Çatışma kuralları:
- Makro + Orta aynı yön → güçlü sinyal (mikro ters olsa bile gir)
- Makro + Orta zıt yön → zayıf sinyal, 3/3 uyum gerekli yoksa SKIP
- 3/3 aynı yön → en güçlü sinyal

## 4. Rejim Tespiti — Trend mi Ranging mi

### Orta katman (1h) ağırlıklı, 2/3 oylama:
| İndikatör | Trend | Ranging |
|-----------|-------|---------|
| ADX       | > 25  | < 20    |
| BB Width  | Genişliyor | Dar/sabit |
| ER (Efficiency Ratio) | > 0.3 | < 0.3 |

- 2/3 Trend → **TREND**
- 2/3 Ranging → **RANGING**
- ADX 20-25 → **Gri bölge** (temkinli gir)

## 5. Kaldıraç Hesaplama — G Bazlı

### G = 5m timeframe zigzag swing ortalaması (geri dalga)

### Rejime göre SL ve kaldıraç:
| | Trend | Ranging |
|---|---|---|
| SL | 1.5 × G | 2 × G |
| Pratik Liq | 3 × G | 4 × G |
| Teorik Liq | (Pratik Liq + fee) / 0.7 | (Pratik Liq + fee) / 0.7 |
| Max Kaldıraç | 100 / Teorik Liq | 100 / Teorik Liq |

- fee = %0.08 (giriş maker + çıkış taker)
- Kaldıraç tamsayıya yuvarlanır (aşağı)

### Örnek (G = %0.5, Trend):
- SL = 0.75%, Pratik Liq = 1.5%, Teorik Liq = (1.5 + 0.08) / 0.7 = 2.26%
- Max Kaldıraç = 100 / 2.26 = **44x**

## 6. Stop Loss
- **Trend**: 1.5 × G
- **Ranging**: 2 × G
- Fee-aware: SL mesafesine fee eklenir

## 7. Kar Alım (TP)
- **Trend**: TP yok → trailing stop ile kâr uzatılır
- **Ranging**: BB karşı bant veya 3G (hangisi yakınsa)

## 8. Çıkış Tipi
| | Trend | Ranging |
|---|---|---|
| Çıkış | Trailing Stop | Sabit TP |
| Trailing tetik | 2G kârda | - |
| Trailing mesafe | 0.5G | - |

## 9. Pozisyon Büyüklüğü
- Bakiye / 12 (System A ile aynı)
- Min 1 USDT
- Max 12 pozisyon

## 10. Giriş Tipi
- **Limit emir** (maker fee %0.02 kazancı)
- Offset: 0.1 × ATR (çok yakın, hızlı dolacak)
- Long → fiyat - 0.1×ATR
- Short → fiyat + 0.1×ATR
- Timeout: 60sn, dolmazsa iptal ve sonraki coine geç

## 11. Giriş Zamanlaması
- Sinyal gelince **hemen** limit emir at

## 12. Korelasyon
- Hacim sırası yeterli, ek korelasyon kontrolü yok

## 13. Max Pozisyon
- 12 pozisyon

## 14. Cooldown
- Yok (şimdilik)

## 15. Funding Rate Kontrolü
| Durum | Aksiyon |
|---|---|
| FR > %0.1 | Long girme |
| FR < -%0.1 | Short girme |
| ±%0.1 arası | Serbest |

## 16. Config Parametreleri
```json
{
  "system_d": {
    "enabled": false,
    "coin_sayisi": 50,
    "makro_tf": "1d",
    "makro_tf_mum": 200,
    "orta_tf": "1h",
    "orta_tf_mum": 200,
    "mikro_tf": "5m",
    "mikro_tf_mum": 200,
    "makro_agirlik": 0.5,
    "orta_agirlik": 0.3,
    "mikro_agirlik": 0.2,
    "yon_belirsiz_esik": 0.1,
    "adx_trend_esik": 25,
    "adx_ranging_esik": 20,
    "er_trend_esik": 0.3,
    "er_ranging_esik": 0.3,
    "swing_n": 10,
    "sl_carpan_trend": 1.5,
    "sl_carpan_ranging": 2.0,
    "pratik_liq_carpan_trend": 3.0,
    "pratik_liq_carpan_ranging": 4.0,
    "liq_seviyesi": 0.7,
    "fee_pct": 0.08,
    "trailing_tetik_g_carpan": 2.0,
    "trailing_mesafe_g_carpan": 0.5,
    "ranging_tp_g_carpan": 3.0,
    "limit_atr_offset": 0.1,
    "limit_timeout_seconds": 60,
    "portfoy_bolen": 12,
    "max_pozisyon": 12,
    "max_funding_rate": 0.001,
    "scan_interval_seconds": 30,
    "min_kaldirac": 2,
    "max_kaldirac": 125,
    "fee_rate": 0.0004,
    "rsi_periyot": 14,
    "ema_fast": 9,
    "ema_slow": 21,
    "macd_fast": 8,
    "macd_slow": 17,
    "macd_signal": 9,
    "adx_periyot": 14,
    "bb_periyot": 20,
    "bb_std": 2.0
  }
}
```
