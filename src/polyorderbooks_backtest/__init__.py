"""Official backtesting library for PolyOrderbooks historical Polymarket order-book data."""

from importlib.metadata import PackageNotFoundError, version as _version

from polyorderbooks_backtest.data import MarketBacktester, MemoryBookLookup, discover_markets
from polyorderbooks_backtest.engine import (
    BacktestMarket,
    BookLookup,
    BookSnapshot,
    Fill,
    MarketResult,
    PriceSample,
    TradeResult,
    run,
)
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

__all__ = [
    "BacktestMarket",
    "BookLookup",
    "BookSnapshot",
    "Coin",
    "Comparator",
    "Entry",
    "EntryCondition",
    "Exit",
    "Fill",
    "MarketBacktester",
    "MarketResult",
    "MarketSelector",
    "MemoryBookLookup",
    "OrCondition",
    "PriceChangeCondition",
    "PriceCondition",
    "PriceSample",
    "Side",
    "Strategy",
    "TimeCondition",
    "TradeResult",
    "discover_markets",
    "run",
    "summarize",
]

try:
    __version__ = _version("polyorderbooks-backtest")
except PackageNotFoundError:
    __version__ = "0.0.0.dev0"