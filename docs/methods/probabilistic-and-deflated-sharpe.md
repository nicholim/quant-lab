# Probabilistic and Deflated Sharpe Ratios (and trade-level analytics)

This note documents the statistical performance metrics implemented in the
**shared** metrics module — `portfolio_optimization_engine.metrics`, imported by
both the portfolio-optimization engine and the backtester — together with the
backtesting wrappers that feed them de-annualized, moment-aware inputs.

The audience is a competent programmer who knows what a Sharpe ratio *is* but not
the statistics of *estimating* it. The math below has been audited and is
correct; this document explains the reasoning, not a proposed change.

- Metrics math: `packages/portfolio-optimization/portfolio_optimization_engine/metrics.py`
- PSR wrapper + trade analytics: `packages/backtesting/src/analytics.py`
- DSR in the parameter sweep: `packages/backtesting/src/param_search.py`

---

## The problem with the Sharpe ratio

The Sharpe ratio is usually written as a population quantity,

$$
\mathrm{SR} = \frac{\mu - r_f}{\sigma},
$$

but in practice we never see $\mu$ or $\sigma$. We see a finite sample of $n$
returns and compute an *estimate* $\widehat{\mathrm{SR}} = \hat\mu / \hat\sigma$
(the code works in excess-return space, so $r_f$ is folded into $\hat\mu$). Three
problems follow, and they compound.

**1. It is an estimate, so it has a standard error.** $\widehat{\mathrm{SR}}$ is a
random variable. A strategy with a true Sharpe of $0$ will, over a short sample,
frequently produce a positive $\widehat{\mathrm{SR}}$ purely by chance. Quoting a
point estimate with no measure of its dispersion is the financial equivalent of
reporting a mean with no confidence interval. Lo (2002), *The Statistics of
Sharpe Ratios*, works out this sampling distribution and shows that the error
shrinks only as $\sqrt{n}$ — so short backtests carry large uncertainty.

**2. Non-normality inflates it.** The textbook standard error
$\sigma_{\widehat{\mathrm{SR}}} \approx \sqrt{1/(n-1)}$ assumes i.i.d. Gaussian
returns. Strategy returns are rarely Gaussian: they are often negatively skewed
(many small wins, occasional large losses — e.g. short-volatility or carry
profiles) and fat-tailed (excess kurtosis). Mertens (2002) extended Lo's result
to non-normal returns; the corrected standard error is *larger* when returns are
negatively skewed and/or leptokurtic. A naive Sharpe ignoring this looks more
significant than the data warrant.

**3. Selecting the best of many configurations biases it upward.** This is the
deepest problem. When you grid-search $N$ parameter combinations and report the
*maximum* Sharpe, you are reporting the maximum of $N$ noisy estimates. Even if
every true Sharpe is zero, the sample maximum grows with $N$ — this is
multiple-testing / selection bias, the statistical engine of *backtest
overfitting*. Bailey, Borwein, López de Prado & Zhu (2014), *Pseudo-Mathematics
and Financial Charlatanism*, make the point bluntly: with enough trials a
researcher can almost always manufacture an impressive in-sample Sharpe, and
**most backtested strategies that are published are false discoveries** because
the number of trials behind them is unreported. The honest fix is not to test
fewer strategies but to *deflate* the winner's Sharpe by how many trials produced
it.

PSR addresses (1) and (2). DSR additionally addresses (3).

---

## Probabilistic Sharpe Ratio (PSR)

**Reference:** Bailey & López de Prado (2012), *The Sharpe Ratio Efficient
Frontier*.

Instead of asking "what is the Sharpe?", PSR asks: **given my sample, what is the
probability that the true Sharpe exceeds a benchmark $\mathrm{SR}^\ast$?**

$$
\widehat{\mathrm{PSR}}(\mathrm{SR}^\ast) =
\Phi\!\left[
\frac{(\widehat{\mathrm{SR}} - \mathrm{SR}^\ast)\,\sqrt{n-1}}
{\sqrt{\,1 - \gamma_3\,\widehat{\mathrm{SR}} + \tfrac{\gamma_4 - 1}{4}\,\widehat{\mathrm{SR}}^2\,}}
\right]
$$

where $\Phi$ is the standard-normal CDF, $n$ is the number of return
observations, $\gamma_3$ is the skewness, and $\gamma_4$ is the **raw
(non-excess) kurtosis** ($\gamma_4 = 3$ for a normal distribution).

The structure is a one-sided z-test. The denominator,

$$
\sigma_{\widehat{\mathrm{SR}}} =
\sqrt{\frac{1 - \gamma_3\,\widehat{\mathrm{SR}} + \tfrac{\gamma_4 - 1}{4}\,\widehat{\mathrm{SR}}^2}{n-1}},
$$

