from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from polyorderbooks import PolyOrderbooksClient

from polyorderbooks_backtest.engine import (
    BacktestMarket,
    BookLookup,
    BookSnapshot,
    MarketResult,
    PriceSample,
    run,
)
from polyorderbooks_backtest.strategy import Side, Strategy

UP_ALIASES = {"up", "yes"}
DOWN_ALIASES = {"down", "no"}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_FALLBACK_RESOLUTIONS = ("1s", "60s", "1m", "5m", "10m", "1h")


def _has_rows(payload: dict) -> bool:
    for values in (payload.get("data") or {}).values():
        if values:
            return True
    return False


def _parse(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_ts(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, (int, float)):
        return _to_ts(datetime.fromtimestamp(value, tz=timezone.utc))
    return str(value)


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pick_label(keys: Sequence[str], side: Side) -> Optional[str]:
    aliases = UP_ALIASES if side is Side.UP else DOWN_ALIASES
    for key in sorted(keys, key=str.lower):
        if key.strip().lower() in aliases:
            return key
    return None


def _normalize_winner(winner: Optional[str]) -> Optional[str]:
    if winner is None:
        return None
    lowered = winner.strip().lower()
    if lowered in {"up", "yes"}:
        return "Up"
    if lowered in {"down", "no"}:
        return "Down"
    return winner.strip()


class MemoryBookLookup(BookLookup):
    def __init__(
        self,
        books_by_token: Dict[
            str, Sequence[Tuple[datetime, Sequence[Sequence[float]], Sequence[Sequence[float]]]]
        ],
    ) -> None:
        self._books: Dict[
            str, Tuple[Tuple[datetime, ...], Sequence[Tuple[datetime, Sequence[Sequence[float]], Sequence[Sequence[float]]]]]
        ] = {}
        for token, entries in books_by_token.items():
            ordered = sorted(entries, key=lambda item: item[0])
            self._books[token] = (tuple(item[0] for item in ordered), tuple(ordered))

    def book_at(self, token_id: Optional[str], at: datetime) -> Optional[BookSnapshot]:
        if token_id is None or token_id not in self._books:
            return None
        timestamps, entries = self._books[token_id]
        idx = bisect_right(timestamps, at) - 1
        if idx < 0:
            return None
        captured_at, bids, asks = entries[idx]
        return BookSnapshot(captured_at, bids, asks)


def discover_markets(
    client: PolyOrderbooksClient,
    strategy: Strategy,
    max_markets: Optional[int] = None,
) -> List[dict]:
    wanted = max(1, max_markets if max_markets is not None else strategy.sample_size)
    limit = max(1, min(1000, wanted * 4))
    markets: List[dict] = []
    for page in client.iter_markets(
        search=strategy.market.slug_prefix(),
        include_closed=True,
        sort="start_date",
        order="desc",
        limit=limit,
    ):
        for market in page.get("data") or []:
            if market.get("is_resolved"):
                markets.append(market)
                if len(markets) >= wanted:
                    return markets
    return markets


class MarketBacktester:
    def __init__(
        self,
        client: PolyOrderbooksClient,
        *,
        resolution: str = "1s",
        book_resolution: Optional[str] = None,
    ) -> None:
        self._client = client
        self._resolution = resolution
        self._book_resolution = book_resolution

    def run(
        self,
        id_or_slug: str,
        strategy: Strategy,
        *,
        start_ts: Any = None,
        end_ts: Any = None,
    ) -> MarketResult:
        market = self._client.get_market(id_or_slug)
        return self.run_market(market, strategy, start_ts=start_ts, end_ts=end_ts)

    def run_market(
        self,
        market: dict,
        strategy: Strategy,
        *,
        start_ts: Any = None,
        end_ts: Any = None,
    ) -> MarketResult:
        id_or_slug = market.get("id") or market.get("slug")
        start = _to_ts(start_ts) if start_ts is not None else market.get("start_date")
        end = _to_ts(end_ts) if end_ts is not None else (
            market.get("end_date") or _now_ts()
        )

        prices = None
        books = None
        requested = (self._resolution,) if self._resolution else ()
        for price_resolution in requested + tuple(
            res for res in _FALLBACK_RESOLUTIONS if res not in requested
        ):
            book_resolution = self._book_resolution or price_resolution
            candidate_prices = self._client.get_market_prices(
                id_or_slug, start_ts=start, end_ts=end, resolution=price_resolution
            )
            candidate_books = self._client.get_market_books(
                id_or_slug,
                start_ts=start,
                end_ts=end,
                resolution=book_resolution,
            )
            if _has_rows(candidate_prices):
                prices = candidate_prices
                books = candidate_books
                break
        if prices is None:
            return MarketResult.skipped("no price history for any supported resolution")

        price_data = prices.get("data") or {}
        book_data = books.get("data") or {}
        keys = set(price_data) | set(book_data)
        label = _pick_label(keys, strategy.side)
        if label is None:
            return MarketResult.skipped(
                "no " + strategy.side.label + " outcome in market data"
            )

        tokens = books.get("tokens") or prices.get("tokens") or {}
        token_id = tokens.get(label) if isinstance(tokens, dict) else None
        outcome_label = "Up" if strategy.side is Side.UP else "Down"

        price_points = price_data.get(label) or []
        book_points = book_data.get(label) or []
        book_by_t = {point["t"]: point for point in book_points}

        samples: List[PriceSample] = []
        for point in price_points:
            pair = book_by_t.get(point["t"])
            bids = (pair or {}).get("bids") or []
            asks = (pair or {}).get("asks") or []
            samples.append(
                PriceSample(
                    captured_at=_parse(point["t"]),
                    token_id=token_id,
                    outcome_label=outcome_label,
                    price=float(point["p"]),
                    best_bid=bids[0][0] if bids else None,
                    best_ask=asks[0][0] if asks else None,
                )
            )
        samples.sort(key=lambda sample: sample.captured_at)

        ordered_books = sorted(book_points, key=lambda point: _parse(point["t"]))
        if token_id is not None:
            book_lookup = MemoryBookLookup(
                {
                    token_id: [
                        (_parse(point["t"]), point.get("bids") or [], point.get("asks") or [])
                        for point in ordered_books
                    ]
                }
            )
        else:
            book_lookup = MemoryBookLookup({})

        start_date = market.get("start_date")
        start_at = (
            _parse(start_date)
            if start_date
            else (samples[0].captured_at if samples else _EPOCH)
        )

        winner = market.get("winner")
        if winner is None:
            try:
                detail = self._client.get_market(id_or_slug)
                winner = (detail or {}).get("winner") or winner
            except Exception:
                pass

        end_date = market.get("end_date")
        market_model = BacktestMarket(
            market_id=market.get("public_id"),
            source_market_id=market.get("id"),
            slug=market.get("slug"),
            winner=_normalize_winner(winner),
            start_at=start_at,
            end_at=_parse(end_date) if end_date else None,
        )
        return run(strategy, market_model, samples, book_lookup)