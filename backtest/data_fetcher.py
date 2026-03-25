"""Binance kline data fetcher for backtesting."""
import requests
import time


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int,
                 limit: int = 1500) -> list:
    """Fetch klines from Binance Futures API with pagination and retry."""
    all_kl = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": symbol, "interval": interval,
            "startTime": int(cursor), "endTime": int(end_ms), "limit": limit,
        }
        data = []
        for attempt in range(5):
            try:
                resp = requests.get(
                    "https://fapi.binance.com/fapi/v1/klines",
                    params=params, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                break
            except (requests.ConnectionError, requests.Timeout):
                if attempt < 4:
                    time.sleep(2 * (attempt + 1))
        if not data:
            break
        all_kl.extend(data)
        cursor = int(data[-1][0]) + 1
        if len(data) < limit:
            break
        time.sleep(0.12)
    return all_kl


def get_top_symbols(n: int = 15) -> list[str]:
    """Get top N USDT futures symbols by 24h volume."""
    for attempt in range(5):
        try:
            resp = requests.get(
                "https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=20)
            resp.raise_for_status()
            break
        except (requests.ConnectionError, requests.Timeout):
            if attempt < 4:
                time.sleep(3)
            else:
                return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    tickers = resp.json()
    usdt = [t for t in tickers if t["symbol"].endswith("USDT")
            and not any(x in t["symbol"] for x in ["_", "BTCDOM", "DEFI"])]
    usdt.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
    return [t["symbol"] for t in usdt[:n]]
