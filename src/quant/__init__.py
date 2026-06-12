"""Quantitative analysis suite: technicals, signals, backtesting, factor models.

Deterministic-first (AGENTS.md): every number here is computed by pure functions
over price history; nothing calls an LLM and nothing routes orders. Annualization is
period-aware (``periods_per_year``) so weekly/monthly backtests are not mis-scaled the
way a daily-only 252 assumption would be.
"""
