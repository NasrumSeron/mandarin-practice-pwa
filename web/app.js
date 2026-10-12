/*
 * app.js — Mandarin practice. Vanilla JS, no CDN.
 *
 * ALL content (phrases, words, grammar) arrives from /api/mandarin/bank.
 * Nothing in this file or index.html is Mandarin text; to add content you
 * edit bank.yaml and nothing else.
 *
 * Every string from the bank goes into the page as a text node (h() below),
 * never as markup.
 */
"use strict";

const API = "/api/mandarin";
const MODES = [
  ["words", "Words"], ["bank", "Word bank"], ["builder", "Builder"], ["cloze", "Cloze"], ["full", "Sentence"],
  ["listen", "Listen"], ["review", "Review"], ["lessons", "Lessons"],
];
const { checkPinyin } = MandarinAnswer;

const S = {
  bank: null, progress: [], mode: "words", last: null, right: 0, total: 0,
  show: {hanzi: true, pinyin: true, english: true}, voice: null, voiceChecked: false,
};
const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------ helpers
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) el.append(k);
  return el;
}
const rnd = (n) => Math.floor(Math.random() * n);
const shuffle = (a) => { a = a.slice(); for (let i = a.length - 1; i > 0; i--) { const j = rnd(i + 1); [a[i], a[j]] = [a[j], a[i]]; } return a; };
const today = () => new Date().toISOString().slice(0, 10);   // UTC, like the server

function loadPrefs() {
  try { Object.assign(S.show, JSON.parse(localStorage.getItem("mandarin-show") || "{}")); } catch (e) {}
}
function savePrefs() { try { localStorage.setItem("mandarin-show", JSON.stringify(S.show)); } catch (e) {} }

// ------------------------------------------------------------ topics
// Drills use only the topics picked here; Word bank, Review and Lessons ignore it.
const DEFAULT_TOPICS = ["my-phrases"];
function loadTopics() {
  let t = null;
  try { t = JSON.parse(localStorage.getItem("mandarin-topics") || "null"); } catch (e) {}
  const known = new Set((S.bank.topics || []).map(x => x.id));
  t = (Array.isArray(t) ? t : DEFAULT_TOPICS).filter(x => known.has(x));
  S.topics = new Set(t.length ? t : DEFAULT_TOPICS.filter(x => known.has(x)));
}
function saveTopics() { try { localStorage.setItem("mandarin-topics", JSON.stringify([...S.topics])); } catch (e) {} }
const inTopics = (tags) => !S.topics || !S.topics.size || (tags || []).some(t => S.topics.has(t));   // no topics in bank = all
function drillPhrases() { return S.bank.phrases.filter(p => inTopics(p.tags)); }
function drillWords() { return S.bank.words.filter(w => inTopics(w.topics)); }
function topicName(id) { const t = (S.bank.topics || []).find(x => x.id === id); return t ? t.en : id; }

function paintTopics() {
  const box = $("topics");
  box.hidden = !(S.bank.topics || []).length;
  if (box.hidden) return;
  const all = S.bank.topics || [];
  const chapters = [...new Set(all.map(t => t.chapter))];
  const label = [...S.topics].slice(0, 2).map(topicName).join(", ") + (S.topics.size > 2 ? ` +${S.topics.size - 2}` : "");
  const changed = () => { saveTopics(); S.last = null; paintTopics(); render(); };
  const groups = chapters.map(ch => {
    const ts = all.filter(t => t.chapter === ch);
    const on = ts.filter(t => S.topics.has(t.id)).length;
    const toggleAll = h("button", {type: "button", class: "chip", "aria-pressed": String(on === ts.length),
      onclick: () => { const add = on !== ts.length; ts.forEach(t => add ? S.topics.add(t.id) : S.topics.delete(t.id));
        if (!S.topics.size) DEFAULT_TOPICS.forEach(x => S.topics.add(x)); changed(); }}, `${ch} (${on}/${ts.length})`);
    return h("div", {class: "topic-group"}, toggleAll, h("div", {class: "row"}, ts.map(t =>
      h("button", {type: "button", class: "chip small", "aria-pressed": String(S.topics.has(t.id)),
        onclick: () => { S.topics.has(t.id) ? S.topics.delete(t.id) : S.topics.add(t.id);
          if (!S.topics.size) DEFAULT_TOPICS.forEach(x => S.topics.add(x)); changed(); }}, t.en))));
  });
  const open = box.open;
  box.replaceChildren(h("summary", {}, `Drilling: ${label} · ${drillPhrases().length} items`), ...groups);
  box.open = open;
}

