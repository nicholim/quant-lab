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


def main() -> None:
    parser = argparse.ArgumentParser(description="Real-Time Market Data Pipeline")
    parser.add_argument(
        "--symbols", type=str, help="Comma-separated symbols (e.g., btcusdt,ethusdt)"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    config = Config()
    if args.symbols:
        config.symbols = args.symbols.split(",")
    if args.log_level:
        config.log_level = args.log_level

    setup_logging(config.log_level)
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
