import json
from datetime import datetime, timedelta
from math import sin

STRATEGY_VERSION = "0.2.0"
LAST_UPDATED = datetime.now().isoformat()
HYPOTHESES = [
    {
        "name": "POI liquidity continuation",
        "claim": (
            "If price sweeps a recent point-of-interest range while volume "
            "expands, the next candles are more likely to continue upward."
        ),
        "rejection": "Reject if win_rate < 0.50, profit_factor < 1.0, or trades < 30.",
    }
]

def _num(candle: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(candle.get(key, default))
    except (TypeError, ValueError):
        return default

def analyze_market_structure(candles: list[dict]) -> dict:
    if len(candles) < 5:
        return {
            "trend": "NEUTRAL",
            "structure_break": False,
            "key_levels": [],
            "signal": "NEUTRAL",
            "confidence": 0.0,
        }

    recent = candles[-5:]
    first_close = _num(recent[0], "close")
    last_close = _num(recent[-1], "close")
    highs = [_num(c, "high") for c in recent]
    lows = [_num(c, "low") for c in recent]

    if last_close > first_close:
        trend = "UP"
    elif last_close < first_close:
        trend = "DOWN"
    else:
        trend = "NEUTRAL"

    previous = candles[-6:-1] if len(candles) >= 6 else candles[:-1]
    previous_high = max((_num(c, "high") for c in previous), default=highs[-1])
    previous_low = min((_num(c, "low") for c in previous), default=lows[-1])
    structure_break = highs[-1] > previous_high or lows[-1] < previous_low

    return {
        "trend": trend,
        "structure_break": structure_break,
        "key_levels": [min(lows), max(highs)],
        "signal": "BULLISH" if trend == "UP" and structure_break else "NEUTRAL",
        "confidence": 0.6 if trend == "UP" and structure_break else 0.2,
    }

def analyze_poi(candles: list[dict], lookback: int = 12) -> dict:
    if len(candles) < 3:
        return {"poi_points": [], "trade_entries": []}

    window = candles[-lookback:]
    poi_points = []
    for index in range(1, len(window)):
        current = window[index]
        previous = window[index - 1]
        if _num(current, "high") > _num(previous, "high") and _num(current, "low") < _num(previous, "low"):
            poi_points.append(
                {
                    "timestamp": current.get("timestamp"),
                    "high": _num(current, "high"),
                    "low": _num(current, "low"),
                }
            )

    last = candles[-1]
    trade_entries = []
    for poi in poi_points:
        touched = _num(last, "high") >= poi["high"] or _num(last, "low") <= poi["low"]
        if touched:
            trade_entries.append(
                {
                    "timestamp": last.get("timestamp"),
                    "price": _num(last, "close"),
                    "poi": poi,
                }
            )

    return {"poi_points": poi_points, "trade_entries": trade_entries}

def analyze_poi_with_liquidity(candles: list[dict], lookback: int = 12, volume_multiple: float = 1.35) -> dict:
    poi_analysis = analyze_poi(candles, lookback=lookback)
    if len(candles) < 6:
        return {**poi_analysis, "liquidity_entries": []}

    current = candles[-1]
    previous = candles[-6:-1]
    average_volume = sum(_num(c, "volume") for c in previous) / len(previous)
    has_liquidity = average_volume > 0 and _num(current, "volume") >= average_volume * volume_multiple

    liquidity_entries = []
    if has_liquidity:
        liquidity_entries.append(
            {
                "timestamp": current.get("timestamp"),
                "volume": _num(current, "volume"),
                "average_volume": average_volume,
            }
        )

    trade_entries = poi_analysis["trade_entries"] if has_liquidity else []
    return {
        "poi_points": poi_analysis["poi_points"],
        "liquidity_entries": liquidity_entries,
        "trade_entries": trade_entries,
    }

def generate_signal(analysis: dict, poi_analysis: dict, poi_liquidity_analysis: dict) -> dict:
    if poi_liquidity_analysis["trade_entries"] and analysis["trend"] != "DOWN":
        return {
            "action": "BUY",
            "confidence": 0.75,
            "reason": "POI touched with expanded volume",
        }
    if poi_analysis["trade_entries"] and analysis["trend"] == "UP":
        return {
            "action": "BUY",
            "confidence": 0.55,
            "reason": "POI touched in upward structure",
        }
    return {
        "action": "HOLD",
        "confidence": 0.0,
        "reason": "No qualified POI/liquidity setup",
    }

def signal_for_window(candles: list[dict]) -> dict:
    analysis = analyze_market_structure(candles)
    poi_analysis = analyze_poi(candles)
    poi_liquidity_analysis = analyze_poi_with_liquidity(candles)
    return generate_signal(analysis, poi_analysis, poi_liquidity_analysis)

def generate_sample_candles(count: int = 1200) -> list[dict]:
    candles = []
    base_time = datetime(2026, 1, 1)
    close = 100.0

    for index in range(count):
        wave = sin(index / 8) * 0.7
        drift = 0.035 if index % 48 < 30 else -0.025
        open_price = close
        close = max(1.0, close + drift + wave * 0.08)
        high = max(open_price, close) + 0.35 + abs(wave) * 0.2
        low = min(open_price, close) - 0.35 - abs(wave) * 0.2
        volume = 1000 + (index % 18) * 25

        if index % 37 == 0 and index > 0:
            high += 0.9
            low -= 0.9
            volume *= 1.8

        candles.append(
            {
                "timestamp": (base_time + timedelta(hours=index)).isoformat(),
                "open": round(open_price, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close, 4),
                "volume": round(volume, 2),
            }
        )

    return candles

def _max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0

    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak:
            max_dd = min(max_dd, (value - peak) / peak)
    return abs(max_dd)

def run_backtest(candles: list[dict], lookback: int = 20, hold_period: int = 3) -> dict:
    trades = []
    equity = 1.0
    equity_curve = [equity]

    end = len(candles) - hold_period
    for index in range(lookback, end):
        window = candles[: index + 1]
        signal = signal_for_window(window)
        if signal["action"] != "BUY":
            continue

        entry = _num(candles[index], "close")
        exit_price = _num(candles[index + hold_period], "close")
        if entry <= 0:
            continue

        pnl_pct = (exit_price - entry) / entry
        equity *= 1 + pnl_pct
        equity_curve.append(equity)
        trades.append(
            {
                "entry_time": candles[index].get("timestamp"),
                "exit_time": candles[index + hold_period].get("timestamp"),
                "entry": entry,
                "exit": exit_price,
                "pnl_pct": pnl_pct,
                "reason": signal["reason"],
            }
        )

    wins = [trade for trade in trades if trade["pnl_pct"] > 0]
    losses = [trade for trade in trades if trade["pnl_pct"] <= 0]
    gross_profit = sum(trade["pnl_pct"] for trade in wins)
    gross_loss = abs(sum(trade["pnl_pct"] for trade in losses))
    profit_factor = gross_profit / gross_loss if gross_loss else (gross_profit if gross_profit else 0.0)

    return {
        "strategy_version": STRATEGY_VERSION,
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "profit_factor": profit_factor,
        "max_drawdown": _max_drawdown(equity_curve),
        "return_pct": equity - 1.0,
        "sample_trades": trades[:5],
        "status": "OK",
    }

def main() -> None:
    candles = generate_sample_candles()
    result = run_backtest(candles)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()