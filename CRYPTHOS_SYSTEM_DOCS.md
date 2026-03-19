# CRYPTHOS TİCARET SİSTEMİ - TAM DOKÜMANTASYON

## 1. GENEL MİMARİ ve AKIŞ

Sistem `main.py`'den başlıyor ve şu bileşenleri başlatıyor:

- **ConfigManager** → `config.json` yükler
- **EventBus** → Bileşenler arası pub-sub iletişim
- **BinanceRestClient** → Binance Futures API ile iletişim
- **ScannerStateMachine** → Ana orkestratör (tarama → alım → tutma → satış döngüsü)
- **PositionManager** → Açık pozisyonları izler, 7 farklı çıkış sinyali değerlendirir
- **ApiOrderExecutor** → Emirleri Binance API'ye gönderir
- **RiskManager** → Bakiye, drawdown, ardışık kayıp takibi
- **IndicatorEngine** → 15+ teknik indikatör hesaplar
- **ScannerScorer** → Coinleri puanlar ve sıralar

---

## 2. KRİPTO TARAMA ve SEÇİM SÜRECİ

Her **30 saniyede** bir döngü çalışır:

### Adım 1: Evren Belirleme
- Binance'ten **~50 yüksek hacimli coin** çekilir (min 5M USDT 24h hacim)

### Adım 2: Veri Toplama
- Her coin için **200 mum** (varsayılan 5m) OHLCV verisi indirilir
- **10 paralel thread** ile hızlı çekim

### Adım 3: İki Aşamalı Duyarlılık Analizi
- **Aşama 1**: TÜM coinler için funding rate (tek API çağrısı)
- **Aşama 2**: Sadece **ilk 15 aday** için Open Interest + OrderBook derinliği

### Adım 4: Puanlama
- Ön puanlama (tüm coinler, sadece funding rate ile)
- **İlk 15**'e tam sentiment verisi ile yeniden puanlama
- **İlk 5**'e Multi-Timeframe analiz (2-üst ve 5-üst zaman dilimi kontrolü)

### Adım 5: Filtreleme
- 12 sert filtreden geçenler **eligible** olur
- Puana göre sıralanır, en iyi fırsatlar önce

---

## 3. PUANLAMA ALGORİTMASI

### Bileşik Puan Ağırlıkları (toplam -100 ile +100):

| Bileşen | Ağırlık | Açıklama |
|---------|---------|----------|
| **Confluence** | %35 | Sinyal gücü |
| **Regime** | %20 | Piyasa rejimi uyumu |
| **Volume** | %15 | OBV/CMF/CVD anlaşması |
| **Trend** | %15 | ADX/DI/MACD/SMA hizalanması |
| **Risk** | %15 | ATR, divergence desteği |

