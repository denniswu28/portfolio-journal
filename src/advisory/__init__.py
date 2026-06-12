"""Daily advisory packet: orchestrates the existing pipeline into one dated brief.

Read-only with respect to portfolio state — it never logs trades or routes orders.
Deterministic numbers come from the existing harness; the boist thesis and LLM prompt
are narrative overlay only. Option ideas are gated (label, don't hide) per AGENTS.md
and ``config/persistent_context.yaml``.
"""
