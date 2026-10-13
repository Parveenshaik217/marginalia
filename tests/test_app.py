import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
from app import llm, rag  # noqa: E402


def make_pdf(pages):
    """Build a tiny valid PDF with one text line per page (no extra libraries needed)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join("%d 0 R" % (4 + 2 * i) for i in range(len(pages)))
    objs.append("<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(pages)))
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, text in enumerate(pages):
        stream = "BT /F1 12 Tf 50 700 Td (%s) Tj ET" % text
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
                    "/Resources << /Font << /F1 3 0 R >> >> >>" % (5 + 2 * i))
        objs.append("<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += ("%d 0 obj\n%s\nendobj\n" % (n, body)).encode("latin-1")
    xref = len(out)
    out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)).encode()
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)).encode()
    return out


class RagTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = rag.Library(os.path.join(self.tmp.name, "lib.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_chunking_keeps_size_and_content(self):
        text = "\n\n".join("Sentence number %d is here. " % i * 5 for i in range(60))
        chunks = rag.split_text(text)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(c) < 1200 for c in chunks))

    def test_bm25_ranks_relevant_first(self):
        self.lib.add("cats", "text", [(None, "Cats are small carnivorous mammals that purr and hunt mice.")])
        self.lib.add("space", "text", [(None, "The Apollo program landed astronauts on the Moon in 1969.")])
        hits = self.lib.search("Who landed on the moon?")
        self.assertEqual(hits[0]["doc_name"], "space")
        self.assertIn("moon", hits[0]["matched"])
        self.assertEqual(self.lib.search("zzzz qqqq"), [])

    def test_persistence_scope_and_delete(self):
        d = self.lib.add("a", "text", [(None, "alpha beta gamma")])
        self.lib.add("b", "text", [(None, "alpha delta epsilon")])
        again = rag.Library(self.lib.path)
        self.assertEqual(len(again.list()), 2)
        self.assertEqual(len(again.search("alpha", doc_ids=[d["id"]])), 1)
        self.assertTrue(again.remove(d["id"]))
        self.assertEqual(len(again.search("alpha")), 1)

    def test_reupload_replaces(self):
        self.lib.add("same", "text", [(None, "first version")])
        self.lib.add("same", "text", [(None, "second version")])
        self.assertEqual(len(self.lib.list()), 1)

    def test_empty_text_rejected(self):
        with self.assertRaises(ValueError):
            self.lib.add("x", "text", [(None, "   \n ")])

    def test_unicode_words(self):
        self.assertEqual(rag.words("Caf\u00e9 \u0c24\u0c46\u0c32\u0c41\u0c17\u0c41 test"), ["caf\u00e9", "\u0c24\u0c46\u0c32\u0c41\u0c17\u0c41", "test"])

    def test_pdf_pages(self):
        pages = rag.extract_pdf(make_pdf(["Photosynthesis converts light into energy", "Mitochondria produce ATP"]))
        self.assertEqual([p for p, _ in pages], [1, 2])
        self.assertIn("Mitochondria", pages[1][1])

    def test_bad_pdf(self):
        with self.assertRaises(ValueError):
            rag.extract_pdf(b"not a pdf at all")

    def test_private_urls_blocked(self):
        with self.assertRaises(ValueError):
            rag.fetch_url("http://127.0.0.1:8000/")
        with self.assertRaises(ValueError):
            rag.fetch_url("ftp://example.com/file")


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        server.library = rag.Library(os.path.join(cls.tmp.name, "lib.json"))
        cls.httpd = server.create_server("127.0.0.1", 0)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None, raw=False):
        data = body if raw else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_01_static_pages(self):
        for path in ("/", "/static/app.js", "/static/style.css"):
            with urllib.request.urlopen(self.base + path) as r:
                self.assertEqual(r.status, 200)
        self.assertEqual(self.call("GET", "/static/../server.py")[0], 404)

    def test_02_full_flow(self):
        self.assertEqual(self.call("POST", "/api/ask", {"question": "hi"})[0], 400)  # nothing added yet
        status, r = self.call("POST", "/api/upload?name=bio.pdf",
                              make_pdf(["Mitochondria produce ATP for the cell", "Chloroplasts run photosynthesis"]), raw=True)
        self.assertEqual(status, 200)
        self.assertEqual(r["doc"]["kind"], "pdf")
        self.assertEqual(self.call("POST", "/api/upload?name=n.txt", b"Deadlines: the report is due on Friday.", raw=True)[0], 200)
        self.assertEqual(self.call("POST", "/api/note", {"title": "My note", "text": "Lunch is at noon."})[0], 200)
        self.assertEqual(self.call("POST", "/api/upload?name=x.exe", b"abc", raw=True)[0], 400)
        self.assertEqual(len(self.call("GET", "/api/docs")[1]["docs"]), 3)

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            status, r = self.call("POST", "/api/ask", {"question": "What makes ATP?"})
        self.assertEqual(status, 200)
        self.assertEqual(r["mode"], "passages")
        self.assertEqual(r["sources"][0]["doc_name"], "bio.pdf")
        self.assertEqual(r["sources"][0]["page"], 1)

        status, r = self.call("POST", "/api/ask", {"question": "banana smoothie recipe"})
        self.assertEqual((status, r["mode"], r["sources"]), (200, "none", []))

        doc_id = self.call("GET", "/api/docs")[1]["docs"][0]["id"]
        self.assertEqual(self.call("DELETE", "/api/docs/" + doc_id)[0], 200)
        self.assertEqual(self.call("DELETE", "/api/docs/" + doc_id)[0], 404)

    def test_03_bad_requests(self):
        self.assertEqual(self.call("POST", "/api/ask", b"{not json", raw=True)[0], 400)
        self.assertEqual(self.call("POST", "/api/note", {"text": ""})[0], 400)
        self.assertEqual(self.call("GET", "/nope")[0], 404)


class LlmTests(unittest.TestCase):
    SOURCES = [{"doc_name": "bio.pdf", "page": 1, "text": "Mitochondria produce ATP."}]

    def fake_response(self, payload):
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps(payload).encode()
        resp.__enter__.return_value = resp
        return resp

    def test_claude_success(self):
        payload = {"content": [{"type": "text", "text": "Mitochondria make ATP [1]."}]}
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
                mock.patch("urllib.request.urlopen", return_value=self.fake_response(payload)) as m:
            out = llm.generate("What makes ATP?", self.SOURCES, [{"q": "hi", "a": "hello"}])
        self.assertEqual(out["mode"], "claude")
        sent = json.loads(m.call_args[0][0].data.decode())
        self.assertEqual(sent["messages"][-1]["role"], "user")
        self.assertEqual(sent["messages"][0]["role"], "user")

    def test_claude_failure_falls_back(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
                mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            out = llm.generate("q", self.SOURCES, [])
        self.assertEqual(out["mode"], "passages")
        self.assertIn("offline", out["note"])


if __name__ == "__main__":
    unittest.main()
