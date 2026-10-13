"""Answer generation. Uses the Anthropic Messages API over plain HTTPS (no SDK needed).

If no ANTHROPIC_API_KEY is set, or the call fails, falls back to showing the best passages.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import List

API_URL = "https://api.anthropic.com/v1/messages"
SYSTEM = (
    "You are a careful research assistant. Answer the question using ONLY the numbered sources provided. "
    "Cite every claim with the source number in square brackets, like [1] or [2][3]. "
    "If the sources do not contain the answer, say so plainly instead of guessing. "
    "Write short plain-text paragraphs. Do not use markdown, headings or bullet symbols."
)


def configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def model_name() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-sonnet-5-5"


def _label(source: dict) -> str:
    page = " (page %s)" % source["page"] if source.get("page") else ""
    return "%s%s" % (source["doc_name"], page)


def _extractive(sources: List[dict], note: str = "") -> dict:
    refs = "".join("[%d]" % (i + 1) for i in range(min(3, len(sources))))
    text = "These passages from your documents match your question best %s. " % refs
    if not note:
        text += "Add an ANTHROPIC_API_KEY (see README) to get written answers with citations."
    return {"answer": text.strip(), "mode": "passages", "note": note}


def generate(question: str, sources: List[dict], history: List[dict]) -> dict:
    if not sources:
        return {"answer": "I couldn't find anything relevant to that in your documents. "
                          "Try different keywords, or add a document that covers it.",
                "mode": "none", "note": ""}
    if not configured():
        return _extractive(sources)

    messages = []
    for turn in history[-4:]:
        messages.append({"role": "user", "content": turn["q"]})
        messages.append({"role": "assistant", "content": turn["a"]})
    blocks = ["[%d] %s\n%s" % (i + 1, _label(s), s["text"]) for i, s in enumerate(sources)]
    messages.append({"role": "user", "content": "Sources:\n\n%s\n\nQuestion: %s" % ("\n\n".join(blocks), question)})

    payload = json.dumps({"model": model_name(), "max_tokens": 1000, "system": SYSTEM, "messages": messages})
    request = urllib.request.Request(
        API_URL, data=payload.encode("utf-8"), method="POST",
        headers={"content-type": "application/json", "anthropic-version": "2023-06-01",
                 "x-api-key": os.environ["ANTHROPIC_API_KEY"].strip()},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
        if not text:
            raise ValueError("empty response")
        return {"answer": text, "mode": "claude", "note": ""}
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))["error"]["message"]
        except Exception:
            detail = exc.reason
        return _extractive(sources, "Claude could not answer (%s: %s). Showing matching passages instead." % (exc.code, detail))
    except Exception as exc:
        return _extractive(sources, "Claude could not answer (%s). Showing matching passages instead." % exc)
