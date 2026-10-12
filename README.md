# Mandarin practice PWA

A small, private, phone-first web app for practising Mandarin. All content
lives in one `bank.yaml` file: add a phrase to the file and it shows up in
every drill, with no code change.

It is built for a handful of known users behind an authenticating reverse
proxy, not for the open internet.

## Features

- **Word cards** - hanzi / pinyin / English, each independently hideable.
- **Word bank** - every word derived from the phrases, sorted by English,
  with a topic filter and search.
- **Sentence builder** - rebuild a sentence from tiles.
- **Cloze** - fill the blanked word in a sentence (the pinyin line is blanked too).
- **Typed full sentence** - type the pinyin; tone marks or tone numbers both
  work (`xie4xie4` equals `xièxiè`), and `v`/`u:` mean `ü`.
- **Listen** - plays the sentence with the browser's `zh-CN` speech voice; if
  the device has none, the page says so.
- **Leitner review** - five boxes, intervals configurable in `bank.yaml`
  (default 1, 2, 4, 8, 16 days). A correct answer promotes, a wrong one resets
  to box 1.
- **Per-user progress** - stored server-side, keyed by the authenticated email.
- **Lessons** - optional grammar notes linked to the phrases that use them.
- Installable PWA with an offline shell. API responses are never cached.

## Architecture

```
phone --> auth proxy (sets identity header) --> nginx --+--> static page (web/)
                                                        '--> /api/mandarin/ --> mandarin-api --> SQLite
                                                                                      '--> bank.yaml (read-only mount)
```

- **Page** (`web/`): vanilla JS, no framework, no CDN, no third-party requests.
  Bank text is only ever inserted as text nodes, never as markup.
- **API** (`api/`): Python standard library `http.server` plus PyYAML. SQLite
  for progress. `bank.yaml` is re-read when its mtime changes; if the new file
  is broken, the last good bank keeps being served and the error is reported.
- **Identity**: the API reads the user's email from the
  `Cf-Access-Authenticated-User-Email` header (what Cloudflare Access sends;
  any auth proxy that sets the same header works). The email is taken from
  that header only, never from a body or query string.

### Why there are no published ports

That header is just a request header: anyone who can reach the API directly
can set it to whatever they like. It is trustworthy only because the *only*
thing that can reach nginx is the auth proxy, and the *only* thing that can
reach the API is nginx. Neither container publishes a port
(`docker-compose.yml` has no `ports:`; a test checks this for the compose file
in the repo). If you publish a port, or put another service on the same
Docker network that you do not trust, the header stops meaning anything. This
is a network guarantee, not a cryptographic one. Verifying the
`Cf-Access-Jwt-Assertion` JWT against the provider's keys would be the
stronger option and is not implemented.

## Security notes

- **Fail-closed allowlist**: `MANDARIN_ALLOWED_EMAILS` is a comma-separated
  list. If it is empty, every request to `/bank` and `/progress` is refused
  with 403. Only `/health` and `/whoami` are open; `/whoami` returns just
  `{"email_known": bool}` and never echoes an email.
- **Default-deny routing**: anything not explicitly open needs a known email.
- **Field allowlist**: `POST /progress` reads only `item_id`, `mode` and
  `correct`. Anything else in the body (box, due date, email) is ignored.
- **Input validation**: `item_id` must match `[A-Za-z0-9_.:-]{1,64}`, `mode`
  must be one of a fixed set, `correct` must be a real boolean, bodies are
  capped at 2 KB and must be a JSON object.
- **Rate limit on writes**: N progress writes per rolling minute per user
  (`MANDARIN_WRITE_PER_MIN`, default 60); reads are not limited.
- **Per-user isolation**: every progress query is scoped to the caller's email.
- **Non-root container**; the bank is mounted read-only; the page sends
  `nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`.
- The service worker never intercepts `/api/`, so a cached identity cannot happen.

## bank.yaml

See [`sample/bank.yaml`](sample/bank.yaml) for a complete example. Each phrase
needs `id` (unique), `en`, `zh` and `py`; the rest is optional.

```yaml
topics:                         # optional; drives the topic picker and word-bank filter
  - {id: food, en: "Food and drink", zh: "饮食", chapter: "Out and about"}
grammar:                        # optional; shown under Lessons
  - {id: g-yao, type: pattern, pattern: "我要 + noun", rule: "...", example: "..."}
phrases:
  - id: p-tea
    en: "I want a cup of tea."
    zh: "我要一杯茶。"
    py: "Wǒ yào yì bēi chá."    # one syllable per hanzi, tone marks
    tags: [food]                # topic ids
    check: verified             # verified | pending | flagged
    words: ["我|wǒ|I", "要|yào|want", "茶|chá|tea"]   # hanzi|pinyin|English
    grammar: [g-yao]
```

- `check: flagged` phrases are never served; `pending` ones are served only
  while `MANDARIN_ALLOW_PENDING=1`.
- Words are derived from each phrase's `words` list; the same hanzi in several
  phrases becomes one word with merged meanings.
- Optional per-phrase fields: `ear` (what a learner typically mishears, shown
  as "you heard / it is"), `sg: true` (shows an "SG" badge for regional usage),
  `kind` and `status` (free-form; stored and ignored by the code).
- The page's built-in default topic id is `my-phrases`; include a topic with
  that id (as the sample does) so a fresh install has a sensible starting set.
- Optional `word_types: {hanzi: type}` and `meta.leitner_days`.
- A malformed bank never takes the app down: the last good copy is served.

## Run the tests

```
# API (stdlib unittest + PyYAML)
cd api && python3 -m unittest discover -s tests -t .

# page, nginx config and JS answer checker (needs node)
python3 -m unittest discover -s tests
```

The pinyin answer checker exists twice (`api/app/answer.py` and
`web/answer.js`); both are run against `tests/answer_cases.json` and the test
suite checks the two copies of that file are identical.

## Deploy sketch

1. `cd api && cp .env.example .env`, then set `MANDARIN_ALLOWED_EMAILS`.
2. Create a `bank/` folder next to `docker-compose.yml` containing `bank.yaml`
   (or edit the volume to point at any folder that holds it, e.g. a synced
   cloud-drive folder). Start from `sample/bank.yaml`.
3. Create the shared Docker network once (`docker network create proxy-net`)
   and run `docker compose up -d --build`. The API listens on 8080 inside the
   network only.
4. Run nginx on the same network with `web/` mounted at `/srv/mandarin`,
   `web/common` at `/srv/common`, `web/nginx/snippets` at
   `/etc/nginx/snippets`, and `web/nginx/mandarin.conf` included in `http{}`
   (change `server_name`). The snippets resolve the upstream per request so
   a rebuilt API container is picked up, and forward the identity header.
5. Put an auth proxy in front of nginx that authenticates users and sets
   `Cf-Access-Authenticated-User-Email`, and make it the only way in.
   Because the page and API share an origin, no CORS configuration is needed.

`web/common/` holds placeholder app icons; replace them with your own.

## License

MIT, see [LICENSE](LICENSE).
