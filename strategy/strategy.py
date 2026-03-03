from datetime import datetime
from strategy.rule import Rule
from strategy.actions import TradeAction


class Strategy:
    """A Strategy groups related Rules for a specific symbol."""

    def __init__(self, name: str, symbol: str, rules: list[Rule] = None,
                 description: str = ""):
        self.name = name
        self.symbol = symbol
        self.rules = rules or []
        self.description = description
        self.enabled = False
        self.created_at = datetime.now().isoformat()

    def add_rule(self, rule: Rule) -> None:
        self.rules.append(rule)

    def remove_rule(self, rule_name: str) -> None:
        self.rules = [r for r in self.rules if r.name != rule_name]

    def evaluate(self, indicator_values: dict, market_data: dict) -> list[TradeAction]:
        if not self.enabled:
            return []
        actions = []
        for rule in self.rules:
            action = rule.evaluate(indicator_values, market_data)
            if action is not None:
                actions.append(action)
        return actions

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "symbol": self.symbol,
            "rules": [r.to_dict() for r in self.rules],
            "description": self.description,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Strategy":
        rules = [Rule.from_dict(r) for r in data.get("rules", [])]
        s = cls(
            name=data["name"],
            symbol=data["symbol"],
            rules=rules,
            description=data.get("description", ""),
        )
        s.enabled = data.get("enabled", False)
        s.created_at = data.get("created_at", datetime.now().isoformat())
        return s
