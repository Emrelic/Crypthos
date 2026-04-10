import tkinter as tk
import customtkinter as ctk
from datetime import datetime, timedelta
from tkcalendar import Calendar
from loguru import logger


# Exit reason display names and colors
EXIT_COLORS = {
    "STOP_LOSS": "#FF1744",
    "TRAILING_STOP": "#FF9800",
    "EMERGENCY_ANTI_LIQ": "#FF0000",
    "TAKE_PROFIT": "#00E676",
    "CONFLUENCE_REVERSAL": "#2196F3",
    "DIVERGENCE_WARNING": "#9C27B0",
    "REGIME_DETERIORATION": "#795548",
    "TIME_LIMIT": "#607D8B",
    "external_close": "#FF9800",
    "TRAILING_RENEW": "#FFC107",
    "PARTIAL_TP": "#4CAF50",
}

EXIT_NAMES_TR = {
    "STOP_LOSS": "Stop Loss",
    "TRAILING_STOP": "Trailing Stop",
    "EMERGENCY_ANTI_LIQ": "Likidasyon Koruma",
    "TAKE_PROFIT": "Kar Al",
    "CONFLUENCE_REVERSAL": "Sinyal Donus",
    "DIVERGENCE_WARNING": "Diverjans",
    "REGIME_DETERIORATION": "Rejim Bozulma",
    "TIME_LIMIT": "Zaman Asimi",
    "external_close": "Server/Harici",
    "TRAILING_RENEW": "Trailing Yenileme",
    "PARTIAL_TP": "Kismi Kar Al",
}


