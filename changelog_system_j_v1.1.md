# System J v1.1 — Darboğaz Gevşetme Günlüğü
# Tarih: 2026-04-02
# Durum: UYGULAMADA
# Amaç: Gereksiz sıkılıkları kaldırarak pozisyon açabilir hale getirmek

---

## Sorun Tespiti

System J v1.0 saatlerdir **0 eligible / 70 coin** döndürüyordu.
Son 10 scan'de 47 coin sonuç üretip hiçbiri uygun bulunmadı.
Sistem fiilen çalışmıyordu.

---

## Yapılan Değişiklikler (3+1)

### Değişiklik 1: Gray Zone → Hurst Dual-Vote
| | Önceki | Yeni |
|---|--------|------|
| ER 0.08-0.25 | GRAY → coin atılır (gray_zone_skip=true) | Hurst hakem → TRENDING veya RANGING |
| Etki | Coinlerin ~%50-60'ı eleniyordu | Hiçbir coin atılmaz |

**Mantık:** ER tek başına karar veremediğinde Hurst Exponent (R/S analizi)
zaman serisinin hafızasını ölçerek hakem olur.
- H > 0.55 → TRENDING (persistent seri)
- H < 0.45 → RANGING (mean-reverting seri)
- H 0.45-0.55 → ER midpoint tiebreaker
- Confidence doğal olarak düşük (max 0.7) → skor cezalandırır

**Geri alınacaksa:** config.json → system_j.regime.gray_zone_skip = true

---

### Değişiklik 2: min_p_win 0.40 → 0.0 (kaldırıldı)
| | Önceki | Yeni |
|---|--------|------|
| min_p_win | 0.40 (%40) | 0.0 (devre dışı) |
| Koruma | EV>0 + R:R≥2.5 + ayrıca P(win)≥%40 | EV>0 + R:R≥2.0 (P(win) filtresi gereksiz) |

**Neden gereksiz:** R:R ≥ 2.0 olan bir komboda EV>0 için gereken minimum P(win):
```
P(win) breakeven = 1 / (1 + R:R) = 1 / (1 + 2.0) = %33.3
```
EV > 0 filtresi bunu zaten garanti ediyor. Ayrıca P(win) ≥ %40 istemek
matematiksel olarak redundant — EV'si pozitif olan karlı işlemleri engelliyor.

**Geri alınacaksa:** config.json → system_j.ev.min_p_win = 0.40

**DIKKAT: Eğer zarar artarsa ilk bakılacak yer burası.**
P(win) düşük ama R:R yüksek işlemler çok sık kaybeder (küçük kayıplar)
ve nadiren kazanır (büyük kazançlar). Psikolojik olarak zor olabilir.
Matematiksel olarak karlı ama çok sayıda küçük kayıp rahatsız ediyorsa
min_p_win = 0.30 veya 0.35 yapılabilir.

---

### Değişiklik 3: min_rr 2.5 → 2.0
| | Önceki | Yeni |
|---|--------|------|
| min_rr | 2.5 | 2.0 |
| 75x geçen kombo | 2/30 | 4/30 |
| 50x geçen kombo | 5/30 | 9/30 |
| 25x geçen kombo | 9/30 | 14/30 |

**Neden:** Fee (0.12%) küçük G değerlerinde TP'yi yiyor, SL'yi şişiriyor.
Bu asimetri yüzünden R:R 2.5 yüksek kaldıraçta neredeyse imkansız:
```
net_TP = tp_mult × G - 0.12%   ← fee TP'den düşer
net_SL = sl_mult × G + 0.12%   ← fee SL'ye eklenir
G küçüldükçe fee oranı büyür → R:R yapay olarak düşer
```
R:R 2.0 hâlâ güçlü asimetrik koruma sağlar (breakeven P(win) = %33.3).

**Geri alınacaksa:** config.json → system_j.ev.min_rr = 2.5

**DIKKAT: Eğer ortalama kayıp/kazanç oranı bozulursa buraya bak.**
R:R düştüğünde her kazanç daha az telafi eder. Eğer win rate de
düşükse (<%35) zarar birikimi hızlanabilir. İzlenecek metrik:
  avg_win_pct / avg_loss_pct >= 1.8 olmalı (güvenli bölge)

---

### Değişiklik 4: min_volume_ratio 1.0 → 0.5
| | Önceki | Yeni |
|---|--------|------|
| min_volume_ratio | 1.0 (medyanın üstü) | 0.5 (medyanın yarısı) |
| Elenen coin | ~%50 (tanım gereği) | ~%25 |

**Neden:** Medyana göre oran olduğu için 1.0 eşiği coinlerin tam yarısını
her zaman eler. Top 50 zaten 24h hacim sıralamasıyla seçilmiş — hepsi likit.
25. sıradaki coin bile günlük milyonlarca dolar hacme sahip.

**Geri alınacaksa:** config.json → system_j.filters.min_volume_ratio = 1.0

**DIKKAT: Eğer slippage artarsa (beklenenden kötü fill) buraya bak.**
Düşük hacimli coinlerde spread ve slippage yüksek olabilir. İzlenecek:
  Ortalama slippage > %0.05 ise volume_ratio'yu 0.7'ye çek.

---

## İzleme Planı

Değişikliklerin etkisini değerlendirmek için izlenecek metrikler:

| Metrik | Beklenen | Alarm Eşiği | Aksiyon |
|--------|----------|-------------|---------|
| eligible/scan | 3-8 | < 1 sürekli | Filtreleri kontrol et |
| win rate | %30-40 | < %25 | min_p_win = 0.30 ekle |
| avg_win / avg_loss | ≥ 1.8 | < 1.5 | min_rr = 2.5'e geri al |
| günlük PnL | ≥ 0 | 3 gün üst üste negatif | tüm değişiklikleri geri al |
| slippage | < %0.05 | > %0.08 | volume_ratio = 0.7 |

## Eski Değerler (Geri Alma Referansı)

```json
{
  "system_j.ev.min_p_win": 0.40,
  "system_j.ev.min_rr": 2.5,
  "system_j.filters.min_volume_ratio": 1.0,
  "system_j.regime.gray_zone_skip": true
}
```

## Değişiklik Sonrası Beklenti

- 70 coinden 0 eligible → tahminen 5-10 eligible
- Daha fazla işlem ama daha düşük confidence → doğal skor filtresi devrede
- EV > 0 hâlâ zorunlu → matematiksel olarak her işlem beklenen değer pozitif
- Yön teyidi, funding filtresi, SL/liq güvenliği → hiçbiri değişmedi
