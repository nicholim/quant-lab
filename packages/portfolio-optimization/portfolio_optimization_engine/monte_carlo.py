import matplotlib.pyplot as plt
import numpy as np


class MonteCarloSimulator:
    """Monte Carlo simulation for portfolio value projection using GBM."""

    def __init__(
        self,
        expected_return: float,
        volatility: float,
        initial_value: float = 100_000,
    ):
        self.expected_return = expected_return
        self.volatility = volatility
        self.initial_value = initial_value
        self.simulations: np.ndarray | None = None

    def simulate(
        self,
        num_simulations: int = 10_000,
        num_days: int = 252,
        random_state: int | None = None,
    ) -> np.ndarray:
        """Project portfolio value using geometric Brownian motion.

        Args:
            num_simulations: Number of Monte Carlo paths.
            num_days: Trading days to project.
            random_state: Seed for reproducibility (None for non-deterministic).

        Returns array of shape (num_simulations, num_days + 1).
        """
        rng = np.random.default_rng(random_state)
        dt = 1 / 252
        drift = (self.expected_return - 0.5 * self.volatility**2) * dt
        diffusion = self.volatility * np.sqrt(dt)

        random_shocks = rng.standard_normal((num_simulations, num_days))
        log_returns = drift + diffusion * random_shocks

        # Cumulative returns starting from initial value
        cumulative = np.exp(np.cumsum(log_returns, axis=1))
        paths = np.ones((num_simulations, num_days + 1)) * self.initial_value
        paths[:, 1:] = self.initial_value * cumulative

        self.simulations = paths
        return paths

    def calculate_var(self, confidence: float = 0.95) -> float:
        """Value at Risk at specified confidence level."""
        if self.simulations is None:
            raise ValueError("Call simulate() first")
        final_values = self.simulations[:, -1]
        pnl = final_values - self.initial_value
        return float(-np.percentile(pnl, (1 - confidence) * 100))

    def calculate_cvar(self, confidence: float = 0.95) -> float:
        """Conditional Value at Risk (Expected Shortfall)."""
        if self.simulations is None:
            raise ValueError("Call simulate() first")
        final_values = self.simulations[:, -1]
        pnl = final_values - self.initial_value
        var_threshold = np.percentile(pnl, (1 - confidence) * 100)
        return float(-np.mean(pnl[pnl <= var_threshold]))

    def plot_simulations(self, num_paths: int = 200, save_path: str | None = None) -> None:
        """Plot Monte Carlo simulation paths."""
        if self.simulations is None:
            raise ValueError("Call simulate() first")

        fig, axes = plt.subplots(1, 2, figsize=(16, 6))

        # Simulation paths
        ax = axes[0]
        subset = self.simulations[:num_paths]
        for path in subset:
            ax.plot(path, alpha=0.1, linewidth=0.5, color="steelblue")
        ax.plot(np.median(self.simulations, axis=0), color="red", linewidth=2, label="Median")
        ax.set_title("Monte Carlo Simulation Paths")
        ax.set_xlabel("Trading Days")
        ax.set_ylabel("Portfolio Value ($)")
        ax.legend()
        ax.grid(True, alpha=0.3)

        # Final value distribution
        ax = axes[1]
        final_values = self.simulations[:, -1]
        ax.hist(final_values, bins=80, alpha=0.7, color="steelblue", edgecolor="white")
        var_95 = self.initial_value - self.calculate_var(0.95)
        ax.axvline(
            var_95, color="red", linestyle="--", linewidth=2, label=f"VaR 95%: ${var_95:,.0f}"
        )
        ax.axvline(
            np.median(final_values),
            color="green",
            linestyle="--",
            linewidth=2,
            label=f"Median: ${np.median(final_values):,.0f}",
        )
        ax.set_title("Distribution of Final Portfolio Values")
        ax.set_xlabel("Portfolio Value ($)")
        ax.set_ylabel("Frequency")
        ax.legend()
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.show()
