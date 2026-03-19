"""BTC Correlation Engine - calculates portfolio beta exposure to BTC.

Prevents excessive directional correlation with BTC across all open positions.
Each altcoin's beta to BTC is calculated from hourly close price correlation.
Total portfolio beta is the net weighted sum of position betas.

Usage:
    engine = BTCCorrelationEngine(rest_client, config)
    engine.refresh()  # call periodically (every 5-10 min)
    allowed, reason = engine.check_position(symbol, direction, positions)
"""
import time
import numpy as np
import pandas as pd
from loguru import logger


class BTCCorrelationEngine:
    """Calculates and caches BTC correlation (beta) for altcoins.

    Beta formula: beta = cov(altcoin_returns, btc_returns) / var(btc_returns)
    Portfolio beta = sum(position_beta * position_weight * direction_sign)
    """

    def __init__(self, rest_client, config):
        self._rest = rest_client
        self._config = config
        self._btc_returns: np.ndarray = None
        self._beta_cache: dict[str, tuple[float, float]] = {}  # symbol -> (timestamp, beta)
        self._beta_cache_ttl: float = 600  # 10 minutes per symbol
        self._last_refresh: float = 0
        self._refresh_interval: float = 300  # 5 minutes
        self._btc_klines: pd.DataFrame = None

    def refresh(self) -> None:
        """Refresh BTC price data. Individual betas expire by TTL."""
        now = time.time()
        if now - self._last_refresh < self._refresh_interval:
            return

        try:
            btc_df = self._rest.get_klines("BTCUSDT", interval="1h", limit=100)
            if btc_df is not None and len(btc_df) >= 30:
                self._btc_klines = btc_df
                btc_close = btc_df["close"].values
                self._btc_returns = np.diff(np.log(btc_close))
                self._last_refresh = now
                # Evict expired entries instead of clearing all
                expired = [s for s, (ts, _) in self._beta_cache.items()
                           if now - ts > self._beta_cache_ttl]
                for s in expired:
                    del self._beta_cache[s]
                logger.debug(f"BTC correlation refreshed ({len(btc_close)} candles, "
                             f"evicted {len(expired)} stale betas)")
        except Exception as e:
            logger.debug(f"BTC correlation refresh failed: {e}")

    def get_beta(self, symbol: str) -> float:
        """Get BTC beta for a symbol. Uses cache if available.

        Returns beta coefficient:
        - beta > 0: moves WITH BTC
        - beta < 0: moves AGAINST BTC
        - beta ~ 1: moves 1:1 with BTC
        - beta ~ 0: uncorrelated
        """
        if symbol == "BTCUSDT":
            return 1.0

        now = time.time()
        cached = self._beta_cache.get(symbol)
        if cached and (now - cached[0]) < self._beta_cache_ttl:
            return cached[1]

        if self._btc_returns is None or len(self._btc_returns) < 20:
            return 0.8  # Default assumption: most altcoins correlate with BTC

        try:
            alt_df = self._rest.get_klines(symbol, interval="1h", limit=100)
            if alt_df is None or len(alt_df) < 30:
                return 0.8

            alt_close = alt_df["close"].values
            alt_returns = np.diff(np.log(alt_close))

            # Align lengths
            min_len = min(len(self._btc_returns), len(alt_returns))
            btc_r = self._btc_returns[-min_len:]
            alt_r = alt_returns[-min_len:]

            # Beta = cov(alt, btc) / var(btc)
            btc_var = np.var(btc_r)
            if btc_var < 1e-20:
                beta = 0.0
            else:
                beta = np.cov(alt_r, btc_r)[0][1] / btc_var

            # Clamp to reasonable range
            beta = max(-2.0, min(3.0, round(beta, 3)))
            self._beta_cache[symbol] = (now, beta)
            return beta

        except Exception as e:
            logger.debug(f"Beta calculation failed for {symbol}: {e}")
            return 0.8  # Conservative default

    def calculate_portfolio_beta(self, positions: dict) -> float:
        """Calculate net portfolio beta from all open positions.

        Args:
            positions: dict of {symbol: ActivePosition}

        Returns:
            Net portfolio beta. Positive = long BTC exposure, negative = short.
            Absolute value > threshold means excessive correlation.
        """
        if not positions:
            return 0.0

        total_notional = sum(p.notional_usdt for p in positions.values())
        if total_notional <= 0:
            return 0.0

        net_beta = 0.0
        for symbol, pos in positions.items():
            beta = self.get_beta(symbol)
            weight = pos.notional_usdt / total_notional
            direction_sign = 1.0 if pos.side.value in ("BUY_LONG", "Buy") else -1.0
            net_beta += beta * weight * direction_sign

        return round(net_beta, 3)

    def check_position(self, symbol: str, direction: str,
                       positions: dict) -> tuple[bool, str]:
        """Check if opening a new position would create excessive BTC beta.

        Args:
            symbol: The symbol to potentially open
            direction: "LONG" or "SHORT"
            positions: Current open positions {symbol: ActivePosition}

        Returns:
            (allowed: bool, reason: str)
        """
        strat = self._config.get("strategy", {})
        if not strat.get("btc_correlation_enabled", False):
            return True, ""

        max_beta = strat.get("btc_max_portfolio_beta", 2.0)

        # Current portfolio beta
        current_beta = self.calculate_portfolio_beta(positions)

        # Estimate new position's contribution
        new_beta = self.get_beta(symbol)
        direction_sign = 1.0 if direction == "LONG" else -1.0
        beta_contribution = new_beta * direction_sign

        # Simple estimate: assume new position is ~equal weight to average
        n_positions = len(positions) + 1
        estimated_new_beta = current_beta + (beta_contribution / n_positions)

        if abs(estimated_new_beta) > max_beta:
            return False, (f"btc_beta_exceeded (current={current_beta:+.2f}, "
                           f"new_contrib={beta_contribution:+.2f}→{estimated_new_beta:+.2f}, "
                           f"max={max_beta})")

        return True, ""
