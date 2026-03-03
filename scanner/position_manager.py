"""Position Manager - tracks active position lifecycle, trailing stops,
and evaluates 7 different exit signals."""
import time
from dataclasses import dataclass
from loguru import logger
from core.config_manager import ConfigManager
from core.event_bus import EventBus
from core.constants import EventType, OrderSide


@dataclass
class ActivePosition:
    """Tracks an open position."""
    symbol: str
    side: OrderSide
    entry_price: float
    entry_time: float
    size: float
    notional_usdt: float
    atr_at_entry: float
    initial_sl: float
    initial_tp: float
    trailing_stop: float
    highest_price: float      # for long trailing
    lowest_price: float       # for short trailing
    trailing_active: bool = False


class PositionManager:
    """Manages the lifecycle of an active position with 7 exit signals."""

    # Exit reasons
    EXIT_SL = "STOP_LOSS"
    EXIT_TP = "TAKE_PROFIT"
    EXIT_TRAILING = "TRAILING_STOP"
    EXIT_CONFLUENCE = "CONFLUENCE_REVERSAL"
    EXIT_DIVERGENCE = "DIVERGENCE_WARNING"
    EXIT_REGIME = "REGIME_DETERIORATION"
    EXIT_TIME = "TIME_LIMIT"

    def __init__(self, config: ConfigManager, event_bus: EventBus):
        self._config = config
        self._event_bus = event_bus
        self._position: ActivePosition = None

    def open_position(self, symbol: str, side: OrderSide, price: float,
                      size: float, atr: float) -> ActivePosition:
        """Create and track a new position."""
        sl_mult = self._config.get("scanner.atr_sl_multiplier", 2.0)
        tp_mult = self._config.get("scanner.atr_tp_multiplier", 4.0)

        if side == OrderSide.BUY_LONG:
            sl = price - atr * sl_mult
            tp = price + atr * tp_mult
        else:
            sl = price + atr * sl_mult
            tp = price - atr * tp_mult

        self._position = ActivePosition(
            symbol=symbol,
            side=side,
            entry_price=price,
            entry_time=time.time(),
            size=size,
            notional_usdt=size * price,
            atr_at_entry=atr,
            initial_sl=sl,
            initial_tp=tp,
            trailing_stop=sl,
            highest_price=price,
            lowest_price=price,
        )

        self._event_bus.publish(EventType.POSITION_OPENED, {
            "symbol": symbol,
            "side": side.value,
            "price": price,
            "size": size,
            "sl": sl,
            "tp": tp,
        })
        logger.info(f"Position opened: {side.value} {size} {symbol} @ {price:.6f} "
                    f"SL={sl:.6f} TP={tp:.6f}")
        return self._position

    def update(self, current_price: float, indicator_values: dict = None,
               confluence: dict = None, regime: dict = None,
               divergences: list = None) -> str:
        """Evaluate all exit signals. Returns 'HOLD' or exit reason.

        Called every evaluation cycle during HOLDING state.
        """
        if not self._position:
            return "HOLD"

        pos = self._position
        indicator_values = indicator_values or {}
        confluence = confluence or {}
        divergences = divergences or []

        # Update price tracking
        if pos.side == OrderSide.BUY_LONG:
            if current_price > pos.highest_price:
                pos.highest_price = current_price
        else:
            if current_price < pos.lowest_price:
                pos.lowest_price = current_price

        # === 1. HARD STOP LOSS ===
        if self._check_stop_loss(current_price):
            return self.EXIT_SL

        # === 2. TAKE PROFIT ===
        if self._check_take_profit(current_price):
            return self.EXIT_TP

        # === 3. TRAILING STOP ===
        self._update_trailing(current_price)
        if self._check_trailing(current_price):
            return self.EXIT_TRAILING

        # === 4. CONFLUENCE REVERSAL ===
        if self._check_confluence_reversal(confluence):
            return self.EXIT_CONFLUENCE

        # === 5. DIVERGENCE WARNING ===
        if self._check_divergence(current_price, divergences):
            return self.EXIT_DIVERGENCE

        # === 6. REGIME DETERIORATION ===
        if regime:
            self._handle_regime_change(regime)

        # === 7. TIME LIMIT ===
        if self._check_time_limit(current_price):
            return self.EXIT_TIME

        # Publish position update
        self._event_bus.publish(EventType.POSITION_UPDATE, {
            "symbol": pos.symbol,
            "entry_price": pos.entry_price,
            "current_price": current_price,
            "pnl": self.get_unrealized_pnl(current_price),
            "pnl_pct": self.get_pnl_percent(current_price),
            "trailing_stop": pos.trailing_stop,
            "highest": pos.highest_price,
            "hold_seconds": time.time() - pos.entry_time,
        })

        return "HOLD"

    def close_position(self, exit_price: float, reason: str) -> dict:
        """Close the active position and return trade result."""
        if not self._position:
            return {}

        pos = self._position
        pnl = self.get_unrealized_pnl(exit_price)
        pnl_pct = self.get_pnl_percent(exit_price)
        hold_duration = time.time() - pos.entry_time

        result = {
            "symbol": pos.symbol,
            "side": pos.side.value,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "size": pos.size,
            "pnl_usdt": round(pnl, 4),
            "pnl_percent": round(pnl_pct, 2),
            "hold_seconds": round(hold_duration, 0),
            "exit_reason": reason,
            "highest_price": pos.highest_price,
            "lowest_price": pos.lowest_price,
        }

        self._event_bus.publish(EventType.POSITION_CLOSED, result)
        logger.info(f"Position closed: {pos.symbol} PnL={pnl:+.4f} USDT "
                    f"({pnl_pct:+.2f}%) reason={reason} "
                    f"held={hold_duration:.0f}s")

        self._position = None
        return result

    # ──── Exit Signal Checks ────

    def _check_stop_loss(self, price: float) -> bool:
        pos = self._position
        if pos.side == OrderSide.BUY_LONG:
            return price <= pos.initial_sl
        else:
            return price >= pos.initial_sl

    def _check_take_profit(self, price: float) -> bool:
        pos = self._position
        if pos.side == OrderSide.BUY_LONG:
            return price >= pos.initial_tp
        else:
            return price <= pos.initial_tp

    def _update_trailing(self, price: float) -> None:
        """Update trailing stop when position is in profit."""
        pos = self._position
        atr = pos.atr_at_entry
        activation_mult = self._config.get("scanner.trailing_activation_atr", 1.0)
        trail_mult = self._config.get("scanner.trailing_atr_multiplier", 1.5)

        if pos.side == OrderSide.BUY_LONG:
            profit = price - pos.entry_price
            if profit >= atr * activation_mult:
                pos.trailing_active = True
                new_trail = price - atr * trail_mult
                if new_trail > pos.trailing_stop:
                    pos.trailing_stop = new_trail
        else:
            profit = pos.entry_price - price
            if profit >= atr * activation_mult:
                pos.trailing_active = True
                new_trail = price + atr * trail_mult
                if new_trail < pos.trailing_stop:
                    pos.trailing_stop = new_trail

    def _check_trailing(self, price: float) -> bool:
        pos = self._position
        if not pos.trailing_active:
            return False
        if pos.side == OrderSide.BUY_LONG:
            return price <= pos.trailing_stop
        else:
            return price >= pos.trailing_stop

    def _check_confluence_reversal(self, confluence: dict) -> bool:
        if not confluence:
            return False
        pos = self._position
        signal = confluence.get("signal", "NEUTRAL")
        score = confluence.get("score", 0)

        # Must have been held for minimum time
        min_hold = self._config.get("scanner.min_hold_time_seconds", 120)
        if time.time() - pos.entry_time < min_hold:
            return False

        if pos.side == OrderSide.BUY_LONG and signal == "SELL" and score <= -4.0:
            return True
        if pos.side == OrderSide.SELL_SHORT and signal == "BUY" and score >= 4.0:
            return True
        return False

    def _check_divergence(self, price: float, divergences: list) -> bool:
        """Only trigger on divergence if we're in profit (lock in gains)."""
        pos = self._position
        pnl = self.get_unrealized_pnl(price)
        if pnl <= 0:
            return False  # Only when in profit

        for d in divergences:
            div_type = d.get("type", "")
            strength = d.get("strength", 0)
            if strength < 0.2:
                continue  # Weak divergence, ignore
            if pos.side == OrderSide.BUY_LONG and "BEARISH" in div_type:
                return True
            if pos.side == OrderSide.SELL_SHORT and "BULLISH" in div_type:
                return True
        return False

    def _handle_regime_change(self, regime: dict) -> None:
        """Tighten trailing stop when regime deteriorates."""
        pos = self._position
        regime_name = regime.get("regime", "")
        confidence = regime.get("confidence", 0)

        if regime_name == "VOLATILE" and confidence > 0.6:
            # Tighten trailing to 1.0 ATR
            atr = pos.atr_at_entry
            if pos.side == OrderSide.BUY_LONG:
                tight_trail = pos.highest_price - atr * 1.0
                if tight_trail > pos.trailing_stop:
                    pos.trailing_stop = tight_trail
                    pos.trailing_active = True
            else:
                tight_trail = pos.lowest_price + atr * 1.0
                if tight_trail < pos.trailing_stop:
                    pos.trailing_stop = tight_trail
                    pos.trailing_active = True

    def _check_time_limit(self, price: float) -> bool:
        pos = self._position
        max_hold = self._config.get("scanner.max_hold_time_seconds", 14400)
        held = time.time() - pos.entry_time
        if held < max_hold:
            return False

        # Only close if not significantly in profit
        pnl_pct = self.get_pnl_percent(price)
        return pnl_pct < 2.0  # Close if under 2% profit after max time

    # ──── Getters ────

    def get_unrealized_pnl(self, current_price: float) -> float:
        if not self._position:
            return 0.0
        pos = self._position
        if pos.side == OrderSide.BUY_LONG:
            return (current_price - pos.entry_price) * pos.size
        else:
            return (pos.entry_price - current_price) * pos.size

    def get_pnl_percent(self, current_price: float) -> float:
        if not self._position or self._position.entry_price == 0:
            return 0.0
        pos = self._position
        if pos.side == OrderSide.BUY_LONG:
            return (current_price - pos.entry_price) / pos.entry_price * 100
        else:
            return (pos.entry_price - current_price) / pos.entry_price * 100

    @property
    def has_position(self) -> bool:
        return self._position is not None

    @property
    def position(self) -> ActivePosition:
        return self._position

    def get_position_info(self) -> dict:
        if not self._position:
            return {}
        pos = self._position
        return {
            "symbol": pos.symbol,
            "side": pos.side.value,
            "entry_price": pos.entry_price,
            "size": pos.size,
            "sl": pos.initial_sl,
            "tp": pos.initial_tp,
            "trailing": pos.trailing_stop,
            "trailing_active": pos.trailing_active,
            "highest": pos.highest_price,
            "lowest": pos.lowest_price,
            "hold_seconds": time.time() - pos.entry_time,
        }
