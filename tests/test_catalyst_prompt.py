from datetime import date
from src.prompt_engine.catalyst_prompt import generate_catalyst_prompt


def test_prompt_lists_tickers_events_and_schema():
    text = generate_catalyst_prompt(
        as_of=date(2026, 6, 12),
        held=[("NVDA", 12.5, 130.0), ("MU", 6.0, 95.0)],
        watchlist=["SMH", "GLDM"],
        events=[("2026-06-17", "June FOMC decision", "market")],
        generated_by_hint="perplexity",
    )
    # Held + watchlist tickers appear
    for t in ("NVDA", "MU", "SMH", "GLDM"):
        assert t in text
    # Upcoming event surfaced
    assert "June FOMC decision" in text
    # Schema instructions present (must request the exact YAML keys)
    for key in ("as_of:", "items:", "direction:", "summary:", "bull | bear | neutral"):
        assert key in text
    # ASCII-only (Windows-safe)
    assert text.encode("ascii", errors="strict")


def test_prompt_handles_no_events():
    text = generate_catalyst_prompt(
        as_of=date(2026, 6, 12), held=[("NVDA", 1.0, 1.0)],
        watchlist=[], events=[], generated_by_hint="claude",
    )
    assert "NVDA" in text
    assert "No calendar events" in text