async function api(path, opts) {
  const r = await fetch(API + path, opts);
  if (r.status === 403) { const e = new Error("refused"); e.refused = true; throw e; }
  if (!r.ok) throw new Error("http " + r.status);
  return r.json();
}

function status(msg, cls) {
  const el = $("status");
  el.hidden = !msg; el.textContent = msg || ""; el.className = "status " + (cls || "");
}

// ------------------------------------------------------------ item choice
function progressFor(id) { return S.progress.filter(p => p.item_id === id); }
function dueRank(id) {
  const rows = progressFor(id);
  return rows.length ? rows.map(r => r.due).sort()[0] : "0000";        // never seen = most due
}
function pick(items, idOf) {
  if (!items.length) return null;
  const ranked = shuffle(items).sort((a, b) => dueRank(idOf(a)) < dueRank(idOf(b)) ? -1 : 1).slice(0, 8);
  const fresh = ranked.filter(x => idOf(x) !== S.last);
  const choice = (fresh.length ? fresh : ranked)[rnd((fresh.length ? fresh : ranked).length)];
  S.last = idOf(choice);
  return choice;
}

// Builder/cloze segmentation: which of a phrase's words actually occur in zh,
// in sentence order, without overlapping (longer match wins).
function wordsInSentence(p) {
  const found = [];
  const ws = (p.words || []).map(s => String(s).split("|"))
    .filter(x => x.length >= 3).map(x => ({hanzi: x[0].trim(), pinyin: x[1].trim(), english: x.slice(2).join("|").trim()}))
    .sort((a, b) => b.hanzi.length - a.hanzi.length);
  const taken = new Array(p.zh.length).fill(false);
  for (const w of ws) {
    const i = w.hanzi ? p.zh.indexOf(w.hanzi) : -1;
    if (i < 0 || taken.slice(i, i + w.hanzi.length).some(Boolean)) continue;
    taken.fill(true, i, i + w.hanzi.length);
    found.push({...w, at: i});
  }
  return found.sort((a, b) => a.at - b.at);
}

// ------------------------------------------------------------ rendering bits
function phrasePieces(p, hide) {
  hide = hide || [];
  return [
    S.show.hanzi && !hide.includes("hanzi") && h("div", {class: "hanzi"}, p.zh, p.sg && h("span", {class: "badge"}, "SG")),
    S.show.pinyin && !hide.includes("pinyin") && h("div", {class: "py"}, p.py),
    S.show.english && !hide.includes("english") && h("div", {class: "en"}, p.en),
  ];
}
function earLine(p) {
  return p.ear ? h("div", {class: "ear"}, "you heard: ", p.ear, " / it is: ", p.py) : null;
}
function feedback(ok, ...detail) {
  return h("div", {class: "fb " + (ok ? "ok" : "bad")}, h("strong", {}, ok ? "Correct" : "Not quite"), ...detail);
}

function record(itemId, mode, correct) {
  S.total++; if (correct) S.right++;
  const m = mode === "review" ? "review" : mode;
  fetch(API + "/progress", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({item_id: itemId, mode: m, correct})})
    .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
    .then(row => {
      S.progress = S.progress.filter(p => !(p.item_id === row.item_id && p.mode === row.mode));
      S.progress.push(row);
    })
    .catch(() => status("Couldn't save that answer", ""));
}

function nextBtn() { return h("button", {class: "primary", onclick: () => render()}, "Next"); }
function scoreLine() { return h("div", {class: "score"}, `${S.right} / ${S.total} this session`); }

// ------------------------------------------------------------ modes
// Each mode builds a card for an item, and returns it. `forced` is a
// {mode,id} from the Review queue.

