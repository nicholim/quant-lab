# SABR and Crank–Nicolson finite differences

This note documents two methods implemented in `packages/options-pricing`: the
**SABR stochastic-volatility smile** (`src/sabr.py`) and the **Crank–Nicolson
finite-difference pricer for American options** (`src/finite_difference.py`).
Both implementations have already been audited as faithful to the literature;
the goal here is to explain *what* they compute, *why* the methods are built
this way, and *how* to call them in this repository. The intended reader is a
competent programmer who knows the basics of options (intrinsic value, the
Black–Scholes price, implied volatility) but is not a volatility-surface
specialist.

---

## SABR stochastic volatility model

### What it is and why desks use it

The single most stubborn fact about traded options is that a single
Black–Scholes volatility does not fit the market: implied volatility varies with
strike (the *smile* / *skew*) and with maturity (the *term structure*). A desk
needs a model that (a) reproduces the observed smile for a given expiry, (b)
interpolates smoothly to strikes that don't trade, and (c) tells you how the
smile *moves* when the underlying moves, so that hedges (the smile-consistent
Greeks) are stable.

SABR — **S**tochastic **A**lpha, **B**eta, **R**ho — is the workhorse for this.
It models the forward price $F$ and its own volatility $\alpha$ as two
correlated diffusions:

```math
dF_t = \alpha_t\, F_t^{\beta}\, dW_t^{1}, \qquad
d\alpha_t = \nu\, \alpha_t\, dW_t^{2}, \qquad
dW_t^{1}\, dW_t^{2} = \rho\, dt .
```

The appeal is that **four intuitive parameters generate an entire smile**, and
each parameter maps onto a feature a trader can see:

- $\alpha$ (`alpha`, the initial vol level) sets the **at-the-money level** of
  the smile. To first order the ATM Black vol is $\alpha / F^{1-\beta}$.
- $\beta$ (`beta`, the CEV elasticity, $0 \le \beta \le 1$) sets the
  **backbone** — how the ATM vol moves as $F$ moves. $\beta = 1$ is lognormal
  (vol roughly constant in $F$), $\beta = 0$ is normal/Bachelier (vol falls as
  $F$ rises), and $\beta = 0.5$ is the CIR-like square-root regime common in
  rates.
- $\rho$ (`rho`, the spot/vol correlation, $-1 < \rho < 1$) sets the **skew** —
  the tilt of the smile. A negative $\rho$ (equities) lifts the downside.
- $\nu$ (`nu`, the vol-of-vol, $\nu \ge 0$) sets the **curvature / convexity** —
  how pronounced the smile's wings are.

Because of this clean separation, SABR fits FX, rates and equity smiles and is
the standard tool for marking and risk-managing vanilla volatility surfaces.

### The Hagan formula

Solving the SABR SDEs for an option price is not closed-form, but Hagan, Kumar,
Lesniewski & Woodward (2002), *Managing Smile Risk*, derived a celebrated
singular-perturbation (asymptotic) expansion for the **Black/Black-76 implied
volatility** $\sigma_B(K, F)$ that the model implies. This is what
`sabr_implied_vol` evaluates. Their eqs. (2.17a–c) give, for $K \neq F$,

```math
\sigma_B(K,F) =
\frac{\alpha}{(FK)^{(1-\beta)/2}\,\big[\,1
  + \tfrac{(1-\beta)^2}{24}\ln^2\!\tfrac{F}{K}
  + \tfrac{(1-\beta)^4}{1920}\ln^4\!\tfrac{F}{K}\,\big]}
\cdot \frac{z}{x(z)} \cdot \big[\,1 + (\,\cdots\,)\,T\,\big],
```

with the dimensionless moneyness variable

```math
z = \frac{\nu}{\alpha}\,(FK)^{(1-\beta)/2}\,\ln\frac{F}{K},
\qquad
x(z) = \ln\!\left[\frac{\sqrt{1 - 2\rho z + z^2} + z - \rho}{1 - \rho}\right],
```

and the time-correction bracket

```math
1 + \left[\,\underbrace{\frac{(1-\beta)^2}{24}\frac{\alpha^2}{(FK)^{1-\beta}}}_{\text{term1}}
  + \underbrace{\frac{1}{4}\frac{\rho\beta\nu\alpha}{(FK)^{(1-\beta)/2}}}_{\text{term2}}
  + \underbrace{\frac{2 - 3\rho^2}{24}\,\nu^2}_{\text{term3}}\,\right] T .
```

Reading the pieces against the code (`F`, `Ka`, `one_m_beta`, `fk_pow`,
`log_fk`, `term1/term2/term3`, `correction`):

- The **prefactor** $\alpha / (FK)^{(1-\beta)/2}[\cdots]$ is the leading-order
  level: the denominator's $\ln^2$ and $\ln^4$ terms are the CEV correction that
  bends the otherwise flat lognormal vol away from the money.
