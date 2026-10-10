from __future__ import annotations

from datetime import datetime, timezone

from polyorderbooks_backtest.cli import strategy_from_yaml
from polyorderbooks_backtest.data import MemoryBookLookup, _normalize_winner, _pick_label
from polyorderbooks_backtest.strategy import (
    OrCondition,
    PriceCondition,
    Side,
    TimeCondition,
)


def _at(second: int) -> datetime:
    return datetime(2026, 8, 1, 0, 0, second, tzinfo=timezone.utc)


def test_yaml_strategy_parse() -> None:
    data = {
        "market": {"coin": "btc", "market_type": "15m"},
        "side": "Down",
        "budget_per_trade": 50,
        "sample_size": 30,
        "taker_fee": 0.07,
        "trade_lag_seconds": 1,
        "max_trades_per_market": 3,
        "entry": [
            {"type": "time", "after_seconds": 60},
            {
                "type": "price",
                "side": "Down",
                "comparator": "<=",
                "threshold": 0.4,
                "within_seconds": 300,
            },
            {
                "type": "or",
                "options": [
                    [
                        {
                            "type": "price",
                            "side": "Down",
                            "comparator": ">=",
                            "threshold": 0.6,
                        }
                    ]
                ],
            },
        ],
        "exit": {"take_profit": 0.3, "stop_loss": 0.6, "hold_to_expiry": False},
    }
    strategy = strategy_from_yaml(data)
    assert strategy.side is Side.DOWN
    assert strategy.market.slug_prefix() == "btc-updown-15m-"
    assert strategy.taker_fee == 0.07
    assert strategy.trade_lag_seconds == 1
    assert strategy.max_trades_per_market == 3
    assert strategy.sample_size == 30
    assert isinstance(strategy.entry.conditions[0], TimeCondition)
    assert isinstance(strategy.entry.conditions[1], PriceCondition)
    assert isinstance(strategy.entry.conditions[2], OrCondition)
    assert strategy.exit.take_profit == 0.3


def test_strategy_defaults() -> None:
    data = {
        "market": {"coin": "eth", "market_type": "5m"},
        "side": "up",
        "entry": [],
        "exit": {"hold_to_expiry": True},
    }
    strategy = strategy_from_yaml(data)
    assert strategy.budget_per_trade == 100
    assert strategy.sample_size == 20
    assert strategy.taker_fee == 0.0
    assert strategy.exit.hold_to_expiry


def test_pick_label() -> None:
    assert _pick_label(["Yes", "No"], Side.UP) == "Yes"
    assert _pick_label(["Up", "Down"], Side.DOWN) == "Down"
    assert _pick_label(["Other"], Side.UP) is None


def test_normalize_winner() -> None:
    assert _normalize_winner("yes") == "Up"
    assert _normalize_winner("NO") == "Down"
    assert _normalize_winner("Up") == "Up"
    assert _normalize_winner(None) is None


def test_memory_book_lookup_nearest_at_or_before() -> None:
    lookup = MemoryBookLookup(
        {
            "tok": [
                (_at(2), [[0.4]], [[0.6]]),
                (_at(5), [[0.45]], [[0.55]]),
            ]
        }
    )
    assert lookup.book_at("tok", _at(1)) is None
    assert lookup.book_at("tok", _at(3)).asks == [[0.6]]
    assert lookup.book_at("tok", _at(6)).bids == [[0.45]]
    assert lookup.book_at("missing", _at(3)) is None