function modeWords(forced) {
  const words = forced ? S.bank.words : drillWords();
  const w = forced ? S.bank.words.find(x => x.id === forced.id) : pick(words, x => x.id);
  if (!w) return empty("No words yet.");
  const dir = forced ? forced.mode : (Math.random() < 0.5 ? "word_en" : "en_word");
  const fb = h("div");
  const card = h("div", {class: "card"});
  let done = false;
  const finish = (ok, shown) => {
    if (done) return; done = true;
    record(w.id, dir, ok);
    fb.replaceChildren(feedback(ok, " ", h("span", {class: "hanzi"}, w.hanzi), " ", h("span", {class: "py"}, w.pinyin), " ", h("span", {class: "en"}, w.english)), nextBtn());
    card.querySelectorAll("button.opt,input,button.check").forEach(b => (b.disabled = true));
  };
  if (dir === "word_en") {
    const others = shuffle((words.length >= 4 ? words : S.bank.words).filter(x => x.english !== w.english)).slice(0, 3);
    const opts = shuffle([w, ...others]);
    card.append(
      h("p", {class: "q"}, "What does this mean?"),
      S.show.hanzi && h("div", {class: "hanzi"}, w.hanzi),
      h("div", {class: "py"}, w.pinyin),
      h("div", {class: "row"}, opts.map(o => h("button", {class: "chip opt", onclick: () => finish(o === w)}, o.english))), fb);
  } else {
    const input = h("input", {type: "text", autocapitalize: "off", autocomplete: "off", autocorrect: "off", placeholder: "pinyin, e.g. xie4xie4"});
    const go = () => finish(checkPinyin(input.value, w.pinyin));
    input.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
    card.append(h("p", {class: "q"}, "Type the pinyin for:"), h("div", {class: "hanzi"}, w.english),
      S.show.hanzi && h("div", {class: "en"}, w.hanzi), input, h("button", {class: "primary check", onclick: go}, "Check"), fb);
  }
  return card;
}

