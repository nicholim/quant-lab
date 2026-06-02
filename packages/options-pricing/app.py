import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from src.binomial_tree import BinomialTree  # noqa: E402
from src.black_scholes import (  # noqa: E402
    black_scholes_price,
    delta,
    gamma,
    implied_volatility,
    rho,
    theta,
    vega,
)
from src.greeks_visualizer import plot_market_iv_smile  # noqa: E402
from src.market_data import (  # noqa: E402
    DEFAULT_RISK_FREE_RATE,
    MarketDataError,
    list_expirations,
    price_chain,
)

st.set_page_config(page_title="Options Pricing Calculator", layout="wide")
st.title("Options Pricing Calculator")

calc_tab, live_tab = st.tabs(["Calculator", "Live market"])

# Sidebar inputs
st.sidebar.header("Parameters")
S = st.sidebar.number_input("Spot Price (S)", value=100.0, min_value=0.01, step=1.0)
K = st.sidebar.number_input("Strike Price (K)", value=105.0, min_value=0.01, step=1.0)
T = st.sidebar.number_input("Time to Expiry (years)", value=0.25, min_value=0.01, step=0.01)
r = st.sidebar.number_input("Risk-Free Rate", value=0.05, min_value=0.0, step=0.01, format="%.4f")
sigma = st.sidebar.number_input(
    "Volatility (σ)", value=0.20, min_value=0.01, step=0.01, format="%.4f"
)
option_type = st.sidebar.selectbox("Option Type", ["call", "put"])
N_steps = st.sidebar.slider("Binomial Tree Steps", min_value=10, max_value=500, value=100)

# Pricing
with calc_tab:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Black-Scholes Model")
        bs_price = black_scholes_price(S, K, T, r, sigma, option_type)
        st.metric("Option Price", f"${bs_price:.4f}")

    with col2:
        st.subheader("Binomial Tree (CRR)")
        tree_eu = BinomialTree(S, K, T, r, sigma, N_steps, option_type, american=False)
        tree_am = BinomialTree(S, K, T, r, sigma, N_steps, option_type, american=True)
        st.metric("European Price", f"${tree_eu.price():.4f}")
        st.metric("American Price", f"${tree_am.price():.4f}")
        early_exercise = tree_am.price() - tree_eu.price()
        if early_exercise > 0.001:
            st.caption(f"Early exercise premium: ${early_exercise:.4f}")

    # Greeks table
    st.subheader("Greeks")
    greeks_data = {
        "Greek": ["Delta", "Gamma", "Theta (daily)", "Vega (per 1%)", "Rho (per 1%)"],
        "Value": [
            f"{delta(S, K, T, r, sigma, option_type):.6f}",
            f"{gamma(S, K, T, r, sigma):.6f}",
            f"{theta(S, K, T, r, sigma, option_type):.6f}",
            f"{vega(S, K, T, r, sigma):.6f}",
            f"{rho(S, K, T, r, sigma, option_type):.6f}",
        ],
    }
    st.table(pd.DataFrame(greeks_data))

    # Charts
    st.subheader("Payoff Diagram")
    spots = np.linspace(K * 0.7, K * 1.3, 200)
    if option_type == "call":
        payoff = np.maximum(spots - K, 0) - bs_price
    else:
        payoff = np.maximum(K - spots, 0) - bs_price

    chart_data = pd.DataFrame({"Spot": spots, "P&L": payoff})
    st.line_chart(chart_data, x="Spot", y="P&L")

    # Greeks vs Spot
    st.subheader("Delta vs Spot Price")
    deltas_call = [delta(s, K, T, r, sigma, "call") for s in spots]
    deltas_put = [delta(s, K, T, r, sigma, "put") for s in spots]
    delta_df = pd.DataFrame({"Spot": spots, "Call Delta": deltas_call, "Put Delta": deltas_put})
    st.line_chart(delta_df, x="Spot", y=["Call Delta", "Put Delta"])

# Implied Volatility solver
st.sidebar.markdown("---")
st.sidebar.header("Implied Volatility Solver")
market_price = st.sidebar.number_input("Market Price", value=bs_price, min_value=0.01, step=0.1)
if st.sidebar.button("Solve IV"):
    iv = implied_volatility(market_price, S, K, T, r, option_type)
    st.sidebar.success(f"Implied Volatility: {iv:.4%}")

# --- Live market tab --------------------------------------------------------
with live_tab:
    st.subheader("Price a real option chain")
    st.caption(
        "Chains from yfinance; spot from Finnhub (set FINNHUB_API_KEY) with a "
        "yfinance fallback. If live data is unavailable, the bundled offline "
        "sample chain is used so the demo never breaks."
    )

    lc1, lc2, lc3 = st.columns([2, 2, 1])
    live_symbol = lc1.text_input("Symbol", value="AAPL").strip().upper()
    live_offline = lc3.checkbox("Offline sample", value=False)

    expiries: list[str] = []
    try:
        expiries = list_expirations(live_symbol, offline=live_offline) if live_symbol else []
    except MarketDataError as exc:
        st.warning(f"Could not list expirations ({exc}). Falling back to offline sample.")
        expiries = list_expirations(live_symbol or "AAPL", offline=True)
        live_offline = True

    live_expiry = lc2.selectbox("Expiry", expiries) if expiries else None
    live_type = st.radio("Option type", ["call", "put"], horizontal=True)

    if st.button("Fetch & price chain") and live_expiry:
        try:
            priced = price_chain(
                live_symbol,
                live_expiry,
                live_type,
                r=DEFAULT_RISK_FREE_RATE,
                offline=live_offline,
            )
        except MarketDataError as exc:
            st.warning(f"Live data unavailable ({exc}); using offline sample.")
            priced = price_chain(live_symbol, live_expiry, live_type, offline=True)

        st.metric("Spot", f"${priced.attrs.get('spot', float('nan')):.2f}")
        st.caption(
            f"Expiry {priced.attrs.get('expiry')} · "
            f"T={priced.attrs.get('T', 0.0):.4f}y · r={DEFAULT_RISK_FREE_RATE:.3f}"
        )
        st.dataframe(priced, width="stretch")

        try:
            plot_market_iv_smile(priced, iv_column="our_iv", save_path=None)
            st.pyplot(plt.gcf())
        finally:
            plt.close("all")
