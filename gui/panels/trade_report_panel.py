import customtkinter as ctk
from datetime import datetime, timedelta
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

        # Date filters
        ctk.CTkLabel(filter_frame, text="Baslangic:",
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=(20, 5))
        self._start_entry = ctk.CTkEntry(filter_frame, width=150, placeholder_text="2026-03-12 00:00")
        self._start_entry.pack(side="left", padx=2)
        # Default: 24 hours ago
        default_start = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
        self._start_entry.insert(0, default_start)

        ctk.CTkLabel(filter_frame, text="Bitis:",
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=(15, 5))
        self._end_entry = ctk.CTkEntry(filter_frame, width=150, placeholder_text="2026-03-13 23:59")
        self._end_entry.pack(side="left", padx=2)
        default_end = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._end_entry.insert(0, default_end)

        # Quick filter buttons
        ctk.CTkButton(filter_frame, text="Son 24s", width=70, height=28,
                      command=lambda: self._quick_range(24)).pack(side="left", padx=(15, 3))
        ctk.CTkButton(filter_frame, text="Son 7g", width=60, height=28,
                      command=lambda: self._quick_range(168)).pack(side="left", padx=3)
        ctk.CTkButton(filter_frame, text="Son 30g", width=60, height=28,
                      command=lambda: self._quick_range(720)).pack(side="left", padx=3)
        ctk.CTkButton(filter_frame, text="Tumu", width=50, height=28,
                      command=self._show_all).pack(side="left", padx=3)

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

        # ── Summary Cards ──
        self._summary_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._summary_frame.pack(fill="x", padx=10, pady=5)
        self._summary_cards = {}
        card_defs = [
            ("total_trades", "Toplam Islem", "0", "#2196F3"),
            ("win_rate", "Kazanma Orani", "%0", "#00C853"),
            ("total_pnl", "Net Kar/Zarar", "$0.00", "#FF9800"),
            ("total_fee", "Toplam Fee", "$0.00", "#FF1744"),
            ("avg_hold", "Ort. Pozisyon", "0dk", "#9C27B0"),
            ("best_trade", "En Iyi", "$0.00", "#00E676"),
            ("worst_trade", "En Kotu", "$0.00", "#FF5252"),
            ("avg_leverage", "Ort. Kaldirac", "0x", "#607D8B"),
        ]
        for i, (key, label, default, color) in enumerate(card_defs):
            card = ctk.CTkFrame(self._summary_frame, fg_color="#1a1a2e",
                                corner_radius=8, width=140, height=70)
            card.pack(side="left", fill="x", expand=True, padx=3)
            card.pack_propagate(False)
            ctk.CTkLabel(card, text=label, font=ctk.CTkFont(size=10),
                         text_color="gray").pack(pady=(8, 0))
            val_lbl = ctk.CTkLabel(card, text=default,
                                    font=ctk.CTkFont(size=16, weight="bold"),
                                    text_color=color)
            val_lbl.pack(pady=(0, 5))
            self._summary_cards[key] = val_lbl

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
        wins = sum(1 for t in trades if t.get("pnl_usdt", 0) > 0)
        losses = sum(1 for t in trades if t.get("pnl_usdt", 0) < 0)
        breakeven = total - wins - losses
        win_rate = (wins / total * 100) if total > 0 else 0
        total_pnl = sum(t.get("pnl_usdt", 0) for t in trades)
        total_fee = sum(t.get("fee_usdt", 0) for t in trades)
        avg_hold = sum(t.get("hold_seconds", 0) for t in trades) / total if total else 0
        pnls = [t.get("pnl_usdt", 0) for t in trades]
        best = max(pnls) if pnls else 0
        worst = min(pnls) if pnls else 0
        leverages = [t.get("leverage", 1) for t in trades if t.get("leverage", 0) > 0]
        avg_lev = sum(leverages) / len(leverages) if leverages else 0

        self._summary_cards["total_trades"].configure(
            text=f"{total} ({wins}K/{losses}Z)")
        self._summary_cards["win_rate"].configure(
            text=f"%{win_rate:.0f}",
            text_color="#00C853" if win_rate >= 50 else "#FF9800" if win_rate >= 30 else "#FF1744")
        self._summary_cards["total_pnl"].configure(
            text=f"${total_pnl:+.4f}",
            text_color="#00C853" if total_pnl >= 0 else "#FF1744")
        self._summary_cards["total_fee"].configure(text=f"${total_fee:.4f}")
        avg_m = int(avg_hold // 60)
        self._summary_cards["avg_hold"].configure(text=f"{avg_m}dk")
        self._summary_cards["best_trade"].configure(text=f"${best:+.4f}")
        self._summary_cards["worst_trade"].configure(text=f"${worst:+.4f}")
        self._summary_cards["avg_leverage"].configure(text=f"{avg_lev:.0f}x")

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