### Sentiment Bonusu (±12 puan):
- **Funding rate**: Kontrarian (yüksek pozitif FR → LONG'u cezalandır)
- **OI değişim**: Yönsel onay
- **OrderBook imbalance**: Alış/satış baskısı
- **Likidite kalitesi**: Yüksek likidite bonusu

**Minimum ticaret puanı: 70** (mutlak değer)

### 12 SERT FİLTRE:

1. **ATR Güvenliği** → Volatilite çok yüksekse reddet
2. **Rejim Volatilite** → VOLATILE rejimde reddet (opsiyonel)
3. **Funding Rate** → LONG: ≤%0.1, SHORT: ≥-%0.1
4. **OrderBook Analizi** → İnce defter veya yönü bloke eden büyük duvar varsa reddet
5. **ADX Rejim Kapısı** → ADX<18: İŞLEM YOK, 18-25: GRİ BÖLGE, ≥25: TREND
6. **Bölgeye Özel Confluence** → RANGING: min 4.0, GRİ: min 6.0, TRENDING: min 6.5
7. **RSI Filtresi** → LONG: RSI≤62, SHORT: RSI≥38
8. **ADX Minimum** → Bölgenin gerektirdiği minimum ADX
9. **Trend Yön Eşleşmesi** → ADX>25 ise trend yönüyle eşleşmeli
10. **Hacim Onayı** → OBV slope veya CMF yön kontrolü
11. **MACD Filtresi** → LONG: histogram>0, SHORT: histogram<0
12. **Gri Bölge Onay Sistemi** → 4 bileşenli özel skor (sadece 18-25 ADX bölgesinde)

---

## 4. CONFLUENCE (Birleşim) ANALİZİ

İki felsefeyi birleştirir:

### TREND Grubu (max ±8 puan):
- **MACD** (±2.5) → histogram + crossover
- **ADX + DI** (±2.0) → trend gücü + yön
- **Fiyat vs SMA200** (±1.5) → uzun vadeli trend
- **EMA50 Cross** (±1.0) → hızlı/yavaş EMA
- **Support/Resistance** (±1.0) → destek/direnç yakınlığı

### REVERSİON Grubu (max ±4.5 puan):
- **RSI** (±2.5) → aşırı alım/satım
- **Bollinger Bands %B** (±2.0) → bant dışı hareket

### VOLUME Onayı (aktif gruba eklenir):
- **OBV Slope** (±1.0), **CMF** (±1.0), **CVD** (±1.5), **VWAP** (±0.5)

### Çatışma Tespiti:
- TREND ve REVERSION aynı yönde güçlü → **BİRLEŞTİR**
- Zıt yönde güçlü → **ÇATIŞMA** (skor=0, işlem yapma)
- Sadece biri güçlü → o grubu kullan + volume

---

## 5. PİYASA REJİMİ TESPİTİ

| ADX | BB Width | Sonuç | Güven |
|-----|----------|-------|-------|
| >25 | herhangi | **TRENDING** | adx/50 |
| 18-25 | <2.0 | **BREAKOUT** | 0.6 |
| 18-25 | herhangi | **TRENDING** | adx/40 |
| ≤18 | >5.0 | **VOLATILE** | min(bbw/8, 1.0) |
| ≤18 | <1.5 | **BREAKOUT** | 0.7 |
| ≤18 | 1.5-5.0 | **RANGING** | 1.0-(adx/20) |

Her rejimde indikatör ağırlıkları değişir:
- **TRENDING**: MACD ve ADX 1.5x güçlendirilir
- **RANGING**: RSI ve BB 1.5x güçlendirilir
- **VOLATILE**: ATR ve BB öne çıkar
- **BREAKOUT**: BB Width, Donchian, Volume öne çıkar

---

## 6. İNDİKATÖR MOTORU (15 İndikatör)

**Temel:** RSI(9), SMA(9,21), EMA(9,50), MACD(8,17,9)

**Trend:** ADX(14) + DI+/DI-, Support/Resistance

**Volatilite:** Bollinger Bands(20,2σ), Donchian(20), ATR(14)

**Hacim:** OBV + slope, CVD, VWAP, CMF(20)

---

## 7. EMİR YERLEŞTİRME ve SUNUCUYA GÖNDERME

### Alım Akışı:

1. **Taze fiyat** API'den çekilir
2. **BTC Korelasyon kontrolü** (portföy beta max 2.0)
3. **Kaldıraç belirleme**: min(config, API max) → varsayılan 10x
4. **Marj hesaplama**:
   - `percentage` modu: bakiye / portfolio_divider (12)
   - `fixed` modu: sabit USDT miktarı
   - Min 1 USDT, max 50 USDT per emir
5. **Miktar ve Notional kontrol**: notional ≥ min_notional (genelde 5 USDT)
6. **Stop Loss hesaplama (FEE-AWARE)**:
   - fee_roi = %0.1 round-trip × kaldıraç
   - raw_sl = (1/kaldıraç) × liq_factor × sl_liq_percent
   - net_sl = raw_sl - fee_roi - slippage_roi
7. **Emir gönderme**:
   - Margin tipi → ISOLATED
   - Kaldıraç ayarla
   - MARKET veya LIMIT emir ver
8. **Sunucu tarafı SL/TP**:
   - STOP_MARKET emri (SL)
   - TAKE_PROFIT_MARKET emri (güvenlik ağı)

### Limit Emir Girişi (opsiyonel):
- Piyasanın 1×ATR altına limit emir koy (maker fee tasarrufu)
- 5 dakika timeout → dolmazsa market fallback (sinyal hâlâ güçlüyse)
- Dolunca: sinyal yeniden kontrol edilir

---

## 8. POZİSYON İZLEME ve 7 ÇIKIŞ SİNYALİ

Her **1 saniyede** bir ayrı thread tüm açık pozisyonları kontrol eder:

### Öncelik Sırasıyla 7 Çıkış Sinyali:

| # | Sinyal | Koşul | Aktif Mi? |
|---|--------|-------|-----------|
| 0 | **ACİL ANTI-LİKİDASYON** | Likidasyon mesafesinin %80'ine ulaşıldı | HER ZAMAN |
| 1 | **SERT STOP LOSS** | Hesaplanan SL fiyatına ulaşıldı | sl_enabled |
| 2 | **SİNYAL ÇIKIŞI** | Confluence ≤-4.0 (LONG ise), kârda, min 180sn tutulmuş | signal_exit_enabled |
| 3 | **TAKE PROFIT** | TP fiyatına ulaşıldı | tp_enabled |
| 3.5 | **KISMI TP** | 2×ATR kârda → pozisyonun %50'sini kapat | partial_tp_enabled |
| 4 | **TRAILING STOP** | N×ATR kâr sonrası M×ATR geri çekilme | trailing_enabled |
| 5 | **DİVERGENCE** | RSI/OBV divergence tespit edildi (kârdayken) | divergence_exit_enabled |
| 6 | **REJİM BOZULMASI** | VOLATILE rejim + güven>0.6 → trailing sıkılaştır | kârdayken |
| 7 | **ZAMAN LİMİTİ** | 480 dakika (8 saat) max tutma süresi | time_limit_enabled |

### Trailing Stop Detayı:
- **Aktivasyon**: 3×ATR kâr hareketi (varsayılan)
- **Mesafe**: 0.5×ATR geri çekilme (varsayılan)
- **Hybrid Renewal**: Trailing tetiklendi ama sinyal hâlâ güçlü → trailing sıfırla, pozisyonu tutmaya devam et
  - virtual_entry_price = current_price olarak güncellenir
  - Yeni SL hesaplanır
  - Bir sonraki 4×ATR hareketi beklenir

### Battle Mode (savaş modu):
- SL yok, zaman limiti yok
- Fee breakeven altında: SADECE acil kapatma
- Fee üstü: sinyal çıkışı (conf ≤-6.0, daha sıkı)
- %50 ROI üstü: trailing ile çıkış
- Trailing mesafesi 2× normal

---

## 9. CONFIG.JSON AYARLARI

### TRUE/FALSE Bayrakları:

| Ayar | Varsayılan | Açıklama |
|------|-----------|----------|
| sl_enabled | true | Sert stop loss aktif |
| emergency_enabled | true | Anti-likidasyon acil kapatma |
| trailing_enabled | true | ATR/ROI trailing stop |
| tp_enabled | false | Sabit take profit (trailing halleder) |
| partial_tp_enabled | true | 2×ATR'da %50 kâr al |
| signal_exit_enabled | true | Güçlü ters sinyal çıkışı |
| signal_only_in_profit | false | Sinyal çıkışı sadece kârdayken mi? |
| divergence_exit_enabled | true | Divergence çıkışı |
| time_limit_enabled | true | 8 saat max tutma |
| direction_balance_enabled | true | Long/Short oranı kontrolü |
| btc_correlation_enabled | true | BTC beta kontrolü |
| limit_exit_enabled | true | Kapanışta limit emir |
| battle_mode | false | Agresif tutma modu |
| close_only | false | Yeni işlem açma, sadece kapat |
| focus_mode | false | Tek coin izle |
| adx_regime_enabled | false | ADX 4-bölge sistemi |
| leverage.enabled | true | Kaldıraç aktif |

### Temel Sayısal Ayarlar:

| Ayar | Değer | Açıklama |
|------|-------|----------|
| min_buy_score | 70 | Min alım puanı |
| min_confluence | 6.5 | Min confluence skoru (trending) |
| min_adx | 25 | Min ADX trend gücü |
| max_positions | 12 | Max eşzamanlı pozisyon |
| portfolio_divider | 12 | Portföyü 12'ye böl (per pozisyon marj) |
| liq_factor | 70 | Likidasyon mesafe faktörü (%) |
| sl_liq_percent | 50 | SL = likidasyon mesafesinin %50'si |
| emergency_liq_percent | 80 | Acil kapanış = likidasyon %80 |
| trailing_atr_activate_mult | 3.0 | Trailing aktivasyon = 3×ATR kâr |
| trailing_atr_distance_mult | 0.5 | Trailing mesafe = 0.5×ATR |
| tp_atr_mult | 3.0 | TP = 3×ATR |
| signal_exit_threshold | 4.0 | Sinyal çıkışı eşiği |
| signal_min_hold_seconds | 180 | Min tutma süresi (3dk) |
| time_limit_minutes | 480 | Max tutma (8 saat) |
| cooldown_seconds | 60 | Taramalar arası bekleme |
| loss_cooldown_seconds | 3600 | Kayıp sonrası coin yasağı (1 saat) |
| coin_daily_loss_limit | 2 | Günlük coin başına max kayıp |
| max_trades_per_hour | 12 | Saatte max işlem |
| max_single_order_usdt | 50 | Emir başına max 50 USDT |

---

## 10. ÖRNEK TAM YAŞAM DÖNGÜSÜ

### 1. TARAMA (her 30sn)
50 coin taranır → BTCUSDT seçildi: skor=+85, yön=LONG

### 2. ALIM
- Fiyat: 65,432 USDT
- Kaldıraç: 10x, Marj: 10 USDT
- Notional: 100 USDT, Miktar: 0.00153 BTC
- SL: 64,892 (fee-aware hesap)
- MARKET emir → FILLED
- Sunucu SL: STOP_MARKET @ 64,892
- Pozisyon açıldı

### 3. İZLEME (her 1sn)
- 7 çıkış sinyali kontrol edilir
- Fiyat 65,800'e çıktı → HOLD
- Henüz 3×ATR kâra ulaşmadı

### 4. TRAİLİNG AKTİVASYON (fiyat 65,950)
- 3×ATR kâr eşiği aşıldı
- Trailing stop: 65,950 - 0.5×ATR = 65,850

### 5. GERİ ÇEKİLME (fiyat 65,820)
- Trailing tetiklendi (65,820 < 65,850)
- AMA confluence hâlâ güçlü (+5.8)
- HYBRID RENEWAL → trailing sıfırlandı
- virtual_entry = 65,820, yeni SL hesaplandı

### 6. GÜÇLÜ DÖNÜŞ (fiyat 65,100'e düştü)
- Confluence: -5.2 (SHORT sinyali)
- Kayıpta → sinyal çıkışı bloke
- Sunucu SL @ 65,566 tetiklendi
- Pozisyon Binance tarafından kapatıldı
- PnL: -4 USDT kayıp
- BTCUSDT 1 saat re-entry yasağı
- Günlük kayıp sayacı: 1/2

---

## 11. TASARIM PRENSİPLERİ

1. **Fee-Aware SL**: Kayıplar komisyon + slippage dahil hesaplanır
2. **Çift Katmanlı Likidasyon Koruması**: Sunucu SL (%50) + Yazılım acil kapatma (%80)
3. **Ortogonal İndikatörler**: Birbirini tekrarlayan indikatörler çıkarılmış (Stochastic, CCI, Ichimoku yok)
4. **Çatışma Tespiti**: Trend ve reversion zıt yönde → işlem yapma
5. **Rejime Uyum**: RANGING'de MACD/volume filtreleri devre dışı, farklı eşikler
6. **Kısmi Kâr Alma**: 2×ATR'da %50 kapat, kalanı büyük hareket için tut
7. **Hybrid Trailing Renewal**: Sinyal güçlüyse trailing sıfırla, erken çıkma
8. **ATR Tabanlı Risk**: Tüm mesafeler volatiliteye orantılı, sabit yüzde değil
9. **Kontrarian Sentiment**: Yüksek funding rate ters sinyal verir
10. **Anti-Churning**: Saatte max 12 işlem, kayıp cooldown, günlük coin yasağı
