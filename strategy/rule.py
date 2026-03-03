import time
from strategy.condition import Condition
from strategy.actions import TradeAction


class Rule:
    """A Rule = conditions (ANDed) + action + cooldown."""

    def __init__(self, name: str, conditions: list[Condition],
                 action: TradeAction, cooldown_seconds: int = 60):
        self.name = name
        self.conditions = conditions
        self.action = action
        self.cooldown_seconds = cooldown_seconds
        self.enabled = True
        self._last_triggered: float = 0

    def evaluate(self, indicator_values: dict, market_data: dict) -> TradeAction | None:
        if not self.enabled:
            return None
        now = time.time()
        if now - self._last_triggered < self.cooldown_seconds:
            return None
        if all(c.evaluate(indicator_values, market_data) for c in self.conditions):
            self._last_triggered = now
            return self.action
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "conditions": [c.to_dict() for c in self.conditions],
            "action": self.action.to_dict(),
            "cooldown_seconds": self.cooldown_seconds,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Rule":
        conditions = [Condition.from_dict(c) for c in data["conditions"]]
        action = TradeAction.from_dict(data["action"])
        rule = cls(
            name=data["name"],
            conditions=conditions,
            action=action,
            cooldown_seconds=data.get("cooldown_seconds", 60),
        )
        rule.enabled = data.get("enabled", True)
        return rule