is precisely the Mertens (2002) / Lo (2002) standard error of the Sharpe
estimator. Read the moment terms intuitively:

- The leading $1$ is the Gaussian term; with $\gamma_3 = 0,\ \gamma_4 = 3$ the
  denominator collapses to $\sqrt{1/(n-1)}$ — the i.i.d.-normal case.
- $-\gamma_3\,\widehat{\mathrm{SR}}$: with a *positive* Sharpe, **negative**
  skew ($\gamma_3 < 0$) makes this term positive, *enlarging* the standard error
  and shrinking PSR. Negatively-skewed strategies are penalized — correctly.
- $\tfrac{\gamma_4 - 1}{4}\,\widehat{\mathrm{SR}}^2$: fat tails ($\gamma_4 > 3$)
  enlarge the standard error too.

So PSR is the probability mass of the SR sampling distribution that lies above
$\mathrm{SR}^\ast$. With $\mathrm{SR}^\ast = 0$ it is "the probability the true
Sharpe is positive." A PSR of, say, $0.95$ means a 95% one-sided confidence that
the true Sharpe beats the benchmark.

**Frequency consistency is essential.** $\widehat{\mathrm{SR}}$ and
$\mathrm{SR}^\ast$ must be on the **same per-period (non-annualized) basis**,
because $n$ counts that same period. Mixing an annualized Sharpe with a
per-period $n$ silently inflates the z-score by $\sqrt{252}$.

### How to compute (this repo)

The pure closed form lives in the shared module:

```python
# portfolio_optimization_engine/metrics.py
def probabilistic_sharpe_ratio(
    observed_sr: float,
    benchmark_sr: float,
    n: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,   # NON-excess: 3.0 is normal
) -> float: ...
```

It clamps the variance term to `1e-12` (extreme skew/kurtosis/SR combinations can
drive it slightly negative) and uses `math.erf` for $\Phi$, so there is no scipy
dependency.

The backtester wraps it and is responsible for the two conversions the closed
form demands:

```python
# packages/backtesting/src/analytics.py  -> PerformanceAnalytics
def probabilistic_sharpe_ratio(self, benchmark_sr: float = 0.0) -> float:
    n = len(self.returns)
    # 1. De-annualize: self.sharpe_ratio() is annualized by sqrt(252).
    per_period_sr = self.sharpe_ratio() / np.sqrt(PERIODS_PER_YEAR)
    # 2. pandas .kurt() is EXCESS kurtosis; the closed form wants raw -> +3.
    skew = float(pd.Series(self.returns).skew())
    kurt = float(pd.Series(self.returns).kurt()) + 3.0
    return probabilistic_sharpe_ratio(per_period_sr, benchmark_sr, n, skew, kurt)
```

Two conversions worth restating because they are easy to get wrong:

- **De-annualization.** `self.sharpe_ratio()` is annualized (multiplied by
  $\sqrt{252}$ in the shared `sharpe_ratio`). Dividing by $\sqrt{252}$ returns it
  to the daily basis that matches $n =$ number of daily returns.
