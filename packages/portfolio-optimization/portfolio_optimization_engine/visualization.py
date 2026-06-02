import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from .optimizer import PortfolioResult


def plot_efficient_frontier(
    portfolios: pd.DataFrame,
    optimal_sharpe: PortfolioResult | None = None,
    optimal_min_vol: PortfolioResult | None = None,
    extra_portfolios: dict[str, PortfolioResult] | None = None,
    save_path: str | None = None,
) -> None:
    """Scatter plot of risk-return with color-coded Sharpe ratio.

    Any of the optimal portfolios may be ``None`` (e.g. when only a single
    objective was run). ``extra_portfolios`` marks additional optimal points
    (risk parity, Sortino, min-CVaR, ...).
    """
    fig, ax = plt.subplots(figsize=(12, 8))

    scatter = ax.scatter(
        portfolios["volatility"],
        portfolios["return"],
        c=portfolios["sharpe"],
        cmap="viridis",
        alpha=0.5,
        s=10,
    )
    plt.colorbar(scatter, ax=ax, label="Sharpe Ratio")

    # Mark optimal portfolios
    if optimal_sharpe is not None:
        ax.scatter(
            optimal_sharpe.volatility,
            optimal_sharpe.expected_return,
            color="red",
            marker="*",
            s=300,
            zorder=5,
            label=f"Max Sharpe ({optimal_sharpe.sharpe_ratio:.2f})",
        )
    if optimal_min_vol is not None:
        ax.scatter(
            optimal_min_vol.volatility,
            optimal_min_vol.expected_return,
            color="blue",
            marker="*",
            s=300,
            zorder=5,
            label=f"Min Volatility ({optimal_min_vol.volatility:.2%})",
        )
    for name, res in (extra_portfolios or {}).items():
        ax.scatter(
            res.volatility,
            res.expected_return,
            marker="D",
            s=120,
            zorder=5,
            edgecolor="black",
            label=name.replace("_", " ").title(),
        )

    ax.set_title("Efficient Frontier", fontsize=14)
    ax.set_xlabel("Annualized Volatility", fontsize=12)
    ax.set_ylabel("Annualized Return", fontsize=12)
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()


def plot_correlation_matrix(returns: pd.DataFrame, save_path: str | None = None) -> None:
    """Heatmap of asset return correlations."""
    fig, ax = plt.subplots(figsize=(10, 8))
    corr = returns.corr()
    mask = np.triu(np.ones_like(corr, dtype=bool), k=1)
    sns.heatmap(
        corr,
        mask=mask,
        annot=True,
        fmt=".2f",
        cmap="RdBu_r",
        center=0,
        square=True,
        ax=ax,
        linewidths=0.5,
    )
    ax.set_title("Asset Return Correlation Matrix", fontsize=14)
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()


def plot_portfolio_weights(
    result: PortfolioResult, tickers: list[str], save_path: str | None = None
) -> None:
    """Pie chart of portfolio allocation weights."""
    fig, ax = plt.subplots(figsize=(10, 8))

    # Filter out near-zero weights
    threshold = 0.01
    labels = []
    sizes = []
    for ticker, weight in zip(tickers, result.weights, strict=False):
        if weight > threshold:
            labels.append(f"{ticker}\n{weight:.1%}")
            sizes.append(weight)

    colors = plt.get_cmap("Set3")(np.linspace(0, 1, len(labels)))
    ax.pie(sizes, labels=labels, colors=colors, autopct="", startangle=140)
    ax.set_title(
        f"Optimal Portfolio Allocation\n"
        f"Return: {result.expected_return:.2%} | "
        f"Vol: {result.volatility:.2%} | "
        f"Sharpe: {result.sharpe_ratio:.2f}",
        fontsize=13,
    )
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()


def plot_cumulative_returns(returns: pd.DataFrame, save_path: str | None = None) -> None:
    """Line chart of cumulative returns for each asset."""
    fig, ax = plt.subplots(figsize=(14, 7))
    cumulative = (1 + returns).cumprod()
    cumulative.plot(ax=ax, linewidth=1.5)
    ax.set_title("Cumulative Returns", fontsize=14)
    ax.set_xlabel("Date")
    ax.set_ylabel("Growth of $1")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()


def plot_drawdown(returns: pd.Series, save_path: str | None = None) -> None:
    """Underwater (drawdown) curve of a periodic return series."""
    fig, ax = plt.subplots(figsize=(14, 6))
    equity = (1 + returns).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    ax.fill_between(drawdown.index, drawdown.values, 0.0, color="firebrick", alpha=0.4)
    ax.plot(drawdown.index, drawdown.values, color="firebrick", linewidth=1)
    ax.set_title("Portfolio Drawdown", fontsize=14)
    ax.set_xlabel("Date")
    ax.set_ylabel("Drawdown")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()
