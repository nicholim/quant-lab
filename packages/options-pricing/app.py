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
    _offline_enabled,
    _years_to_expiry,
    get_option_chain,
    get_spot,
    list_expirations,
    price_chain,
)
from src.vol_surface import fit_svi_surface, svi_smile  # noqa: E402

# --- page + theme ----------------------------------------------------------
st.set_page_config(
    page_title="Options Pricing Studio",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get Help": "https://github.com/nicholim/quant",
        "Report a bug": "https://github.com/nicholim/quant/issues",
        "About": (
            "Options Pricing Studio — Black-Scholes, binomial trees, the full "
            "Greek set, and a solved implied-volatility surface from live "
            "(or bundled offline) option chains."
        ),
    },
)

# A small, restrained matplotlib style so library plots match the UI palette.
PALETTE = {
    "ink": "#1f4e79",
    "teal": "#2a9d8f",
    "amber": "#e09f3e",
    "rose": "#9e2a2b",
    "slate": "#1c2733",
    "grid": "#d6dbe2",
}
plt.rcParams.update(
    {
        "axes.edgecolor": PALETTE["grid"],
        "axes.labelcolor": PALETTE["slate"],
        "axes.titlecolor": PALETTE["slate"],
        "axes.grid": True,
        "grid.color": PALETTE["grid"],
        "grid.linewidth": 0.6,
        "xtick.color": PALETTE["slate"],
        "ytick.color": PALETTE["slate"],
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    }
)


# --- formatting helpers -----------------------------------------------------
def fmt_money(x: float) -> str:
    return f"${x:,.2f}"


def fmt_pct(x: float) -> str:
    return f"{x:.2%}"


# --- offline helpers --------------------------------------------------------
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


# --- header -----------------------------------------------------------------
st.title("📈 Options Pricing Studio")
st.caption(
    "European & American option pricing, the full Greek set, and a solved "
    "implied-volatility surface — backed by our own NumPy/SciPy engine."
)

# --- sidebar: global inputs -------------------------------------------------
st.sidebar.header("Global inputs")

force_offline = st.sidebar.toggle(
    "Offline sample mode",
    value=_offline_enabled(False),
    help=(
        "Use the bundled sample option chain instead of any live source. Handy "
        "for a deterministic demo or when running without network access."
    ),
)

st.sidebar.subheader("Contract")
S = st.sidebar.number_input(
    "Spot price (S)", value=100.0, min_value=0.01, step=1.0, help="Current price of the underlying."
)
K = st.sidebar.number_input(
    "Strike price (K)", value=105.0, min_value=0.01, step=1.0, help="Option strike."
)
T = st.sidebar.number_input(
    "Time to expiry (years)",
    value=0.25,
    min_value=0.001,
    step=0.01,
    help="Annualized time to expiration, e.g. 0.25 ≈ 3 months.",
)
r = st.sidebar.number_input(
    "Risk-free rate (r)",
    value=0.05,
    min_value=0.0,
    step=0.01,
    format="%.4f",
    help="Annualized continuously-compounded risk-free rate.",
)
sigma = st.sidebar.number_input(
    "Volatility (σ)",
    value=0.20,
    min_value=0.01,
    step=0.01,
    format="%.4f",
    help="Annualized volatility of the underlying.",
)
option_type = st.sidebar.selectbox("Option type", ["call", "put"])
N_steps = st.sidebar.slider(
    "Binomial tree steps",
    min_value=10,
    max_value=500,
    value=100,
    help="More steps → more accurate American/European tree price (slower).",
)

if force_offline:
    st.info(
        "🔌 Offline sample mode is ON — live tabs use the bundled sample chain, "
        "no network calls are made.",
        icon="🔌",
    )

calc_tab, live_tab, surface_tab = st.tabs(["📊 Calculator", "🌐 Live market", "🗺️ IV surface"])

