from __future__ import annotations

from datetime import datetime, timezone

import pytest

from polyorderbooks_backtest.engine import TradeResult
from polyorderbooks_backtest.metrics import summarize


def trade(pnl: float, return_pct: float, fees: float = 0.0) -> TradeResult:
    return TradeResult(
        entered_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        exited_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        entry_price=0.5,
        exit_price=0.6,
        shares=100.0,
        pnl=pnl,
        return_pct=return_pct,
        fees=fees,
        reason="test",
        entry_fill_source="book_ladder",
    )


def test_empty_summary_zeros() -> None:
    summary = summarize([])
    assert summary["num_trades"] == 0
    assert summary["net_pnl"] == 0


def test_wins_and_net() -> None:
    summary = summarize([trade(3.0, 0.03, fees=0.5), trade(-2.0, -0.02)])
    assert summary["num_trades"] == 2
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["win_rate"] == 0.5
    assert summary["gross_pnl"] == 1.0
    assert summary["fees"] == 0.5
    assert summary["net_pnl"] == 0.5


def test_single_trade_sharpe_zero() -> None:
    assert summarize([trade(1.0, 0.01)])["sharpe"] == 0


def test_max_drawdown() -> None:
    summary = summarize(
        [
            trade(10.0, 0.10),
            trade(-8.0, -0.08),
            trade(4.0, 0.04),
        ]
    )
    assert summary["max_drawdown"] == pytest.approx(0.8)


def test_best_and_worst() -> None:
    summary = summarize([trade(5.0, 0.05), trade(-3.0, -0.03), trade(2.0, 0.02)])
    assert summary["best_trade"] == 5.0
    assert summary["worst_trade"] == -3.0