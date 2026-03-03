import tkinter as tk
import customtkinter as ctk
import pandas as pd


class IndicatorChart(ctk.CTkFrame):
    """Simple canvas-based price chart with indicator overlays."""

    def __init__(self, parent, width: int = 700, height: int = 350):
        super().__init__(parent)
        self._width = width
        self._height = height
        self._price_height = int(height * 0.7)
        self._rsi_height = int(height * 0.3)

        self._canvas = tk.Canvas(
            self, width=width, height=height,
            bg="#1a1a2e", highlightthickness=0,
        )
        self._canvas.pack(fill="both", expand=True)

    def update_data(self, klines: pd.DataFrame, indicators: dict = None) -> None:
        self._canvas.delete("all")
        if klines is None or klines.empty:
            self._canvas.create_text(
                self._width // 2, self._height // 2,
                text="Veri bekleniyor...", fill="gray", font=("Arial", 12),
            )
            return

        w = self._canvas.winfo_width() or self._width
        h = self._canvas.winfo_height() or self._height
        self._price_height = int(h * 0.7)
        self._rsi_height = int(h * 0.3)

        last_n = min(100, len(klines))
        df = klines.tail(last_n).reset_index(drop=True)

        self._draw_price(df, w, self._price_height)
        self._draw_separator(w, self._price_height)

        if indicators and "RSI" in indicators:
            rsi_val = indicators["RSI"]
            if isinstance(rsi_val, dict):
                rsi_val = rsi_val.get("value")
            self._draw_rsi_area(w, self._price_height, self._rsi_height, rsi_val)

    def _draw_price(self, df: pd.DataFrame, w: int, h: int) -> None:
        closes = df["close"].values
        if len(closes) < 2:
            return

        min_p = min(closes)
        max_p = max(closes)
        rng = max_p - min_p if max_p != min_p else 1
        margin = 10
        usable_w = w - 2 * margin
        usable_h = h - 2 * margin

        points = []
        for i, c in enumerate(closes):
            x = margin + (i / (len(closes) - 1)) * usable_w
            y = margin + (1 - (c - min_p) / rng) * usable_h
            points.append((x, y))

        # Draw line
        for i in range(len(points) - 1):
            color = "#00C853" if closes[i + 1] >= closes[i] else "#FF1744"
            self._canvas.create_line(
                points[i][0], points[i][1],
                points[i + 1][0], points[i + 1][1],
                fill=color, width=1.5,
            )

        # Price labels
        self._canvas.create_text(w - 5, margin, text=f"{max_p:.6f}",
                                 fill="gray", anchor="ne", font=("Arial", 8))
        self._canvas.create_text(w - 5, h - margin, text=f"{min_p:.6f}",
                                 fill="gray", anchor="se", font=("Arial", 8))

        # Current price
        last_price = closes[-1]
        self._canvas.create_text(
            w - 5, points[-1][1], text=f" {last_price:.6f}",
            fill="white", anchor="e", font=("Arial", 9, "bold"),
        )

    def _draw_separator(self, w: int, y: int) -> None:
        self._canvas.create_line(0, y, w, y, fill="#333", width=1)

    def _draw_rsi_area(self, w: int, y_start: int, h: int, rsi_value) -> None:
        margin = 10
        # Draw RSI levels
        for level, color_line in [(70, "#FF1744"), (30, "#00C853"), (50, "#555")]:
            y = y_start + margin + (1 - level / 100) * (h - 2 * margin)
            self._canvas.create_line(0, y, w, y, fill=color_line, width=0.5, dash=(3, 3))
            self._canvas.create_text(w - 5, y, text=str(level),
                                     fill=color_line, anchor="e", font=("Arial", 7))

        # RSI label
        self._canvas.create_text(5, y_start + 5, text="RSI",
                                 fill="gray", anchor="nw", font=("Arial", 8))

        # Current RSI value
        if rsi_value is not None:
            try:
                rsi_val = float(rsi_value)
                color = "#00C853" if rsi_val < 30 else "#FF1744" if rsi_val > 70 else "white"
                self._canvas.create_text(
                    50, y_start + 5, text=f"{rsi_val:.1f}",
                    fill=color, anchor="nw", font=("Arial", 9, "bold"),
                )
            except (TypeError, ValueError):
                pass
