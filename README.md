# PolyOrderbooks Backtest

[![PyPI version](https://img.shields.io/pypi/v/polyorderbooks-backtest.svg)](https://pypi.org/project/polyorderbooks-backtest/)
[![Python Versions](https://img.shields.io/pypi/pyversions/polyorderbooks-backtest)](https://pypi.org/project/polyorderbooks-backtest/)
[![CI](https://github.com/polyorderbookslabs/polyorderbooks-backtest-python/actions/workflows/ci.yml/badge.svg)](https://github.com/polyorderbookslabs/polyorderbooks-backtest-python/actions/workflows/ci.yml)

Replay-accurate backtesting for Polymarket order-book history, built on the
[PolyOrderbooks API](https://polyorderbooks.com).

Where most Polymarket backtesters approximate fills because they only have
prices, this library replays real **L2 order books** — a buy walks the visible
ask ladder and a sell walks the bid ladder, so the numbers include the spread
and depth costs you would actually pay. Fills, fees, entry/exit rules and
settlement mirror the engine behind the production backtest service, open and
scriptable.

## The data

Polymarket crypto markets: up/down rounds (5m, 15m, 4h), price thresholds, and
related event markets. Order books are captured at 250ms resolution. Every plan
can query down to `250ms`, including the free tier — plans differ on history
window and request volume (see [pricing](https://polyorderbooks.com/pricing)).

### Resolution and coverage

You request a `resolution` (default `1s`); if that market has no rows at the
requested granularity the loader falls back to coarser buckets
(`60s` → `1m` → `5m` → `10m` → `1h`) so a run still produces results. Outcome
tokens surfacing as `Yes`/`No` are treated as aliases of `Up`/`Down` for the
side you trade. Free-tier history windows are limited; pass `--start`/`--end`
inside the covered window or use a paid plan for longer horizons.

## Install

```bash
pip install polyorderbooks-backtest
```

This installs the
[`polyorderbooks`](https://pypi.org/project/polyorderbooks/) SDK as a dependency.

## Quickstart

Get an API key from [polyorderbooks.com/signup](https://polyorderbooks.com/signup), then define a strategy in YAML:

```yaml
market:
  coin: btc
  market_type: "15m"
side: Up
budget_per_trade: 100
sample_size: 20
taker_fee: 0.07
trade_lag_seconds: 1
max_trades_per_market: 5
entry:
  - type: time
    after_seconds: 60
  - type: price_change
    side: Up
    comparator: ">="
    percent: 0.05
    over_seconds: 60
exit:
  take_profit: 0.85
  stop_loss: 0.15
  trailing_stop_distance: 0.05
  trailing_stop_activation: 0.70
```

Replay it over a sample of recent resolved markets:

```bash
export POLYORDERBOOKS_API_KEY="pob_..."
polyorderbooks-backtest strategy.yaml --top 20 --out trades.csv
```

Output includes a per-market pass/fail list and a summarized scorecard (trades,
win rate, gross/net PnL, fees, average return, Sharpe, max drawdown), plus an
optional CSV of every fill. Run a single market instead:

```bash
polyorderbooks-backtest strategy.yaml --market btc-updown-15m-2026-08-01-1200
```

## Python API

```python
from polyorderbooks import PolyOrderbooksClient
from polyorderbooks_backtest import MarketBacktester
from polyorderbooks_backtest.metrics import summarize

client = PolyOrderbooksClient()
backtester = MarketBacktester(client, resolution="1s", book_resolution="250ms")
result = backtester.run("btc-updown-15m-2026-08-01-1200", strategy)

print(summarize(result.trades))
for trade in result.trades:
    print(trade.entered_at, trade.exit_price, trade.reason, trade.pnl)
```

`MarketResult.trades` is a list of `TradeResult`; every one carries `entered_at`
/ `exited_at`, `entry_price` / `exit_price` (actual effective fills), `shares`,
`pnl`, `return_pct`, `fees`, the exit `reason` (`take_profit`, `stop_loss`,
`trailing_stop`, `max_hold`, `exit_before_close`, `expiry`, `end_of_window`),
the fill `source` (`book_ladder`, `best_quote`, `last_price`, `settlement`), and
a `partial_fill` flag.

## How fills are simulated

- **Entry** spends `budget_per_trade` walking the ask ladder in price order.
  If the visible ladder cannot absorb the budget, the remainder fills at the
  best ask (or last price) and the trade is flagged `partial`.
- **Exit** liquidates the position walking the bid ladder; a missing exit book
  settles at `$1`/`$0` against the resolved winner.
- **Fees** apply the Polymarket crypto CLOB taker-fee curve
  `C × 0.07 × p × (1 − p)` on each fill when `taker_fee` is set.
- **Lag** (`trade_lag_seconds`) fills `N` ticks after a condition triggers, to
  model execution latency instead of assuming you got the trigger price.
- **Point-in-time:** every sample uses only price and book data observed at
  that timestamp, so there is no lookahead.
- **Settlement** for `hold_to_expiry` pays out `$1.00` if the side won,
  `$0.00` otherwise, against the market's recorded winner, at the market's
  scheduled close — so truncated price history never distorts a hold trade.

## Strategy reference

### Entry conditions (ANDed together; nest `or` groups for OR)

| Type | Fields | Meaning |
| --- | --- | --- |
| `price` | `side`, `comparator`, `threshold`, `within_seconds?` | side token price compares against threshold, optionally only within `within_seconds` of market start |
| `price_change` | `side`, `comparator`, `percent`, `over_seconds` | side token has moved at least `percent` (signed, e.g. `0.05` = up 5%) over the trailing `over_seconds` |
| `time` | `after_seconds` | market has been live for at least `after_seconds` |
| `or` | `options` | group-of-groups: any one option (a list of ANDed conditions) satisfied |

Comparators: `<`, `>`, `<=`, `>=`.

### Exit (first wins)

| Field | Meaning |
| --- | --- |
| `take_profit` | sell when the held token price reaches this level |
| `stop_loss` | sell when the held token price falls to this level |
| `trailing_stop_distance` | sell when price retraces this much from its high-water mark |
| `trailing_stop_activation` | only start trailing once price reaches this level |
| `max_hold_seconds` | force sell after a fixed hold duration |
| `exit_before_close_seconds` | sell once the window is within this many seconds of its end |
| `hold_to_expiry` | hold to settlement and pay `$1`/`$0` against the winner |

### Global fields

| Field | Default | Meaning |
| --- | --- | --- |
| `budget_per_trade` | `100` | capital deployed per entry |
| `sample_size` | `20` | how many resolved markets to sample when no `--market` is given |
| `taker_fee` | `0.0` | fee curve multiplier; `0.07` matches the crypto CLOB |
| `trade_lag_seconds` | `0` | ticks to wait between trigger and fill |
| `max_trades_per_market` | `0` | cap on round-trips per market (`0` = unlimited) |

## CLI reference

```
polyorderbooks-backtest strategy.yaml [--top N] [--market SLUG] [--out trades.csv]
                                      [--start ISO] [--end ISO] [--resolution 1s]
                                      [--book-resolution 250ms] [--api-key pob_...]
```

- `--top` samples up to N resolved markets matching the strategy's coin/type
  prefix (default 20).
- `--book-resolution` can be finer than `--resolution` for realistic depth at
  entry/exit while keeping prices at a coarser bucket.
- The free tier covers ~3 days of history — run `--start`/`--end` inside that
  window, or use a paid plan for longer horizons.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Links

- [Python SDK on PyPI](https://pypi.org/project/polyorderbooks/) — `pip install polyorderbooks`
- [Runnable strategy cookbook](https://github.com/polyorderbookslabs/backtest-strategies)
- [Docs](https://docs.polyorderbooks.com)
- [OpenAPI](https://docs.polyorderbooks.com/openapi.json)
- [Free datasets](https://polyorderbooks.com/datasets)

## License

MIT — see [LICENSE](LICENSE). Not affiliated with, endorsed by, or connected to Polymarket.