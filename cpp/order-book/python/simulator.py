import json
import random

import numpy as np


class MarketSimulator:
    """Generate realistic order flow for testing the matching engine."""

    def __init__(self, symbol: str = "AAPL", price_center: float = 150.0, spread: float = 0.50):
        self.symbol = symbol
        self.price_center = price_center
        self.spread = spread

    def generate_random_orders(self, n: int) -> list[dict]:
        """Generate n random orders with realistic price distribution."""
        orders = []
        price = self.price_center

        for i in range(n):
            # Random walk for price center
            price += np.random.randn() * 0.10

            side = random.choice(["BUY", "SELL"])
            order_type = random.choices(
                ["LIMIT", "MARKET"],
                weights=[0.8, 0.2],
            )[0]

            if order_type == "LIMIT":
                if side == "BUY":
                    order_price = round(price - abs(np.random.randn() * self.spread), 2)
                else:
                    order_price = round(price + abs(np.random.randn() * self.spread), 2)
            else:
                order_price = 0.0

            quantity = random.choice([10, 25, 50, 100, 200, 500])

            orders.append(
                {
                    "id": i + 1,
                    "symbol": self.symbol,
                    "side": side,
                    "type": order_type,
                    "price": order_price,
                    "quantity": quantity,
                }
            )

        return orders

    def save_orders(self, orders: list[dict], filepath: str = "orders.json") -> None:
        """Save generated orders to JSON file."""
        with open(filepath, "w") as f:
            json.dump(orders, f, indent=2)
        print(f"Saved {len(orders)} orders to {filepath}")


if __name__ == "__main__":
    sim = MarketSimulator(symbol="AAPL", price_center=150.0, spread=0.50)
    orders = sim.generate_random_orders(500)
    sim.save_orders(orders)

    # Print summary
    buys = sum(1 for o in orders if o["side"] == "BUY")
    sells = sum(1 for o in orders if o["side"] == "SELL")
    markets = sum(1 for o in orders if o["type"] == "MARKET")
    limits = sum(1 for o in orders if o["type"] == "LIMIT")

    print(f"\nGenerated {len(orders)} orders:")
    print(f"  BUY: {buys}, SELL: {sells}")
    print(f"  LIMIT: {limits}, MARKET: {markets}")
