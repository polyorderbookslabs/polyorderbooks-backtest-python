from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional, Sequence, Tuple

import pytest

from polyorderbooks_backtest.engine import (
    BacktestMarket,
    BookLookup,
    BookSnapshot,
    Fill,
    PriceSample,
    round_price,
    run,
)
from polyorderbooks_backtest.strategy import (
    Comparator,
    Entry,
    Exit,
    MarketSelector,
    PriceChangeCondition,
    PriceCondition,
    Side,
    Strategy,
    TimeCondition,
)


def dt(second: int) -> datetime:
    return datetime(2026, 8, 1, 0, 0, second, tzinfo=timezone.utc)


def market(winner: str = "Up", start_second: int = 0) -> BacktestMarket:
    return BacktestMarket(
        market_id="m1",
        source_market_id="sm1",
        slug="btc-updown-15m-test",
        winner=winner,
        start_at=dt(start_second),
    )


def prices(values: Sequence[float], label: str = "Up") -> list[PriceSample]:
    return [
        PriceSample(dt(second), "tok-up", label, value)
        for second, value in enumerate(values)
    ]


class LadderBook(BookLookup):
    def __init__(
        self, books: Dict[int, Tuple[Sequence[Sequence[float]], Sequence[Sequence[float]]]]
    ) -> None:
        self._books = books

    def book_at(self, token_id: Optional[str], at: datetime) -> Optional[BookSnapshot]:
        entry = self._books.get(at.second)
        if entry is None:
            return None
        bids, asks = entry
        return BookSnapshot(at, bids, asks)


def simple_strategy(
    entry: Entry = Entry((TimeCondition(0),)),
    exit_model: Exit = Exit(hold_to_expiry=True),
    *,
    fee: float = 0.0,
    lag: int = 0,
    max_trades: int = 0,
    budget: float = 100.0,
) -> Strategy:
    return Strategy(
        market=MarketSelector(coin="btc", market_type="15m"),
        side=Side.UP,
        entry=entry,
        exit=exit_model,
        budget_per_trade=budget,
        sample_size=10,
        taker_fee=fee,
        trade_lag_seconds=lag,
        max_trades_per_market=max_trades,
    )


def test_not_enough_history() -> None:
    result = run(simple_strategy(), market(), prices([0.5]), LadderBook({}))
    assert not result.traded_any
    assert result.skip_reason == "not enough Up price history (1 rows)"


def test_no_samples_after_market_start() -> None:
    series = prices([0.5, 0.5])
    result = run(simple_strategy(Entry((TimeCondition(0),))), market(start_second=10), series, LadderBook({}))
    assert not result.traded_any
    assert result.skip_reason == "first Up snapshot after market start is missing"


def test_buy_ladder_sell_at_window_end() -> None:
    series = prices([0.5, 0.55, 0.6, 0.65, 0.7])
    books = LadderBook(
        {
            0: ([], [[0.5, 1_000_000.0]]),
            4: ([[0.7, 1_000_000.0]], []),
        }
    )
    result = run(simple_strategy(exit_model=Exit()), market(), series, books)
    assert result.traded_any
    trade = result.trade
    assert trade is not None
    assert trade.reason == "end_of_window"
    assert trade.entry_price == 0.5
    assert trade.exit_price == 0.7
    assert len(result.trades) == 1


def test_take_profit_with_fees() -> None:
    series = prices([0.5, 0.6, 0.7, 0.8, 0.9])
    books = LadderBook(
        {
            0: ([], [[0.5, 1_000_000.0]]),
            3: ([[0.8, 1_000_000.0]], []),
        }
    )
    strategy = simple_strategy(
        exit_model=Exit(take_profit=0.75, stop_loss=0.2),
        fee=0.07,
    )
    result = run(strategy, market(), series, books)
    assert result.traded_any
    trade = result.trade
    assert trade is not None
    assert trade.reason == "take_profit"
    assert trade.entry_price == 0.5
    assert trade.exit_price == 0.8

    entry_eff = 0.5 * (1 + 0.07 * 0.5 * 0.5)
    shares = 100.0 / entry_eff
    exit_eff = 0.8 * (1 - 0.07 * 0.8 * 0.2)
    expected_pnl = (exit_eff - entry_eff) * shares
    entry_fee = 200.0 * 0.5 * 0.07 * 0.5 * (1 - 0.5)
    exit_fee = shares * 0.8 * 0.07 * 0.8 * (1 - 0.8)

    assert trade.pnl == pytest.approx(round_price(expected_pnl))
    assert trade.fees == pytest.approx(round_price(entry_fee + exit_fee))
    assert trade.shares == pytest.approx(round_price(shares))
    assert trade.entry_fill_source == "book_ladder"
    assert not trade.partial_fill


