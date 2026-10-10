from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Sequence, Union


class Coin(str, enum.Enum):
    BTC = "btc"
    ETH = "eth"
    SOL = "sol"
    XRP = "xrp"
    HYPE = "hype"
    BNB = "bnb"
    DOGE = "doge"
    ZEC = "zec"

    def __str__(self) -> str:
        return self.value


class Side(str, enum.Enum):
    UP = "Up"
    DOWN = "Down"

    @property
    def label(self) -> str:
        return self.value

    def won(self, winner: str | None) -> bool:
        return winner is not None and self.label.lower() == winner.strip().lower()


class Comparator(str, enum.Enum):
    LT = "<"
    GT = ">"
    LE = "<="
    GE = ">="

    def __str__(self) -> str:
        return self.value

    def test(self, left: float, right: float) -> bool:
        if self is Comparator.LT:
            return left < right
        if self is Comparator.GT:
            return left > right
        if self is Comparator.LE:
            return left <= right
        return left >= right


@dataclass(frozen=True)
class PriceCondition:
    side: Side
    comparator: Comparator
    threshold: float
    within_seconds: int | None = None


@dataclass(frozen=True)
class PriceChangeCondition:
    side: Side
    comparator: Comparator
    percent: float
    over_seconds: int


@dataclass(frozen=True)
class TimeCondition:
    after_seconds: int


@dataclass(frozen=True)
class OrCondition:
    options: Sequence[Sequence["EntryCondition"]]


EntryCondition = Union[
    PriceCondition, PriceChangeCondition, TimeCondition, OrCondition
]


@dataclass(frozen=True)
class Entry:
    conditions: Sequence[EntryCondition] = field(default_factory=tuple)


@dataclass(frozen=True)
class Exit:
    take_profit: float | None = None
    stop_loss: float | None = None
    hold_to_expiry: bool = False
    trailing_stop_distance: float | None = None
    trailing_stop_activation: float | None = None
    max_hold_seconds: int | None = None
    exit_before_close_seconds: int | None = None

    def has_directive(self) -> bool:
        return (
            self.take_profit is not None
            or self.stop_loss is not None
            or self.trailing_stop_distance is not None
            or self.max_hold_seconds is not None
            or self.exit_before_close_seconds is not None
        )


@dataclass(frozen=True)
class MarketSelector:
    coin: Coin
    market_type: str

    def slug_prefix(self) -> str:
        return f"{str(self.coin)}-updown-{self.market_type}-"


@dataclass(frozen=True)
class Strategy:
    market: MarketSelector
    side: Side
    entry: Entry
    exit: Exit
    budget_per_trade: float
    sample_size: int
    taker_fee: float = 0.0
    trade_lag_seconds: int = 0
    max_trades_per_market: int = 0