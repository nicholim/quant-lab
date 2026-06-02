from typing import TYPE_CHECKING

import matplotlib.pyplot as plt
import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

from .black_scholes import (
    black_scholes_price,
    delta,
    gamma,
    implied_volatility_vec,
    rho,
    theta,
    vega,
)


def plot_greeks_vs_spot(
    K: float = 100,
    T: float = 0.25,
    r: float = 0.05,
    sigma: float = 0.2,
    save_path: str | None = None,
) -> None:
    """Plot all Greeks as a function of spot price."""
    spots = np.linspace(K * 0.7, K * 1.3, 200)

    fig, axes = plt.subplots(2, 3, figsize=(16, 10))
    fig.suptitle(f"Greeks vs Spot Price (K={K}, T={T:.2f}y, σ={sigma:.0%})", fontsize=14)

    for opt_type, color in [("call", "steelblue"), ("put", "coral")]:
        prices = [black_scholes_price(s, K, T, r, sigma, opt_type) for s in spots]
        deltas = [delta(s, K, T, r, sigma, opt_type) for s in spots]
        gammas = [gamma(s, K, T, r, sigma) for s in spots]
        thetas = [theta(s, K, T, r, sigma, opt_type) for s in spots]
        vegas = [vega(s, K, T, r, sigma) for s in spots]
        rhos = [rho(s, K, T, r, sigma, opt_type) for s in spots]

        axes[0, 0].plot(spots, prices, color=color, label=opt_type)
        axes[0, 1].plot(spots, deltas, color=color, label=opt_type)
        axes[0, 2].plot(spots, gammas, color=color, label=opt_type)
        axes[1, 0].plot(spots, thetas, color=color, label=opt_type)
        axes[1, 1].plot(spots, vegas, color=color, label=opt_type)
        axes[1, 2].plot(spots, rhos, color=color, label=opt_type)

    titles = ["Price", "Delta", "Gamma", "Theta (daily)", "Vega (per 1%)", "Rho (per 1%)"]
    for ax, title in zip(axes.flat, titles, strict=False):
        ax.set_title(title)
        ax.set_xlabel("Spot Price")
        ax.axvline(K, color="gray", linestyle="--", alpha=0.5, label="Strike")
        ax.legend()
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()


def plot_greeks_vs_time(
    S: float = 100,
    K: float = 100,
    r: float = 0.05,
    sigma: float = 0.2,
    save_path: str | None = None,
) -> None:
    """Plot Greeks as a function of time to expiry."""
    times = np.linspace(0.01, 1.0, 200)

    fig, axes = plt.subplots(2, 3, figsize=(16, 10))
    fig.suptitle(f"Greeks vs Time to Expiry (S={S}, K={K}, σ={sigma:.0%})", fontsize=14)

    for opt_type, color in [("call", "steelblue"), ("put", "coral")]:
        prices = [black_scholes_price(S, K, t, r, sigma, opt_type) for t in times]
        deltas = [delta(S, K, t, r, sigma, opt_type) for t in times]
        gammas = [gamma(S, K, t, r, sigma) for t in times]
        thetas = [theta(S, K, t, r, sigma, opt_type) for t in times]
        vegas = [vega(S, K, t, r, sigma) for t in times]
        rhos = [rho(S, K, t, r, sigma, opt_type) for t in times]

        axes[0, 0].plot(times, prices, color=color, label=opt_type)
        axes[0, 1].plot(times, deltas, color=color, label=opt_type)
        axes[0, 2].plot(times, gammas, color=color, label=opt_type)
        axes[1, 0].plot(times, thetas, color=color, label=opt_type)
        axes[1, 1].plot(times, vegas, color=color, label=opt_type)
        axes[1, 2].plot(times, rhos, color=color, label=opt_type)

    titles = ["Price", "Delta", "Gamma", "Theta (daily)", "Vega (per 1%)", "Rho (per 1%)"]
    for ax, title in zip(axes.flat, titles, strict=False):
        ax.set_title(title)
        ax.set_xlabel("Time to Expiry (years)")
        ax.legend()
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()


