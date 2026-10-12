/*
 * answer.js — pinyin answer checking. MIRROR of mandarin-api/app/answer.py;
 * change both, and tests/answer_cases.json (run by both test suites) keeps
 * them honest.
 *
 *   tone marks or numbers are equal (xièxiè == xie4xie4); v / u: mean ü;
 *   case, spaces, punctuation ignored; a MISSING tone is accepted only where
 *   the expected syllable is toneless (ma, le, de...). 0 or 5 = toneless.
 */
"use strict";
(function (root) {
  const MARKS = {"\u0304": 1, "\u0301": 2, "\u030c": 3, "\u0300": 4};
  const V = "aeiou\u00fc";
  const SYL = new RegExp(
    "(?:zh|ch|sh|[bpmfdtnlgkhjqxrzcsyw])?[" + V + "]+" +
    "(?:ng(?![" + V + "])|n(?![" + V + "])|r(?![" + V + "]))?[0-5]?", "y");

  function syllables(text) {
    const s = text.toLowerCase().normalize("NFC")
      .replace(/u:/g, "\u00fc").replace(/v/g, "\u00fc");
    const w = [], marks = [];
    for (const ch of s.normalize("NFD")) {
      if (/\p{M}/u.test(ch)) {
        if (!w.length) continue;
        if (MARKS[ch]) marks[marks.length - 1] = MARKS[ch];
        else if (ch === "\u0308" && w[w.length - 1] === "u") w[w.length - 1] = "\u00fc";
        continue;
      }
      w.push(/\p{L}|[0-5]/u.test(ch) ? ch : " ");
      marks.push(0);
    }
    const flat = w.join(""), out = [];
    let pos = 0;
    while (pos < flat.length) {
      if (flat[pos] === " " || /[0-5]/.test(flat[pos])) { pos++; continue; }
      SYL.lastIndex = pos;
      const m = SYL.exec(flat);
      const end = m ? pos + m[0].length : pos + 1;
      let chunk = flat.slice(pos, end), tone = 0;
      if (/[0-5]/.test(chunk[chunk.length - 1])) {
        tone = Number(chunk[chunk.length - 1]);
        chunk = chunk.slice(0, -1);
        if (tone === 5) tone = 0;
      } else {
        tone = marks.slice(pos, end).find(t => t) || 0;
      }
      out.push([chunk, tone]);
      pos = end;
    }
    return out;
  }

  function checkPinyin(given, expected) {
    const g = syllables(given), e = syllables(expected);
    if (!e.length || g.length !== e.length) return false;
    return e.every((x, i) => g[i][0] === x[0] && g[i][1] === x[1]);
  }

  /*
   * segment(zh, dict) -> builder tiles covering the WHOLE sentence (
   * tiles used to be only the phrase's own `words`, so most of the sentence was
   * missing). Punctuation and spaces are dropped; a run of Latin letters/digits
   * (e.g. "kopi C kosong") is one tile; otherwise the longest hanzi run found in
   * dict wins (up to the longest dict entry); an unknown hanzi becomes a one-char tile with no
   * pinyin. dict: {hanzi: {pinyin, english}}.
   */
  const PUNCT = /[\s\u3000-\u303f\uff00-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65!-\/:-@\[-`{-~\u2026\u2014]/;
  const LATIN = /[A-Za-z0-9]/;
  function segment(zh, dict) {
    const out = [];
    let i = 0;
    const s = String(zh || "");
    const maxLen = Math.max(1, ...Object.keys(dict).map(k => k.length));
    while (i < s.length) {
      const c = s[i];
      if (LATIN.test(c)) {
        let j = i;
        while (j < s.length && (LATIN.test(s[j]) || (s[j] === " " && LATIN.test(s[j + 1] || "")))) j++;
        const t = s.slice(i, j);
        out.push({hanzi: t, pinyin: t, english: ""});
        i = j; continue;
      }
      if (PUNCT.test(c)) { i++; continue; }
      let hit = null;
      for (let n = Math.min(maxLen, s.length - i); n >= 1; n--) {
        const t = s.slice(i, i + n);
        if (dict[t]) { hit = {hanzi: t, pinyin: dict[t].pinyin || "", english: dict[t].english || ""}; break; }
      }
      if (!hit) hit = {hanzi: c, pinyin: "", english: ""};
      out.push(hit);
      i += hit.hanzi.length;
    }
    return out;
  }

  const api = {syllables, checkPinyin, segment};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.MandarinAnswer = api;
})(typeof self !== "undefined" ? self : this);
