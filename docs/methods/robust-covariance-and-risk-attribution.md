# Robust Covariance / Mean Estimation and Risk Attribution

*Method documentation for `packages/portfolio-optimization`
(`portfolio_optimization_engine.covariance`, `.estimators`, `.optimizer`).*

This document explains **why** the estimators in this package exist and how their
math works, for a reader who knows mean-variance basics but not estimation
theory. The formulas described here are implemented exactly as written; this is
documentation, not a change.

---

## Why estimation matters

Markowitz mean-variance optimization takes two inputs — a vector of expected
returns $\mu$ and a covariance matrix $\Sigma$ — and returns the weights $w$
that maximize the risk-adjusted objective (e.g. maximize $w^\top\mu$ for a given
$w^\top\Sigma w$). The textbook treats $\mu$ and $\Sigma$ as *known*. In
practice they are **estimated** from a finite, noisy return sample, and the
optimizer does not know the difference between signal and estimation error.

The damaging fact is that mean-variance optimization is an **"error
maximizer"** (Michaud, 1989). The optimizer actively seeks out assets that
*appear* to offer high return per unit of risk. Those are precisely the assets
whose estimates were pushed up by lucky sampling noise, and the optimizer
leverages into them. So the procedure systematically over-weights the most
*over-estimated* assets and under-weights the most *under-estimated* ones — it
amplifies estimation error rather than averaging it out. The result is unstable,
extreme, often negatively-correlated-with-truth allocations that look great
in-sample and disappoint out-of-sample.

Chopra & Ziemba (1993) quantified the relative damage: errors in **expected
returns** are roughly an order of magnitude more costly than errors in
variances, which in turn are more costly than errors in covariances (the exact
ratio scales with risk tolerance). The practical takeaway that organizes this
whole package: **the quality of your estimates of $\mu$ and $\Sigma$ dominates
out-of-sample performance far more than the choice of objective function.** A
robust estimator plugged into a plain optimizer beats a clever objective fed
garbage inputs.

Two specific failure modes motivate the covariance estimators below.

**The sample covariance is ill-conditioned in the random-matrix regime.** With
$n$ assets and $T$ observations, the sample covariance $S$ has on the order of
$n(n+1)/2$ free parameters estimated from $nT$ numbers. As the ratio $q = n/T$
approaches 1, $S$ becomes nearly singular: its smallest eigenvalues are biased
toward zero and its largest toward infinity, even when the *true* covariance is
well-behaved. Mean-variance weights depend on $\Sigma^{-1}$, so the optimizer
tilts violently into those artificially-small-variance eigen-directions — the
exact directions that are pure noise. When $n > T$ the sample covariance is
outright singular and cannot be inverted at all.

**The sample mean is notoriously noisy.** The standard error of an asset's mean
return is $\sigma/\sqrt{T}$, and for typical equities the per-period $\sigma$ is
so large relative to the true drift that you need decades of data to estimate
$\mu$ to even one significant figure. Combined with Chopra-Ziemba, this is why
sample means are the single weakest input in the pipeline.

---

## Covariance estimators

All covariance estimators in this package operate on a `(T, n)` matrix of
**per-period** (daily) returns and return an `(n, n)` matrix on the same
per-period scale as `np.cov(returns, rowvar=False)`. Annualization (`* 252`) is
applied by the optimizer afterward, not inside the estimators.

### EWMA / RiskMetrics

The equally-weighted sample covariance implicitly assumes returns are
identically distributed across the whole window — that volatility today equals
volatility a year ago. Markets violate this badly: volatility **clusters** (calm
and turbulent regimes persist) and the covariance structure drifts. The
exponentially-weighted moving-average (EWMA) covariance addresses this by
weighting recent observations more.

This implementation is the **normalized batch-weighted** form. For a sample of
$T$ rows, the observation at lag $k$ from the most recent row receives weight

$$ w_k \;=\; \frac{(1-\lambda)\,\lambda^{k}}{\sum_{j=0}^{T-1}(1-\lambda)\,\lambda^{j}}, \qquad k = 0, 1, \dots, T-1, $$

