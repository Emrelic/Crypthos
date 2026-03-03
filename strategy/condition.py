from core.constants import ConditionOperator


class Condition:
    """A single evaluable condition like 'RSI < 30'."""

    def __init__(self, indicator: str, operator: ConditionOperator,
                 threshold, label: str = None):
        self.indicator = indicator
        self.operator = operator
        self.threshold = threshold
        self.label = label or f"{indicator} {operator.value} {threshold}"

    def evaluate(self, indicator_values: dict, market_data: dict) -> bool:
        value = self._resolve_value(indicator_values, market_data)
        if value is None:
            return False

        threshold = self._resolve_threshold(indicator_values, market_data)

        match self.operator:
            case ConditionOperator.LESS_THAN:
                return value < threshold
            case ConditionOperator.LESS_EQUAL:
                return value <= threshold
            case ConditionOperator.GREATER_THAN:
                return value > threshold
            case ConditionOperator.GREATER_EQUAL:
                return value >= threshold
            case ConditionOperator.EQUAL:
                return value == threshold
            case ConditionOperator.CROSSES_ABOVE:
                prev = self._resolve_prev_value(indicator_values, market_data)
                if prev is None:
                    return False
                return prev <= threshold and value > threshold
            case ConditionOperator.CROSSES_BELOW:
                prev = self._resolve_prev_value(indicator_values, market_data)
                if prev is None:
                    return False
                return prev >= threshold and value < threshold
        return False

    def _resolve_value(self, indicator_values: dict, market_data: dict):
        # Check indicator results first
        if self.indicator in indicator_values:
            v = indicator_values[self.indicator]
            return v["value"] if isinstance(v, dict) else v
        # Check market data (price, funding, etc.)
        market_map = {
            "Price": "price",
            "Mark_Price": "mark_price",
            "Funding_Rate": "funding_rate",
            "confluence_score": "confluence_score",
            "confluence_signal": "confluence_signal",
            "regime": "regime",
        }
        if self.indicator in market_map:
            return market_data.get(market_map[self.indicator])
        # Fallback: check market_data directly by key
        if self.indicator in market_data:
            return market_data[self.indicator]
        return None

    def _resolve_prev_value(self, indicator_values: dict, market_data: dict):
        if self.indicator in indicator_values:
            v = indicator_values[self.indicator]
            if isinstance(v, dict):
                return v.get("prev_value")
        return None

    def _resolve_threshold(self, indicator_values: dict, market_data: dict):
        """Threshold can be a number or a reference to another indicator."""
        if isinstance(self.threshold, str):
            # Reference to another indicator like "SMA_slow"
            if self.threshold in indicator_values:
                v = indicator_values[self.threshold]
                return v["value"] if isinstance(v, dict) else v
        return self.threshold

    def to_dict(self) -> dict:
        return {
            "indicator": self.indicator,
            "operator": self.operator.value,
            "threshold": self.threshold,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Condition":
        op = ConditionOperator(data["operator"])
        return cls(
            indicator=data["indicator"],
            operator=op,
            threshold=data["threshold"],
            label=data.get("label"),
        )
