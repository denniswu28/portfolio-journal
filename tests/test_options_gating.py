"""Tests for the fail-closed options gating predicate and config loading."""

import pytest

from src.advisory.gating import options_gate_status
from src.data_ingestion.models import OptionsGating, PersistentContext
from src.utils.config_loader import load_persistent_context


def test_default_is_fail_closed():
    status = options_gate_status(PersistentContext(), account_value=50_000)
    assert status.executable is False
    assert status.privilege_pending is True
    assert "pending" in status.reason.lower()


def test_under_minimum_blocks_even_when_enabled():
    gating = OptionsGating(options_enabled=True, options_min_account_value=10_000)
    status = options_gate_status(gating, account_value=5_000)
    assert status.executable is False
    assert status.privilege_pending is False
    assert "below" in status.reason.lower()


def test_executable_when_enabled_and_funded():
    gating = OptionsGating(options_enabled=True, options_min_account_value=10_000)
    status = options_gate_status(gating, account_value=12_000)
    assert status.executable is True
    assert "active" in status.reason.lower()


def test_enabled_account_override_still_requires_minimum():
    gating = OptionsGating(options_enabled=False, enabled_accounts=["Z123"],
                           options_min_account_value=10_000)
    # Privilege satisfied by the account, but value still too low.
    low = options_gate_status(gating, account_value=8_000, account_id="Z123")
    assert low.executable is False and low.privilege_pending is False
    # Funded + enabled account -> executable.
    ok = options_gate_status(gating, account_value=15_000, account_id="Z123")
    assert ok.executable is True


def test_banner_text():
    status = options_gate_status(PersistentContext(), account_value=1_000)
    assert "ADVISORY ONLY" in status.banner


def test_persistent_context_yaml_round_trip_loads_gating():
    ctx = load_persistent_context()
    assert isinstance(ctx.options_gating, OptionsGating)
    # Repo default config keeps the main account gated.
    assert ctx.options_gating.options_enabled is False
    assert ctx.options_gating.options_min_account_value == 10_000


def test_invalid_ctx_type_raises():
    with pytest.raises(TypeError):
        options_gate_status("not-a-context", account_value=1)
