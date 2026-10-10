from __future__ import annotations

import csv
import sys
from argparse import ArgumentParser
from typing import List, Optional

import yaml

from polyorderbooks import PolyOrderbooksClient

from polyorderbooks_backtest.data import MarketBacktester, discover_markets
from polyorderbooks_backtest.engine import MarketResult
from polyorderbooks_backtest.metrics import summarize
from polyorderbooks_backtest.strategy import (
    Coin,
    Comparator,
    Entry,
    EntryCondition,
    Exit,
    MarketSelector,
    OrCondition,
    PriceChangeCondition,
    PriceCondition,
    Side,
    Strategy,
    TimeCondition,
)


def _opt_float(value: object) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _opt_int(value: object) -> Optional[int]:
    if value is None:
        return None
    return int(value)


def _condition(node: dict) -> EntryCondition:
    ctype = node["type"]
    if ctype == "price":
        return PriceCondition(
            side=Side(str(node["side"]).capitalize()),
            comparator=Comparator(str(node["comparator"])),
            threshold=float(node["threshold"]),
            within_seconds=_opt_int(node.get("within_seconds")),
        )
    if ctype == "price_change":
        return PriceChangeCondition(
            side=Side(str(node["side"]).capitalize()),
            comparator=Comparator(str(node["comparator"])),
            percent=float(node["percent"]),
            over_seconds=int(node["over_seconds"]),
        )
    if ctype == "time":
        return TimeCondition(after_seconds=int(node["after_seconds"]))
    if ctype == "or":
        options = [[_condition(item) for item in option] for option in node["options"]]
        return OrCondition(options)
    raise ValueError(f"unknown condition type: {ctype}")


def strategy_from_yaml(data: dict) -> Strategy:
    market = data["market"]
    exit_data = data.get("exit") or {}
    return Strategy(
        market=MarketSelector(coin=Coin(str(market["coin"]).lower()), market_type=str(market["market_type"])),
        side=Side(str(data["side"]).capitalize()),
        entry=Entry([_condition(node) for node in data.get("entry", [])]),
        exit=Exit(
            take_profit=_opt_float(exit_data.get("take_profit")),
            stop_loss=_opt_float(exit_data.get("stop_loss")),
            hold_to_expiry=bool(exit_data.get("hold_to_expiry", False)),
            trailing_stop_distance=_opt_float(exit_data.get("trailing_stop_distance")),
            trailing_stop_activation=_opt_float(exit_data.get("trailing_stop_activation")),
            max_hold_seconds=_opt_int(exit_data.get("max_hold_seconds")),
            exit_before_close_seconds=_opt_int(exit_data.get("exit_before_close_seconds")),
        ),
        budget_per_trade=float(data.get("budget_per_trade", 100)),
        sample_size=int(data.get("sample_size", 20)),
        taker_fee=float(data.get("taker_fee", 0.0)),
        trade_lag_seconds=int(data.get("trade_lag_seconds", 0)),
        max_trades_per_market=int(data.get("max_trades_per_market", 0)),
    )


def _write_csv(path: str, rows: List[dict], market_slug: str) -> None:
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "market",
                "entered_at",
                "exited_at",
                "entry_price",
                "exit_price",
                "shares",
                "pnl",
                "return_pct",
                "fees",
                "reason",
                "fill_source",
                "partial",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    market_slug,
                    row.entered_at.isoformat(),
                    row.exited_at.isoformat(),
                    row.entry_price,
                    row.exit_price,
                    row.shares,
                    row.pnl,
                    row.return_pct,
                    row.fees,
                    row.reason,
                    row.entry_fill_source,
                    row.partial_fill,
                ]
            )


def _run_one(
    backtester: MarketBacktester,
    market: dict,
    strategy: Strategy,
    start_ts: Optional[str],
    end_ts: Optional[str],
) -> MarketResult:
    return backtester.run_market(
        market, strategy, start_ts=start_ts, end_ts=end_ts
    )


def _print_summary(results: List[dict]) -> None:
    all_trades = [trade for market in results for trade in market["trades"]]
    traded = [market for market in results if market["trades"]]
    skipped = [market for market in results if not market["trades"]]

    print()
    print("Markets replayed: {}".format(len(results)))
    print("Traded: {}   Skipped: {}".format(len(traded), len(skipped)))
    for market in skipped:
        reason = market["result"].skip_reason or "no trades"
        print("  - {}: {}".format(market["slug"], reason))
    print()
    summary = summarize(all_trades)
    for key, value in summary.items():
        print("{:>12}: {}".format(key, value))


def _parser() -> ArgumentParser:
    parser = ArgumentParser(
        prog="polyorderbooks-backtest",
        description="Replay a YAML strategy over Polymarket order-book history and report the results.",
    )
    parser.add_argument("strategy", help="path to a strategy YAML file")
    parser.add_argument("--market", help="a specific market id or slug to replay")
    parser.add_argument("--start", help="start ISO timestamp (default: market start)")
    parser.add_argument("--end", help="end ISO timestamp (default: market end)")
    parser.add_argument("--resolution", default="1s", help="price resolution (default: 1s)")
    parser.add_argument("--book-resolution", default=None, help="book resolution (default: same as --resolution)")
    parser.add_argument("--api-key", default=None, help="PolyOrderbooks API key (default: POLYORDERBOOKS_API_KEY)")
    parser.add_argument("--top", type=int, default=20, help="sample up to N resolved markets (default: 20)")
    parser.add_argument("--out", default=None, help="write all trades to this CSV file")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    with open(args.strategy, "r", encoding="utf-8") as handle:
        strategy = strategy_from_yaml(yaml.safe_load(handle))

    client = PolyOrderbooksClient(api_key=args.api_key)
    backtester = MarketBacktester(
        client,
        resolution=args.resolution,
        book_resolution=args.book_resolution,
    )

    if args.market:
        market = client.get_market(args.market)
        if not market.get("is_resolved") and not market.get("winner"):
            print("warning: market {} is not resolved; results treat it as unresolved".format(args.market))
        result = _run_one(backtester, market, strategy, args.start, args.end)
        results = [
            {
                "slug": market.get("slug"),
                "trades": list(result.trades),
                "result": result,
            }
        ]
    else:
        markets = discover_markets(client, strategy, max_markets=args.top)
        if not markets:
            print(
                "no resolved {} markets found; widen the window or check the strategy market prefix".format(
                    strategy.market.slug_prefix()
                )
            )
            return 1
        print("Found {} resolved markets".format(len(markets)))
        results = []
        for market in markets:
            result = _run_one(backtester, market, strategy, args.start, args.end)
            results.append(
                {
                    "slug": market.get("slug"),
                    "trades": list(result.trades),
                    "result": result,
                }
            )

    all_trades = [trade for market in results for trade in market["trades"]]
    if args.out and all_trades:
        _write_csv(
            args.out,
            all_trades,
            market_slug=results[0]["slug"] if len(results) == 1 else "",
        )
        print("Wrote {} trades to {}".format(len(all_trades), args.out))

    _print_summary(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())