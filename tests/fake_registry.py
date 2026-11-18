"""An in-process registry server with the real service's wire format, plus
knobs for failure injection and request counting."""

from __future__ import annotations

import json
import threading
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class FakeRegistry:
    records: list[dict[str, Any]] = field(default_factory=list)
    page_size: int = 5
    # Statuses to answer with before serving normally, e.g. [503, 503].
    fail_with: list[int] = field(default_factory=list)
    requests: list[dict[str, str]] = field(default_factory=list)
    url: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def respond(self, query: dict[str, str]) -> tuple[int, dict[str, Any]]:
        with self._lock:
            self.requests.append(query)
            if self.fail_with:
                return self.fail_with.pop(0), {"error": "injected"}
        wanted = query.get("name", "").casefold().strip()
        for record in self.records:
            names = [record["name"], *record.get("aliases", [])]
            if wanted in (n.casefold() for n in names):
                page = int(query["page"].rsplit(":", 1)[1]) if "page" in query else 0
                start = page * self.page_size
                more = len(record["officers"]) > start + self.page_size
                return 200, {
                    "id": record["id"],
                    "name": record["name"],
                    "country": record["country"],
                    "officers": record["officers"][start : start + self.page_size],
                    "next_page": f"{record['id']}:{page + 1}" if more else None,
                    "links": record["links"],
                }
        return 404, {"error": "not found"}


@contextmanager
def serve(registry: FakeRegistry) -> Iterator[FakeRegistry]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            status, payload = registry.respond(query)
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    registry.url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield registry
    finally:
        server.shutdown()
        server.server_close()


def record(
    id: str,
    name: str,
    officers: list[tuple[str, str]],
    links: list[tuple[str, str]] | None = None,
    country: str = "IR",
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "country": country,
        "aliases": aliases or [],
        "officers": [{"name": n, "role": r} for n, r in officers],
        "links": [{"name": n, "relation": r} for n, r in links or []],
    }
