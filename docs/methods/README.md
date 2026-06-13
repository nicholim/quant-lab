# Method documentation

Academic write-ups of the quantitative methods implemented across the monorepo —
what each method is, the mathematics and the reasoning behind it, how to compute
it with the actual repo API, and honest limitations. Every formula here has been
checked term-by-term against the primary source cited in each document.

| Document | Methods | Package(s) |
|----------|---------|------------|
| [SABR & finite-difference pricing](./sabr-and-finite-difference.md) | Hagan (2002) lognormal SABR smile + calibration; Crank–Nicolson finite-difference American pricer with PSOR early exercise | `packages/options-pricing` |
| [Robust covariance & risk attribution](./robust-covariance-and-risk-attribution.md) | EWMA / OAS / Marchenko–Pastur covariance; EWMA & James–Stein means; Euler (MCR/CCR) risk attribution | `packages/portfolio-optimization` |
| [Probabilistic & Deflated Sharpe](./probabilistic-and-deflated-sharpe.md) | PSR & DSR (Bailey–López de Prado) for selection-bias-corrected performance; trade-level analytics (expectancy, payoff ratio, MAE/MFE) | shared `metrics` + `packages/backtesting` |
| [Market-microstructure features](./market-microstructure-features.md) | Size-weighted mid, book imbalance, quoted spread, VWAP, signed trade-flow | `packages/market-data`, `cpp/order-book` |
| [Avellaneda–Stoikov market making](./avellaneda-stoikov-market-making.md) | Inventory-aware optimal quoting (reservation price + optimal spread) on a real matching engine | `cpp/order-book` |

## Scope & honesty conventions

These documents are deliberately precise about what is and isn't implemented:

- **SABR** uses the Hagan asymptotic expansion — accurate near-the-money for
  moderate maturities, **not** arbitrage-free in the deep wings; no negative-rate
  shift.
- The **finite-difference** pricer is single-factor Black–Scholes only (no
  stochastic vol / barriers).
- The **size-weighted mid** is the imbalance-weighted top-of-book price, **not**
  Stoikov's (2018) martingale-adjusted micro-price.
- The book/flow features are **snapshot-level**, **not** event-level Order Flow
  Imbalance (Cont–Kukanov–Stoikov), which needs an incrementally maintained book.
- **PSR/DSR** operate on per-period (de-annualised) Sharpe ratios; DSR's
  trial-variance is a practical estimator that is noisy on small grids.
- **Avellaneda–Stoikov** is the finite-horizon single-asset model; $k$ is
  user-supplied (not auto-calibrated) and fills come from the real engine.

Each document's *Limitations* section states these in full, and points to the
natural next-step extensions (arbitrage-free SABR, Heston, Guéant–Lehalle–
Fernández-Tapia, event-level OFI, etc.).
