"""Part G — a semantic response cache that is safe, fresh, and keeps FAQ answers up in an outage.

* ``SemanticCache``: lookup by embedding similarity *within a scope* (prompt
  version; personal questions never), TTL + a stale window, invalidation by
  source document, bounded size (LRU).
* ``CachingClient``: wraps your ResilientClient. A fresh hit answers without
  calling any provider; a miss goes to the providers and the answer is stored;
  when the providers give up, a *stale* answer beats no answer (fallback
  ladder, rung 3: "cached response").

Tests: tests/test_cache.py, then `make cache` (tape C1).

Lifetime of an entry (age = now - stored_at):

    0 ......... ttl_s ................ ttl_s + max_stale_s ......
    |   fresh   |  stale: only if no provider answers  |  expired: drop it

Only FAQ calls carry ``call.request``; every other call has ``request=None``
and must go straight to the inner client.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from gameday_lab.sim.contracts import (  # noqa: F401
    Action,
    Answer,
    Attempt,
    BaseClient,
    CallContext,
    GiveUp,
    ProviderError,
    Request,
    Serve,
)


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """PROVIDED. Cosine similarity: 1.0 = same direction (high = strict threshold)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class Entry:
    embedding: tuple[float, ...]
    answer: Answer
    stored_at: float
    last_used: float


@dataclass(frozen=True)
class CacheHit:
    answer: Answer
    similarity: float
    age_s: float
    fresh: bool  # age_s <= ttl_s; a stale hit may only be served when no provider can answer


class SemanticCache:
    def __init__(self, similarity_threshold: float = 0.9, ttl_s: float = 60.0, max_stale_s: float = 600.0,
                 max_entries: int = 500) -> None:
        """PROVIDED: reads the `cache:` block of configs/gameday.yaml."""
        self.similarity_threshold = similarity_threshold
        self.ttl_s = ttl_s
        self.max_stale_s = max_stale_s
        self.max_entries = max_entries
        self.entries: list[Entry] = []

    def lookup(self, req: Request, now: float) -> CacheHit | None:
        """The best usable entry for ``req``, or None."""
        if req.personal:
            return None

        # Drop expired entries (age > ttl_s + max_stale_s)
        self.entries = [e for e in self.entries if (now - e.stored_at) <= (self.ttl_s + self.max_stale_s)]

        # Scope: only entries whose answer.prompt_version == req.prompt_version
        scoped = [e for e in self.entries if e.answer.prompt_version == req.prompt_version]
        if not scoped:
            return None

        best_entry = None
        best_sim = -1.0
        for e in scoped:
            sim = cosine(req.embedding, e.embedding)
            if sim > best_sim:
                best_sim = sim
                best_entry = e

        if best_entry is not None and best_sim >= self.similarity_threshold:
            best_entry.last_used = now
            age_s = now - best_entry.stored_at
            return CacheHit(best_entry.answer, best_sim, age_s, fresh=(age_s <= self.ttl_s))

        return None

    def store(self, req: Request, answer: Answer, now: float) -> None:
        """Remember ``answer`` for ``req``."""
        if req.personal:
            return

        # If an entry in the same scope is already >= similarity_threshold similar, replace it
        scoped = [e for e in self.entries if e.answer.prompt_version == req.prompt_version]
        for e in scoped:
            if cosine(req.embedding, e.embedding) >= self.similarity_threshold:
                e.embedding = req.embedding
                e.answer = answer
                e.stored_at = now
                e.last_used = now
                return

        # Otherwise append; if more than max_entries, evict LRU
        new_entry = Entry(req.embedding, answer, stored_at=now, last_used=now)
        self.entries.append(new_entry)
        if len(self.entries) > self.max_entries:
            lru_entry = min(self.entries, key=lambda e: e.last_used)
            self.entries.remove(lru_entry)

    def invalidate(self, source: str, now: float) -> int:
        """Drop every entry whose ``answer.sources`` contains ``source``; return how many."""
        kept = []
        dropped = 0
        for e in self.entries:
            if source in e.answer.sources:
                dropped += 1
            else:
                kept.append(e)
        self.entries = kept
        return dropped


class CachingClient(BaseClient):
    name = "cached"

    def __init__(self, inner: BaseClient, cache: SemanticCache) -> None:
        """PROVIDED. ``inner`` is your ResilientClient (see factory.build_client("cached"))."""
        self.inner = inner
        self.cache = cache

    def on_cancel(self, call: CallContext, tag: Any, now: float) -> None:
        """PROVIDED."""
        self.inner.on_cancel(call, tag, now)

    # ------------------------------------------------------------------ TODO
    def start(self, call: CallContext, now: float) -> Action:
        """FAQ call with fresh hit -> Serve; otherwise inner.start() passed through _or_stale."""
        if call.request is not None:
            hit = self.cache.lookup(call.request, now)
            if hit is not None and hit.fresh:
                return Serve(hit.answer, stale=False)
        action = self.inner.start(call, now)
        return self._or_stale(call, action, now)

    def on_success(self, attempt: Attempt, now: float, latency_s: float) -> None:
        """Tell inner client first, then store answer if FAQ."""
        self.inner.on_success(attempt, now, latency_s)
        if attempt.call.request is not None and attempt.call.answer is not None:
            self.cache.store(attempt.call.request, attempt.call.answer, now)

    def on_error(self, attempt: Attempt, err: ProviderError, now: float) -> Action:
        """Inner on_error passed through _or_stale."""
        action = self.inner.on_error(attempt, err, now)
        return self._or_stale(attempt.call, action, now)

    def on_content_update(self, source: str, now: float) -> None:
        """A document changed (webhook) — invalidate the answers built on it."""
        self.cache.invalidate(source, now)
        self.inner.on_content_update(source, now)

    def _or_stale(self, call: CallContext, action: Action, now: float) -> Action:
        """Stale-if-error."""
        if isinstance(action, GiveUp) and call.request is not None:
            hit = self.cache.lookup(call.request, now)
            if hit is not None:
                return Serve(hit.answer, stale=not hit.fresh)
        return action

