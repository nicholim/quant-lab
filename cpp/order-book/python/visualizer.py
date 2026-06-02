import matplotlib.pyplot as plt
import numpy as np


class OrderBookVisualizer:
    """Visualize order book depth, trade tape, and spread."""

    @staticmethod
    def plot_depth_chart(bids: list[dict], asks: list[dict], save_path: str | None = None) -> None:
        """Standard depth chart: price vs cumulative volume.

        Args:
            bids: [{"price": float, "quantity": int}, ...] sorted desc by price
            asks: [{"price": float, "quantity": int}, ...] sorted asc by price
        """
        fig, ax = plt.subplots(figsize=(12, 6))

        if bids:
            bid_prices = [b["price"] for b in bids]
            bid_cum_qty = np.cumsum([b["quantity"] for b in bids])
            ax.fill_between(bid_prices, bid_cum_qty, alpha=0.3, color="green", step="post")
            ax.step(bid_prices, bid_cum_qty, color="green", linewidth=2, where="post", label="Bids")

        if asks:
            ask_prices = [a["price"] for a in asks]
            ask_cum_qty = np.cumsum([a["quantity"] for a in asks])
            ax.fill_between(ask_prices, ask_cum_qty, alpha=0.3, color="red", step="post")
            ax.step(ask_prices, ask_cum_qty, color="red", linewidth=2, where="post", label="Asks")

        ax.set_title("Order Book Depth", fontsize=14)
        ax.set_xlabel("Price")
        ax.set_ylabel("Cumulative Quantity")
        ax.legend()
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.show()

    @staticmethod
    def plot_trade_tape(trades: list[dict], save_path: str | None = None) -> None:
        """Time series of executed trades.

        Args:
            trades: [{"timestamp": int, "price": float, "quantity": int}, ...]
        """
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

        times = range(len(trades))
        prices = [t["price"] for t in trades]
        quantities = [t["quantity"] for t in trades]

        ax1.plot(times, prices, "o-", markersize=3, linewidth=1, color="steelblue")
        ax1.set_title("Trade Tape — Price", fontsize=14)
        ax1.set_ylabel("Price")
        ax1.grid(True, alpha=0.3)

        ax2.bar(times, quantities, color="steelblue", alpha=0.7)
        ax2.set_title("Trade Tape — Volume", fontsize=14)
        ax2.set_xlabel("Trade #")
        ax2.set_ylabel("Quantity")
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.show()

    @staticmethod
    def plot_spread_over_time(spreads: list[float], save_path: str | None = None) -> None:
        """Bid-ask spread over time."""
        fig, ax = plt.subplots(figsize=(14, 5))
        ax.plot(spreads, linewidth=1, color="purple")
        ax.fill_between(range(len(spreads)), spreads, alpha=0.2, color="purple")
        ax.set_title("Bid-Ask Spread Over Time", fontsize=14)
        ax.set_xlabel("Time Step")
        ax.set_ylabel("Spread")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.show()


# Demo with sample data
if __name__ == "__main__":
    viz = OrderBookVisualizer()

    # Sample depth data
    bids = [
        {"price": 150.00, "quantity": 150},
        {"price": 149.50, "quantity": 200},
        {"price": 149.00, "quantity": 100},
        {"price": 148.50, "quantity": 300},
        {"price": 148.00, "quantity": 250},
    ]
    asks = [
        {"price": 150.50, "quantity": 100},
        {"price": 151.00, "quantity": 200},
        {"price": 151.50, "quantity": 50},
        {"price": 152.00, "quantity": 175},
        {"price": 152.50, "quantity": 300},
    ]
    viz.plot_depth_chart(bids, asks)

    # Sample trades
    trades = [
        {"price": 150.0 + np.random.randn() * 0.5, "quantity": np.random.randint(10, 200)}
        for _ in range(100)
    ]
    viz.plot_trade_tape(trades)

    # Sample spreads
    spreads = [0.50 + np.random.randn() * 0.1 for _ in range(200)]
    viz.plot_spread_over_time(spreads)
