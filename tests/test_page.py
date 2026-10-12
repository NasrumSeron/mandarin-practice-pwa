"""
test_page.py - the static page, the nginx config and the JS answer checker,
checked without a browser. Run from the repo root:

    python3 -m unittest discover -s tests

(needs node for the JS checks)

What these guard against:
  * a call to an /api/ path nginx does not proxy;
  * a $("some-id") lookup for an element that no longer exists;
  * a service-worker shell entry the disk does not have (addAll() rejects as a
    unit, so one wrong path silently means no offline at all);
  * answer.js drifting from api/app/answer.py (both run tests/answer_cases.json).
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent
REPO = ROOT.parent
WEB = REPO / "web"
NGX = WEB / "nginx"

NGINX = (NGX / "mandarin.conf").read_text()
SNIPPETS = {p.name: p.read_text() for p in (NGX / "snippets").iterdir()}
COMPOSE = (REPO / "api" / "docker-compose.yml").read_text()


def server_block(host: str) -> str:
    """The one server{} block for a hostname."""
    i = NGINX.index(f"server_name {host};")
    start = NGINX.rindex("server {", 0, i)
    depth, j = 0, start
    while True:
        if NGINX[j] == "{": depth += 1
        elif NGINX[j] == "}":
            depth -= 1
            if depth == 0: return NGINX[start:j + 1]
        j += 1


def expand(block: str) -> str:
    """A server block with its snippet includes inlined (two levels deep)."""
    out = block
    for _ in range(2):
        for name, body in SNIPPETS.items():
            out = out.replace(f"include /etc/nginx/snippets/{name};", body)
    return out


MAND_BLOCK = expand(server_block("mandarin.example.com"))
MAND_RAW = server_block("mandarin.example.com")
MAND_JS = (WEB / "app.js").read_text()
MAND_HTML = (WEB / "index.html").read_text()
MAND_SW = (WEB / "sw.js").read_text()


class SyntaxTest(unittest.TestCase):
    def test_all_javascript_parses(self):
        for name in ("app.js", "sw.js", "answer.js"):
            with self.subTest(file=name):
                r = subprocess.run(["node", "--check", str(WEB / name)],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)


class IconTest(unittest.TestCase):
    def test_referenced_common_files_exist(self):
        refs = re.findall(r'"(/common/[^"]+)"', MAND_HTML + (WEB / "manifest.webmanifest").read_text())
        self.assertTrue(refs)
        for ref in refs:
            with self.subTest(ref=ref):
                self.assertTrue((WEB / ref.lstrip("/")).exists())


class MandarinNginxTest(unittest.TestCase):
    def test_server_block_exists_on_8080(self):
        self.assertIn("server_name mandarin.example.com;", NGINX)
        self.assertIn("listen 8080;", MAND_RAW)
        self.assertIn("root /srv/mandarin;", MAND_RAW)

    def test_no_literal_hostname_in_any_proxy_pass(self):
        """A literal hostname is resolved once at load and cached forever."""
        for m in re.finditer(r"proxy_pass\s+http://([^:;/\s]+)", NGINX):
            with self.subTest(upstream=m.group(1)):
                self.assertTrue(m.group(1).startswith("$"),
                                f"proxy_pass to literal host {m.group(1)}")

    def test_resolver_is_in_scope_of_the_mandarin_proxy(self):
        self.assertIn("resolver 127.0.0.11", MAND_BLOCK)
        self.assertIn("set $mandarin mandarin-api;", MAND_RAW)
        self.assertIn("proxy_pass http://$mandarin:8080;", MAND_RAW)

    def test_prefix_is_stripped(self):
        self.assertIn("location /api/mandarin/ {", MAND_RAW)
        self.assertIn("rewrite ^/api/mandarin/(.*)$ /$1 break;", MAND_RAW)

    def test_identity_header_is_forwarded(self):
        self.assertEqual(MAND_RAW.count("proxy_pass "),
                         MAND_RAW.count("include /etc/nginx/snippets/proxy.conf;"))
        self.assertIn("Cf-Access-Authenticated-User-Email", MAND_BLOCK)

    def test_mandarin_reaches_only_its_own_api(self):
        self.assertEqual(re.findall(r"location\s+(?:=\s+)?(/api/[^\s{]*)", MAND_BLOCK),
                         ["/api/mandarin/"])

    def test_static_snippet_is_included_like_the_others(self):
        self.assertIn("include /etc/nginx/snippets/app.conf;", MAND_RAW)

    def test_no_published_ports(self):
        self.assertNotRegex(COMPOSE, r"(?m)^\s*ports:")


class MandarinPageTest(unittest.TestCase):
    def test_service_worker_never_caches_api(self):
        self.assertIn('url.pathname.startsWith("/api/")', MAND_SW)
        self.assertLess(MAND_SW.index('startsWith("/api/")'), MAND_SW.index("respondWith"))
        shell = json.loads(re.search(r"const SHELL = (\[.*?\]);", MAND_SW, re.S).group(1))
        self.assertFalse([p for p in shell if "/api/" in p])
        self.assertNotIn("/api/mandarin", MAND_SW)

    def test_cache_name(self):
        self.assertIn('const CACHE = "mandarin-v1.1.0";', MAND_SW)

    def test_old_caches_deleted_and_same_origin_only(self):
        self.assertIn("caches.delete(k)", MAND_SW)
        self.assertIn("url.origin !== self.location.origin", MAND_SW)

    def test_every_shell_entry_exists(self):
        shell = json.loads(re.search(r"const SHELL = (\[.*?\]);", MAND_SW, re.S).group(1))
        for path in shell:
            if path != "/":
                with self.subTest(path=path):
                    self.assertTrue((WEB / path.lstrip("/")).exists())

    def test_html_local_references_exist(self):
        for ref in re.findall(r'(?:src|href)="([^"]+)"', MAND_HTML):
            if ref.startswith("/common/"):      # served by app.conf's alias
                continue
            with self.subTest(ref=ref):
                self.assertTrue((WEB / ref.lstrip("/")).exists(), ref)

    def test_nothing_from_the_internet(self):
        for name in ("index.html", "app.js", "style.css", "sw.js", "answer.js",
                     "manifest.webmanifest"):
            with self.subTest(file=name):
                self.assertNotRegex((WEB / name).read_text(),
                                    r"""(?:src=|href=|url\(|import\s|fetch\()\s*["']?https?://""")
        self.assertNotIn("@import", (WEB / "style.css").read_text())

    def test_all_content_comes_from_the_api(self):
        """No Mandarin text in the page code: add content by editing bank.yaml."""
        for name in ("index.html", "app.js", "sw.js", "style.css"):
            text = (WEB / name).read_text()
            if name == "index.html":
                text = text.replace("汉", "")     # the hanzi-toggle button label
            with self.subTest(file=name):
                self.assertNotRegex(text, r"[\u4e00-\u9fff]")
        self.assertIn('"/api/mandarin"', MAND_JS)
        self.assertIn('/bank', MAND_JS)

    def test_every_call_is_proxied(self):
        paths = set(re.findall(r'api\("(/[a-z]+)"', MAND_JS)) | set(re.findall(r'API \+ "(/[a-z]+)"', MAND_JS))
        self.assertTrue({"/whoami", "/bank", "/progress"} <= paths, paths)
        self.assertIn('const API = "/api/mandarin";', MAND_JS)

    def test_403_is_a_refusal_not_an_outage(self):
        self.assertIn("r.status === 403", MAND_JS)
        self.assertIn("Not allowed for this account", MAND_JS)
        self.assertIn("/whoami", MAND_JS)

    def test_bank_error_banner_and_listen_fallback(self):
        self.assertIn("bank_error", MAND_JS)
        self.assertIn("Listen needs a Chinese voice on this device", MAND_JS)

    def test_ear_and_sg_are_rendered(self):
        self.assertIn("you heard: ", MAND_JS)
        self.assertIn("it is: ", MAND_JS)
        self.assertIn('"SG"', MAND_JS)

    def test_bank_text_is_never_put_in_innerhtml(self):
        self.assertNotIn("innerHTML", MAND_JS)

    def test_manifest(self):
        m = json.loads((WEB / "manifest.webmanifest").read_text())
        self.assertEqual((m["scope"], m["start_url"]), ("/", "/"))

    def test_html_ids_unique_and_looked_up_ids_exist(self):
        ids = re.findall(r'id="([^"]+)"', MAND_HTML)
        self.assertEqual(len(ids), len(set(ids)))
        for used in set(re.findall(r'\$\("([^"]+)"\)', MAND_JS)):
            with self.subTest(id=used):
                self.assertIn(used, ids)


class MandarinAnswerParityTest(unittest.TestCase):
    """answer.js must agree with answer.py: both run the same shared cases."""

    CASES = ROOT / "answer_cases.json"
    API_CASES = REPO / "api" / "tests" / "answer_cases.json"

    def test_case_files_are_identical(self):
        self.assertEqual(self.CASES.read_text(), self.API_CASES.read_text())

    def test_js_checker_passes_shared_cases(self):
        script = (
            "const a=require(process.argv[1]);const c=JSON.parse(require('fs').readFileSync(process.argv[2],'utf8'));"
            "const bad=c.filter(([g,e,w])=>a.checkPinyin(g,e)!==w);"
            "if(bad.length){console.log(JSON.stringify(bad));process.exit(1)}")
        r = subprocess.run(["node", "-e", script, str(WEB / "answer.js"), str(self.CASES)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)



class MandarinBuilderSegmentTest(unittest.TestCase):
    """Builder tiles must cover the WHOLE sentence, not just the phrase's own words."""
    SCRIPT = (
        "const a=require(process.argv[1]);"
        "const d={'你好':{pinyin:'nǐ hǎo'},'我':{pinyin:'wǒ'},'要':{pinyin:'yào'},'去':{pinyin:'qù'},"
        "'这里':{pinyin:'zhèlǐ'},'你':{pinyin:'nǐ'},'可以':{pinyin:'kěyǐ'},'帮':{pinyin:'bāng'},'吗':{pinyin:'ma'},"
        "'国家图书馆':{pinyin:'Guójiā Túshūguǎn'},'一':{pinyin:'yī'},'杯':{pinyin:'bēi'}};"
        "const t1=a.segment('你好，我要去这里。你可以帮我吗？',d).map(x=>x.hanzi).join('|');"
        "const t2=a.segment('我在国家图书馆',d).map(x=>x.hanzi).join('|');"
        "const t3=a.segment('一杯 kopi C kosong 冰。',d).map(x=>x.hanzi+':'+x.pinyin).join('|');"
        "console.log(JSON.stringify([t1,t2,t3]));")

    def test_segment(self):
        r = subprocess.run(["node", "-e", self.SCRIPT, str(WEB / "answer.js")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        t1, t2, t3 = json.loads(r.stdout)
        self.assertEqual(t1, "你好|我|要|去|这里|你|可以|帮|我|吗")          # whole sentence, both 我 and 你 kept
        self.assertEqual(t2, "我|在|国家图书馆")                          # long words beat single chars
        self.assertEqual(t3, "一:yī|杯:bēi|kopi C kosong:kopi C kosong|冰:")  # Latin run = one tile; unknown = no pinyin

    def test_builder_uses_segment_and_compares_text(self):
        self.assertIn("MandarinAnswer.segment(p.zh, dict)", MAND_JS)
        self.assertIn("t.w.hanzi === want[k].hanzi", MAND_JS)



class MandarinWordBankClozeTest(unittest.TestCase):
    """Word bank tab (sorted by English, type filter, search) + pinyin line in Cloze."""

    def test_word_bank_mode_wired(self):
        self.assertIn('["bank", "Word bank"]', MAND_JS)
        self.assertIn("bank: modeBank", MAND_JS)
        self.assertIn("localeCompare(sortKey(b.english))", MAND_JS)
        self.assertIn('type: "search"', MAND_JS)

    def test_cloze_blanks_pinyin_including_tone_changes(self):
        fn = re.search(r"function blankPinyin[\s\S]*?\n}\n", MAND_JS).group(0)
        script = fn + (
            "console.log(JSON.stringify([blankPinyin('Nǐ hǎo, wǒ yào qù zhèlǐ. Nǐ kěyǐ bāng wǒ ma?','kěyǐ'),"
            "blankPinyin('Yì bēi kopi C kosong bīng.','yī'),"
            "blankPinyin('Wǒ bú shì Huárén.','bù'),"
            "blankPinyin('Nǐ hǎo','xyz')]));")
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        a, b, c, d = json.loads(r.stdout)
        self.assertEqual(a, "Nǐ hǎo, wǒ yào qù zhèlǐ. Nǐ ＿＿ bāng wǒ ma?")
        self.assertEqual(b, "＿＿ bēi kopi C kosong bīng.")
        self.assertEqual(c, "Wǒ ＿＿ shì Huárén.")
        self.assertIsNone(d)                      # never fall back to showing the answer



class MandarinTopicsTest(unittest.TestCase):
    """Drills follow picked topics; word bank filters by topic."""

    def test_topics_picker_and_drill_filter(self):
        self.assertIn('id="topics"', MAND_HTML)
        self.assertIn('const DEFAULT_TOPICS = ["my-phrases"];', MAND_JS)
        for mode in ("pick(drillPhrases()", "drillWords()"):
            self.assertIn(mode, MAND_JS)
        self.assertIn('localStorage.setItem("mandarin-topics"', MAND_JS)

    def test_word_bank_filters_by_topic_not_type(self):
        self.assertIn('h("select", {"aria-label": "Topic"})', MAND_JS)
        self.assertNotIn("w.type", MAND_JS)

    def test_builder_pinyin_from_sentence_skips_latin(self):
        self.assertIn("!latin.has(x)", MAND_JS)



if __name__ == "__main__":
    unittest.main(verbosity=1)
