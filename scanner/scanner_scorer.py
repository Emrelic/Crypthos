"""Scanner Scorer - analyzes and ranks symbols for trading opportunity quality.
Computes indicators, confluence, regime, divergence for each symbol and produces
a composite score (0-100) for ranking."""
import math
from dataclasses import dataclass, field
import pandas as pd
from loguru import logger
from core.config_manager import ConfigManager
from indicators.indicator_engine import IndicatorEngine
from analysis.confluence import ConfluenceScorer
from analysis.market_regime import MarketRegimeDetector
from analysis.divergence import DivergenceDetector


@dataclass
class ScanResult:
    """Result of scanning a single symbol."""
    symbol: str
    score: float                    # composite opportunity score (-100 to +100)
    direction: str                  # "LONG" or "SHORT"
    confluence: dict = field(default_factory=dict)
    regime: dict = field(default_factory=dict)
    divergences: list = field(default_factory=list)
    indicator_values: dict = field(default_factory=dict)
    volume_24h: float = 0.0
    price_change_pct: float = 0.0
    price: float = 0.0
    atr: float = 0.0
    atr_percent: float = 0.0       # ATR as % of price
    rsi: float = 50.0
    adx: float = 0.0
    eligible: bool = False          # passes all hard filters
    reject_reason: str = ""
    leverage: int = 0               # max leverage for this coin
    timeframe: str = "1m"           # optimal timeframe


