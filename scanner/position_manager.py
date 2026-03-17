"""Position Manager - tracks multiple active positions, trailing stops,
and evaluates 7 different exit signals per position."""
import time
import threading
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
    current_price: float = 0.0     # last known price (updated by check_position)
    entry_score: float = 0.0       # composite score at entry
    entry_confluence: float = 0.0  # confluence score at entry
    entry_adx: float = 0.0        # ADX at entry
    entry_rsi: float = 50.0       # RSI at entry
    # Hybrid trailing: virtual entry for renewed trailing
    virtual_entry_price: float = 0.0   # reset point for ATR trailing (0 = use real entry)
    trailing_renewal_count: int = 0    # how many times trailing was renewed
    # Market regime at entry
    entry_regime: str = ""             # TRENDING, RANGING, GRAY, VOLATILE, BREAKOUT
    entry_regime_confidence: float = 0.0
    entry_bb_width: float = 0.0
    # Partial take profit tracking
    partial_tp_taken: bool = False     # whether partial TP has been executed
    original_size: float = 0.0         # size before partial close
    # Mean Reversion mode
    entry_mode: str = "TREND"          # "TREND" or "MEAN_REVERSION"
    regime_switched: bool = False       # MR->TREND transition happened
    mr_tp_price: float = 0.0           # BB middle TP target (MR mode only)


