from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from polyorderbooks_backtest.strategy import (
    EntryCondition,
    OrCondition,
    PriceChangeCondition,
    PriceCondition,
    Side,
    Strategy,
    TimeCondition,
)

SETTLEMENT_WIN_VALUE = 1.0
SETTLEMENT_LOSS_VALUE = 0.0
MIN_FILL_NOTIONAL = 0.0001
CRYPTO_FEE_RATE = 0.07


@dataclass(frozen=True)
class PriceSample:
    captured_at: datetime
    token_id: Optional[str]
    outcome_label: Optional[str]
    price: Optional[float]
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None


@dataclass(frozen=True)
class BacktestMarket:
    market_id: Optional[str]
    source_market_id: Optional[str]
    slug: str
    winner: Optional[str]
    start_at: datetime
    end_at: Optional[datetime] = None


@dataclass(frozen=True)
class BookSnapshot:
    captured_at: datetime
    bids: Sequence[Sequence[float]]
    asks: Sequence[Sequence[float]]


class BookLookup:
    def book_at(self, token_id: Optional[str], at: datetime) -> Optional[BookSnapshot]:
        raise NotImplementedError


@dataclass(frozen=True)
class Fill:
    price: float
    shares: float
    partial: bool = False
    source: str = "book_ladder"

    @classmethod
    def settlement(cls, price: float) -> "Fill":
        return cls(price, 0.0, False, "settlement")


@dataclass(frozen=True)
class TradeResult:
    entered_at: datetime
    exited_at: datetime
    entry_price: float
    exit_price: float
    shares: float
    pnl: float
    return_pct: float
    fees: float
    reason: str
    entry_fill_source: str
    partial_fill: bool = False


@dataclass(frozen=True)
class MarketResult:
    trades: Sequence[TradeResult]
    skip_reason: Optional[str] = None

    @classmethod
    def skipped(cls, reason: str) -> "MarketResult":
        return cls((), reason)

    @classmethod
    def traded(cls, trades: Sequence[TradeResult]) -> "MarketResult":
        return cls(trades, None)

    @property
    def traded_any(self) -> bool:
        return bool(self.trades)

    @property
    def trade(self) -> Optional[TradeResult]:
        return self.trades[0] if self.trades else None


def round_price(value: float) -> float:
    return round(value * 1_000_000.0) / 1_000_000.0


def effective_price(raw: float, fee_rate: float) -> float:
    return raw * (1.0 + fee_rate * raw * (1.0 - raw))


def fill_sample(
    series: Sequence[PriceSample], index: int, lag_seconds: int
) -> PriceSample:
    requested = index + max(lag_seconds, 0)
    return series[min(requested, len(series) - 1)]


def execute_buy(
    asks: Sequence[Sequence[float]],
    quote: Optional[float],
    last: Optional[float],
    budget: float,
) -> Optional[Fill]:
    remaining = budget
    spent = 0.0
    shares = 0.0
    for level in asks:
        price = level[0]
        size = level[1]
        if price <= 0 or size <= 0:
            continue
        take = min(size, remaining / price)
        if take * price <= MIN_FILL_NOTIONAL:
            break
        shares += take
        spent += take * price
        remaining -= take * price

    partial = False
    source = "book_ladder"
    if remaining > MIN_FILL_NOTIONAL:
        fallback = quote if quote is not None else last
        if fallback is None or not (fallback > 0):
            if shares > 0 and spent > 0:
                return Fill(spent / shares, shares, True, source)
            return None
        shares += remaining / fallback
        spent += remaining
        remaining = 0.0
        partial = True
    if shares <= 0 or spent <= 0:
        return None
    if not asks:
        source = "best_quote" if quote is not None else "last_price"
    return Fill(spent / shares, shares, partial, source)