- The factor $z/x(z)$ carries the **skew and convexity in moneyness**. As
  $\nu \to 0$ or $K \to F$ it tends to $1$, recovering the CEV backbone; away
  from the money it tilts (via $\rho$) and steepens (via $\nu$) the wings.
- In the time bracket, **term1** is the pure CEV $\beta$-effect, **term2**
  $\tfrac14 \rho\beta\nu\alpha$ is the leading skew correction coupling
  correlation, backbone and vol-of-vol, and **term3**
  $\tfrac{2-3\rho^2}{24}\nu^2$ is the convexity/vol-of-vol contribution. These
  are an $O(T)$ correction, so they matter more as maturity grows.

**The ATM limit.** When $K = F$, the moneyness $z \to 0$ and the ratio
$z/x(z) \to 1$, giving Hagan's eq. (2.18):

```math
\sigma_{\mathrm{ATM}} = \frac{\alpha}{F^{1-\beta}}
\left[\,1 + \Big(\frac{(1-\beta)^2}{24}\frac{\alpha^2}{F^{2-2\beta}}
  + \frac{1}{4}\frac{\rho\beta\nu\alpha}{F^{1-\beta}}
  + \frac{2-3\rho^2}{24}\nu^2\Big) T\,\right].
```

The implementation handles this explicitly: a $0/0$ would otherwise arise in
$z/x(z)$, so near $K = F$ (detected by `np.isclose`) the ratio is set directly
to $1$ rather than divided.

### Calibration

In practice $\beta$ is **not** fitted freely. $\beta$ and $\rho$ are nearly
collinear (both pull on the skew), so the desk convention is to **fix $\beta$**
by asset class / regime — `fit_sabr_slice` defaults to `beta=0.5` — and
calibrate only the three remaining parameters $(\alpha, \rho, \nu)$ to the
observed implied vols of one expiry slice. The fit is an ordinary nonlinear
least-squares problem,

```math
\min_{\alpha,\rho,\nu}\;
\sum_{i} \big(\sigma_B(K_i,F;\alpha,\beta,\rho,\nu) - \sigma_i^{\text{mkt}}\big)^2,
```

solved with `scipy.optimize.least_squares` (trust-region reflective) under the
bounds `alpha > 0`, `-0.999 < rho < 0.999`, `nu >= 0`. The starting point is
data-driven: $\alpha_0 = \sigma_{\text{ATM}} \cdot F^{1-\beta}$ inverts the ATM
relation, with $\rho_0 = -0.2$, $\nu_0 = 0.4$. By design the function mirrors
the SVI slice fit in `src.vol_surface.fit_svi_slice`, so it consumes the tidy
per-expiry slices produced by `src.greeks_visualizer.solve_iv_surface`.

### How to compute (this repo)

Real signatures:

```python
class SABRParams(NamedTuple):
    alpha: float; beta: float; rho: float; nu: float

def sabr_implied_vol(F, K, T, alpha, beta, rho, nu) -> NDArray[np.float64]
def sabr_smile(params, F, T, strikes) -> NDArray[np.float64]
def fit_sabr_slice(strikes, ivs, F, T, beta=0.5, initial=None, max_nfev=2000) -> SABRParams
```

`sabr_implied_vol` is vectorized over `K` (scalar or array); the other inputs
are scalars. A short worked example — build parameters, evaluate a smile across
strikes, fit to a market slice, and feed the resulting vol into the Black-76
pricer:

```python
import numpy as np
from src.sabr import SABRParams, sabr_implied_vol, sabr_smile, fit_sabr_slice
from src.black_scholes import black_76_price

F, T = 100.0, 0.5
p = SABRParams(alpha=0.20, beta=0.5, rho=-0.3, nu=0.4)

# Smile across strikes (two equivalent calls):
strikes = np.array([80.0, 90.0, 100.0, 110.0, 120.0])
ivs = sabr_implied_vol(F, strikes, T, *p)          # unpack the 4 params
ivs2 = sabr_smile(p, F, T, strikes)                 # convenience wrapper

# Calibrate (alpha, rho, nu) to an observed slice; beta held at 0.5:
mkt = np.array([0.26, 0.22, 0.20, 0.21, 0.24])
fitted = fit_sabr_slice(strikes, mkt, F=F, T=T, beta=0.5)

# Price a 110-strike call off the fitted smile via Black-76:
iv_110 = float(sabr_implied_vol(F, 110.0, T, *fitted))
price = black_76_price(F, 110.0, T, r=0.02, sigma=iv_110, option_type="call")
```

Note that `sabr_implied_vol` raises `ValueError` for non-positive `F`, `T`, or
`K`, and `fit_sabr_slice` raises if fewer than three finite `(strike, iv)`
points survive filtering.

