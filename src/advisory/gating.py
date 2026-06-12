"""Options gating predicate — the safety-critical, fail-closed gate.

A single tested function decides whether an option idea may be labeled *executable*.
It defaults closed (privilege pending) and is checked before any option ticket is
presented as placeable. When gated, ideas are still shown but clearly labeled
"advisory only — not executable" (label, don't hide).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from src.data_ingestion.models import OptionsGating, PersistentContext


@dataclass(frozen=True)
class GateStatus:
    executable: bool
    reason: str
    account_value: float
    min_required: float
    privilege_pending: bool

    @property
    def banner(self) -> str:
        state = "EXECUTABLE" if self.executable else "ADVISORY ONLY — NOT EXECUTABLE"
        return f"OPTIONS GATE: {state} — {self.reason}"


def _gating_of(ctx: Union[PersistentContext, OptionsGating, None]) -> OptionsGating:
    if ctx is None:
        return OptionsGating()
    if isinstance(ctx, PersistentContext):
        return ctx.options_gating
    if isinstance(ctx, OptionsGating):
        return ctx
    raise TypeError("ctx must be a PersistentContext, OptionsGating, or None")


def options_gate_status(
    ctx: Union[PersistentContext, OptionsGating, None],
    account_value: float,
    account_id: Optional[str] = None,
) -> GateStatus:
    """Return whether options may be presented as executable, with a reason.

    Executable requires BOTH privilege (``options_enabled`` or the account explicitly
    in ``enabled_accounts``) AND ``account_value >= options_min_account_value``. The
    $10k minimum is a hard rule and applies even to an explicitly enabled account.
    """
    gating = _gating_of(ctx)
    min_required = float(gating.options_min_account_value)
    account_value = float(account_value)

    privilege_ok = bool(gating.options_enabled) or (
        account_id is not None and account_id in gating.enabled_accounts
    )
    value_ok = account_value >= min_required

    if not privilege_ok:
        reason = f"Level-2 options privilege pending on {gating.options_account_label}."
    elif not value_ok:
        reason = (
            f"Account/position value ${account_value:,.0f} is below the "
            f"${min_required:,.0f} minimum required to place options."
        )
    else:
        reason = "Level-2 privilege active and account at/above the minimum."

    return GateStatus(
        executable=privilege_ok and value_ok,
        reason=reason,
        account_value=account_value,
        min_required=min_required,
        privilege_pending=not privilege_ok,
    )
