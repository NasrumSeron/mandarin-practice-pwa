"""
bank.py — load, validate and derive from bank.yaml.

The bank is the ONLY place content lives. It is re-read when the file's mtime
changes; if the new file is broken the LAST GOOD bank stays in service and
`error` says why (short, no stack trace).
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading

import yaml

log = logging.getLogger("mandarin.bank")

DEFAULT_LEITNER = [1, 2, 4, 8, 16]


def validate(doc) -> None:
    """Raises ValueError with a short message."""
    if not isinstance(doc, dict):
        raise ValueError("top level must be a mapping")
    phrases = doc.get("phrases")
    if not isinstance(phrases, list):
        raise ValueError("'phrases' must be a list")
    seen = set()
    for i, p in enumerate(phrases):
        if not isinstance(p, dict):
            raise ValueError(f"phrase #{i + 1} is not a mapping")
        for k in ("id", "en", "zh", "py"):
            if not isinstance(p.get(k), str) or not p[k].strip():
                raise ValueError(f"phrase #{i + 1} ({p.get('id', '?')}) missing '{k}'")
        if p["id"] in seen:
            raise ValueError(f"duplicate phrase id '{p['id']}'")
        seen.add(p["id"])
    g = doc.get("grammar", [])
    if g is not None and not isinstance(g, list):
        raise ValueError("'grammar' must be a list")


def leitner_days(doc: dict) -> list[int]:
    meta = doc.get("meta")
    days = meta.get("leitner_days") if isinstance(meta, dict) else None
    if (isinstance(days, list) and days
            and all(isinstance(d, int) and not isinstance(d, bool) and d > 0 for d in days)):
        return list(days)
    return list(DEFAULT_LEITNER)


def eligible(phrase: dict, allow_pending: bool) -> bool:
    check = phrase.get("check", "pending")
    return check == "verified" or (check == "pending" and allow_pending)


def word_id(hanzi: str) -> str:
    """Stable progress key for a word (hanzi is not allowed in item_id)."""
    return "w_" + hashlib.sha1(hanzi.encode("utf-8")).hexdigest()[:10]


def word_types(doc: dict) -> dict:
    """Optional top-level `word_types: {hanzi: type}`. Bad shapes are ignored."""
    t = doc.get("word_types")
    if not isinstance(t, dict):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in t.items()
            if isinstance(v, str) and v.strip() and len(str(v)) <= 40}


def derive_words(phrases: list[dict], types: dict | None = None) -> list[dict]:
    types = types or {}
    words: dict[str, dict] = {}
    for p in phrases:
        for raw in p.get("words") or []:
            parts = [x.strip() for x in str(raw).split("|", 2)]
            if len(parts) != 3 or not parts[0]:
                continue
            hanzi, py, en = parts
            w = words.setdefault(hanzi, {"id": word_id(hanzi), "hanzi": hanzi,
                                         "pinyin": py, "english": en,
                                         "type": types.get(hanzi, ""), "phrases": [], "topics": []})
            if en and en.lower() not in [x.lower() for x in w["english"].split("; ")]:
                w["english"] += "; " + en                 # same hanzi, another meaning
            for t in p.get("tags") or []:
                if isinstance(t, str) and t not in w["topics"]:
                    w["topics"].append(t)
            if p["id"] not in w["phrases"]:
                w["phrases"].append(p["id"])
    return list(words.values())


def topics_of(doc: dict) -> list[dict]:
    """Optional top-level `topics: [{id, en, zh, chapter}]` Bad items are skipped."""
    out = []
    for t in doc.get("topics") or []:
        if isinstance(t, dict) and isinstance(t.get("id"), str):
            out.append({k: str(t.get(k) or "") for k in ("id", "en", "zh", "chapter")})
    return out


def derive(doc: dict, allow_pending: bool) -> dict:
    phrases = [p for p in doc["phrases"] if eligible(p, allow_pending)]
    tags = sorted({t for p in phrases for t in (p.get("tags") or []) if isinstance(t, str)})
    return {
        "meta": doc.get("meta") or {},
        "phrases": phrases,
        "words": derive_words(phrases, word_types(doc)),
        "grammar": doc.get("grammar") or [],
        "tags": tags,
        "topics": topics_of(doc),
        "leitner_days": leitner_days(doc),
    }


class Bank:
    """Holds the last good parse; reloads when mtime changes."""

    def __init__(self, path: str):
        self.path = path
        self.doc: dict | None = None
        self.error: str | None = None
        self._mtime: float | None = None
        self._lock = threading.Lock()

    def refresh(self) -> None:
        with self._lock:
            try:
                mtime = os.stat(self.path).st_mtime_ns
            except OSError:
                self.error = "bank file not found"
                return
            if mtime == self._mtime:
                return
            self._mtime = mtime
            try:
                with open(self.path, encoding="utf-8") as f:
                    doc = yaml.load(f, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))   # C loader when available: much faster on large banks
                validate(doc)
            except yaml.YAMLError:
                self.error = "bank.yaml is not valid YAML"
                log.warning(self.error)
                return
            except (OSError, ValueError, UnicodeDecodeError) as e:
                self.error = str(e)[:160]
                log.warning("bank rejected: %s", self.error)
                return
            self.doc, self.error = doc, None

    def view(self, allow_pending: bool) -> tuple[dict | None, str | None]:
        self.refresh()
        return (derive(self.doc, allow_pending) if self.doc else None), self.error

    def phrase_count(self, allow_pending: bool) -> int:
        self.refresh()
        return len(derive(self.doc, allow_pending)["phrases"]) if self.doc else 0
