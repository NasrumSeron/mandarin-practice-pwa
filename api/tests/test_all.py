"""Offline tests for mandarin-api. Run: python3 -m unittest discover -s tests -t . -v"""
import json
import logging
import os
import pathlib
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

from app import answer, bank as bankmod
from app.identity import HEADER, Identity
from app.store import Store, next_state
from app.webapi import RateLimiter, make_server

HERE = pathlib.Path(__file__).resolve().parent
FIX = HERE / "fixtures" / "bank5.yaml"
SEED = HERE.parent.parent / "sample" / "bank.yaml"
A, B = "a@example.com", "b@example.com"


def load(path, pending=True):
    b = bankmod.Bank(str(path))
    b.refresh()
    return b


class BankTest(unittest.TestCase):
    def test_sample_bank_parses(self):
        b = load(SEED)
        self.assertIsNone(b.error)
        v, _ = b.view(True)
        self.assertGreaterEqual(len(v["phrases"]), 8)
        self.assertTrue(v["grammar"])
        self.assertTrue(v["words"])

    def test_words_dedupe_first_wins_and_track_phrases(self):
        v, _ = load(FIX).view(True)
        by = {w["hanzi"]: w for w in v["words"]}
        self.assertEqual(by["你好"]["english"], "hello; hello (dup)")   # meanings merge, first first
        self.assertEqual(by["你好"]["phrases"], ["t2", "t3"])
        self.assertEqual(by["谢谢"]["english"], "thank you; thanks (dup)")
        self.assertEqual(by["谢谢"]["phrases"], ["t1", "t5"])
        self.assertEqual(len({w["hanzi"] for w in v["words"]}), len(v["words"]))
        for w in v["words"]:
            self.assertRegex(w["id"], r"^[A-Za-z0-9_.:-]+$")

    def test_eligibility(self):
        b = load(FIX)
        ids = lambda pend: [p["id"] for p in b.view(pend)[0]["phrases"]]
        self.assertEqual(ids(False), ["t1", "t2", "t5"])
        self.assertEqual(ids(True), ["t1", "t2", "t3", "t5"])      # flagged never
        self.assertNotIn("不对", [w["hanzi"] for w in b.view(True)[0]["words"]])
        self.assertNotIn("flagged-tag", b.view(True)[0]["tags"])

    def test_unknown_fields_pass_through_harmlessly(self):
        v, _ = load(FIX).view(True)
        self.assertEqual(v["tags"], ["basic", "pending-tag"])

    def test_bad_yaml_keeps_last_good(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "bank.yaml"
            shutil.copy(FIX, p)
            b = load(p)
            self.assertIsNone(b.error)
            p.write_text("phrases: [unclosed\n  - : :", encoding="utf-8")
            os.utime(p, ns=(1, 2_000_000_000))
            v, err = b.view(True)
            self.assertIsNotNone(err)
            self.assertNotIn("Traceback", err)
            self.assertEqual(len(v["phrases"]), 4)                   # last good survives

    def test_invalid_shape_keeps_last_good(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "bank.yaml"
            shutil.copy(FIX, p)
            b = load(p)
            p.write_text("- just\n- a list\n", encoding="utf-8")
            os.utime(p, ns=(1, 3_000_000_000))
            v, err = b.view(True)
            self.assertIn("mapping", err)
            self.assertEqual(len(v["phrases"]), 4)

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            bankmod.validate({"phrases": [
                {"id": "x", "en": "a", "zh": "b", "py": "c"},
                {"id": "x", "en": "a", "zh": "b", "py": "c"}]})
        with self.assertRaises(ValueError):
            bankmod.validate({"phrases": [{"id": "x", "en": "a", "zh": "b"}]})

    def test_no_bank_at_all(self):
        b = load("/nonexistent/bank.yaml")
        self.assertIsNone(b.view(True)[0])
        self.assertIsNotNone(b.error)

    def test_one_file_change_adds_phrases(self):
        """New content = edit bank.yaml only. Zero code change."""
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "bank.yaml"
            shutil.copy(SEED, p)
            b = load(p)
            before = len(b.view(False)[0]["phrases"])
            extra = "".join(f"""  - id: new{i}
    en: "New {i}"
    zh: "新{i}"
    py: "Xīn {i}"
    tags: [added]
    status: learning
    check: verified
    words: ["新词{i}|xīncí|new word {i}"]
    grammar: []
""" for i in range(3))
            with open(p, "a", encoding="utf-8") as f:
                f.write("\n" + extra)
            os.utime(p, ns=(1, 4_000_000_000))
            v, err = b.view(False)
            self.assertIsNone(err)
            self.assertEqual(len(v["phrases"]), before + 3)
            self.assertIn("added", v["tags"])
            self.assertIn("新词2", [w["hanzi"] for w in v["words"]])


class LeitnerTest(unittest.TestCase):
    D = [1, 2, 4, 8, 16]
    T = date(2026, 10, 10)

    def test_promote_cap_and_due(self):
        self.assertEqual(next_state(0, True, self.D, self.T), (1, "2026-10-11"))
        self.assertEqual(next_state(1, True, self.D, self.T), (2, "2026-10-12"))
        self.assertEqual(next_state(4, True, self.D, self.T), (5, "2026-10-26"))
        self.assertEqual(next_state(5, True, self.D, self.T), (5, "2026-10-26"))

    def test_demote(self):
        self.assertEqual(next_state(4, False, self.D, self.T), (1, "2026-10-11"))

    def test_meta_override(self):
        self.assertEqual(bankmod.leitner_days({"meta": {"leitner_days": [3, 6]}}), [3, 6])
        self.assertEqual(next_state(4, True, [3, 6], self.T), (5, "2026-10-16"))  # clamps to last
        for bad in ([], [0, 1], [1, "2"], [1, True], "1,2", None, [-1]):
            self.assertEqual(bankmod.leitner_days({"meta": {"leitner_days": bad}}), self.D)
        self.assertEqual(bankmod.leitner_days({}), self.D)

    def test_store_sequence(self):
        s = Store()
        now = datetime(2026, 10, 10, 5, tzinfo=timezone.utc)
        r = s.record(A, "t1", "full", True, self.D, now)
        self.assertEqual((r["box"], r["correct_count"]), (1, 1))
        r = s.record(A, "t1", "full", True, self.D, now)
        self.assertEqual((r["box"], r["due"]), (2, "2026-10-12"))
        r = s.record(A, "t1", "full", False, self.D, now)
        self.assertEqual((r["box"], r["wrong_count"]), (1, 1))
        s.record(A, "t1", "cloze", True, self.D, now)                 # separate mode row
        self.assertEqual(len(s.list(A)), 2)


class AnswerTest(unittest.TestCase):
    def test_shared_cases(self):
        for given, expected, want in json.loads((HERE / "answer_cases.json").read_text()):
            with self.subTest(given=given, expected=expected):
                self.assertEqual(answer.check_pinyin(given, expected), want)

    def test_headline_examples(self):
        self.assertTrue(answer.check_pinyin("xie4xie4", "xièxiè"))
        self.assertTrue(answer.check_pinyin("ni3hao3", "nǐ hǎo"))
        self.assertFalse(answer.check_pinyin("ni2hao3", "nǐ hǎo"))


class ApiBase(unittest.TestCase):
    allowed = f"{A}, {B.upper()}"
    per_min = 60
    pending = True

    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.make()

    @classmethod
    def make(cls):
        cls.bank = load(FIX)
        cls.store = Store()
        cls.srv = make_server(cls.bank, cls.store, Identity(cls.allowed),
                              RateLimiter(cls.per_min), cls.pending, "127.0.0.1", 0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        logging.disable(logging.NOTSET)

    def call(self, method, path, email=None, body=None, raw=None, headers=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method)
        if email is not None:
            req.add_header(HEADER, email)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


class IdentityApiTest(ApiBase):
    def test_health_open_and_no_user_data(self):
        st, body = self.call("GET", "/health")
        self.assertEqual((st, body), (200, {"ok": True, "bank_ok": True, "phrases": 4}))

    def test_whoami(self):
        self.assertEqual(self.call("GET", "/whoami", A)[1], {"email_known": True})
        self.assertEqual(self.call("GET", "/whoami", B.upper())[1], {"email_known": True})
        self.assertEqual(self.call("GET", "/whoami", "z@example.com")[1], {"email_known": False})
        self.assertEqual(self.call("GET", "/whoami")[1], {"email_known": False})

    def test_no_header_and_unknown_are_403(self):
        for email in (None, "", "z@example.com"):
            for path in ("/bank", "/progress"):
                self.assertEqual(self.call("GET", path, email)[0], 403)
            self.assertEqual(self.call("POST", "/progress", email,
                                       {"item_id": "t1", "mode": "full", "correct": True})[0], 403)

    def test_bank_for_known_user(self):
        st, b = self.call("GET", "/bank", A)
        self.assertEqual(st, 200)
        self.assertIsNone(b["bank_error"])
        self.assertEqual(b["leitner_days"], [1, 2, 4, 8, 16])
        self.assertEqual([p["id"] for p in b["phrases"]], ["t1", "t2", "t3", "t5"])
        for k in ("words", "grammar", "tags"):
            self.assertTrue(b[k])
        self.assertNotIn("a@example.com", json.dumps(b))

    def test_users_are_isolated(self):
        row = {"item_id": "t1", "mode": "full", "correct": True}
        self.assertEqual(self.call("POST", "/progress", A, row)[0], 200)
        self.assertEqual(len(self.call("GET", "/progress", A)[1]), 1)
        self.assertEqual(self.call("GET", "/progress", B)[1], [])
        self.call("POST", "/progress", B, {**row, "correct": False})
        a = self.call("GET", "/progress", A)[1][0]
        b = self.call("GET", "/progress", B)[1][0]
        self.assertEqual((a["box"], b["box"]), (1, 1))
        self.assertEqual((a["correct_count"], b["wrong_count"]), (1, 1))
        self.assertNotIn("email", a)


class ProgressApiTest(ApiBase):
    def post(self, body, email=A):
        return self.call("POST", "/progress", email, body)

    def test_extra_fields_ignored(self):
        st, r = self.post({"item_id": "t2", "mode": "cloze", "correct": True,
                           "box": 5, "email": "x@example.com", "due": "2099-01-01"})
        self.assertEqual(st, 200)
        self.assertEqual(r["box"], 1)
        self.assertNotEqual(r["due"], "2099-01-01")
        rows = self.call("GET", "/progress", A)[1]
        self.assertTrue(any(x["item_id"] == "t2" and x["box"] == 1 for x in rows))
        self.assertEqual(self.call("GET", "/progress", "x@example.com")[0], 403)

    def test_bad_bodies_are_400(self):
        good = {"item_id": "t1", "mode": "full", "correct": True}
        bad = [
            [1, 2], "str", 5, None,
            {**good, "item_id": "x" * 65}, {**good, "item_id": "has space"},
            {**good, "item_id": "汉字"}, {**good, "item_id": 7}, {**good, "item_id": None},
            {**good, "mode": "nope"}, {**good, "mode": ["full"]}, {**good, "mode": 1},
            {**good, "correct": "yes"}, {**good, "correct": 1}, {"item_id": "t1", "mode": "full"},
        ]
        for b in bad:
            with self.subTest(body=b):
                self.assertEqual(self.post(b)[0], 400)
        self.assertEqual(self.call("POST", "/progress", A, raw=b"{not json")[0], 400)
        self.assertEqual(self.call("POST", "/progress", A, raw=b"")[0], 400)
        big = json.dumps({**good, "pad": "y" * 3000}).encode()
        self.assertEqual(self.call("POST", "/progress", A, raw=big)[0], 400)

    def test_valid_ids_and_modes(self):
        for i, m in enumerate(["word_en", "en_word", "builder", "cloze", "full", "listen", "review"]):
            self.assertEqual(self.post({"item_id": f"w_ab.c:d-{i}", "mode": m, "correct": False})[0], 200)

    def test_unknown_route(self):
        self.assertEqual(self.call("GET", "/nope", A)[0], 404)


class EmptyAllowlistTest(ApiBase):
    allowed = ""

    def test_refuses_everyone(self):
        for e in (A, "", None):
            self.assertEqual(self.call("GET", "/bank", e)[0], 403)
            self.assertEqual(self.call("GET", "/whoami", e)[1], {"email_known": False})
        self.assertEqual(self.call("GET", "/health")[0], 200)

    def test_logs_error_at_startup(self):
        logging.disable(logging.NOTSET)
        try:
            with self.assertLogs("mandarin.identity", "ERROR"):
                Identity("")
            with self.assertLogs("mandarin.identity", "ERROR"):
                Identity(" , ")
        finally:
            logging.disable(logging.CRITICAL)


class RateLimitTest(ApiBase):
    per_min = 3

    def test_429_after_limit_per_email(self):
        row = {"item_id": "t1", "mode": "full", "correct": True}
        codes = [self.call("POST", "/progress", A, row)[0] for _ in range(5)]
        self.assertEqual(codes, [200, 200, 200, 429, 429])
        self.assertEqual(self.call("POST", "/progress", B, row)[0], 200)   # B unaffected
        self.assertEqual(self.call("GET", "/progress", A)[0], 200)         # reads not limited

    def test_window_rolls(self):
        t = [0.0]
        rl = RateLimiter(2, clock=lambda: t[0])
        self.assertTrue(rl.allow("a")); self.assertTrue(rl.allow("a"))
        self.assertFalse(rl.allow("a"))
        t[0] = 61
        self.assertTrue(rl.allow("a"))


class PendingOffApiTest(ApiBase):
    pending = False

    def test_pending_excluded(self):
        st, b = self.call("GET", "/bank", A)
        self.assertEqual([p["id"] for p in b["phrases"]], ["t1", "t2", "t5"])
        self.assertEqual(self.call("GET", "/health")[1]["phrases"], 3)


class BrokenBankApiTest(ApiBase):
    @classmethod
    def make(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.p = pathlib.Path(cls.tmp) / "bank.yaml"
        shutil.copy(FIX, cls.p)
        cls.bank = load(cls.p)
        cls.store = Store()
        cls.srv = make_server(cls.bank, cls.store, Identity(A), RateLimiter(60), True, "127.0.0.1", 0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_bank_error_reported_and_last_good_served(self):
        self.p.write_text("phrases: nope\n", encoding="utf-8")
        os.utime(self.p, ns=(1, 5_000_000_000))
        st, b = self.call("GET", "/bank", A)
        self.assertEqual(st, 200)
        self.assertIsNotNone(b["bank_error"])
        self.assertEqual(len(b["phrases"]), 4)
        self.assertFalse(self.call("GET", "/health")[1]["bank_ok"])


if __name__ == "__main__":
    unittest.main()


class WordTypesTest(unittest.TestCase):
    """Optional top-level word_types map -> `type` on each derived word."""

    def test_types_attached_and_bad_shapes_ignored(self):
        from app.bank import derive_words, word_types
        ph = [{"id": "x1", "words": ["我|wǒ|I", "吃|chī|eat", "好|hǎo|good"]}]
        t = word_types({"word_types": {"我": "pronoun", "吃": "verb", "好": 5}})
        by = {w["hanzi"]: w for w in derive_words(ph, t)}
        self.assertEqual(by["我"]["type"], "pronoun")
        self.assertEqual(by["吃"]["type"], "verb")
        self.assertEqual(by["好"]["type"], "")            # non-string type ignored
        self.assertEqual(word_types({"word_types": ["nope"]}), {})
        self.assertEqual(word_types({}), {})


class TopicsTest(unittest.TestCase):
    """Topics list, word topics, merged meanings."""

    def test_topics_and_merge(self):
        from app.bank import derive
        doc = {"topics": [{"id": "t/a", "en": "weather", "zh": "天气", "chapter": "the essentials"}, "junk"],
               "phrases": [
                   {"id": "c1", "en": "wet", "zh": "潮湿的", "py": "cháo shī de", "check": "verified",
                    "tags": ["t/a"], "words": ["潮湿的|cháo shī de|wet"]},
                   {"id": "c2", "en": "humid", "zh": "潮湿的", "py": "cháo shī de", "check": "verified",
                    "tags": ["t/b"], "words": ["潮湿的|cháo shī de|humid"]}]}
        out = derive(doc, False)
        self.assertEqual(out["topics"], [{"id": "t/a", "en": "weather", "zh": "天气", "chapter": "the essentials"}])
        (w,) = out["words"]
        self.assertEqual(w["english"], "wet; humid")
        self.assertEqual(w["topics"], ["t/a", "t/b"])
        self.assertEqual(w["phrases"], ["c1", "c2"])

