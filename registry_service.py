#!/usr/bin/env python3
"""Local mirror of the Acme corporate-registry service.

Run: python3 registry_service.py   (listens on http://localhost:7311)
Python 3.12+, stdlib only. Data: data/registry.json (same directory tree).
"""
from __future__ import annotations

import json
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PORT = 7311
LATENCY_SECONDS = 0.3
PAGE_SIZE = 5

_DATA: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "data" / "registry.json").read_text()
)
_INDEX: dict[str, dict[str, Any]] = {}
for _record in _DATA:
    _INDEX[_record["name"].casefold().strip()] = _record
    for _alias in _record.get("aliases", []):
        _INDEX[_alias.casefold().strip()] = _record


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        time.sleep(LATENCY_SECONDS)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/entities":
            self._send(404, {"error": "not found"})
            return
        params = urllib.parse.parse_qs(parsed.query)
        name = (params.get("name") or [""])[0].casefold().strip()
        record = _INDEX.get(name)
        if record is None:
            self._send(404, {"error": "not found"})
            return
        page = 0
        if "page" in params:
            try:
                page = int(params["page"][0].rsplit(":", 1)[1])
            except (IndexError, ValueError):
                self._send(400, {"error": "bad page token"})
                return
        start = page * PAGE_SIZE
        officers = record["officers"][start : start + PAGE_SIZE]
        has_more = len(record["officers"]) > start + PAGE_SIZE
        self._send(
            200,
            {
                "id": record["id"],
                "name": record["name"],
                "country": record["country"],
                "officers": officers,
                "next_page": f"{record['id']}:{page + 1}" if has_more else None,
                "links": record["links"],
            },
        )

    def do_POST(self) -> None:
        time.sleep(LATENCY_SECONDS)
        self._send(404, {"error": "not found"})

    def log_message(self, *args: Any) -> None:  # keep candidate consoles quiet
        pass


if __name__ == "__main__":
    print(f"registry service listening on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