class PositionManager:
    """Manages multiple active positions with 7 exit signals each.

    Supports up to max_positions concurrent positions.
    """

    # Exit reasons
    EXIT_EMERGENCY = "EMERGENCY_ANTI_LIQ"
    EXIT_SL = "STOP_LOSS"
    EXIT_TP = "TAKE_PROFIT"
    EXIT_TRAILING = "TRAILING_STOP"
    EXIT_TRAILING_RENEW = "TRAILING_RENEW"  # trailing hit but signal strong → needs re-eval
    EXIT_CONFLUENCE = "CONFLUENCE_REVERSAL"
    EXIT_DIVERGENCE = "DIVERGENCE_WARNING"
    EXIT_REGIME = "REGIME_DETERIORATION"
    EXIT_TIME = "TIME_LIMIT"
    EXIT_PARTIAL_TP = "PARTIAL_TP"
    EXIT_MR_TP = "MR_BB_MIDDLE_TP"
    EXIT_MR_REGIME_SWITCH = "MR_REGIME_SWITCH"  # informational, position stays open

    def __init__(self, config: ConfigManager, event_bus: EventBus):
        self._config = config
        self._event_bus = event_bus
        self._lock = threading.RLock()  # Thread safety for _positions dict
        self._positions: dict[str, ActivePosition] = {}  # symbol -> position
        self._max_positions = config.get("strategy.max_positions", 6)

    @property
    def max_positions(self) -> int:
        return self._max_positions

    @property
    def has_capacity(self) -> bool:
        with self._lock:
            return len(self._positions) < self._max_positions

    @property
    def position_count(self) -> int:
        with self._lock:
            return len(self._positions)

    @property
    def has_position(self) -> bool:
        with self._lock:
            return len(self._positions) > 0

    @property
    def position(self) -> ActivePosition:
        """Legacy: return first position (for backward compat)."""
        with self._lock:
            if self._positions:
                return next(iter(self._positions.values()))
            return None

    def is_holding(self, symbol: str) -> bool:
        with self._lock:
            return symbol in self._positions

    def get_held_symbols(self) -> list[str]:
        with self._lock:
            return list(self._positions.keys())

    def get_position(self, symbol: str) -> ActivePosition:
        with self._lock:
            return self._positions.get(symbol)

    def get_all_positions(self) -> dict[str, 'ActivePosition']:
        """Return a snapshot copy of all positions. Thread-safe."""
        with self._lock:
            return dict(self._positions)

    def update_position_size(self, symbol: str, new_size: float) -> None:
        """Update position size after partial close."""
        with self._lock:
            if symbol in self._positions:
                self._positions[symbol].size = new_size

    def get_direction_counts(self) -> tuple[int, int]:
        """Return (long_count, short_count) of currently open positions."""
        with self._lock:
            longs = sum(1 for p in self._positions.values()
                        if p.side == OrderSide.BUY_LONG)
            shorts = sum(1 for p in self._positions.values()
                         if p.side == OrderSide.SELL_SHORT)
            return longs, shorts

    def get_mr_position_count(self) -> int:
        """Return count of Mean Reversion positions (not yet switched to TREND)."""
        with self._lock:
            return sum(1 for p in self._positions.values()
                       if p.entry_mode == "MEAN_REVERSION" and not p.regime_switched)

    def open_position(self, symbol: str, side: OrderSide, price: float,
                      size: float, atr: float,
                      leverage: int = 1,
                      margin_usdt: float = 0.0,
                      timeframe: str = "1m",
                      entry_score: float = 0.0,
                      entry_confluence: float = 0.0,
                      entry_adx: float = 0.0,
                      entry_rsi: float = 50.0,
                      entry_regime: str = "",
                      entry_regime_confidence: float = 0.0,
                      entry_bb_width: float = 0.0,
                      entry_mode: str = "TREND",
                      mr_tp_price: float = 0.0) -> ActivePosition:
        """Create and track a new position."""
        with self._lock:
            return self._open_position_locked(
                symbol, side, price, size, atr, leverage, margin_usdt,
                timeframe, entry_score, entry_confluence, entry_adx, entry_rsi,
                entry_regime, entry_regime_confidence, entry_bb_width,
                entry_mode, mr_tp_price)

    def _open_position_locked(self, symbol: str, side: OrderSide, price: float,
                              size: float, atr: float,
                              leverage: int = 1,
                              margin_usdt: float = 0.0,
                              timeframe: str = "1m",
                              entry_score: float = 0.0,
                              entry_confluence: float = 0.0,
                              entry_adx: float = 0.0,
                              entry_rsi: float = 50.0,
                              entry_regime: str = "",
                              entry_regime_confidence: float = 0.0,
                              entry_bb_width: float = 0.0,
                              entry_mode: str = "TREND",
                              mr_tp_price: float = 0.0) -> ActivePosition:
        """Internal: create position (caller must hold lock)."""
        if symbol in self._positions:
            logger.warning(f"Already holding {symbol}, skipping duplicate")
            return self._positions[symbol]

        lev_enabled = leverage >= 1  # All leverages use fee-aware SL (including 1x)

        # Read from strategy config (with sensible defaults)
        strat = self._config.get("strategy", {})

        # === DYNAMIC CALCULATION FROM LEVERAGE (FEE-AWARE) ===
        fee_pct = strat.get("fee_pct", 0.10) / 100.0  # config: 0.10 = %0.10 round-trip
        fee_roi = fee_pct * leverage * 100  # fee as % of margin
        slippage_mult = strat.get("slippage_mult", 0.5)
        slippage_roi = fee_roi * slippage_mult  # estimated slippage

        # Liquidation: (1/L) with practical factor (maintenance margin eats rest)
        liq_factor = strat.get("liq_factor", 70) / 100.0
        liq_pct = (1.0 / leverage) * liq_factor
        sl_liq_pct = strat.get("sl_liq_percent", 50) / 100.0
        emergency_liq_pct = strat.get("emergency_liq_percent", 80) / 100.0
        tp_liq_mult = strat.get("tp_liq_multiplier", 3.0)

        # Fee-aware SL: gerçek kayıp = SL fiyat hareketi + fee + slippage
        # Hedef toplam kayıp ROI'den fee ve slippage düşülür
        raw_sl_roi = liq_pct * sl_liq_pct * leverage * 100
        net_sl_roi = max(raw_sl_roi - fee_roi - slippage_roi, fee_roi)
        sl_pct = net_sl_roi / (leverage * 100)

        emergency_pct = liq_pct * emergency_liq_pct

        # TP: 3×ATR bazlı (config: tp_atr_mult, fallback: tp_liq_multiplier)
        tp_atr_mult = strat.get("tp_atr_mult", 3.0)
        if atr > 0 and price > 0:
            tp_pct = (atr * tp_atr_mult) / price
        else:
            tp_pct = liq_pct * tp_liq_mult

        # ROI calculations for logging
        sl_roi = net_sl_roi
        total_sl_roi = raw_sl_roi  # fee dahil toplam kayıp
        emergency_roi = emergency_pct * leverage * 100
        tp_roi = tp_pct * leverage * 100

        logger.info(f"[{leverage}x] Liq={liq_pct*100:.3f}% "
                    f"SL={sl_pct*100:.3f}%(ROI-{sl_roi:.0f}%) "
                    f"fee={fee_roi:.0f}%+slip={slippage_roi:.0f}% "
                    f"toplam_kayip={total_sl_roi:.0f}% "
                    f"TP={tp_atr_mult}xATR({tp_pct*100:.3f}%, ROI={tp_roi:.0f}%) "
                    f"Emergency={emergency_pct*100:.3f}%(ROI-{emergency_roi:.0f}%)")

        if side == OrderSide.BUY_LONG:
            sl = price * (1 - sl_pct)
            tp = price * (1 + tp_pct)
            liq_price = price * (1 - 1.0 / leverage * 0.95) if leverage > 1 else 0.0
            emergency_price = price * (1 - emergency_pct)
        else:
            sl = price * (1 + sl_pct)
            tp = price * (1 - tp_pct)
            liq_price = price * (1 + 1.0 / leverage * 0.95) if leverage > 1 else 0.0
            emergency_price = price * (1 + emergency_pct)

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

        pos.entry_score = entry_score
        pos.entry_confluence = entry_confluence
        pos.entry_adx = entry_adx
        pos.entry_rsi = entry_rsi
        pos.entry_regime = entry_regime
        pos.entry_regime_confidence = entry_regime_confidence
        pos.entry_bb_width = entry_bb_width
        pos.entry_mode = entry_mode
        pos.mr_tp_price = mr_tp_price

        # MR mode: override SL to tighter value (1.5×ATR instead of 2×ATR)
        if entry_mode == "MEAN_REVERSION" and atr > 0 and price > 0:
            mr_sl_mult = strat.get("mr_sl_atr_mult", 1.5)
            mr_sl_pct = (atr * mr_sl_mult) / price
            if side == OrderSide.BUY_LONG:
                pos.initial_sl = price * (1 - mr_sl_pct)
            else:
                pos.initial_sl = price * (1 + mr_sl_pct)
            pos.trailing_stop = pos.initial_sl
            logger.info(f"  [MR] SL override: {mr_sl_mult}xATR = {pos.initial_sl:.6f} "
                        f"| TP: BB middle = {mr_tp_price:.6f}")

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
        # ATR trailing hesaplamalari
        atr_pct = (atr / price * 100) if price > 0 and atr > 0 else 0
        strat = self._config.get("strategy", {})
        activate_mult = strat.get("trailing_atr_activate_mult", 4.0)
        distance_mult = strat.get("trailing_atr_distance_mult", 1.0)

        # ADX regime override for trailing params
        if strat.get("adx_regime_enabled", False) and entry_regime in (
                "RANGING", "WEAK_TREND", "STRONG_TREND"):
            prefix = {"RANGING": "adx_regime_ranging",
                      "WEAK_TREND": "adx_regime_weak",
                      "STRONG_TREND": "adx_regime_strong"}[entry_regime]
            activate_mult = strat.get(f"{prefix}_trail_activate_atr", activate_mult)
            distance_mult = strat.get(f"{prefix}_trail_callback_atr", distance_mult)

            # RANGING regime: override TP with ATR-based TP
            if entry_regime == "RANGING":
                tp_atr_mult = strat.get("adx_regime_ranging_tp_atr", 3.0)
                if atr > 0 and price > 0:
                    if side == OrderSide.BUY_LONG:
                        pos.tp = price * (1 + (atr * tp_atr_mult / price))
                    else:
                        pos.tp = price * (1 - (atr * tp_atr_mult / price))
                    tp = pos.tp
                    logger.info(f"  [ADX RANGING] TP={tp_atr_mult}xATR @ {tp:.6f}")
        trailing_activate_pct = atr_pct * activate_mult  # 7xATR yuzde hareket
        trailing_distance_pct = atr_pct * distance_mult  # 1xATR geri gelme yuzde
        trailing_activate_roi = trailing_activate_pct * leverage if lev_enabled else trailing_activate_pct

        if side == OrderSide.BUY_LONG:
            trailing_activate_price = price * (1 + trailing_activate_pct / 100)
            trailing_distance_price = atr * distance_mult
        else:
            trailing_activate_price = price * (1 - trailing_activate_pct / 100)
            trailing_distance_price = atr * distance_mult

        logger.info(f"Position opened: {side.value} {size} {symbol} @ {price:.6f} "
                    f"SL={sl:.6f} TP={tp:.6f}{lev_str} "
                    f"[{len(self._positions)}/{self._max_positions}]")
        logger.info(f"  ATR: {atr:.8f} ({atr_pct:.3f}%) TF={timeframe} | "
                    f"Trailing aktif={activate_mult}xATR ({trailing_activate_pct:.2f}% hareket, "
                    f"ROI={trailing_activate_roi:.1f}%, "
                    f"fiyat={trailing_activate_price:.6f}) | "
                    f"Geri gelme={distance_mult}xATR ({trailing_distance_pct:.3f}%, "
                    f"{trailing_distance_price:.8f})")
        return pos

    def check_position(self, symbol: str, current_price: float,
                       indicator_values: dict = None,
                       confluence: dict = None, regime: dict = None,
                       divergences: list = None) -> str:
        """Check a single position for exit signals. Returns 'HOLD' or exit reason."""
        with self._lock:
            if symbol not in self._positions:
                return "HOLD"
            pos = self._positions[symbol]
        pos.current_price = current_price
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
        battle_mode = self._config.get("strategy.battle_mode", False)
        if battle_mode and pos.leverage > 1:
            return self._check_battle_mode(pos, current_price, confluence, divergences)

        # === MEAN REVERSION MODE ===
        if pos.entry_mode == "MEAN_REVERSION" and not pos.regime_switched:
            return self._check_mean_reversion(pos, current_price, indicator_values,
                                               confluence, divergences)

        strat = self._config.get("strategy", {})

        # === 0. EMERGENCY ANTI-LIQUIDATION (highest priority, ALWAYS active) ===
        if strat.get("emergency_enabled", True):
            if self._check_emergency_close(pos, current_price):
                return self.EXIT_EMERGENCY

        # === 1. HARD STOP LOSS (ALWAYS active) ===
        if strat.get("sl_enabled", True):
            if self._check_stop_loss(pos, current_price):
                return self.EXIT_SL

        # === 2. SINYAL CIKIS (EN KRITIK KURAL — her seviyede aktif) ===
        # Sinyal SAT derse → trailing, profit zone, her seyi override et.
        # signal_only_in_profit=true: sadece kârda iken sinyal çıkışı yapar.
        # Zararda server SL korur, sinyal dönüşü erken kapatma yapmaz.
        if strat.get("signal_exit_enabled", True):
            if self._check_confluence_reversal(pos, confluence, current_price):
                return self.EXIT_CONFLUENCE

        # === PROFIT ZONE CHECK (trailing icin) ===
        # Hybrid: virtual_entry_price varsa, trailing hesabi oraya gore yapilir
        atr = pos.atr_at_entry
        trailing_activate_mult = strat.get("trailing_atr_activate_mult", 4.0)
        ref_price = pos.virtual_entry_price if pos.virtual_entry_price > 0 else pos.entry_price
        profit_atr = 0.0
        if atr > 0:
            if pos.side == OrderSide.BUY_LONG:
                profit_atr = (current_price - ref_price) / atr
            else:
                profit_atr = (ref_price - current_price) / atr

        in_profit_zone = profit_atr >= trailing_activate_mult

        # === 3. TAKE PROFIT (only if enabled) ===
        if strat.get("tp_enabled", False):
            if self._check_take_profit(pos, current_price):
                return self.EXIT_TP

        # === 3.5 PARTIAL TAKE PROFIT (2×ATR profit, close 50%) ===
        if strat.get("partial_tp_enabled", False) and not pos.partial_tp_taken:
            partial_mult = strat.get("partial_tp_atr_mult", 2.0)
            if profit_atr >= partial_mult:
                pos.partial_tp_taken = True
                pos.original_size = pos.size
                return self.EXIT_PARTIAL_TP

        # === 4. TRAILING STOP (Hybrid) ===
        # N×ATR'de aktive olur, 1×ATR geri cekilmede tetiklenir.
        # Tetiklenince: sinyal hala gucluyse → trailing sifirla (RENEW)
        #               sinyal zayifsa → kapat (TRAILING_STOP)
        if strat.get("trailing_enabled", True):
            self._update_trailing(pos, current_price)
            if self._check_trailing(pos, current_price):
                return self.EXIT_TRAILING_RENEW

        # === 5. DIVERGENCE WARNING (profit zone'da) ===
        if in_profit_zone and strat.get("divergence_exit_enabled", False):
            if self._check_divergence(pos, current_price, divergences):
                return self.EXIT_DIVERGENCE

        # === 6. REGIME DETERIORATION (profit zone'da) ===
        if in_profit_zone and regime:
            if self._handle_regime_change(pos, current_price, regime):
                return self.EXIT_REGIME

        # === 7. TIME LIMIT (her seviyede) ===
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
        - Below fee breakeven (net_roi < 0): ONLY emergency close, nothing else
        - Above fee but <50% ROI: sell only if signals strongly reversed
        - Above 50% ROI: sell if signals reversed, otherwise HOLD
        - Trailing: very wide (uses normal ATR config but battle-specific thresholds)
        - NO time limit, NO take profit, NO divergence exit below fee
        - NO SL exit (server SL handles that, we just track)
        """
        lev = pos.leverage
        strat = self._config.get("strategy", {})
        fee_pct = strat.get("fee_pct", 0.10) / 100.0  # config: 0.10 = %0.10 round-trip
        fee_roi = fee_pct * lev * 100  # fee as % of margin

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
        """Battle mode trailing: very wide distance, lets profits run.
        Uses 2x the normal ATR activation to be more patient."""
        lev = pos.leverage
        fee_roi = 0.001 * lev * 100
        strat = self._config.get("strategy", {})
        trailing_mode = strat.get("trailing_mode", "roi")

        if trailing_mode == "atr" and pos.atr_at_entry > 0:
            atr = pos.atr_at_entry
            # Battle mode: 2x normal activation, 2x normal distance (more patient)
            normal_activate = strat.get("trailing_atr_activate_mult", 7.0)
            normal_distance = strat.get("trailing_atr_distance_mult", 1.0)
            activate_mult = normal_activate * 2.0
            distance_mult = normal_distance * 2.0
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
                net_roi = roi - fee_roi  # fee-aware
                if net_roi >= activate_roi:
                    if not pos.trailing_active:
                        logger.info(f"[SAVAS] {pos.symbol} trailing activated @ net ROI {net_roi:.1f}%")
                    pos.trailing_active = True
                    new_trail = price * (1 - trail_price_pct)
                    if new_trail > pos.trailing_stop:
                        pos.trailing_stop = new_trail
            else:
                roi = (pos.entry_price - price) / pos.entry_price * lev * 100
                net_roi = roi - fee_roi  # fee-aware
                if net_roi >= activate_roi:
                    if not pos.trailing_active:
                        logger.info(f"[SAVAS] {pos.symbol} trailing activated @ net ROI {net_roi:.1f}%")
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

    # ──── MEAN REVERSION MODE ────

    def _check_mean_reversion(self, pos: ActivePosition, price: float,
                               indicator_values: dict, confluence: dict,
                               divergences: list) -> str:
        """Mean Reversion exit logic.

        Priority:
        0. Emergency anti-liquidation (always active)
        1. Stop loss (tight: 1.5×ATR)
        2. BB middle TP (primary MR target)
        3. Regime switch detection (breakout → convert to TREND)
        4. Signal reversal (confluence against us)
        5. Time limit (MR-specific, shorter)

        Returns 'HOLD' or exit reason string.
        """
        strat = self._config.get("strategy", {})

        # === 0. EMERGENCY (always active) ===
        if strat.get("emergency_enabled", True):
            if self._check_emergency_close(pos, price):
                return self.EXIT_EMERGENCY

        # === 1. STOP LOSS (tight MR SL) ===
        if strat.get("sl_enabled", True):
            if self._check_stop_loss(pos, price):
                return self.EXIT_SL

        # === 2. BB MIDDLE TP (primary MR target) ===
        if pos.mr_tp_price > 0:
            if pos.side == OrderSide.BUY_LONG and price >= pos.mr_tp_price:
                logger.info(f"[MR] {pos.symbol} BB middle TP hit @ {price:.6f} "
                            f"(target={pos.mr_tp_price:.6f})")
                return self.EXIT_MR_TP
            elif pos.side == OrderSide.SELL_SHORT and price <= pos.mr_tp_price:
                logger.info(f"[MR] {pos.symbol} BB middle TP hit @ {price:.6f} "
                            f"(target={pos.mr_tp_price:.6f})")
                return self.EXIT_MR_TP

        # === 3. REGIME SWITCH DETECTION (MR → TREND) ===
        # If price breaks through opposite BB + volume surging + ADX rising
        # → convert this position to TREND mode (remove TP, start trailing)
        if strat.get("mr_breakout_to_trend", True) and indicator_values:
            switched = self._check_mr_regime_switch(pos, price, indicator_values)
            if switched:
                # Position stays open but mode changes — return HOLD
                return "HOLD"

        # === 4. SIGNAL REVERSAL ===
        if strat.get("signal_exit_enabled", True):
            if self._check_confluence_reversal(pos, confluence, price):
                return self.EXIT_CONFLUENCE

        # === 5. TIME LIMIT (MR-specific, shorter) ===
        if strat.get("time_limit_enabled", True):
            mr_time_limit = strat.get("mr_time_limit_minutes", 240) * 60
            hold_time = time.time() - pos.entry_time
            if hold_time >= mr_time_limit:
                logger.info(f"[MR] {pos.symbol} MR time limit reached "
                            f"({hold_time/60:.0f}min >= {mr_time_limit/60:.0f}min)")
                return self.EXIT_TIME

        # Publish position update
        self._event_bus.publish(EventType.POSITION_UPDATE, {
            "symbol": pos.symbol,
            "entry_price": pos.entry_price,
            "current_price": price,
            "pnl": self._get_pnl(pos, price),
            "pnl_pct": self._get_pnl_pct(pos, price),
            "trailing_stop": pos.trailing_stop,
            "highest": pos.highest_price,
            "hold_seconds": time.time() - pos.entry_time,
        })

        return "HOLD"

    def _check_mr_regime_switch(self, pos: ActivePosition, price: float,
                                 indicators: dict) -> bool:
        """Detect breakout from MR range → switch to TREND mode.

        Conditions (all must be true):
        1. Price broke through opposite BB band
        2. Volume ratio > breakout threshold
        3. ADX is rising (trend forming)

        Returns True if regime switch happened.
        """
        strat = self._config.get("strategy", {})
        breakout_vol = strat.get("mr_breakout_volume_ratio", 1.5)

        bb_upper = indicators.get("BB_Upper", 0)
        bb_lower = indicators.get("BB_Lower", 0)
        vol_ratio = indicators.get("Volume_ratio", 1.0)
        adx_slope = indicators.get("ADX_slope", 0)

        if bb_upper <= 0 or bb_lower <= 0:
            return False

        # Check breakout conditions
        breakout = False
        if pos.side == OrderSide.BUY_LONG:
            # LONG from lower band: breakout if price > upper BB + volume + ADX rising
            if price > bb_upper and vol_ratio >= breakout_vol and adx_slope > 0:
                breakout = True
        else:
            # SHORT from upper band: breakout if price < lower BB + volume + ADX rising
            if price < bb_lower and vol_ratio >= breakout_vol and adx_slope > 0:
                breakout = True

        if breakout:
            logger.info(f"[MR→TREND] {pos.symbol} regime switch! "
                        f"Price broke {'upper' if pos.side == OrderSide.BUY_LONG else 'lower'} BB "
                        f"(vol={vol_ratio:.1f}x, ADX_slope={adx_slope:.1f})")
            pos.entry_mode = "TREND"
            pos.regime_switched = True
            pos.mr_tp_price = 0.0  # Remove MR TP

            # Activate trailing for trend mode
            atr = pos.atr_at_entry
            activate_mult = strat.get("trailing_atr_activate_mult", 4.0)
            distance_mult = strat.get("trailing_atr_distance_mult", 1.0)

            if atr > 0:
                ref_price = pos.entry_price
                if pos.side == OrderSide.BUY_LONG:
                    profit_atr = (price - ref_price) / atr
                    trail_dist = atr * distance_mult
                    new_trail = price - trail_dist
                    if new_trail > pos.trailing_stop:
                        pos.trailing_stop = new_trail
                    if profit_atr >= activate_mult:
                        pos.trailing_active = True
                else:
                    profit_atr = (ref_price - price) / atr
                    trail_dist = atr * distance_mult
                    new_trail = price + trail_dist
                    if pos.trailing_stop <= 0 or new_trail < pos.trailing_stop:
                        pos.trailing_stop = new_trail
                    if profit_atr >= activate_mult:
                        pos.trailing_active = True

            return True

        return False

    def close_position(self, symbol: str, exit_price: float, reason: str) -> dict:
        """Close a specific position and return trade result. Thread-safe."""
        with self._lock:
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
                "notional_usdt": pos.notional_usdt,
                "entry_time": pos.entry_time,
                "initial_sl": pos.initial_sl,
                "initial_tp": pos.initial_tp,
                "atr_at_entry": pos.atr_at_entry,
                "timeframe": pos.timeframe,
                "entry_score": pos.entry_score,
                "entry_confluence": pos.entry_confluence,
                "entry_adx": pos.entry_adx,
                "entry_rsi": pos.entry_rsi,
                "entry_regime": pos.entry_regime,
                "entry_regime_confidence": pos.entry_regime_confidence,
                "entry_bb_width": pos.entry_bb_width,
            }

            del self._positions[symbol]

        self._event_bus.publish(EventType.POSITION_CLOSED, result)
        logger.info(f"Position closed: {pos.symbol} PnL={pnl:+.4f} USDT "
                    f"({pnl_pct:+.2f}%) reason={reason} "
                    f"held={hold_duration:.0f}s "
                    f"[{self.position_count}/{self._max_positions}]")

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
        if pos.leverage >= 1:
            lev = pos.leverage
            fee_roi = 0.001 * lev * 100
            strat = self._config.get("strategy", {})
            trailing_mode = strat.get("trailing_mode", "roi")

            if trailing_mode == "atr" and pos.atr_at_entry > 0:
                # ATR-based trailing: activate at N*ATR profit, trail at M*ATR distance
                # Uses virtual_entry_price for renewed trailing (hybrid system)
                atr = pos.atr_at_entry
                activate_mult = strat.get("trailing_atr_activate_mult", 4.0)
                distance_mult = strat.get("trailing_atr_distance_mult", 1.0)
                activate_price = atr * activate_mult
                trail_distance = atr * distance_mult
                ref_price = pos.virtual_entry_price if pos.virtual_entry_price > 0 else pos.entry_price

                if pos.side == OrderSide.BUY_LONG:
                    profit = price - ref_price
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
                    profit = ref_price - price
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
            # Fallback: ATR mode olmadığında da aynı parametreleri kullan (tutarlılık)
            atr = pos.atr_at_entry
            activation_mult = strat.get("trailing_atr_activate_mult", 7.0)
            trail_mult = strat.get("trailing_atr_distance_mult", 1.0)

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
        """Trailing stop tetiklendi mi? 7×ATR'de aktive olur, 1×ATR geri cekilmede kapatir.
        Min kar = 6×ATR. Risk/Reward 1:3."""
        if not pos.trailing_active:
            return False

        triggered = False
        if pos.side == OrderSide.BUY_LONG:
            triggered = price <= pos.trailing_stop
        else:
            triggered = price >= pos.trailing_stop

        if triggered:
            # Log the profit at close
            atr = pos.atr_at_entry
            if atr > 0:
                if pos.side == OrderSide.BUY_LONG:
                    profit_atr = (price - pos.entry_price) / atr
                else:
                    profit_atr = (pos.entry_price - price) / atr
                lev = pos.leverage if pos.leverage > 1 else 1
                roi = profit_atr * (atr / pos.entry_price) * lev * 100
                logger.info(f"[{pos.symbol}] Trailing kapaniyor: "
                            f"{profit_atr:.1f}×ATR kar, ROI {roi:.1f}%")

        return triggered

    def renew_trailing(self, symbol: str, current_price: float, new_atr: float = 0) -> None:
        """Hybrid trailing: sinyal güçlü, trailing sıfırlanıyor.
        Sanki bu fiyattan yeni pozisyon açılmış gibi davran:
        - virtual_entry_price = current_price
        - trailing_active = False (yeniden 7×ATR bekle)
        - SL'i ileri taşı (yeni sanal girişe göre)
        - ATR güncelle (opsiyonel)
        """
        with self._lock:
            if symbol not in self._positions:
                return

            pos = self._positions[symbol]
            old_virtual = pos.virtual_entry_price or pos.entry_price
            pos.virtual_entry_price = current_price
            pos.trailing_active = False
            pos.trailing_renewal_count += 1

            # Update ATR if provided (fresh ATR from current candles)
            if new_atr > 0:
                pos.atr_at_entry = new_atr

            atr = pos.atr_at_entry
            strat = self._config.get("strategy", {})
            liq_factor = strat.get("liq_factor", 70) / 100.0
            sl_liq_pct = strat.get("sl_liq_percent", 50) / 100.0
            lev = pos.leverage

            # New SL from virtual entry (fee-aware, same formula as initial SL)
            fee_pct = strat.get("fee_pct", 0.10) / 100.0
            fee_roi = fee_pct * lev * 100
            slippage_mult = strat.get("slippage_mult", 0.5)
            slippage_roi = fee_roi * slippage_mult
            liq_pct = (1.0 / lev) * liq_factor
            raw_sl_roi = liq_pct * sl_liq_pct * lev * 100
            net_sl_roi = max(raw_sl_roi - fee_roi - slippage_roi, fee_roi)
            sl_pct = net_sl_roi / (lev * 100)

            if pos.side == OrderSide.BUY_LONG:
                new_sl = current_price * (1 - sl_pct)
                # SL must never go backwards
                if new_sl > pos.initial_sl:
                    pos.initial_sl = new_sl
                pos.highest_price = current_price
                pos.trailing_stop = 0.0
            else:
                new_sl = current_price * (1 + sl_pct)
                if new_sl < pos.initial_sl:
                    pos.initial_sl = new_sl
                pos.lowest_price = current_price
                pos.trailing_stop = float('inf')

            # Calculate profit from real entry for logging
            if pos.side == OrderSide.BUY_LONG:
                total_profit_atr = (current_price - pos.entry_price) / atr if atr > 0 else 0
            else:
                total_profit_atr = (pos.entry_price - current_price) / atr if atr > 0 else 0
            roi = total_profit_atr * (atr / pos.entry_price) * lev * 100 if atr > 0 else 0

        logger.info(f"[{pos.symbol}] TRAILING RENEWED #{pos.trailing_renewal_count}: "
                    f"virtual_entry={current_price:.6f} "
                    f"new_SL={pos.initial_sl:.6f} "
                    f"total_profit={total_profit_atr:.1f}×ATR (ROI {roi:.1f}%) "
                    f"waiting for next 7×ATR move")

    def _signal_supports_position(self, pos: ActivePosition, confluence: dict) -> bool:
        """Check if current signals still support the position direction.
        Returns True if signal says HOLD/BUY for LONG or HOLD/SELL for SHORT."""
        if not confluence:
            return False  # no data = no support, close to be safe
        score = confluence.get("score", 0)
        signal = confluence.get("signal", "NEUTRAL")

        if pos.side == OrderSide.BUY_LONG:
            # LONG position: signal must be BUY or at least not SELL
            return score > 0 and signal != "SELL"
        else:
            # SHORT position: signal must be SELL or at least not BUY
            return score < 0 and signal != "BUY"

    def _check_confluence_reversal(self, pos: ActivePosition, confluence: dict,
                                    current_price: float = 0) -> bool:
        if not confluence:
            return False
        signal = confluence.get("signal", "NEUTRAL")
        score = confluence.get("score", 0)

        strat = self._config.get("strategy", {})
        min_hold = strat.get("signal_min_hold_seconds", 180)
        if time.time() - pos.entry_time < min_hold:
            return False

        # Kârda mıyız? (fee-aware ROI, tüm kaldıraç seviyelerinde tutarlı)
        in_profit = False
        roi = 0.0
        if current_price > 0 and pos.entry_price > 0:
            lev = max(pos.leverage, 1)
            fee_roi = 0.001 * lev * 100
            if pos.side == OrderSide.BUY_LONG:
                roi = (current_price - pos.entry_price) / pos.entry_price * lev * 100
            else:
                roi = (pos.entry_price - current_price) / pos.entry_price * lev * 100
            in_profit = roi >= fee_roi

        # Sinyal eşikleri
        min_score = strat.get("min_buy_score", 70)
        threshold = strat.get("signal_exit_threshold", 4.0)
        deep_threshold = strat.get("signal_deep_exit_threshold", 8.0)
        only_in_profit = strat.get("signal_only_in_profit", True)
        abs_score = abs(score)
        reverse_worthy = abs_score >= min_score

        # --- Kârda: normal eşik (conf threshold) ile çıkış ---
        if in_profit:
            if pos.side == OrderSide.BUY_LONG and signal == "SELL" and score <= -threshold and reverse_worthy:
                logger.info(f"[SIGNAL EXIT] {pos.symbol} kârda sinyal çıkışı "
                            f"(conf={score:.1f}, abs={abs_score:.1f} >= min_score={min_score}, ROI={roi:.1f}%)")
                return True
            if pos.side == OrderSide.SELL_SHORT and signal == "BUY" and score >= threshold and reverse_worthy:
                logger.info(f"[SIGNAL EXIT] {pos.symbol} kârda sinyal çıkışı "
                            f"(conf={score:.1f}, abs={abs_score:.1f} >= min_score={min_score}, ROI={roi:.1f}%)")
                return True

        # --- Zararda: derin reversal eşiği (signal_only_in_profit=false ise) ---
        if not in_profit and not only_in_profit:
            if pos.side == OrderSide.BUY_LONG and signal == "SELL" and score <= -deep_threshold and reverse_worthy:
                logger.info(f"[SIGNAL EXIT] {pos.symbol} zararda derin reversal çıkışı "
                            f"(conf={score:.1f} <= -{deep_threshold:.0f}, ROI={roi:.1f}%)")
                return True
            if pos.side == OrderSide.SELL_SHORT and signal == "BUY" and score >= deep_threshold and reverse_worthy:
                logger.info(f"[SIGNAL EXIT] {pos.symbol} zararda derin reversal çıkışı "
                            f"(conf={score:.1f} >= +{deep_threshold:.0f}, ROI={roi:.1f}%)")
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

    def _handle_regime_change(self, pos: ActivePosition, price: float,
                              regime: dict) -> bool:
        """Volatile rejimde trailing'i sıkılaştır.
        Eğer sıkılaştırılan trail zaten aşılmışsa → anında EXIT_REGIME döndür.
        Değilse trailing sonraki döngüde halleder → False döndür."""
        regime_name = regime.get("regime", "")
        confidence = regime.get("confidence", 0)

        if regime_name == "VOLATILE" and confidence > 0.6:
            atr = pos.atr_at_entry
            # Volatile rejimde trailing mesafesini 0.5×ATR'ye daralt (normal=1×ATR)
            if pos.side == OrderSide.BUY_LONG:
                tight_trail = pos.highest_price - atr * 0.5
                if tight_trail > pos.trailing_stop:
                    pos.trailing_stop = tight_trail
                    pos.trailing_active = True
                # Fiyat zaten tight trail'in altındaysa → anında çık
                if price <= pos.trailing_stop:
                    logger.info(f"[REGIME] {pos.symbol} VOLATILE rejim + fiyat trail altında "
                                f"(price={price:.6f} <= trail={pos.trailing_stop:.6f}), "
                                f"anında çıkış")
                    return True
            else:
                tight_trail = pos.lowest_price + atr * 0.5
                if tight_trail < pos.trailing_stop:
                    pos.trailing_stop = tight_trail
                    pos.trailing_active = True
                if price >= pos.trailing_stop:
                    logger.info(f"[REGIME] {pos.symbol} VOLATILE rejim + fiyat trail üstünde "
                                f"(price={price:.6f} >= trail={pos.trailing_stop:.6f}), "
                                f"anında çıkış")
                    return True

        return False

    def _check_time_limit(self, pos: ActivePosition, price: float) -> bool:
        strat = self._config.get("strategy", {})
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
                lev = max(pos.leverage, 1)
                fee_roi = 0.001 * lev * 100
                if pos.side == OrderSide.BUY_LONG:
                    roi = (price - pos.entry_price) / pos.entry_price * lev * 100
                else:
                    roi = (pos.entry_price - price) / pos.entry_price * lev * 100
                if roi > -fee_roi and held < max_hold * 2:
                    return False
            return True
        return False

    # ──── PnL helpers ────

    def _get_pnl(self, pos: ActivePosition, price: float) -> float:
        """PnL including estimated trading fees (entry + exit)."""
        if pos.side == OrderSide.BUY_LONG:
            raw_pnl = (price - pos.entry_price) * pos.size
        else:
            raw_pnl = (pos.entry_price - price) * pos.size
        # Subtract round-trip fee (0.05% taker each side)
        entry_fee = pos.size * pos.entry_price * 0.0005
        exit_fee = pos.size * price * 0.0005
        return raw_pnl - entry_fee - exit_fee

    def _get_pnl_pct(self, pos: ActivePosition, price: float) -> float:
        """PnL percentage including fees (consistent with _get_pnl)."""
        if pos.entry_price == 0 or pos.size == 0:
            return 0.0
        pnl = self._get_pnl(pos, price)
        notional = pos.size * pos.entry_price
        return (pnl / notional) * 100

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
        # Calculate current ROI
        roi = 0.0
        current = pos.current_price if pos.current_price > 0 else pos.entry_price
        if pos.leverage >= 1 and pos.margin_usdt > 0:
            pnl = self._get_pnl(pos, current)
            roi = pnl / pos.margin_usdt * 100
        # ATR trailing hesaplamalari
        atr = pos.atr_at_entry
        price = pos.entry_price
        lev = pos.leverage if pos.leverage >= 1 else 1
        strat = self._config.get("strategy", {})
        activate_mult = strat.get("trailing_atr_activate_mult", 4.0)
        distance_mult = strat.get("trailing_atr_distance_mult", 1.0)
        atr_pct = (atr / price * 100) if price > 0 and atr > 0 else 0
        trailing_activate_pct = atr_pct * activate_mult
        trailing_distance_pct = atr_pct * distance_mult
        trailing_activate_roi = trailing_activate_pct * lev

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
            "atr_at_entry": pos.atr_at_entry,
            "atr_pct": atr_pct,
            "trailing_activate_pct": trailing_activate_pct,
            "trailing_activate_roi": trailing_activate_roi,
            "trailing_distance_pct": trailing_distance_pct,
            "entry_score": pos.entry_score,
            "entry_confluence": pos.entry_confluence,
            "entry_adx": pos.entry_adx,
            "entry_rsi": pos.entry_rsi,
            "roi_percent": roi,
        }
