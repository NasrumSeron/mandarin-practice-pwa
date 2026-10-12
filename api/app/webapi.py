"""
webapi.py — mandarin-api's HTTP front door (stdlib http.server).

    GET  /health     open   {"ok", "bank_ok", "phrases"}  no user data
    GET  /whoami     open   {"email_known": bool}          never echoes emails
    GET  /bank       known  the bank + derived words/grammar/tags/leitner_days
    GET  /progress   known  the caller's own rows only
    POST /progress   known  {item_id, mode, correct}; everything else ignored

Default-deny: anything not in OPEN needs a known email. The email is ONLY ever
taken from the identity header, never from a body or query string.
See identity.py for what that header does and does not guarantee.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .identity import HEADER

log = logging.getLogger("mandarin.web")

OPEN = {"/health", "/whoami"}
MODES = {"word_en", "en_word", "builder", "cloze", "full", "listen", "review"}
ITEM_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
MAX_BODY = 2048


class RateLimiter:
    """N writes per rolling minute, per email."""

    def __init__(self, per_min: int, clock=time.monotonic):
        self.per_min, self.clock = per_min, clock
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] >= 60:
                q.popleft()
            if len(q) >= self.per_min:
                return False
            q.append(now)
            return True


class Handler(BaseHTTPRequestHandler):
    bank = store = identity = limiter = None
    allow_pending = True

    def log_message(self, *_a):
        pass

    def _send(self, status: int, body) -> None:
        data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _email(self) -> str:
        return (self.headers.get(HEADER, "") or "").strip().lower()

    def _gate(self) -> str | None:
        """Email if known, else sends 403 and returns None."""
        email = self._email()
        if self.identity.known(email):
            return email
        log.warning("refused %s %s (known=False)", self.command, self.path)
        self._send(403, {"ok": False, "error": "not allowed"})
        return None

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            n = self.bank.phrase_count(self.allow_pending)
            return self._send(200, {"ok": True, "bank_ok": self.bank.doc is not None
                                    and self.bank.error is None, "phrases": n})
        if path == "/whoami":
            return self._send(200, {"email_known": self.identity.known(self._email())})
        email = self._gate()
        if email is None:
            return
        if path == "/bank":
            view, err = self.bank.view(self.allow_pending)
            if view is None:
                return self._send(503, {"ok": False, "bank_error": err or "no bank loaded"})
            return self._send(200, {**view, "bank_error": err})
        if path == "/progress":
            return self._send(200, self.store.list(email))
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            n = -1
        # Always consume (a bounded amount of) the body before replying, or the
        # client sees a reset instead of our 403/400/429.
        raw = self.rfile.read(min(max(n, 0), 65536)) if n > 0 else b""
        email = self._gate()
        if email is None:
            return
        if path != "/progress":
            return self._send(404, {"ok": False, "error": "not found"})
        if not self.limiter.allow(email):
            return self._send(429, {"ok": False, "error": "too many writes, slow down"})
        if n <= 0 or n > MAX_BODY:
            return self._send(400, {"ok": False, "error": "body missing or too large"})
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {"ok": False, "error": "body is not valid JSON"})
        if not isinstance(body, dict):
            return self._send(400, {"ok": False, "error": "body must be an object"})
        item_id, mode, correct = body.get("item_id"), body.get("mode"), body.get("correct")
        if not (isinstance(item_id, str) and len(item_id) <= 64 and ITEM_ID.match(item_id)):
            return self._send(400, {"ok": False, "error": "bad item_id"})
        if not isinstance(mode, str) or mode not in MODES:
            return self._send(400, {"ok": False, "error": "bad mode"})
        if not isinstance(correct, bool):
            return self._send(400, {"ok": False, "error": "correct must be true or false"})
        view, _ = self.bank.view(self.allow_pending)
        days = view["leitner_days"] if view else [1, 2, 4, 8, 16]
        self._send(200, {"ok": True, **self.store.record(email, item_id, mode, correct, days)})


def make_server(bank, store, identity, limiter, allow_pending=True,
                host="0.0.0.0", port=8080) -> ThreadingHTTPServer:
    h = type("BoundHandler", (Handler,), {
        "bank": bank, "store": store, "identity": identity,
        "limiter": limiter, "allow_pending": allow_pending})
    return ThreadingHTTPServer((host, port), h)