def plot_price_surface(
    S: float = 100,
    K_range: tuple[float, float] = (80, 120),
    T_range: tuple[float, float] = (0.05, 1.0),
    r: float = 0.05,
    sigma: float = 0.2,
    save_path: str | None = None,
) -> None:
    """3D surface plot of option PRICE across strikes and maturities.

    Note: this holds volatility CONSTANT, so it is a price surface, not a
    volatility surface. For the real implied-vol smile/surface from market
    data see :func:`plot_market_iv_smile` / :func:`plot_market_iv_surface`.
    """
    strikes = np.linspace(K_range[0], K_range[1], 50)
    maturities = np.linspace(T_range[0], T_range[1], 50)
    K_grid, T_grid = np.meshgrid(strikes, maturities)

    prices = np.array(
        [[black_scholes_price(S, k, t, r, sigma, "call") for k in strikes] for t in maturities]
    )

    fig = plt.figure(figsize=(14, 9))
    ax = fig.add_subplot(111, projection="3d")
    # 3D-axes methods exist at runtime (Axes3D) but aren't on matplotlib's Axes stub.
    ax.plot_surface(K_grid, T_grid, prices, cmap="viridis", alpha=0.8)  # type: ignore[attr-defined]
    ax.set_xlabel("Strike Price")
    ax.set_ylabel("Time to Expiry (years)")
    ax.set_zlabel("Call Price")  # type: ignore[attr-defined]
    ax.set_title(f"Option Price Surface (S={S}, σ={sigma:.0%})")

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()


# Backward-compatible alias: the old name actually plotted price at constant
# sigma. Kept so existing imports/tests don't break. New code should use
# plot_price_surface (a price surface) or plot_market_iv_* (the real IV surface).
plot_volatility_surface = plot_price_surface


def plot_payoff_diagram(
    K: float = 100,
    premium: float = 5.0,
    option_type: str = "call",
    save_path: str | None = None,
) -> None:
    """P&L diagram at expiry."""
    spots = np.linspace(K * 0.7, K * 1.3, 200)

    if option_type == "call":
        pnl_long = np.maximum(spots - K, 0) - premium
        pnl_short = -pnl_long
    else:
        pnl_long = np.maximum(K - spots, 0) - premium
        pnl_short = -pnl_long

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(spots, pnl_long, "steelblue", linewidth=2, label=f"Long {option_type}")
    ax.plot(spots, pnl_short, "coral", linewidth=2, label=f"Short {option_type}")
    ax.axhline(0, color="black", linewidth=0.5)
    ax.axvline(K, color="gray", linestyle="--", alpha=0.5, label="Strike")
    # numpy bool arrays are valid `where` masks at runtime; matplotlib's stub wants Sequence[bool].
    ax.fill_between(spots, pnl_long, 0, where=pnl_long > 0, alpha=0.1, color="green")  # type: ignore[arg-type]
    ax.fill_between(spots, pnl_long, 0, where=pnl_long < 0, alpha=0.1, color="red")  # type: ignore[arg-type]
    ax.set_title(f"{option_type.title()} Option Payoff (K={K}, Premium={premium:.2f})")
    ax.set_xlabel("Spot Price at Expiry")
    ax.set_ylabel("Profit / Loss")
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()


def plot_market_iv_smile(
    chain_df: "pd.DataFrame",
    iv_column: str = "our_iv",
    title: str | None = None,
    save_path: str | None = None,
) -> None:
    """Plot the REAL implied-volatility smile (IV vs strike) from a chain.

    This is the genuine volatility smile that :func:`plot_price_surface`
    (the old ``plot_volatility_surface``) only pretended to be. ``chain_df`` is
    a chain DataFrame (e.g. from ``market_data.price_chain``) with a ``strike``
    column and the IV column named by ``iv_column`` (``our_iv`` or
    ``market_iv``). Rows with missing/zero IV are dropped.
    """
    df = chain_df.dropna(subset=[iv_column])
    df = df[df[iv_column] > 0]
    strikes = df["strike"].to_numpy(dtype=float)
    ivs = df[iv_column].to_numpy(dtype=float) * 100.0

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(strikes, ivs, "o-", color="steelblue", label=iv_column)
    if iv_column == "our_iv" and "market_iv" in df:
        mkt = df["market_iv"].to_numpy(dtype=float) * 100.0
        ax.plot(strikes, mkt, "s--", color="coral", alpha=0.7, label="market_iv")
    ax.set_title(title or "Implied Volatility Smile")
    ax.set_xlabel("Strike")
    ax.set_ylabel("Implied Volatility (%)")
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()


