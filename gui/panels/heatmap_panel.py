"""Heatmap Panel — TF Indicator Heatmap Tablosu.

Bir coin icin 10 timeframe'de (1m -> 1d) EMA/MACD/RSI sinyallerini
dakika bazinda gorsellestiren isi haritasi paneli.
"""
import threading
import datetime
import customtkinter as ctk
from loguru import logger

from backtest.tf_heatmap import TFHeatmapEngine, HeatmapData

# ═══ Column Layout ═══
HM_TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d"]
HM_COL_TIME_W = 60
HM_COL_TF_W = 72
HM_COL_TOTAL_W = 60

# ═══ Color Constants ═══
_CLR_AL = "#00E676"       # green — AL (buy)
_CLR_SAT = "#FF5252"      # red — SAT (sell)
_CLR_NOTR = "#546E7A"     # gray — NOTR (neutral)

_ALIGN_3 = {"border": "#00E676", "bg": "#0a2a0a"}   # 3/3 uyum
_ALIGN_2 = {"border": "#FFD54F", "bg": "#2a2a0a"}   # 2/3 uyum
_ALIGN_1 = {"border": "#FF8A65", "bg": "#2a1a0a"}   # 1/3 uyum
_ALIGN_0 = {"border": "#37474F", "bg": "transparent"}  # no alignment

_ROW_EVEN = "#1c2d4d"
_ROW_ODD = "#1a1a2e"


def _vote_color(vote: int) -> str:
    """Return text color for a vote value."""
    if vote > 0:
        return _CLR_AL
    elif vote < 0:
        return _CLR_SAT
    return _CLR_NOTR


def _alignment_style(votes: list) -> dict:
    """Return border/bg dict based on vote alignment."""
    pos = sum(1 for v in votes if v > 0)
    neg = sum(1 for v in votes if v < 0)
    best = max(pos, neg)
    if best == 3:
        return _ALIGN_3
    elif best == 2:
        return _ALIGN_2
    elif best == 1:
        return _ALIGN_1
    return _ALIGN_0


