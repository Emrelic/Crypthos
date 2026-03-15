"""Order Book analysis: imbalance, depth ratio, wall detection, liquidity scoring.

Analyzes Binance Futures depth data to detect:
  - Bid/Ask imbalance (weighted by proximity to current price)
  - Large order walls (support/resistance from big players)
  - Volume-relative wall strength (seconds to break through)
  - Total depth pressure (all levels combined)
  - Spread-based liquidity filtering
"""
from loguru import logger


class OrderBookAnalyzer:
    """Analyzes order book data for trading signals.

    Input format (Binance /fapi/v1/depth):
      {"bids": [[price, qty], ...], "asks": [[price, qty], ...]}
    OR legacy format:
      {"bids": [{"price": x, "size": y}, ...], "asks": [...]}
    """

    # Weights for top levels (closer to spread = higher weight)
    LEVEL_WEIGHTS = [1.0, 0.7, 0.5, 0.35, 0.25, 0.18, 0.12, 0.08, 0.05, 0.03]

    # Wall detection: a single level with this many times the average = wall
    WALL_MULTIPLIER = 5.0

    def analyze(self, order_book: dict, current_price: float = 0,
                volume_24h: float = 0, thin_book_seconds: float = 5.0) -> dict:
        """Analyze order book and return comprehensive signal data.

        Args:
            order_book: Binance depth data
            current_price: current market price
            volume_24h: 24h quote volume in USDT (for wall strength calculation)

        Returns dict with:
          - imbalance, weighted_imbalance: bid/ask balance (-1 to +1)
          - depth_ratio: bid_total / ask_total
          - spread, spread_pct: absolute and percentage spread
          - bid_wall, ask_wall: detected walls {price, size, distance_pct, wall_seconds}
          - liquidity_score: 0-100 (how liquid this market is)
          - signal: BUY/SELL/NEUTRAL
          - signal_strength: 0.0-1.0
          - wall_signal: UP_BLOCKED/DOWN_BLOCKED/NONE
          - thin_book: True if dangerously low liquidity
          - ask_depth_seconds: total ask side depth in seconds of volume
          - bid_depth_seconds: total bid side depth in seconds of volume
        """
        bids_raw = order_book.get("bids", [])
        asks_raw = order_book.get("asks", [])

        # Normalize to [{price, size}, ...] format
        bids = self._normalize_levels(bids_raw)
        asks = self._normalize_levels(asks_raw)

        result = {
            "imbalance": 0.0,
            "weighted_imbalance": 0.0,
            "depth_ratio": 0.0,
            "bid_total": 0.0,
            "ask_total": 0.0,
            "bid_total_usdt": 0.0,
            "ask_total_usdt": 0.0,
            "spread": 0.0,
            "spread_pct": 0.0,
            "weighted_mid_price": 0.0,
            "bid_wall": None,
            "ask_wall": None,
            "liquidity_score": 0.0,
            "signal": "NEUTRAL",
            "signal_strength": 0.0,
            "wall_signal": "NONE",
            "thin_book": False,
            "ask_depth_seconds": 0.0,
            "bid_depth_seconds": 0.0,
            "book_depth_seconds": 0.0,
        }

        if not bids or not asks:
            result["thin_book"] = True
            return result

        best_bid = bids[0]["price"]
        best_ask = asks[0]["price"]
        mid_price = (best_bid + best_ask) / 2 if best_bid > 0 else current_price
        if mid_price <= 0:
            return result

        # Volume per second (for wall strength calculation)
        vol_per_sec = volume_24h / 86400 if volume_24h > 0 else 0

        # --- Total volumes (in USDT) ---
        bid_total = sum(b["size"] for b in bids)
        ask_total = sum(a["size"] for a in asks)
        bid_total_usdt = sum(b["size"] * b["price"] for b in bids)
        ask_total_usdt = sum(a["size"] * a["price"] for a in asks)
        result["bid_total"] = bid_total
        result["ask_total"] = ask_total
        result["bid_total_usdt"] = bid_total_usdt
        result["ask_total_usdt"] = ask_total_usdt

        # --- Total depth in seconds (how long to eat through all levels) ---
        if vol_per_sec > 0:
            result["ask_depth_seconds"] = round(ask_total_usdt / vol_per_sec, 1)
            result["bid_depth_seconds"] = round(bid_total_usdt / vol_per_sec, 1)

        # --- Simple imbalance ---
        total = bid_total + ask_total
        if total > 0:
            result["imbalance"] = (bid_total - ask_total) / total

        # --- Weighted imbalance (closer levels matter more) ---
        w_bid = 0.0
        w_ask = 0.0
        for i, b in enumerate(bids[:10]):
            w = self.LEVEL_WEIGHTS[i] if i < len(self.LEVEL_WEIGHTS) else 0.02
            w_bid += b["size"] * b["price"] * w  # USDT-weighted
        for i, a in enumerate(asks[:10]):
            w = self.LEVEL_WEIGHTS[i] if i < len(self.LEVEL_WEIGHTS) else 0.02
            w_ask += a["size"] * a["price"] * w
        w_total = w_bid + w_ask
        if w_total > 0:
            result["weighted_imbalance"] = (w_bid - w_ask) / w_total

        # --- Depth ratio ---
        if ask_total > 0:
            result["depth_ratio"] = bid_total / ask_total

        # --- Spread ---
        if best_bid > 0 and best_ask > 0:
            spread = best_ask - best_bid
            result["spread"] = spread
            result["spread_pct"] = (spread / mid_price) * 100

        # --- Weighted mid-price ---
        bid_vol = bids[0]["size"]
        ask_vol = asks[0]["size"]
        if bid_vol + ask_vol > 0:
            result["weighted_mid_price"] = (
                best_bid * ask_vol + best_ask * bid_vol
            ) / (bid_vol + ask_vol)

        # --- Wall Detection (with volume-relative strength) ---
        result["bid_wall"] = self._detect_wall(bids, mid_price, side="bid",
                                                vol_per_sec=vol_per_sec)
        result["ask_wall"] = self._detect_wall(asks, mid_price, side="ask",
                                                vol_per_sec=vol_per_sec)

        # Wall signal: if a wall is close and big, it blocks that direction
        if result["ask_wall"] and result["ask_wall"]["distance_pct"] < 1.0:
            result["wall_signal"] = "UP_BLOCKED"
        elif result["bid_wall"] and result["bid_wall"]["distance_pct"] < 1.0:
            result["wall_signal"] = "DOWN_BLOCKED"

        # --- Liquidity Score (0-100) ---
        result["liquidity_score"] = self._calc_liquidity_score(
            bid_total_usdt, ask_total_usdt, result["spread_pct"], len(bids), len(asks))

        # --- Thin book detection (volume-relative) ---
        total_usdt = bid_total_usdt + ask_total_usdt
        # Spread too wide: direct cost too high
        spread_thin = result["spread_pct"] > 0.15
        # Volume-relative: book depth < N seconds of trading volume
        # Means the entire visible book can be consumed in under N seconds
        if vol_per_sec > 0:
            book_seconds = total_usdt / vol_per_sec if total_usdt > 0 else 0.0
            result["book_depth_seconds"] = round(book_seconds, 1)
            vol_thin = book_seconds < thin_book_seconds
        else:
            # No volume data: fallback to absolute threshold
            vol_thin = total_usdt < 50_000
        result["thin_book"] = spread_thin or vol_thin

        # --- Generate signal ---
        imb = result["weighted_imbalance"]
        if imb > 0.25:
            result["signal"] = "BUY"
            result["signal_strength"] = min(imb / 0.5, 1.0)
        elif imb < -0.25:
            result["signal"] = "SELL"
            result["signal_strength"] = min(abs(imb) / 0.5, 1.0)
        else:
            result["signal"] = "NEUTRAL"
            result["signal_strength"] = 0.0

        return result

    def _normalize_levels(self, levels: list) -> list[dict]:
        """Convert Binance depth format [[price, qty], ...] or
        legacy [{"price": x, "size": y}, ...] to unified format."""
        result = []
        for item in levels:
            if isinstance(item, (list, tuple)):
                # Binance format: [price_str, qty_str]
                result.append({
                    "price": float(item[0]),
                    "size": float(item[1]),
                })
            elif isinstance(item, dict):
                # Legacy format
                result.append({
                    "price": float(item.get("price", 0)),
                    "size": float(item.get("size", 0)),
                })
        return result

    def _detect_wall(self, levels: list[dict], mid_price: float,
                     side: str, vol_per_sec: float = 0) -> dict | None:
        """Detect if any single level has disproportionately large volume.
        Returns {price, size, size_usdt, distance_pct, multiplier, wall_seconds} or None."""
        if len(levels) < 5:
            return None

        # Average USDT size of levels (excluding top 2 biggest to reduce skew)
        usdt_sizes = sorted([lv["size"] * lv["price"] for lv in levels])
        # Trim top 2 outliers for cleaner average
        trimmed = usdt_sizes[:-2] if len(usdt_sizes) > 4 else usdt_sizes
        avg_usdt = sum(trimmed) / len(trimmed) if trimmed else 1

        if avg_usdt <= 0:
            return None

        # Find the largest level
        best = None
        best_mult = 0
        best_usdt = 0
        for lv in levels:
            lv_usdt = lv["size"] * lv["price"]
            mult = lv_usdt / avg_usdt
            if mult > best_mult:
                best_mult = mult
                best = lv
                best_usdt = lv_usdt

        if best and best_mult >= self.WALL_MULTIPLIER:
            dist = abs(best["price"] - mid_price) / mid_price * 100
            # Wall strength: how many seconds of average volume to break this wall
            wall_seconds = best_usdt / vol_per_sec if vol_per_sec > 0 else 9999.0
            return {
                "price": best["price"],
                "size": best["size"],
                "size_usdt": best_usdt,
                "distance_pct": round(dist, 3),
                "multiplier": round(best_mult, 1),
                "wall_seconds": round(wall_seconds, 1),
            }

        return None

    def _calc_liquidity_score(self, bid_usdt: float, ask_usdt: float,
                              spread_pct: float,
                              bid_levels: int, ask_levels: int) -> float:
        """Calculate overall liquidity quality (0-100).
        Higher = more liquid = safer to trade."""
        score = 0.0

        # Total depth (both sides)
        total = bid_usdt + ask_usdt
        if total > 1_000_000:
            score += 40
        elif total > 500_000:
            score += 30
        elif total > 100_000:
            score += 20
        elif total > 50_000:
            score += 10

        # Spread quality
        if spread_pct < 0.01:
            score += 30  # very tight
        elif spread_pct < 0.03:
            score += 25
        elif spread_pct < 0.05:
            score += 15
        elif spread_pct < 0.10:
            score += 5
        # >0.10% spread = no bonus

        # Balance between bid/ask (extreme one-sidedness is bad liquidity)
        if total > 0:
            balance = min(bid_usdt, ask_usdt) / max(bid_usdt, ask_usdt) if max(bid_usdt, ask_usdt) > 0 else 0
            score += balance * 20  # max 20 pts for perfect balance

        # Level count
        min_levels = min(bid_levels, ask_levels)
        if min_levels >= 15:
            score += 10
        elif min_levels >= 10:
            score += 5

        return min(score, 100)
