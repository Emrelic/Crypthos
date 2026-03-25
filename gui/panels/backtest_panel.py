"""Backtest Panel — GUI for running and viewing backtest results.

Configurable parameters, progress tracking, results table with trade details.
"""
import threading
from datetime import datetime, timezone
import customtkinter as ctk
from loguru import logger

from backtest.engine import (
    BacktestEngine, BacktestConfig, BacktestResult, DEFAULT_SF_PARAMS,
)

# ═══ Table Layout ═══
BT_HEADERS = [
    "#", "Tarih", "Sembol", "Yon", "Lev", "Giris", "Cikis",
    "Sebep", "Sure", "ROI%", "Skor", "EV%",
]
BT_WIDTHS = [
    28, 110, 80, 48, 36, 80, 80,
    72, 64, 60, 44, 48,
]


class BacktestPanel(ctk.CTkFrame):
    """Backtest configuration, execution, and results panel."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._engine: BacktestEngine | None = None
        self._thread: threading.Thread | None = None
        self._rows = []
        self._build_ui()

    def _build_ui(self) -> None:
        # ═══ TITLE ═══
        ctk.CTkLabel(
            self, text="Backtest - Gecmise Donuk Test",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#FFA726",
        ).pack(anchor="w", padx=8, pady=(4, 0))

        # ═══ CONFIG BAR ═══
        cfg_bar = ctk.CTkFrame(self, height=40)
        cfg_bar.pack(fill="x", padx=5, pady=(3, 1))
        cfg_bar.pack_propagate(False)

        self._params = {}
        param_defs = [
            ("days_back", "Gun:", "30", 36),
            ("check_interval", "Aralik(dk):", "15", 36),
            ("top_coins", "Coin:", "15", 36),
            ("min_tf_uyum", "TF Uyum:", "4", 28),
            ("min_skor", "Min Skor:", "85", 36),
            ("ev_min", "Min EV%:", "15", 36),
            ("rsi_long", "RSI Long:", "60", 32),
            ("rsi_short", "RSI Short:", "40", 32),
        ]

        for key, label, default, width in param_defs:
            ctk.CTkLabel(
                cfg_bar, text=label,
                font=ctk.CTkFont(size=11), text_color="#90A4AE",
            ).pack(side="left", padx=(6, 1), pady=4)
            var = ctk.StringVar(value=default)
            ctk.CTkEntry(
                cfg_bar, textvariable=var,
                width=width, height=26, font=ctk.CTkFont(size=11),
            ).pack(side="left", padx=(0, 2), pady=4)
            self._params[key] = var

        # MACD momentum checkbox
        self._macd_mom_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            cfg_bar, text="MACD Momentum",
            variable=self._macd_mom_var,
            font=ctk.CTkFont(size=11), height=26,
            checkbox_width=18, checkbox_height=18,
        ).pack(side="left", padx=(8, 2), pady=4)

        # Volume spike checkbox
        self._vol_spike_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            cfg_bar, text="Hacim Spike",
            variable=self._vol_spike_var,
            font=ctk.CTkFont(size=11), height=26,
            checkbox_width=18, checkbox_height=18,
        ).pack(side="left", padx=(4, 2), pady=4)

        # ═══ ACTION BAR ═══
        act_bar = ctk.CTkFrame(self, height=36)
        act_bar.pack(fill="x", padx=5, pady=(1, 1))
        act_bar.pack_propagate(False)

        self._start_btn = ctk.CTkButton(
            act_bar, text="Baslat", width=80, height=28,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#2E7D32", hover_color="#388E3C",
            command=self._on_start,
        )
        self._start_btn.pack(side="left", padx=(8, 4), pady=4)

        self._cancel_btn = ctk.CTkButton(
            act_bar, text="Iptal", width=60, height=28,
            font=ctk.CTkFont(size=11),
            fg_color="#C62828", hover_color="#D32F2F",
            command=self._on_cancel, state="disabled",
        )
        self._cancel_btn.pack(side="left", padx=(0, 8), pady=4)

        self._progress = ctk.CTkProgressBar(
            act_bar, width=300, height=14, mode="determinate",
        )
        self._progress.pack(side="left", padx=(0, 8), pady=8)
        self._progress.set(0)

        self._status_var = ctk.StringVar(value="Hazir")
        ctk.CTkLabel(
            act_bar, textvariable=self._status_var,
            font=ctk.CTkFont(size=11), text_color="#78909C",
        ).pack(side="left", padx=4, pady=4)

        # ═══ SUMMARY BAR ═══
        self._summary_bar = ctk.CTkFrame(self, height=32)
        self._summary_bar.pack(fill="x", padx=5, pady=(1, 1))
        self._summary_bar.pack_propagate(False)

        self._summary_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            self._summary_bar, textvariable=self._summary_var,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#FFA726",
        ).pack(side="left", padx=8, pady=4)

        # Reject stats button
        self._reject_btn = ctk.CTkButton(
            self._summary_bar, text="Red Sebepleri", width=100, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="#37474F", hover_color="#455A64",
            command=self._show_reject_stats, state="disabled",
        )
        self._reject_btn.pack(side="right", padx=8, pady=4)

        # ═══ TABLE ═══
        table_frame = ctk.CTkFrame(self)
        table_frame.pack(fill="both", expand=True, padx=3, pady=(1, 3))

        # Header
        hdr = ctk.CTkFrame(table_frame, fg_color="transparent")
        hdr.pack(fill="x", padx=2)
        hdr_font = ctk.CTkFont(size=12, weight="bold")
        hdr_colors = {
            "#": "#B0BEC5", "Tarih": "#B0BEC5", "Sembol": "#81D4FA",
            "Yon": "#CE93D8", "Lev": "#FFD54F", "Giris": "#B0BEC5",
            "Cikis": "#B0BEC5", "Sebep": "#FF8A65", "Sure": "#B0BEC5",
            "ROI%": "#00E676", "Skor": "#FFD54F", "EV%": "#4FC3F7",
        }
        for h, w in zip(BT_HEADERS, BT_WIDTHS):
            ctk.CTkLabel(
                hdr, text=h, width=w, font=hdr_font,
                text_color=hdr_colors.get(h, "#B0BEC5"),
            ).pack(side="left", padx=0)

        self._scroll = ctk.CTkScrollableFrame(table_frame, height=400)
        self._scroll.pack(fill="both", expand=True, padx=2)

        self._empty_label = ctk.CTkLabel(
            self._scroll,
            text="Ayarlari yapin ve 'Baslat' butonuna basin",
            font=ctk.CTkFont(size=12), text_color="#546E7A",
        )
        self._empty_label.pack(pady=20)

        # ═══ NOTE BAR ═══
        note = ctk.CTkLabel(
            self,
            text="NOT: Orderbook filtresi atlanir (gecmis veri yok). "
                 "FR=0 varsayilir. Hacim spike 5m proxy kullanir.",
            font=ctk.CTkFont(size=10), text_color="#546E7A",
        )
        note.pack(anchor="w", padx=8, pady=(0, 3))

    # ═══ Actions ═══

    def _on_start(self) -> None:
        """Start backtest in background thread."""
        # Build config from GUI
        try:
            cfg = BacktestConfig(
                days_back=int(self._params["days_back"].get()),
                check_interval_min=int(self._params["check_interval"].get()),
                top_coins=int(self._params["top_coins"].get()),
                min_tf_uyum=int(self._params["min_tf_uyum"].get()),
            )
        except ValueError:
            self._status_var.set("HATA: Gecersiz parametre!")
            return

        # Override system params from GUI
        sf = dict(DEFAULT_SF_PARAMS)
        try:
            sf["min_skor"] = float(self._params["min_skor"].get())
            sf["ev_min_pct"] = float(self._params["ev_min"].get())
            sf["rsi_long_esik"] = float(self._params["rsi_long"].get())
            sf["rsi_short_esik"] = float(self._params["rsi_short"].get())
        except ValueError:
            pass
        sf["macd_momentum_required"] = self._macd_mom_var.get()
        sf["volume_spike_required"] = self._vol_spike_var.get()
        cfg.system_params = sf

        # Clear previous
        self._clear_table()
        self._summary_var.set("")
        self._reject_btn.configure(state="disabled")

        # Create engine
        self._engine = BacktestEngine(cfg)
        self._cancelled = False

        # UI state
        self._start_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal")
        self._progress.set(0)

        # Launch thread
        self._thread = threading.Thread(target=self._run_backtest, daemon=True)
        self._thread.start()
        self.after(500, self._poll)

    def _on_cancel(self) -> None:
        if self._engine:
            self._engine.cancel()
        self._cancel_btn.configure(state="disabled")
        self._status_var.set("Iptal ediliyor...")

    def _run_backtest(self) -> None:
        """Runs in background thread."""
        try:
            self._engine.run()
        except Exception as e:
            logger.error(f"Backtest error: {e}")

    def _poll(self) -> None:
        """Poll engine progress from main thread."""
        if self._engine is None:
            return

        # Update progress
        self._progress.set(self._engine.progress_pct)
        self._status_var.set(self._engine.progress_msg)

        # Check completion
        if self._engine.result is not None:
            self._on_complete(self._engine.result)
            return

        # Continue polling
        self.after(500, self._poll)

    def _on_complete(self, result: BacktestResult) -> None:
        """Handle backtest completion."""
        self._start_btn.configure(state="normal")
        self._cancel_btn.configure(state="disabled")
        self._progress.set(1.0)

        if not result.trades:
            self._status_var.set(
                f"Tamamlandi — sinyal bulunamadi "
                f"({result.total_checks} kontrol noktasi)")
            self._reject_btn.configure(state="normal")
            self._last_result = result
            return

        # Render trades
        self._render_trades(result)

        # Summary
        w = "+" if result.total_roi > 0 else ""
        self._summary_var.set(
            f"Toplam: {result.total_trades} trade  |  "
            f"Kazanc: {result.win_count}  |  "
            f"Kayip: {result.loss_count}  |  "
            f"Win Rate: {result.win_rate:.0f}%  |  "
            f"Toplam ROI: {w}{result.total_roi:.1f}%  |  "
            f"Ort ROI: {result.avg_roi:+.1f}%"
        )
        self._reject_btn.configure(state="normal")
        self._last_result = result
        self._status_var.set(f"Tamamlandi! {result.total_checks} kontrol noktasi")

    # ═══ Table Rendering ═══

    def _clear_table(self) -> None:
        for row in self._rows:
            row.destroy()
        self._rows.clear()
        if hasattr(self, '_empty_label') and self._empty_label.winfo_exists():
            self._empty_label.destroy()

    def _render_trades(self, result: BacktestResult) -> None:
        self._clear_table()
        font = ctk.CTkFont(size=11)
        font_bold = ctk.CTkFont(size=11, weight="bold")

        for i, trade in enumerate(result.trades):
            row = ctk.CTkFrame(self._scroll, fg_color="transparent", height=24)
            row.pack(fill="x", padx=1, pady=0)
            row.pack_propagate(False)
            self._rows.append(row)

            dt = datetime.fromtimestamp(
                trade.time_ms / 1000, tz=timezone.utc)
            date_str = dt.strftime("%m-%d %H:%M")

            # Direction colors
            if trade.direction == "LONG":
                dir_color = "#00E676"
                dir_text = "LONG"
            else:
                dir_color = "#FF5252"
                dir_text = "SHORT"

            # ROI color
            if trade.roi_net > 0:
                roi_color = "#00E676"
                roi_text = f"+{trade.roi_net:.1f}"
            else:
                roi_color = "#FF5252"
                roi_text = f"{trade.roi_net:.1f}"

            # Exit reason color
            reason_colors = {
                "TRAILING": "#00E676", "SL": "#FF5252",
                "EMERGENCY": "#FF1744", "TIME_LIMIT": "#FFA726",
                "DATA_END": "#78909C",
            }
            reason_color = reason_colors.get(trade.exit_reason, "#B0BEC5")

            values = [
                (str(i + 1), "#B0BEC5", font),
                (date_str, "#B0BEC5", font),
                (trade.symbol.replace("USDT", ""), "#81D4FA", font_bold),
                (dir_text, dir_color, font_bold),
                (f"{trade.leverage}x", "#FFD54F", font),
                (f"{trade.entry_price:.2f}", "#B0BEC5", font),
                (f"{trade.exit_price:.2f}", "#B0BEC5", font),
                (trade.exit_reason, reason_color, font_bold),
                (trade.hold_str, "#B0BEC5", font),
                (roi_text, roi_color, font_bold),
                (f"{trade.score:.0f}", "#FFD54F", font),
                (f"{trade.ev_pct:.1f}", "#4FC3F7", font),
            ]

            for (text, color, f), w in zip(values, BT_WIDTHS):
                ctk.CTkLabel(
                    row, text=text, width=w, font=f,
                    text_color=color,
                ).pack(side="left", padx=0)

    def _show_reject_stats(self) -> None:
        """Show reject statistics in a popup window."""
        if not hasattr(self, '_last_result') or not self._last_result:
            return

        result = self._last_result
        rs = result.reject_stats
        if not rs:
            return

        # Toplevel popup
        popup = ctk.CTkToplevel(self)
        popup.title("Red Sebepleri Dagilimi")
        popup.geometry("400x350")
        popup.attributes("-topmost", True)

        ctk.CTkLabel(
            popup, text="Filtre Red Dagilimi",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#FFA726",
        ).pack(pady=(10, 5))

        total = sum(rs.values())
        scroll = ctk.CTkScrollableFrame(popup, height=260)
        scroll.pack(fill="both", expand=True, padx=10, pady=5)

        font = ctk.CTkFont(size=11)
        font_bold = ctk.CTkFont(size=11, weight="bold")

        for reason, count in sorted(rs.items(), key=lambda x: -x[1]):
            pct = count / total * 100 if total > 0 else 0
            row = ctk.CTkFrame(scroll, fg_color="transparent", height=22)
            row.pack(fill="x", pady=0)
            row.pack_propagate(False)
            ctk.CTkLabel(
                row, text=reason, width=100, font=font_bold,
                text_color="#81D4FA", anchor="w",
            ).pack(side="left", padx=(4, 8))
            ctk.CTkLabel(
                row, text=f"{count:,}", width=60, font=font,
                text_color="#B0BEC5", anchor="e",
            ).pack(side="left", padx=(0, 4))
            ctk.CTkLabel(
                row, text=f"({pct:.1f}%)", width=60, font=font,
                text_color="#78909C", anchor="w",
            ).pack(side="left", padx=(0, 4))

            # Simple bar
            bar_w = int(pct * 1.5)
            if bar_w > 0:
                bar = ctk.CTkFrame(
                    row, fg_color="#FFA726", width=bar_w, height=10,
                    corner_radius=2,
                )
                bar.pack(side="left", padx=2, pady=6)
