"""Tests for the CLI wiring (``--exchange`` flag) and ``.env`` / dotenv parity.

No live network or asyncio loop: the CLI is exercised through the importable
``build_parser`` / ``build_config`` helpers, and the dotenv behavior is verified
by writing a temporary ``.env`` and reloading the config module.
"""

import os

import pytest

import main as cli_main
from src.adapters import BitstampAdapter, CoinbaseAdapter, KrakenAdapter
from src.config import Config
from src.pipeline import build_exchange_adapter


class TestExchangeFlag:
    def test_default_no_exchange_flag_preserves_env(self, monkeypatch):
        monkeypatch.delenv("EXCHANGE", raising=False)
        args = cli_main.build_parser().parse_args([])
        assert args.exchange is None
        cfg = cli_main.build_config(args)
        # Unset flag -> falls back to the env/.env default (binance).
        assert cfg.exchange == "binance"

    def test_exchange_flag_overrides_to_coinbase(self, monkeypatch):
        monkeypatch.delenv("EXCHANGE", raising=False)
        args = cli_main.build_parser().parse_args(["--exchange", "coinbase"])
        cfg = cli_main.build_config(args)
        assert cfg.exchange == "coinbase"
        assert isinstance(build_exchange_adapter(cfg), CoinbaseAdapter)

    def test_exchange_flag_selects_kraken(self, monkeypatch):
        monkeypatch.delenv("EXCHANGE", raising=False)
        args = cli_main.build_parser().parse_args(["--exchange", "kraken"])
        cfg = cli_main.build_config(args)
        assert cfg.exchange == "kraken"
        assert isinstance(build_exchange_adapter(cfg), KrakenAdapter)

    def test_exchange_flag_selects_bitstamp(self, monkeypatch):
        monkeypatch.delenv("EXCHANGE", raising=False)
        args = cli_main.build_parser().parse_args(["--exchange", "bitstamp"])
        cfg = cli_main.build_config(args)
        assert cfg.exchange == "bitstamp"
        assert isinstance(build_exchange_adapter(cfg), BitstampAdapter)

    def test_exchange_flag_is_lowercased(self, monkeypatch):
        monkeypatch.delenv("EXCHANGE", raising=False)
        args = cli_main.build_parser().parse_args(["--exchange", "KRAKEN"])
        cfg = cli_main.build_config(args)
        assert cfg.exchange == "kraken"

    def test_exchange_flag_overrides_env(self, monkeypatch):
        # A real EXCHANGE env var is set, but the flag should win.
        monkeypatch.setenv("EXCHANGE", "binance")
        args = cli_main.build_parser().parse_args(["--exchange", "bitstamp"])
        cfg = cli_main.build_config(args)
        assert cfg.exchange == "bitstamp"

    def test_symbols_flag_still_works(self, monkeypatch):
        monkeypatch.delenv("SYMBOLS", raising=False)
        args = cli_main.build_parser().parse_args(["--symbols", "btcusd,ethusd"])
        cfg = cli_main.build_config(args)
        assert cfg.symbols == ["btcusd", "ethusd"]


class TestDepthFlag:
    def test_default_no_depth_flag_keeps_depth_off(self, monkeypatch):
        monkeypatch.delenv("ENABLE_DEPTH", raising=False)
        args = cli_main.build_parser().parse_args([])
        assert args.enable_depth is False
        cfg = cli_main.build_config(args)
        # Default: trades only, byte-identical to before.
        assert cfg.enable_depth is False

    def test_enable_depth_flag_turns_depth_on(self, monkeypatch):
        monkeypatch.delenv("ENABLE_DEPTH", raising=False)
        args = cli_main.build_parser().parse_args(["--enable-depth"])
        cfg = cli_main.build_config(args)
        assert cfg.enable_depth is True

    def test_enable_depth_env_var_parsing(self, monkeypatch):
        for truthy in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("ENABLE_DEPTH", truthy)
            assert Config().enable_depth is True
        for falsy in ("0", "false", "no", "", "off"):
            monkeypatch.setenv("ENABLE_DEPTH", falsy)
            assert Config().enable_depth is False

    def test_multi_symbol_fanout_from_cli(self, monkeypatch):
        monkeypatch.delenv("SYMBOLS", raising=False)
        args = cli_main.build_parser().parse_args(
            ["--symbols", "btcusdt,ethusdt,solusdt", "--enable-depth"]
        )
        cfg = cli_main.build_config(args)
        assert cfg.symbols == ["btcusdt", "ethusdt", "solusdt"]
        assert cfg.enable_depth is True


class TestDotenv:
    @pytest.fixture(autouse=True)
    def _isolate_dotenv(self, monkeypatch):
        """Snapshot env + the dotenv latch, restore both after each test.

        ``load_dotenv`` mutates ``os.environ`` with keys monkeypatch didn't set
        (so monkeypatch can't auto-revert them); snapshot and restore the full
        environment so a temp ``.env`` never leaks into later tests, and reset
        the module's idempotency latch so the temp ``.env`` is actually loaded.
        """
        import src.config as config_mod

        before_env = dict(os.environ)
        before_latch = config_mod._dotenv_loaded
        yield
        os.environ.clear()
        os.environ.update(before_env)
        config_mod._dotenv_loaded = before_latch

    def test_real_env_overrides_dotenv(self, tmp_path, monkeypatch):
        """A real exported env var must win over a value in ``.env``."""
        env_file = tmp_path / ".env"
        env_file.write_text("EXCHANGE=bitstamp\nREDIS_URL=redis://from-dotenv:6379\n")
        monkeypatch.chdir(tmp_path)
        # Real env var present: dotenv must NOT clobber it.
        monkeypatch.setenv("EXCHANGE", "kraken")
        monkeypatch.delenv("REDIS_URL", raising=False)

        import src.config as config_mod

        # Reset the idempotency latch so the temp .env is actually loaded.
        config_mod._dotenv_loaded = False
        config_mod._load_dotenv_once()

        cfg = Config()
        # Real env var wins over .env.
        assert cfg.exchange == "kraken"
        # Unset-in-env value is filled from .env.
        assert cfg.redis_url == "redis://from-dotenv:6379"

    def test_dotenv_fills_unset_vars(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("EXCHANGE=coinbase\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("EXCHANGE", raising=False)

        import src.config as config_mod

        config_mod._dotenv_loaded = False
        config_mod._load_dotenv_once()

        assert Config().exchange == "coinbase"

    def test_load_dotenv_once_is_idempotent(self, tmp_path, monkeypatch):
        """A second call after the latch is set is a no-op (won't reload)."""
        env_file = tmp_path / ".env"
        env_file.write_text("EXCHANGE=bitstamp\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("EXCHANGE", raising=False)

        import src.config as config_mod

        # Latch already True -> the temp .env is NOT loaded.
        config_mod._dotenv_loaded = True
        config_mod._load_dotenv_once()
        assert config_mod.Config().exchange == "binance"
