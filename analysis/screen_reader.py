"""Reads live data directly from Binance Desktop UI elements.
Extracts MA values, OHLC, Order Book levels, Account info."""
import re
from loguru import logger


class BinanceScreenReader:
    """Reads data from Binance Desktop window via pywinauto descendants."""

    def __init__(self, binance_app):
        self._app = binance_app

    def read_all(self) -> dict:
        """Read all available data from Binance screen."""
        data = {}
        try:
            texts = self._get_all_texts()
            data["ohlc"] = self._extract_ohlc(texts)
            data["moving_averages"] = self._extract_moving_averages(texts)
            data["order_book"] = self._extract_order_book(texts)
            data["market_info"] = self._extract_market_info(texts)
            data["account"] = self._extract_account_info(texts)
            data["volume_atr"] = self._extract_volume_atr(texts)
        except Exception as e:
            logger.debug(f"Screen reader error: {e}")
        return data

    def _get_all_texts(self) -> list:
        """Get all text elements with positions from Binance UI."""
        descendants = self._app._get_descendants()
        texts = []
        for d in descendants:
            try:
                ct = d.element_info.control_type
                nm = d.element_info.name or ""
                if nm and ct == "Text":
                    r = d.rectangle()
                    texts.append((r.left, r.top, nm))
            except Exception:
                continue
        texts.sort(key=lambda x: (x[1], x[0]))
        return texts

    def _extract_ohlc(self, texts: list) -> dict:
        """Extract OHLC candle data from chart area."""
        ohlc = {}
        labels = {"O": "open", "H": "high", "L": "low", "C": "close"}
        for i, (l, t, nm) in enumerate(texts):
            if nm in labels and t < 500 and i + 1 < len(texts):
                next_nm = texts[i + 1][2]
                try:
                    ohlc[labels[nm]] = float(next_nm)
                except ValueError:
                    pass
        return ohlc

    def _extract_moving_averages(self, texts: list) -> dict:
        """Extract MA(7), MA(25), MA(99) values from chart legend."""
        mas = {}
        i = 0
        while i < len(texts) - 2:
            _, t, nm = texts[i]
            if nm == "MA" and t < 500:
                # Next text should be the period, then skip some, then the value
                try:
                    period = texts[i + 1][2]
                    # Find the float value after "close" and "0" entries
                    for j in range(i + 2, min(i + 6, len(texts))):
                        val_text = texts[j][2]
                        if re.match(r"^0\.\d{4,}", val_text):
                            mas[f"MA_{period}"] = float(val_text)
                            break
                except (IndexError, ValueError):
                    pass
            i += 1
        return mas

    def _extract_order_book(self, texts: list) -> dict:
        """Extract order book bid/ask levels."""
        bids = []
        asks = []
        ob_started = False
        mid_price = 0.0

        for i, (l, t, nm) in enumerate(texts):
            if nm == "Order Book":
                ob_started = True
                continue
            if not ob_started:
                continue
            if nm in ("Trades", "Top Movers"):
                break

            # Price entries look like "0.09141"
            if re.match(r"^0\.\d{4,}", nm) and l > 1100:
                price = float(nm)
                # Try to get size from next text
                size = 0.0
                if i + 1 < len(texts):
                    size_text = texts[i + 1][2].replace(",", "").replace("K", "e3").replace("M", "e6")
                    try:
                        size = float(size_text)
                    except ValueError:
                        pass

                # Determine bid vs ask by vertical position relative to spread
                if mid_price == 0:
                    mid_price = price
                if price >= mid_price:
                    asks.append({"price": price, "size": size})
                else:
                    bids.append({"price": price, "size": size})

        return {
            "bids": bids[:6],
            "asks": asks[:6],
            "bid_count": len(bids),
            "ask_count": len(asks),
        }

    def _extract_market_info(self, texts: list) -> dict:
        """Extract market data: mark price, funding, 24h high/low, volume."""
        info = {}
        for i, (l, t, nm) in enumerate(texts):
            if nm == "Mark" and i + 1 < len(texts):
                try:
                    info["mark_price"] = float(texts[i + 1][2])
                except (ValueError, IndexError):
                    pass
            elif nm.startswith("Funding") and i + 1 < len(texts):
                for j in range(i + 1, min(i + 5, len(texts))):
                    val = texts[j][2].replace("%", "")
                    try:
                        info["funding_rate"] = float(val) / 100
                        break
                    except ValueError:
                        continue
            elif nm == "24h High" and i + 1 < len(texts):
                try:
                    info["high_24h"] = float(texts[i + 1][2])
                except (ValueError, IndexError):
                    pass
            elif nm == "24h Low" and i + 1 < len(texts):
                try:
                    info["low_24h"] = float(texts[i + 1][2])
                except (ValueError, IndexError):
                    pass
            elif nm.startswith("24h Vol") and "USDT" in nm and i + 1 < len(texts):
                try:
                    vol = texts[i + 1][2].replace(",", "")
                    info["volume_24h_usdt"] = float(vol)
                except (ValueError, IndexError):
                    pass
            elif nm == "Open Interest" and i + 2 < len(texts):
                for j in range(i + 1, min(i + 4, len(texts))):
                    val = texts[j][2].replace(",", "")
                    try:
                        info["open_interest"] = float(val)
                        break
                    except ValueError:
                        continue
        return info

    def _extract_account_info(self, texts: list) -> dict:
        """Extract account data: balance, margin, PNL."""
        account = {}
        for i, (l, t, nm) in enumerate(texts):
            if nm == "Margin Balance" and i + 1 < len(texts):
                try:
                    account["margin_balance"] = float(texts[i + 1][2])
                except (ValueError, IndexError):
                    pass
            elif nm == "Unrealized PNL" and i + 1 < len(texts):
                try:
                    account["unrealized_pnl"] = float(texts[i + 1][2])
                except (ValueError, IndexError):
                    pass
            elif nm == "Maintenance Margin" and i + 1 < len(texts):
                try:
                    account["maintenance_margin"] = float(texts[i + 1][2])
                except (ValueError, IndexError):
                    pass
            elif nm == "Margin Ratio" and i + 1 < len(texts):
                try:
                    account["margin_ratio"] = float(texts[i + 1][2])
                except (ValueError, IndexError):
                    pass
        return account

    def _extract_volume_atr(self, texts: list) -> dict:
        """Extract Volume and ATR from chart."""
        data = {}
        for i, (l, t, nm) in enumerate(texts):
            if nm == "Volume" and t < 600 and i + 1 < len(texts):
                vol_text = texts[i + 1][2].replace(",", "")
                vol_text = vol_text.replace("M", "e6").replace("K", "e3").replace("B", "e9")
                try:
                    data["chart_volume"] = float(vol_text)
                except ValueError:
                    pass
            elif nm == "ATR" and t < 700:
                # ATR period then value
                for j in range(i + 1, min(i + 3, len(texts))):
                    try:
                        data["chart_atr"] = float(texts[j][2])
                        break
                    except ValueError:
                        continue
        return data
