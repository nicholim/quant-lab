import numpy as np


class BinomialTree:
    """Cox-Ross-Rubinstein binomial tree for option pricing."""

    def __init__(
        self,
        S: float,
        K: float,
        T: float,
        r: float,
        sigma: float,
        N: int = 100,
        option_type: str = "call",
        american: bool = False,
    ):
        self.S = S
        self.K = K
        self.T = T
        self.r = r
        self.sigma = sigma
        self.N = N
        self.option_type = option_type
        self.american = american

        # CRR parameters
        self.dt = T / N
        self.u = np.exp(sigma * np.sqrt(self.dt))
        self.d = 1 / self.u
        self.p = (np.exp(r * self.dt) - self.d) / (self.u - self.d)
        self.discount = np.exp(-r * self.dt)

    def _payoff(self, spot: float) -> float:
        if self.option_type == "call":
            return max(spot - self.K, 0.0)
        else:
            return max(self.K - spot, 0.0)

    def price(self) -> float:
        """Compute option price using backward induction."""
        # Terminal payoffs
        spots = self.S * self.u ** np.arange(self.N, -1, -1) * self.d ** np.arange(0, self.N + 1)
        values = np.array([self._payoff(s) for s in spots])

        # Backward induction
        for step in range(self.N - 1, -1, -1):
            values = self.discount * (self.p * values[:-1] + (1 - self.p) * values[1:])

            if self.american:
                spots = (
                    self.S * self.u ** np.arange(step, -1, -1) * self.d ** np.arange(0, step + 1)
                )
                exercise = np.array([self._payoff(s) for s in spots])
                values = np.maximum(values, exercise)

        return float(values[0])

    def build_tree(self) -> tuple[np.ndarray, np.ndarray]:
        """Build full price and option value trees for visualization.

        Returns (price_tree, value_tree) each of shape (N+1, N+1) with NaN padding.
        """
        n = self.N
        price_tree = np.full((n + 1, n + 1), np.nan)
        value_tree = np.full((n + 1, n + 1), np.nan)

        # Build price tree
        for step in range(n + 1):
            for node in range(step + 1):
                price_tree[node, step] = self.S * self.u ** (step - node) * self.d**node

        # Terminal values
        for node in range(n + 1):
            value_tree[node, n] = self._payoff(price_tree[node, n])

        # Backward induction
        for step in range(n - 1, -1, -1):
            for node in range(step + 1):
                hold = self.discount * (
                    self.p * value_tree[node, step + 1]
                    + (1 - self.p) * value_tree[node + 1, step + 1]
                )
                if self.american:
                    exercise = self._payoff(price_tree[node, step])
                    value_tree[node, step] = max(hold, exercise)
                else:
                    value_tree[node, step] = hold

        return price_tree, value_tree
