"""Corporate-registry lookups over HTTP.

Written against the service's *actual* behaviour, which differs from API.md
(see NOTES.md):

- ``POST /entities/bulk`` always answers 404 -- indistinguishable from "entity
  not found" -- so it is not used; relying on it would silently report every
  entity as having no record.
- Officers are paginated (5 per page): the response carries ``next_page``, a
  token passed back as ``?page=``. Ignoring it under-counts officers and misses
  follow-ups.
- Linked companies arrive as ``links: [{name, relation}]`` (not
  ``linked_entities: [str]``) and are repeated, unpaginated, on every page.

Transport (measured, see NOTES.md):

- ``http.client`` rather than ``urllib.request``: on macOS ``urlopen`` consults
  the system proxy configuration on *every* request, which under 16 threads
  cost ~10s of CPU per run.
- Connections are *opened* one at a time (requests still run concurrently).
  The service's listen backlog is 5 and it closes the connection after every
  response (HTTP/1.0), so concurrent workers reconnect in synchronised bursts
  that overflow the backlog and get reset. A localhost connect takes
  microseconds while a response takes ~300ms, so serialising connects costs
  nothing measurable and removed every reset up to 32 workers.
- A circuit breaker stops trying once the service looks down. Serialised
  connects make an unreachable (packet-dropping) host the worst case: every
  connect waits out the timeout in turn, ~24 minutes for a default run. After
  ``failure_threshold`` consecutive failed attempts spanning at least
  ``breaker_min_seconds``, remaining lookups fail immediately -- still reported
  as errors, never as "no record". The time condition matters: with 16 threads,
  a momentary overload (e.g. two runs at once) resets several connections in
  the same millisecond, which retries absorb; counting alone would read that
  burst as an outage and fail the whole run.

Failure policy: a lookup that cannot be completed raises :class:`RegistryError`
-- it is never reported as "no record". For compliance, "we could not check" and
"there is nothing there" are different answers.
"""

from __future__ import annotations

import http.client
import json
import logging
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Protocol

from orchestrator.models import (
    Failed,
    LinkedCompany,
    Officer,
    Outcome,
    Ran,
    RegistryFindings,
    RegistryRecord,
)
from orchestrator.names import NameKey, name_key

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class RegistryError(Exception):
    """A registry lookup could not be completed."""


class _CircuitOpen(Exception):
    """Internal: the circuit breaker refused a request."""


class RegistryLookup(Protocol):
    def lookup(self, name: str) -> RegistryRecord | None: ...


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 3
    backoff_seconds: float = 0.2  # doubled after each failed attempt

    def delay(self, attempt: int) -> float:
        return self.backoff_seconds * float(2**attempt)