- **Kurtosis convention.** pandas `Series.kurt()` returns *excess* kurtosis
  (Fisher's definition, $0$ for normal). The formula's $\gamma_4$ is *raw* kurtosis
  ($3$ for normal), so the wrapper adds `3.0`. Skewness needs no shift. NaN moments
  (degenerate samples) fall back to the Gaussian defaults $0$ and $3$.

---

## Deflated Sharpe Ratio (DSR)

**Reference:** Bailey & López de Prado (2014), *The Deflated Sharpe Ratio:
Correcting for Selection Bias, Backtest Overfitting and Non-Normality*.

DSR is simply **PSR evaluated at a non-zero, trial-aware benchmark**. Instead of
asking "is the Sharpe positive?", it asks: **is this Sharpe larger than what the
luckiest of $N$ trials would have produced by chance alone?**

The benchmark is the *expected maximum* Sharpe across $N$ independent trials whose
true Sharpes are all zero:

$$
\mathrm{SR}^\ast = \sqrt{V}\,\Big[(1-\gamma)\,Z^{-1}\!\big(1 - \tfrac{1}{N}\big)
\; + \; \gamma\,Z^{-1}\!\big(1 - \tfrac{1}{N e}\big)\Big]
$$

where $V$ is the **variance of the trial Sharpe estimates**, $Z^{-1}$ is the
inverse standard-normal CDF (the probit), $e$ is Euler's number, and
$\gamma \approx 0.5772$ is the **Euler–Mascheroni constant**.

This is the Gumbel / extreme-value approximation for the expected maximum of $N$
i.i.d. Gaussians. The intuition: the maximum of $N$ standard normals grows like
$\sqrt{2\ln N}$, and scaling by the dispersion of the trial Sharpes ($\sqrt V$)
turns that into the Sharpe units we are testing against. More trials ($N$) or more
spread among the trial results ($V$) raises the bar a strategy must clear.

Then:

$$
\mathrm{DSR} = \widehat{\mathrm{PSR}}(\mathrm{SR}^\ast), \qquad
\mathrm{SR}^\ast = \mathbb{E}[\max \mathrm{SR}_N].
$$

Properties that fall out directly:

- **$N = 1 \Rightarrow \mathrm{SR}^\ast = 0 \Rightarrow \mathrm{DSR} = \mathrm{PSR}$.**
  With one trial there is no selection bias to correct, so DSR reduces to the
  plain PSR against zero.
- **$N > 1 \Rightarrow \mathrm{DSR} \le \mathrm{PSR}$.** A positive benchmark can
  only shrink the probability. The more configurations you tried, the more your
  winner has to clear, and the lower its deflated significance.

So DSR answers the question the plain Sharpe cannot: *"Is this strategy
significant after accounting for the fact that I tried $N$ configurations?"*

### How to compute (this repo)

The expected-max benchmark and the DSR live in the shared module:

```python
# portfolio_optimization_engine/metrics.py
def expected_max_sharpe(n_trials: int, sr_variance: float) -> float: ...
# returns 0.0 for n_trials <= 1 or sr_variance <= 0 (no inflation);
# uses statistics.NormalDist().inv_cdf for an exact, scipy-free probit.

def deflated_sharpe_ratio(
    observed_sr: float,    # per-period Sharpe of the SELECTED config
    n: int,                # observations behind observed_sr
    n_trials: int,         # number of configurations tried
    sr_variance: float,    # variance of the trial Sharpes (same frequency!)
    skew: float = 0.0,
    kurtosis: float = 3.0, # non-excess
) -> float:
    benchmark_sr = expected_max_sharpe(n_trials, sr_variance)
    return probabilistic_sharpe_ratio(observed_sr, benchmark_sr, n, skew, kurtosis)
```

`grid_search` wires the multiple-testing correction automatically. For each
combo it stores the **per-period** Sharpe (de-annualized identically to the PSR
wrapper) plus the return-series moments; after the sweep it computes
$V$ as the sample variance of those per-period Sharpes and feeds the grid size as
$N$:

```python
# packages/backtesting/src/param_search.py  -> grid_search
pp_sharpe = analytics.sharpe_ratio() / np.sqrt(PERIODS_PER_YEAR)
per_period_sharpes.append(pp_sharpe)
# ... after the loop ...
n_trials = len(rows)
if n_trials > 1:
    sr_variance = float(np.var(per_period_sharpes, ddof=1))
    # one DSR per row, each using its own SR/n/skew/kurtosis but the shared
    # n_trials and sr_variance:
    deflated_sharpe_ratio(observed_sr=inp["sr"], n=inp["n"],
                          n_trials=n_trials, sr_variance=sr_variance,
                          skew=inp["skew"], kurtosis=inp["kurtosis"])
else:
    df["dsr"] = float("nan")   # a single trial has no selection bias to deflate
```

The returned DataFrame gains a `dsr` column. The deflation *benchmark*
($N$ and $V$) is a sweep-level quantity shared by every row, but each row's DSR
still uses that row's own SR, $n$, skew and kurtosis.

**Honest caveat.** $V$ is the sample variance of the trial Sharpes, and on a
**tiny grid** (a handful of combos) it is a noisy estimator — and the
$\mathbb{E}[\max]$ extreme-value approximation, plus the independence assumption
behind it, is most trustworthy when $N$ is large and the trials are genuinely
distinct. On small or highly-correlated grids treat the DSR as indicative rather
than exact. It is still strictly more honest than reporting the undeflated
maximum Sharpe and saying nothing about how many trials produced it.

---

## Trade-level analytics

The metrics above are computed from the equity-curve return series. The
backtester also reports a block of standard practitioner trade statistics — the
same family found in quantstats, pyfolio and backtrader — computed from a
**signed-FIFO round-trip ledger** rather than the return path. The ledger is
built in `PerformanceAnalytics._round_trips()` (and the P&L-only
`_compute_round_trip_pnl()`): BUYs and SELLs are matched per symbol, first-in
first-out; in long-only mode a SELL with no open long is dropped, and in
`allow_short` mode the FIFO is fully signed so short round trips (sell-to-open →
buy-to-cover) and flips through flat are matched. Each closed leg records signed
P&L, entry/exit timestamps, entry/exit prices, side, and matched quantity.

Let the realized round-trip P&Ls be $\{p_i\}$, with win rate $w = \#\{p_i>0\}/m$
and loss rate $1-w$ over $m$ trips.

- **Win rate** $w$ — fraction of round trips that were profitable.
  `win_rate()`. Tells you how *often* you are right, but says nothing about
  magnitude — a high win rate with a few catastrophic losses is a losing system.

- **Average win / average loss** — mean of the positive P&Ls and mean of the
  negative P&Ls (the latter reported as a negative number).
  `avg_win()`, `avg_loss()`. The magnitude side that win rate omits.

- **Expectancy** — expected P&L per trip:
  $$
  \mathbb{E}[p] = \text{avg\_win}\cdot w + \text{avg\_loss}\cdot(1-w),
  $$
  which equals the plain mean of all round-trip P&Ls. `expectancy()`. Positive
  expectancy is the minimum bar for a tradeable system; it fuses frequency and
  magnitude into one number.

- **Payoff ratio** — $\text{avg\_win} / |\text{avg\_loss}|$. `payoff_ratio()`
  (returns $\infty$ if there are wins but no losses). How much a typical winner
  pays relative to a typical loser; a low win rate can still be profitable with a
  high payoff ratio (trend-following), and vice versa (mean-reversion).

- **Profit factor** — gross profit over gross loss,
  $\sum_{p_i>0} p_i \,/\, \lvert\sum_{p_i<0} p_i\rvert$. `profit_factor()`
  ($\infty$ if there are no losses). A portfolio-level health ratio; $>1$ means
  the wins outweigh the losses in aggregate.

- **Average holding period** — mean of $(\text{exit\_ts} - \text{entry\_ts})$
  across trips, in days. `avg_holding_period()`. Characterizes the strategy's
  turnover and time horizon, and contextualizes capacity and transaction-cost
  drag.

- **Exposure time** — fraction of equity-curve bars during which a position was
  held. `exposure_time()`. A strategy that earns its return while in the market
  only 10% of the time has a very different risk profile from one fully invested
  throughout; it also bounds how much of the benchmark's risk you actually took.

- **MAE / MFE** — Maximum Adverse Excursion and Maximum Favorable Excursion: for
  each trip, the **worst** and **best** unrealized (mark-to-market) P&L reached
  *during* the trade, before it closed. `mae_mfe()` returns a per-trip frame with
  `mae`, `mfe`, side and final `pnl`. Introduced by John Sweeney (*Campaign
  Trading* / *Maximum Adverse Excursion*), MAE/MFE diagnose stop placement and
  exit timing: large MAE on eventual winners says your stops would have been hit
  prematurely; large MFE on eventual losers (giving back open profit) says your
  exits are too loose. The repo computes the excursion as
  $(\text{path} - \text{entry\_price}) \cdot \text{side} \cdot \text{qty}$ over the
  price window between entry and exit.

### How to compute (this repo)

```python
# packages/backtesting/src/analytics.py  -> PerformanceAnalytics
stats = analytics.trade_stats()
# {win_rate, profit_factor, avg_win, avg_loss, expectancy,
#  payoff_ratio, avg_holding_period, exposure_time}
mae_mfe_df = analytics.mae_mfe()   # columns: symbol, side, pnl, mae, mfe
```

**Honest caveats.** `exposure_time` and `mae_mfe` are **equity-path proxies**
unless a per-symbol close is supplied. `exposure_time` marks a bar "in market"
when the equity value moved between bars (`|Δequity| > 1e-9`), which can
misclassify a flat-but-drifting curve. `mae_mfe` walks the per-trip price window
of the equity DataFrame, preferring a `close` column when present and otherwise
falling back to the portfolio `equity` curve — so without per-symbol closes the
excursions reflect portfolio marks rather than the individual instrument. Both
are computed strictly post-hoc and never touch the hot event loop.

---

## References

- **Bailey, D. H., & López de Prado, M. (2012).** *The Sharpe Ratio Efficient
  Frontier.* Journal of Risk, 15(2), 3–44. (Probabilistic Sharpe Ratio.)
- **Bailey, D. H., & López de Prado, M. (2014).** *The Deflated Sharpe Ratio:
  Correcting for Selection Bias, Backtest Overfitting and Non-Normality.* Journal
  of Portfolio Management, 40(5), 94–107.
- **Bailey, D. H., Borwein, J. M., López de Prado, M., & Zhu, Q. J. (2014).**
  *Pseudo-Mathematics and Financial Charlatanism: The Effects of Backtest
  Overfitting on Out-of-Sample Performance.* Notices of the AMS, 61(5), 458–471.
- **Lo, A. W. (2002).** *The Statistics of Sharpe Ratios.* Financial Analysts
  Journal, 58(4), 36–52.
- **Mertens, E. (2002).** *Comments on Variance of the IID Estimator in Lo
  (2002).* (Non-normal standard error of the Sharpe ratio estimator.)
- **Sweeney, J. (1996).** *Maximum Adverse Excursion: Analyzing Price Fluctuations
  for Trading Management.* Wiley. (MAE/MFE.)