def execute_sell(
    bids: Sequence[Sequence[float]],
    quote: Optional[float],
    last: Optional[float],
    owned: float,
) -> Optional[Fill]:
    remaining = owned
    proceeds = 0.0
    sold = 0.0
    for level in bids:
        price = level[0]
        size = level[1]
        if price <= 0 or size <= 0:
            continue
        take = min(size, remaining)
        if take * price <= MIN_FILL_NOTIONAL:
            break
        sold += take
        proceeds += take * price
        remaining -= take

    partial = False
    source = "book_ladder"
    if remaining > MIN_FILL_NOTIONAL:
        fallback = quote if quote is not None else last
        if fallback is None or not (fallback > 0):
            if sold > 0 and proceeds > 0:
                return Fill(proceeds / sold, sold, True, source)
            return None
        sold += remaining
        proceeds += remaining * fallback
        remaining = 0.0
        partial = True
    if sold <= 0 or proceeds <= 0:
        return None
    if not bids:
        source = "best_quote" if quote is not None else "last_price"
    return Fill(proceeds / sold, sold, partial, source)


def _best(value: Optional[float]) -> Optional[float]:
    if value is not None and value > 0:
        return value
    return None


def fill_buy(
    budget: float, token_id: Optional[str], sample: PriceSample, books: BookLookup
) -> Optional[Fill]:
    book = books.book_at(token_id, sample.captured_at)
    asks = book.asks if book is not None else ()
    return execute_buy(asks, _best(sample.best_ask), sample.price, budget)


def fill_sell(
    token_id: Optional[str], owned_shares: float, sample: PriceSample, books: BookLookup
) -> Optional[Fill]:
    book = books.book_at(token_id, sample.captured_at)
    bids = book.bids if book is not None else ()
    return execute_sell(bids, _best(sample.best_bid), sample.price, owned_shares)


def _price_for_settlement(market: BacktestMarket, side: Side) -> float:
    return SETTLEMENT_WIN_VALUE if side.won(market.winner) else SETTLEMENT_LOSS_VALUE


def _entry_token_series(
    prices: Sequence[PriceSample], side: Side
) -> Sequence[PriceSample]:
    wanted = side.label.lower()
    return sorted(
        (
            sample
            for sample in prices
            if sample.outcome_label is not None
            and sample.outcome_label.strip().lower() == wanted
        ),
        key=lambda sample: sample.captured_at,
    )


def _collect_changes(
    conditions: Sequence[EntryCondition], out: list[PriceChangeCondition]
) -> None:
    for condition in conditions:
        if isinstance(condition, PriceChangeCondition):
            out.append(condition)
        elif isinstance(condition, OrCondition):
            for option in condition.options:
                _collect_changes(option, out)


def _change_conditions(
    conditions: Sequence[EntryCondition],
) -> list[PriceChangeCondition]:
    out: list[PriceChangeCondition] = []
    _collect_changes(conditions, out)
    return out


def _advance_reference(
    series: Sequence[PriceSample], i: int, ref: int, over_seconds: int
) -> int:
    target = series[i].captured_at.timestamp() - over_seconds
    j = max(ref, 0)
    if j >= i:
        return -1
    if series[j].captured_at.timestamp() > target:
        return -1
    while j + 1 < i and series[j + 1].captured_at.timestamp() <= target:
        j += 1
    return j


def _holds(
    condition: EntryCondition,
    series: Sequence[PriceSample],
    i: int,
    price: float,
    elapsed: int,
    changes: list[PriceChangeCondition],
    change_ref: list[int],
) -> bool:
    if isinstance(condition, PriceCondition):
        if condition.within_seconds is not None and elapsed > condition.within_seconds:
            return False
        return condition.comparator.test(price, condition.threshold)
    if isinstance(condition, PriceChangeCondition):
        idx = changes.index(condition)
        if idx < 0 or change_ref[idx] < 0:
            return False
        ref_price = series[change_ref[idx]].price
        if ref_price is None or ref_price == 0:
            return False
        change_pct = (price - ref_price) / ref_price
        return condition.comparator.test(change_pct, condition.percent)
    if isinstance(condition, TimeCondition):
        return elapsed >= condition.after_seconds
    if isinstance(condition, OrCondition):
        for option in condition.options:
            if _all_hold(option, series, i, price, elapsed, changes, change_ref):
                return True
        return False
    return False