class HeatmapPanel(ctk.CTkFrame):
    """TF Indicator Heatmap panel — paginated minute-by-minute indicator view."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)

        self._engine: TFHeatmapEngine | None = None
        self._data: HeatmapData | None = None
        self._minute_keys: list[int] = []
        self._running = False
        self._cancel_flag = False
        self._progress_value = 0.0
        self._progress_text = "Hazir"
        self._rows: list = []  # list of (row_frame, time_label, cell_frames, total_label)

        self._build_ui()

    # ═══════════════════════════════════════════════════════════════════
    # UI Build
    # ═══════════════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        # ═══ TITLE ═══
        ctk.CTkLabel(self, text="TF Indicator Heatmap",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color="#81D4FA").pack(anchor="w", padx=8, pady=(4, 0))

        # ═══ TOP BAR: Coin + Days + Buttons + Progress ═══
        top_bar = ctk.CTkFrame(self, height=44)
        top_bar.pack(fill="x", padx=5, pady=(3, 1))
        top_bar.pack_propagate(False)

        ctk.CTkLabel(top_bar, text="Coin:",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#B0BEC5").pack(side="left", padx=(8, 4), pady=6)

        self._symbol_var = ctk.StringVar(value="BTCUSDT")
        self._symbol_combo = ctk.CTkComboBox(
            top_bar, variable=self._symbol_var,
            values=["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT",
                    "XRPUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT"],
            width=140, height=30, font=ctk.CTkFont(size=12),
            state="normal",
            dropdown_font=ctk.CTkFont(size=12),
        )
        self._symbol_combo.pack(side="left", padx=(0, 8), pady=5)

        ctk.CTkLabel(top_bar, text="Gun:",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#B0BEC5").pack(side="left", padx=(0, 4), pady=6)

        self._days_var = ctk.StringVar(value="7")
        ctk.CTkEntry(top_bar, textvariable=self._days_var,
                     width=40, height=30,
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 8), pady=5)

        # Hesapla button
        self._calc_btn = ctk.CTkButton(
            top_bar, text="Hesapla", width=90, height=30,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#1565C0", hover_color="#1976D2",
            command=self._on_calculate,
        )
        self._calc_btn.pack(side="left", padx=(0, 4), pady=5)

        # Iptal button
        self._cancel_btn = ctk.CTkButton(
            top_bar, text="Iptal", width=70, height=30,
            font=ctk.CTkFont(size=11),
            fg_color="#C62828", hover_color="#D32F2F",
            state="disabled",
            command=self._on_cancel,
        )
        self._cancel_btn.pack(side="left", padx=(0, 8), pady=5)

        # Progress bar
        self._progress_bar = ctk.CTkProgressBar(top_bar, width=160, height=14)
        self._progress_bar.pack(side="left", padx=(0, 8), pady=8)
        self._progress_bar.set(0)

        # Status label
        self._status_var = ctk.StringVar(value="Hazir")
        ctk.CTkLabel(
            top_bar, textvariable=self._status_var,
            font=ctk.CTkFont(size=11), text_color="#78909C",
        ).pack(side="right", padx=8, pady=6)

        # ═══ NAVIGATION BAR ═══
        nav_bar = ctk.CTkFrame(self, height=40)
        nav_bar.pack(fill="x", padx=5, pady=(1, 1))
        nav_bar.pack_propagate(False)

        ctk.CTkLabel(nav_bar, text="Tarih:",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#90A4AE").pack(side="left", padx=(8, 4), pady=6)

        self._date_var = ctk.StringVar(value="--")
        self._date_combo = ctk.CTkComboBox(
            nav_bar, variable=self._date_var,
            values=["--"], width=110, height=28,
            font=ctk.CTkFont(size=11), state="readonly",
            dropdown_font=ctk.CTkFont(size=11),
            command=self._on_date_changed,
        )
        self._date_combo.pack(side="left", padx=(0, 8), pady=5)

        ctk.CTkLabel(nav_bar, text="Saat:",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#90A4AE").pack(side="left", padx=(0, 4), pady=6)

        self._hour_var = ctk.StringVar(value="0")
        self._hour_combo = ctk.CTkComboBox(
            nav_bar, variable=self._hour_var,
            values=[str(h) for h in range(24)],
            width=60, height=28,
            font=ctk.CTkFont(size=11), state="readonly",
            dropdown_font=ctk.CTkFont(size=11),
            command=self._on_hour_changed,
        )
        self._hour_combo.pack(side="left", padx=(0, 8), pady=5)

        # Time slider (0-1439 minutes in a day)
        self._slider_var = ctk.IntVar(value=0)
        self._slider = ctk.CTkSlider(
            nav_bar, from_=0, to=1439,
            variable=self._slider_var,
            width=200, height=16,
            command=self._on_slider_changed,
        )
        self._slider.pack(side="left", padx=(0, 8), pady=8)

        # Current time display
        self._time_display_var = ctk.StringVar(value="--:--")
        ctk.CTkLabel(
            nav_bar, textvariable=self._time_display_var,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#81D4FA",
        ).pack(side="left", padx=(0, 12), pady=6)

        # Rows to show
        ctk.CTkLabel(nav_bar, text="Satir:",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#90A4AE").pack(side="left", padx=(0, 4), pady=6)

        self._rows_var = ctk.StringVar(value="60")
        ctk.CTkComboBox(
            nav_bar, variable=self._rows_var,
            values=["30", "60", "120", "240"],
            width=70, height=28,
            font=ctk.CTkFont(size=11), state="readonly",
            dropdown_font=ctk.CTkFont(size=11),
            command=self._on_rows_changed,
        ).pack(side="left", padx=(0, 8), pady=5)

        # ═══ TABLE HEADER ═══
        table_frame = ctk.CTkFrame(self)
        table_frame.pack(fill="both", expand=True, padx=3, pady=(1, 1))

        self._build_table_header(table_frame)

        # ═══ TABLE BODY (scrollable) ═══
        self._scroll = ctk.CTkScrollableFrame(table_frame, height=500)
        self._scroll.pack(fill="both", expand=True, padx=2)

        self._show_empty_message("Coin secip 'Hesapla' butonuna basin")

        # ═══ SUMMARY BAR (bottom) ═══
        self._summary_frame = ctk.CTkFrame(self, height=28)
        self._summary_frame.pack(fill="x", padx=5, pady=(1, 3))
        self._summary_frame.pack_propagate(False)

        self._summary_var = ctk.StringVar(value="")
        ctk.CTkLabel(
            self._summary_frame, textvariable=self._summary_var,
            font=ctk.CTkFont(size=11), text_color="#90A4AE",
        ).pack(side="left", padx=8, pady=4)

    def _build_table_header(self, parent) -> None:
        """Build the fixed header row: Zaman + 10 TFs + Toplam."""
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x", padx=2)
        bold = ctk.CTkFont(size=12, weight="bold")

        # Zaman column
        ctk.CTkLabel(hdr, text="Zaman", width=HM_COL_TIME_W, font=bold,
                     text_color="#B0BEC5").pack(side="left", padx=0)

        # TF columns
        tf_colors = {
            "1m": "#78909C", "5m": "#90A4AE", "15m": "#4FC3F7", "30m": "#4FC3F7",
            "1h": "#FFD54F", "2h": "#FFD54F", "4h": "#FF8A65", "8h": "#FF8A65",
            "12h": "#CE93D8", "1d": "#CE93D8",
        }
        for tf in HM_TIMEFRAMES:
            color = tf_colors.get(tf, "#7799BB")
            ctk.CTkLabel(hdr, text=tf, width=HM_COL_TF_W, font=bold,
                         text_color=color).pack(side="left", padx=0)

        # Toplam column
        ctk.CTkLabel(hdr, text="Toplam", width=HM_COL_TOTAL_W, font=bold,
                     text_color="#FFD54F").pack(side="left", padx=0)

    # ═══════════════════════════════════════════════════════════════════
    # Computation
    # ═══════════════════════════════════════════════════════════════════

    def _on_calculate(self) -> None:
        """Start heatmap computation in a background thread."""
        symbol = self._symbol_var.get().strip().upper()
        if not symbol:
            self._status_var.set("Coin secin!")
            return
        if not symbol.endswith("USDT"):
            symbol += "USDT"

        try:
            days = int(self._days_var.get())
        except (ValueError, TypeError):
            days = 7

        self._running = True
        self._cancel_flag = False
        self._progress_value = 0.0
        self._progress_text = "Baslatiliyor..."
        self._calc_btn.configure(state="disabled", text="Hesaplaniyor...")
        self._cancel_btn.configure(state="normal")
        self._progress_bar.set(0)

        thread = threading.Thread(
            target=self._run_computation, args=(symbol, days), daemon=True
        )
        thread.start()
        self._poll_progress()

    def _run_computation(self, symbol: str, days: int) -> None:
        """Background thread: run TFHeatmapEngine."""
        try:
            def progress_cb(msg: str, pct: float):
                self._progress_value = pct
                self._progress_text = msg

            self._engine = TFHeatmapEngine(on_progress=progress_cb)
            self._data = self._engine.compute(symbol=symbol, days_back=days)

            if self._cancel_flag:
                self._engine.cancel()
                self._progress_text = "Iptal edildi"
                self._data = None
            else:
                self._progress_value = 1.0
                self._progress_text = "Tamamlandi"

        except Exception as e:
            logger.error(f"Heatmap computation error: {e}")
            self._progress_text = f"Hata: {e}"
            self._data = None
        finally:
            self._running = False

    def _poll_progress(self) -> None:
        """Poll computation progress from the main thread."""
        self._progress_bar.set(self._progress_value)
        self._status_var.set(self._progress_text)

        if self._running:
            self.after(500, self._poll_progress)
            return

        # Computation finished
        self._calc_btn.configure(state="normal", text="Hesapla")
        self._cancel_btn.configure(state="disabled")

        if self._data is not None:
            self._on_data_ready()

    def _on_cancel(self) -> None:
        """Cancel ongoing computation."""
        self._cancel_flag = True
        if self._engine:
            self._engine.cancel()
        self._status_var.set("Iptal ediliyor...")

    # ═══════════════════════════════════════════════════════════════════
    # Data Ready — populate navigation
    # ═══════════════════════════════════════════════════════════════════

    def _on_data_ready(self) -> None:
        """Called when computation is done and data is available."""
        self._minute_keys = self._data.get_minute_range()
        if not self._minute_keys:
            self._show_empty_message("Veri bulunamadi")
            return

        # Build date list from minute keys
        dates_set = set()
        for ms in self._minute_keys:
            dt = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
            dates_set.add(dt.strftime("%Y-%m-%d"))

        dates_sorted = sorted(dates_set)
        self._date_combo.configure(values=dates_sorted)
        if dates_sorted:
            self._date_var.set(dates_sorted[-1])  # start with latest date

        self._status_var.set(
            f"Hazir: {len(self._minute_keys)} dakika, "
            f"{len(dates_sorted)} gun"
        )
        self._render_page()

    # ═══════════════════════════════════════════════════════════════════
    # Navigation Events
    # ═══════════════════════════════════════════════════════════════════

    def _on_date_changed(self, _=None) -> None:
        self._render_page()

    def _on_hour_changed(self, _=None) -> None:
        try:
            hour = int(self._hour_var.get())
            self._slider_var.set(hour * 60)
        except (ValueError, TypeError):
            pass
        self._render_page()

    def _on_slider_changed(self, _=None) -> None:
        minute_of_day = int(self._slider_var.get())
        hour = minute_of_day // 60
        self._hour_var.set(str(hour))
        self._render_page()

    def _on_rows_changed(self, _=None) -> None:
        self._render_page()

    # ═══════════════════════════════════════════════════════════════════
    # Page Rendering
    # ═══════════════════════════════════════════════════════════════════

    def _render_page(self) -> None:
        """Render the visible page of heatmap rows."""
        if self._data is None or not self._minute_keys:
            return

        # Parse selected date + slider offset
        date_str = self._date_var.get()
        if date_str == "--" or not date_str:
            return

        minute_of_day = int(self._slider_var.get())
        hour = minute_of_day // 60
        minute = minute_of_day % 60

        try:
            rows_to_show = int(self._rows_var.get())
        except (ValueError, TypeError):
            rows_to_show = 60

        # Calculate start timestamp
        try:
            base_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            base_dt = base_dt.replace(
                hour=hour, minute=minute,
                tzinfo=datetime.timezone.utc
            )
            start_ms = int(base_dt.timestamp() * 1000)
        except (ValueError, TypeError):
            return

        # Update time display
        self._time_display_var.set(
            f"{date_str} {hour:02d}:{minute:02d}"
        )

        # Find the start index in minute_keys
        start_idx = self._find_nearest_index(start_ms)

        # Slice the data
        end_idx = min(start_idx + rows_to_show, len(self._minute_keys))
        visible_keys = self._minute_keys[start_idx:end_idx]

        if not visible_keys:
            self._show_empty_message("Bu aralikta veri yok")
            return

        # Clear old rows and render new ones
        self._clear_rows()
        self._render_rows(visible_keys)

        # Update summary
        self._update_summary(visible_keys)

    def _find_nearest_index(self, target_ms: int) -> int:
        """Binary search for the nearest minute key index."""
        keys = self._minute_keys
        if not keys:
            return 0
        lo, hi = 0, len(keys) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if keys[mid] < target_ms:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _clear_rows(self) -> None:
        """Destroy all existing row widgets."""
        for row_data in self._rows:
            row_data[0].destroy()
        self._rows.clear()

    def _render_rows(self, minute_keys: list[int]) -> None:
        """Create row widgets for the given minute timestamps."""
        small_font = ctk.CTkFont(size=10)
        indicator_font = ctk.CTkFont(size=9, weight="bold")

        for row_idx, ms in enumerate(minute_keys):
            bg = _ROW_EVEN if row_idx % 2 == 0 else _ROW_ODD

            row_frame = ctk.CTkFrame(self._scroll, fg_color=bg)
            row_frame.pack(fill="x", pady=0)

            # Time column
            dt = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
            time_str = dt.strftime("%H:%M")
            time_lbl = ctk.CTkLabel(
                row_frame, text=time_str, width=HM_COL_TIME_W,
                font=small_font, text_color="#90A4AE",
            )
            time_lbl.pack(side="left", padx=0)

            # TF cells
            cell_frames = []
            full_align_al = 0
            full_align_sat = 0

            for tf in HM_TIMEFRAMES:
                signals = self._data.get_at(ms, tf)
                ema_v = signals.get("ema", 0)
                macd_v = signals.get("macd", 0)
                rsi_v = signals.get("rsi", 0)

                cell = self._render_cell(
                    row_frame, ema_v, macd_v, rsi_v, indicator_font
                )
                cell.pack(side="left", padx=0)
                cell_frames.append(cell)

                # Track 3/3 alignment for Toplam
                votes = [ema_v, macd_v, rsi_v]
                pos = sum(1 for v in votes if v > 0)
                neg = sum(1 for v in votes if v < 0)
                if pos == 3:
                    full_align_al += 1
                elif neg == 3:
                    full_align_sat += 1

            # Toplam column
            total_text, total_color = self._format_total(full_align_al, full_align_sat)
            total_lbl = ctk.CTkLabel(
                row_frame, text=total_text, width=HM_COL_TOTAL_W,
                font=small_font, text_color=total_color,
            )
            total_lbl.pack(side="left", padx=0)

            self._rows.append((row_frame, time_lbl, cell_frames, total_lbl))

    def _render_cell(self, parent, ema_vote: int, macd_vote: int, rsi_vote: int,
                     font) -> ctk.CTkFrame:
        """Create a cell with 3 colored indicator labels (E, M, R)."""
        votes = [ema_vote, macd_vote, rsi_vote]
        style = _alignment_style(votes)

        cell = ctk.CTkFrame(
            parent, border_color=style["border"], border_width=1,
            fg_color=style["bg"], width=HM_COL_TF_W, height=20,
            corner_radius=2,
        )
        cell.pack_propagate(False)

        # E label
        ctk.CTkLabel(
            cell, text="E", width=20, height=18,
            font=font, text_color=_vote_color(ema_vote),
            fg_color="transparent",
        ).pack(side="left", padx=(2, 0))

        # M label
        ctk.CTkLabel(
            cell, text="M", width=20, height=18,
            font=font, text_color=_vote_color(macd_vote),
            fg_color="transparent",
        ).pack(side="left", padx=0)

        # R label
        ctk.CTkLabel(
            cell, text="R", width=20, height=18,
            font=font, text_color=_vote_color(rsi_vote),
            fg_color="transparent",
        ).pack(side="left", padx=(0, 2))

        return cell

    def _format_total(self, al_count: int, sat_count: int) -> tuple:
        """Format Toplam column text and color."""
        if al_count > 0 and sat_count == 0:
            return f"+{al_count}", _CLR_AL
        elif sat_count > 0 and al_count == 0:
            return f"-{sat_count}", _CLR_SAT
        elif al_count > 0 and sat_count > 0:
            return f"+{al_count}/-{sat_count}", "#FFD54F"
        return "0", _CLR_NOTR

    # ═══════════════════════════════════════════════════════════════════
    # Summary Bar
    # ═══════════════════════════════════════════════════════════════════

    def _update_summary(self, visible_keys: list[int]) -> None:
        """Update the bottom summary bar."""
        if not visible_keys:
            self._summary_var.set("")
            return

        first_dt = datetime.datetime.fromtimestamp(
            visible_keys[0] / 1000, tz=datetime.timezone.utc
        )
        last_dt = datetime.datetime.fromtimestamp(
            visible_keys[-1] / 1000, tz=datetime.timezone.utc
        )

        # Count how many TFs are currently aligned (at the first visible minute)
        ms = visible_keys[0]
        aligned_al = 0
        aligned_sat = 0
        for tf in HM_TIMEFRAMES:
            signals = self._data.get_at(ms, tf)
            votes = [signals.get("ema", 0), signals.get("macd", 0), signals.get("rsi", 0)]
            pos = sum(1 for v in votes if v > 0)
            neg = sum(1 for v in votes if v < 0)
            if pos == 3:
                aligned_al += 1
            elif neg == 3:
                aligned_sat += 1

        range_str = (
            f"{first_dt.strftime('%H:%M')} - {last_dt.strftime('%H:%M')}"
        )
        align_str = (
            f"Uyumlu TF: {aligned_al} AL, {aligned_sat} SAT"
        )
        self._summary_var.set(
            f"Aralik: {range_str}  |  {len(visible_keys)} satir  |  {align_str}"
        )

    # ═══════════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════════

    def _show_empty_message(self, msg: str) -> None:
        """Show a single-row message in the table."""
        self._clear_rows()
        row_frame = ctk.CTkFrame(self._scroll, fg_color=_ROW_EVEN)
        row_frame.pack(fill="x", pady=0)
        ctk.CTkLabel(
            row_frame, text=msg, font=ctk.CTkFont(size=12),
            text_color="#78909C",
        ).pack(side="left", padx=12, pady=6)
        self._rows.append((row_frame, None, [], None))