class TradeReportPanel(ctk.CTkFrame):
    """Comprehensive trade analysis and reporting panel with date filtering."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._trades = []
        self._build_ui()

    def _build_ui(self) -> None:
        # ── Top: Filter Bar ──
        filter_frame = ctk.CTkFrame(self, fg_color="#1a1a2e")
        filter_frame.pack(fill="x", padx=10, pady=(10, 5))

        ctk.CTkLabel(filter_frame, text="Islem Raporu",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=10)

        # ── Date-Time Picker: Takvim popup + saat/dakika ──
        now = datetime.now()
        ago24 = now - timedelta(hours=24)

        # Başlangıç
        ctk.CTkLabel(filter_frame, text="Baslangic:",
                     font=ctk.CTkFont(size=11)).pack(side="left", padx=(15, 3))
        self._start_btn = ctk.CTkButton(
            filter_frame, text=ago24.strftime("%Y-%m-%d %H:%M"),
            width=145, height=28, font=ctk.CTkFont(size=12),
            fg_color="#263238", hover_color="#37474F", border_width=1,
            border_color="#546E7A", anchor="center",
            command=lambda: self._open_datetime_picker("start"))
        self._start_btn.pack(side="left", padx=2)

        # Bitiş
        ctk.CTkLabel(filter_frame, text="Bitis:",
                     font=ctk.CTkFont(size=11)).pack(side="left", padx=(10, 3))
        self._end_btn = ctk.CTkButton(
            filter_frame, text=now.strftime("%Y-%m-%d %H:%M"),
            width=145, height=28, font=ctk.CTkFont(size=12),
            fg_color="#263238", hover_color="#37474F", border_width=1,
            border_color="#546E7A", anchor="center",
            command=lambda: self._open_datetime_picker("end"))
        self._end_btn.pack(side="left", padx=2)

        # Uyumluluk: _start_entry / _end_entry arayüzü
        class _BtnDateAccessor:
            def __init__(self, btn):
                self._btn = btn
            def get(self):
                return self._btn.cget("text")
            def delete(self, *a):
                pass
            def insert(self, idx, val):
                self._btn.configure(text=val.strip())
            def strip(self):
                return self.get().strip()
        self._start_entry = _BtnDateAccessor(self._start_btn)
        self._end_entry = _BtnDateAccessor(self._end_btn)

        # Quick filter buttons
        quick_frame = ctk.CTkFrame(filter_frame, fg_color="transparent")
        quick_frame.pack(side="left", padx=(10, 0))
        for text, hours in [("1s", 1), ("4s", 4), ("12s", 12), ("24s", 24),
                            ("2g", 48), ("3g", 72), ("7g", 168), ("30g", 720)]:
            ctk.CTkButton(quick_frame, text=text, width=38, height=26,
                          font=ctk.CTkFont(size=11),
                          fg_color="#37474F", hover_color="#455A64",
                          command=lambda h=hours: self._quick_range(h)).pack(side="left", padx=1)
        ctk.CTkButton(quick_frame, text="Tumu", width=42, height=26,
                      font=ctk.CTkFont(size=11),
                      fg_color="#37474F", hover_color="#455A64",
                      command=self._show_all).pack(side="left", padx=1)

        # Config period filter
        ctk.CTkLabel(filter_frame, text="Config:",
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=(15, 5))
        self._config_combo = ctk.CTkComboBox(filter_frame, width=200,
                                              values=["Tum Donemler"],
                                              command=self._on_config_period_change)
        self._config_combo.pack(side="left", padx=2)
        self._config_combo.set("Tum Donemler")

        ctk.CTkButton(filter_frame, text="Filtrele", width=80, height=32,
                      fg_color="#00C853", hover_color="#00E676",
                      command=self._apply_filter).pack(side="right", padx=10)

        self._import_status = ctk.CTkLabel(filter_frame, text="",
                                            font=ctk.CTkFont(size=10), text_color="gray")
        self._import_status.pack(side="right", padx=5)

        ctk.CTkButton(filter_frame, text="Binance'den Cek", width=120, height=28,
                      fg_color="#FF9800", hover_color="#FFB74D",
                      command=self._import_from_binance).pack(side="right", padx=5)

        # ── Summary Cards (2 rows) ──
        self._summary_cards = {}

        # Row 1: Temel metrikler
        row1 = ctk.CTkFrame(self, fg_color="transparent")
        row1.pack(fill="x", padx=10, pady=(5, 1))
        row1_defs = [
            ("total_trades", "Toplam Islem", "0", "#2196F3"),
            ("total_volume", "Islem Hacmi", "$0", "#42A5F5"),
            ("win_rate", "Kazanma Orani", "%0", "#00C853"),
            ("total_profit", "Toplam Kar", "$0.00", "#00E676"),
            ("total_loss", "Toplam Zarar", "$0.00", "#FF5252"),
            ("total_pnl", "Net Kar/Zarar", "$0.00", "#FF9800"),
            ("total_fee", "Toplam Fee", "$0.00", "#E91E63"),
            ("profit_factor", "Kar Faktoru", "0.0", "#FFC107"),
        ]
        for key, label, default, color in row1_defs:
            self._make_card(row1, key, label, default, color)

        # Row 2: Detay metrikler
        row2 = ctk.CTkFrame(self, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=(1, 5))
        row2_defs = [
            ("sl_total", "Stop Loss", "$0.00", "#FF1744"),
            ("tp_total", "Take Profit", "$0.00", "#00C853"),
            ("trail_total", "Trailing Stop", "$0.00", "#FF9800"),
            ("liq_total", "Likidasyon", "$0.00", "#D50000"),
            ("signal_total", "Sinyal Cikis", "$0.00", "#2196F3"),
            ("avg_hold", "Ort. Sure", "0dk", "#9C27B0"),
            ("avg_leverage", "Ort. Kaldirac", "0x", "#607D8B"),
            ("long_short", "Long/Short", "0/0", "#00BCD4"),
        ]
        for key, label, default, color in row2_defs:
            self._make_card(row2, key, label, default, color)

        # Row 3: Streak + risk metrikleri
        row3 = ctk.CTkFrame(self, fg_color="transparent")
        row3.pack(fill="x", padx=10, pady=(1, 5))
        row3_defs = [
            ("best_trade", "En Iyi Islem", "$0.00", "#00E676"),
            ("worst_trade", "En Kotu Islem", "$0.00", "#FF5252"),
            ("avg_win", "Ort. Kazanc", "$0.00", "#66BB6A"),
            ("avg_loss", "Ort. Kayip", "$0.00", "#EF5350"),
            ("max_win_streak", "Max Kazanc Serisi", "0", "#00C853"),
            ("max_loss_streak", "Max Kayip Serisi", "0", "#FF1744"),
            ("max_drawdown", "Max Drawdown", "$0.00", "#D50000"),
            ("best_coin", "En Iyi Coin", "--", "#FFD600"),
        ]
        for key, label, default, color in row3_defs:
            self._make_card(row3, key, label, default, color)

        # ── Exit Reason Breakdown ──
        self._breakdown_frame = ctk.CTkFrame(self, fg_color="#1a1a2e", corner_radius=8)
        self._breakdown_frame.pack(fill="x", padx=10, pady=5)
        self._breakdown_inner = ctk.CTkFrame(self._breakdown_frame, fg_color="transparent")
        self._breakdown_inner.pack(fill="x", padx=10, pady=5)

        # ── Config Period Comparison ──
        self._config_comparison_frame = ctk.CTkFrame(self, fg_color="#1a1a2e", corner_radius=8)
        self._config_comparison_frame.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(self._config_comparison_frame, text="Config Donemi Karsilastirmasi",
                     font=ctk.CTkFont(size=11, weight="bold"), text_color="white").pack(anchor="w", padx=10, pady=(5, 0))
        self._config_comparison_inner = ctk.CTkFrame(self._config_comparison_frame, fg_color="transparent")
        self._config_comparison_inner.pack(fill="x", padx=10, pady=5)

        # ── Trade Table ──
        table_header = ctk.CTkFrame(self, fg_color="#1a1a2e")
        table_header.pack(fill="x", padx=10, pady=(5, 0))

        cols = [
            ("Acilis", 130), ("Kapanis", 130), ("Sembol", 90), ("Yon", 65),
            ("Kaldirac", 55), ("Marjin", 65), ("Giris", 85), ("Cikis", 85),
            ("PnL $", 70), ("ROI %", 60), ("Fee", 55), ("Cikis Nedeni", 110),
            ("Sure", 60), ("Rejim", 75), ("Score", 45), ("RSI", 40), ("ADX", 40), ("Conf", 40),
        ]
        for text, w in cols:
            ctk.CTkLabel(table_header, text=text, width=w,
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color="#90CAF9").pack(side="left", padx=1)

        self._table_scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._table_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _make_card(self, parent, key: str, label: str,
                   default: str, color: str) -> None:
        """Create a summary stat card."""
        card = ctk.CTkFrame(parent, fg_color="#1a1a2e",
                            corner_radius=8, height=62)
        card.pack(side="left", fill="x", expand=True, padx=2)
        card.pack_propagate(False)
        ctk.CTkLabel(card, text=label, font=ctk.CTkFont(size=9),
                     text_color="gray").pack(pady=(6, 0))
        val_lbl = ctk.CTkLabel(card, text=default,
                                font=ctk.CTkFont(size=14, weight="bold"),
                                text_color=color)
        val_lbl.pack(pady=(0, 4))
        self._summary_cards[key] = val_lbl

    def _open_datetime_picker(self, which: str) -> None:
        """Takvim + saat/dakika seçici popup aç."""
        # Mevcut değeri parse et
        btn = self._start_btn if which == "start" else self._end_btn
        current_text = btn.cget("text")
        try:
            current_dt = datetime.strptime(current_text, "%Y-%m-%d %H:%M")
        except ValueError:
            current_dt = datetime.now()

        # Popup pencere
        popup = tk.Toplevel(self)
        popup.title("Tarih & Saat Sec" if which == "start" else "Bitis Tarih & Saat")
        popup.geometry("320x360")
        popup.resizable(False, False)
        popup.configure(bg="#1a1a2e")
        popup.attributes("-topmost", True)
        popup.grab_set()

        # Takvim widget'ı
        cal = Calendar(popup, selectmode="day",
                       year=current_dt.year, month=current_dt.month, day=current_dt.day,
                       date_pattern="yyyy-mm-dd",
                       background="#1a1a2e", foreground="white",
                       selectbackground="#3d5afe", selectforeground="white",
                       normalbackground="#263238", normalforeground="white",
                       weekendbackground="#1a1a2e", weekendforeground="#90A4AE",
                       headersbackground="#0d47a1", headersforeground="white",
                       bordercolor="#37474F", othermonthforeground="#546E7A",
                       othermonthwebackground="#1a1a2e",
                       font=("Segoe UI", 11))
        cal.pack(padx=10, pady=(10, 5), fill="x")

        # Saat & Dakika seçici
        time_frame = tk.Frame(popup, bg="#1a1a2e")
        time_frame.pack(pady=5)

        tk.Label(time_frame, text="Saat:", bg="#1a1a2e", fg="white",
                 font=("Segoe UI", 11)).pack(side="left", padx=(0, 5))
        hour_var = tk.StringVar(value=f"{current_dt.hour:02d}")
        hour_spin = tk.Spinbox(time_frame, from_=0, to=23, width=3, wrap=True,
                               textvariable=hour_var, format="%02.0f",
                               font=("Segoe UI", 14), justify="center",
                               bg="#263238", fg="white", buttonbackground="#37474F",
                               insertbackground="white")
        hour_spin.pack(side="left", padx=2)

        tk.Label(time_frame, text=":", bg="#1a1a2e", fg="white",
                 font=("Segoe UI", 14, "bold")).pack(side="left")

        minute_var = tk.StringVar(value=f"{current_dt.minute:02d}")
        minute_spin = tk.Spinbox(time_frame, from_=0, to=59, width=3, wrap=True,
                                 textvariable=minute_var, format="%02.0f",
                                 increment=5, font=("Segoe UI", 14), justify="center",
                                 bg="#263238", fg="white", buttonbackground="#37474F",
                                 insertbackground="white")
        minute_spin.pack(side="left", padx=2)

        # Hızlı saat butonları
        quick_time = tk.Frame(popup, bg="#1a1a2e")
        quick_time.pack(pady=3)
        for label, h, m in [("00:00", 0, 0), ("06:00", 6, 0), ("12:00", 12, 0),
                            ("18:00", 18, 0), ("Simdi", -1, -1), ("23:59", 23, 59)]:
            def _set_time(hh=h, mm=m):
                if hh == -1:
                    n = datetime.now()
                    hour_var.set(f"{n.hour:02d}")
                    minute_var.set(f"{n.minute:02d}")
                else:
                    hour_var.set(f"{hh:02d}")
                    minute_var.set(f"{mm:02d}")
            tk.Button(quick_time, text=label, width=6, bg="#37474F", fg="white",
                      activebackground="#455A64", activeforeground="white",
                      relief="flat", font=("Segoe UI", 9),
                      command=_set_time).pack(side="left", padx=2)

        # Onay butonu
        def _confirm():
            date_str = cal.get_date()
            h = int(hour_var.get())
            m = int(minute_var.get())
            result = f"{date_str} {h:02d}:{m:02d}"
            btn.configure(text=result)
            popup.destroy()

        confirm_frame = tk.Frame(popup, bg="#1a1a2e")
        confirm_frame.pack(pady=(10, 5))
        tk.Button(confirm_frame, text="  Uygula  ", bg="#00C853", fg="white",
                  activebackground="#00E676", activeforeground="white",
                  relief="flat", font=("Segoe UI", 12, "bold"),
                  command=_confirm).pack(side="left", padx=5)
        tk.Button(confirm_frame, text="  Iptal  ", bg="#455A64", fg="white",
                  activebackground="#546E7A", activeforeground="white",
                  relief="flat", font=("Segoe UI", 12),
                  command=popup.destroy).pack(side="left", padx=5)

    def _quick_range(self, hours: int) -> None:
        self._start_entry.delete(0, "end")
        self._end_entry.delete(0, "end")
        self._start_entry.insert(0, (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M"))
        self._end_entry.insert(0, datetime.now().strftime("%Y-%m-%d %H:%M"))
        self._apply_filter()

    def _show_all(self) -> None:
        self._start_entry.delete(0, "end")
        self._end_entry.delete(0, "end")
        self._start_entry.insert(0, "2020-01-01 00:00")
        self._end_entry.insert(0, datetime.now().strftime("%Y-%m-%d %H:%M"))
        self._apply_filter()

    def _apply_filter(self) -> None:
        if not self.controller.order_logger:
            return

        start = self._start_entry.get().strip()
        end = self._end_entry.get().strip()

        try:
            trades = self.controller.order_logger.get_trades_between(start, end)
        except Exception as e:
            logger.warning(f"Trade query error: {e}")
            trades = []

        self._trades = trades
        self._update_summary(trades)
        self._update_breakdown(trades)
        self._update_table(trades)
        self._load_config_periods()
        self._update_config_comparison(trades)

    def _update_summary(self, trades: list) -> None:
        if not trades:
            for card in self._summary_cards.values():
                card.configure(text="--")
            return

        total = len(trades)
        pnls = [t.get("pnl_usdt", 0) for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p < 0)
        win_rate = (wins / total * 100) if total > 0 else 0

        total_profit = sum(p for p in pnls if p > 0)
        total_loss = sum(p for p in pnls if p < 0)
        total_pnl = sum(pnls)
        total_fee = sum(t.get("fee_usdt", 0) for t in trades)
        total_volume = sum(t.get("notional_usdt", 0) or
                           (t.get("margin_usdt", 0) * t.get("leverage", 1))
                           for t in trades)
        profit_factor = abs(total_profit / total_loss) if total_loss != 0 else 0.0

        avg_hold = sum(t.get("hold_seconds", 0) for t in trades) / total
        leverages = [t.get("leverage", 1) for t in trades if t.get("leverage", 0) > 0]
        avg_lev = sum(leverages) / len(leverages) if leverages else 0

        best = max(pnls)
        worst = min(pnls)
        avg_win = total_profit / wins if wins > 0 else 0
        avg_loss = total_loss / losses if losses > 0 else 0

        # Long/Short sayisi
        long_count = sum(1 for t in trades
                         if "Buy" in t.get("side", "") or "LONG" in t.get("side", "").upper())
        short_count = total - long_count

        # Exit reason bazli PnL toplami
        exit_pnl = {}
        exit_count = {}
        for t in trades:
            r = t.get("exit_reason", "unknown")
            exit_pnl[r] = exit_pnl.get(r, 0) + t.get("pnl_usdt", 0)
            exit_count[r] = exit_count.get(r, 0) + 1

        sl_pnl = exit_pnl.get("STOP_LOSS", 0)
        tp_pnl = exit_pnl.get("TAKE_PROFIT", 0) + exit_pnl.get("PARTIAL_TP", 0)
        trail_pnl = (exit_pnl.get("TRAILING_STOP", 0) +
                     exit_pnl.get("TRAILING_RENEW", 0))
        liq_pnl = (exit_pnl.get("EMERGENCY_ANTI_LIQ", 0) +
                   exit_pnl.get("LIQUIDATION", 0))
        signal_pnl = (exit_pnl.get("CONFLUENCE_REVERSAL", 0) +
                      exit_pnl.get("DIVERGENCE_WARNING", 0))

        sl_n = exit_count.get("STOP_LOSS", 0)
        tp_n = exit_count.get("TAKE_PROFIT", 0) + exit_count.get("PARTIAL_TP", 0)
        trail_n = exit_count.get("TRAILING_STOP", 0) + exit_count.get("TRAILING_RENEW", 0)
        liq_n = exit_count.get("EMERGENCY_ANTI_LIQ", 0) + exit_count.get("LIQUIDATION", 0)
        signal_n = (exit_count.get("CONFLUENCE_REVERSAL", 0) +
                    exit_count.get("DIVERGENCE_WARNING", 0))

        # Max consecutive win/loss streak
        max_w_streak = max_l_streak = cur_w = cur_l = 0
        for p in pnls:
            if p > 0:
                cur_w += 1
                cur_l = 0
            elif p < 0:
                cur_l += 1
                cur_w = 0
            else:
                cur_w = cur_l = 0
            max_w_streak = max(max_w_streak, cur_w)
            max_l_streak = max(max_l_streak, cur_l)

        # Max drawdown (equity curve)
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            equity += p
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        # Best coin
        coin_pnl = {}
        for t in trades:
            sym = t.get("symbol", "").replace("USDT", "")
            coin_pnl[sym] = coin_pnl.get(sym, 0) + t.get("pnl_usdt", 0)
        best_coin = max(coin_pnl, key=coin_pnl.get) if coin_pnl else "--"
        best_coin_pnl = coin_pnl.get(best_coin, 0)

        # ── Row 1 ──
        self._summary_cards["total_trades"].configure(
            text=f"{total} ({wins}K/{losses}Z)")
        self._summary_cards["total_volume"].configure(
            text=f"${total_volume:.2f}")
        self._summary_cards["win_rate"].configure(
            text=f"%{win_rate:.0f}",
            text_color="#00C853" if win_rate >= 50 else "#FF9800" if win_rate >= 30 else "#FF1744")
        self._summary_cards["total_profit"].configure(
            text=f"${total_profit:+.4f}")
        self._summary_cards["total_loss"].configure(
            text=f"${total_loss:+.4f}")
        self._summary_cards["total_pnl"].configure(
            text=f"${total_pnl:+.4f}",
            text_color="#00C853" if total_pnl >= 0 else "#FF1744")
        self._summary_cards["total_fee"].configure(
            text=f"${total_fee:.4f}")
        pf_color = "#00C853" if profit_factor >= 1.5 else "#FF9800" if profit_factor >= 1.0 else "#FF1744"
        self._summary_cards["profit_factor"].configure(
            text=f"{profit_factor:.2f}", text_color=pf_color)

        # ── Row 2 ──
        self._summary_cards["sl_total"].configure(
            text=f"${sl_pnl:+.4f} ({sl_n})")
        self._summary_cards["tp_total"].configure(
            text=f"${tp_pnl:+.4f} ({tp_n})")
        self._summary_cards["trail_total"].configure(
            text=f"${trail_pnl:+.4f} ({trail_n})")
        self._summary_cards["liq_total"].configure(
            text=f"${liq_pnl:+.4f} ({liq_n})",
            text_color="#D50000" if liq_n > 0 else "gray")
        self._summary_cards["signal_total"].configure(
            text=f"${signal_pnl:+.4f} ({signal_n})")
        avg_m = int(avg_hold // 60)
        self._summary_cards["avg_hold"].configure(text=f"{avg_m}dk")
        self._summary_cards["avg_leverage"].configure(text=f"{avg_lev:.0f}x")
        self._summary_cards["long_short"].configure(
            text=f"{long_count}L / {short_count}S")

        # ── Row 3 ──
        self._summary_cards["best_trade"].configure(text=f"${best:+.4f}")
        self._summary_cards["worst_trade"].configure(text=f"${worst:+.4f}")
        self._summary_cards["avg_win"].configure(text=f"${avg_win:+.4f}")
        self._summary_cards["avg_loss"].configure(text=f"${avg_loss:+.4f}")
        self._summary_cards["max_win_streak"].configure(text=f"{max_w_streak}")
        self._summary_cards["max_loss_streak"].configure(
            text=f"{max_l_streak}",
            text_color="#FF1744" if max_l_streak >= 3 else "#FF9800" if max_l_streak >= 2 else "gray")
        self._summary_cards["max_drawdown"].configure(
            text=f"${max_dd:.4f}",
            text_color="#D50000" if max_dd > 0.5 else "#FF9800" if max_dd > 0.1 else "gray")
        bc_text = f"{best_coin}" if best_coin != "--" else "--"
        if best_coin != "--":
            bc_text += f" ({best_coin_pnl:+.3f})"
        self._summary_cards["best_coin"].configure(text=bc_text)

    def _update_breakdown(self, trades: list) -> None:
        for w in self._breakdown_inner.winfo_children():
            w.destroy()

        if not trades:
            ctk.CTkLabel(self._breakdown_inner, text="Veri yok",
                         text_color="gray").pack()
            return

        ctk.CTkLabel(self._breakdown_inner, text="Cikis Nedeni Dagilimi:",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="white").pack(side="left", padx=(0, 15))

        # Count by exit reason
        reason_counts = {}
        reason_pnl = {}
        for t in trades:
            r = t.get("exit_reason", "unknown")
            reason_counts[r] = reason_counts.get(r, 0) + 1
            reason_pnl[r] = reason_pnl.get(r, 0) + t.get("pnl_usdt", 0)

        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            color = EXIT_COLORS.get(reason, "#607D8B")
            name = EXIT_NAMES_TR.get(reason, reason)
            pnl = reason_pnl.get(reason, 0)
            pnl_color = "#00C853" if pnl >= 0 else "#FF1744"

            tag = ctk.CTkFrame(self._breakdown_inner, fg_color=color,
                               corner_radius=4)
            tag.pack(side="left", padx=3, pady=2)
            ctk.CTkLabel(tag, text=f"{name}: {count}",
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color="white").pack(side="left", padx=6, pady=2)
            ctk.CTkLabel(tag, text=f"({pnl:+.3f}$)",
                         font=ctk.CTkFont(size=10),
                         text_color=pnl_color).pack(side="left", padx=(0, 6), pady=2)

    def _update_table(self, trades: list) -> None:
        for w in self._table_scroll.winfo_children():
            w.destroy()

        if not trades:
            ctk.CTkLabel(self._table_scroll, text="Secilen tarih araliginda islem bulunamadi.",
                         font=ctk.CTkFont(size=13), text_color="gray").pack(pady=30)
            return

        for t in trades:
            pnl = t.get("pnl_usdt", 0)
            row_color = "#0a2e0a" if pnl > 0 else "#2e0a0a" if pnl < 0 else "transparent"
            row = ctk.CTkFrame(self._table_scroll, fg_color=row_color, corner_radius=3)
            row.pack(fill="x", pady=1)

            # Format values
            open_t = t.get("open_time", "")[:16]
            close_t = t.get("close_time", "")[:16]
            symbol = t.get("symbol", "").replace("USDT", "")
            side = t.get("side", "")
            side_short = "LONG" if "Buy" in side or "LONG" in side.upper() else "SHORT"
            side_color = "#00C853" if side_short == "LONG" else "#FF1744"
            leverage = t.get("leverage", 1)
            margin = t.get("margin_usdt", 0)
            entry_p = t.get("entry_price", 0)
            exit_p = t.get("exit_price", 0)
            roi = t.get("roi_percent", 0)
            fee = t.get("fee_usdt", 0)
            reason = t.get("exit_reason", "")
            reason_name = EXIT_NAMES_TR.get(reason, reason[:14])
            reason_color = EXIT_COLORS.get(reason, "#607D8B")
            hold_s = t.get("hold_seconds", 0)
            hold_m = int(hold_s // 60)
            hold_sec = int(hold_s % 60)
            score = t.get("entry_score", 0)
            rsi = t.get("entry_rsi", 0)
            adx = t.get("entry_adx", 0)
            conf = t.get("entry_confluence", 0)
            regime = t.get("entry_regime", "")
            regime_conf = t.get("entry_regime_confidence", 0)

            pnl_color = "#00E676" if pnl > 0 else "#FF5252" if pnl < 0 else "gray"
            roi_color = "#00E676" if roi > 0 else "#FF5252" if roi < 0 else "gray"

            def fmt_price(p):
                if p == 0:
                    return "--"
                if p < 0.001:
                    return f"{p:.7f}"
                if p < 0.01:
                    return f"{p:.6f}"
                if p < 1:
                    return f"{p:.5f}"
                if p < 100:
                    return f"{p:.4f}"
                return f"{p:.2f}"

            # Regime display colors
            regime_colors = {
                "TRENDING": "#00E676", "RANGING": "#2196F3",
                "VOLATILE": "#FF9800", "BREAKOUT": "#FF1744",
                "CHOPPY": "#9C27B0", "GRAY": "#607D8B",
            }
            regime_short = regime.replace("TRENDING", "TREND").replace("RANGING", "RANGE") \
                                  .replace("VOLATILE", "VOLAT").replace("BREAKOUT", "BREAK") \
                                  .replace("CHOPPY", "CHOP")
            regime_color = regime_colors.get(regime, "#607D8B")
            regime_text = f"{regime_short}" if regime else "--"
            if regime_conf > 0:
                regime_text += f" {regime_conf:.0%}"

            vals = [
                (open_t, 130, "white"),
                (close_t, 130, "white"),
                (symbol, 90, "white"),
                (side_short, 65, side_color),
                (f"{leverage}x", 55, "white"),
                (f"${margin:.2f}", 65, "white"),
                (fmt_price(entry_p), 85, "white"),
                (fmt_price(exit_p), 85, "white"),
                (f"{pnl:+.4f}", 70, pnl_color),
                (f"{roi:+.1f}%", 60, roi_color),
                (f"${fee:.3f}", 55, "#FF9800"),
                (reason_name, 110, reason_color),
                (f"{hold_m}:{hold_sec:02d}", 60, "white"),
                (regime_text, 75, regime_color),
                (f"{score:+.0f}", 45, "#2196F3"),
                (f"{rsi:.0f}", 40, "#FFC107" if 30 < rsi < 70 else "#FF5252"),
                (f"{adx:.0f}", 40, "#00C853" if adx > 25 else "gray"),
                (f"{conf:+.0f}", 40, "#00E676" if conf > 0 else "#FF5252" if conf < 0 else "gray"),
            ]
            for text, w, color in vals:
                ctk.CTkLabel(row, text=text, width=w, text_color=color,
                             font=ctk.CTkFont(family="Consolas", size=10)
                             ).pack(side="left", padx=1)

    def _load_config_periods(self) -> None:
        """Load config snapshots into the dropdown."""
        try:
            snapshots = self.controller.order_logger.get_config_snapshots(limit=20)
            values = ["Tum Donemler"]
            for s in snapshots:
                ts = s.get("timestamp", "")[:16]
                src = s.get("change_source", "")
                summary = s.get("summary", "")[:40]
                sid = s.get("id", 0)
                values.append(f"#{sid} ({ts}) [{src}] {summary}")
            self._config_combo.configure(values=values)
        except Exception:
            pass

    def _on_config_period_change(self, value: str) -> None:
        """Filter trades by selected config period."""
        if value == "Tum Donemler" or not value:
            self._apply_filter()
            return

        try:
            # Extract snapshot ID from "#{id} (...)"
            snapshot_id = int(value.split("#")[1].split(" ")[0])
            trades = self.controller.order_logger.get_trades_by_config(snapshot_id)
            self._trades = trades
            self._update_summary(trades)
            self._update_breakdown(trades)
            self._update_table(trades)
        except Exception as e:
            logger.debug(f"Config period filter error: {e}")

    def _update_config_comparison(self, trades: list) -> None:
        """Show performance comparison across config periods."""
        for w in self._config_comparison_inner.winfo_children():
            w.destroy()

        if not trades:
            return

        # Group trades by config_snapshot_id
        by_config = {}
        for t in trades:
            cid = t.get("config_snapshot_id", 0)
            if cid not in by_config:
                by_config[cid] = []
            by_config[cid].append(t)

        if len(by_config) <= 1:
            ctk.CTkLabel(self._config_comparison_inner, text="Tek config donemi — karsilastirma yok",
                         text_color="gray", font=ctk.CTkFont(size=10)).pack(pady=2)
            return

        # Header
        header = ctk.CTkFrame(self._config_comparison_inner, fg_color="transparent")
        header.pack(fill="x")
        for text, w in [("Config #", 70), ("Islem", 50), ("Win%", 50), ("PnL $", 80), ("Fee $", 60), ("Ort ROI", 60)]:
            ctk.CTkLabel(header, text=text, width=w, font=ctk.CTkFont(size=10, weight="bold"),
                         text_color="#90CAF9").pack(side="left", padx=2)

        for cid, ctrades in sorted(by_config.items()):
            row = ctk.CTkFrame(self._config_comparison_inner, fg_color="#1a1a2e", corner_radius=3)
            row.pack(fill="x", pady=1)

            total = len(ctrades)
            wins = sum(1 for t in ctrades if t.get("pnl_usdt", 0) > 0)
            win_rate = (wins / total * 100) if total > 0 else 0
            total_pnl = sum(t.get("pnl_usdt", 0) for t in ctrades)
            total_fee = sum(t.get("fee_usdt", 0) for t in ctrades)
            rois = [t.get("roi_percent", 0) for t in ctrades]
            avg_roi = sum(rois) / len(rois) if rois else 0

            pnl_color = "#00E676" if total_pnl >= 0 else "#FF5252"
            wr_color = "#00C853" if win_rate >= 50 else "#FF9800" if win_rate >= 30 else "#FF1744"

            vals = [
                (f"#{cid}", 70, "white"),
                (f"{total}", 50, "white"),
                (f"{win_rate:.0f}%", 50, wr_color),
                (f"{total_pnl:+.4f}", 80, pnl_color),
                (f"{total_fee:.4f}", 60, "#FF9800"),
                (f"{avg_roi:+.1f}%", 60, pnl_color),
            ]
            for text, w, color in vals:
                ctk.CTkLabel(row, text=text, width=w, text_color=color,
                             font=ctk.CTkFont(family="Consolas", size=10)).pack(side="left", padx=2)

    def _import_from_binance(self) -> None:
        """Import historical trades from Binance API income history."""
        import threading

        self._import_status.configure(text="Binance'den veriler cekiliyor...",
                                       text_color="#FF9800")

        def _do_import():
            try:
                scanner = self.controller.scanner
                if not scanner or not hasattr(scanner, '_order_executor'):
                    self.after(0, lambda: self._import_status.configure(
                        text="Scanner/API bulunamadi!", text_color="#FF1744"))
                    return

                executor = scanner._order_executor
                if not hasattr(executor, '_rest'):
                    self.after(0, lambda: self._import_status.configure(
                        text="REST client bulunamadi!", text_color="#FF1744"))
                    return

                rest_client = executor._rest

                # Parse date range from filter fields
                start_str = self._start_entry.get().strip()
                end_str = self._end_entry.get().strip()

                try:
                    start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
                    start_ms = int(start_dt.timestamp() * 1000)
                except ValueError:
                    start_ms = 0

                try:
                    end_dt = datetime.strptime(end_str, "%Y-%m-%d %H:%M")
                    end_ms = int(end_dt.timestamp() * 1000)
                except ValueError:
                    end_ms = 0

                count = self.controller.order_logger.import_from_binance(
                    rest_client, start_ms=start_ms, end_ms=end_ms)

                self.after(0, lambda: self._import_status.configure(
                    text=f"{count} islem import edildi!", text_color="#00C853"))
                self.after(0, self._apply_filter)

            except Exception as e:
                logger.error(f"Binance import error: {e}")
                self.after(0, lambda: self._import_status.configure(
                    text=f"Hata: {e}", text_color="#FF1744"))

        threading.Thread(target=_do_import, daemon=True).start()