(so the weights sum to 1), and the covariance is the weighted cross-product
about the **weighted** mean $\bar x = \sum_k w_k x_k$:

$$ \widehat\Sigma_{\text{EWMA}} \;=\; \sum_{k} w_k\,(x_k - \bar x)(x_k - \bar x)^\top. $$

The decay factor $\lambda = 0.94$ is the RiskMetrics daily default (J.P. Morgan,
1996). Larger $\lambda$ means slower decay and longer memory; smaller $\lambda$
tracks the latest regime more aggressively. Note this is **not** the recursive
RiskMetrics recursion $\sigma_t^2 = \lambda\sigma_{t-1}^2 + (1-\lambda)r_t^2$;
the batch-weighted and recursive forms are both legitimate and asymptotically
equivalent — the batch form re-normalizes a finite window so the weights sum
exactly to one, which is what is implemented here.

**How to compute (this repo)**

```python
from portfolio_optimization_engine.covariance import ewma_covariance, estimate_covariance

cov = ewma_covariance(returns, lam=0.94)          # per-period (n, n) matrix
cov = estimate_covariance(returns, "ewma", lam=0.94)
```

### OAS — Oracle Approximating Shrinkage

**Why shrinkage works.** The sample covariance $S$ is unbiased but
high-variance; a structured *target* $F$ (here a scaled identity) is biased but
low-variance. A convex combination

$$ \widehat\Sigma \;=\; (1-\rho)\,S + \rho\,F $$

trades a little bias for a large reduction in variance — the classic
bias-variance tradeoff. Geometrically, shrinkage pulls the dispersed,
noise-inflated eigenvalues of $S$ back toward their common mean: it lifts the
artificially tiny eigenvalues (the ones the optimizer abuses) and pulls down the
artificially large ones, producing a far better-conditioned, always-invertible
estimate.

The target used here is the **scaled identity** $F = \mu I$ with
$\mu = \operatorname{tr}(S)/p$ (the average sample variance), where $p$ is the
number of assets. This target assumes equal variances and zero correlations —
maximally structured, maximally biased, minimal variance.

The intensity $\rho$ is chosen by the **OAS** closed form (Chen, Wiesel, Eldar &
Hero, 2010, eq. 23). With $S$ the MLE sample covariance ($\div T$), $p$ the
number of assets and $T$ the number of observations:

$$ \rho \;=\; \frac{\left(1-\tfrac{2}{p}\right)\operatorname{tr}(S^2) + \big(\operatorname{tr} S\big)^2} {\left(T+1-\tfrac{2}{p}\right)\!\left(\operatorname{tr}(S^2) - \tfrac{(\operatorname{tr} S)^2}{p}\right)}, $$

clipped to $[0,1]$ (and set to 1 when the denominator is non-positive). In the
code, `tr_s2` $= \operatorname{tr}(S^2) = \sum_{ij} S_{ij}^2$ and `tr_s_2`
$= (\operatorname{tr} S)^2$; `n` is $p$ (assets) and `T` is observations.

**How OAS differs from Ledoit-Wolf.** Ledoit-Wolf (also in this module as
`ledoit_wolf_shrinkage`) estimates the *asymptotically optimal* intensity that
minimizes expected Frobenius distance to the true $\Sigma$. OAS instead
**iteratively approximates the oracle** intensity — the intensity you would pick
if you actually knew $\Sigma$ — and its closed form converges to that oracle
faster under Gaussian assumptions, which often makes it better in **small
samples**. Ledoit-Wolf here also offers a richer `constant_correlation` target;
OAS is hard-wired to the scaled identity.

**How to compute (this repo)**

```python
from portfolio_optimization_engine.covariance import oas_shrinkage, estimate_covariance

cov, intensity = oas_shrinkage(returns)           # intensity == rho in [0, 1]
cov = estimate_covariance(returns, "oas")         # discards the intensity
```

### Marchenko–Pastur denoising

