"""Retrieval engine: text extraction, chunking, BM25 ranking and a small on-disk library.

Pure standard library except pypdf (PDF reading). Works on Python 3.9+.
"""
from __future__ import annotations

import io
import ipaddress
import json
import math
import os
import re
import socket
import threading
import time
import unicodedata
import urllib.request
import uuid
from collections import Counter
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

STOPWORDS = set(
    "a an and are as at be been being but by can could did do does for from had has have how i if "
    "in into is it its of on or our she he her his so than that the their them then there these they "
    "this to was we were what when where which who why will with would you your about also not no".split()
)
K1, B = 1.5, 0.75
Pages = List[Tuple[Optional[int], str]]


# ---------- text processing ----------
def words(text: str) -> List[str]:
    """Split text into lowercase words. Unicode-aware, so non-English documents work too."""
    out, cur = [], []
    for ch in text.lower():
        if unicodedata.category(ch)[0] in "LNM":
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def stem(word: str) -> str:
    for suffix in ("ingly", "edly", "ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokenize(text: str, drop_stop: bool = True) -> List[str]:
    return [stem(w) for w in words(text) if not (drop_stop and w in STOPWORDS)]


def _pieces(text: str, max_chars: int) -> List[str]:
    out: List[str] = []
    for para in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        para = re.sub(r"\s+", " ", para).strip()
        if not para:
            continue
        if len(para) <= max_chars:
            out.append(para)
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            while len(sentence) > max_chars:
                cut = sentence.rfind(" ", 0, max_chars)
                if cut <= 0:
                    cut = max_chars
                out.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if sentence:
                out.append(sentence)
    return out


def split_text(text: str, max_chars: int = 900, overlap: int = 150) -> List[str]:
    """Pack paragraphs/sentences into ~max_chars chunks with a little overlap between chunks."""
    chunks: List[str] = []
    cur = ""
    for piece in _pieces(text, max_chars):
        if cur and len(cur) + 1 + len(piece) > max_chars:
            chunks.append(cur)
            tail = ""
            if len(cur) > overlap * 2:
                tail = cur[-overlap:]
                space = tail.find(" ")
                tail = tail[space + 1:] if space != -1 else tail
            cur = (tail + " " + piece).strip()
        else:
            cur = (cur + " " + piece).strip()
    if cur:
        chunks.append(cur)
    return chunks


# ---------- extraction ----------
def extract_pdf(data: bytes) -> Pages:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("This PDF is password-protected. Remove the password and try again.")
        pages: Pages = []
        for number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if text.strip():
                pages.append((number, text))
        return pages
    except ValueError:
        raise
    except Exception as exc:  # corrupt file, missing crypto library, etc.
        raise ValueError("Could not read this PDF (%s)." % exc)


def extract_plain(data: bytes) -> Pages:
    return [(None, data.decode("utf-8", errors="replace"))]


class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form"}
    BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self._skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n\n" if tag != "br" else "\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def _check_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter a full link starting with http:// or https://")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except OSError:
        raise ValueError("Could not find that website. Check the link and your internet connection.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("Links to private network addresses are not allowed.")
    return url


def fetch_url(url: str) -> Tuple[str, Pages]:
    """Download a web page (or PDF) and return (title, pages)."""
    _check_public_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "Marginalia/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            raw = resp.read(8 * 1024 * 1024)
            ctype = (resp.headers.get_content_type() or "").lower()
            charset = resp.headers.get_content_charset() or "utf-8"
    except Exception as exc:
        raise ValueError("Could not download that link (%s)." % exc)
    if ctype == "application/pdf":
        return urlparse(url).path.rsplit("/", 1)[-1] or url, extract_pdf(raw)
    parser = _HTMLText()
    parser.feed(raw.decode(charset, errors="replace"))
    title = re.sub(r"\s+", " ", parser.title).strip() or urlparse(url).netloc
    return title, [(None, "".join(parser.parts))]


# ---------- library ----------
class Library:
    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.docs: Dict[str, dict] = {}
        self._entries: Optional[List[dict]] = None
        self._df: Counter = Counter()
        self._avgdl = 1.0
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                self.docs = json.load(fh).get("docs", {})
        except (OSError, ValueError):
            self.docs = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"docs": self.docs}, fh)
        os.replace(tmp, self.path)

    def add(self, name: str, kind: str, pages: Pages, source: str = "") -> dict:
        chunks = []
        for page, text in pages:
            for piece in split_text(text):
                chunks.append({"page": page, "text": piece})
        if not chunks:
            raise ValueError("No readable text found. Scanned or image-only PDFs need OCR first.")
        name = (name or "Untitled").strip()[:120]
        with self.lock:
            for old_id in [d for d, doc in self.docs.items() if doc["name"] == name]:
                del self.docs[old_id]  # re-adding a file replaces the old copy
            doc_id = uuid.uuid4().hex[:10]
            self.docs[doc_id] = {
                "id": doc_id, "name": name, "kind": kind, "source": source,
                "added": int(time.time()), "chunks": chunks,
            }
            self._entries = None
            self._save()
            return self.summary(doc_id)

    def remove(self, doc_id: str) -> bool:
        with self.lock:
            if doc_id not in self.docs:
                return False
            del self.docs[doc_id]
            self._entries = None
            self._save()
            return True

    def summary(self, doc_id: str) -> dict:
        d = self.docs[doc_id]
        return {"id": d["id"], "name": d["name"], "kind": d["kind"], "source": d.get("source", ""),
                "added": d["added"], "chunks": len(d["chunks"])}

    def list(self) -> List[dict]:
        with self.lock:
            return sorted((self.summary(i) for i in self.docs), key=lambda s: -s["added"])

    def _ensure_index(self) -> None:
        if self._entries is not None:
            return
        entries, df = [], Counter()
        for doc in self.docs.values():
            for chunk in doc["chunks"]:
                tokens = tokenize(chunk["text"])
                tf = Counter(tokens)
                df.update(tf.keys())
                entries.append({"doc": doc, "chunk": chunk, "tf": tf, "len": len(tokens) or 1})
        self._entries, self._df = entries, df
        self._avgdl = (sum(e["len"] for e in entries) / len(entries)) if entries else 1.0

    def search(self, query: str, k: int = 6, doc_ids: Optional[List[str]] = None) -> List[dict]:
        with self.lock:
            self._ensure_index()
            terms = set(tokenize(query) or tokenize(query, drop_stop=False))
            if not terms or not self._entries:
                return []
            allowed = set(doc_ids) if doc_ids is not None else None
            n_total = len(self._entries)
            scored = []
            for e in self._entries:
                if allowed is not None and e["doc"]["id"] not in allowed:
                    continue
                score = 0.0
                for t in terms:
                    f = e["tf"].get(t, 0)
                    if not f:
                        continue
                    n = self._df[t]
                    idf = math.log(1 + (n_total - n + 0.5) / (n + 0.5))
                    score += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * e["len"] / self._avgdl))
                if score > 0:
                    scored.append((score, e))
            scored.sort(key=lambda pair: -pair[0])
            results = []
            for score, e in scored[:k]:
                text = e["chunk"]["text"]
                matched = sorted({w for w in words(text) if stem(w) in terms})[:15]
                results.append({
                    "doc_id": e["doc"]["id"], "doc_name": e["doc"]["name"], "kind": e["doc"]["kind"],
                    "page": e["chunk"]["page"], "text": text, "score": round(score, 3), "matched": matched,
                })
            return results
