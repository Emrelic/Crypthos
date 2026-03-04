"""Position Manager - tracks multiple active positions, trailing stops,
and evaluates 7 different exit signals per position."""
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
    leverage: int = 1
    margin_usdt: float = 0.0
    liquidation_price: float = 0.0


class PositionManager:
    """Manages multiple active positions with 7 exit signals each.

    Supports up to max_positions concurrent positions.
    """

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
        self._positions: dict[str, ActivePosition] = {}  # symbol -> position
        self._max_positions = config.get("scanner.max_positions", 5)

    @property
    def max_positions(self) -> int:
        return self._max_positions

    @property
    def has_capacity(self) -> bool:
        return len(self._positions) < self._max_positions

    @property
    def position_count(self) -> int:
        return len(self._positions)

    @property
    def has_position(self) -> bool:
        return len(self._positions) > 0

    @property
    def position(self) -> ActivePosition:
        """Legacy: return first position (for backward compat)."""
        if self._positions:
            return next(iter(self._positions.values()))
        return None

    def is_holding(self, symbol: str) -> bool:
        return symbol in self._positions

    def get_held_symbols(self) -> list[str]:
        return list(self._positions.keys())

    def open_position(self, symbol: str, side: OrderSide, price: float,
                      size: float, atr: float,
                      leverage: int = 1,
                      margin_usdt: float = 0.0) -> ActivePosition:
        """Create and track a new position."""
        if symbol in self._positions:
            logger.warning(f"Already holding {symbol}, skipping duplicate")
            return self._positions[symbol]

        lev_enabled = leverage > 1

        if lev_enabled:
            # === DYNAMIC CALCULATION FROM LEVERAGE ===
            # All parameters derived scientifically from leverage
            fee_pct = 0.001  # 0.1% round-trip (0.05% entry + 0.05% exit)
            liq_pct = (1.0 / leverage) * 0.85  # effective liquidation distance
            sl_pct = liq_pct * 0.65  # 35% safety margin from liquidation
            tp_pct = max(liq_pct * 0.4 * 0.65, fee_pct * 1.5)  # above fee breakeven

            fee_on_margin = fee_pct * leverage * 100
            tp_roi = tp_pct * leverage * 100 - fee_on_margin
            sl_roi = sl_pct * leverage * 100 + fee_on_margin

            logger.info(f"[{leverage}x] Liq=%{liq_pct*100:.3f} "
                        f"SL=%{sl_pct*100:.3f}(ROI-{sl_roi:.0f}%) "
                        f"TP=%{tp_pct*100:.3f}(ROI+{tp_roi:.0f}%) "
                        f"Fee={fee_on_margin:.0f}%margin")

            if side == OrderSide.BUY_LONG:
                sl = price * (1 - sl_pct)
                tp = price * (1 + tp_pct)
                liq_price = price * (1 - 1.0 / leverage * 0.95)
            else:
                sl = price * (1 + sl_pct)
                tp = price * (1 - tp_pct)
                liq_price = price * (1 + 1.0 / leverage * 0.95)
        else:
            sl_mult = self._config.get("scanner.atr_sl_multiplier", 2.0)
            tp_mult = self._config.get("scanner.atr_tp_multiplier", 4.0)
            if side == OrderSide.BUY_LONG:
                sl = price - atr * sl_mult
                tp = price + atr * tp_mult
            else:
                sl = price + atr * sl_mult
                tp = price - atr * tp_mult
            liq_price = 0.0

        pos = ActivePosition(
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
            leverage=leverage,
            margin_usdt=margin_usdt if margin_usdt > 0 else size * price,
            liquidation_price=liq_price,
        )

        self._positions[symbol] = pos

        self._event_bus.publish(EventType.POSITION_OPENED, {
            "symbol": symbol,
            "side": side.value,
            "price": price,
            "size": size,
            "sl": sl,
            "tp": tp,
            "leverage": leverage,
            "margin_usdt": margin_usdt,
            "position_count": len(self._positions),
        })
        lev_str = f" LEV={leverage}x" if lev_enabled else ""
        logger.info(f"Position opened: {side.value} {size} {symbol} @ {price:.6f} "
                    f"SL={sl:.6f} TP={tp:.6f}{lev_str} "
                    f"[{len(self._positions)}/{self._max_positions}]")
        return pos

    def check_position(self, symbol: str, current_price: float,
                       indicator_values: dict = None,
                       confluence: dict = None, regime: dict = None,
                       divergences: list = None) -> str:
        """Check a single position for exit signals. Returns 'HOLD' or exit reason."""
        if symbol not in self._positions:
            return "HOLD"

        pos = self._positions[symbol]
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
        if self._check_stop_loss(pos, current_price):
            return self.EXIT_SL

        # === 2. TAKE PROFIT ===
        if self._check_take_profit(pos, current_price):
            return self.EXIT_TP

        # === 3. TRAILING STOP ===
        self._update_trailing(pos, current_price)
        if self._check_trailing(pos, current_price):
            return self.EXIT_TRAILING

        # === 4. CONFLUENCE REVERSAL ===
        if self._check_confluence_reversal(pos, confluence):
            return self.EXIT_CONFLUENCE

        # === 5. DIVERGENCE WARNING ===
        if self._check_divergence(pos, current_price, divergences):
            return self.EXIT_DIVERGENCE

        # === 6. REGIME DETERIORATION ===
        if regime:
            self._handle_regime_change(pos, regime)

        # === 7. TIME LIMIT ===
        if self._check_time_limit(pos, current_price):
            return self.EXIT_TIME

        # Publish position update
        self._event_bus.publish(EventType.POSITION_UPDATE, {
            "symbol": pos.symbol,
            "entry_price": pos.entry_price,
            "current_price": current_price,
            "pnl": self._get_pnl(pos, current_price),
            "pnl_pct": self._get_pnl_pct(pos, current_price),
            "trailing_stop": pos.trailing_stop,
            "highest": pos.highest_price,
            "hold_seconds": time.time() - pos.entry_time,
        })

        return "HOLD"

    def close_position(self, symbol: str, exit_price: float, reason: str) -> dict:
        """Close a specific position and return trade result."""
        if symbol not in self._positions:
            return {}

        pos = self._positions[symbol]
        pnl = self._get_pnl(pos, exit_price)
        pnl_pct = self._get_pnl_pct(pos, exit_price)
        hold_duration = time.time() - pos.entry_time
        roi_pct = self._get_margin_roi(pos, exit_price)

        result = {
            "symbol": pos.symbol,
            "side": pos.side.value,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "size": pos.size,
            "pnl_usdt": round(pnl, 4),
            "pnl_percent": round(pnl_pct, 2),
            "roi_percent": round(roi_pct, 2),
            "hold_seconds": round(hold_duration, 0),
            "exit_reason": reason,
            "highest_price": pos.highest_price,
            "lowest_price": pos.lowest_price,
            "leverage": pos.leverage,
            "margin_usdt": pos.margin_usdt,
        }

        self._event_bus.publish(EventType.POSITION_CLOSED, result)
        logger.info(f"Position closed: {pos.symbol} PnL={pnl:+.4f} USDT "
                    f"({pnl_pct:+.2f}%) reason={reason} "
                    f"held={hold_duration:.0f}s "
                    f"[{len(self._positions)-1}/{self._max_positions}]")

        del self._positions[symbol]
        return result

    # ──── Exit Signal Checks ────

    def _check_stop_loss(self, pos: ActivePosition, price: float) -> bool:
        if pos.side == OrderSide.BUY_LONG:
            return price <= pos.initial_sl
        else:
            return price >= pos.initial_sl

    def _check_take_profit(self, pos: ActivePosition, price: float) -> bool:
        if pos.side == OrderSide.BUY_LONG:
            return price >= pos.initial_tp
        else:
            return price <= pos.initial_tp

    def _update_trailing(self, pos: ActivePosition, price: float) -> None:
        if pos.leverage > 1:
            # Dynamic trailing from leverage (same formula as TP)
            lev = pos.leverage
            liq_pct = (1.0 / lev) * 0.85
            tp_pct = max(liq_pct * 0.4 * 0.65, 0.001 * 1.5)
            act_pct = tp_pct * 0.6   # activate at 60% of TP
            trail_pct = tp_pct * 0.3  # trail at 30% of TP

            if pos.side == OrderSide.BUY_LONG:
                profit_pct = (price - pos.entry_price) / pos.entry_price
                if profit_pct >= act_pct:
                    pos.trailing_active = True
                    new_trail = price * (1 - trail_pct)
                    if new_trail > pos.trailing_stop:
                        pos.trailing_stop = new_trail
            else:
                profit_pct = (pos.entry_price - price) / pos.entry_price
                if profit_pct >= act_pct:
                    pos.trailing_active = True
                    new_trail = price * (1 + trail_pct)
                    if new_trail < pos.trailing_stop:
                        pos.trailing_stop = new_trail
        else:
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

    def _check_trailing(self, pos: ActivePosition, price: float) -> bool:
        if not pos.trailing_active:
            return False
        if pos.side == OrderSide.BUY_LONG:
            return price <= pos.trailing_stop
        else:
            return price >= pos.trailing_stop

    def _check_confluence_reversal(self, pos: ActivePosition, confluence: dict) -> bool:
        if not confluence:
            return False
        signal = confluence.get("signal", "NEUTRAL")
        score = confluence.get("score", 0)

        min_hold = self._config.get("scanner.min_hold_time_seconds", 120)
        if time.time() - pos.entry_time < min_hold:
            return False

        if pos.side == OrderSide.BUY_LONG and signal == "SELL" and score <= -4.0:
            return True
        if pos.side == OrderSide.SELL_SHORT and signal == "BUY" and score >= 4.0:
            return True
        return False

    def _check_divergence(self, pos: ActivePosition, price: float,
                          divergences: list) -> bool:
        pnl = self._get_pnl(pos, price)
        if pnl <= 0:
            return False

        for d in divergences:
            div_type = d.get("type", "")
            strength = d.get("strength", 0)
            if strength < 0.2:
                continue
            if pos.side == OrderSide.BUY_LONG and "BEARISH" in div_type:
                return True
            if pos.side == OrderSide.SELL_SHORT and "BULLISH" in div_type:
                return True
        return False

    def _handle_regime_change(self, pos: ActivePosition, regime: dict) -> None:
        regime_name = regime.get("regime", "")
        confidence = regime.get("confidence", 0)

        if regime_name == "VOLATILE" and confidence > 0.6:
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

    def _check_time_limit(self, pos: ActivePosition, price: float) -> bool:
        if pos.leverage > 1:
            max_hold = self._config.get("leverage.max_hold_minutes", 60) * 60
            held = time.time() - pos.entry_time
            return held >= max_hold
        else:
            max_hold = self._config.get("scanner.max_hold_time_seconds", 14400)
            held = time.time() - pos.entry_time
            if held < max_hold:
                return False
            pnl_pct = self._get_pnl_pct(pos, price)
            return pnl_pct < 2.0

    # ──── PnL helpers ────

    def _get_pnl(self, pos: ActivePosition, price: float) -> float:
        """PnL including estimated trading fees (entry + exit)."""
        if pos.side == OrderSide.BUY_LONG:
            raw_pnl = (price - pos.entry_price) * pos.size
        else:
            raw_pnl = (pos.entry_price - price) * pos.size
        # Subtract round-trip fee (0.05% taker each side)
        notional = pos.size * pos.entry_price
        fee = notional * 0.001  # 0.05% entry + 0.05% exit = 0.1%
        return raw_pnl - fee

    def _get_pnl_pct(self, pos: ActivePosition, price: float) -> float:
        if pos.entry_price == 0:
            return 0.0
        if pos.side == OrderSide.BUY_LONG:
            return (price - pos.entry_price) / pos.entry_price * 100
        else:
            return (pos.entry_price - price) / pos.entry_price * 100

    def _get_margin_roi(self, pos: ActivePosition, price: float) -> float:
        if pos.margin_usdt <= 0:
            return self._get_pnl_pct(pos, price)
        pnl = self._get_pnl(pos, price)
        return pnl / pos.margin_usdt * 100

    # ──── Public getters ────

    def get_unrealized_pnl(self, current_price: float) -> float:
        """Legacy: PnL of first position."""
        if not self._positions:
            return 0.0
        pos = next(iter(self._positions.values()))
        return self._get_pnl(pos, current_price)

    def get_pnl_percent(self, current_price: float) -> float:
        if not self._positions:
            return 0.0
        pos = next(iter(self._positions.values()))
        return self._get_pnl_pct(pos, current_price)

    def get_margin_roi_percent(self, current_price: float) -> float:
        if not self._positions:
            return 0.0
        pos = next(iter(self._positions.values()))
        return self._get_margin_roi(pos, current_price)

    def get_total_margin(self) -> float:
        return sum(p.margin_usdt for p in self._positions.values())

    def get_position_info(self) -> dict:
        """Legacy: info of first position."""
        if not self._positions:
            return {}
        pos = next(iter(self._positions.values()))
        return self._pos_info(pos)

    def get_all_positions_info(self) -> list[dict]:
        return [self._pos_info(p) for p in self._positions.values()]

    def _pos_info(self, pos: ActivePosition) -> dict:
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
            "leverage": pos.leverage,
            "margin_usdt": pos.margin_usdt,
            "liquidation_price": pos.liquidation_price,
        }