// Builder tiles cover the whole sentence: this phrase's own words first, then the
// bank-wide word list (MandarinAnswer.segment).
function builderTiles(p) {
  const dict = {};
  for (const w of S.bank.words || []) if (w.hanzi && !dict[w.hanzi]) dict[w.hanzi] = w;
  for (const w of wordsInSentence(p)) dict[w.hanzi] = w;     // the phrase's own pinyin wins
  const tiles = MandarinAnswer.segment(p.zh, dict);
  // Pinyin is one syllable per hanzi, so every tile takes its pinyin from the sentence itself
  // (right tone in context, and no tile is left without pinyin).
  const latin = new Set(String(p.zh).match(/[A-Za-z]+/g) || []);      // e.g. "kopi C kosong" is not pinyin
  const syl = String(p.py || "").split(/[\s,.?!:;"'“”‘’()（）\/…-]+/).filter(x => x && !/\d/.test(x) && !latin.has(x));
  const hz = [...p.zh].filter(c => /[\u4e00-\u9fff]/.test(c));
  if (syl.length === hz.length) {
    let k = 0;
    for (const t of tiles) {
      const n = [...t.hanzi].filter(c => /[\u4e00-\u9fff]/.test(c)).length;
      if (n) { t.pinyin = syl.slice(k, k + n).join(" "); k += n; }
    }
  }
  return tiles;
}

function modeBuilder(forced) {
  const usable = drillPhrases().filter(p => { const n = builderTiles(p).length; return n >= 2 && n <= 16; });
  const p = forced ? S.bank.phrases.find(x => x.id === forced.id) : pick(usable, x => x.id);
  if (!p) return empty("No phrases usable for the builder yet.");
  const want = builderTiles(p);
  const tiles = shuffle(want.map((w, i) => ({w, i})));
  const chosen = [];
  const zone = h("div", {class: "row answer-zone"});
  const pool = h("div", {class: "row"});
  const fb = h("div");
  const label = (w) => h("span", {}, S.show.hanzi || !S.show.pinyin || !w.pinyin ? w.hanzi : w.pinyin,
    S.show.hanzi && S.show.pinyin && w.pinyin && w.pinyin !== w.hanzi && h("small", {}, w.pinyin));
  const paint = () => {
    zone.replaceChildren(...chosen.map(t => h("button", {class: "tile", onclick: () => { chosen.splice(chosen.indexOf(t), 1); paint(); }}, label(t.w))));
    pool.replaceChildren(...tiles.map(t => h("button", {class: "tile", disabled: chosen.includes(t), onclick: () => { chosen.push(t); paint(); }}, label(t.w))));
  };
  const check = () => {
    // compare text, not tile identity: a word used twice can go in either slot
    const ok = chosen.length === want.length && chosen.every((t, k) => t.w.hanzi === want[k].hanzi);
    record(p.id, "builder", ok);
    fb.replaceChildren(feedback(ok, ...phrasePieces(p)), earLine(p), nextBtn());
    card.querySelectorAll("button.tile,button.check").forEach(b => (b.disabled = true));
  };
  paint();
  const card = h("div", {class: "card"}, h("p", {class: "q"}, "Put the words in order:"),
    h("div", {class: "en"}, p.en), zone, pool,
    h("button", {class: "primary check", onclick: check}, "Check"), fb);
  return card;
}

// Blank the target word in the sentence's pinyin too (learners often read pinyin, not
// characters). Matches ignoring case and spaces inside the word; null if not found,
// so the answer is never shown by accident.
function blankPinyin(py, target) {
  // 1st pass exact (tones included); 2nd pass ignores tone marks, for tone changes in a
  // sentence (yī -> yì bēi, bù -> bú shì). Same length per char, so indices line up.
  const src = String(py || "").normalize("NFC");
  const toneless = (x) => [...x].map(c => c.normalize("NFD")[0]).join("");
  for (const strip of [false, true]) {
    const low = strip ? toneless(src.toLowerCase()) : src.toLowerCase();
    let want = String(target || "").normalize("NFC").toLowerCase().replace(/\s+/g, "");
    if (strip) want = toneless(want);
    if (!want) return null;
    for (let i = 0; i < low.length; i++) {
      if (i > 0 && /[a-zà-ǜ]/i.test(low[i - 1])) continue;       // start on a word boundary
      let j = i, k = 0;
      while (j < low.length && k < want.length) {
        if (low[j] === " ") { j++; continue; }
        if (low[j] !== want[k]) break;
        j++; k++;
      }
      if (k === want.length && !/[a-zà-ǜ]/i.test(low[j] || " ")) return src.slice(0, i) + "＿＿" + src.slice(j);
    }
  }
  return null;
}

function modeCloze(forced) {
  const usable = drillPhrases().filter(p => wordsInSentence(p).some(w => w.hanzi.length < p.zh.replace(/[。！？，.!?]/g, "").length));
  const p = forced ? S.bank.phrases.find(x => x.id === forced.id) : pick(usable, x => x.id);
  if (!p) return empty("No phrases usable for cloze yet.");
  const ws0 = wordsInSentence(p);
  const ws = ws0.filter(w => w.hanzi.length < p.zh.replace(/[。！？，.!?]/g, "").length);
  const target = (ws.length ? ws : ws0)[rnd((ws.length ? ws : ws0).length)];
  const blanked = p.zh.slice(0, target.at) + "＿＿" + p.zh.slice(target.at + target.hanzi.length);
  const blankedPy = blankPinyin(p.py, target.pinyin);
  const pool = S.bank.words.filter(x => x.pinyin !== target.pinyin);
  const opts = shuffle([target.pinyin, ...shuffle(pool).slice(0, 3).map(x => x.pinyin)]);
  const fb = h("div");
  const finish = (ok) => {
    record(p.id, "cloze", ok);
    fb.replaceChildren(feedback(ok, " ", h("span", {class: "hanzi"}, target.hanzi), " ", h("span", {class: "py"}, target.pinyin)), ...phrasePieces(p), earLine(p), nextBtn());
    card.querySelectorAll("button.opt,input,button.check").forEach(b => (b.disabled = true));
  };
  const input = h("input", {type: "text", autocapitalize: "off", autocomplete: "off", autocorrect: "off", placeholder: "type the pinyin"});
  const go = () => finish(checkPinyin(input.value, target.pinyin));
  input.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  const card = h("div", {class: "card"}, h("p", {class: "q"}, "Fill the blank:"),
    S.show.hanzi && h("div", {class: "hanzi"}, blanked),
    (S.show.pinyin || !S.show.hanzi) && blankedPy && h("div", {class: "py big"}, blankedPy),
    S.show.english && h("div", {class: "en"}, p.en),
    h("div", {class: "row"}, opts.map(o => h("button", {class: "chip opt", onclick: () => finish(o === target.pinyin)}, o))),
    input, h("button", {class: "primary check", onclick: go}, "Check"), fb);
  return card;
}

function modeFull(forced) {
  const p = forced ? S.bank.phrases.find(x => x.id === forced.id) : pick(drillPhrases(), x => x.id);
  if (!p) return empty("No phrases yet.");
  const fb = h("div");
  const input = h("input", {type: "text", autocapitalize: "off", autocomplete: "off", autocorrect: "off", placeholder: "pinyin, tone marks or numbers"});
  const go = () => {
    const ok = checkPinyin(input.value, p.py);
    record(p.id, "full", ok);
    fb.replaceChildren(feedback(ok, ...phrasePieces(p)), earLine(p), nextBtn());
    input.disabled = true; card.querySelector("button.check").disabled = true;
  };
  input.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  const card = h("div", {class: "card"}, h("p", {class: "q"}, "Say it in Mandarin (type the pinyin):"),
    h("div", {class: "hanzi", style: "font-size:1.3rem"}, p.en),
    S.show.hanzi && h("div", {class: "en"}, p.zh), input,
    h("button", {class: "primary check", onclick: go}, "Check"), fb);
  return card;
}

function findVoice() {
  if (!("speechSynthesis" in window)) return null;
  const vs = speechSynthesis.getVoices();
  return vs.find(v => /^zh[-_]CN/i.test(v.lang)) || vs.find(v => /^zh/i.test(v.lang)) || null;
}

function modeListen(forced) {
  if (!S.voice) return empty("Listen needs a Chinese voice on this device");
  const p = forced ? S.bank.phrases.find(x => x.id === forced.id) : pick(drillPhrases(), x => x.id);
  if (!p) return empty("No phrases yet.");
  const say = () => { speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(p.zh); u.voice = S.voice; u.lang = S.voice.lang; u.rate = 0.85; speechSynthesis.speak(u); };
  const fb = h("div");
  const input = h("input", {type: "text", autocapitalize: "off", autocomplete: "off", autocorrect: "off", placeholder: "type what you heard (pinyin)"});
  const go = () => {
    const ok = checkPinyin(input.value, p.py);
    record(p.id, "listen", ok);
    fb.replaceChildren(feedback(ok, ...phrasePieces(p)), earLine(p), nextBtn());
    input.disabled = true; card.querySelector("button.check").disabled = true;
  };
  input.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  const card = h("div", {class: "card"}, h("p", {class: "q"}, "Listen, then type the pinyin:"),
    h("button", {class: "ghost", onclick: say}, "Play"), input,
    h("button", {class: "primary check", onclick: go}, "Check"), fb);
  setTimeout(say, 150);
  return card;
}

// Word bank: every word, sorted by English, with its type; filter by type, search.
function sortKey(en) {
  const k = String(en || "").toLowerCase().replace(/^[\s'"“”‘’(….]+/, "").replace(/^to\s+/, "").trim();
  return /^\d/.test(k) ? "\uffff" + k.replace(/\s/g, "").padStart(12, "0") : k;   // numbers last, in order
}
function plain(x) { return String(x || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/\s+/g, ""); }
function modeBank() {
  const words = S.bank.words.slice().sort((a, b) => sortKey(a.english).localeCompare(sortKey(b.english)));
  if (!words.length) return empty("No words yet.");
  const list = h("div", {class: "wordlist"});
  const search = h("input", {type: "search", placeholder: "Search English or pinyin", autocapitalize: "off", autocomplete: "off", autocorrect: "off"});
  const sel = h("select", {"aria-label": "Topic"});
  const chapters = [...new Set((S.bank.topics || []).map(t => t.chapter))];
  sel.append(h("option", {value: "all"}, "All topics"),
    ...chapters.map(ch => h("optgroup", {label: ch}, (S.bank.topics || []).filter(t => t.chapter === ch)
      .map(t => h("option", {value: t.id}, t.en)))));
  if (!S.bankTopic) S.bankTopic = "all";
  sel.value = S.bankTopic;
  const paint = () => {
    const q = plain(search.value);
    const shown = words.filter(w => (S.bankTopic === "all" || (w.topics || []).includes(S.bankTopic)) &&
      (!q || plain(w.english).includes(q) || plain(w.pinyin).includes(q)));
    const cap = 400;                                  // keep the phone fast; search narrows it
    list.replaceChildren(h("div", {class: "note"}, `${shown.length} of ${words.length} words` + (shown.length > cap ? ` · showing first ${cap}, search to narrow` : "")),
      ...shown.slice(0, cap).map(w => h("div", {class: "wordrow"},
        h("div", {class: "w-en"}, w.english),
        h("div", {class: "w-py py"}, w.pinyin),
        S.show.hanzi && h("div", {class: "w-hz"}, w.hanzi),
        h("span", {class: "badge"}, topicName((w.topics || [])[0] || "")))));
  };
  search.addEventListener("input", paint);
  sel.addEventListener("change", () => { S.bankTopic = sel.value; paint(); });
  paint();
  return h("div", {class: "card"}, search, sel, list);
}

const REVIEW_RUN = {word_en: modeWords, en_word: modeWords, builder: modeBuilder, cloze: modeCloze,
  full: modeFull, listen: modeListen, review: modeFull};

function modeReview() {
  const known = new Set([...S.bank.phrases.map(p => p.id), ...S.bank.words.map(w => w.id)]);
  const due = S.progress.filter(r => r.due <= today() && known.has(r.item_id) && REVIEW_RUN[r.mode])
    .filter(r => r.mode !== "listen" || S.voice).sort((a, b) => (a.due < b.due ? -1 : 1));
  if (!due.length) return empty("Nothing due. Come back tomorrow, or practise in another mode.");
  const r = due.find(x => x.item_id !== S.last) || due[0];
  S.last = r.item_id;
  const mode = r.mode === "review" ? "full" : r.mode;
  return h("div", {}, h("div", {class: "score"}, `${due.length} due`), REVIEW_RUN[mode]({mode, id: r.item_id}));
}

function modeLessons() {
  const g = S.bank.grammar;
  if (!g.length) return empty("No lessons yet.");
  return h("div", {}, g.map(e => {
    const used = S.bank.phrases.filter(p => (p.grammar || []).includes(e.id));
    return h("details", {class: "lesson"},
      h("summary", {}, e.pattern || e.id, e.type && h("span", {class: "badge"}, e.type)),
      e.rule && h("p", {}, e.rule),
      e.example && h("p", {class: "py"}, e.example),
      used.length ? h("div", {}, h("div", {class: "note"}, "Seen in:"), used.map(p => h("div", {class: "card"}, phrasePieces(p), earLine(p)))) : null);
  }));
}

function empty(msg) { return h("div", {class: "card"}, h("p", {}, msg)); }

// ------------------------------------------------------------ shell
const RUN = {words: modeWords, bank: modeBank, builder: modeBuilder, cloze: modeCloze, full: modeFull,
  listen: modeListen, review: modeReview, lessons: modeLessons};

function render() {
  if (!S.bank) return;
  const main = $("main");
  main.replaceChildren(RUN[S.mode](), S.mode === "lessons" || S.mode === "bank" ? "" : scoreLine());
}

function paintModes() {
  const nav = $("modes");
  nav.hidden = false;
  nav.replaceChildren(...MODES.filter(([id]) => id !== "listen" || S.voice).map(([id, name]) =>
    h("button", {type: "button", "aria-pressed": String(id === S.mode), onclick: () => { S.mode = id; S.last = null; paintModes(); render(); }}, name)));
  const note = $("note");
  note.hidden = !!S.voice || !S.voiceChecked;
  note.textContent = S.voice ? "" : "Listen needs a Chinese voice on this device";
}

function paintToggles() {
  document.querySelectorAll("#toggles button").forEach(b => b.setAttribute("aria-pressed", String(S.show[b.dataset.t])));
}

function wireToggles() {
  $("toggles").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-t]");
    if (!b) return;
    S.show[b.dataset.t] = !S.show[b.dataset.t];
    savePrefs(); paintToggles(); render();
  });
}

function setupVoice() {
  const check = () => {
    S.voice = findVoice(); S.voiceChecked = true;
    if (S.bank) { if (!S.voice && S.mode === "listen") S.mode = "words"; paintModes(); }
  };
  if (!("speechSynthesis" in window)) { S.voiceChecked = true; return; }
  speechSynthesis.addEventListener("voiceschanged", check);
  check();
  setTimeout(check, 1500);               // some browsers load voices late and never fire the event
}

async function boot() {
  loadPrefs(); paintToggles(); wireToggles(); setupVoice();
  status("Loading...");
  try {
    const who = await api("/whoami");
    if (!who.email_known) return status("Not allowed for this account", "refused");
    const [bank, progress] = await Promise.all([api("/bank"), api("/progress")]);
    S.bank = bank; S.progress = progress;
  } catch (e) {
    if (e.refused) return status("Not allowed for this account", "refused");
    return status("Can't reach the Mandarin server. Check your connection and reload.");
  }
  status("");
  const b = $("banner");
  b.hidden = !S.bank.bank_error;
  b.textContent = S.bank.bank_error ? "Bank problem, using the last good copy: " + S.bank.bank_error : "";
  loadTopics(); paintTopics(); paintModes(); render();
}

boot();
