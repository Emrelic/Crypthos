"""Elliott Wave Pattern Detection Module.

Mevcut zigzag altyapisinin uzerine kurulur.
Impulse (5-dalga) ve Correction (ABC) pattern'leri tespit eder.
Fibonacci uyumu skorlanir, yon ve sonraki dalga projeksiyonu uretilir.
"""

import numpy as np
from dataclasses import dataclass
from typing import Optional

# SwingPoint ve zigzag import
from scanner.system_b_scanner import SwingPoint, detect_zigzag_swings


@dataclass
class ElliottPattern:
    """Elliott Wave pattern sonucu."""
    pattern_type: str       # "IMPULSE_5" veya "CORRECTION_ABC"
    current_wave: int       # Impulse: 1-5, Correction: 1-3 (A=1, B=2, C=3)
    confidence: float       # 0.0 - 1.0 arasi genel guven skoru
    fib_score: float        # 0.0 - 1.0 arasi Fibonacci uyum skoru
    direction: str          # "BULL" veya "BEAR" (pattern yonu)
    next_move_dir: str      # "LONG" veya "SHORT" (sonraki beklenen hareket)
    next_move_pct: float    # Fibonacci projeksiyonuna gore beklenen hareket %
    waves: list             # Dalga fiyatlari listesi
    start_index: int = 0    # Pattern baslangic indeksi
    end_index: int = 0      # Pattern bitis indeksi


def _fib_proximity(actual_ratio: float, ideal_ratio: float, tolerance: float = 0.15) -> float:
    """Gercek oran ile ideal Fibonacci orani arasindaki yakinlik skoru (0-1)."""
    diff = abs(actual_ratio - ideal_ratio)
    if diff <= tolerance:
        return 1.0 - (diff / tolerance)
    return max(0.0, 1.0 - diff)


def fit_impulse_5(swings: list[SwingPoint]) -> Optional[ElliottPattern]:
    """Son 5 swing'i Elliott impulse pattern'e fit et.

    Impulse kurallari:
    - Kural 1: Wave 2 retrace < %100 Wave 1 (wave2 end, wave1 basinin otesine gecemez)
    - Kural 2: Wave 3 en kisa dalga olamaz (1, 3, 5 arasinda)
    - Kural 3: Wave 4, Wave 1 fiyat bolgesine giremez (no overlap)

    Fibonacci ideal oranlari:
    - Wave 2: %61.8 retrace of Wave 1
    - Wave 3: %161.8 extension of Wave 1
    - Wave 4: %38.2 retrace of Wave 3
    """
    if len(swings) < 6:
        return None

    # Son 6 swing noktasi = 5 dalga arasi
    pts = swings[-6:]

    # Yonu belirle: ilk hareket yukari mi asagi mi
    if pts[0].price < pts[1].price:
        direction = "BULL"  # yukari impulse
    else:
        direction = "BEAR"  # asagi impulse

    # Dalga buyuklukleri
    w1 = abs(pts[1].price - pts[0].price)
    w2 = abs(pts[2].price - pts[1].price)
    w3 = abs(pts[3].price - pts[2].price)
    w4 = abs(pts[4].price - pts[3].price)
    w5 = abs(pts[5].price - pts[4].price)

    if w1 == 0 or w3 == 0:
        return None

    # Kural 1: Wave 2 < %100 Wave 1
    if w2 >= w1:
        return None

    # Kural 2: Wave 3 en kisa dalga olamaz (w1, w3, w5 arasinda)
    impulse_waves = [w1, w3, w5]
    if w3 == min(impulse_waves):
        return None

    # Kural 3: Wave 4, Wave 1 fiyat bolgesine giremez
    if direction == "BULL":
        # Bull: Wave 1 bolgesi = pts[0].price - pts[1].price
        # Wave 4 sonu (pts[4].price) Wave 1 tepesinin (pts[1].price) altina inmemeli
        if pts[4].price <= pts[1].price:
            return None
        # Wave 5 sonu Wave 3 tepesinden yukari olmali (trend devam)
        if pts[5].price <= pts[3].price:
            return None
    else:
        # Bear: Wave 4 sonu Wave 1 dibinin (pts[1].price) ustune cikmamali
        if pts[4].price >= pts[1].price:
            return None
        # Wave 5 sonu Wave 3 dibinden asagi olmali
        if pts[5].price >= pts[3].price:
            return None

    # Fibonacci skor
    w2_retrace = w2 / w1  # ideal: 0.618
    w3_extension = w3 / w1  # ideal: 1.618
    w4_retrace = w4 / w3  # ideal: 0.382

    fib_w2 = _fib_proximity(w2_retrace, 0.618)
    fib_w3 = _fib_proximity(w3_extension, 1.618, tolerance=0.3)
    fib_w4 = _fib_proximity(w4_retrace, 0.382)

    fib_score = (fib_w2 * 0.3 + fib_w3 * 0.4 + fib_w4 * 0.3)

    # Wave 3'un buyuklugu onemli — w3 > w1 ise bonus
    size_bonus = min(0.15, (w3 / w1 - 1.0) * 0.1) if w3 > w1 else 0

    confidence = min(1.0, fib_score * 0.7 + size_bonus + 0.15)

    # Sonraki hareket: impulse bittiyse correction beklenir (ters yon)
    if direction == "BULL":
        next_move_dir = "SHORT"  # correction asagi
        # ABC correction Wave A tahmini: W5'in %38.2 - %61.8 retrace
        next_move_pct = (w5 * 0.382 / pts[5].price) * 100
    else:
        next_move_dir = "LONG"
        next_move_pct = (w5 * 0.382 / pts[5].price) * 100

    return ElliottPattern(
        pattern_type="IMPULSE_5",
        current_wave=5,
        confidence=confidence,
        fib_score=fib_score,
        direction=direction,
        next_move_dir=next_move_dir,
        next_move_pct=next_move_pct,
        waves=[pts[i].price for i in range(6)],
        start_index=pts[0].index,
        end_index=pts[5].index,
    )