class CircuitBreaker:
    """Opens after ``threshold`` consecutive failures spanning at least
    ``min_seconds``; any success closes it. A burst of simultaneous failures from
    concurrent requests is one moment of trouble, not proof the service is down.

    No half-open probing: this is a batch run of a few seconds, so once the
    service is judged down, failing the rest of the run fast is the goal. A
    long-lived service would add a cool-down and a probe request.
    """

    def __init__(
        self,
        threshold: int,
        min_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = threshold
        self._min_seconds = min_seconds
        self._clock = clock
        self._failures = 0
        self._streak_started = 0.0
        self._open = False
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._open

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open = False

    def record_failure(self) -> None:
        with self._lock:
            now = self._clock()
            if self._failures == 0:
                self._streak_started = now
            self._failures += 1
            if (
                self._failures >= self._threshold
                and now - self._streak_started >= self._min_seconds
            ):
                self._open = True


class RegistryClient:
    """Thread-safe HTTP client for the registry (no caching here)."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 5.0,
        retry: RetryPolicy = RetryPolicy(),
        max_pages: int = 100,
        failure_threshold: int = 5,
        breaker_min_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout must be positive")
        url = urllib.parse.urlsplit(base_url)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError(f"unsupported registry URL {base_url!r}")
        self._connection_class = (
            http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        )
        self._host = url.hostname
        self._port = url.port
        self._path_prefix = url.path.rstrip("/")
        self._connect_gate = threading.Lock()
        self._breaker = CircuitBreaker(failure_threshold, breaker_min_seconds, clock)
        self._timeout = timeout_seconds
        self._retry = retry
        self._max_pages = max_pages
        self._sleep = sleep

    def lookup(self, name: str) -> RegistryRecord | None:
        """Fetch the full record (all officer pages) for ``name``, or None if absent."""
        first = self._get_page(name, page=None)
        if first is None:
            return None
        pages = [first]
        token = first.get("next_page")
        seen_tokens: set[str] = set()
        while token is not None:
            if not isinstance(token, str) or token in seen_tokens:
                raise RegistryError(f"{name!r}: invalid or repeated page token {token!r}")
            if len(pages) >= self._max_pages:
                raise RegistryError(f"{name!r}: more than {self._max_pages} pages")
            seen_tokens.add(token)
            page = self._get_page(name, page=token)
            if page is None or page.get("id") != first.get("id"):
                raise RegistryError(f"{name!r}: record changed during pagination")
            pages.append(page)
            token = page.get("next_page")
        return _parse_record(pages)

    def _get_page(self, name: str, page: str | None) -> dict[str, Any] | None:
        query = {"name": name} | ({"page": page} if page is not None else {})
        path = f"{self._path_prefix}/entities?{urllib.parse.urlencode(query)}"
        last_error = ""
        for attempt in range(self._retry.attempts):
            try:
                status, body = self._request(path)
            except _CircuitOpen:
                raise RegistryError(
                    f"{name!r}: registry unavailable (gave up after repeated failures)"
                ) from None
            except (OSError, http.client.HTTPException) as exc:  # network-level failure
                self._breaker.record_failure()
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if status in RETRYABLE_STATUS:
                    self._breaker.record_failure()
                    last_error = f"HTTP {status}"
                else:
                    self._breaker.record_success()  # the service is answering
                    if status == 200:
                        return _decode(body, name)
                    if status == 404:
                        return None
                    raise RegistryError(f"{name!r}: HTTP {status}")
            if attempt + 1 < self._retry.attempts:
                delay = self._retry.delay(attempt)
                log.info("registry %r: %s, retrying in %.1fs", name, last_error, delay)
                self._sleep(delay)
        raise RegistryError(f"{name!r}: {last_error} (after {self._retry.attempts} attempts)")

    def _request(self, path: str) -> tuple[int, bytes]:
        connection = self._connection_class(self._host, self._port, timeout=self._timeout)
        try:
            with self._connect_gate:
                # Checked inside the gate so threads queued behind a slow connect
                # fail fast once the breaker opens.
                if self._breaker.is_open:
                    raise _CircuitOpen
                connection.connect()
            connection.request("GET", path, headers={"Accept": "application/json"})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()


def _decode(body: bytes, name: str) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise RegistryError(f"{name!r}: response is not JSON") from exc
    if not isinstance(payload, dict):
        raise RegistryError(f"{name!r}: unexpected response shape")
    return payload


def _parse_record(pages: list[dict[str, Any]]) -> RegistryRecord:
    first = pages[0]
    try:
        return RegistryRecord(
            id=str(first["id"]),
            name=str(first["name"]),
            country=str(first["country"]).upper(),
            officers=tuple(
                Officer(name, roles)
                for name, roles in _merge(
                    (o["name"], o["role"]) for page in pages for o in page["officers"]
                )
            ),
            # Links are repeated on every page; merging de-duplicates them.
            linked=tuple(
                LinkedCompany(name, relations)
                for name, relations in _merge(
                    (link["name"], link["relation"]) for page in pages for link in page["links"]
                )
            ),
        )
    except (KeyError, TypeError) as exc:
        raise RegistryError(f"malformed registry record: {exc!r}") from exc


def _merge(pairs: Iterable[tuple[str, str]]) -> list[tuple[str, tuple[str, ...]]]:
    """Group (name, attribute) pairs by normalised name, keeping first-seen order.

    One person listed twice with different roles becomes one officer with two
    roles, so officer counts count people, not positions.
    """
    names: dict[NameKey, str] = {}
    attributes: dict[NameKey, list[str]] = {}
    for name, attribute in pairs:
        key = name_key(name)
        names.setdefault(key, name)
        attrs = attributes.setdefault(key, [])
        if attribute not in attrs:
            attrs.append(attribute)
    return [(names[k], tuple(attributes[k])) for k in names]


class CachingRegistry:
    """Memoises lookups by normalised name; safe to share between threads.

    Concurrent lookups of the same name share one in-flight request (the cache
    holds futures, not values). Failures are cached too: within a run, a name
    that exhausted its retries is not hammered again.
    """

    def __init__(self, client: RegistryLookup) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._cache: dict[NameKey, Future[Outcome[RegistryFindings]]] = {}
        self.hits = 0
        self.misses = 0

    def lookup(self, name: str) -> Outcome[RegistryFindings]:
        key = name_key(name)
        with self._lock:
            future = self._cache.get(key)
            owner = future is None
            if future is None:
                future = self._cache[key] = Future()
                self.misses += 1
            else:
                self.hits += 1
        if owner:
            try:
                future.set_result(self._fetch(name))
            except BaseException as exc:  # never leave waiters blocked on a dead future
                future.set_exception(exc)
                raise
        return future.result()

    def _fetch(self, name: str) -> Outcome[RegistryFindings]:
        try:
            return Ran(RegistryFindings(self._client.lookup(name)))
        except RegistryError as exc:
            log.warning("registry lookup failed: %s", exc)
            return Failed(str(exc))