# --- Calculator tab ---------------------------------------------------------
with calc_tab:
    st.subheader("Pricing")
    price_cols = st.columns(3)

    bs_price = black_scholes_price(S, K, T, r, sigma, option_type)
    tree_eu = BinomialTree(S, K, T, r, sigma, N_steps, option_type, american=False)
    tree_am = BinomialTree(S, K, T, r, sigma, N_steps, option_type, american=True)
    early_exercise = tree_am.price() - tree_eu.price()

    price_cols[0].metric(
        "Black-Scholes",
        fmt_money(bs_price),
        help="Closed-form European price under Black-Scholes.",
    )
    price_cols[1].metric(
        "Binomial (European)",
        fmt_money(tree_eu.price()),
        help="Cox-Ross-Rubinstein binomial tree, European exercise.",
    )
    price_cols[2].metric(
        "Binomial (American)",
        fmt_money(tree_am.price()),
        delta=(fmt_money(early_exercise) if early_exercise > 0.001 else None),
        delta_color="normal",
        help="CRR tree with early exercise; delta shows the early-exercise premium.",
    )
    if early_exercise > 0.001:
        st.caption(
            f"Early-exercise premium ≈ {fmt_money(early_exercise)} — the American "
            "right to exercise early is worth something here."
        )

    st.divider()

    st.subheader("Greeks")
    st.caption("First-order sensitivities of the Black-Scholes price.")
    g_delta = delta(S, K, T, r, sigma, option_type)
    g_gamma = gamma(S, K, T, r, sigma)
    g_theta = theta(S, K, T, r, sigma, option_type)
    g_vega = vega(S, K, T, r, sigma)
    g_rho = rho(S, K, T, r, sigma, option_type)

    gcols = st.columns(5)
    gcols[0].metric("Delta", f"{g_delta:.4f}", help="∂Price/∂Spot — directional exposure.")
    gcols[1].metric("Gamma", f"{g_gamma:.4f}", help="∂Delta/∂Spot — convexity / hedging cost.")
    gcols[2].metric("Theta", f"{g_theta:.4f}", help="∂Price/∂Time per calendar day — time decay.")
    gcols[3].metric("Vega", f"{g_vega:.4f}", help="∂Price/∂σ per 1% vol move.")
    gcols[4].metric("Rho", f"{g_rho:.4f}", help="∂Price/∂r per 1% rate move.")

    with st.expander("What do the Greeks mean?"):
        st.markdown(
            "- **Delta** — how much the option moves per $1 move in the spot.\n"
            "- **Gamma** — how fast Delta itself changes; high near the money.\n"
            "- **Theta** — daily time decay; usually negative for long options.\n"
            "- **Vega** — sensitivity to a 1-point change in implied volatility.\n"
            "- **Rho** — sensitivity to a 1-point change in interest rates."
        )

    st.divider()

    st.subheader("Payoff at expiry")
    st.caption("Profit & loss at expiration, net of the premium paid.")
    spots = np.linspace(K * 0.7, K * 1.3, 200)
    if option_type == "call":
        payoff = np.maximum(spots - K, 0) - bs_price
    else:
        payoff = np.maximum(K - spots, 0) - bs_price
    st.line_chart(pd.DataFrame({"Spot": spots, "P&L": payoff}), x="Spot", y="P&L")

    st.subheader("Delta vs spot")
    st.caption("How directional exposure shifts as the underlying moves.")
    deltas_call = [delta(s, K, T, r, sigma, "call") for s in spots]
    deltas_put = [delta(s, K, T, r, sigma, "put") for s in spots]
    st.line_chart(
        pd.DataFrame({"Spot": spots, "Call delta": deltas_call, "Put delta": deltas_put}),
        x="Spot",
        y=["Call delta", "Put delta"],
    )

    st.divider()
    st.subheader("Implied-volatility solver")
    st.caption("Back out the volatility implied by a quoted market price for this contract.")
    iv_cols = st.columns([3, 2])
    market_price = iv_cols[0].number_input(
        "Observed market price",
        value=round(bs_price, 4),
        min_value=0.01,
        step=0.1,
        help="A quoted/traded price; we solve for the σ that reproduces it.",
    )
    if iv_cols[1].button("Solve IV", use_container_width=True):
        iv = implied_volatility(market_price, S, K, T, r, option_type)
        if iv is None:
            st.warning(
                "No implied volatility could be solved — the price may be below "
                "intrinsic value or the solver did not converge."
            )
        else:
            st.success(f"Implied volatility: {fmt_pct(iv)}")

