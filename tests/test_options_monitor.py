"""Tests for the option position monitor rules engine."""

from datetime import date, timedelta

from src.options.events import MarketEvent
from src.options.models import OptionPosition
from src.options.monitor import (
    ASSIGNMENT,
    EVENT,
    NO_DATA,
    STOP_LOSS,
    TAKE_PROFIT,
    TIME_STOP,
    all_alerts,
    monitor_position,
    monitor_positions,
)
from src.options.strategies import cash_secured_put, covered_call, long_call


def _position(strategy, net_debit, **kw):
    return OptionPosition(strategy=strategy, entry_net_debit=net_debit, **kw)


def _kinds(pm):
    return {a.kind for a in pm.alerts}


class TestMonitorRules:
    def test_take_profit_on_decayed_credit_put(self):
        # Credit put: collected $500. Far OTM at low vol near expiry -> mark ~ 0 -> big profit.
        exp = date.today() + timedelta(days=30)
        pos = _position(cash_secured_put("X", 100, exp), net_debit=-500.0)
        pm = monitor_position(pos, spot=130.0, rate=0.04, vol=0.20)
        assert TAKE_PROFIT in _kinds(pm)
        assert pm.pnl > 0

    def test_stop_loss_and_assignment_on_deep_itm_short_put(self):
        exp = date.today() + timedelta(days=30)
        pos = _position(cash_secured_put("X", 100, exp), net_debit=-500.0)
        pm = monitor_position(pos, spot=70.0, rate=0.04, vol=0.30)
        assert STOP_LOSS in _kinds(pm)
        assert ASSIGNMENT in _kinds(pm)
        assert pm.pnl < 0

    def test_time_stop_when_dte_low(self):
        exp = date.today() + timedelta(days=5)
        pos = _position(long_call("X", 100, exp), net_debit=300.0, close_by_dte=21)
        pm = monitor_position(pos, spot=100.0, rate=0.04, vol=0.30)
        assert TIME_STOP in _kinds(pm)

    def test_assignment_on_itm_short_call(self):
        exp = date.today() + timedelta(days=30)
        pos = _position(covered_call("X", 100, exp, shares=100), net_debit=-200.0)
        pm = monitor_position(pos, spot=120.0, rate=0.04, vol=0.30)
        assert ASSIGNMENT in _kinds(pm)

    def test_event_flag(self):
        exp = date.today() + timedelta(days=30)
        pos = _position(long_call("MU", 100, exp), net_debit=300.0)
        events = [MarketEvent(date.today() + timedelta(days=4), "FOMC", "market")]
        pm = monitor_position(pos, spot=100.0, rate=0.04, vol=0.30, events=events)
        assert EVENT in _kinds(pm)

    def test_no_data_when_missing_spot(self):
        exp = date.today() + timedelta(days=30)
        pos = _position(long_call("X", 100, exp), net_debit=300.0)
        pm = monitor_position(pos, spot=None, rate=0.04, vol=None)
        assert NO_DATA in _kinds(pm)


class TestMonitorAggregate:
    def test_monitor_positions_and_alert_sorting(self):
        exp = date.today() + timedelta(days=30)
        positions = [
            _position(cash_secured_put("X", 100, exp), net_debit=-500.0),   # take-profit at high spot
            _position(long_call("Y", 100, exp), net_debit=300.0),
        ]
        monitors = monitor_positions(
            positions, {"X": 130.0, "Y": 100.0}, 0.04, {"X": 0.20, "Y": 0.30}
        )
        assert len(monitors) == 2
        alerts = all_alerts(monitors)
        severities = [a.severity for a in alerts]
        # ACTION alerts sort before WARN/INFO.
        assert severities == sorted(severities, key=lambda s: {"ACTION": 0, "WARN": 1, "INFO": 2}[s])
