"""Marginalia: ask questions across your own documents, with cited answers.

Run with:  python server.py     (or:  bash run.sh)
"""
from __future__ import annotations

import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from app import llm, rag  # noqa: E402

MAX_UPLOAD = 25 * 1024 * 1024
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/style.css": ("style.css", "text/css; charset=utf-8"),
    "/static/app.js": ("app.js", "application/javascript; charset=utf-8"),
}


def load_env() -> None:
    """Tiny .env reader so no extra package is needed."""
    try:
        with open(os.path.join(BASE, ".env"), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except OSError:
        pass


library = rag.Library(os.path.join(BASE, "data", "library.json"))


class Handler(BaseHTTPRequestHandler):
    server_version = "Marginalia/1.0"

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    # ----- helpers -----
    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            raise ValueError("That file is larger than 25 MB.")
        return self.rfile.read(length) if length else b""

    def _json_body(self) -> dict:
        try:
            data = json.loads(self._body().decode("utf-8") or "{}")
        except ValueError:
            raise ValueError("Invalid request.")
        if not isinstance(data, dict):
            raise ValueError("Invalid request.")
        return data

    def _guard(self, fn) -> None:
        try:
            fn()
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:  # never crash the server on a bad request
            print("Server error:", repr(exc), file=sys.stderr)
            self._error(500, "Something went wrong on the server. Check the terminal for details.")

    # ----- routes -----
    def do_GET(self):
        path = urlparse(self.path).path
        if path in STATIC:
            name, ctype = STATIC[path]
            with open(os.path.join(BASE, "static", name), "rb") as fh:
                return self._send(200, fh.read(), ctype)
        if path == "/api/docs":
            return self._guard(lambda: self._json(200, {"docs": library.list()}))
        if path == "/api/status":
            return self._json(200, {"claude": llm.configured(), "model": llm.model_name()})
        self._error(404, "Not found.")

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/docs/"):
            ok = library.remove(unquote(path.rsplit("/", 1)[-1]))
            return self._json(200, {"ok": True}) if ok else self._error(404, "Document not found.")
        self._error(404, "Not found.")

    def do_POST(self):
        parsed = urlparse(self.path)
        routes = {"/api/upload": self._upload, "/api/note": self._note, "/api/url": self._url, "/api/ask": self._ask}
        handler = routes.get(parsed.path)
        if not handler:
            return self._error(404, "Not found.")
        self._guard(lambda: handler(parsed))

    def _upload(self, parsed):
        name = unquote((parse_qs(parsed.query).get("name") or ["file"])[0])
        data = self._body()
        if not data:
            raise ValueError("The file is empty.")
        lower = name.lower()
        if lower.endswith(".pdf"):
            doc = library.add(name, "pdf", rag.extract_pdf(data))
        elif lower.endswith((".txt", ".md", ".markdown")):
            doc = library.add(name, "text", rag.extract_plain(data))
        else:
            raise ValueError("Unsupported file type. Use PDF, TXT or MD.")
        self._json(200, {"doc": doc})

    def _note(self, parsed):
        data = self._json_body()
        text = str(data.get("text") or "").strip()
        if not text:
            raise ValueError("Paste some text first.")
        title = str(data.get("title") or "").strip() or "Note: " + text[:40]
        self._json(200, {"doc": library.add(title, "note", [(None, text)])})

    def _url(self, parsed):
        url = str(self._json_body().get("url") or "").strip()
        title, pages = rag.fetch_url(url)
        self._json(200, {"doc": library.add(title, "link", pages, source=url)})

    def _ask(self, parsed):
        data = self._json_body()
        question = str(data.get("question") or "").strip()[:2000]
        if not question:
            raise ValueError("Type a question first.")
        if not library.docs:
            raise ValueError("Add a document first.")
        doc_ids = data.get("doc_ids")
        doc_ids = [str(i) for i in doc_ids] if isinstance(doc_ids, list) else None
        history = []
        for turn in (data.get("history") or [])[-4:]:
            if isinstance(turn, dict) and isinstance(turn.get("q"), str) and isinstance(turn.get("a"), str):
                history.append({"q": turn["q"][:2000], "a": turn["a"][:4000]})
        query = question
        if history and len(question.split()) < 6:  # short follow-ups borrow context from the last question
            query = history[-1]["q"] + " " + question
        sources = library.search(query, k=6, doc_ids=doc_ids)
        result = llm.generate(question, sources, history)
        for i, s in enumerate(sources, start=1):
            s["n"] = i
        result["sources"] = sources
        result["terms"] = sorted({w for s in sources for w in s["matched"]})
        self._json(200, result)


def create_server(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    load_env()
    host = os.environ.get("HOST", "127.0.0.1")
    start = int(os.environ.get("PORT", "8000"))
    server = None
    for port in range(start, start + 20):  # if 8000 is busy, try the next ports
        try:
            server = create_server(host, port)
            break
        except OSError:
            continue
    if server is None:
        sys.exit("Could not open a port. Close other apps or set PORT=9000 and try again.")
    url = "http://%s:%d" % ("127.0.0.1" if host in ("0.0.0.0", "") else host, server.server_address[1])
    print("\nMarginalia is running at %s\nPress Ctrl+C to stop.\n" % url)
    if llm.configured():
        print("Claude answers: ON (model %s)" % llm.model_name())
    else:
        print("Claude answers: OFF (no ANTHROPIC_API_KEY set; showing matching passages). See README.")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
