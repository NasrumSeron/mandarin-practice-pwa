"""Entry point: wires env -> Bank, Store, Identity, and serves on 0.0.0.0:8080."""
import logging
import os

from .bank import Bank
from .identity import Identity
from .store import Store
from .webapi import RateLimiter, make_server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    identity = Identity.from_env()
    bank = Bank(os.environ.get("BANK_PATH", "/bank/bank.yaml"))
    bank.refresh()
    store = Store(os.environ.get("DB_PATH", "/app/data/mandarin.db"))
    per_min = int(os.environ.get("MANDARIN_WRITE_PER_MIN", "60") or 60)
    pending = os.environ.get("MANDARIN_ALLOW_PENDING", "1") not in ("0", "false", "False", "")
    srv = make_server(bank, store, identity, RateLimiter(per_min), pending)
    logging.getLogger("mandarin").info(
        "listening on 0.0.0.0:8080 (allowlist=%d, pending=%s, bank_error=%s)",
        len(identity.allowed), pending, bank.error)
    srv.serve_forever()


if __name__ == "__main__":
    main()
