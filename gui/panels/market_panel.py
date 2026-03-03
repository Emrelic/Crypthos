"""Market Panel - displays live market data, indicators, analysis results."""
import customtkinter as ctk
from gui.widgets.indicator_chart import IndicatorChart


class MarketPanel(ctk.CTkFrame):
    """Live market data display with indicators, regime, confluence, and chart."""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller
        self.pack(fill="both", expand=True)
        self._build_ui()

    def _build_ui(self) -> None:
        # Top: pair and interval selection
        top = ctk.CTkFrame(self)
        top.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(top, text="Parite:").pack(side="left", padx=5)
        symbols = self.controller.get_watched_symbols()
        self._pair_var = ctk.StringVar(value=self.controller.get_current_symbol())
        self._pair_menu = ctk.CTkOptionMenu(
            top, variable=self._pair_var, values=symbols,
            command=self._on_pair_change,
        )
        self._pair_menu.pack(side="left", padx=5)

        ctk.CTkLabel(top, text="Interval:").pack(side="left", padx=(20, 5))
        self._interval_var = ctk.StringVar(value="15m")
        ctk.CTkOptionMenu(
            top, variable=self._interval_var,
            values=["1m", "5m", "15m", "1h", "4h", "1d"],
        ).pack(side="left", padx=5)

        # Price info row
        price_frame = ctk.CTkFrame(self)
        price_frame.pack(fill="x", padx=10, pady=5)

        self._labels = {}
        for name in ["Price", "Mark", "24h High", "24h Low",
                      "Funding", "Volume", "OI"]:
            f = ctk.CTkFrame(price_frame, fg_color="transparent")
            f.pack(side="left", padx=8)
            ctk.CTkLabel(f, text=f"{name}:", text_color="gray",
                         font=ctk.CTkFont(size=11)).pack()
            lbl = ctk.CTkLabel(f, text="--", font=ctk.CTkFont(size=12, weight="bold"))
            lbl.pack()
            self._labels[name] = lbl

        # === Analysis Summary Row ===
        analysis_frame = ctk.CTkFrame(self)
        analysis_frame.pack(fill="x", padx=10, pady=5)

        # Regime
        regime_f = ctk.CTkFrame(analysis_frame, fg_color="transparent")
        regime_f.pack(side="left", padx=10)
        ctk.CTkLabel(regime_f, text="Rejim:", text_color="gray",
                     font=ctk.CTkFont(size=11)).pack(side="left")
        self._regime_lbl = ctk.CTkLabel(
            regime_f, text="--", font=ctk.CTkFont(size=12, weight="bold")
        )
        self._regime_lbl.pack(side="left", padx=5)

        # Confluence Score
        conf_f = ctk.CTkFrame(analysis_frame, fg_color="transparent")
        conf_f.pack(side="left", padx=10)
        ctk.CTkLabel(conf_f, text="Confluence:", text_color="gray",
                     font=ctk.CTkFont(size=11)).pack(side="left")
        self._confluence_lbl = ctk.CTkLabel(
            conf_f, text="--", font=ctk.CTkFont(size=12, weight="bold")
        )
        self._confluence_lbl.pack(side="left", padx=5)

        # Confluence Signal
        signal_f = ctk.CTkFrame(analysis_frame, fg_color="transparent")
        signal_f.pack(side="left", padx=10)
        ctk.CTkLabel(signal_f, text="Sinyal:", text_color="gray",
                     font=ctk.CTkFont(size=11)).pack(side="left")
        self._signal_lbl = ctk.CTkLabel(
            signal_f, text="--", font=ctk.CTkFont(size=14, weight="bold")
        )
        self._signal_lbl.pack(side="left", padx=5)

        # Divergence
        div_f = ctk.CTkFrame(analysis_frame, fg_color="transparent")
        div_f.pack(side="left", padx=10)
        ctk.CTkLabel(div_f, text="Diverjans:", text_color="gray",
                     font=ctk.CTkFont(size=11)).pack(side="left")
        self._div_lbl = ctk.CTkLabel(
            div_f, text="--", font=ctk.CTkFont(size=11, weight="bold")
        )
        self._div_lbl.pack(side="left", padx=5)

        # === Risk Stats Row ===
        risk_frame = ctk.CTkFrame(self)
        risk_frame.pack(fill="x", padx=10, pady=3)

        self._risk_labels = {}
        for name in ["Drawdown", "Gunluk Kayip", "Win Rate", "Kelly %", "ATR SL", "ATR TP"]:
            rf = ctk.CTkFrame(risk_frame, fg_color="transparent")
            rf.pack(side="left", padx=8)
            ctk.CTkLabel(rf, text=f"{name}:", text_color="gray",
                         font=ctk.CTkFont(size=10)).pack()
            rl = ctk.CTkLabel(rf, text="--", font=ctk.CTkFont(size=10, weight="bold"))
            rl.pack()
            self._risk_labels[name] = rl

        # === Indicators Grid (expanded) ===
        ind_frame = ctk.CTkFrame(self)
        ind_frame.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(ind_frame, text="Indikatörler",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=5)

        self._ind_labels = {}
        ind_grid = ctk.CTkFrame(ind_frame)
        ind_grid.pack(fill="x", padx=5, pady=5)

        indicator_names = [
            "RSI", "StochRSI_K", "MFI", "CCI", "Williams_R",
            "MACD_line", "MACD_histogram", "ADX",
            "Supertrend_trend", "PSAR_trend", "Ichimoku_Position",
            "BB_PercentB", "ATR", "OBV_slope", "CMF",
        ]
        cols = 5
        for i, name in enumerate(indicator_names):
            row, col = divmod(i, cols)
            f = ctk.CTkFrame(ind_grid, fg_color="transparent")
            f.grid(row=row, column=col, padx=8, pady=2, sticky="w")
            short = name.replace("_trend", "").replace("_Position", "")
            ctk.CTkLabel(f, text=f"{short}:", text_color="gray",
                         font=ctk.CTkFont(size=10)).pack(side="left")
            lbl = ctk.CTkLabel(f, text="--", font=ctk.CTkFont(size=10, weight="bold"))
            lbl.pack(side="left", padx=3)
            self._ind_labels[name] = lbl

        # Chart
        self._chart = IndicatorChart(self)
        self._chart.pack(fill="both", expand=True, padx=10, pady=5)

    def _on_pair_change(self, symbol: str) -> None:
        self.controller.switch_pair(symbol)

    def update_data(self, market_data: dict, indicator_values: dict,
                    klines=None) -> None:
        """Update all market data displays."""
        price = market_data.get("price", 0)
        fmt = ".6f" if price < 1 else ".2f"

        self._labels["Price"].configure(text=f"{price:{fmt}}")
        self._labels["Mark"].configure(
            text=f"{market_data.get('mark_price', 0):{fmt}}"
        )
        self._labels["24h High"].configure(
            text=f"{market_data.get('high_24h', 0):{fmt}}"
        )
        self._labels["24h Low"].configure(
            text=f"{market_data.get('low_24h', 0):{fmt}}"
        )

        fr = market_data.get("funding_rate", 0)
        fr_color = "red" if fr < 0 else "green"
        self._labels["Funding"].configure(
            text=f"{fr * 100:.4f}%", text_color=fr_color,
        )

        vol = market_data.get("volume_24h", 0)
        if vol > 1_000_000_000:
            vol_text = f"{vol / 1_000_000_000:.1f}B"
        elif vol > 1_000_000:
            vol_text = f"{vol / 1_000_000:.1f}M"
        else:
            vol_text = f"{vol:,.0f}"
        self._labels["Volume"].configure(text=vol_text)
        self._labels["OI"].configure(text="--")

        # Update indicator values
        for name, lbl in self._ind_labels.items():
            val = indicator_values.get(name)
            if val is not None:
                if isinstance(val, str):
                    color = "white"
                    if val in ("UP", "ABOVE"):
                        color = "#00C853"
                    elif val in ("DOWN", "BELOW"):
                        color = "#FF1744"
                    lbl.configure(text=val, text_color=color)
                else:
                    try:
                        v = float(val)
                        lbl.configure(text=f"{v:.2f}")
                    except (TypeError, ValueError):
                        lbl.configure(text=str(val))

        # RSI coloring
        rsi_val = indicator_values.get("RSI")
        if rsi_val is not None:
            try:
                rv = float(rsi_val)
                color = "#00C853" if rv < 30 else "#FF1744" if rv > 70 else "white"
                self._ind_labels["RSI"].configure(
                    text=f"{rv:.1f}", text_color=color,
                )
            except (TypeError, ValueError):
                pass

        # Update analysis displays
        self._update_analysis()

        # Update risk displays
        self._update_risk(indicator_values)

        # Update chart
        if klines is not None:
            self._chart.update_data(klines, indicator_values)

    def _update_analysis(self) -> None:
        """Update regime, confluence, and divergence displays."""
        # Regime
        regime = self.controller.get_regime()
        if regime:
            r = regime.get("regime", "--")
            regime_colors = {
                "TRENDING": "#2196F3",
                "RANGING": "#FF9800",
                "VOLATILE": "#FF1744",
                "BREAKOUT": "#00E676",
            }
            self._regime_lbl.configure(
                text=r, text_color=regime_colors.get(r, "white")
            )

        # Confluence
        confluence = self.controller.get_confluence()
        if confluence:
            score = confluence.get("score", 0)
            signal = confluence.get("signal", "NEUTRAL")
            strength = confluence.get("strength", 0)
            bullish = confluence.get("bullish_count", 0)
            bearish = confluence.get("bearish_count", 0)

            score_color = "#00C853" if score > 0 else "#FF1744" if score < 0 else "white"
            self._confluence_lbl.configure(
                text=f"{score:.1f} ({bullish}B/{bearish}S)",
                text_color=score_color,
            )

            signal_colors = {
                "BUY": "#00C853",
                "SELL": "#FF1744",
                "NEUTRAL": "#FF9800",
            }
            self._signal_lbl.configure(
                text=f"{signal} ({strength:.0%})",
                text_color=signal_colors.get(signal, "white"),
            )

        # Divergences
        divergences = self.controller.get_divergences()
        if divergences:
            # Show most recent/strongest
            top = max(divergences, key=lambda d: d.get("strength", 0))
            div_type = top.get("type", "").replace("_", " ")
            div_ind = top.get("indicator", "")
            div_signal = top.get("signal", "")
            div_color = "#00C853" if div_signal == "BUY" else "#FF1744"
            self._div_lbl.configure(
                text=f"{div_type} ({div_ind})",
                text_color=div_color,
            )
        else:
            self._div_lbl.configure(text="Yok", text_color="gray")

    def _update_risk(self, indicator_values: dict) -> None:
        """Update risk statistics display."""
        stats = self.controller.get_risk_stats()
        if stats:
            dd = stats.get("drawdown_pct", 0)
            dd_color = "#FF1744" if dd > 10 else "#FF9800" if dd > 5 else "#00C853"
            self._risk_labels["Drawdown"].configure(
                text=f"{dd:.1f}%", text_color=dd_color
            )
            self._risk_labels["Gunluk Kayip"].configure(
                text=f"{stats.get('daily_loss', 0):.2f}$"
            )
            wr = stats.get("win_rate", 0)
            wr_color = "#00C853" if wr > 50 else "#FF9800" if wr > 40 else "#FF1744"
            self._risk_labels["Win Rate"].configure(
                text=f"{wr:.0f}%", text_color=wr_color
            )
            self._risk_labels["Kelly %"].configure(
                text=f"{stats.get('kelly_fraction', 0):.1f}%"
            )

        # ATR stops
        atr_stops = self.controller.get_atr_stops("BUY")
        if atr_stops:
            self._risk_labels["ATR SL"].configure(
                text=f"{atr_stops.get('sl_percent', 0):.1f}%"
            )
            self._risk_labels["ATR TP"].configure(
                text=f"{atr_stops.get('tp_percent', 0):.1f}%"
            )
