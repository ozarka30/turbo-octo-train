"""Token usage and cost accounting across a session.

Every Claude call records its usage here; the CLI prints a summary at exit and
the memory database keeps a running total. Prices are USD per million tokens
for the Claude API; the subscription backend reports Claude Code's own
estimate instead.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# input, output per MTok; cache read multiplier; cache write multipliers for 5m / 1h TTL
PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
}
CACHE_READ_MULT = {"claude-fable-5-1": 0.025}
CACHE_WRITE_MULT = {"5m": 1.25, "1h": 2.0}


@dataclass
class Call:
    backend: str
    model: str
    kind: str  # ocr | lesson
    input_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output_tokens: int = 0
    cost_usd: Optional[float] = None
    ts: float = field(default_factory=time.time)


def estimate_cost(model: str, *, input_tokens: int, cache_read: int, cache_write: int, output_tokens: int, ttl: str = "5m") -> Optional[float]:
    price = PRICES.get(model)
    if price is None:
        return None
    pin, pout = price
    read_mult = CACHE_READ_MULT.get(model, 0.1)
    write_mult = CACHE_WRITE_MULT.get(ttl, 1.25)
    return (input_tokens * pin + cache_read * pin * read_mult + cache_write * pin * write_mult + output_tokens * pout) / 1_000_000


class UsageMeter:
    def __init__(self):
        self.calls: List[Call] = []
        self._lock = threading.Lock()
        self.listeners = []  # callables taking a Call, e.g. memory persistence

    def record(self, call: Call) -> Call:
        with self._lock:
            self.calls.append(call)
            listeners = list(self.listeners)
        for fn in listeners:
            try:
                fn(call)
            except Exception:  # accounting must never break a lesson
                pass
        return call

    def record_api(self, backend: str, model: str, kind: str, usage, ttl: str = "5m") -> Call:
        """Record from an Anthropic SDK `usage` object."""
        c = Call(
            backend=backend, model=model, kind=kind,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )
        c.cost_usd = estimate_cost(model, input_tokens=c.input_tokens, cache_read=c.cache_read, cache_write=c.cache_write, output_tokens=c.output_tokens, ttl=ttl)
        return self.record(c)

    def record_claude_code(self, model: str, kind: str, envelope: dict) -> Call:
        """Record from a `claude -p --output-format json` result envelope."""
        u = envelope.get("usage") or {}
        c = Call(
            backend="claude-code", model=model, kind=kind,
            input_tokens=int(u.get("input_tokens", 0) or 0),
            cache_read=int(u.get("cache_read_input_tokens", 0) or 0),
            cache_write=int(u.get("cache_creation_input_tokens", 0) or 0),
            output_tokens=int(u.get("output_tokens", 0) or 0),
            cost_usd=envelope.get("total_cost_usd"),
        )
        return self.record(c)

    def totals(self) -> Dict[str, float]:
        with self._lock:
            calls = list(self.calls)
        t = {"calls": len(calls), "input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "cost": 0.0, "priced": 0}
        for c in calls:
            t["input"] += c.input_tokens
            t["cache_read"] += c.cache_read
            t["cache_write"] += c.cache_write
            t["output"] += c.output_tokens
            if c.cost_usd is not None:
                t["cost"] += c.cost_usd
                t["priced"] += 1
        total_in = t["input"] + t["cache_read"] + t["cache_write"]
        t["cached_pct"] = (100.0 * t["cache_read"] / total_in) if total_in else 0.0
        return t

    def summary(self) -> str:
        t = self.totals()
        if not t["calls"]:
            return "Claude usage this session: no calls"
        s = (
            f"Claude usage this session: {t['calls']} calls, "
            f"{t['input'] + t['cache_read'] + t['cache_write']:,} input tokens ({t['cached_pct']:.0f}% served from cache), "
            f"{t['output']:,} output tokens"
        )
        if t["priced"]:
            s += f", about ${t['cost']:.3f}"
            if t["priced"] < t["calls"]:
                s += " (some calls unpriced)"
        return s


_METER = UsageMeter()


def get_meter() -> UsageMeter:
    return _METER
