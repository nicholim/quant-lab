import argparse
import asyncio
import logging
import signal

from src.config import Config
from src.pipeline import Pipeline


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def run(config: Config) -> None:
    pipeline = Pipeline(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(pipeline.stop()))

    await pipeline.start()


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser (importable so the wiring is testable)."""
    parser = argparse.ArgumentParser(description="Real-Time Market Data Pipeline")
    parser.add_argument(
        "--symbols", type=str, help="Comma-separated symbols (e.g., btcusdt,ethusdt)"
    )
    parser.add_argument(
        "--exchange",
        type=str,
        default=None,
        help=(
            "Exchange adapter to stream from (overrides EXCHANGE / .env). "
            "One of: binance, coinbase, kraken, bitstamp. Default: binance."
        ),
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser


def build_config(args: argparse.Namespace) -> Config:
    """Apply parsed CLI args onto a fresh :class:`Config` (env/.env defaults).

    Each flag overrides the corresponding env/.env value; unset flags preserve
    the prior behavior (``--exchange`` defaults to ``EXCHANGE`` / binance).
    """
    config = Config()
    if args.symbols:
        config.symbols = args.symbols.split(",")
    if args.exchange:
        config.exchange = args.exchange.lower()
    if args.log_level:
        config.log_level = args.log_level
    return config


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config = build_config(args)

    setup_logging(config.log_level)
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
