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
    emergency_close_price: float = 0.0  # 80% of liq distance — last line of defense
    timeframe: str = "1m"  # chart timeframe for this position's indicators


class PositionManager:
    """Manages multiple active positions with 7 exit signals each.

    Supports up to max_positions concurrent positions.
    """

    # Exit reasons
    EXIT_EMERGENCY = "EMERGENCY_ANTI_LIQ"
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
                      margin_usdt: float = 0.0,
                      timeframe: str = "1m") -> ActivePosition:
        """Create and track a new position."""
        if symbol in self._positions:
            logger.warning(f"Already holding {symbol}, skipping duplicate")
            return self._positions[symbol]

        lev_enabled = leverage > 1

        if lev_enabled:
            # === DYNAMIC CALCULATION FROM LEVERAGE ===
            fee_pct = 0.001  # 0.1% round-trip
            fee_roi = fee_pct * leverage * 100

            # Read from strategy config (with sensible defaults)
            strat = self._config.get("strategy", {})

            # Liquidation: (1/L) with practical factor (maintenance margin eats rest)
            liq_factor = strat.get("liq_factor", 70) / 100.0
            liq_pct = (1.0 / leverage) * liq_factor
            sl_liq_pct = strat.get("sl_liq_percent", 50) / 100.0
            emergency_liq_pct = strat.get("emergency_liq_percent", 80) / 100.0
            tp_liq_mult = strat.get("tp_liq_multiplier", 3.0)

            sl_pct = liq_pct * sl_liq_pct
            emergency_pct = liq_pct * emergency_liq_pct
            tp_pct = liq_pct * tp_liq_mult

            # ROI calculations for logging
            sl_roi = sl_pct * leverage * 100
            emergency_roi = emergency_pct * leverage * 100
            tp_roi = tp_pct * leverage * 100

            logger.info(f"[{leverage}x] Liq={liq_pct*100:.3f}% "
                        f"SL={sl_pct*100:.3f}%(ROI-{sl_roi:.0f}%) "
                        f"Emergency={emergency_pct*100:.3f}%(ROI-{emergency_roi:.0f}%) "
                        f"Fee={fee_roi:.0f}%margin")

            if side == OrderSide.BUY_LONG:
                sl = price * (1 - sl_pct)
                tp = price * (1 + tp_pct)
                liq_price = price * (1 - 1.0 / leverage * 0.95)
                emergency_price = price * (1 - emergency_pct)
            else:
                sl = price * (1 + sl_pct)
                tp = price * (1 - tp_pct)
                liq_price = price * (1 + 1.0 / leverage * 0.95)
                emergency_price = price * (1 + emergency_pct)
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
            emergency_price = 0.0

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
            emergency_close_price=emergency_price if lev_enabled else 0.0,
            timeframe=timeframe,
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

        # === SAVAS MODU (Battle Mode) ===
        battle_mode = self._config.get("scanner.battle_mode", False)
        if battle_mode and pos.leverage > 1:
            return self._check_battle_mode(pos, current_price, confluence, divergences)

        strat = self._config.get("strategy", {})

        # === 0. EMERGENCY ANTI-LIQUIDATION (highest priority) ===
        if strat.get("emergency_enabled", True):
            if self._check_emergency_close(pos, current_price):
                return self.EXIT_EMERGENCY

        # === 1. HARD STOP LOSS ===
        if strat.get("sl_enabled", True):
            if self._check_stop_loss(pos, current_price):
                return self.EXIT_SL

        # === 2. TAKE PROFIT ===
        if strat.get("tp_enabled", True):
            if self._check_take_profit(pos, current_price):
                return self.EXIT_TP

        # === 3. TRAILING STOP ===
        if strat.get("trailing_enabled", True):
            self._update_trailing(pos, current_price)
            if self._check_trailing(pos, current_price):
                return self.EXIT_TRAILING

        # === 4. CONFLUENCE REVERSAL ===
        if strat.get("signal_exit_enabled", True):
            if self._check_confluence_reversal(pos, confluence, current_price):
                return self.EXIT_CONFLUENCE

        # === 5. DIVERGENCE WARNING ===
        if strat.get("divergence_exit_enabled", True):
            if self._check_divergence(pos, current_price, divergences):
                return self.EXIT_DIVERGENCE

        # === 6. REGIME DETERIORATION ===
        if regime:
            self._handle_regime_change(pos, regime)

        # === 7. TIME LIMIT ===
        if strat.get("time_limit_enabled", True):
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

    # ──── SAVAS MODU (Battle Mode) ────

    def _check_battle_mode(self, pos: ActivePosition, price: float,
                           confluence: dict, divergences: list) -> str:
        """SAVAS MODU - Fight to the last drop.

        Rules:
        - ALWAYS: Emergency close at 80% liq distance (survive)
        - Below fee breakeven (<10% ROI): ONLY emergency close, nothing else
        - Above fee but <50% ROI: sell only if signals strongly reversed
        - Above 50% ROI: sell if signals reversed, otherwise HOLD
        - Trailing: very wide (4x fee distance), lets profits run to 200-300%+
        - NO time limit, NO take profit, NO divergence exit below fee
        - NO SL exit (server SL handles that, we just track)
        """
        lev = pos.leverage
        fee_roi = 0.001 * lev * 100  # fee as % of margin

        if pos.side == OrderSide.BUY_LONG:
            roi = (price - pos.entry_price) / pos.entry_price * lev * 100
        else:
            roi = (pos.entry_price - price) / pos.entry_price * lev * 100

        net_roi = roi - fee_roi  # ROI after fees

        # === EMERGENCY: Always active - survive liquidation ===
        if self._check_emergency_close(pos, price):
            return self.EXIT_EMERGENCY

        # === BELOW FEE BREAKEVEN: Fight mode - only emergency closes ===
        if net_roi < 0:
            # We're losing money. Only emergency close saves us.
            # No SL, no trailing, no confluence exit. HOLD and pray.
            return "HOLD"

        # === ABOVE FEE BREAKEVEN: Smart exit mode ===

        # Update trailing with WIDE distance (4x fee = very patient)
        self._update_battle_trailing(pos, price)

        # Above 50% ROI: only sell if signals clearly reversed OR trailing hit
        if net_roi >= 50:
            # Check trailing first
            if pos.trailing_active and self._check_battle_trailing(pos, price):
                logger.info(f"[SAVAS] {pos.symbol} trailing triggered @ ROI {roi:.1f}% "
                            f"(net {net_roi:.1f}%). Taking profit.")
                return self.EXIT_TRAILING

            # Check strong reversal signal
            if confluence:
                conf_score = confluence.get("score", 0)
                if pos.side == OrderSide.BUY_LONG and conf_score <= -6.0:
                    logger.info(f"[SAVAS] {pos.symbol} strong reversal (conf={conf_score}) "
                                f"@ ROI {roi:.1f}%. Exiting with profit.")
                    return self.EXIT_CONFLUENCE
                if pos.side == OrderSide.SELL_SHORT and conf_score >= 6.0:
                    logger.info(f"[SAVAS] {pos.symbol} strong reversal (conf={conf_score}) "
                                f"@ ROI {roi:.1f}%. Exiting with profit.")
                    return self.EXIT_CONFLUENCE

            return "HOLD"  # In profit, signals OK, STAY

        # Between fee breakeven and 50% ROI
        # Sell if signals strongly reversed (protect small profit)
        if confluence:
            conf_score = confluence.get("score", 0)
            if pos.side == OrderSide.BUY_LONG and conf_score <= -5.0:
                logger.info(f"[SAVAS] {pos.symbol} reversal detected (conf={conf_score}) "
                            f"@ ROI {roi:.1f}% (net {net_roi:.1f}%). Securing profit.")
                return self.EXIT_CONFLUENCE
            if pos.side == OrderSide.SELL_SHORT and conf_score >= 5.0:
                logger.info(f"[SAVAS] {pos.symbol} reversal detected (conf={conf_score}) "
                            f"@ ROI {roi:.1f}% (net {net_roi:.1f}%). Securing profit.")
                return self.EXIT_CONFLUENCE

        # Check trailing
        if pos.trailing_active and self._check_battle_trailing(pos, price):
            logger.info(f"[SAVAS] {pos.symbol} trailing triggered @ ROI {roi:.1f}%")
            return self.EXIT_TRAILING

        return "HOLD"

    def _update_battle_trailing(self, pos: ActivePosition, price: float) -> None:
        """Battle mode trailing: very wide distance, lets profits run."""
        lev = pos.leverage
        fee_roi = 0.001 * lev * 100
        strat = self._config.get("strategy", {})
        trailing_mode = strat.get("trailing_mode", "roi")

        if trailing_mode == "atr" and pos.atr_at_entry > 0:
            atr = pos.atr_at_entry
            activate_mult = strat.get("trailing_atr_activate_mult", 4.0)
            distance_mult = strat.get("trailing_atr_distance_mult", 1.0)
            activate_price = atr * activate_mult
            trail_distance = atr * distance_mult

            if pos.side == OrderSide.BUY_LONG:
                profit = price - pos.entry_price
                if profit >= activate_price:
                    if not pos.trailing_active:
                        logger.info(f"[SAVAS] {pos.symbol} ATR trailing activated "
                                    f"@ {profit/pos.entry_price*100:.3f}% move")
                    pos.trailing_active = True
                    new_trail = price - trail_distance
                    if new_trail > pos.trailing_stop:
                        pos.trailing_stop = new_trail
            else:
                profit = pos.entry_price - price
                if profit >= activate_price:
                    if not pos.trailing_active:
                        logger.info(f"[SAVAS] {pos.symbol} ATR trailing activated "
                                    f"@ {profit/pos.entry_price*100:.3f}% move")
                    pos.trailing_active = True
                    new_trail = price + trail_distance
                    if new_trail < pos.trailing_stop:
                        pos.trailing_stop = new_trail
        else:
            direct_activate = strat.get("trailing_activate_roi", 0)
            direct_distance = strat.get("trailing_distance_roi", 0)
            if direct_activate > 0 and direct_distance > 0:
                activate_roi = direct_activate
                trail_roi = direct_distance
            else:
                activate_roi = fee_roi * 2.0
                trail_roi = fee_roi * 4.0

            trail_price_pct = trail_roi / (lev * 100)

            if pos.side == OrderSide.BUY_LONG:
                roi = (price - pos.entry_price) / pos.entry_price * lev * 100
                if roi >= activate_roi:
                    if not pos.trailing_active:
                        logger.info(f"[SAVAS] {pos.symbol} trailing activated @ ROI {roi:.1f}%")
                    pos.trailing_active = True
                    new_trail = price * (1 - trail_price_pct)
                    if new_trail > pos.trailing_stop:
                        pos.trailing_stop = new_trail
            else:
                roi = (pos.entry_price - price) / pos.entry_price * lev * 100
                if roi >= activate_roi:
                    if not pos.trailing_active:
                        logger.info(f"[SAVAS] {pos.symbol} trailing activated @ ROI {roi:.1f}%")
                    pos.trailing_active = True
                    new_trail = price * (1 + trail_price_pct)
                    if new_trail < pos.trailing_stop:
                        pos.trailing_stop = new_trail

    def _check_battle_trailing(self, pos: ActivePosition, price: float) -> bool:
        """Check if battle mode trailing stop is hit."""
        if not pos.trailing_active:
            return False
        if pos.side == OrderSide.BUY_LONG:
            return price <= pos.trailing_stop
        else:
            return price >= pos.trailing_stop

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

    def _check_emergency_close(self, pos: ActivePosition, price: float) -> bool:
        """EMERGENCY: Close before Binance liquidates us.
        Triggers at 80% of liquidation distance. Last line of defense.
        Better to lose 80% of margin than 100% from liquidation."""
        if pos.emergency_close_price <= 0:
            return False
        if pos.side == OrderSide.BUY_LONG:
            if price <= pos.emergency_close_price:
                logger.warning(
                    f"[EMERGENCY] {pos.symbol} price {price:.6f} hit "
                    f"emergency level {pos.emergency_close_price:.6f} "
                    f"(liq={pos.liquidation_price:.6f}). Closing NOW!")
                return True
        else:
            if price >= pos.emergency_close_price:
                logger.warning(
                    f"[EMERGENCY] {pos.symbol} price {price:.6f} hit "
                    f"emergency level {pos.emergency_close_price:.6f} "
                    f"(liq={pos.liquidation_price:.6f}). Closing NOW!")
                return True
        return False

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
            lev = pos.leverage
            fee_roi = 0.001 * lev * 100
            strat = self._config.get("strategy", {})
            trailing_mode = strat.get("trailing_mode", "roi")

            if trailing_mode == "atr" and pos.atr_at_entry > 0:
                # ATR-based trailing: activate at N*ATR profit, trail at M*ATR distance
                atr = pos.atr_at_entry
                activate_mult = strat.get("trailing_atr_activate_mult", 4.0)
                distance_mult = strat.get("trailing_atr_distance_mult", 1.0)
                activate_price = atr * activate_mult
                trail_distance = atr * distance_mult

                if pos.side == OrderSide.BUY_LONG:
                    profit = price - pos.entry_price
                    if profit >= activate_price:
                        if not pos.trailing_active:
                            roi = profit / pos.entry_price * lev * 100
                            logger.info(f"[{pos.symbol}] ATR trailing activated at "
                                        f"{profit/pos.entry_price*100:.3f}% move "
                                        f"(ROI {roi:.1f}%, {activate_mult}x ATR)")
                        pos.trailing_active = True
                        new_trail = price - trail_distance
                        if new_trail > pos.trailing_stop:
                            pos.trailing_stop = new_trail
                else:
                    profit = pos.entry_price - price
                    if profit >= activate_price:
                        if not pos.trailing_active:
                            roi = profit / pos.entry_price * lev * 100
                            logger.info(f"[{pos.symbol}] ATR trailing activated at "
                                        f"{profit/pos.entry_price*100:.3f}% move "
                                        f"(ROI {roi:.1f}%, {activate_mult}x ATR)")
                        pos.trailing_active = True
                        new_trail = price + trail_distance
                        if new_trail < pos.trailing_stop:
                            pos.trailing_stop = new_trail
            else:
                # ROI-based trailing stop
                direct_activate = strat.get("trailing_activate_roi", 0)
                direct_distance = strat.get("trailing_distance_roi", 0)
                if direct_activate > 0 and direct_distance > 0:
                    activate_roi = direct_activate
                    trail_roi = direct_distance
                else:
                    activate_mult = strat.get("trailing_activate_fee_mult", 3.0)
                    distance_mult = strat.get("trailing_distance_fee_mult", 2.0)
                    activate_roi = fee_roi * activate_mult
                    trail_roi = fee_roi * distance_mult

                trail_price_pct = trail_roi / (lev * 100)

                if pos.side == OrderSide.BUY_LONG:
                    roi = (price - pos.entry_price) / pos.entry_price * lev * 100
                    if roi >= activate_roi:
                        if not pos.trailing_active:
                            logger.info(f"[{pos.symbol}] Trailing activated at ROI "
                                        f"{roi:.1f}% (net {roi - fee_roi:.1f}%)")
                        pos.trailing_active = True
                        new_trail = price * (1 - trail_price_pct)
                        if new_trail > pos.trailing_stop:
                            pos.trailing_stop = new_trail
                else:
                    roi = (pos.entry_price - price) / pos.entry_price * lev * 100
                    if roi >= activate_roi:
                        if not pos.trailing_active:
                            logger.info(f"[{pos.symbol}] Trailing activated at ROI "
                                        f"{roi:.1f}% (net {roi - fee_roi:.1f}%)")
                        pos.trailing_active = True
                        new_trail = price * (1 + trail_price_pct)
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

        triggered = False
        if pos.side == OrderSide.BUY_LONG:
            triggered = price <= pos.trailing_stop
        else:
            triggered = price >= pos.trailing_stop

        if triggered and pos.leverage > 1:
            # Don't close unless net profit (after fees) is positive
            lev = pos.leverage
            fee_roi = 0.001 * lev * 100
            if pos.side == OrderSide.BUY_LONG:
                roi = (price - pos.entry_price) / pos.entry_price * lev * 100
            else:
                roi = (pos.entry_price - price) / pos.entry_price * lev * 100
            net_roi = roi - fee_roi
            if net_roi < fee_roi * 0.5:
                # Net profit too small, deactivate trailing and wait for better exit
                logger.debug(f"[{pos.symbol}] Trailing triggered but net ROI {net_roi:.1f}% "
                             f"< min {fee_roi * 0.5:.1f}%, holding")
                pos.trailing_active = False
                pos.trailing_stop = pos.initial_sl  # reset to SL
                return False

        return triggered

    def _check_confluence_reversal(self, pos: ActivePosition, confluence: dict,
                                    current_price: float = 0) -> bool:
        if not confluence:
            return False
        signal = confluence.get("signal", "NEUTRAL")
        score = confluence.get("score", 0)

        strat = self._config.get("strategy", {})
        min_hold = strat.get("signal_min_hold_seconds", 30) if pos.leverage > 1 \
            else self._config.get("scanner.min_hold_time_seconds", 120)
        if time.time() - pos.entry_time < min_hold:
            return False

        # For leverage: only exit on reversal if we're in profit
        only_profit = strat.get("signal_only_in_profit", True)
        if only_profit and pos.leverage > 1 and current_price > 0:
            fee_roi = 0.001 * pos.leverage * 100
            if pos.side == OrderSide.BUY_LONG:
                roi = (current_price - pos.entry_price) / pos.entry_price * pos.leverage * 100
            else:
                roi = (pos.entry_price - current_price) / pos.entry_price * pos.leverage * 100
            if roi < fee_roi:
                return False

        threshold = strat.get("signal_exit_threshold", 4.0)
        if pos.side == OrderSide.BUY_LONG and signal == "SELL" and score <= -threshold:
            return True
        if pos.side == OrderSide.SELL_SHORT and signal == "BUY" and score >= threshold:
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
        strat = self._config.get("strategy", {})
        if pos.leverage > 1:
            max_hold = strat.get("time_limit_minutes", 480) * 60
            held = time.time() - pos.entry_time
            if held >= max_hold:
                # If trailing is active and config says extend, trust trailing
                if pos.trailing_active and strat.get("time_limit_extend_trailing", True):
                    logger.debug(f"{pos.symbol} time limit hit but trailing active, "
                                 f"letting trailing handle exit")
                    return False
                # If near breakeven and config says extend, give 2x more time
                if strat.get("time_limit_extend_breakeven", True):
                    lev = pos.leverage
                    fee_roi = 0.001 * lev * 100
                    if pos.side == OrderSide.BUY_LONG:
                        roi = (price - pos.entry_price) / pos.entry_price * lev * 100
                    else:
                        roi = (pos.entry_price - price) / pos.entry_price * lev * 100
                    if roi > -fee_roi and held < max_hold * 2:
                        return False
                return True
            return False
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
            "emergency_price": pos.emergency_close_price,
            "timeframe": pos.timeframe,
        }