# --- Live market tab --------------------------------------------------------
with live_tab:
    st.subheader("Price a real option chain")
    st.caption(
        "Chains from yfinance; spot from Finnhub (set `FINNHUB_API_KEY`) with a "
        "yfinance fallback. If live data is unavailable — or offline mode is on — "
        "the bundled sample chain is used so the demo never breaks."
    )

    lc1, lc2, lc3 = st.columns([2, 2, 1])
    live_symbol = lc1.text_input("Symbol", value="AAPL").strip().upper()
    live_offline = lc3.checkbox(
        "Offline sample", value=force_offline, help="Force the bundled sample chain."
    )

    expiries: list[str] = []
    if not live_symbol:
        st.info("Enter a ticker symbol to load its option expiries.")
    else:
        try:
            with st.spinner(f"Loading expiries for {live_symbol}…"):
                expiries = list_expirations(live_symbol, offline=live_offline)
        except MarketDataError as exc:
            st.warning(
                f"Could not list expirations ({exc}). Falling back to the offline sample.",
                icon="⚠️",
            )
            expiries = list_expirations(live_symbol or "AAPL", offline=True)
            live_offline = True

    live_expiry = lc2.selectbox("Expiry", expiries) if expiries else None
    live_type = st.radio("Option type", ["call", "put"], horizontal=True)

    if st.button("Fetch & price chain", type="primary") and live_expiry:
        try:
            with st.spinner("Fetching and pricing the chain…"):
                priced = price_chain(
                    live_symbol,
                    live_expiry,
                    live_type,
                    r=DEFAULT_RISK_FREE_RATE,
                    offline=live_offline,
                )
        except MarketDataError as exc:
            st.warning(f"Live data unavailable ({exc}); using the offline sample.", icon="⚠️")
            priced = price_chain(live_symbol, live_expiry, live_type, offline=True)

        if priced.empty:
            st.info("That chain came back empty — try another symbol or expiry.")
        else:
            m1, m2, m3 = st.columns(3)
            m1.metric("Spot", fmt_money(priced.attrs.get("spot", float("nan"))))
            m2.metric("Expiry", str(priced.attrs.get("expiry", "—")))
            m3.metric("Time to expiry", f"{priced.attrs.get('T', 0.0):.3f} yr")
            st.caption(f"Risk-free rate r = {DEFAULT_RISK_FREE_RATE:.3%}")
            st.dataframe(priced, width="stretch")

            st.markdown("**Market implied-volatility smile**")
            try:
                plot_market_iv_smile(priced, iv_column="our_iv", save_path=None)
                st.pyplot(plt.gcf())
            finally:
                plt.close("all")

