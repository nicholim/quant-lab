from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, to_tree
from scipy.optimize import linprog, minimize
from scipy.spatial.distance import squareform

from .black_litterman import black_litterman
from .covariance import ledoit_wolf_shrinkage
from .data_cache import download_close_prices


@dataclass
class PortfolioResult:
    weights: np.ndarray
    expected_return: float
    volatility: float
    sharpe_ratio: float
    sortino_ratio: float | None = None
    cvar: float | None = None
    objective: str | None = None


class PortfolioOptimizer:
    """Modern Portfolio Theory optimizer with efficient frontier computation.

    Supports multiple objectives (max Sharpe, min volatility, risk parity, max
    Sortino, min CVaR, and target-based) and flexible weight constraints
    (per-asset/group bounds, optional shorting). All objective methods accept the
    same constraint keyword arguments, forwarded to ``_build_bounds_constraints``:

        min_weights, max_weights : float | list | dict[ticker, float] | None
        allow_short              : bool
        groups                   : dict[name, (members, gmin, gmax)]

    With all constraint kwargs at their defaults the behavior is long-only and
    fully invested (weights in [0, 1] summing to 1).
    """

    def __init__(
        self,
        tickers: list[str],
        start_date: str,
        end_date: str,
        risk_free_rate: float = 0.02,
        use_cache: bool = True,
        offline: bool = False,
    ):
        self.tickers = tickers
        self.start_date = start_date
        self.end_date = end_date
        self.risk_free_rate = risk_free_rate
        self.use_cache = use_cache
        self.offline = offline
        self.prices: pd.DataFrame | None = None
        self.returns: pd.DataFrame | None = None
        self.mean_returns: pd.Series | None = None
        self.cov_matrix: pd.DataFrame | None = None
        #: Ledoit-Wolf intensity from the last calculate_returns(shrinkage=...) call;
        #: None when shrinkage was off (the default).
        self.shrinkage_intensity: float | None = None
        self.num_assets = len(tickers)

    def fetch_data(self) -> pd.DataFrame:
        """Fetch adjusted close prices (cached locally; see data_cache)."""
        self.prices = download_close_prices(
            self.tickers,
            self.start_date,
            self.end_date,
            auto_adjust=True,
            use_cache=self.use_cache,
            offline=self.offline,
        )
        return self.prices

    def calculate_returns(
        self,
        shrinkage: str | None = None,
    ) -> pd.DataFrame:
        """Compute daily and annualized return statistics.

        Parameters
        ----------
        shrinkage:
            Covariance estimator for ``cov_matrix``. ``None`` (default) uses the
            plain sample covariance exactly as before -- this preserves metrics
            parity with the backtester, which never opts in. Pass
            ``"constant_correlation"`` or ``"identity"`` to apply Ledoit-Wolf
            shrinkage toward that structured target with the analytically optimal
            intensity. When shrinkage is applied, the chosen intensity is stored
            on ``self.shrinkage_intensity`` (otherwise ``None``).
        """
        if self.prices is None:
            raise ValueError("Call fetch_data() first")
        self.returns = self.prices.pct_change().dropna()
        self.mean_returns = self.returns.mean() * 252
        self.cov_matrix = self._estimate_cov_matrix(self.returns, shrinkage)
        return self.returns

    def _estimate_cov_matrix(self, returns: pd.DataFrame, shrinkage: str | None) -> pd.DataFrame:
        """Annualized covariance: plain sample (default) or Ledoit-Wolf shrunk.

        With ``shrinkage is None`` this returns ``returns.cov() * 252`` -- byte
        identical to the previous behavior. Shrinkage is computed on the
        per-period returns then annualized, and labeled with the asset columns.
        """
        if shrinkage is None:
            self.shrinkage_intensity = None
            return returns.cov() * 252
        shrunk, intensity = ledoit_wolf_shrinkage(returns, target=shrinkage)
        self.shrinkage_intensity = intensity
        return pd.DataFrame(shrunk * 252, index=returns.columns, columns=returns.columns)

    # --- Portfolio statistics ---

    def portfolio_return(self, weights: np.ndarray) -> float:
        """Annualized expected portfolio return."""
        # mean_returns set by calculate_returns()/injection before any optimize_* call
        return float(np.dot(weights, self.mean_returns))  # type: ignore[arg-type]

    def portfolio_volatility(self, weights: np.ndarray) -> float:
        """Annualized portfolio volatility (standard deviation)."""
        # cov_matrix set by calculate_returns()/injection before any optimize_* call
        return float(np.sqrt(np.dot(weights.T, np.dot(self.cov_matrix, weights))))  # type: ignore[arg-type]

    def portfolio_sharpe(self, weights: np.ndarray) -> float:
        """Sharpe ratio of the portfolio."""
        ret = self.portfolio_return(weights)
        vol = self.portfolio_volatility(weights)
        return (ret - self.risk_free_rate) / vol

    def portfolio_risk_contributions(self, weights: np.ndarray) -> np.ndarray:
        """Risk contribution of each asset: ``w_i * (Cov @ w)_i / sigma_p``."""
        cov = np.asarray(self.cov_matrix)
        vol = max(self.portfolio_volatility(weights), 1e-12)
        marginal = cov @ weights
        return weights * marginal / vol

    def downside_deviation(self, weights: np.ndarray, mar_annual: float | None = None) -> float:
        """Annualized downside deviation of daily returns below the MAR."""
        port_daily = self.returns.values @ weights  # type: ignore[union-attr]  # returns is set before call
        mar = self.risk_free_rate if mar_annual is None else mar_annual
        mar_daily = mar / 252
        shortfall = np.minimum(port_daily - mar_daily, 0.0)
        dd_daily = np.sqrt(np.mean(shortfall**2))
        return float(dd_daily * np.sqrt(252))

    def portfolio_sortino(self, weights: np.ndarray, mar_annual: float | None = None) -> float:
        """Sortino ratio (excess return over annualized downside deviation)."""
        mar = self.risk_free_rate if mar_annual is None else mar_annual
        excess = self.portfolio_return(weights) - mar
        dd = self.downside_deviation(weights, mar_annual)
        return excess / max(dd, 1e-12)

    def portfolio_cvar(self, weights: np.ndarray, confidence: float = 0.95) -> float:
        """Historical CVaR (expected shortfall) of daily portfolio returns.

        Distinct from the Monte Carlo / parametric CVaR in ``monte_carlo.py``:
        this is the in-sample empirical tail loss of the realized daily returns.
        """
        port_daily = self.returns.values @ weights  # type: ignore[union-attr]  # returns is set before call
        losses = -port_daily
        var = np.quantile(losses, confidence)
        tail = losses[losses >= var]
        return float(tail.mean()) if tail.size else float(var)

    # --- Constraint handling ---

    def _normalize_bound(self, spec, default: float) -> np.ndarray:
        """Turn a scalar/list/ticker-dict bound spec into a length-n array."""
        if spec is None:
            return np.full(self.num_assets, default, dtype=float)
        if isinstance(spec, dict):
            out = np.full(self.num_assets, default, dtype=float)
            idx = {t: i for i, t in enumerate(self.tickers)}
            for ticker, value in spec.items():
                if ticker not in idx:
                    raise ValueError(f"Unknown ticker in weight bound: '{ticker}'")
                out[idx[ticker]] = value
            return out
        if np.isscalar(spec):
            return np.full(self.num_assets, float(spec), dtype=float)  # type: ignore[arg-type]  # np.isscalar guards a numeric scalar
        arr = np.asarray(spec, dtype=float)
        if arr.shape != (self.num_assets,):
            raise ValueError(
                f"Weight bound array must have length {self.num_assets}, got {arr.shape}"
            )
        return arr

    def _group_masks(self, groups) -> list[tuple[np.ndarray, float, float]]:
        """Translate a groups dict into (mask, gmin, gmax) tuples."""
        if not groups:
            return []
        idx = {t: i for i, t in enumerate(self.tickers)}
        out = []
        for name, (members, gmin, gmax) in groups.items():
            mask = np.zeros(self.num_assets)
            for ticker in members:
                if ticker not in idx:
                    raise ValueError(f"Group '{name}' references unknown ticker '{ticker}'")
                mask[idx[ticker]] = 1.0
            out.append((mask, float(gmin), float(gmax)))
        return out

    def _build_bounds_constraints(
        self,
        min_weights=None,
        max_weights=None,
        allow_short: bool = False,
        groups=None,
    ):
        """Build scipy ``bounds`` and extra constraint dicts from constraint kwargs."""
        default_lo = -1.0 if allow_short else 0.0
        lo = self._normalize_bound(min_weights, default_lo)
        hi = self._normalize_bound(max_weights, 1.0)
        bounds = tuple((float(lo[i]), float(hi[i])) for i in range(self.num_assets))

        extra = []
        for mask, gmin, gmax in self._group_masks(groups):
            # ineq means fun(w) >= 0: gmin <= mask@w <= gmax
            extra.append({"type": "ineq", "fun": (lambda w, m=mask, g=gmin: m @ w - g)})
            extra.append({"type": "ineq", "fun": (lambda w, m=mask, g=gmax: g - m @ w)})
        return bounds, extra

    # --- Solver scaffolding ---

    def _solve(
        self,
        objective_fn,
        *,
        bounds=None,
        extra_constraints=None,
        initial=None,
        label="optimization",
    ) -> np.ndarray:
        """Run an SLSQP solve with the budget (sum=1) constraint always applied."""
        if self.mean_returns is None:
            raise ValueError("Call calculate_returns() first")

        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
        if extra_constraints:
            constraints.extend(extra_constraints)
        if bounds is None:
            bounds = tuple((0.0, 1.0) for _ in range(self.num_assets))
        if initial is None:
            initial = np.array([1 / self.num_assets] * self.num_assets)

        result = minimize(
            fun=objective_fn,
            x0=initial,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-9},
        )
        if not result.success:
            raise ValueError(f"{label} failed: {result.message}")
        return result.x

    def _make_result(self, weights: np.ndarray, objective: str) -> PortfolioResult:
        """Build a fully-populated PortfolioResult for a solved weight vector."""
        return PortfolioResult(
            weights=weights,
            expected_return=self.portfolio_return(weights),
            volatility=self.portfolio_volatility(weights),
            sharpe_ratio=self.portfolio_sharpe(weights),
            sortino_ratio=self.portfolio_sortino(weights),
            cvar=self.portfolio_cvar(weights),
            objective=objective,
        )

    # --- Efficient frontier ---

    def efficient_frontier(
        self, num_portfolios: int = 5000, random_state: int | None = None
    ) -> pd.DataFrame:
        """Generate random portfolios to approximate the efficient frontier."""
        if self.mean_returns is None:
            raise ValueError("Call calculate_returns() first")

        rng = np.random.default_rng(random_state)
        results = []
        for _ in range(num_portfolios):
            weights = rng.dirichlet(np.ones(self.num_assets))
            ret = self.portfolio_return(weights)
            vol = self.portfolio_volatility(weights)
            sharpe = (ret - self.risk_free_rate) / vol
            results.append(
                {
                    "return": ret,
                    "volatility": vol,
                    "sharpe": sharpe,
                    **{f"w_{t}": w for t, w in zip(self.tickers, weights, strict=False)},
                }
            )

        return pd.DataFrame(results)

    def solved_efficient_frontier(
        self,
        n_points: int = 50,
        **cons,
    ) -> pd.DataFrame:
        """Compute the TRUE (solved) efficient frontier.

        Unlike :meth:`efficient_frontier` (a random Dirichlet "cloud" used for the
        scatter background), this sweeps :meth:`optimize_min_vol_target_return`
        across a grid of target returns from the minimum to the maximum feasible
        return and records the minimum-volatility portfolio for each -- i.e. the
        actual lower-left boundary of the feasible set.

        The grid runs between the return of the global minimum-volatility
        portfolio (the frontier's left tip) and the highest achievable return
        under the supplied constraints. Target points that are infeasible (or for
        which the solver fails to converge) are skipped, so the result may have
        fewer than ``n_points`` rows. Accepts the same constraint kwargs as the
        ``optimize_*`` methods (``min_weights``, ``max_weights``, ``allow_short``,
        ``groups``).

        Returns
        -------
        DataFrame with columns ``return``, ``volatility``, ``sharpe`` and one
        ``w_<ticker>`` column per asset, sorted by ascending return. Each row's
        weights sum to 1 and respect the constraint bounds.
        """
        if self.mean_returns is None:
            raise ValueError("Call calculate_returns() first")
        if n_points < 2:
            raise ValueError("n_points must be >= 2")

        mu = np.asarray(self.mean_returns, dtype=float)
        # Lower bound: return of the global min-vol portfolio (left tip of frontier).
        min_vol = self.optimize_min_volatility(**cons)
        lo_ret = float(min_vol.expected_return)
        # Upper bound: the maximum achievable return under the constraints. For the
        # common long-only / bounded case the max single-asset return is the
        # ceiling; clip to it so we never request an infeasible target.
        hi_ret = float(mu.max())
        if hi_ret <= lo_ret:
            hi_ret = lo_ret + abs(lo_ret) * 1e-3 + 1e-6

        targets = np.linspace(lo_ret, hi_ret, n_points)
        rows = []
        for target in targets:
            try:
                res = self.optimize_min_vol_target_return(float(target), **cons)
            except ValueError:
                # infeasible target (above the long-only ceiling) or solver failure
                continue
            rows.append(
                {
                    "return": res.expected_return,
                    "volatility": res.volatility,
                    "sharpe": res.sharpe_ratio,
                    **{f"w_{t}": w for t, w in zip(self.tickers, res.weights, strict=False)},
                }
            )

        frontier = pd.DataFrame(rows)
        if not frontier.empty:
            frontier = (
                frontier.sort_values("return").drop_duplicates("return").reset_index(drop=True)
            )
        return frontier

    # --- Objectives ---

    def optimize_sharpe(self, **cons) -> PortfolioResult:
        """Find the portfolio that maximizes the Sharpe ratio."""
        bounds, extra = self._build_bounds_constraints(**cons)
        weights = self._solve(
            lambda w: -self.portfolio_sharpe(w),
            bounds=bounds,
            extra_constraints=extra,
            label="Sharpe optimization",
        )
        return self._make_result(weights, "sharpe")

    def optimize_min_volatility(self, **cons) -> PortfolioResult:
        """Find the minimum volatility portfolio."""
        bounds, extra = self._build_bounds_constraints(**cons)
        weights = self._solve(
            self.portfolio_volatility,
            bounds=bounds,
            extra_constraints=extra,
            label="Min-volatility optimization",
        )
        return self._make_result(weights, "min_volatility")

    def optimize_risk_parity(self, **cons) -> PortfolioResult:
        """Find the portfolio that equalizes each asset's risk contribution."""
        if cons.get("allow_short"):
            raise ValueError("Risk parity is undefined with shorting; set allow_short=False")
        bounds, extra = self._build_bounds_constraints(**cons)

        def objective(w):
            rc = self.portfolio_risk_contributions(w)
            return float(np.sum((rc - rc.mean()) ** 2))

        # inverse-volatility starting point improves convergence
        inv_vol = 1.0 / np.sqrt(np.diag(np.asarray(self.cov_matrix)))
        initial = inv_vol / inv_vol.sum()
        weights = self._solve(
            objective,
            bounds=bounds,
            extra_constraints=extra,
            initial=initial,
            label="Risk-parity optimization",
        )
        return self._make_result(weights, "risk_parity")

    # --- Hierarchical Risk Parity (López de Prado, 2016) ---

    @staticmethod
    def _hrp_quasi_diag(link: np.ndarray) -> list[int]:
        """Stage 2 -- quasi-diagonalization.

        Reorder the original assets so that similar (highly-correlated) ones sit
        next to each other, by reading the leaves of the hierarchical-clustering
        tree left-to-right. Returns a list of original asset indices.
        """
        n = link.shape[0] + 1  # number of original leaves
        tree = to_tree(link, rd=False)
        # pre_order with the identity leaf func yields original leaf ids in
        # tree (quasi-diagonal) order.
        order = tree.pre_order(lambda node: node.id)
        # defensive: pre_order returns exactly the n leaves
        return [int(i) for i in order][:n]

    @staticmethod
    def _hrp_cluster_var(cov: np.ndarray, items: list[int]) -> float:
        """Inverse-variance ("naive risk parity") variance of a sub-cluster.

        Allocate weights inside the cluster proportional to ``1/diag(cov)`` and
        return the resulting cluster variance ``w' Cov w``.
        """
        sub = cov[np.ix_(items, items)]
        ivp = 1.0 / np.diag(sub)
        ivp = ivp / ivp.sum()
        return float(ivp @ sub @ ivp)

    def _hrp_recursive_bisection(self, cov: np.ndarray, order: list[int]) -> np.ndarray:
        """Stage 3 -- recursive bisection.

        Start every asset at weight 1, then repeatedly split each contiguous
        cluster (in quasi-diagonal order) in two and scale the two halves
        inversely to their cluster variances. The split factor for the left
        cluster is ``1 - var_left / (var_left + var_right)``.
        """
        weights = np.ones(self.num_assets, dtype=float)
        clusters = [list(order)]
        while clusters:
            next_clusters: list[list[int]] = []
            for cluster in clusters:
                if len(cluster) <= 1:
                    continue
                mid = len(cluster) // 2
                left, right = cluster[:mid], cluster[mid:]
                var_left = self._hrp_cluster_var(cov, left)
                var_right = self._hrp_cluster_var(cov, right)
                total = var_left + var_right
                # equal split if both variances are ~0 (degenerate)
                alpha = 1.0 - var_left / total if total > 0 else 0.5
                for i in left:
                    weights[i] *= alpha
                for i in right:
                    weights[i] *= 1.0 - alpha
                next_clusters.extend([left, right])
            clusters = next_clusters
        return weights

    def optimize_hrp(self, linkage_method: str = "single") -> PortfolioResult:
        """Hierarchical Risk Parity allocation (López de Prado, 2016).

        A solver-free, long-only, fully-invested allocation built in three stages:

        1. **Tree clustering** -- hierarchical clustering on the correlation
           distance ``d = sqrt(0.5 * (1 - corr))`` (``scipy`` ``linkage``).
        2. **Quasi-diagonalization** -- reorder assets by the linkage so that
           similar assets are adjacent (:meth:`_hrp_quasi_diag`).
        3. **Recursive bisection** -- split the ordered list and allocate
           inversely to each sub-cluster's variance, recursively
           (:meth:`_hrp_recursive_bisection`).

        Unlike the ``optimize_*`` SLSQP/LP methods this needs no optimizer: it
        derives weights directly from the covariance structure, which makes it
        robust when the covariance is ill-conditioned (where mean-variance
        solvers tilt heavily into noisy low-eigenvalue directions). It honors the
        same injected-returns contract (uses ``self.cov_matrix``) and returns the
        same :class:`PortfolioResult` (weights summing to 1, with
        return/volatility/Sharpe via the shared metrics path) as every other
        objective, so the backtester can call it the same zero-arg way.

        HRP is intrinsically long-only and fully invested; it takes no weight
        bounds / shorting / group constraints (passing them is unsupported, hence
        the deliberately narrow signature).

        Parameters
        ----------
        linkage_method:
            Linkage criterion forwarded to ``scipy.cluster.hierarchy.linkage``
            (default ``"single"``, as in the original paper).
        """
        if self.cov_matrix is None:
            raise ValueError("Call calculate_returns() first")

        cov = np.asarray(self.cov_matrix, dtype=float)
        n = self.num_assets

        # Degenerate: a single asset gets the whole budget.
        if n == 1:
            return self._make_result(np.array([1.0]), "hrp")

        # Stage 1 -- correlation distance + hierarchical clustering.
        std = np.sqrt(np.diag(cov))
        denom = np.outer(std, std)
        with np.errstate(divide="ignore", invalid="ignore"):
            corr = np.where(denom > 0, cov / denom, 0.0)
        # clip floating-point drift so 1 - corr stays in [0, 2] under the sqrt
        corr = np.clip(corr, -1.0, 1.0)
        np.fill_diagonal(corr, 1.0)
        dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, None))
        # condensed (upper-triangular) form required by linkage
        condensed = squareform(dist, checks=False)
        link = linkage(condensed, method=linkage_method)

        # Stage 2 -- quasi-diagonalization.
        order = self._hrp_quasi_diag(link)

        # Stage 3 -- recursive bisection.
        weights = self._hrp_recursive_bisection(cov, order)

        # Numerical guard: renormalize to sum to 1 (long-only by construction).
        total = weights.sum()
        weights = weights / total if total > 0 else np.full(n, 1.0 / n)
        return self._make_result(weights, "hrp")

    # --- Black-Litterman (1992) ---

    def black_litterman_returns(
        self,
        P: np.ndarray | None = None,
        Q: np.ndarray | None = None,
        *,
        w_mkt: np.ndarray | None = None,
        omega: np.ndarray | str | None = None,
        tau: float = 0.05,
        risk_aversion: float = 2.5,
        pi: np.ndarray | None = None,
    ) -> pd.Series:
        """Black-Litterman posterior expected (excess) returns as a Series.

        Blends the market-implied equilibrium prior ``Pi = delta * Sigma @ w_mkt``
        (reverse optimization on the neutral portfolio ``w_mkt``; default
        equal-weight) with the investor's views ``P @ E[R] = Q`` (uncertainty
        ``Omega``, default ``diag(tau P Sigma P^T)``) via the BL master formula.

        With no views (``P``/``Q`` omitted) the posterior equals the prior
        ``Pi`` exactly. Uses only ``self.cov_matrix`` (the injected-returns
        contract); raises the usual ValueError if it is unset. See
        :func:`black_litterman.black_litterman` for the full parameter docs.

        Returns a pandas Series indexed by ``self.tickers`` so callers can read
        the posterior per asset; values are annualized excess returns (the cov
        matrix is annualized).
        """
        if self.cov_matrix is None:
            raise ValueError("Call calculate_returns() first")
        posterior = black_litterman(
            self.cov_matrix,
            w_mkt=w_mkt,
            P=P,
            Q=Q,
            omega=omega,
            tau=tau,
            risk_aversion=risk_aversion,
            pi=pi,
        )
        return pd.Series(posterior, index=self.tickers)

    def optimize_black_litterman(
        self,
        P: np.ndarray | None = None,
        Q: np.ndarray | None = None,
        *,
        w_mkt: np.ndarray | None = None,
        omega: np.ndarray | str | None = None,
        tau: float = 0.05,
        risk_aversion: float = 2.5,
        pi: np.ndarray | None = None,
        **cons,
    ) -> PortfolioResult:
        """Maximize Sharpe on the Black-Litterman posterior expected returns.

        Computes the BL posterior (see :meth:`black_litterman_returns`), sets it
        as ``self.mean_returns``, then runs the existing max-Sharpe optimizer so
        the result is produced through the shared :meth:`_make_result` /
        ``portfolio_*`` metrics path -- giving valid long-only weights that sum
        to 1 (under default constraints) and the same ``PortfolioResult`` shape
        as every other objective. ``self.mean_returns`` is restored afterward so
        this call has no lingering side effect on the optimizer state.

        This is purely OPT-IN: with no views the posterior is the equilibrium
        prior, so the optimization runs on the equilibrium returns. Accepts the
        same constraint kwargs (``min_weights``, ``max_weights``, ``allow_short``,
        ``groups``) as the other ``optimize_*`` methods.
        """
        if self.cov_matrix is None:
            raise ValueError("Call calculate_returns() first")
        posterior = self.black_litterman_returns(
            P,
            Q,
            w_mkt=w_mkt,
            omega=omega,
            tau=tau,
            risk_aversion=risk_aversion,
            pi=pi,
        )
        saved = self.mean_returns
        try:
            self.mean_returns = posterior
            bounds, extra = self._build_bounds_constraints(**cons)
            weights = self._solve(
                lambda w: -self.portfolio_sharpe(w),
                bounds=bounds,
                extra_constraints=extra,
                label="Black-Litterman optimization",
            )
            result = self._make_result(weights, "black_litterman")
        finally:
            self.mean_returns = saved
        return result

    def optimize_sortino(self, mar_annual: float | None = None, **cons) -> PortfolioResult:
        """Find the portfolio that maximizes the Sortino ratio."""
        bounds, extra = self._build_bounds_constraints(**cons)
        weights = self._solve(
            lambda w: -self.portfolio_sortino(w, mar_annual),
            bounds=bounds,
            extra_constraints=extra,
            label="Sortino optimization",
        )
        return self._make_result(weights, "sortino")

    def optimize_min_cvar(self, confidence: float = 0.95, **cons) -> PortfolioResult:
        """Minimize historical CVaR via the Rockafellar-Uryasev linear program.

        Empirical CVaR is piecewise-linear and non-smooth, so SLSQP can stall.
        The R-U reformulation is an exact convex LP solved with scipy ``linprog``.
        """
        if self.mean_returns is None:
            raise ValueError("Call calculate_returns() first")
        weights = self._min_cvar_lp(confidence, **cons)
        return self._make_result(weights, "min_cvar")

    def _min_cvar_lp(
        self, confidence, min_weights=None, max_weights=None, allow_short: bool = False, groups=None
    ) -> np.ndarray:
        """Solve the R-U min-CVaR LP. Decision vector z = [w(n), alpha(1), u(T)]."""
        R = self.returns.values  # type: ignore[union-attr]  # returns is set before call  # (T, n)
        T, n = R.shape
        coef = 1.0 / ((1.0 - confidence) * T)

        # objective: alpha + coef * sum(u)
        c = np.concatenate([np.zeros(n), [1.0], np.full(T, coef)])

        # u_t >= -R_t @ w - alpha  ->  -R_t@w - alpha - u_t <= 0
        A_loss = np.hstack([-R, -np.ones((T, 1)), -np.eye(T)])
        b_loss = np.zeros(T)

        A_ub_parts: list[np.ndarray] = [A_loss]
        b_ub_parts: list[np.ndarray] = [b_loss]
        for mask, gmin, gmax in self._group_masks(groups):
            row = np.concatenate([mask, [0.0], np.zeros(T)])
            A_ub_parts.append(-row[None, :])
            b_ub_parts.append(np.array([-gmin]))  # mask@w >= gmin
            A_ub_parts.append(row[None, :])
            b_ub_parts.append(np.array([gmax]))  # mask@w <= gmax
        A_ub = np.vstack(A_ub_parts)
        b_ub = np.concatenate(b_ub_parts)

        # budget: sum(w) = 1
        A_eq = np.concatenate([np.ones(n), [0.0], np.zeros(T)])[None, :]
        b_eq = np.array([1.0])

        lo = self._normalize_bound(min_weights, -1.0 if allow_short else 0.0)
        hi = self._normalize_bound(max_weights, 1.0)
        bounds = (
            [(lo[i], hi[i]) for i in range(n)]  # w
            + [(None, None)]  # alpha free
            + [(0.0, None)] * T  # u >= 0
        )

        res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
        if not res.success:
            raise ValueError(f"Min-CVaR LP failed: {res.message}")
        return res.x[:n]

    def optimize_max_return_target_vol(self, target_vol: float, **cons) -> PortfolioResult:
        """Maximize expected return subject to volatility <= target_vol."""
        bounds, extra = self._build_bounds_constraints(**cons)
        extra = list(extra) + [
            {"type": "ineq", "fun": lambda w: target_vol - self.portfolio_volatility(w)}
        ]
        weights = self._solve(
            lambda w: -self.portfolio_return(w),
            bounds=bounds,
            extra_constraints=extra,
            label="Max-return @ target-vol",
        )
        return self._make_result(weights, "max_return_target_vol")

    def optimize_min_vol_target_return(self, target_return: float, **cons) -> PortfolioResult:
        """Minimize volatility subject to expected return >= target_return."""
        if not cons.get("allow_short"):
            max_achievable = float(np.asarray(self.mean_returns).max())
            if target_return > max_achievable + 1e-9:
                raise ValueError(
                    f"Target return {target_return:.4f} exceeds the max achievable "
                    f"long-only return {max_achievable:.4f}"
                )
        bounds, extra = self._build_bounds_constraints(**cons)
        extra = list(extra) + [
            {"type": "ineq", "fun": lambda w: self.portfolio_return(w) - target_return}
        ]
        weights = self._solve(
            self.portfolio_volatility,
            bounds=bounds,
            extra_constraints=extra,
            label="Min-vol @ target-return",
        )
        return self._make_result(weights, "min_vol_target_return")
