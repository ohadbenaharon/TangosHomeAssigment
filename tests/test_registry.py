from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

import pytest

import registry_service
from orchestrator.models import Failed, LinkedCompany, Officer, Ran, RegistryFindings
from orchestrator.sources.registry import (
    CachingRegistry,
    CircuitBreaker,
    RegistryClient,
    RegistryError,
    RetryPolicy,
)
from tests.fake_registry import FakeRegistry, record, serve

SEVEN_OFFICERS = [
    ("Farhad Noori", "director"),
    ("Leila Karimi", "director"),
    ("Omid Shirazi", "secretary"),
    ("Sara Tehrani", "treasurer"),
    ("Babak Jafari", "director"),
    ("Dariush Rahimi", "director"),
    ("Mina Soltani", "secretary"),
]
ARIA = record(
    "REG-IR-0042",
    "Aria Shipping Lines",
    SEVEN_OFFICERS,
    [("Caspian Trade Bank", "subsidiary"), ("Zagros Freight", "shareholder")],
    aliases=["Aria Lines"],
)
NO_RETRY_WAIT = RetryPolicy(attempts=3, backoff_seconds=0)


@pytest.fixture
def fake() -> Iterator[FakeRegistry]:
    with serve(FakeRegistry(records=[ARIA])) as registry:
        yield registry


def client(fake: FakeRegistry, retry: RetryPolicy = NO_RETRY_WAIT) -> RegistryClient:
    return RegistryClient(fake.url, timeout_seconds=2, retry=retry)


def test_follows_pagination_to_collect_all_officers(fake: FakeRegistry) -> None:
    rec = client(fake).lookup("Aria Shipping Lines")
    assert rec is not None
    assert [o.name for o in rec.officers] == [n for n, _ in SEVEN_OFFICERS]
    assert [r.get("page") for r in fake.requests] == [None, "REG-IR-0042:1"]


def test_links_repeated_on_every_page_are_not_duplicated(fake: FakeRegistry) -> None:
    rec = client(fake).lookup("aria lines")
    assert rec is not None
    assert rec.linked == (
        LinkedCompany("Caspian Trade Bank", ("subsidiary",)),
        LinkedCompany("Zagros Freight", ("shareholder",)),
    )


def test_one_person_with_two_positions_is_one_officer() -> None:
    rec_data = record("REG-1", "X Co", [("Emre Aydin", "director"), ("emre aydin", "secretary")])
    with serve(FakeRegistry(records=[rec_data])) as fake:
        rec = client(fake).lookup("X Co")
    assert rec is not None
    assert rec.officers == (Officer("Emre Aydin", ("director", "secretary")),)


def test_unknown_entity_is_none(fake: FakeRegistry) -> None:
    assert client(fake).lookup("Northwind Logistics") is None


def test_transient_errors_are_retried(fake: FakeRegistry) -> None:
    fake.fail_with = [503, 500]
    assert client(fake).lookup("Aria Shipping Lines") is not None
    assert len(fake.requests) == 4  # 2 failures + 2 pages


def test_persistent_errors_raise_instead_of_reporting_not_found(fake: FakeRegistry) -> None:
    fake.fail_with = [503] * 3
    with pytest.raises(RegistryError, match="HTTP 503"):
        client(fake).lookup("Aria Shipping Lines")


def test_client_errors_are_not_retried(fake: FakeRegistry) -> None:
    fake.fail_with = [400]
    with pytest.raises(RegistryError, match="HTTP 400"):
        client(fake).lookup("Aria Shipping Lines")
    assert len(fake.requests) == 1


def test_retry_backoff_is_exponential() -> None:
    sleeps: list[float] = []
    dead = RegistryClient(
        "http://127.0.0.1:9",  # discard port: connection refused
        retry=RetryPolicy(attempts=3, backoff_seconds=0.1),
        sleep=sleeps.append,
    )
    with pytest.raises(RegistryError, match="after 3 attempts"):
        dead.lookup("anything")
    assert sleeps == [0.1, 0.2]


def test_circuit_breaker_fails_fast_once_the_service_looks_down() -> None:
    clock = FakeClock()
    dead = RegistryClient(
        "http://127.0.0.1:9",  # connection refused
        retry=RetryPolicy(attempts=3, backoff_seconds=0.1),
        failure_threshold=2,
        breaker_min_seconds=0.1,
        sleep=clock.sleep,
        clock=clock,
    )
    sleeps = clock.sleeps
    with pytest.raises(RegistryError, match="unavailable"):
        dead.lookup("first")  # two refused attempts open the breaker; third refused fast
    with pytest.raises(RegistryError, match="unavailable"):
        dead.lookup("second")  # no network attempt, no backoff
    assert sleeps == [0.1, 0.2]


def test_circuit_breaker_ignores_a_burst_of_simultaneous_failures() -> None:
    """16 threads can hit 5 connection resets in the same millisecond (e.g. two runs
    at once overflow the service's backlog); retries absorb that, it is no outage."""
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=5, min_seconds=1.0, clock=clock)
    for _ in range(10):
        breaker.record_failure()
    assert not breaker.is_open
    clock.sleep(1.0)
    breaker.record_failure()  # still failing a second later: now it is down
    assert breaker.is_open
    breaker.record_success()
    assert not breaker.is_open


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_circuit_breaker_resets_on_any_answer(fake: FakeRegistry) -> None:
    flaky = RegistryClient(fake.url, retry=NO_RETRY_WAIT, failure_threshold=3)
    for _ in range(3):  # 2 failures each time, never 3 in a row: breaker stays closed
        fake.fail_with = [503, 503]
        assert flaky.lookup("Aria Shipping Lines") is not None


def test_cache_dedupes_by_normalised_name(fake: FakeRegistry) -> None:
    cache = CachingRegistry(client(fake))
    first = cache.lookup("Aria Shipping Lines")
    assert cache.lookup("  aria shipping LINES ") == first
    assert isinstance(first, Ran) and first.value.record is not None
    assert len(fake.requests) == 2  # one lookup = two pages
    assert (cache.hits, cache.misses) == (1, 1)


def test_cache_shares_one_in_flight_request_between_threads(fake: FakeRegistry) -> None:
    cache = CachingRegistry(client(fake))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(cache.lookup, ["Aria Shipping Lines"] * 8))
    assert all(r == results[0] for r in results)
    assert len(fake.requests) == 2


def test_cache_turns_failures_into_failed_outcomes_and_caches_them(fake: FakeRegistry) -> None:
    fake.fail_with = [503] * 3
    cache = CachingRegistry(client(fake))
    assert isinstance(cache.lookup("Aria Shipping Lines"), Failed)
    assert isinstance(cache.lookup("Aria Shipping Lines"), Failed)
    assert len(fake.requests) == 3


def test_contract_with_the_real_registry_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards against drift between our client and the provided service."""
    monkeypatch.setattr(registry_service, "LATENCY_SECONDS", 0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), registry_service.Handler)
    threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
    try:
        real = RegistryClient(f"http://127.0.0.1:{server.server_address[1]}")
        rec = real.lookup("Aria Lines")
        assert rec is not None
        assert (rec.id, len(rec.officers), len(rec.linked)) == ("REG-IR-0042", 7, 3)
        assert real.lookup("No Such Company") is None
        assert RegistryFindings(real.lookup("aria shipping lines")).record == rec
    finally:
        server.shutdown()
        server.server_close()
