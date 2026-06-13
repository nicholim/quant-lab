import argparse

from src.binomial_tree import BinomialTree
from src.black_scholes import (
    black_76_price,
    black_scholes_price,
    charm,
    delta,
    gamma,
    implied_volatility,
    rho,
    theta,
    vanna,
    vega,
    volga,
)
from src.finite_difference import fd_price
from src.greeks_visualizer import (
    plot_greeks_vs_spot,
    plot_greeks_vs_time,
    plot_payoff_diagram,
    plot_volatility_surface,
)
from src.market_data import (
    DEFAULT_RISK_FREE_RATE,
    MarketDataError,
    list_expirations,
    price_chain,
)
from src.monte_carlo import monte_carlo_price
from src.sabr import SABRParams, sabr_implied_vol


def run_live(
    symbol: str,
    expiry: str | None,
    option_type: str = "call",
    offline: bool = False,
) -> None:
    """Fetch + price a REAL option chain and print it."""
    print("=" * 72)
    print(f"Live option chain — {symbol.upper()} ({option_type})")
    print("=" * 72)
    try:
        if expiry is None:
            expiry = list_expirations(symbol, offline=offline)[0]
        priced = price_chain(symbol, expiry, option_type, r=DEFAULT_RISK_FREE_RATE, offline=offline)
    except MarketDataError as exc:
        print(f"\nMarket data unavailable: {exc}")
        print("Tip: retry with --offline to use the bundled sample chain.")
        return

    spot = priced.attrs.get("spot")
    print(
        f"\nSpot: ${spot:.2f}   Expiry: {priced.attrs.get('expiry')}   "
        f"T: {priced.attrs.get('T'):.4f}y   r: {DEFAULT_RISK_FREE_RATE:.3f}"
    )
    print(f"\n{'strike':>8} {'mid':>9} {'model':>9} {'our_iv':>8} {'mkt_iv':>8} {'mispr':>9}")
    print("-" * 56)
    for row in priced.itertuples(index=False):
        our_iv = f"{row.our_iv:.1%}" if row.our_iv is not None else "   n/a"
        print(
            f"{row.strike:>8.2f} {row.mid:>9.2f} {row.model_price:>9.2f} "
            f"{our_iv:>8} {row.market_iv:>8.1%} {row.mispricing:>9.2f}"
        )


def main() -> None:
    S, K, T, r, sigma = 100.0, 105.0, 0.25, 0.05, 0.20

    print("=" * 60)
    print("Options Pricing Calculator")
    print("=" * 60)

    for opt_type in ["call", "put"]:
        bs = black_scholes_price(S, K, T, r, sigma, opt_type)

        tree_eu = BinomialTree(S, K, T, r, sigma, N=200, option_type=opt_type, american=False)
        tree_am = BinomialTree(S, K, T, r, sigma, N=200, option_type=opt_type, american=True)

        print(f"\n--- European {opt_type.upper()} (S={S}, K={K}, T={T}, r={r}, σ={sigma}) ---")
        print(f"  Black-Scholes:  ${bs:.4f}")
        print(f"  Binomial (EU):  ${tree_eu.price():.4f}")
        print(f"  Binomial (AM):  ${tree_am.price():.4f}")
        print(f"  Delta:  {delta(S, K, T, r, sigma, opt_type):.6f}")
        print(f"  Gamma:  {gamma(S, K, T, r, sigma):.6f}")
        print(f"  Theta:  {theta(S, K, T, r, sigma, opt_type):.6f} (daily)")
        print(f"  Vega:   {vega(S, K, T, r, sigma):.6f} (per 1%)")
        print(f"  Rho:    {rho(S, K, T, r, sigma, opt_type):.6f} (per 1%)")
        print("  Higher-order Greeks:")
        print(f"    Vanna:  {vanna(S, K, T, r, sigma):.6f}")
        print(f"    Volga:  {volga(S, K, T, r, sigma):.6f}")
        print(f"    Charm:  {charm(S, K, T, r, sigma, opt_type):.6f}")

    # Black-76 — European option on a future/forward (no spot carry)
    F = S * 1.02
    print(f"\n--- Black-76 futures option (F={F}, K={K}, T={T}, r={r}, σ={sigma}) ---")
    print(f"  Call: ${black_76_price(F, K, T, r, sigma, 'call'):.4f}")
    print(f"  Put:  ${black_76_price(F, K, T, r, sigma, 'put'):.4f}")

    # Monte-Carlo pricer (under GBM, antithetic + control variate) vs closed-form
    print("\n--- Monte-Carlo (GBM, antithetic + control variate) vs closed-form ---")
    for opt_type in ["call", "put"]:
        bs = black_scholes_price(S, K, T, r, sigma, opt_type)
        mc = monte_carlo_price(S, K, T, r, sigma, opt_type, n_paths=200_000, seed=42)
        print(
            f"  {opt_type.upper():>4}: MC ${mc.price:.4f} ± {mc.std_error:.4f} (SE)  "
            f"vs BS ${bs:.4f}  (diff {abs(mc.price - bs):.4f})"
        )

    # Crank-Nicolson finite-difference American pricer vs the binomial benchmark
    print("\n--- Crank-Nicolson FD (American) vs binomial benchmark ---")
    for opt_type in ["call", "put"]:
        bt = BinomialTree(S, K, T, r, sigma, N=1000, option_type=opt_type, american=True).price()
        fd = fd_price(S, K, T, r, sigma, opt_type, american=True, n_space=400, n_time=400)
        print(
            f"  {opt_type.upper():>4}: FD ${fd.price:.4f} (Δ {fd.delta:+.4f}, Γ {fd.gamma:.4f})  "
            f"vs binomial ${bt:.4f}  (diff {abs(fd.price - bt):.4f})"
        )

    # SABR (Hagan 2002) smile -> Black-76 price for an ITM strike
    print("\n--- SABR (Hagan 2002) smile -> Black-76 price ---")
    sabr = SABRParams(alpha=0.30, beta=0.5, rho=-0.30, nu=0.40)
    F_sabr = S * 1.02
    for strike in (90.0, F_sabr, 120.0):
        iv = float(sabr_implied_vol(F_sabr, strike, T, *sabr))
        px = black_76_price(F_sabr, strike, T, r, iv, "call")
        print(f"  K={strike:>6.2f}: SABR Black vol {iv:.2%} -> Black-76 call ${px:.4f}")

    # Implied volatility
    market_price = 3.50
    iv = implied_volatility(market_price, S, K, T, r, "call")
    print(f"\nImplied Vol for market price ${market_price}: {iv:.4%}")

    # Visualizations
    print("\nGenerating visualizations...")
    plot_greeks_vs_spot(K=K, T=T, r=r, sigma=sigma)
    plot_greeks_vs_time(S=S, K=K, r=r, sigma=sigma)
    plot_volatility_surface(S=S)
    plot_payoff_diagram(K=K, premium=black_scholes_price(S, K, T, r, sigma, "call"))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Options pricing calculator (textbook demo, or live chain pricing)."
    )
    parser.add_argument("--symbol", help="Ticker to price a live chain for (e.g. AAPL).")
    parser.add_argument("--expiry", help="Expiry YYYY-MM-DD (default: nearest available).")
    parser.add_argument("--type", choices=["call", "put"], default="call", dest="option_type")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use the bundled sample chain instead of the network.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":  # pragma: no cover
    _args = _parse_args()
    if _args.symbol:
        run_live(_args.symbol, _args.expiry, _args.option_type, offline=_args.offline)
    else:
        main()
