"""
identity.py — who is asking.

An auth proxy such as Cloudflare Access authenticates at the edge and forwards
the verified address in `Cf-Access-Authenticated-User-Email`. That header is
only as trustworthy as the answer to "who can send a request to this
container?". The intended answer: only the `web` nginx, which only the auth
proxy (e.g. cloudflared) can reach. mandarin-api and web publish no ports, so
forging the header means already running code on the host.

That is a NETWORK guarantee, not a cryptographic one (verifying
Cf-Access-Jwt-Assertion against Cloudflare's JWKS would be the latter). If you
ever add `ports:` to this service or to web, come back here first.

Fail closed: an empty allowlist refuses everyone.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("mandarin.identity")

HEADER = "Cf-Access-Authenticated-User-Email"


def _clean(s: str) -> str:
    return (s or "").strip().lower()


class Identity:
    def __init__(self, allowed: str = ""):
        self.allowed = frozenset(_clean(x) for x in (allowed or "").split(",") if _clean(x))
        if not self.allowed:
            log.error("MANDARIN_ALLOWED_EMAILS is not set - every request will "
                      "be refused. Set it in .env and restart.")

    @classmethod
    def from_env(cls) -> "Identity":
        return cls(os.environ.get("MANDARIN_ALLOWED_EMAILS", ""))

    def known(self, email: str) -> bool:
        e = _clean(email)
        return bool(e) and e in self.allowed
