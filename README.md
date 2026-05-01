# IDX Quant Screener Dashboard

![Jupyter](https://img.shields.io/badge/Jupyter-%23F37600.svg?logo=jupyter&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Status](https://img.shields.io/badge/Status-Skeleton%20Code-yellow.svg)

This notebook provides a comprehensive **Fundamental and Technical Screener Dashboard** for stocks listed on the Indonesia Stock Exchange (IDX). It combines financial health metrics with price action analysis to identify potentially undervalued investment opportunities.

## Features

### 1. Fundamental Analysis
Fetches key financial ratios and data using `yfinance`:
*   **Market Capitalization**: Total market value of the company.
*   **P/E Ratio**: Trailing Price-to-Earnings ratio.
*   **Price to Book (P/B)**: Valuation relative to book value.
*   **Dividend Yield**: Annual dividend percentage.
*   **Beta**: Volatility relative to the market index (JKSE).
*   **Financials**: Revenue and Net Income tracking.

### 2. Technical Indicators
Calculates momentum and trend signals:
*   **Relative Strength Index (RSI)**: 14-day momentum oscillator to identify overbought/oversold conditions.
*   **Moving Averages**: 5-day and 20-day MA slopes for short-term trend detection.
*   **Bollinger Bands**: Volatility envelopes around price action.
*   **Volume Ratio**: Current volume compared to the 10-day average.

### 3. Quantitative Scoring (Undervaluation Score)
Implements a custom scoring algorithm (0-100) based on:
*   Low P/E ratios (valuation).
*   Oversold RSI levels (momentum entry).
*   Positive price changes (short-term strength).
*   Lower Beta (risk-adjusted stability).

### 4. Interactive Dashboard
Visualizes findings through four key perspectives:
1.  **Score Comparison**: Ranking stocks by their quantitative undervaluation score.
2.  **Valuation vs. Momentum**: Scatter plot of P/E Ratio vs. RSI.
3.  **Recent Performance**: 30-day price change percentage.
4.  **Risk/Scale Analysis**: Market Cap vs. Beta distribution.

## Prerequisites

To run this notebook, you will need the following Python libraries installed:

```bash
pip install yfinance pandas numpy matplotlib seaborn
```

## How to Use
1.  **Define Tickers**: The `FundamentallyTechnicalScreener` class is initialized with a dictionary of IDX tickers (e.g., `BBCA.JK`, `TLKM.JK`).
2.  **Run Screener**: Execute the data generation cell to fetch live market data.
3.  **Analyze Results**: Review the "Top Recommendations" printed in the output.
4.  **Visualize**: Run the visualization cell to generate the graphical dashboard.

---
*Disclaimer: This tool is for educational and analysis purposes only. Quantitative scores are based on simplified logic and do not constitute financial advice.*