Shrinkage pulls *every* eigenvalue toward the mean, including the genuine
signal-carrying ones. Random-matrix theory (RMT) offers a more surgical
alternative: clean only the eigenvalues that are *statistically
indistinguishable from noise* and leave the signal untouched (López de Prado,
*Machine Learning for Asset Managers*, 2020, ch. 2).

The key result is the **Marchenko–Pastur distribution**: if returns were pure
noise (a covariance equal to the identity), the eigenvalues of the empirical
*correlation* matrix would not be all 1 — they would spread into a predictable
"bulk" bounded above by

$$ \lambda_+ \;=\; \sigma^2\left(1 + \sqrt{n/T}\right)^2, \qquad \sigma^2 = 1 \text{ for a correlation matrix}. $$

So any empirical eigenvalue **below** $\lambda_+$ is consistent with pure noise
and carries no reliable information; eigenvalues **above** $\lambda_+$ are
"signal" that the noise model cannot explain.

The procedure implemented here:

1. Convert $S$ to a **correlation** matrix and eigendecompose it
   ($C = V\Lambda V^\top$, symmetric so eigenvalues are real).
2. Compute the edge $\lambda_+ = (1 + \sqrt{n/T})^2$.
3. Replace every eigenvalue **below** $\lambda_+$ with their common **mean**
   (the noise eigenvalues are flattened to one value); keep the signal
   eigenvalues unchanged.
4. Rebuild the correlation matrix $V\widetilde\Lambda V^\top$, then **rescale to
   a unit diagonal** so the correlation trace is preserved ($=n$).
5. Scale back to a covariance using the original standard deviations.

Averaging the noise eigenvalues (rather than discarding them) keeps the matrix
**trace-preserving and positive semi-definite** while removing the spurious
dispersion the optimizer would otherwise exploit. Because it touches only the
noise bulk, it cleans the matrix **without shrinking the signal** — its main
advantage over uniform shrinkage. It requires $T > n$ (more periods than assets)
so the bulk edge is well-defined and the correlation matrix is full-rank.

**How to compute (this repo)**

```python
from portfolio_optimization_engine.covariance import mp_denoise, estimate_covariance

cov = mp_denoise(returns)                          # requires T > n
cov = estimate_covariance(returns, "mp")
```

---

## Mean estimators

The mean estimators operate on the same `(T, n)` per-period returns and return a
length-`n` per-period mean vector on the same scale as `returns.mean(axis=0)`.

### EWMA mean

The same recency argument as the EWMA covariance applies to the first moment:
recent returns are more representative of the current regime. The EWMA mean
weights observation at lag $k$ proportionally to $\lambda^k$, normalized to sum
to 1:

$$ \widehat\mu_{\text{EWMA}} \;=\; \sum_{k} \tilde w_k\, x_k, \qquad \tilde w_k = \frac{\lambda^{k}}{\sum_j \lambda^{j}}. $$

(There is no $(1-\lambda)$ factor here as in the covariance, because it cancels
in a normalized first-moment average.) This is a tilt toward recency, not a
variance-reduction estimator like James-Stein below.

**How to compute (this repo)**

```python
from portfolio_optimization_engine.estimators import ewma_mean, estimate_mean

mu = ewma_mean(returns, lam=0.94)
mu = estimate_mean(returns, "ewma", lam=0.94)
```

### James–Stein shrinkage

Here is one of the genuine surprises of statistics. The sample mean is the
maximum-likelihood, unbiased estimator of $\mu$ — and yet **Stein's paradox**
(James & Stein, 1961) says that in **three or more dimensions** it is
*inadmissible*: there exists another estimator with uniformly lower total
mean-squared error, regardless of the true $\mu$. The trick is to **shrink** all
the individual means toward a common point. Even though shrinking biases each
component, the joint reduction in variance more than pays for it. Efron & Morris
(1973) gave the famous intuition: estimating many means together, you should
"borrow strength" across them rather than treat each in isolation.

In finance this is exactly the cure for the noisy-sample-mean problem. Jorion
(1986) introduced the Bayes-Stein estimator for portfolio inputs, shrinking each
asset's mean toward a common prior and demonstrably improving out-of-sample
allocations.

