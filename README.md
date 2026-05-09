# 📈 IDX Quant Screener

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Market](https://img.shields.io/badge/Market-IDX%20(Indonesia)-success)](https://www.idx.co.id/)
[![Status](https://img.shields.io/badge/status-production--ready-brightgreen)](#)

A professional-grade quantitative stock screener and forecasting engine designed for the **Indonesia Stock Exchange (IDX)**. This tool automates the process of fetching financial data, computing fundamental/technical metrics, and using Machine Learning to identify undervalued investment opportunities.

---

## ✨ Key Features

-   **🔄 Automated Data Pipeline**: Seamlessly fetches real-time and historical data for IDX tickers using `yfinance` with built-in retry logic.
-   **📊 Multi-Factor Scoring**: Evaluates stocks using a normalized Z-Score methodology across P/E, P/B, Dividend Yield, and Beta.
-   **📈 Advanced Technicals**: Computes RSI, Moving Average slopes, and Bollinger Bands to assess momentum and volatility.
-   **🤖 ML-Powered Ranking**: Integrated `StockRanker` engine that uses scikit-learn to predict "undervaluation scores" based on historical performance.
-   **🎨 Professional Visualizations**: Generates high-fidelity 3x2 diagnostic dashboards comparing stocks against the **Jakarta Composite Index (^JKSE)**.
-   **🚀 Risk-Adjusted Analysis**: Support for risk-adjusted scoring to prioritize stability alongside growth.
-   **📁 Data Export**: Automatically generates `top_picks.csv` and full analytical reports for further research.

---

## 🛠️ Tech Stack

-   **Core**: Python 3.10+
-   **Data Processing**: Pandas, NumPy
-   **Machine Learning**: Scikit-Learn
-   **Finance**: yfinance
-   **Visualization**: Matplotlib, Seaborn
-   **Configuration**: Pydantic (Settings), PyYAML
-   **Logging**: Loguru

---

## 🚀 Getting Started

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/idx-quant-screener.git
cd idx-quant-screener
```

### 2. Set Up Environment
It is recommended to use a virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configuration
1.  Copy the example environment file:
    ```bash
    cp .env.example .env
    ```
2.  Adjust settings in `.env` (e.g., `LOG_LEVEL`, `CACHE_TTL`).
3.  Modify `configs/default.yaml` to add or remove stock tickers you wish to track.

---

## 🖥️ Usage

Run the main screener execution:

```bash
python main.py
```

Upon execution, the system will:
1.  Fetch latest data for all configured tickers.
2.  Compute technical indicators and fundamental scores.
3.  Train/Update the ML ranking model.
4.  Generate a visualization dashboard in `data/output/`.
5.  Print the **Top 5 Undervalued Picks** to your terminal.

---

## 📂 Project Structure

```text
idx_quant_screener/
├── configs/            # YAML configuration files
├── data/               # Output directory for CSVs and plots
├── models/             # Serialized ML models
├── notebooks/          # Research and exploratory analysis
├── src/
│   ├── analysis/       # Fundamental, Technical, and ML engines
│   ├── core/           # Config loaders and logging setup
│   ├── fetchers/       # Data ingestion logic (yfinance)
│   ├── viz/            # Visualization and dashboard rendering
│   └── __main__.py     # Core application workflow
└── main.py             # Entry point script
```

---

## 📊 Sample Output

The screener provides a terminal summary like this:

```text
🏆 TOP 5 UNDERVALUED PICKS:
ticker           name  undervaluation_score  pe_ratio  rsi_14  price_change_pct
 ADRO.JK  Adaro Energy                 0.842      4.12   42.15             -2.4
 BBRI.JK  Bank Rakyat                  0.795     12.45   55.10              1.2
 ...
```

---

## ⚖️ License & Disclaimer

This project is licensed under the MIT License.

**Disclaimer**: *This tool is for educational and research purposes only. It does not constitute financial advice. Always perform your own due diligence before making investment decisions.*

---

## 🤝 Contributing

Contributions are welcome! Whether it's fixing bugs, adding new technical indicators, or improving the ML model, feel free to help out.

1.  Fork the Project
2.  Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3.  Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4.  Push to the Branch (`git push origin feature/AmazingFeature`)
5.  Open a Pull Request

---

## 📬 Contact

For business inquiries, technical support, or partnership opportunities, please contact me at:

- **Email**: h.sethiawan@gmail.com
- **Website**: [hrsethiawan.com](https://www.hrsethiawan.com/)

---

<p align="center">
  Made with ❤️ for the IDX Trading Community
</p>
