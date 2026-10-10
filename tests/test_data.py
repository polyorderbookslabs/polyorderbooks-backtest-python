from __future__ import annotations

from datetime import datetime, timezone

from polyorderbooks_backtest.data import MarketBacktester
from polyorderbooks_backtest.strategy import (
    Entry,
    Exit,
    MarketSelector,
    Coin,
    Side,
    Strategy,
    TimeCondition,
)

_TS = "2026-08-01T00:02:00Z"
_START = "2026-08-01T00:00:00Z"
_END = "2026-08-01T01:00:00Z"


class _StubClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.detail_winner = "yes"
        self.detail_end_date = _END

    def get_market_prices(self, id_or_slug: str, **params):
        self.calls.append(params.get("resolution", ""))
        if params.get("resolution", "") in ("1s", "60s", "1m"):
            return {"data": {}, "tokens": None}
        return {
            "data": {"Yes": [{"t": _TS, "p": 0.52}, {"t": "2026-08-01T00:04:00Z", "p": 0.54}]},
            "tokens": {"Yes": "tok-a"},
        }

    def get_market_books(self, id_or_slug: str, **params):
        if params.get("resolution", "") in ("1s", "60s", "1m"):
            return {"data": {}, "tokens": None}
        return {
            "data": {
                "Yes": [{"t": _TS, "bids": [], "asks": [[0.52, 1000]]}]
            },
            "tokens": {"Yes": "tok-a"},
        }

    def get_market(self, id_or_slug: str):
        return {"winner": self.detail_winner, "end_date": self.detail_end_date}


def _strategy() -> Strategy:
    return Strategy(
        market=MarketSelector(coin=Coin.BTC, market_type="15m"),
        side=Side.UP,
        entry=Entry((TimeCondition(after_seconds=60),)),
        exit=Exit(hold_to_expiry=True),
        budget_per_trade=100.0,
        sample_size=20,
    )


def _run() -> None:
    market = {
        "public_id": "pub-1",
        "id": "id-1",
        "slug": "btc-updown-15m-1",
        "winner": None,
        "start_date": _START,
        "end_date": _END,
    }
    client = _StubClient()
    result = MarketBacktester(client).run_market(market, _strategy())
    assert client.calls[0] == "1s"
    assert "5m" in client.calls
    assert result.traded_any
    trade = result.trade
    assert trade is not None
    assert trade.reason == "expiry"
    assert trade.entry_fill_source == "book_ladder"
    assert trade.entry_price == 0.52
    assert trade.exit_price == 1.0
    assert trade.exited_at == datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)


def test_run_market_aliases_winner_books_and_exit_end() -> None:
    _run()


def test_run_market_when_detail_winner_missing_still_runs() -> None:
    client = _StubClient()
    client.detail_winner = None
    market = {
        "slug": "btc-updown-15m-1",
        "winner": None,
        "start_date": _START,
        "end_date": _END,
    }
    result = MarketBacktester(client).run_market(market, _strategy())
    assert result.traded_any