This package shrinks each asset's sample mean toward the **grand mean** $\bar x$
(the cross-sectional average across assets) by a data-estimated, positive-part
factor:

$$ \widehat\mu_i \;=\; \bar x + (1-s)\,(\hat\mu_i - \bar x), \qquad s \;=\; \left(\frac{(p-3)\,\sigma^2}{\lVert \hat\mu - \bar x\rVert^2}\right)_{\!+}, $$

where $\hat\mu$ is the sample mean vector, $\sigma^2$ is the (pooled) variance of
the sample mean $= \overline{\operatorname{var}(x)}/T$,
$\lVert\hat\mu-\bar x\rVert^2$ is the cross-sectional dispersion of the means,
and $(\cdot)_+$ clips the factor to $[0,1]$. The positive part guarantees the
shrunk mean never overshoots past the grand mean.

The intuition for the shrinkage factor: when the asset means are tightly bunched
($\lVert\hat\mu-\bar x\rVert^2$ small) the dispersion is probably just noise, so
$s\to 1$ and we shrink almost entirely to the grand mean; when they are widely
spread, the spread is likely real signal, so $s\to 0$ and we trust the sample
means.

**Why $(p-3)$ and not the classic $(p-2)$.** The textbook James-Stein constant
is $(p-2)$ when shrinking toward a *fixed, known* point. Here the target — the
grand mean — is itself **estimated from the data**, which costs one additional
degree of freedom. The Efron-Morris adjustment for shrinking toward an
estimated common mean is therefore $(p-3)$, and the dominance result needs
$p \ge 3$. The code returns the plain sample mean when $n < 3$.

**How to compute (this repo)**

```python
from portfolio_optimization_engine.estimators import james_stein_mean, estimate_mean

mu = james_stein_mean(returns)
mu = estimate_mean(returns, "james_stein")
```

---

## Risk attribution (Euler / marginal risk decomposition)

Once a portfolio is built, a natural question is: **where is my risk coming
from?** The answer is not simply "the largest weights" — a small position in a
highly-correlated, high-volatility asset can dominate the risk budget. The
principled decomposition uses Euler's theorem.

Portfolio volatility $\sigma_p = \sqrt{w^\top\Sigma w}$ is **homogeneous of
degree 1** in the weights: scaling $w$ by $c$ scales $\sigma_p$ by $c$. Euler's
theorem for degree-1 homogeneous functions then states that the function equals
the sum of (variable $\times$ partial derivative):

$$ \sigma_p \;=\; \sum_i w_i\,\frac{\partial \sigma_p}{\partial w_i}. $$

This gives an **exact, additive** decomposition. Define:

- **Marginal contribution to risk** — how much $\sigma_p$ changes per unit
  increase in $w_i$:
  $$ \text{MCR}_i \;=\; \frac{\partial \sigma_p}{\partial w_i} \;=\; \frac{(\Sigma w)_i}{\sigma_p}. $$
- **Component contribution to risk** — asset $i$'s actual share of the risk:
  $$ \text{CCR}_i \;=\; w_i\cdot\text{MCR}_i, \qquad \sum_i \text{CCR}_i = \sigma_p \;\;(\text{Euler, exactly}). $$
- **Percentage risk** — the normalized share, summing to 1:
  $$ \text{pct\_risk}_i \;=\; \frac{\text{CCR}_i}{\sigma_p}, \qquad \sum_i \text{pct\_risk}_i = 1. $$

Because the CCRs sum *exactly* to total volatility (not approximately), this is
the standard institutional way to report a risk budget. It is also the
foundation of **risk parity**: Maillard, Roncalli & Teïletche (2010) define the
equal-risk-contribution portfolio as the one where every $\text{CCR}_i$ is
equal, which is solved by manipulating exactly these quantities.

The package exposes both the raw vector and a labeled rollup:

