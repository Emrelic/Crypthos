"""Crypthos Strategy Engine - Full Live Test with Real Binance Data
Tests: indicators, confluence, regime, divergence, risk manager, strategies, live loop
"""
import requests
import pandas as pd
import numpy as np
import time
import threading
from core.config_manager import ConfigManager
from core.event_bus import EventBus
from core.constants import EventType, OrderSide, OrderType, ConditionOperator
from indicators.indicator_engine import IndicatorEngine
from strategy.strategy_engine import StrategyEngine
from strategy.strategy import Strategy
from strategy.rule import Rule
from strategy.condition import Condition
from strategy.actions import TradeAction
from analysis.confluence import ConfluenceScorer
from analysis.market_regime import MarketRegimeDetector
from analysis.divergence import DivergenceDetector
from safety.risk_manager import RiskManager


def fetch_klines(symbol="DOGEUSDT", interval="15m", limit=200):
    url = "https://fapi.binance.com/fapi/v1/klines"
    resp = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": limit})
    raw = resp.json()
    cols = ["time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "taker_buy", "taker_buy_qav", "ignore"]
    df = pd.DataFrame(raw, columns=cols)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


def fetch_price(symbol="DOGEUSDT"):
    url = f"https://fapi.binance.com/fapi/v1/ticker/price"
    resp = requests.get(url, params={"symbol": symbol})
    return float(resp.json()["price"])


def fetch_funding(symbol="DOGEUSDT"):
    url = "https://fapi.binance.com/fapi/v1/premiumIndex"
    resp = requests.get(url, params={"symbol": symbol})
    data = resp.json()
    return {
        "mark_price": float(data.get("markPrice", 0)),
        "funding_rate": float(data.get("lastFundingRate", 0)),
    }


print("=" * 70)
print("  CRYPTHOS STRATEJI MOTORU - KAPSAMLI CANLI TEST")
print("=" * 70)

# ===== PART 1: Indicator Test =====
print("\n[1] Binance API'den DOGEUSDT 15m kline verisi cekiliyor...")
df = fetch_klines()
current_price = fetch_price()
funding = fetch_funding()
print(f"   {len(df)} mum, Son fiyat: {current_price:.6f}")
print(f"   Mark: {funding['mark_price']:.6f}, Funding: {funding['funding_rate']*100:.4f}%")

print("\n[2] 30 indikator hesaplaniyor...")
config = ConfigManager()
engine = IndicatorEngine(config)
results = engine.compute_all(df)
print(f"   {len(results)} deger hesaplandi")

# Key indicators
key_inds = {
    "RSI": results.get("RSI"),
    "StochRSI_K": results.get("StochRSI_K"),
    "MFI": results.get("MFI"),
    "CCI": results.get("CCI"),
    "MACD_hist": results.get("MACD_histogram"),
    "ADX": results.get("ADX"),
    "+DI": results.get("ADX_plus_DI"),
    "-DI": results.get("ADX_minus_DI"),
    "Supertrend": results.get("Supertrend_trend"),
    "PSAR": results.get("PSAR_trend"),
    "Ichimoku": results.get("Ichimoku_Position"),
    "BB_%B": results.get("BB_PercentB"),
    "ATR": results.get("ATR"),
    "OBV_slope": results.get("OBV_slope"),
    "CMF": results.get("CMF"),
}
for k, v in key_inds.items():
    if isinstance(v, str):
        print(f"   {k:14s}: {v}")
    elif v is not None:
        print(f"   {k:14s}: {v:.4f}")

# ===== PART 2: Regime Detection =====
print("\n[3] Piyasa rejimi...")
regime_det = MarketRegimeDetector()
regime = regime_det.detect(results)
print(f"   {regime['regime']} | Yon: {regime['trend_direction']} | Guven: {regime['confidence']:.0%}")
print(f"   Onerilen: {regime['recommended_strategies']}")

# ===== PART 3: Confluence =====
print("\n[4] Confluence skoru...")
scorer = ConfluenceScorer(threshold=4.0)
confluence = scorer.score(results, regime.get("indicator_weights", {}))
print(f"   Skor: {confluence['score']:+.1f} | Sinyal: {confluence['signal']} | Guc: {confluence['strength']:.0%}")
print(f"   {confluence['bullish_count']} yukselis / {confluence['bearish_count']} dusus")
for k, v in confluence["details"].items():
    bar = ("+" * int(abs(v) * 4)) if v > 0 else ("-" * int(abs(v) * 4)) if v < 0 else "."
    print(f"     {k:18s}: {v:+5.1f}  {bar}")

# ===== PART 4: Divergence =====
print("\n[5] Diverjans analizi...")
div_det = DivergenceDetector(lookback=30)
ind_series = {}
for name in ["RSI", "CCI", "MFI", "OBV"]:
    ind = engine.get_indicator(name)
    if ind and ind._series is not None:
        ind_series[name] = ind._series
divs = div_det.detect_all(df, ind_series)
if divs:
    for d in divs:
        print(f"   ! {d['type']:20s} ({d['indicator']}) -> {d['signal']} (guc: {d['strength']:.3f})")
else:
    print("   Aktif diverjans yok")

# ===== PART 5: Risk Manager =====
print("\n[6] Risk Manager...")
event_bus = EventBus()
rm = RiskManager(config, event_bus)
atr = results.get("ATR", 0.001)
sl_pct = rm.calculate_sl_percent(current_price, atr, "BUY")
tp_pct = rm.calculate_tp_percent(current_price, atr, "BUY")
kelly = rm.kelly_position_size(100.0, current_price)
kelly_qty = rm.kelly_position_qty(100.0, current_price)
print(f"   ATR SL: {sl_pct:.2f}% | ATR TP: {tp_pct:.2f}%")
print(f"   Kelly: {kelly:.2f} USDT ({kelly_qty:.0f} DOGE)")
print(f"   Trailing (+1%): {rm.calculate_trailing_stop(current_price, current_price*1.01, atr, 'BUY'):.6f}")

# Validate order
valid, reason = rm.validate_order(kelly_qty, current_price)
print(f"   Emir validasyon: {'GECERLI' if valid else 'REDDEDILDI'} {reason}")

# ===== PART 6: Strategy Evaluation =====
print("\n[7] 5 strateji degerlendiriliyor...")

buy_action = TradeAction(side=OrderSide.BUY_LONG, order_type=OrderType.MARKET,
                         size_usdt=5.0, tp_percent=tp_pct, sl_percent=sl_pct)
sell_action = TradeAction(side=OrderSide.SELL_SHORT, order_type=OrderType.MARKET,
                          size_usdt=5.0, tp_percent=tp_pct, sl_percent=sl_pct)

strategies = []

# 1. RSI Al-Sat
s = Strategy("RSI_AlSat", "DOGEUSDT", [
    Rule("RSI<35_Buy", [Condition("RSI", ConditionOperator.LESS_THAN, 35)],
         buy_action, cooldown_seconds=60),
    Rule("RSI>65_Sell", [Condition("RSI", ConditionOperator.GREATER_THAN, 65)],
         sell_action, cooldown_seconds=60),
])
s.enabled = True
strategies.append(s)

# 2. MACD Cross
s = Strategy("MACD_Cross", "DOGEUSDT", [
    Rule("MACD_Bull", [Condition("MACD_bullish_cross", ConditionOperator.EQUAL, True)],
         buy_action, cooldown_seconds=120),
    Rule("MACD_Bear", [Condition("MACD_bearish_cross", ConditionOperator.EQUAL, True)],
         sell_action, cooldown_seconds=120),
])
s.enabled = True
strategies.append(s)

# 3. Supertrend Follow
s = Strategy("Supertrend", "DOGEUSDT", [
    Rule("ST_UP", [Condition("Supertrend_trend", ConditionOperator.EQUAL, "UP")],
         buy_action, cooldown_seconds=300),
    Rule("ST_DOWN", [Condition("Supertrend_trend", ConditionOperator.EQUAL, "DOWN")],
         sell_action, cooldown_seconds=300),
])
s.enabled = True
strategies.append(s)

# 4. Bollinger Revert
s = Strategy("Bollinger", "DOGEUSDT", [
    Rule("BB<0.1", [Condition("BB_PercentB", ConditionOperator.LESS_THAN, 0.1)],
         buy_action, cooldown_seconds=120),
    Rule("BB>0.9", [Condition("BB_PercentB", ConditionOperator.GREATER_THAN, 0.9)],
         sell_action, cooldown_seconds=120),
])
s.enabled = True
strategies.append(s)

# 5. Multi Confluence
s = Strategy("Confluence", "DOGEUSDT", [
    Rule("Conf>=4", [Condition("confluence_score", ConditionOperator.GREATER_EQUAL, 4.0)],
         buy_action, cooldown_seconds=300),
    Rule("Conf<=-4", [Condition("confluence_score", ConditionOperator.LESS_EQUAL, -4.0)],
         sell_action, cooldown_seconds=300),
])
s.enabled = True
strategies.append(s)

market_data = {
    "price": current_price,
    "symbol": "DOGEUSDT",
    "confluence_score": confluence["score"],
    "confluence_signal": confluence["signal"],
    "regime": regime["regime"],
}

triggered = []
for strat in strategies:
    actions = strat.evaluate(results, market_data)
    if actions:
        for a in actions:
            sz = a.calculate_size(current_price)
            side_txt = "ALIS" if "Buy" in a.side.value else "SATIS"
            print(f"   >>> {strat.name:15s}: {side_txt} {sz:.0f} DOGE | TP={a.tp_percent:.2f}% SL={a.sl_percent:.2f}%")
            triggered.append(strat.name)
    else:
        print(f"   --- {strat.name:15s}: Kosul saglanmadi")

# ===== PART 7: Live Strategy Engine Loop =====
print("\n[8] Strateji motoru canli dongusu (3 tur, 5 sn aralik)...")

# Capture signals
received_signals = []


def on_signal(data):
    received_signals.append(data)
    p = data.get("params", {})
    print(f"   [SINYAL] {data.get('strategy_name')}: {p.get('side', '?').value} "
          f"{p.get('size', 0):.0f} @ {p.get('price', 0)} "
          f"TP={p.get('tp_percent', 0):.1f}% SL={p.get('sl_percent', 0):.1f}%")


def on_analysis(data):
    c = data.get("confluence", {})
    r = data.get("regime", {})
    print(f"   [ANALIZ] Rejim={r.get('regime', '?')} | "
          f"Confluence={c.get('score', 0):+.1f} -> {c.get('signal', '?')} | "
          f"Diverjans={len(data.get('divergences', []))}")


event_bus2 = EventBus()
event_bus2.subscribe(EventType.STRATEGY_SIGNAL, on_signal)
event_bus2.subscribe(EventType.ANALYSIS_UPDATE, on_analysis)

rm2 = RiskManager(config, event_bus2)
ie2 = IndicatorEngine(config)
se = StrategyEngine(ie2, event_bus2, eval_interval=5)
se.set_risk_manager(rm2)


# Mock market data provider
class MockProvider:
    def __init__(self, symbol):
        self.symbol = symbol
        self._klines = None
        self._price = 0.0
        self._funding = {}

    def refresh(self):
        self._klines = fetch_klines(self.symbol)
        self._price = fetch_price(self.symbol)
        self._funding = fetch_funding(self.symbol)

    def get_klines(self, symbol):
        return self._klines

    def get_price(self, symbol):
        return self._price

    def get_funding_rate(self, symbol):
        return self._funding


provider = MockProvider("DOGEUSDT")
provider.refresh()
se.set_market_data_provider(provider)

# Add strategies to engine
for strat in strategies:
    # Reset cooldowns for fresh test
    for rule in strat.rules:
        rule._last_triggered = 0
    se.add_strategy(strat)

print(f"   {len(strategies)} strateji yuklendi, motor baslatiliyor...")
se.start()

for turn in range(3):
    time.sleep(6)
    provider.refresh()
    regime_now = se.get_regime()
    conf_now = se.get_confluence()
    div_now = se.get_divergences()
    print(f"\n   --- Tur {turn + 1} ---")
    print(f"   Fiyat: {provider._price:.6f}")
    if regime_now:
        print(f"   Rejim: {regime_now.get('regime', '?')} ({regime_now.get('trend_direction', '?')})")
    if conf_now:
        print(f"   Confluence: {conf_now.get('score', 0):+.1f} -> {conf_now.get('signal', '?')}")
    print(f"   Diverjans: {len(div_now)} adet")
    print(f"   Toplam sinyal: {len(received_signals)}")

se.stop()

# ===== SUMMARY =====
print("\n" + "=" * 70)
print("  FINAL OZET")
print("=" * 70)
print(f"  Fiyat: {provider._price:.6f} USDT")
r = se.get_regime()
c = se.get_confluence()
if r:
    print(f"  Rejim: {r.get('regime')} | Yon: {r.get('trend_direction')} | Guven: {r.get('confidence', 0):.0%}")
if c:
    print(f"  Confluence: {c.get('score', 0):+.1f} -> {c.get('signal', '?')} ({c.get('strength', 0):.0%} guc)")
    print(f"  Oy dagilimi: {c.get('bullish_count', 0)} yukselis / {c.get('bearish_count', 0)} dusus")
print(f"  Diverjans: {len(se.get_divergences())} adet")
print(f"  Risk: ATR SL={sl_pct:.2f}% TP={tp_pct:.2f}% | Kelly={kelly:.2f}$")
print(f"  Tetiklenen stratejiler: {triggered}")
print(f"  Motor sinyalleri: {len(received_signals)} adet")
for sig in received_signals:
    p = sig.get("params", {})
    print(f"    - {sig.get('strategy_name')}: {p.get('side', '?').value} {p.get('size', 0):.0f} DOGE")

if c and c.get("signal") == "BUY":
    print("\n  ===> GENEL KARAR: ALIS")
elif c and c.get("signal") == "SELL":
    print("\n  ===> GENEL KARAR: SATIS")
else:
    score = c.get("score", 0) if c else 0
    print(f"\n  ===> GENEL KARAR: BEKLE (skor: {score:+.1f})")
print("=" * 70)
print("TEST TAMAMLANDI")