def fit_correction_abc(swings: list[SwingPoint]) -> Optional[ElliottPattern]:
    """Son 3 swing'i ABC correction pattern'e fit et.

    ABC kurallari:
    - Wave B < %100 Wave A (B, A'nin basini gecemez)
    - Wave C = %100-%161.8 Wave A

    Fibonacci ideal:
    - Wave B: %50-%61.8 retrace of Wave A
    - Wave C: %100-%161.8 extension of Wave A
    """
    if len(swings) < 4:
        return None

    pts = swings[-4:]

    # Yonu belirle: correction icinde ilk hareket
    if pts[0].price > pts[1].price:
        direction = "BEAR"  # asagi correction (onceki trend BULL idi)
    else:
        direction = "BULL"  # yukari correction (onceki trend BEAR idi)

    wa = abs(pts[1].price - pts[0].price)
    wb = abs(pts[2].price - pts[1].price)
    wc = abs(pts[3].price - pts[2].price)

    if wa == 0:
        return None

    # Wave B < %100 Wave A
    if wb >= wa:
        return None

    # Wave C ~= %100-%161.8 Wave A
    wc_ratio = wc / wa
    if wc_ratio < 0.6 or wc_ratio > 2.5:
        return None

    # Fibonacci skor
    wb_retrace = wb / wa  # ideal: 0.50 - 0.618
    fib_wb = max(_fib_proximity(wb_retrace, 0.50), _fib_proximity(wb_retrace, 0.618))
    fib_wc = max(_fib_proximity(wc_ratio, 1.0, tolerance=0.2),
                 _fib_proximity(wc_ratio, 1.618, tolerance=0.3))

    fib_score = fib_wb * 0.4 + fib_wc * 0.6

    confidence = min(1.0, fib_score * 0.65 + 0.15)

    # Sonraki hareket: correction bittiyse yeni impulse beklenir
    if direction == "BEAR":
        next_move_dir = "LONG"   # yeni yukari impulse
        next_move_pct = (wa * 1.618 / pts[3].price) * 100
    else:
        next_move_dir = "SHORT"  # yeni asagi impulse
        next_move_pct = (wa * 1.618 / pts[3].price) * 100

    return ElliottPattern(
        pattern_type="CORRECTION_ABC",
        current_wave=3,
        confidence=confidence,
        fib_score=fib_score,
        direction=direction,
        next_move_dir=next_move_dir,
        next_move_pct=next_move_pct,
        waves=[pts[i].price for i in range(4)],
        start_index=pts[0].index,
        end_index=pts[3].index,
    )


def detect_elliott(swings: list[SwingPoint], min_confidence: float = 0.3) -> Optional[ElliottPattern]:
    """Ana fonksiyon: impulse + correction dene, en iyi skoru don.

    Args:
        swings: Zigzag swing noktalari (detect_zigzag_swings ciktisi)
        min_confidence: Minimum guven esigi

    Returns:
        En iyi ElliottPattern veya None
    """
    if len(swings) < 4:
        return None

    candidates = []

    # Impulse 5 dene (son 6 swing noktasi)
    if len(swings) >= 6:
        imp = fit_impulse_5(swings)
        if imp and imp.confidence >= min_confidence:
            candidates.append(imp)

    # Correction ABC dene (son 4 swing noktasi)
    abc = fit_correction_abc(swings)
    if abc and abc.confidence >= min_confidence:
        candidates.append(abc)

    if not candidates:
        return None

    # En yuksek confidence'li pattern'i sec
    return max(candidates, key=lambda p: p.confidence)


def project_next_wave(pattern: ElliottPattern, current_price: float) -> dict:
    """Fibonacci hedef hesapla.

    Returns:
        dict: target_price, stop_price, reward_risk, direction
    """
    if not pattern or not pattern.waves:
        return {}

    direction = pattern.next_move_dir

    if pattern.pattern_type == "IMPULSE_5":
        # Impulse bitti, correction bekleniyor
        last_price = pattern.waves[-1]
        w5_size = abs(pattern.waves[-1] - pattern.waves[-2])

        if direction == "LONG":
            # Bear impulse bitti, yukari correction
            target = current_price + w5_size * 0.618
            stop = current_price - w5_size * 0.236
        else:
            # Bull impulse bitti, asagi correction
            target = current_price - w5_size * 0.618
            stop = current_price + w5_size * 0.236

    elif pattern.pattern_type == "CORRECTION_ABC":
        # Correction bitti, yeni impulse bekleniyor
        wa_size = abs(pattern.waves[1] - pattern.waves[0])

        if direction == "LONG":
            target = current_price + wa_size * 1.618
            stop = current_price - wa_size * 0.382
        else:
            target = current_price - wa_size * 1.618
            stop = current_price + wa_size * 0.382
    else:
        return {}

    reward = abs(target - current_price)
    risk = abs(stop - current_price)
    rr = reward / risk if risk > 0 else 0

    return {
        "direction": direction,
        "target_price": target,
        "stop_price": stop,
        "reward_risk": rr,
    }


def detect_elliott_from_ohlc(highs: np.ndarray, lows: np.ndarray,
                              n: int = 10, min_confidence: float = 0.3) -> Optional[ElliottPattern]:
    """Convenience: OHLC verisinden direkt Elliott pattern tespit et."""
    swings = detect_zigzag_swings(highs, lows, n=n)
    return detect_elliott(swings, min_confidence=min_confidence)