```python
# raw per-asset CCR vector  (w_i * (Cov @ w)_i / sigma_p)
rc = optimizer.portfolio_risk_contributions(weights)

# full Euler table: columns weight, mcr, ccr, pct_risk; index = tickers
table = optimizer.risk_attribution(weights)

# group/sector rollup -- same dict convention the optimize_* methods accept:
#   {name: (members, gmin, gmax)}  (gmin/gmax ignored here)
groups = {"tech": (["AAPL", "MSFT"], 0.0, 1.0), "energy": (["XOM"], 0.0, 1.0)}
by_group = optimizer.risk_attribution(weights, groups=groups)
```

When `groups` is given, `weight`, `ccr` and `pct_risk` are summed per group and
the group `mcr` is reported as the value-weighted figure (so group
`ccr == weight * mcr` stays consistent); tickers in no group fall under
`"unassigned"`.

---

## A note on defaults and parity

Every estimator above is strictly **opt-in**. With no arguments,
`PortfolioOptimizer.calculate_returns()` uses the plain sample mean
(`returns.mean() * 252`) and plain sample covariance (`returns.cov() * 252`) —
**byte-identical** to the pre-existing path, which preserves metrics parity with
the backtester. The robust estimators are selected explicitly:

```python
optimizer.calculate_returns(
    cov_estimator="oas",        # "sample" | "ewma" | "oas" | "mp"
    mean_estimator="james_stein",  # "sample" | "ewma" | "james_stein"
    ewma_lambda=0.94,           # used only by the "ewma" estimators
)
# Ledoit-Wolf is selected via the separate (mutually exclusive) argument:
optimizer.calculate_returns(shrinkage="constant_correlation")  # or "identity"
```

`shrinkage` (Ledoit-Wolf) and `cov_estimator` are mutually exclusive. When a
shrinkage/OAS estimator runs, the chosen intensity is stored on
`optimizer.shrinkage_intensity` (otherwise `None`).

---

## References

- Chen, Y., Wiesel, A., Eldar, Y. C., & Hero, A. O. (2010). *Shrinkage
  Algorithms for MMSE Covariance Estimation.* IEEE Transactions on Signal
  Processing, 58(10), 5016–5029. (OAS intensity, eq. 23.)
- Chopra, V. K., & Ziemba, W. T. (1993). *The Effect of Errors in Means,
  Variances, and Covariances on Optimal Portfolio Choice.* Journal of Portfolio
  Management, 19(2), 6–11.
- Efron, B., & Morris, C. (1973). *Stein's Estimation Rule and Its Competitors —
  An Empirical Bayes Approach.* Journal of the American Statistical Association,
  68(341), 117–130.
- James, W., & Stein, C. (1961). *Estimation with Quadratic Loss.* Proceedings
  of the Fourth Berkeley Symposium on Mathematical Statistics and Probability,
  Vol. 1, 361–379.
- Jorion, P. (1986). *Bayes-Stein Estimation for Portfolio Analysis.* Journal of
  Financial and Quantitative Analysis, 21(3), 279–292.
- Ledoit, O., & Wolf, M. (2003). *Improved Estimation of the Covariance Matrix of
  Stock Returns with an Application to Portfolio Selection.* Journal of Empirical
  Finance, 10(5), 603–621. (And Ledoit & Wolf, 2004, *Honey, I Shrunk the Sample
  Covariance Matrix*, Journal of Portfolio Management, 30(4), 110–119.)
- López de Prado, M. (2020). *Machine Learning for Asset Managers.* Cambridge
  University Press. (Ch. 2, Marchenko–Pastur denoising.)
- Maillard, S., Roncalli, T., & Teïletche, J. (2010). *The Properties of
  Equally Weighted Risk Contribution Portfolios.* Journal of Portfolio
  Management, 36(4), 60–70.
- Marchenko, V. A., & Pastur, L. A. (1967). *Distribution of Eigenvalues for
  Some Sets of Random Matrices.* Mathematics of the USSR-Sbornik, 1(4), 457–483.
- Michaud, R. O. (1989). *The Markowitz Optimization Enigma: Is "Optimized"
  Optimal?* Financial Analysts Journal, 45(1), 31–42.
- J.P. Morgan / Reuters (1996). *RiskMetrics — Technical Document* (4th ed.).
  (EWMA covariance, $\lambda = 0.94$ daily default.)