def _all_hold(
    conditions: Sequence[EntryCondition],
    series: Sequence[PriceSample],
    i: int,
    price: float,
    elapsed: int,
    changes: list[PriceChangeCondition],
    change_ref: list[int],
) -> bool:
    for condition in conditions:
        if not _holds(condition, series, i, price, elapsed, changes, change_ref):
            return False
    return True


def _find_entry_index(
    strategy: Strategy,
    market: BacktestMarket,
    series: Sequence[PriceSample],
    from_index: int,
) -> Optional[int]:
    conditions = list(strategy.entry.conditions)
    changes = _change_conditions(conditions)
    change_ref = [-1] * len(changes)
    for i in range(from_index, len(series)):
        sample = series[i]
        if sample.price is None:
            continue
        elapsed = max(
            0, int((sample.captured_at - market.start_at).total_seconds())
        )
        for k, change in enumerate(changes):
            change_ref[k] = _advance_reference(
                series, i, change_ref[k], change.over_seconds
            )
        if _all_hold(conditions, series, i, sample.price, elapsed, changes, change_ref):
            return i
    return None


def _trade_result(
    strategy: Strategy,
    at_entry: PriceSample,
    entry_fill: Fill,
    exit_time: datetime,
    exit_fill: Fill,
    reason: str,
) -> TradeResult:
    fee = strategy.taker_fee
    entry_eff = effective_price(entry_fill.price, fee)
    exit_eff = effective_price(exit_fill.price, -fee)
    shares = strategy.budget_per_trade / entry_eff if entry_eff > 0 else 0.0
    pnl = (exit_eff - entry_eff) * shares
    return_pct = (
        pnl / strategy.budget_per_trade if strategy.budget_per_trade > 0 else 0.0
    )
    entry_fee = (
        entry_fill.shares
        * entry_fill.price
        * fee
        * entry_fill.price
        * (1.0 - entry_fill.price)
    )
    exit_fee = (
        exit_fill.shares
        * exit_fill.price
        * fee
        * exit_fill.price
        * (1.0 - exit_fill.price)
    )
    return TradeResult(
        entered_at=at_entry.captured_at,
        exited_at=exit_time,
        entry_price=round_price(entry_fill.price),
        exit_price=round_price(exit_fill.price),
        shares=round_price(shares),
        pnl=round_price(pnl),
        return_pct=round_price(return_pct),
        fees=round_price(entry_fee + exit_fee),
        reason=reason,
        entry_fill_source=entry_fill.source,
        partial_fill=entry_fill.partial or exit_fill.partial,
    )


