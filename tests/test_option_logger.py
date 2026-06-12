"""Tests for option position/trade persistence and the event calendar."""

from datetime import date, timedelta

from src.options.events import MarketEvent, load_event_calendar, relevant_events
from src.options.strategies import bull_put_spread, cash_secured_put
from src.trade_log.option_logger import OptionTradeLogger

EXP = date.today() + timedelta(days=40)


def _logger(tmp_path):
    return OptionTradeLogger(tmp_path / "options_history.json", tmp_path / "options_positions.json")


class TestOptionTradeLogger:
    def test_log_open_persists_position_and_history(self, tmp_path):
        logger = _logger(tmp_path)
        strat = bull_put_spread("SMH", 580, 530, EXP)
        pos = logger.log_open(strat, net_debit=-553.0, rationale="boist put-sell", tags=["boist"])
        assert pos.status == "OPEN"
        assert pos.is_credit
        open_positions = logger.load_open_positions()
        assert len(open_positions) == 1
        assert open_positions[0].id == pos.id
        history = logger.load_history()
        assert len(history) == 1
        assert history[0].action == "OPEN"

    def test_round_trip_serialization(self, tmp_path):
        logger = _logger(tmp_path)
        logger.log_open(cash_secured_put("SNDK", 1500, EXP), net_debit=-2500.0)
        reloaded = _logger(tmp_path).load_open_positions()
        assert reloaded[0].strategy.legs[0].expiry == EXP

    def test_log_close_marks_closed(self, tmp_path):
        logger = _logger(tmp_path)
        pos = logger.log_open(cash_secured_put("SNDK", 1500, EXP), net_debit=-2500.0)
        closed = logger.log_close(pos.id, net_debit=-1000.0, rationale="50% profit")
        assert closed is not None
        assert closed.status == "CLOSED"
        assert logger.load_open_positions() == []
        actions = [t.action for t in logger.load_history()]
        assert actions == ["OPEN", "CLOSE"]

    def test_close_unknown_id_returns_none(self, tmp_path):
        logger = _logger(tmp_path)
        assert logger.log_close("nope", net_debit=0.0) is None


class TestEventCalendar:
    def test_load_and_filter(self, tmp_path):
        path = tmp_path / "events.yaml"
        path.write_text(
            "events:\n"
            f"  - date: {(date.today() + timedelta(days=3)).isoformat()}\n"
            "    label: FOMC\n"
            "    scope: market\n"
            f"  - date: {(date.today() + timedelta(days=5)).isoformat()}\n"
            "    label: MU earnings\n"
            "    scope: MU\n"
            f"  - date: {(date.today() + timedelta(days=60)).isoformat()}\n"
            "    label: Far away\n"
            "    scope: market\n",
            encoding="utf-8",
        )
        events = load_event_calendar(path)
        assert len(events) == 3

        smh = relevant_events(events, "SMH", horizon_days=14)
        assert [e.label for e in smh] == ["FOMC"]   # market event only, far one excluded

        mu = relevant_events(events, "MU", horizon_days=14)
        assert {e.label for e in mu} == {"FOMC", "MU earnings"}

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_event_calendar(tmp_path / "absent.yaml") == []
