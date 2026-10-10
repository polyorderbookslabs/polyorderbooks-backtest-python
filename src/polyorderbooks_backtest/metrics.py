from __future__ import annotations

import math
from typing import Dict, Sequence

from polyorderbooks_backtest.engine import TradeResult


def _empty_summary() -> Dict[str, float]:
    return {
        "num_trades": 0.0,
        "wins": 0.0,
        "losses": 0.0,
        "win_rate": 0.0,
        "gross_pnl": 0.0,
        "fees": 0.0,
        "net_pnl": 0.0,
        "avg_return": 0.0,
        "sharpe": 0.0,
        "max_drawdown": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
    }


def summarize(trades: Sequence[TradeResult]) -> Dict[str, float]:
    if not trades:
        return _empty_summary()

    wins = sum(1 for trade in trades if trade.pnl > 0)
    losses = len(trades) - wins
    gross = sum(trade.pnl for trade in trades)
    fees = sum(trade.fees for trade in trades)

    returns = [trade.return_pct for trade in trades]
    avg = sum(returns) / len(returns)
    if len(returns) >= 2:
        variance = sum((value - avg) ** 2 for value in returns) / (len(returns) - 1)
        std = math.sqrt(variance)
        sharpe = avg / std * math.sqrt(len(returns)) if std > 0 else 0.0
    else:
        sharpe = 0.0

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in trades:
        equity += trade.pnl - trade.fees
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)

    return {
        "num_trades": float(len(trades)),
        "wins": float(wins),
        "losses": float(losses),
        "win_rate": wins / len(trades),
        "gross_pnl": round(gross, 6),
        "fees": round(fees, 6),
        "net_pnl": round(gross - fees, 6),
        "avg_return": round(avg, 6),
        "sharpe": round(sharpe, 6),
        "max_drawdown": round(max_drawdown, 6),
        "best_trade": round(max(trade.pnl for trade in trades), 6),
        "worst_trade": round(min(trade.pnl for trade in trades), 6),
    }