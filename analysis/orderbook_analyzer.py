"""Order Book analysis: imbalance, depth ratio, weighted mid-price."""
from loguru import logger


class OrderBookAnalyzer:
    """Analyzes order book data for trading signals."""

    # Weights for top 5 price levels (closer = higher weight)
    LEVEL_WEIGHTS = [1.0, 0.5, 0.25, 0.125, 0.0625]

    def analyze(self, order_book: dict) -> dict:
        """Analyze order book and return signal data."""
        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])

        result = {
            "imbalance": 0.0,
            "weighted_imbalance": 0.0,
            "depth_ratio": 0.0,
            "bid_total": 0.0,
            "ask_total": 0.0,
            "spread": 0.0,
            "weighted_mid_price": 0.0,
            "signal": "NEUTRAL",
            "signal_strength": 0.0,
        }

        if not bids or not asks:
            return result

        # Total volumes
        bid_total = sum(b.get("size", 0) for b in bids)
        ask_total = sum(a.get("size", 0) for a in asks)
        result["bid_total"] = bid_total
        result["ask_total"] = ask_total

        # Simple imbalance: (bid - ask) / (bid + ask)
        total = bid_total + ask_total
        if total > 0:
            result["imbalance"] = (bid_total - ask_total) / total

        # Weighted imbalance (closer levels matter more)
        w_bid = 0.0
        w_ask = 0.0
        for i, b in enumerate(bids[:5]):
            w = self.LEVEL_WEIGHTS[i] if i < len(self.LEVEL_WEIGHTS) else 0.0
            w_bid += b.get("size", 0) * w
        for i, a in enumerate(asks[:5]):
            w = self.LEVEL_WEIGHTS[i] if i < len(self.LEVEL_WEIGHTS) else 0.0
            w_ask += a.get("size", 0) * w
        w_total = w_bid + w_ask
        if w_total > 0:
            result["weighted_imbalance"] = (w_bid - w_ask) / w_total

        # Depth ratio
        if ask_total > 0:
            result["depth_ratio"] = bid_total / ask_total

        # Spread
        best_bid = bids[0].get("price", 0)
        best_ask = asks[0].get("price", 0)
        if best_bid > 0 and best_ask > 0:
            result["spread"] = best_ask - best_bid

        # Weighted mid-price
        bid_vol = bids[0].get("size", 1)
        ask_vol = asks[0].get("size", 1)
        if bid_vol + ask_vol > 0:
            result["weighted_mid_price"] = (
                best_bid * ask_vol + best_ask * bid_vol
            ) / (bid_vol + ask_vol)

        # Generate signal
        imb = result["weighted_imbalance"]
        if imb > 0.3:
            result["signal"] = "BUY"
            result["signal_strength"] = min(imb / 0.6, 1.0)
        elif imb < -0.3:
            result["signal"] = "SELL"
            result["signal_strength"] = min(abs(imb) / 0.6, 1.0)
        else:
            result["signal"] = "NEUTRAL"
            result["signal_strength"] = 0.0

        return result
