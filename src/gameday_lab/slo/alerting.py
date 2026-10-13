"""Part E — Multi-window, multi-burn-rate alerting (Google SRE Workbook, ch. 5).

Input: per-minute ``total`` and ``bad`` request counts for a 30-day window.

burn_rate(window) = error_ratio(window) / (1 - SLO)

A rule fires at minute i when BOTH its long and its short window (ending at i,
inclusive) burn at least ``burn_rate``. The long window says "this is
significant"; the short window says "it is still happening" (so the alert
resets quickly after recovery).

Tests: tests/test_alerting.py, then `make slo`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlertRule:
    name: str
    severity: str  # "page" or "ticket"
    long_min: int
    short_min: int
    burn_rate: float


# SRE Workbook defaults for a 30-day SLO window
DEFAULT_RULES: tuple[AlertRule, ...] = (
    AlertRule("fast_burn_2pct_1h", "page", 60, 5, 14.4),
    AlertRule("slow_burn_5pct_6h", "page", 360, 30, 6.0),
    AlertRule("budget_leak_10pct_3d", "ticket", 4320, 360, 1.0),
)


@dataclass(frozen=True)
class AlertEvent:
    rule: str
    severity: str
    start_min: int  # first minute the rule fired
    end_min: int  # first minute it stopped firing (exclusive)


class WindowSums:
    """O(1) sum of any window, via prefix sums. Build once, query many times.

    43,200 minutes x 6 windows: recomputing every window with sum() is far too slow.
    """

    def __init__(self, values: list[int]) -> None:
        """Build the prefix-sum list (prefix[0] = 0)."""
        self.values = values
        self.prefix: list[int] = [0] * (len(values) + 1)
        for i, v in enumerate(values):
            self.prefix[i + 1] = self.prefix[i] + v

    def sum(self, end_inclusive: int, length: int) -> int:
        """Sum of values[end_inclusive - length + 1 .. end_inclusive],
        clipped at index 0 when the window starts before the data."""
        if end_inclusive < 0 or length <= 0 or not self.values:
            return 0
        end_idx = min(len(self.values) - 1, end_inclusive)
        start_idx = max(0, end_inclusive - length + 1)
        if start_idx > end_idx:
            return 0
        return self.prefix[end_idx + 1] - self.prefix[start_idx]


class BurnRateAlerter:
    def __init__(self, slo: float = 0.995, rules: tuple[AlertRule, ...] = DEFAULT_RULES) -> None:
        if not 0 < slo < 1:
            raise ValueError("slo must be in (0, 1)")
        self.slo = slo
        self.rules = rules

    def burn_rate(self, total: WindowSums, bad: WindowSums, end: int, length: int) -> float:
        """(bad / total over the window) / (1 - slo); 0.0 if total is 0."""
        tot = total.sum(end, length)
        if tot == 0:
            return 0.0
        b = bad.sum(end, length)
        return (b / tot) / (1.0 - self.slo)

    def evaluate(self, total: list[int], bad: list[int]) -> list[AlertEvent]:
        """One AlertEvent per contiguous firing episode of each rule."""
        total_ws = WindowSums(total)
        bad_ws = WindowSums(bad)
        n = len(total)
        events: list[AlertEvent] = []

        for rule in self.rules:
            firing = False
            start_min = 0
            for i in range(n):
                burn_long = self.burn_rate(total_ws, bad_ws, i, rule.long_min)
                burn_short = self.burn_rate(total_ws, bad_ws, i, rule.short_min)
                is_firing = (burn_long >= rule.burn_rate) and (burn_short >= rule.burn_rate)
                if is_firing and not firing:
                    firing = True
                    start_min = i
                elif not is_firing and firing:
                    firing = False
                    events.append(AlertEvent(rule.name, rule.severity, start_min, i))
            if firing:
                events.append(AlertEvent(rule.name, rule.severity, start_min, n))

        events.sort(key=lambda e: (e.start_min, e.end_min, e.rule))
        return events


def error_budget_report(total: list[int], bad: list[int], slo: float = 0.995) -> dict[str, float | bool]:
    """How much of the 30-day error budget is spent?"""
    requests = sum(total)
    allowed_bad = round((1.0 - slo) * requests, 1)
    tot_bad = sum(bad)
    availability = round(1.0 - (tot_bad / requests if requests else 0.0), 6)
    budget_consumed = round(tot_bad / allowed_bad if allowed_bad else 0.0, 4)
    budget_remaining = round(1.0 - budget_consumed, 4)
    freeze_releases = bool(budget_remaining <= 0.0)
    return {
        "requests": requests,
        "allowed_bad": allowed_bad,
        "bad": tot_bad,
        "availability": availability,
        "budget_consumed": budget_consumed,
        "budget_remaining": budget_remaining,
        "freeze_releases": freeze_releases,
    }

