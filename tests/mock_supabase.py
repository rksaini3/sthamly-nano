"""A tiny in-memory stand-in for Supabase's REST API (PostgREST), for tests. Runs on localhost in a thread.
It understands only what scripts/admin.py and scripts/sthamly_common.py use: eq / ilike / in filters,
select / insert / patch, and the RPC calls claim_next_job, fail_stale_jobs, purge_old_jobs.
"""
from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote, urlparse

TABLES = ("jobs", "licenses", "models")


class MockSupabase:
    def __init__(self, api_key: str = "test-service-key"):
        self.api_key = api_key
        self.rows: dict[str, list[dict]] = {t: [] for t in TABLES}
        self.requests: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep test output quiet
                pass

            def _send(self, code: int, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _route(self, method: str):
                with outer._lock:
                    outer.requests.append((method, self.path))
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n)) if n else None
                    if self.headers.get("apikey") != outer.api_key or self.headers.get("Authorization") != f"Bearer {outer.api_key}":
                        return self._send(401, {"message": "bad key"})
                    u = urlparse(self.path)
                    parts = u.path.strip("/").split("/")  # rest/v1/<table or rpc>/<fn>
                    query = parse_qsl(u.query, keep_blank_values=True)
                    if parts[2] == "rpc":
                        return self._send(200, outer._rpc(parts[3], body))
                    table = parts[2]
                    if table not in outer.rows:
                        return self._send(404, {"message": "no such table"})
                    if method == "GET":
                        return self._send(200, outer._filter(table, query)[: outer._limit(query)])
                    if method == "POST":
                        row = {"id": str(uuid.uuid4()), **body}
                        if table == "jobs":
                            row.setdefault("status", "queued")
                            row.setdefault("progress", 0)
                        outer.rows[table].append(row)
                        return self._send(201, [row])
                    if method == "PATCH":
                        hit = outer._filter(table, query)
                        for r in hit:
                            r.update(body)
                        return self._send(200, hit)
                    return self._send(405, {"message": "method"})

            def do_GET(self): self._route("GET")
            def do_POST(self): self._route("POST")
            def do_PATCH(self): self._route("PATCH")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    # -- helpers for tests
    def add_job(self, model: str, kind: str = "audit", status: str = "queued") -> dict:
        row = {"id": str(uuid.uuid4()), "kind": kind, "hf_model": model, "status": status, "progress": 0,
               "requester_hash": "t", "steps": [], "result": None, "error": None}
        self.rows["jobs"].append(row)
        return row

    @staticmethod
    def _limit(query) -> int:
        return int(dict(query).get("limit", 10_000))

    def _filter(self, table: str, query) -> list[dict]:
        out = list(self.rows[table])
        for col, val in query:
            if col in ("select", "order", "limit"):
                continue
            op, _, arg = val.partition(".")
            arg = unquote(arg)
            if op == "eq":
                out = [r for r in out if str(r.get(col)) == arg]
            elif op == "ilike":
                want = arg.replace("\\_", "_").lower()
                out = [r for r in out if str(r.get(col, "")).lower() == want]
            elif op == "in":
                allowed = arg.strip("()").split(",")
                out = [r for r in out if str(r.get(col)) in allowed]
        return out

    def _rpc(self, fn: str, args):
        if fn == "claim_next_job":
            for r in self.rows["jobs"]:
                if r["status"] == "queued":
                    r["status"] = "running"
                    return {"id": r["id"], "kind": r["kind"], "hf_model": r["hf_model"]}
            return None
        if fn in ("fail_stale_jobs", "purge_old_jobs"):
            return 0
        return None
