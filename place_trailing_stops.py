"""
Açık pozisyonlar için Binance'e TRAILING_STOP_MARKET emirleri gönderir.
Tek seferlik kullanım scripti.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from market.binance_rest import BinanceRestClient
from indicators.indicator_engine import IndicatorEngine
from core.config_manager import ConfigManager


def main():
    config = ConfigManager("config.json")
    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")

    if not api_key or not api_secret:
        print("HATA: .env dosyasinda BINANCE_API_KEY ve BINANCE_API_SECRET bulunamadi")
        return

    rest = BinanceRestClient(api_key=api_key, api_secret=api_secret)

    # 1. Açık pozisyonları al
    print("\n=== ACIK POZISYONLAR ===")
    positions = rest.get_positions()
    open_positions = [p for p in positions if float(p.get("positionAmt", 0)) != 0]

    if not open_positions:
        print("Acik pozisyon yok!")
        return

    strat = config.get("strategy", {})
    activate_mult = strat.get("trailing_atr_activate_mult", 4.0)
    distance_mult = strat.get("trailing_atr_distance_mult", 1.0)
    interval = config.get("strategy.kline_interval",
                          config.get("indicators.kline_interval", "5m"))
    kline_limit = config.get("strategy.kline_limit", 200)

    print(f"Strateji: trailing_mode=atr, activate={activate_mult}xATR, "
          f"distance={distance_mult}xATR, tf={interval}")
    print(f"Pozisyon sayisi: {len(open_positions)}")
    print()

    results = []

    for p in open_positions:
        symbol = p.get("symbol", "")
        amt = float(p.get("positionAmt", 0))
        entry_price = float(p.get("entryPrice", 0))
        leverage = int(p.get("leverage", 1))
        margin = float(p.get("isolatedWallet", 0))
        unrealized_pnl = float(p.get("unRealizedProfit", 0))
        is_long = amt > 0
        size = abs(amt)

        # Güncel fiyat
        try:
            ticker = rest.get_ticker_price(symbol)
            current_price = float(ticker.get("price", 0))
        except Exception:
            current_price = entry_price

        # ROI hesapla
        if margin > 0:
            roi = unrealized_pnl / margin * 100
        else:
            roi = 0

        # ATR hesapla
        atr = 0.0
        atr_pct = 0.0
        try:
            klines = rest.get_klines(symbol, interval, limit=kline_limit)
            if klines is not None and len(klines) > 50:
                eng = IndicatorEngine(config)
                indicators = eng.compute_all(klines)
                atr = indicators.get("ATR", 0)
                if entry_price > 0 and atr > 0:
                    atr_pct = atr / entry_price * 100
        except Exception as e:
            print(f"  UYARI: {symbol} ATR hesaplanamadi: {e}")

        # Trailing hesaplamalari
        trailing_activate_pct = atr_pct * activate_mult
        trailing_activate_roi = trailing_activate_pct * leverage
        trailing_distance_pct = atr_pct * distance_mult

        # Mevcut ATR karı
        if atr > 0:
            profit_atr = ((current_price - entry_price) / atr) if is_long else \
                         ((entry_price - current_price) / atr)
        else:
            profit_atr = 0

        # Callback rate (Binance limiti: 0.1% - 5.0%)
        if atr > 0 and current_price > 0:
            callback_pct = (atr * distance_mult) / current_price * 100
        else:
            callback_pct = 1.0
        callback_pct = max(0.1, min(5.0, round(callback_pct, 1)))

        side_str = "LONG" if is_long else "SHORT"
        close_side = "SELL" if is_long else "BUY"

        print(f"{'='*80}")
        print(f"  {symbol} | {side_str} | {leverage}x | TF={interval}")
        print(f"  Giris: {entry_price:.6f} | Guncel: {current_price:.6f} | ROI: {roi:+.1f}%")
        print(f"  Margin: {margin:.2f}$ | PnL: {unrealized_pnl:+.4f}$")
        print(f"  ATR: {atr:.8f} ({atr_pct:.3f}%)")
        print(f"  7xATR hareket: {trailing_activate_pct:.2f}% | Gereken ROI: {trailing_activate_roi:.0f}%")
        print(f"  Mevcut kar: {profit_atr:.1f}xATR / {activate_mult:.0f}xATR hedef")
        print(f"  Geri gelme (callback): {callback_pct:.1f}% ({trailing_distance_pct:.3f}%)")
        print()

        results.append({
            "symbol": symbol,
            "close_side": close_side,
            "current_price": current_price,
            "callback_pct": callback_pct,
            "atr": atr,
            "atr_pct": atr_pct,
            "profit_atr": profit_atr,
            "roi": roi,
            "trailing_activate_roi": trailing_activate_roi,
            "entry_price": entry_price,
            "leverage": leverage,
            "is_long": is_long,
            "size": size,
        })

    # Onay iste
    print(f"\n{'='*80}")
    print(f"TOPLAM {len(results)} pozisyon icin TRAILING_STOP_MARKET emri gonderilecek.")
    print(f"Bu emirler Binance server tarafinda calisacak (program kapansa bile).")
    print(f"MEVCUT SL emirleri KORUNACAK (cancel edilmeyecek).")
    print()
    # Komut satiri argumaninda --yes varsa otomatik onayla
    if "--yes" in sys.argv:
        confirm = "evet"
    else:
        confirm = input("Devam etmek istiyor musunuz? (evet/hayir): ").strip().lower()

    if confirm not in ("evet", "e", "yes", "y"):
        print("Iptal edildi.")
        return

    # Trailing stop emirlerini gönder
    print("\n=== TRAILING STOP EMIRLERI GONDERILIYOR ===\n")

    success_count = 0
    for r in results:
        symbol = r["symbol"]
        close_side = r["close_side"]
        current_price = r["current_price"]
        callback_pct = r["callback_pct"]

        # Price precision
        try:
            info = rest.get_exchange_info(symbol)
            pp = info.get("pricePrecision", 4) if info else 4
        except Exception:
            pp = 4

        activation_price = round(current_price, pp)

        # Activation price = 7×ATR ötesinde (trailing burada başlar)
        atr_val = r["atr"]
        if r["is_long"]:
            act_price = round(r["entry_price"] + (atr_val * activate_mult), pp)
        else:
            act_price = round(r["entry_price"] - (atr_val * activate_mult), pp)

        try:
            result = rest.place_order(
                symbol=symbol,
                side=close_side,
                order_type="TRAILING_STOP_MARKET",
                quantity=r["size"],
                stop_price=act_price,
                callback_rate=callback_pct,
            )
            print(f"  BASARILI: {symbol} trailing stop "
                  f"aktivasyon={act_price} ({activate_mult}xATR) "
                  f"callback={callback_pct:.1f}% ({distance_mult}xATR)")
            success_count += 1
        except Exception as e:
            print(f"  BASARISIZ: {symbol} - {e}")

    print(f"\n{'='*80}")
    print(f"Sonuc: {success_count}/{len(results)} trailing stop emri basariyla gonderildi.")
    print(f"Bu emirler Binance'te aktif. Program kapansa bile koruma devam eder.")


if __name__ == "__main__":
    main()
