"""
answer.py — pinyin answer checking. Pure functions.

MIRRORED in out/web/srv/mandarin/answer.js — change both, and the shared cases
in tests/answer_cases.json keep them honest.

Rules
  * tone marks or tone numbers: xièxiè == xie4xie4
  * `v` or `u:` mean ü
  * case, spaces and punctuation are ignored
  * a tone must match. A MISSING tone is accepted only where the expected
    syllable is toneless (ma, le, de ...). Number 0 or 5 means toneless.
"""
from __future__ import annotations

import re
import unicodedata

_MARKS = {"̄": 1, "́": 2, "̌": 3, "̀": 4}
_VOW = "aeiouü"
# initial? + vowels + coda? + tone digit?. A coda n/ng/r is taken only when
# no vowel follows (otherwise it is the next syllable's initial).
_SYL = re.compile(
    r"(?:zh|ch|sh|[bpmfdtnlgkhjqxrzcsyw])?[" + _VOW + r"]+"
    r"(?:ng(?![" + _VOW + r"])|n(?![" + _VOW + r"])|r(?![" + _VOW + r"]))?[0-5]?")


def syllables(text: str) -> list[tuple[str, int]]:
    """Text -> [(letters, tone)], tone 0 = none/neutral."""
    s = unicodedata.normalize("NFC", text.lower()).replace("u:", "ü").replace("v", "ü")
    w: list[str] = []          # base characters (letters, digits, " " for a break)
    marks: list[int] = []      # tone mark carried by the same index
    for ch in unicodedata.normalize("NFD", s):
        if unicodedata.combining(ch):
            if not w:
                continue
            if ch in _MARKS:
                marks[-1] = _MARKS[ch]
            elif ch == "̈" and w[-1] == "u":
                w[-1] = "ü"
            continue
        if ch.isalpha() or ch in "012345":
            w.append(ch)
        else:
            w.append(" ")
        marks.append(0)
    flat = "".join(w)
    out: list[tuple[str, int]] = []
    pos = 0
    while pos < len(flat):
        if flat[pos] == " " or flat[pos].isdigit():
            pos += 1
            continue
        m = _SYL.match(flat, pos)
        end = m.end() if m else pos + 1
        chunk = flat[pos:end]
        tone = 0
        if chunk[-1].isdigit():
            tone = int(chunk[-1])
            chunk = chunk[:-1]
            if tone == 5:
                tone = 0
        else:
            tone = next((t for t in marks[pos:end] if t), 0)
        out.append((chunk, tone))
        pos = end
    return out


def check_pinyin(given: str, expected: str) -> bool:
    g, e = syllables(given), syllables(expected)
    if not e or len(g) != len(e):
        return False
    for (gl, gt), (el, et) in zip(g, e):
        if gl != el:
            return False
        if gt != et:        # a missing tone (0) matches only an expected 0
            return False
    return True
