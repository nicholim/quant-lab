"""Tests for AnalysisConfig parsing, validation, and CLI/JSON precedence.

Pure (no network/IO except a tmp JSON file), so the suite stays deterministic.
"""

import json

import pytest

from portfolio_optimization_engine.config import (
    AnalysisConfig,
    _validate,
    build_parser,
    parse_args,
)

# --- Defaults / dataclass ---


class TestDefaults:
    def test_defaults_are_valid(self):
        # the dataclass defaults must pass validation unchanged
        cfg = _validate(AnalysisConfig())
        assert cfg.objective == "both"
        assert cfg.tickers  # non-empty

    def test_default_tickers_independent_instances(self):
        # default_factory must not share the same list across instances
        a = AnalysisConfig()
        b = AnalysisConfig()
        a.tickers.append("EXTRA")
        assert "EXTRA" not in b.tickers


# --- Validation ---


class TestValidation:
    def test_empty_tickers_raises(self):
        with pytest.raises(ValueError, match="ticker"):
            _validate(AnalysisConfig(tickers=[]))

    def test_bad_date_format_raises(self):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            _validate(AnalysisConfig(start_date="01-2020-01"))

    def test_start_not_before_end_raises(self):
        with pytest.raises(ValueError, match="before"):
            _validate(AnalysisConfig(start_date="2024-01-01", end_date="2020-01-01"))

    def test_equal_dates_raises(self):
        with pytest.raises(ValueError):
            _validate(AnalysisConfig(start_date="2024-01-01", end_date="2024-01-01"))

    def test_non_positive_num_portfolios_raises(self):
        with pytest.raises(ValueError, match="num_portfolios"):
            _validate(AnalysisConfig(num_portfolios=0))

    def test_bad_objective_raises(self):
        with pytest.raises(ValueError, match="objective"):
            _validate(AnalysisConfig(objective="nonsense"))

    def test_bad_export_format_raises(self):
        with pytest.raises(ValueError, match="export_format"):
            _validate(AnalysisConfig(export_format="xml"))

    @pytest.mark.parametrize(
        "objective",
        [
            "sharpe",
            "min_vol",
            "risk_parity",
            "sortino",
            "min_cvar",
            "min_cdar",
            "hrp",
            "both",
            "all",
        ],
    )
    def test_all_documented_objectives_valid(self, objective):
        _validate(AnalysisConfig(objective=objective))


# --- CLI parsing ---


class TestCLI:
    def test_parser_builds(self):
        assert build_parser() is not None

    def test_cli_overrides_defaults(self):
        cfg = parse_args(["--tickers", "AAPL", "MSFT", "--objective", "sharpe"])
        assert cfg.tickers == ["AAPL", "MSFT"]
        assert cfg.objective == "sharpe"

    def test_unset_flags_keep_defaults(self):
        cfg = parse_args(["--objective", "min_vol"])
        # untouched flags fall back to dataclass defaults
        assert cfg.risk_free_rate == AnalysisConfig().risk_free_rate

    def test_random_state_parsed_as_int(self):
        cfg = parse_args(["--random-state", "7"])
        assert cfg.random_state == 7

    def test_no_plots_flag(self):
        cfg = parse_args(["--no-plots"])
        assert cfg.no_plots is True

    def test_offline_flag(self):
        cfg = parse_args(["--offline"])
        assert cfg.offline is True

    def test_offline_defaults_false(self):
        cfg = parse_args(["--objective", "sharpe"])
        assert cfg.offline is False


# --- JSON config + precedence ---


class TestJsonConfig:
    def test_json_config_loaded(self, tmp_path):
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps({"objective": "min_vol", "num_portfolios": 100}))
        cfg = parse_args(["--config", str(path)])
        assert cfg.objective == "min_vol"
        assert cfg.num_portfolios == 100

    def test_cli_overrides_json(self, tmp_path):
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps({"objective": "min_vol"}))
        # explicit CLI flag wins over the file value
        cfg = parse_args(["--config", str(path), "--objective", "sharpe"])
        assert cfg.objective == "sharpe"

    def test_unknown_json_key_raises(self, tmp_path):
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps({"bogus_key": 1}))
        with pytest.raises(ValueError, match="Unknown keys"):
            parse_args(["--config", str(path)])

    def test_invalid_config_from_json_rejected(self, tmp_path):
        # JSON values still go through _validate
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps({"num_portfolios": -5}))
        with pytest.raises(ValueError):
            parse_args(["--config", str(path)])
