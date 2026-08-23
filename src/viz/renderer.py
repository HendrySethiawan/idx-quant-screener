# src/viz/renderer.py
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import numpy as np
import matplotlib.patheffects as path_effects  # ✅ Fixed import
from pathlib import Path
from core.logger import setup_logger

logger = setup_logger(Path("logs"))


class ScreenerViz:
    def __init__(self, output_dir: Path, sectors: dict = None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.sectors = sectors or {}
    def save_analysis(self, df: pd.DataFrame, filename: str = "screener_analysis.png", benchmark_data: dict = None):
        benchmark_data = benchmark_data or {}
        required_cols = ["undervaluation_score", "pe_ratio", "rsi_14", "price_change_pct"]
        existing_cols = [c for c in required_cols if c in df.columns]
        
        if not existing_cols:
            logger.warning("No required columns found for visualization")
            return
        
        df_clean = df.dropna(subset=existing_cols).copy()
        if df_clean.empty:
            logger.warning("No valid data for visualization after dropping NA")
            return
        
        if self.sectors and "ticker" in df_clean.columns:
            df_clean["sector"] = df_clean["ticker"].map(self.sectors).fillna("Unknown")
        
        # ✅ Add global rank column for consistent labeling
        if "undervaluation_score" in df_clean.columns:
            df_clean["global_rank"] = df_clean["undervaluation_score"].rank(ascending=False, method="first").astype(int)
        
        n_stocks = len(df_clean)
        fig_width = 18 if n_stocks > 20 else 14
        fig_height = 16 if n_stocks > 30 else 14
        
        # Strict 3x2 Grid
        nrows, ncols = 3, 2
        fig, axes = plt.subplots(nrows, ncols, figsize=(fig_width, fig_height))
        fig.suptitle("IDX Stock Screener Analysis", fontsize=18, fontweight="bold", y=0.98)
        
        norm = plt.Normalize(vmin=df_clean["undervaluation_score"].min(), vmax=df_clean["undervaluation_score"].max())
        
        # Calculate benchmark price change if available
        bm_price_change = 0
        bm_ticker = ""
        bm_df = pd.DataFrame()
        has_benchmark = len(benchmark_data) > 0
        if has_benchmark:
            bm_ticker = list(benchmark_data.keys())[0]
            bm_df = benchmark_data[bm_ticker]
            if len(bm_df) >= 2:
                bm_price_change = ((bm_df["Close"].iloc[-1] - bm_df["Close"].iloc[-2]) / bm_df["Close"].iloc[-2]) * 100

        # ========== ROW 1, LEFT: Benchmark Trend ==========
        ax = axes[0, 0]
        if has_benchmark and not bm_df.empty:
            ax.plot(bm_df.index, bm_df["Close"], color="#ff7f0e", linewidth=2.5, label=bm_ticker)
            
            # Highlight the last session (last day)
            last_day = bm_df.index[-1]
            if len(bm_df.index) > 1:
                second_last_day = bm_df.index[-2]
                ax.axvspan(second_last_day, last_day, color='gray', alpha=0.2, label='Latest Session')
            
            # Annotation for % change
            color_pct = "green" if bm_price_change >= 0 else "red"
            ax.annotate(f"Current: {bm_price_change:+.2f}%", 
                        xy=(0.05, 0.9), xycoords='axes fraction', 
                        fontsize=12, fontweight='bold', color=color_pct,
                        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="gray", alpha=0.8))
            
            ax.set_title(f"Benchmark Trend ({bm_ticker})", fontweight="bold", fontsize=13)
            ax.set_ylabel("Close Price")
            ax.grid(True, alpha=0.3, linestyle="--")
            ax.tick_params(axis='x', rotation=45)
            import matplotlib.dates as mdates
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
            ax.legend(loc='upper right')
        else:
            ax.text(0.5, 0.5, "Benchmark Data Unavailable", ha='center', va='center', fontsize=12)
            ax.set_title("Benchmark Trend", fontweight="bold", fontsize=13)

        # ========== ROW 1, RIGHT: All Stocks vs Benchmark ==========
        ax = axes[0, 1]
        all_stocks = df_clean.sort_values("price_change_pct", ascending=False).copy()
        
        bar_colors = ["#2ca02c" if v >= bm_price_change else "#d62728" for v in all_stocks["price_change_pct"]]
        labels = [str(row.get('ticker', '')) for _, row in all_stocks.iterrows()]
        
        bars = ax.bar(range(len(all_stocks)), all_stocks["price_change_pct"], color=bar_colors, edgecolor="black", linewidth=0.5)
        
        # Benchmark horizontal line
        ax.axhline(bm_price_change, color='blue', linestyle='dashed', linewidth=2, label=f'{bm_ticker} ({bm_price_change:+.2f}%)')
        
        # Value labels (only if readable)
        if len(all_stocks) <= 40:
            for i, bar in enumerate(bars):
                height = bar.get_height()
                offset = 0.05 if height >= 0 else -0.05
                va = 'bottom' if height >= 0 else 'top'
                ax.text(bar.get_x() + bar.get_width()/2, height + offset, f"{height:+.1f}", 
                        ha='center', va=va, fontsize=6, fontweight='bold')
        
        ax.set_xticks(range(len(all_stocks)))
        ax.set_xticklabels(labels, rotation=90, ha="center", fontsize=7)
        
        ax.set_title("Stocks vs Benchmark (Price Change (%))", fontweight="bold", fontsize=13)
        ax.set_ylabel("Change (%)")
        ax.legend()
        ax.grid(axis="y", alpha=0.3, linestyle="--")

        # ========== ROW 2, LEFT: Top Undervalued Stocks ==========
        ax = axes[1, 0]
        max_bars = len(df_clean)
        top_n = df_clean.nlargest(max_bars, "undervaluation_score").copy()
        colors = plt.cm.RdYlGn(norm(top_n["undervaluation_score"]))
        bar_width = 0.8 if n_stocks <= 20 else 0.9
        
        ax.bar(range(len(top_n)), top_n["undervaluation_score"], color=colors, edgecolor="black", linewidth=0.5, width=bar_width)
        for i, (_, row) in enumerate(top_n.iterrows()):
            ax.text(i, row["undervaluation_score"] + 0.01, str(int(row["global_rank"])), ha='center', va='bottom', fontsize=8, fontweight='bold', color='black')
        
        ax.set_xticks(range(len(top_n)))
        x_labels = [row.get("name", row.get("ticker", ""))[:15] + "…" if len(row.get("name", row.get("ticker", ""))) > 15 else row.get("name", row.get("ticker", "")) for _, row in top_n.iterrows()]
        ax.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=7)
        # "Top Ranked", not "Top Undervalued": undervaluation_score is min-max
        # normalised across the universe, so the leader scores 1.0 even if every
        # name is expensive. The fair-value verdict lives in the brief.
        ax.set_title("Top Ranked Stocks (relative, not a valuation)",
                     fontweight="bold", fontsize=13)
        ax.set_ylabel("Score")
        ax.set_ylim(0, max(1.0, top_n["undervaluation_score"].max() * 1.12))
        ax.grid(axis="y", alpha=0.3, linestyle="--")

        # ========== ROW 2, RIGHT: P/E vs RSI ==========
        ax = axes[1, 1]
        if "pe_ratio" in df_clean.columns and "rsi_14" in df_clean.columns:
            plot_df = df_clean[df_clean["pe_ratio"].notna() & df_clean["rsi_14"].notna()].copy()
            scatter = ax.scatter(plot_df["pe_ratio"], plot_df["rsi_14"], c=plot_df["undervaluation_score"], cmap="RdYlGn", s=90, alpha=0.85, edgecolors="black", linewidth=0.5, norm=norm)
            
            for _, row in plot_df.iterrows():
                ax.text(row["pe_ratio"], row["rsi_14"], str(int(row["global_rank"])), ha='center', va='center', fontsize=5, fontweight='bold', color='white', path_effects=[path_effects.withStroke(linewidth=1.2, foreground='black')])
            
            ax.set_xlabel("P/E Ratio")
            ax.set_ylabel("RSI (14)")
            ax.set_title("P/E vs RSI", fontweight="bold", fontsize=13)
            cbar = plt.colorbar(scatter, ax=ax, label="Score")
            cbar.ax.tick_params(labelsize=8)
            ax.grid(True, alpha=0.3, linestyle="--")

        # ========== ROW 3, LEFT: Price Change (%) ==========
        ax = axes[2, 0]
        top_n = df_clean.sort_values("undervaluation_score", ascending=False)
        bar_colors = ["#2ca02c" if v > 0 else "#d62728" for v in top_n["price_change_pct"]]
        ax.bar(range(len(top_n)), top_n["price_change_pct"], color=bar_colors, edgecolor="black", linewidth=0.5)
        
        for i, (_, row) in enumerate(top_n.iterrows()):
            if pd.notna(row["price_change_pct"]):
                value = row["price_change_pct"]
                offset = 0.25 if value >= 0 else -0.25
                ax.text(i, value + offset, f"{value:+.1f}", ha='center', va='center', fontsize=6, fontweight='bold', color='black')
        
        ax.set_xticks(range(len(top_n)))
        x_labels = [row.get("name", row.get("ticker", ""))[:15] + "…" if len(row.get("name", row.get("ticker", ""))) > 15 else row.get("name", row.get("ticker", "")) for _, row in top_n.iterrows()]
        ax.set_xticklabels(x_labels, rotation=45, ha="right", fontsize=7)
        ax.set_title("Price Change (%)", fontweight="bold", fontsize=13)
        ax.set_ylabel("Change (%)")
        ax.axhline(y=0, color="gray", linestyle="--", linewidth=0.8)
        from matplotlib.ticker import FuncFormatter
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:.1f}%"))
        ax.grid(axis="y", alpha=0.3, linestyle="--")

        # ========== ROW 3, RIGHT: Market Cap vs Beta ==========
        ax = axes[2, 1]
        if "market_cap" in df_clean.columns and "beta" in df_clean.columns:
            mc_values = df_clean["market_cap"]
            xlabel = "Market Cap"
            if mc_values.max() > 1e9:
                mc_values = mc_values / 1e9
                xlabel = "Market Cap (B IDR)"
            
            scatter = ax.scatter(mc_values, df_clean["beta"], c=df_clean["undervaluation_score"], cmap="RdYlGn", s=70, alpha=0.8, edgecolors="black", linewidth=0.5, norm=norm)
            for _, row in df_clean.iterrows():
                if pd.notna(row["market_cap"]) and pd.notna(row["beta"]):
                    mc_val = row["market_cap"] / 1e9 if row["market_cap"] > 1e9 else row["market_cap"]
                    ax.text(mc_val, row["beta"], str(int(row["global_rank"])), ha='center', va='center', fontsize=5, fontweight='bold', color='white', path_effects=[path_effects.withStroke(linewidth=0.8, foreground='black')])
            
            ax.set_xlabel(xlabel)
            ax.set_ylabel("Beta")
            ax.set_title("Market Cap vs Beta", fontweight="bold", fontsize=13)
            plt.colorbar(scatter, ax=ax, label="Score", shrink=0.8)
            ax.grid(True, alpha=0.3, linestyle="--")

        # ========== COMPACT LEGEND AT BOTTOM ==========
        legend_stocks = df_clean.sort_values("global_rank")
        legend_elements = []
        for _, row in legend_stocks.iterrows():
            rank = int(row["global_rank"])
            name = row.get("name", row.get("ticker", ""))
            short_name = name[:18] + "…" if len(name) > 18 else name
            color = plt.cm.RdYlGn(norm(row["undervaluation_score"]))
            legend_elements.append(plt.Line2D([0], [0], marker='o', color='w', markerfacecolor=color, markeredgecolor='black', markersize=8, linewidth=1.5, label=f"{rank}. {short_name}"))
        
        n_cols = 10 if n_stocks > 30 else 6
        font_size = 8 if n_stocks > 30 else 10
        fig.legend(handles=legend_elements, loc='lower center', bbox_to_anchor=(0.5, 0.02), ncol=n_cols, fontsize=font_size, frameon=True, fancybox=True, handlelength=1.0, handletextpad=0.4, columnspacing=0.8)
        
        plt.tight_layout(rect=[0, 0.15, 1, 0.96])
        
        filepath = self.output_dir / filename
        fig.savefig(filepath, dpi=200, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        logger.info(f"Visualization saved to {filepath}")
        
        self._save_summary_stats(df_clean)

    def _get_sector_color(self, sector: str) -> str:
        color_map = {
            "Financials": "#1f77b4", "Energy": "#ff7f0e", "Telecommunications": "#2ca02c",
            "Consumer Cyclical": "#d62728", "Basic Materials": "#9467bd", "Unknown": "#7f7f7f"
        }
        return color_map.get(sector, "#7f7f7f")

    def _save_summary_stats(self, df: pd.DataFrame):
        if df.empty: return
        summary = {
            "Total Stocks": len(df),
            "Avg Score": df["undervaluation_score"].mean() if "undervaluation_score" in df.columns else None,
            "Top Pick": df.loc[df["undervaluation_score"].idxmax(), "name"] if "undervaluation_score" in df.columns and "name" in df.columns else None,
            "Avg P/E": df["pe_ratio"].mean() if "pe_ratio" in df.columns else None,
            "Avg RSI": df["rsi_14"].mean() if "rsi_14" in df.columns else None,
        }
        if "sector" in df.columns:
            summary["Top Sector"] = df.groupby("sector")["undervaluation_score"].mean().idxmax()
        
        with open(self.output_dir / "summary.txt", "w", encoding="utf-8") as f:
            f.write("📊 IDX Screener Summary\n" + "=" * 40 + "\n")
            for k, v in summary.items():
                f.write(f"{k}: {v}\n")
        logger.info("Summary stats saved to summary.txt")