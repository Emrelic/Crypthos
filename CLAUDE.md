# Crypthos - Claude Code Instructions

## Kisayollar

### *kza — Kar/Zarar Analizi
Kullanici `*kza` yazdiginda asagidaki tam analizi calistir:

1. **Portfoy Durumu**: Son rapordan bu yana wallet balance, margin balance degisimi. Canli Binance API'den `get_account()` + `get_positions()` cek. Onceki ve simdiki portfoy karsilastirmasi yap. Tum pozisyonlar kapatilirsa portfoy kac dolar?

2. **Stop Loss Analizi**: Tetiklenen SL sayisi ve toplam zarari. DB'de `exit_reason LIKE '%SL%' OR '%STOP%'` filtrele.

3. **Likidasyon Analizi**: Tetiklenen likidasyon sayisi. DB'de `exit_reason LIKE '%LIQ%'` filtrele.

4. **Fee ve Funding Rate Odemeleri**: Toplam komisyon (`fee_usdt`) ve funding (`funding_fee_usdt`) miktarlari.

5. **Reverse Analizi**: Karli reverse'ler (pnl>0, reason LIKE 'REVERSE%') ve zararli reverse'ler ayri ayri listele. Net reverse kar/zarar.

6. **Acik Pozisyonlar**: Su anda kardaki ve zarardaki acik pozisyonlari Binance API'den canli listele. Unrealized PnL per pozisyon.

7. **Zararli Kapanan Pozisyonlarin Temel Sebebi**: Exit reason bazinda gruplama. Emergency, reverse, SL, liq ayri ayri. Ortalama zarar ve hold suresi.

8. **Kar Kacirma Analizi**: Her trade icin `max_potential` (highest/lowest vs entry) ile `realized` karsilastirmasi. Kacirilan kar = max_potential - max(realized, 0). Yuzdesel kacirma orani.

9. **Yon Tespiti Analizi**: Her trade'de fiyat gercekten tahmin edilen yone gitti mi? Side=Buy ise high>entry mi (yon dogru), Side=Sell ise low<entry mi? Direction OK/MIXED/WRONG siniflandirmasi.

**Cutoff**: Her zaman onceki raporun timestamp'ini kullan (konusmada son *kza'nin zamani). Yoksa son 24 saati al.

**Veri kaynaklari**:
- DB: `data/crypthos.db` -> `trades` tablosu (kapanan islemler)
- Canli: `.env`'den API key'leri yukle, `BinanceRestClient(requests.Session(), key, secret)` ile baglanti kur
- API: `rest.get_account()` (wallet/margin balance), `rest.get_positions()` (acik pozisyonlar, mark price, unrealized)

10. **OZET (en sonda, zorunlu)**: Tum analizden sonra 3-5 cumlelik kisa bir ozet yaz. Su sorulari cevapla:
    - Son analizden bu yana portfoy karda mi zararda mi? Kac USDT degisim?
    - Tum pozisyonlar kapatilirsa portfoy kac dolar?
    - En buyuk risk/sorun ne?
    - SL/Emergency/Reverse performansi tek cumleyle.
    Bu ozet kullanicinin hizlica "durum iyi mi kotu mu" sorusunu cevaplasin.

**Cutoff**: Her zaman onceki raporun timestamp'ini kullan (konusmada son *kza'nin zamani). Yoksa son 24 saati al.

**Cikti formati**: Markdown tablolar + her bolum sonrasi kisa yorum + EN SONDA mutlaka genel ozet. Turkce.
