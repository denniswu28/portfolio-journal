"""Phase 4: overlay technical SignalSets onto band-derived basket verdicts.

The band tells us *whether* a basket is off-policy (ADD/TRIM/HOLD); the technical
signal tells us *whether the tape agrees*. Combining them turns a band-only verdict
into an evidence-based one with a confidence label — without ever changing the
deterministic band math. Pure function over already-computed SignalSets (the network
fetch happens in the CLI), so it is fully testable offline.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from src.advisory.models import BasketActionCandidate
from src.quant.signals import SignalSet

_BULLISH_THRESHOLD = 0.34
_BEARISH_THRESHOLD = -0.34


def _bias(signal: SignalSet) -> str:
    score = (signal.composite or {}).get("overall")
    if score is None:
        return "neutral"
    if score >= _BULLISH_THRESHOLD:
        return "bullish"
    if score <= _BEARISH_THRESHOLD:
        return "bearish"
    return "neutral"


def _confidence_for(verdict: str, bias: str) -> Tuple[str, str]:
    """Return (confidence_label, note_suffix) for a verdict given the signal bias."""
    if verdict == "ADD":
        if bias == "bullish":
            return "confirmed", "signals confirm uptrend — add on schedule."
        if bias == "bearish":
            return "counter-trend", "signals bearish — scale in slowly / wait for stabilization."
        return "neutral", "signals neutral."
    if verdict == "TRIM":
        if bias == "bearish":
            return "confirmed", "signals confirm weakness — trim now."
        if bias == "bullish":
            return "into-strength", "signals strong — trim into strength (partial)."
        return "neutral", "signals neutral."
    # HOLD
    if bias == "bullish":
        return "bullish-watch", "within band; signals bullish — accumulate on dips."
    if bias == "bearish":
        return "bearish-watch", "within band; signals bearish — watch for exit."
    return "neutral", "within band; signals neutral."


def enrich_basket_actions(
    candidates: List[BasketActionCandidate],
    signals_by_ticker: Optional[Dict[str, SignalSet]],
    representative: Optional[Dict[str, str]],
) -> List[BasketActionCandidate]:
    """Attach signal bias + confidence to each verdict (band math unchanged).

    ``representative`` maps a basket name to the ticker whose signal stands in for the
    basket (typically its top holding). Candidates without a usable signal pass through
    unchanged.
    """
    signals_by_ticker = signals_by_ticker or {}
    representative = representative or {}
    enriched: List[BasketActionCandidate] = []

    for candidate in candidates:
        ticker = (representative.get(candidate.basket) or "").upper()
        signal = signals_by_ticker.get(ticker) if ticker else None
        if signal is None or signal.close is None:
            enriched.append(candidate)
            continue

        bias = _bias(signal)
        confidence, suffix = _confidence_for(candidate.verdict, bias)
        score = (signal.composite or {}).get("overall")
        enriched.append(BasketActionCandidate(
            basket=candidate.basket,
            weight_pct=candidate.weight_pct,
            band_min_pct=candidate.band_min_pct,
            band_max_pct=candidate.band_max_pct,
            band_status=candidate.band_status,
            verdict=candidate.verdict,
            note=f"{candidate.note} [{ticker} {bias}] {suffix}",
            signal_ticker=ticker,
            signal_score=score,
            signal_flags=list(signal.flags),
            confidence=confidence,
        ))
    return enriched