def plot_market_iv_surface(
    chains_by_expiry: dict[str, "pd.DataFrame"],
    iv_column: str = "our_iv",
    title: str | None = None,
    save_path: str | None = None,
) -> None:
    """Plot a 3D implied-vol surface (IV vs strike vs expiry) from live chains.

    ``chains_by_expiry`` maps each expiry (``YYYY-MM-DD``) to a chain DataFrame
    carrying ``strike`` and ``iv_column``. Expiries are plotted as integer
    series indices on the time axis (labels are the dates).
    """
    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(111, projection="3d")

    expiries = sorted(chains_by_expiry)
    for idx, expiry in enumerate(expiries):
        df = chains_by_expiry[expiry].dropna(subset=[iv_column])
        df = df[df[iv_column] > 0]
        strikes = df["strike"].to_numpy(dtype=float)
        ivs = df[iv_column].to_numpy(dtype=float) * 100.0
        xs = np.full_like(strikes, idx, dtype=float)
        ax.plot(xs, strikes, ivs, "o-", alpha=0.8, label=expiry)

    ax.set_xticks(range(len(expiries)))
    ax.set_xticklabels(expiries, rotation=30, ha="right", fontsize=8)
    ax.set_xlabel("Expiry")
    ax.set_ylabel("Strike")
    ax.set_zlabel("Implied Volatility (%)")  # type: ignore[attr-defined]
    ax.set_title(title or "Implied Volatility Surface")

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()


def solve_iv_surface(
    chains_by_expiry: "dict[str, pd.DataFrame]",
    spot: float,
    expiry_years: "dict[str, float]",
    r: float = 0.045,
    option_type: str = "call",
    q: float = 0.0,
) -> "pd.DataFrame":
    """Solve OUR implied vol per (strike, time-to-expiry) for a multi-expiry chain.

    This is the data behind the *real* IV surface: for each expiry it solves our
    own Black-Scholes IV from the market ``mid`` via the VECTORIZED solver
    (:func:`implied_volatility_vec`) -- a whole chain in one broadcasted Newton
    pass, no python loop per contract -- and stacks the results.

    Args:
        chains_by_expiry: maps expiry (``YYYY-MM-DD``) -> chain DataFrame with at
            least ``strike`` and ``mid`` columns (e.g. from ``market_data``).
        spot: underlying spot price.
        expiry_years: maps each expiry -> its time-to-expiry in years (``T``).
        r: risk-free rate; ``option_type``/``q`` passed through to the solver.

    Returns:
        A tidy DataFrame with columns ``expiry``, ``T``, ``strike``, ``iv``
        (our solved IV as a fraction). Contracts whose IV does not solve are
        dropped (the solver returns ``nan`` for them; never raises).
    """
    import numpy as np
    import pandas as pd

    frames: list[pd.DataFrame] = []
    for expiry in sorted(chains_by_expiry):
        chain = chains_by_expiry[expiry]
        T = expiry_years[expiry]
        strikes = chain["strike"].to_numpy(dtype=float)
        mids = chain["mid"].to_numpy(dtype=float)
        iv = implied_volatility_vec(mids, spot, strikes, T, r, option_type, q)
        frame = pd.DataFrame({"expiry": expiry, "T": T, "strike": strikes, "iv": iv})
        frame = frame[np.isfinite(frame["iv"].to_numpy())]
        frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["expiry", "T", "strike", "iv"])
    return pd.concat(frames, ignore_index=True)


def plot_solved_iv_surface(
    chains_by_expiry: "dict[str, pd.DataFrame]",
    spot: float,
    expiry_years: "dict[str, float]",
    r: float = 0.045,
    option_type: str = "call",
    q: float = 0.0,
    title: str | None = None,
    save_path: str | None = None,
) -> "pd.DataFrame":
    """Plot a *real* implied-vol surface: OUR solved IV over strike x T.

    Unlike :func:`plot_price_surface` (price at constant sigma), this solves the
    implied volatility per contract from market mids via the vectorized solver
    and plots IV as the z-axis over strike (y) and time-to-expiry in years (x).
    Returns the tidy IV DataFrame from :func:`solve_iv_surface` for inspection.
    Headless-safe (uses whatever backend is active; tests force ``Agg``).
    """
    surface = solve_iv_surface(chains_by_expiry, spot, expiry_years, r, option_type, q)

    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(111, projection="3d")
    for expiry in sorted(set(surface["expiry"])):
        sub = surface[surface["expiry"] == expiry].sort_values("strike")
        ax.plot(
            sub["T"].to_numpy(dtype=float),
            sub["strike"].to_numpy(dtype=float),
            sub["iv"].to_numpy(dtype=float) * 100.0,
            "o-",
            alpha=0.8,
            label=expiry,
        )
    ax.set_xlabel("Time to Expiry (years)")
    ax.set_ylabel("Strike")
    ax.set_zlabel("Implied Volatility (%)")  # type: ignore[attr-defined]
    ax.set_title(title or "Solved Implied Volatility Surface")
    if len(set(surface["expiry"])) > 0:
        ax.legend(fontsize=8)

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()
    return surface