# --- IV surface tab ---------------------------------------------------------
with surface_tab:
    st.subheader("Solved implied-volatility surface")
    st.caption(
        "Fetches option chains across multiple expiries, solves OUR own implied "
        "volatility per (strike, expiry) from market mids via the vectorized "
        "Newton solver, and renders the real solved IV surface plus a per-expiry "
        "smile. Degrades gracefully offline to the bundled sample chain."
    )

    sc1, sc2, sc3 = st.columns([2, 2, 1])
    surf_symbol = sc1.text_input("Symbol", value="AAPL", key="surf_symbol").strip().upper()
    surf_type = sc2.radio("Option type", ["call", "put"], horizontal=True, key="surf_type")
    surf_offline = sc3.checkbox(
        "Offline sample",
        value=force_offline,
        key="surf_offline",
        help="Force the bundled sample chain.",
    )

    max_expiries = st.slider("Max expiries to fetch", 2, 8, 4, key="surf_max_exp")

    if st.button("Build IV surface", key="surf_build", type="primary"):
        # Choose the expiry set. Live: take the nearest N real expiries (falling
        # back to the offline fixture on any failure). Offline: synthesize a
        # spread of T values over the same bundled chain.
        if surf_offline:
            expiries = _offline_surface_expiries()[:max_expiries]
            effective_offline = True
        else:
            try:
                with st.spinner(f"Listing expiries for {surf_symbol}…"):
                    expiries = list_expirations(surf_symbol, offline=False)[:max_expiries]
                effective_offline = False
            except MarketDataError as exc:
                st.warning(f"Could not list expirations ({exc}); using offline sample.", icon="⚠️")
                expiries = _offline_surface_expiries()[:max_expiries]
                effective_offline = True

        try:
            with st.spinner("Fetching chains and solving implied vols…"):
                chains, years, spot = build_surface_chains(
                    surf_symbol, expiries, surf_type, offline=effective_offline
                )
        except MarketDataError as exc:
            st.warning(f"Live data unavailable ({exc}); using offline sample.", icon="⚠️")
            expiries = _offline_surface_expiries()[:max_expiries]
            chains, years, spot = build_surface_chains(
                surf_symbol, expiries, surf_type, offline=True
            )

        if not chains:
            st.info("No usable option data for that symbol/expiries — try another symbol.")
        else:
            st.metric("Spot", fmt_money(spot))
            st.caption(
                f"Solved across {len(chains)} expiries "
                f"({', '.join(sorted(chains))}) · r = {DEFAULT_RISK_FREE_RATE:.3%}"
            )

            with st.spinner("Solving the surface…"):
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

                st.markdown("**Per-expiry IV smile with SVI fit (%)**")
                st.caption(
                    "Points are OUR solved IV; the solid lines are a Gatheral "
                    "raw-SVI fit per expiry (a smile fit/interpolation — not an "
                    "arbitrage-free surface)."
                )
                # Fit raw SVI per expiry and overlay the fitted smile on the
                # solved-IV points. Each expiry gets a points column plus an
                # "<expiry> SVI" line column on a shared strike index.
                svi_fits = fit_svi_surface(surface, spot, r=DEFAULT_RISK_FREE_RATE, q=0.0)
                smile_cols: dict[str, "pd.Series"] = {}
                for expiry in sorted(set(surface["expiry"])):
                    sub = surface[surface["expiry"] == expiry].sort_values("strike")
                    strikes = sub["strike"].to_numpy(dtype=float)
                    smile_cols[expiry] = pd.Series(
                        sub["iv"].to_numpy(dtype=float) * 100.0, index=strikes
                    )
                    params = svi_fits.get(expiry)
                    if params is not None:
                        T_exp = float(sub["T"].iloc[0])
                        forward = spot * np.exp(DEFAULT_RISK_FREE_RATE * T_exp)
                        fitted = svi_smile(params, T_exp, strikes, forward) * 100.0
                        smile_cols[f"{expiry} SVI"] = pd.Series(fitted, index=strikes)
                smile_df = pd.DataFrame(smile_cols).sort_index()
                st.line_chart(smile_df)
                with st.expander("Solved IV table"):
                    st.dataframe(surface, width="stretch")

    # Vectorized batch pricing — price a whole strike grid in one broadcasted call.
    st.divider()
    st.subheader("Vectorized batch pricing")
    st.caption(
        "Price an entire strike grid at once with the vectorized Black-Scholes "
        "kernel (one broadcasted call, no per-strike Python loop)."
    )
    bp1, bp2, bp3 = st.columns(3)
    grid_spot = bp1.number_input("Spot", value=100.0, min_value=0.01, key="grid_spot")
    grid_T = bp2.number_input("T (years)", value=0.25, min_value=0.001, key="grid_T")
    grid_sigma = bp3.number_input("σ", value=0.20, min_value=0.01, key="grid_sigma")
    grid_type = st.radio("Type", ["call", "put"], horizontal=True, key="grid_type")
    strike_grid = np.linspace(grid_spot * 0.7, grid_spot * 1.3, 25)
    grid_prices = black_scholes_price_vec(
        grid_spot, strike_grid, grid_T, DEFAULT_RISK_FREE_RATE, grid_sigma, grid_type
    )
    grid_df = pd.DataFrame({"Strike": strike_grid, "Price": grid_prices})
    st.line_chart(grid_df, x="Strike", y="Price")