class ScannerScorer:
    """Scores each symbol for buy/short opportunity quality."""

    def __init__(self, config: ConfigManager):
        self._config = config
        # Dedicated indicator engine for scanning (not shared with main engine)
        self._engine = IndicatorEngine(config)
        self._confluence = ConfluenceScorer(threshold=4.0)
        self._regime = MarketRegimeDetector()
        self._divergence = DivergenceDetector(lookback=20)

        # Score weights
        self._w_confluence = 0.35
        self._w_regime = 0.20
        self._w_volume = 0.15
        self._w_trend = 0.15
        self._w_risk = 0.15

    def score_symbol(self, symbol: str, klines: pd.DataFrame,
                     volume_24h: float = 0, price_change_pct: float = 0) -> ScanResult:
        """Compute full analysis and opportunity score for one symbol."""
        result = ScanResult(
            symbol=symbol,
            score=0.0,
            direction="LONG",
            volume_24h=volume_24h,
            price_change_pct=price_change_pct,
        )

        if klines is None or klines.empty or len(klines) < 50:
            result.reject_reason = "insufficient_data"
            return result

        try:
            # Compute all 30+ indicators
            indicators = self._engine.compute_all(klines)
            result.indicator_values = indicators
            result.price = indicators.get("Price", 0)
            result.atr = indicators.get("ATR", 0)
            rsi_val = indicators.get("RSI", 50)
            result.rsi = 50.0 if (rsi_val is None or (isinstance(rsi_val, float) and math.isnan(rsi_val))) else rsi_val
            adx_val = indicators.get("ADX", 0)
            result.adx = 0.0 if (adx_val is None or (isinstance(adx_val, float) and math.isnan(adx_val))) else adx_val

            if result.price > 0 and result.atr > 0:
                result.atr_percent = (result.atr / result.price) * 100

            # Regime detection
            regime = self._regime.detect(indicators)
            result.regime = regime

            # Confluence with regime weights
            regime_weights = regime.get("indicator_weights", {})
            confluence = self._confluence.score(indicators, regime_weights)
            result.confluence = confluence

            # Divergence
            ind_series = {}
            for name in ["RSI", "CCI", "MFI", "OBV"]:
                ind = self._engine.get_indicator(name)
                if ind and ind._series is not None:
                    ind_series[name] = ind._series
            divergences = self._divergence.detect_all(klines, ind_series)
            result.divergences = divergences

            # Determine direction
            conf_score = confluence.get("score", 0)
            if conf_score >= 0:
                result.direction = "LONG"
            else:
                result.direction = "SHORT"

            # Check eligibility (hard filters)
            eligible, reason = self._check_eligibility(result)
            result.eligible = eligible
            result.reject_reason = reason

            # Compute composite score
            if eligible:
                result.score = self._compute_score(result)

        except Exception as e:
            logger.debug(f"Scoring error for {symbol}: {e}")
            result.reject_reason = f"error: {e}"

        return result

    def score_batch(self, klines_map: dict[str, pd.DataFrame],
                    ticker_data: dict[str, dict]) -> list[ScanResult]:
        """Score multiple symbols and return sorted results."""
        results = []
        for symbol, klines in klines_map.items():
            ticker = ticker_data.get(symbol, {})
            vol = ticker.get("volume_24h", 0)
            change = ticker.get("price_change_pct", 0)
            result = self.score_symbol(symbol, klines, vol, change)
            results.append(result)

        # Sort by absolute score descending (best opportunities first)
        results.sort(key=lambda r: abs(r.score), reverse=True)
        return results

    def _check_eligibility(self, r: ScanResult) -> tuple[bool, str]:
        """Check hard filters. Returns (eligible, reason)."""
        conf_score = r.confluence.get("score", 0)
        conf_signal = r.confluence.get("signal", "NEUTRAL")
        regime_name = r.regime.get("regime", "UNKNOWN")
        trend_dir = r.regime.get("trend_direction", "NONE")

        # === READ STRATEGY CONFIG ===
        strat = self._config.get("strategy", {})
        max_lev = self._config.get("leverage.max_leverage", 100)
        high_leverage = max_lev >= 50

        # ATR vs LEVERAGE: 1-candle ATR should be <50% of liq distance
        if max_lev > 1 and r.atr_percent > 0:
            target_atr_pct = (1.0 / max_lev) * 100.0 * 0.25
            max_safe_atr_pct = target_atr_pct * 2.0
            if r.atr_percent > max_safe_atr_pct:
                return False, (f"atr_too_volatile_{max_lev}x "
                               f"(1m ATR={r.atr_percent:.3f}% > "
                               f"safe={max_safe_atr_pct:.3f}%)")

        # VOLATILE regime filter
        if strat.get("volatile_filter", True) and regime_name == "VOLATILE" and high_leverage:
            return False, "volatile_regime_high_leverage"

        # === CONFIGURABLE THRESHOLDS ===
        min_conf = strat.get("min_confluence", 4.0)
        min_adx = strat.get("min_adx", 18 if high_leverage else 15)
        max_rsi_long = strat.get("max_rsi_long", 62 if high_leverage else 65)
        min_rsi_short = strat.get("min_rsi_short", 38 if high_leverage else 35)
        use_macd = strat.get("macd_filter", True)
        use_volume = strat.get("volume_filter", True)

        if r.direction == "LONG":
            if conf_score < min_conf:
                return False, f"confluence_low ({conf_score:.1f}, need {min_conf}+)"
            if r.rsi > max_rsi_long:
                return False, f"rsi_overbought ({r.rsi:.0f})"
            if r.adx < min_adx:
                return False, f"adx_too_low ({r.adx:.0f}, need {min_adx}+)"

            # Volume confirmation
            if use_volume:
                obv_slope = r.indicator_values.get("OBV_slope", 0)
                cmf = r.indicator_values.get("CMF", 0)
                if obv_slope <= 0 and cmf <= 0:
                    return False, "no_volume_confirmation"

            # MACD filter
            if use_macd and high_leverage:
                macd_h = r.indicator_values.get("MACD_histogram", 0)
                if macd_h <= 0:
                    return False, f"macd_not_bullish ({macd_h:.4f})"

        else:
            if conf_score > -min_conf:
                return False, f"confluence_high ({conf_score:.1f}, need -{min_conf})"
            if r.rsi < min_rsi_short:
                return False, f"rsi_oversold ({r.rsi:.0f})"
            if r.adx < min_adx:
                return False, f"adx_too_low ({r.adx:.0f}, need {min_adx}+)"

            if use_volume:
                obv_slope = r.indicator_values.get("OBV_slope", 0)
                cmf = r.indicator_values.get("CMF", 0)
                if obv_slope >= 0 and cmf >= 0:
                    return False, "no_volume_confirmation"

            if use_macd and high_leverage:
                macd_h = r.indicator_values.get("MACD_histogram", 0)
                if macd_h >= 0:
                    return False, f"macd_not_bearish ({macd_h:.4f})"

        return True, ""

    def _compute_score(self, r: ScanResult) -> float:
        """Compute composite score (0-100, negative for SHORT)."""
        conf_score = self._score_confluence(r)
        regime_score = self._score_regime(r)
        volume_score = self._score_volume(r)
        trend_score = self._score_trend(r)
        risk_score = self._score_risk(r)

        raw = (
            self._w_confluence * conf_score +
            self._w_regime * regime_score +
            self._w_volume * volume_score +
            self._w_trend * trend_score +
            self._w_risk * risk_score
        )

        # Cap at 100
        raw = min(raw, 100.0)

        # Negative for SHORT direction
        if r.direction == "SHORT":
            raw = -raw

        return round(raw, 1)

    def _score_confluence(self, r: ScanResult) -> float:
        """Score 0-100 based on confluence signal strength."""
        conf = r.confluence
        raw_score = abs(conf.get("score", 0))
        strength = conf.get("strength", 0)
        bullish = conf.get("bullish_count", 0)
        total = conf.get("total_indicators", 1)

        # Normalize: confluence score of 4.0 = 50, 8.0 = 80, 12+ = 100
        base = min(raw_score / 12.0 * 100, 100)

        # Bonus for high agreement ratio
        agreement = bullish / total if r.direction == "LONG" else \
            conf.get("bearish_count", 0) / total
        agreement_bonus = agreement * 20

        return min(base + agreement_bonus, 100)

    def _score_regime(self, r: ScanResult) -> float:
        """Score 0-100 based on regime alignment with direction."""
        regime = r.regime.get("regime", "UNKNOWN")
        trend_dir = r.regime.get("trend_direction", "NONE")
        confidence = r.regime.get("confidence", 0)

        if r.direction == "LONG":
            if regime == "TRENDING" and trend_dir == "UP":
                base = 100
            elif regime == "BREAKOUT":
                base = 80
            elif regime == "TRENDING" and trend_dir == "DOWN":
                base = 20  # counter-trend
            elif regime == "RANGING":
                base = 50
            else:
                base = 30
        else:  # SHORT
            if regime == "TRENDING" and trend_dir == "DOWN":
                base = 100
            elif regime == "VOLATILE":
                base = 70
            elif regime == "BREAKOUT":
                base = 60
            elif regime == "TRENDING" and trend_dir == "UP":
                base = 20
            else:
                base = 40

        return base * confidence

    def _score_volume(self, r: ScanResult) -> float:
        """Score 0-100 based on volume confirmation."""
        score = 50.0  # base
        obv_slope = r.indicator_values.get("OBV_slope", 0)
        cmf = r.indicator_values.get("CMF", 0)
        mfi = r.indicator_values.get("MFI", 50)

        if r.direction == "LONG":
            if obv_slope > 0:
                score += 20
            if cmf > 0.1:
                score += 15
            elif cmf > 0:
                score += 5
            if mfi < 30:
                score += 15  # oversold MFI = buying opportunity
        else:
            if obv_slope < 0:
                score += 20
            if cmf < -0.1:
                score += 15
            elif cmf < 0:
                score += 5
            if mfi > 70:
                score += 15

        return min(score, 100)

    def _score_trend(self, r: ScanResult) -> float:
        """Score 0-100 based on trend strength indicators."""
        score = 0.0
        adx = r.indicator_values.get("ADX", 0)
        plus_di = r.indicator_values.get("ADX_plus_DI", 0)
        minus_di = r.indicator_values.get("ADX_minus_DI", 0)
        st = r.indicator_values.get("Supertrend_trend", "")
        macd_h = r.indicator_values.get("MACD_histogram", 0)
        price = r.indicator_values.get("Price", 0)
        sma200 = r.indicator_values.get("SMA_slow", 0)

        # ADX strength
        if adx > 30:
            score += 30
        elif adx > 20:
            score += 15

        # DI alignment
        if r.direction == "LONG" and plus_di > minus_di:
            score += 20
        elif r.direction == "SHORT" and minus_di > plus_di:
            score += 20

        # Supertrend
        if r.direction == "LONG" and st == "UP":
            score += 20
        elif r.direction == "SHORT" and st == "DOWN":
            score += 20

        # MACD histogram
        if r.direction == "LONG" and macd_h > 0:
            score += 15
        elif r.direction == "SHORT" and macd_h < 0:
            score += 15

        # Price vs SMA200
        if price > 0 and sma200 > 0:
            if r.direction == "LONG" and price > sma200:
                score += 15
            elif r.direction == "SHORT" and price < sma200:
                score += 15

        return min(score, 100)

    def _score_risk(self, r: ScanResult) -> float:
        """Score 0-100 based on risk quality (ATR, divergences)."""
        score = 60.0

        # ATR% in sweet spot (adjusted for leverage)
        max_lev = self._config.get("leverage.max_leverage", 20)
        atr_pct = r.atr_percent
        # 20x: liq=%5, SL~%1.75 -> sweet spot 0.2%-1.5%
        # 75x: liq=%1.3, SL~%0.3 -> sweet spot 0.05%-0.3%
        safe_atr = (1.0 / max(max_lev, 1)) * 100 * 0.25
        if atr_pct <= safe_atr:
            score += 20
        elif atr_pct <= safe_atr * 2:
            score += 10  # borderline
        elif atr_pct > safe_atr * 4:
            score -= 30  # too volatile
        if atr_pct < 0.05:
            score -= 20  # no movement

        # Divergence check
        for d in r.divergences:
            if r.direction == "LONG" and d.get("type") == "REGULAR_BULLISH":
                score += 20  # bullish divergence supports long
            elif r.direction == "LONG" and d.get("type") == "REGULAR_BEARISH":
                score -= 20  # bearish divergence warns against long
            elif r.direction == "SHORT" and d.get("type") == "REGULAR_BEARISH":
                score += 20
            elif r.direction == "SHORT" and d.get("type") == "REGULAR_BULLISH":
                score -= 20

        return max(0, min(score, 100))