### Limitations (honest)

The Hagan formula is an **asymptotic expansion in $T$ and in log-moneyness**, so
it is accurate for short-to-medium expiries and moderate strikes but **degrades
for very long maturities and for deep ITM/OTM wings**, where higher-order terms
are no longer negligible. More importantly, it is **not arbitrage-free**: the
approximation can imply a negative risk-neutral probability density in extreme
wings (a butterfly-arbitrage violation), which matters if you price far-OTM
exotics off the smile. This implementation is the **unshifted** Hagan vol, so it
**requires $F, K > 0$** and has **no negative-rate (shifted-SABR) variant**. The
arbitrage-free SABR of Hagan et al. (2014), which solves the model's forward
density via a PDE, and full SABR-PDE pricing are **out of scope** here; reach
for a dedicated library when those matter.

---

## Crank–Nicolson finite-difference American pricer

### What it is and why

An American option may be exercised at any time before expiry, which creates an
**early-exercise free boundary**: at each spot and time you must decide whether
continuing is worth more than the intrinsic payoff, and the boundary between
"hold" and "exercise" is itself part of the unknown. There is no closed form.
The two production approaches are lattices (binomial/trinomial trees) and
finite-difference solution of the pricing PDE. The PDE / grid approach is
generally preferred for production because it yields a **smooth solution surface
that can be differentiated on the grid** for stable Greeks, and it generalizes
cleanly to richer dynamics. `fd_price` implements the grid approach.

### The PDE and its discretization

Under Black–Scholes dynamics the option value $V(S,t)$ satisfies

```math
\frac{\partial V}{\partial t}
+ \tfrac12 \sigma^2 S^2 \frac{\partial^2 V}{\partial S^2}
+ (r - q) S \frac{\partial V}{\partial S} - rV = 0 .
```

The implementation transforms to **log-spot** $x = \ln S$, which turns the
$S$-dependent coefficients into **constants** (so the discrete operator is the
same at every node — a uniform tridiagonal system). In $x$ the spatial operator
is

```math
\mathcal{L}V = \tfrac12 \sigma^2 V_{xx}
+ \big(r - q - \tfrac12 \sigma^2\big) V_x - rV,
```

and the code's drift term `nu = r - q - 0.5*sigma**2` is exactly the $V_x$
coefficient. The grid is centered on $\ln S_0$ and spans $\pm$ `n_std`
standard deviations of log-spot ($\sigma\sqrt{T}$); the total node count is
forced **odd** so that $\ln S_0$ lands exactly on a node and the price/Greeks are
read back without interpolation bias.

Time is marched backward from expiry with the **Crank–Nicolson** scheme
($\theta = \tfrac12$), a time-centered average of the implicit and explicit
operators that is second-order accurate in time and unconditionally stable:

```math
\big(I - \theta\, \Delta t\, \mathcal{L}\big)\, v^{n+1}
= \big(I + (1-\theta)\, \Delta t\, \mathcal{L}\big)\, v^{n}.
```

Each step is therefore a **tridiagonal linear solve**; the code builds the three
constant bands `a`, `b`, `c` (coefficients of $V_{i-1}, V_i, V_{i+1}$) and, in
the European case, solves with `scipy.linalg.solve_banded`. The two ends use
**Dirichlet boundary conditions** set to the known asymptotic option values
(`_boundaries`): for a call, $0$ at the low boundary and
$S_{\text{hi}}e^{-q\tau} - Ke^{-r\tau}$ at the high boundary (and symmetrically
for a put), evaluated at the relevant time-to-expiry $\tau$.

A subtlety: pure Crank–Nicolson is only conditionally damping, and the
**non-smooth payoff kink at $K$** excites spurious oscillations in the
near-the-money Greeks. The standard remedy, used here, is **Rannacher startup**
(Rannacher 1984): run the first few steps **fully implicit** ($\theta = 1$,
strongly damping) before switching to CN. In the code, `theta = 1.0 if step <
rannacher_steps else 0.5`, with `rannacher_steps=2` by default.

### Early exercise via PSOR

The American constraint is $V(S,t) \ge \text{payoff}(S)$ everywhere, with
equality on the exercise region. Combined with the PDE this becomes a **linear
complementarity problem (LCP)**: at each step solve $A v = b$ subject to
$v \ge g$ (the obstacle $g$ = payoff), with complementarity between the residual
and the slack. The classical LCP solver for this setting is **Projected
Successive Over-Relaxation (PSOR)** — see Wilmott, and Brennan & Schwartz (1977)
who first applied finite differences to American options.

PSOR is a Gauss–Seidel sweep with two twists, exactly as in `_psor`:

