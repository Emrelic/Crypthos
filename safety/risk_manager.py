"""Advanced Risk Manager - ATR-based stops, trailing stops, Kelly criterion,
drawdown protection, daily loss limits, position sizing."""
import time
from loguru import logger
from core.config_manager import ConfigManager
from core.event_bus import EventBus
from core.constants import EventType


class RiskManager:
    """Validates orders against risk rules before execution.

    Features:
    - Max single order / total position limits
    - ATR-based dynamic stop loss
    - Trailing stop loss
    - Kelly Criterion position sizing
    - Max drawdown protection
    - Daily loss limit
    - Consecutive loss tracking
    """

    def __init__(self, config: ConfigManager, event_bus: EventBus):
        self._config = config
        self._event_bus = event_bus
        self._total_exposure_usdt: float = 0.0
        self._killed = False

        # Drawdown tracking
        self._peak_balance: float = config.get("risk.initial_balance", 100.0)
        self._current_balance: float = self._peak_balance

        # Daily loss tracking
        self._daily_loss: float = 0.0
        self._daily_reset_time: float = 0.0
        self._reset_daily_if_needed()

        # Consecutive losses
        self._consecutive_losses: int = 0
        self._max_consecutive_before_pause: int = config.get(
            "risk.max_consecutive_losses", 5
        )

        # Trade history for Kelly
        self._win_count: int = 0
        self._loss_count: int = 0
        self._total_win_amount: float = 0.0
        self._total_loss_amount: float = 0.0

    # ──────────────────── Order Validation ────────────────────

    def validate_order(self, size: float, price: float,
                       symbol: str = None) -> tuple[bool, str]:
        if self._killed:
            return False, "Kill switch active"

        if size is None or price is None:
            return False, "Size or price is None"

        if size <= 0 or price <= 0:
            return False, "Size and price must be positive"

        notional = size * price

        # Max single order check
        max_single = self._config.get("risk.max_single_order_usdt", 50.0)
        if notional > max_single:
            return False, f"Order {notional:.2f} USDT exceeds max single order {max_single:.2f}"

        # Max total position check
        max_pos = self._config.get("risk.max_position_usdt", 100.0)
        if self._total_exposure_usdt + notional > max_pos:
            return False, (
                f"Total exposure would be {self._total_exposure_usdt + notional:.2f} USDT, "
                f"exceeds max {max_pos:.2f}"
            )

        # Daily loss limit
        self._reset_daily_if_needed()
        daily_limit = self._config.get("risk.daily_loss_limit_usdt", 50.0)
        if self._daily_loss >= daily_limit:
            return False, f"Daily loss limit reached: {self._daily_loss:.2f}/{daily_limit:.2f} USDT"

        # Drawdown check
        max_dd = self._config.get("risk.max_drawdown_percent", 20.0)
        current_dd = self.get_drawdown_percent()
        if current_dd >= max_dd:
            return False, f"Max drawdown reached: {current_dd:.1f}% >= {max_dd:.1f}%"

        # Consecutive loss pause
        if self._consecutive_losses >= self._max_consecutive_before_pause:
            return False, (
                f"Paused: {self._consecutive_losses} consecutive losses. "
                f"Reset exposure or deactivate to continue."
            )

        return True, ""

    def requires_confirmation(self, size: float, price: float) -> bool:
        if size is None or price is None:
            return False
        notional = size * price
        threshold = self._config.get("risk.confirm_above_usdt", 20.0)
        return notional > threshold

    # ──────────────────── ATR-Based Stop Loss ────────────────────

    def calculate_atr_stop(self, price: float, atr: float,
                           side: str = "BUY", multiplier: float = None) -> float:
        """Calculate dynamic stop loss based on ATR.

        Args:
            price: Current entry price
            atr: Current ATR value
            side: BUY or SELL
            multiplier: ATR multiplier (default from config)

        Returns:
            Stop loss price
        """
        if atr <= 0:
            return 0.0

        mult = multiplier or self._config.get("risk.atr_stop_multiplier", 2.0)
        stop_distance = atr * mult

        if side.upper() in ("BUY", "BUY_LONG"):
            return price - stop_distance
        else:
            return price + stop_distance

    def calculate_atr_tp(self, price: float, atr: float,
                         side: str = "BUY", rr_ratio: float = None) -> float:
        """Calculate take profit based on ATR and risk:reward ratio.

        Args:
            price: Entry price
            atr: Current ATR value
            side: BUY or SELL
            rr_ratio: Risk:Reward ratio (default from config)

        Returns:
            Take profit price
        """
        if atr <= 0:
            return 0.0

        ratio = rr_ratio or self._config.get("risk.reward_ratio", 2.0)
        mult = self._config.get("risk.atr_stop_multiplier", 2.0)
        tp_distance = atr * mult * ratio

        if side.upper() in ("BUY", "BUY_LONG"):
            return price + tp_distance
        else:
            return price - tp_distance

    def calculate_sl_percent(self, price: float, atr: float,
                             side: str = "BUY") -> float:
        """Convert ATR stop to percentage for order executor."""
        stop = self.calculate_atr_stop(price, atr, side)
        if price <= 0 or stop <= 0:
            return 2.0  # Default 2%
        return abs(price - stop) / price * 100

    def calculate_tp_percent(self, price: float, atr: float,
                             side: str = "BUY") -> float:
        """Convert ATR take profit to percentage for order executor."""
        tp = self.calculate_atr_tp(price, atr, side)
        if price <= 0 or tp <= 0:
            return 5.0  # Default 5%
        return abs(tp - price) / price * 100

    # ──────────────────── Trailing Stop ────────────────────

    def calculate_trailing_stop(self, entry_price: float, current_price: float,
                                atr: float, side: str = "BUY") -> float:
        """Calculate trailing stop that moves with price.

        The stop moves up (for long) as price increases, but never moves back down.

        Returns:
            Trailing stop price
        """
        if atr <= 0:
            return 0.0

        trail_mult = self._config.get("risk.trailing_atr_multiplier", 1.5)
        trail_distance = atr * trail_mult

        if side.upper() in ("BUY", "BUY_LONG"):
            # For longs, trail below current price
            trail_stop = current_price - trail_distance
            # Never below entry stop
            entry_stop = self.calculate_atr_stop(entry_price, atr, side)
            return max(trail_stop, entry_stop)
        else:
            # For shorts, trail above current price
            trail_stop = current_price + trail_distance
            entry_stop = self.calculate_atr_stop(entry_price, atr, side)
            return min(trail_stop, entry_stop)

    # ──────────────────── Kelly Criterion ────────────────────

    def kelly_position_size(self, balance: float, price: float,
                            atr: float = None) -> float:
        """Calculate optimal position size using Kelly Criterion.

        Kelly % = W - (1-W)/R
        Where W = win rate, R = average win / average loss

        Returns position size in USDT (capped by config limits).
        """
        total_trades = self._win_count + self._loss_count
        if total_trades < 10:
            # Not enough data, use fixed fraction
            fraction = self._config.get("risk.default_position_fraction", 0.02)
            return balance * fraction

        win_rate = self._win_count / total_trades
        if self._total_loss_amount == 0:
            avg_rr = 1.5
        else:
            avg_win = self._total_win_amount / max(self._win_count, 1)
            avg_loss = self._total_loss_amount / max(self._loss_count, 1)
            avg_rr = avg_win / avg_loss if avg_loss > 0 else 1.5

        kelly_pct = win_rate - (1 - win_rate) / avg_rr

        # Cap Kelly at half (fractional Kelly for safety)
        kelly_pct = max(0, min(kelly_pct * 0.5, 0.25))

        position_usdt = balance * kelly_pct

        # Apply config limits
        max_single = self._config.get("risk.max_single_order_usdt", 50.0)
        position_usdt = min(position_usdt, max_single)

        return round(position_usdt, 2)

    def kelly_position_qty(self, balance: float, price: float,
                           atr: float = None) -> float:
        """Calculate position size in quantity (coins)."""
        usdt = self.kelly_position_size(balance, price, atr)
        if price <= 0:
            return 0.0
        return round(usdt / price, 4)

    # ──────────────────── Drawdown Protection ────────────────────

    def get_drawdown_percent(self) -> float:
        """Current drawdown from peak balance."""
        if self._peak_balance <= 0:
            return 0.0
        return (self._peak_balance - self._current_balance) / self._peak_balance * 100

    def update_balance(self, new_balance: float) -> None:
        """Update current balance and peak tracking."""
        self._current_balance = new_balance
        if new_balance > self._peak_balance:
            self._peak_balance = new_balance

    # ──────────────────── Trade Recording ────────────────────

    def record_order(self, size: float, price: float) -> None:
        """Record a new order placement."""
        self._total_exposure_usdt += size * price

    def record_trade_result(self, pnl: float) -> None:
        """Record trade result for Kelly calculation and loss tracking.

        Args:
            pnl: Profit/loss in USDT (positive = win, negative = loss)
        """
        if pnl >= 0:
            self._win_count += 1
            self._total_win_amount += pnl
            self._consecutive_losses = 0
        else:
            self._loss_count += 1
            self._total_loss_amount += abs(pnl)
            self._consecutive_losses += 1
            self._daily_loss += abs(pnl)

        # Update balance
        self._current_balance += pnl
        if self._current_balance > self._peak_balance:
            self._peak_balance = self._current_balance

        # Publish stats
        self._event_bus.publish(EventType.RISK_UPDATE, {
            "drawdown": self.get_drawdown_percent(),
            "daily_loss": self._daily_loss,
            "consecutive_losses": self._consecutive_losses,
            "win_rate": self.get_win_rate(),
            "kelly_fraction": self._get_kelly_fraction(),
        })

    def get_win_rate(self) -> float:
        total = self._win_count + self._loss_count
        if total == 0:
            return 0.0
        return self._win_count / total

    def get_risk_stats(self) -> dict:
        """Get comprehensive risk statistics."""
        return {
            "total_exposure": round(self._total_exposure_usdt, 2),
            "current_balance": round(self._current_balance, 2),
            "peak_balance": round(self._peak_balance, 2),
            "drawdown_pct": round(self.get_drawdown_percent(), 2),
            "daily_loss": round(self._daily_loss, 2),
            "consecutive_losses": self._consecutive_losses,
            "win_count": self._win_count,
            "loss_count": self._loss_count,
            "win_rate": round(self.get_win_rate() * 100, 1),
            "kelly_fraction": round(self._get_kelly_fraction() * 100, 2),
            "killed": self._killed,
        }

    def _get_kelly_fraction(self) -> float:
        total = self._win_count + self._loss_count
        if total < 10:
            return self._config.get("risk.default_position_fraction", 0.02)
        win_rate = self._win_count / total
        if self._total_loss_amount == 0:
            return 0.02
        avg_win = self._total_win_amount / max(self._win_count, 1)
        avg_loss = self._total_loss_amount / max(self._loss_count, 1)
        avg_rr = avg_win / avg_loss if avg_loss > 0 else 1.5
        kelly = win_rate - (1 - win_rate) / avg_rr
        return max(0, min(kelly * 0.5, 0.25))

    # ──────────────────── Daily Reset ────────────────────

    def _reset_daily_if_needed(self) -> None:
        """Reset daily loss counter at midnight."""
        now = time.time()
        # Check if a new day started (simple: every 24h from first call)
        if self._daily_reset_time == 0:
            self._daily_reset_time = now
        elif now - self._daily_reset_time > 86400:
            self._daily_loss = 0.0
            self._daily_reset_time = now
            logger.info("Daily loss counter reset")

    # ──────────────────── Kill Switch ────────────────────

    def activate_kill_switch(self) -> None:
        self._killed = True
        self._event_bus.publish_sync(EventType.KILL_SWITCH, {})
        logger.critical("KILL SWITCH ACTIVATED - all orders blocked")

    def deactivate_kill_switch(self) -> None:
        self._killed = False
        logger.info("Kill switch deactivated")

    def reset_exposure(self) -> None:
        self._total_exposure_usdt = 0.0

    def reset_consecutive_losses(self) -> None:
        """Reset consecutive loss counter (allows trading to resume)."""
        self._consecutive_losses = 0
        logger.info("Consecutive loss counter reset")

    @property
    def is_killed(self) -> bool:
        return self._killed

    @property
    def total_exposure(self) -> float:
        return self._total_exposure_usdt
