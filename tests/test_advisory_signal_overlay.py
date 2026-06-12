"""Tests for the Phase 4 technical-signal overlay on basket verdicts."""

from src.advisory.models import BasketActionCandidate
from src.advisory.signal_overlay import enrich_basket_actions
from src.quant.signals import SignalSet


def _signal(ticker, overall, flags=None):
    return SignalSet(
        ticker=ticker, as_of=None, close=100.0,
        composite={"overall": overall, "trend": overall, "momentum": overall},
        flags=flags or [],
    )


def _candidate(basket, verdict, status="OK"):
    return BasketActionCandidate(basket=basket, weight_pct=10.0, band_min_pct=5,
                                 band_max_pct=15, band_status=status, verdict=verdict,
                                 note="band note.")


def test_add_confirmed_by_bullish_signal():
    cands = [_candidate("Memory", "ADD", "BELOW")]
    out = enrich_basket_actions(cands, {"MU": _signal("MU", 0.8, ["above_200dma"])},
                                {"Memory": "MU"})
    assert out[0].confidence == "confirmed"
    assert out[0].signal_score == 0.8
    assert "above_200dma" in out[0].signal_flags
    assert "MU bullish" in out[0].note


def test_add_counter_trend_when_bearish():
    out = enrich_basket_actions([_candidate("Memory", "ADD", "BELOW")],
                                {"MU": _signal("MU", -0.7, ["death_cross"])},
                                {"Memory": "MU"})
    assert out[0].confidence == "counter-trend"


def test_trim_into_strength_when_bullish():
    out = enrich_basket_actions([_candidate("AI Platform", "TRIM", "ABOVE")],
                                {"MSFT": _signal("MSFT", 0.9)}, {"AI Platform": "MSFT"})
    assert out[0].confidence == "into-strength"


def test_trim_confirmed_when_bearish():
    out = enrich_basket_actions([_candidate("AI Platform", "TRIM", "ABOVE")],
                                {"MSFT": _signal("MSFT", -0.6)}, {"AI Platform": "MSFT"})
    assert out[0].confidence == "confirmed"


def test_hold_watch_labels():
    out = enrich_basket_actions([_candidate("Gold", "HOLD")],
                                {"GLDM": _signal("GLDM", 0.5)}, {"Gold": "GLDM"})
    assert out[0].confidence == "bullish-watch"


def test_passthrough_when_no_signal():
    cands = [_candidate("Memory", "ADD")]
    out = enrich_basket_actions(cands, {}, {"Memory": "MU"})
    assert out[0].confidence == "" and out[0].signal_score is None
    assert out[0] is cands[0]  # unchanged object


def test_neutral_bias_in_dead_zone():
    out = enrich_basket_actions([_candidate("X", "ADD", "BELOW")],
                                {"T": _signal("T", 0.1)}, {"X": "T"})
    assert out[0].confidence == "neutral"
