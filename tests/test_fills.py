from __future__ import annotations

import pytest

from polyorderbooks_backtest.engine import execute_buy, execute_sell


def test_buy_walks_ladder_to_budget() -> None:
    fill = execute_buy([[0.5, 10.0], [0.6, 20.0]], None, None, 10.0)
    assert fill is not None
    assert fill.partial is False
    assert fill.source == "book_ladder"
    assert fill.shares == pytest.approx(18.333333333333334)
    assert fill.price == pytest.approx(0.5454545454545454)
    assert not fill.partial


def test_buy_falls_back_when_ladder_too_thin() -> None:
    fill = execute_buy([[0.5, 2.0]], None, 0.7, 10.0)
    assert fill is not None
    assert fill.partial is True
    assert fill.source == "book_ladder"
    assert fill.price == pytest.approx(10.0 / 14.857142857142858)


def test_buy_uses_quote_with_empty_ladder() -> None:
    fill = execute_buy([], 0.6, 0.7, 5.0)
    assert fill is not None
    assert fill.partial is True
    assert fill.source == "best_quote"
    assert fill.price == pytest.approx(0.6)


def test_buy_with_no_depth_and_no_quote_is_none() -> None:
    assert execute_buy([], None, None, 5.0) is None


def test_sell_walks_bids() -> None:
    fill = execute_sell([[0.5, 10.0], [0.4, 20.0]], None, None, 15.0)
    assert fill is not None
    assert not fill.partial
    assert fill.source == "book_ladder"
    assert fill.price == pytest.approx(7.0 / 15.0)


def test_sell_falls_back_beyond_depth() -> None:
    fill = execute_sell([[0.5, 10.0], [0.4, 20.0]], None, 0.3, 50.0)
    assert fill is not None
    assert fill.partial
    assert fill.price == pytest.approx(19.0 / 50.0)


def test_sell_with_no_bids_and_no_quote_is_none() -> None:
    assert execute_sell([], None, None, 10.0) is None