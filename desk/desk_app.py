"""Software ka dimaag: license, dastavez, model se baat, aur localhost web server. Sab desk_*.py ko jodta hai.

Suraksha: server sirf 127.0.0.1 par sunta hai, Host header jaanchta hai (DNS rebinding se bachav) aur har API call
par per-session token maangta hai. Isliye kisi website ka JavaScript is software se baat nahi kar sakta.
"""
from __future__ import annotations

import base64
import binascii
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import desk_llama
import desk_prompts
from desk_docs import MAX_PDF_BYTES, DocStore
from desk_errors import DeskError
from desk_license import LicenseStore
from desk_ui import PAGE

MAX_BODY = int(MAX_PDF_BYTES * 1.4) + 4096  # base64 me PDF ~33% badi hoti hai
HISTORY_TURNS = 6


class Desk:
    def __init__(self, cfg: dict[str, Any], llama_url: str, home: Path, dev: bool = False):
        self.cfg, self.llama_url, self.home, self.dev = cfg, llama_url, home, dev
        self.token = secrets.token_urlsafe(24)
        self.docs = DocStore()
        self.licenses = LicenseStore(home, cfg, dev=dev)

    # ---- license
    def license(self) -> dict[str, Any] | None:
        return self.licenses.current()

    def require_license(self) -> dict[str, Any]:
        lic = self.license()
        if not lic:
            raise DeskError("locked", "Pehle activation key daalo.", 403)
        return lic

    # ---- state
    def state(self) -> dict[str, Any]:
        lic = self.license()
        return {"license": lic, "docs": self.docs.listing() if lic else [], "dev": self.dev}

    # ---- documents
    def add_pdf(self, name: str, b64: str) -> dict[str, Any]:
        self.require_license()
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise DeskError("bad_upload", "File theek se nahi pahunchi. Dobara try karo.") from None
        return self.docs.add(name, data)

    # ---- chat
    def build_messages(self, message: str, action: str, history: Any) -> list[dict[str, str]]:
        lic = self.require_license()
        message = (message or "").strip()[:4000]
        
        if action:
            task = desk_prompts.action_prompt(action)
            profile = lic.get("profile") or "general"
            
            # --- CHAMELEON LOGIC VALIDATION UPGRADE ---
            # Enforce that actions are mapped accurately based on profile tokens safely
            if task is None:
                raise DeskError("bad_action", "Ye button algorithm array me nahi mila.")
            
            # Profile wise boundary lockdown to prevent token leaking between variants
            if profile == "lawyer" and action not in ["draft_bail", "draft_notice", "extract_ratios"] and not self.dev:
                raise DeskError("bad_action", "Ye legal shortcut is license par allowed nahi hai.")
            elif profile == "finance" and action not in ["balance_analysis", "statement_summary"] and not self.dev:
                raise DeskError("bad_action", "Ye finance shortcut is license par allowed nahi hai.")
                
            question = task + (f"\n\nUser ki extra baat: {message}" if message else "")
        else:
            if not message:
                raise DeskError("empty", "Pehle kuch likho.")
            question = message
            
        context = self.docs.context(question)
        if action and not context:
            raise DeskError("need_pdf", "Pehle PDF jodo, phir ye button dabao.")
            
        turns: list[dict[str, str]] = []
        for h in (history if isinstance(history, list) else [])[-HISTORY_TURNS:]:
            if isinstance(h, dict) and h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
                turns.append({"role": h["role"], "content": h["content"][:4000]})
                
        user = f"DOCUMENTS:\n{context}\n\nTASK:\n{question}" if context else question
        return [{"role": "system", "content": desk_prompts.system_prompt(lic.get("profile") or "general")},
                *turns, {"role": "user", "content": user}]

    def stream(self, messages: list[dict[str, str]]) -> Iterator[str]:
        return desk_llama.stream_chat(self.llama_url, messages)


def make_server(desk: Desk, port: int = 8765) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "SthamlyDesk"

        def log_message(self, *_a: Any) -> None:  # console shaant rakho
            pass

        # ---- helpers
        def _json(self, status: int, obj: Any) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self) -> bool:
            port_ = self.server.server_address[1]
            return (self.headers.get("Host") or "") in (f"127.0.0.1:{port_}", f"localhost:{port_}")

        def _body(self) -> dict[str, Any]:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise DeskError("too_big", "File bahut badi hai.", 413)
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                raise DeskError("bad_json", "Request samajh nahi aayi.") from None
            if not isinstance(data, dict):
                raise DeskError("bad_json", "Request samajh nahi aayi.")
            return data

        def _guard(self) -> bool:
            if not self._host_ok():
                self._json(403, {"error": "bad_host", "message": "Ye address allowed nahi hai."})
                return False
            if not secrets.compare_digest(self.headers.get("X-Desk-Token") or "", desk.token):
                self._json(403, {"error": "bad_token", "message": "Page dobara kholo (session purana ho gaya)."})
                return False
            return True

        # ---- routes
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                if not self._host_ok():
                    return self._json(403, {"error": "bad_host", "message": "Ye address allowed nahi hai."})
                page = PAGE.replace("__TOKEN__", desk.token).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Frame-Options", "DENY")
                self.end_headers()
                self.wfile.write(page)
            elif self.path == "/api/state":
                if self._guard():
                    self._json(200, desk.state())
            else:
                self._json(404, {"error": "not_found", "message": "Ye page nahi mila."})

        def do_POST(self) -> None:  # noqa: N802
            if not self._guard():
                return
            try:
                body = self._body()
                if self.path == "/api/activate":
                    lic = desk.licenses.activate(str(body.get("key", "")))
                    return self._json(200, {"license": lic})
                if self.path == "/api/upload":
                    return self._json(200, desk.add_pdf(str(body.get("name", "")), str(body.get("data", ""))))
                if self.path == "/api/delete":
                    desk.require_license()
                    return self._json(200, {"ok": desk.docs.remove(str(body.get("id", "")))})
                if self.path == "/api/chat":
                    msgs = desk.build_messages(str(body.get("message", "")), str(body.get("action", "")), body.get("history"))
                    return self._chat(msgs)
                self._json(404, {"error": "not_found", "message": "Ye page nahi mila."})
            except DeskError as e:
                self._json(e.status, {"error": e.code, "message": e.message})

        def _chat(self, msgs: list[dict[str, str]]) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                for piece in desk.stream(msgs):
                    self.wfile.write(piece.encode("utf-8"))
                    self.wfile.flush()
            except DeskError as e:
                self.wfile.write(f"\n[{e.message}]".encode("utf-8"))
            except (BrokenPipeError, ConnectionResetError):
                pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
