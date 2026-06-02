import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from src.binomial_tree import BinomialTree  # noqa: E402
from src.black_scholes import (  # noqa: E402
    black_scholes_price,
    black_scholes_price_vec,
    delta,
    gamma,
    implied_volatility,
    rho,
    theta,
    vega,
)
from src.greeks_visualizer import (  # noqa: E402
    plot_market_iv_smile,
    plot_solved_iv_surface,
    solve_iv_surface,
)
from src.market_data import (  # noqa: E402
    DEFAULT_RISK_FREE_RATE,
    MarketDataError,
    _years_to_expiry,
    get_option_chain,
    get_spot,
    list_expirations,
    price_chain,
)

st.set_page_config(page_title="Options Pricing Calculator", layout="wide")
st.title("Options Pricing Calculator")

calc_tab, live_tab, surface_tab = st.tabs(["Calculator", "Live market", "IV surface"])


def _offline_surface_expiries() -> list[str]:
    """Synthesize a spread of expiries for the offline fixture.

    Offline ``get_option_chain`` returns the same bundled chain regardless of the
    requested expiry, so we vary only the time-to-expiry by generating a handful
    of future dates around the fixture's nominal expiry. This lets the offline
    demo render a genuine multi-expiry surface (same smile shape, different T).
    """
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc)
    return [(base + timedelta(days=d)).strftime("%Y-%m-%d") for d in (20, 45, 90, 160)]


def build_surface_chains(
    symbol: str,
    expiries: list[str],
    option_type: str,
    offline: bool,
) -> tuple[dict, dict, float]:
    """Assemble multi-expiry chains + time-to-expiry for the IV surface.

    Returns ``(chains_by_expiry, expiry_years, spot)`` ready to feed
    :func:`solve_iv_surface` / :func:`plot_solved_iv_surface`. Sparse/failed
    expiries are skipped (never raises); the underlying library functions handle
    the offline fixture so the data path always produces something.
    """
    spot = get_spot(symbol, offline=offline)
    chains: dict[str, pd.DataFrame] = {}
    years: dict[str, float] = {}
    for exp in expiries:
        try:
            chain = get_option_chain(symbol, exp, option_type, offline=offline)
        except MarketDataError:
            continue
        if chain.empty:
            continue
        chains[exp] = chain
        years[exp] = _years_to_expiry(exp)
    return chains, years, spot


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

# --- IV surface tab ---------------------------------------------------------
with surface_tab:
    st.subheader("Solved implied-volatility surface")
    st.caption(
        "Fetches option chains across MULTIPLE expiries, solves OUR own implied "
        "volatility per (strike, expiry) from market mids via the vectorized "
        "Newton solver, and renders the real solved IV surface plus a per-expiry "
        "smile. Degrades gracefully offline to the bundled sample chain."
    )

    sc1, sc2, sc3 = st.columns([2, 2, 1])
    surf_symbol = sc1.text_input("Symbol", value="AAPL", key="surf_symbol").strip().upper()
    surf_type = sc2.radio("Option type", ["call", "put"], horizontal=True, key="surf_type")
    surf_offline = sc3.checkbox("Offline sample", value=False, key="surf_offline")

    max_expiries = st.slider("Max expiries to fetch", 2, 8, 4, key="surf_max_exp")

    if st.button("Build IV surface", key="surf_build"):
        # Choose the expiry set. Live: take the nearest N real expiries (falling
        # back to the offline fixture on any failure). Offline: synthesize a
        # spread of T values over the same bundled chain.
        if surf_offline:
            expiries = _offline_surface_expiries()[:max_expiries]
            effective_offline = True
        else:
            try:
                expiries = list_expirations(surf_symbol, offline=False)[:max_expiries]
                effective_offline = False
            except MarketDataError as exc:
                st.warning(f"Could not list expirations ({exc}); using offline sample.")
                expiries = _offline_surface_expiries()[:max_expiries]
                effective_offline = True

        try:
            chains, years, spot = build_surface_chains(
                surf_symbol, expiries, surf_type, offline=effective_offline
            )
        except MarketDataError as exc:
            st.warning(f"Live data unavailable ({exc}); using offline sample.")
            expiries = _offline_surface_expiries()[:max_expiries]
            chains, years, spot = build_surface_chains(
                surf_symbol, expiries, surf_type, offline=True
            )

        if not chains:
            st.info("No usable option data for that symbol/expiries — try another symbol.")
        else:
            st.metric("Spot", f"${spot:.2f}")
            st.caption(
                f"Solved across {len(chains)} expiries "
                f"({', '.join(sorted(chains))}) · r={DEFAULT_RISK_FREE_RATE:.3f}"
            )

            surface = solve_iv_surface(
                chains, spot, years, r=DEFAULT_RISK_FREE_RATE, option_type=surf_type
            )
            if surface.empty:
                st.info("IV did not solve for any contract (sparse/illiquid quotes).")
            else:
                try:
                    plot_solved_iv_surface(
                        chains,
                        spot,
                        years,
                        r=DEFAULT_RISK_FREE_RATE,
                        option_type=surf_type,
                        save_path=None,
                    )
                    st.pyplot(plt.gcf())
                finally:
                    plt.close("all")

                st.markdown("**Per-expiry IV smile**")
                pivot = surface.pivot_table(
                    index="strike", columns="expiry", values="iv"
                ).sort_index()
                st.line_chart(pivot * 100.0)
                with st.expander("Solved IV table"):
                    st.dataframe(surface, width="stretch")

    # Vectorized batch pricing — price a whole strike grid in one broadcasted call.
    st.markdown("---")
    st.subheader("Vectorized batch pricing")
    st.caption(
        "Price an entire strike grid at once with the vectorized Black-Scholes "
        "kernel (one broadcasted call, no per-strike Python loop)."
    )
    bp1, bp2, bp3 = st.columns(3)
    grid_spot = bp1.number_input("Spot", value=100.0, min_value=0.01, key="grid_spot")
    grid_T = bp2.number_input("T (years)", value=0.25, min_value=0.01, key="grid_T")
    grid_sigma = bp3.number_input("σ", value=0.20, min_value=0.01, key="grid_sigma")
    grid_type = st.radio("Type", ["call", "put"], horizontal=True, key="grid_type")
    strike_grid = np.linspace(grid_spot * 0.7, grid_spot * 1.3, 25)
    grid_prices = black_scholes_price_vec(
        grid_spot, strike_grid, grid_T, DEFAULT_RISK_FREE_RATE, grid_sigma, grid_type
    )
    grid_df = pd.DataFrame({"Strike": strike_grid, "Price": grid_prices})
    st.line_chart(grid_df, x="Strike", y="Price")