def run(
    strategy: Strategy,
    market: BacktestMarket,
    prices: Sequence[PriceSample],
    books: BookLookup,
) -> MarketResult:
    series = _entry_token_series(prices, strategy.side)
    if len(series) < 2:
        return MarketResult.skipped(
            "not enough "
            + strategy.side.label
            + " price history ("
            + str(len(series))
            + " rows)"
        )

    entry_sample = next(
        (sample for sample in series if not sample.captured_at < market.start_at),
        None,
    )
    if entry_sample is None:
        return MarketResult.skipped(
            "first " + strategy.side.label + " snapshot after market start is missing"
        )

    trades: list[TradeResult] = []
    skip_reason: Optional[str] = None
    scan_from = 0
    while True:
        open_index = _find_entry_index(strategy, market, series, scan_from)
        if open_index is None:
            break
        lag = strategy.trade_lag_seconds
        entry_fill_sample = fill_sample(series, open_index, lag)
        entry_fill = fill_buy(
            strategy.budget_per_trade,
            entry_fill_sample.token_id,
            entry_fill_sample,
            books,
        )
        if entry_fill is None:
            if skip_reason is None:
                skip_reason = (
                    "no L2 depth or quote at entry to buy " + strategy.side.label
                )
            scan_from = open_index + 1
            continue

        shares = strategy.budget_per_trade / effective_price(
            entry_fill.price, strategy.taker_fee
        )
        entry_time = entry_fill_sample.captured_at
        window_end = market.end_at or series[-1].captured_at
        trailing_distance = strategy.exit.trailing_stop_distance
        trailing_activation = strategy.exit.trailing_stop_activation
        max_hold = strategy.exit.max_hold_seconds
        exit_before_close = strategy.exit.exit_before_close_seconds
        high_water = entry_fill.price
        trailing_active = (
            trailing_distance is None
            or trailing_activation is None
            or entry_fill.price >= trailing_activation
        )

        trigger_index = -1
        reason: Optional[str] = None
        for i in range(open_index + 1, len(series)):
            sample = series[i]
            if sample.price is None:
                continue
            price = sample.price

            if (
                strategy.exit.take_profit is not None
                and price >= strategy.exit.take_profit
            ):
                trigger_index = i
                reason = "take_profit"
                break

            if trailing_distance is not None:
                if not trailing_active:
                    if (
                        trailing_activation is not None
                        and price >= trailing_activation
                    ):
                        trailing_active = True
                        high_water = price
                else:
                    if price > high_water:
                        high_water = price
                    if price <= high_water - trailing_distance:
                        trigger_index = i
                        reason = "trailing_stop"
                        break

            if strategy.exit.stop_loss is not None and price <= strategy.exit.stop_loss:
                trigger_index = i
                reason = "stop_loss"
                break

            if (
                max_hold is not None
                and int((sample.captured_at - entry_time).total_seconds()) >= max_hold
            ):
                trigger_index = i
                reason = "max_hold"
                break

            if (
                exit_before_close is not None
                and int((window_end - sample.captured_at).total_seconds())
                <= exit_before_close
            ):
                trigger_index = i
                reason = "exit_before_close"
                break

        if trigger_index >= 0:
            exit_fill_sample = fill_sample(series, trigger_index, lag)
            exit_fill = fill_sell(
                exit_fill_sample.token_id, shares, exit_fill_sample, books
            )
            if exit_fill is None:
                exit_fill = Fill.settlement(
                    _price_for_settlement(market, strategy.side)
                )
            trade = _trade_result(
                strategy,
                entry_fill_sample,
                entry_fill,
                exit_fill_sample.captured_at,
                exit_fill,
                reason,
            )
            exit_index = trigger_index
        elif strategy.exit.hold_to_expiry:
            exit_fill = Fill.settlement(_price_for_settlement(market, strategy.side))
            trade = _trade_result(
                strategy,
                entry_fill_sample,
                entry_fill,
                window_end,
                exit_fill,
                "expiry",
            )
            exit_index = len(series) - 1
        else:
            last = series[-1]
            exit_fill = fill_sell(last.token_id, shares, last, books)
            if exit_fill is None:
                exit_fill = Fill.settlement(
                    _price_for_settlement(market, strategy.side)
                )
            trade = _trade_result(
                strategy,
                entry_fill_sample,
                entry_fill,
                last.captured_at,
                exit_fill,
                "end_of_window",
            )
            exit_index = len(series) - 1

        trades.append(trade)
        scan_from = exit_index + 1
        if strategy.max_trades_per_market > 0 and len(trades) >= strategy.max_trades_per_market:
            break

    if not trades:
        if skip_reason is not None:
            return MarketResult.skipped(skip_reason)
        return MarketResult.skipped("entry conditions never triggered")
    return MarketResult.traded(trades)