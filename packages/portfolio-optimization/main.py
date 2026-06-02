from pathlib import Path

from portfolio_optimization_engine.analysis import print_report, run_analysis
from portfolio_optimization_engine.config import parse_args
from portfolio_optimization_engine.export import export
from portfolio_optimization_engine.visualization import (
    plot_correlation_matrix,
    plot_cumulative_returns,
    plot_efficient_frontier,
    plot_portfolio_weights,
)


def _render_plots(analysis, config) -> None:
    optimizer = analysis["optimizer"]
    frontier = analysis["frontier"]
    results = analysis["results"]

    out = Path(config.output_dir) if config.export_format != "none" else None

    def path(name):
        return str(out / name) if out else None

    extras = {n: r for n, r in results.items() if n not in ("max_sharpe", "min_vol")}
    plot_efficient_frontier(
        frontier,
        results.get("max_sharpe"),
        results.get("min_vol"),
        extra_portfolios=extras or None,
        save_path=path("efficient_frontier.png"),
    )
    plot_correlation_matrix(optimizer.returns, save_path=path("correlation_matrix.png"))
    primary = results[analysis["primary"]]
    plot_portfolio_weights(primary, config.tickers, save_path=path("weights.png"))
    plot_cumulative_returns(optimizer.returns, save_path=path("cumulative_returns.png"))
    analysis["monte_carlo"]["simulator"].plot_simulations(save_path=path("monte_carlo.png"))


def main(argv=None) -> None:
    config = parse_args(argv)
    print(f"Fetching data for {', '.join(config.tickers)}...")
    analysis = run_analysis(config)
    print_report(analysis, config)

    if config.export_format != "none":
        paths = export(
            config, analysis["results"], config.tickers, analysis["metrics"], analysis["mc_summary"]
        )
        if paths:
            print("\nExported:")
            for p in paths:
                print(f"  {p}")

    if not config.no_plots:
        print("\nGenerating visualizations...")
        _render_plots(analysis, config)

    print("\nDone.")


if __name__ == "__main__":
    main()