1. **Gauss–Seidel** computes each updated component using already-updated
   neighbors: $\text{gs}_i = \big(b_i - A_{i,i-1}v_{i-1} - A_{i,i+1}v_{i+1}\big)/A_{ii}$.
2. **Over-relaxation** by $\omega > 1$ accelerates convergence:
   $v_i \leftarrow v_i + \omega(\text{gs}_i - v_i)$ (default `omega=1.2`).
3. **Projection** enforces the obstacle at every update:
   $v_i \leftarrow \max(\,\cdot\,, g_i)$ — this is what makes the scheme respect
   early exercise rather than solving the plain linear system.

The sweep repeats until the maximum correction falls below `psor_tol`
(default `1e-8`) or `psor_max_iter` is hit. Once the grid is solved, **delta and
gamma are read off by central differences at $S_0$** (uniform in $x$, hence
non-uniform in $S$, so the differences use the actual spot spacings around the
center node).

### How to compute (this repo)

Real signature and result type:

```python
class FDResult(NamedTuple):
    price: float; delta: float; gamma: float

def fd_price(S, K, T, r, sigma, option_type="call", q=0.0, american=True,
             n_space=200, n_time=200, n_std=6.0, rannacher_steps=2,
             omega=1.2, psor_tol=1e-8, psor_max_iter=10_000) -> FDResult
```

Worked example, with the two built-in cross-checks the package relies on — the
**European limit** against the closed-form `black_scholes_price`, and the
**American** value against the CRR `BinomialTree`:

```python
from src.finite_difference import fd_price
from src.black_scholes import black_scholes_price
from src.binomial_tree import BinomialTree

S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.2

# American put (early exercise via PSOR):
am = fd_price(S, K, T, r, sigma, option_type="put", american=True)
print(am.price, am.delta, am.gamma)

# European limit should match Black-Scholes:
eu = fd_price(S, K, T, r, sigma, option_type="put", american=False)
bs = black_scholes_price(S, K, T, r, sigma, option_type="put")
assert abs(eu.price - bs) < 1e-2

# American value should match a CRR tree within grid tolerance:
crr = BinomialTree(S, K, T, r, sigma, N=2000, option_type="put", american=True).price()
assert abs(am.price - crr) < 1e-2
```

Degenerate inputs are handled in closed form: for `T <= 0` the function returns
intrinsic value, and for `sigma <= 0` the discounted deterministic-forward
intrinsic (the grid diffusion is undefined there). An unknown `option_type`
raises `ValueError`.

### Limitations

This is a **single-factor Black–Scholes PDE** solver only. There is **no
stochastic volatility (no Heston / two-factor PDE), no ADI** splitting for
multi-dimensional problems, no non-uniform mesh refinement around the strike,
and **no barrier or path-dependent payoffs**. For any of those, a specialized
PDE/Monte-Carlo library (e.g. QuantLib) is the right tool.

---

## References

- Hagan, P. S., Kumar, D., Lesniewski, A. S., & Woodward, D. E. (2002).
  *Managing Smile Risk*. **Wilmott Magazine**, September 2002, pp. 84–108. (The
  lognormal-SABR implied-volatility approximation, eqs. 2.17a–c and the ATM
  limit 2.18.)
- Hagan, P. S., Kumar, D., Lesniewski, A. S., & Woodward, D. E. (2014).
  *Arbitrage-Free SABR*. **Wilmott Magazine**, January 2014, pp. 60–75. (The
  PDE-based, density-consistent reformulation of SABR.)
- Rannacher, R. (1984). *Finite element solution of diffusion problems with
  irregular data*. **Numerische Mathematik**, 43(2), 309–327. (The fully-implicit
  startup steps that damp Crank–Nicolson oscillations near payoff kinks.)
- Wilmott, P. (2006). *Paul Wilmott on Quantitative Finance* (2nd ed.). Wiley.
  (Finite-difference methods, American-option LCP, and PSOR.)
- Brennan, M. J., & Schwartz, E. S. (1977). *The Valuation of American Put
  Options*. **Journal of Finance**, 32(2), 449–462. (Finite-difference solution
  of the American free-boundary problem.)
- Crank, J., & Nicolson, P. (1947). *A practical method for numerical evaluation
  of solutions of partial differential equations of the heat-conduction type*.
  **Proceedings of the Cambridge Philosophical Society**, 43(1), 50–67. (The
  time-centered $\theta = \tfrac12$ scheme.)
- Cox, J. C., Ross, S. A., & Rubinstein, M. (1979). *Option Pricing: A
  Simplified Approach*. **Journal of Financial Economics**, 7(3), 229–263. (The
  CRR binomial tree used as the American benchmark.)
- Black, F. (1976). *The Pricing of Commodity Contracts*. **Journal of Financial
  Economics**, 3(1–2), 167–179. (The Black-76 model into which SABR vols feed.)