def test_trailing_stop() -> None:
    series = prices([0.5, 0.6, 0.9, 0.85, 0.8])
    books = LadderBook(
        {
            0: ([], [[0.5, 1_000_000.0]]),
            3: ([[0.85, 1_000_000.0]], []),
        }
    )
    strategy = simple_strategy(
        exit_model=Exit(trailing_stop_distance=0.05, trailing_stop_activation=0.70)
    )
    result = run(strategy, market(), series, books)
    assert result.traded_any
    trade = result.trade
    assert trade is not None
    assert trade.reason == "trailing_stop"
    assert trade.exit_price == 0.85


def test_hold_to_expiry_win_and_loss() -> None:
    series = prices([0.5, 0.5, 0.5, 0.5, 0.5])
    books = LadderBook({0: ([], [[0.5, 1_000_000.0]])})
    strategy = simple_strategy(exit_model=Exit(hold_to_expiry=True))
    win = run(strategy, market(winner="Up"), series, books)
    assert win.traded_any
    assert win.trade is not None
    assert win.trade.reason == "expiry"
    assert win.trade.exit_price == 1.0

    lose = run(strategy, market(winner="Down"), series, books)
    assert lose.traded_any
    assert lose.trade is not None
    assert lose.trade.exit_price == 0.0


def test_trade_lag_fills_later_sample() -> None:
    series = prices([0.5, 0.6, 0.7, 0.8, 0.9])
    books = LadderBook({1: ([], [[0.6, 1_000_000.0]])})
    strategy = simple_strategy(exit_model=Exit(hold_to_expiry=True), lag=1)
    result = run(strategy, market(), series, books)
    assert result.traded_any
    assert result.trade is not None
    assert result.trade.entry_price == 0.6


def test_reentry_capped_by_max_trades() -> None:
    series = prices([0.5, 0.56, 0.52, 0.54, 0.60, 0.56])
    books = LadderBook(
        {
            0: ([], [[0.5, 1_000_000.0]]),
            2: ([[0.52, 1_000_000.0]], []),
            3: ([], [[0.54, 1_000_000.0]]),
            5: ([[0.56, 1_000_000.0]], []),
        }
    )
    strategy = simple_strategy(
        exit_model=Exit(trailing_stop_distance=0.02, trailing_stop_activation=0.05),
        max_trades=2,
    )
    result = run(strategy, market(), series, books)
    assert len(result.trades) == 2
    assert all(trade.reason == "trailing_stop" for trade in result.trades)


def test_price_change_entry() -> None:
    series = prices([0.5, 0.5, 0.55, 0.60, 0.65])
    books = LadderBook({3: ([], [[0.60, 1_000_000.0]])})
    strategy = simple_strategy(
        Entry((PriceChangeCondition(Side.UP, Comparator.GE, 0.10, 3),)),
        Exit(hold_to_expiry=True),
    )
    result = run(strategy, market(), series, books)
    assert result.traded_any
    assert result.trade is not None
    assert result.trade.entry_price == 0.6


def test_price_condition_within_seconds() -> None:
    series = prices([0.5, 0.55, 0.6, 0.65])
    books = LadderBook({0: ([], [[0.5, 1_000_000.0]])})
    strategy = simple_strategy(
        Entry((PriceCondition(Side.UP, Comparator.LT, 0.7, within_seconds=10),)),
        Exit(hold_to_expiry=True),
    )
    result = run(strategy, market(), series, books)
    assert result.traded_any
    assert result.trade is not None
    assert result.trade.entry_price == 0.5


def test_exit_before_close() -> None:
    series = prices([0.5, 0.52, 0.54, 0.56, 0.58])
    books = LadderBook(
        {
            0: ([], [[0.5, 1_000_000.0]]),
            2: ([[0.54, 1_000_000.0]], []),
        }
    )
    strategy = simple_strategy(exit_model=Exit(exit_before_close_seconds=2))
    result = run(strategy, market(), series, books)
    assert result.traded_any
    assert result.trade is not None
    assert result.trade.reason == "exit_before_close"
    assert result.trade.exit_price == 0.54


def test_fills_at_last_price_when_no_ladder() -> None:
    series = prices([0.5, 0.5, 0.5, 0.5])
    strategy = simple_strategy()
    result = run(strategy, market(), series, LadderBook({}))
    assert result.traded_any
    trade = result.trade
    assert trade is not None
    assert trade.entry_fill_source == "last_price"
    assert trade.partial_fill