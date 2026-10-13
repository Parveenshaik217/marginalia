(function () {
  "use strict";
  var $ = function (s) { return document.querySelector(s); };
  var docs = [];
  var selected = new Set();
  var history = [];
  var turns = 0;
  var busy = false;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function api(path, opts) {
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(data.error || "Request failed (" + r.status + ")");
        return data;
      });
    });
  }

  function setStatus(msg, isError) {
    var s = $("#add-status");
    s.textContent = msg || "";
    s.className = "status" + (isError ? " error" : "");
  }

  // ---------- library ----------
  function loadDocs(autoSelectId) {
    return api("/api/docs").then(function (d) {
      var known = new Set(docs.map(function (x) { return x.id; }));
      docs = d.docs;
      var ids = new Set(docs.map(function (x) { return x.id; }));
      selected.forEach(function (id) { if (!ids.has(id)) selected.delete(id); });
      docs.forEach(function (x) { if (!known.has(x.id) || x.id === autoSelectId) selected.add(x.id); });
      renderDocs();
    });
  }

  var KIND = { pdf: "PDF", text: "Text file", note: "Note", link: "Link" };

  function renderDocs() {
    var list = $("#docs");
    list.textContent = "";
    $("#docs-empty").hidden = docs.length > 0;
    docs.forEach(function (d) {
      var li = el("li");
      var box = el("input");
      box.type = "checkbox";
      box.checked = selected.has(d.id);
      box.setAttribute("aria-label", "Search " + d.name);
      box.addEventListener("change", function () {
        if (box.checked) selected.add(d.id); else selected.delete(d.id);
      });
      var info = el("div");
      info.appendChild(el("div", "name", d.name));
      info.appendChild(el("div", "meta", (KIND[d.kind] || d.kind) + " \u00b7 " + d.chunks + " passages"));
      var rm = el("button", "remove", "\u00d7");
      rm.type = "button";
      rm.title = "Remove";
      rm.setAttribute("aria-label", "Remove " + d.name);
      rm.addEventListener("click", function () {
        if (!confirm("Remove \"" + d.name + "\" from your library?")) return;
        api("/api/docs/" + encodeURIComponent(d.id), { method: "DELETE" })
          .then(function () { return loadDocs(); })
          .catch(function (e) { setStatus(e.message, true); });
      });
      li.appendChild(box);
      li.appendChild(info);
      li.appendChild(rm);
      list.appendChild(li);
    });
  }

  function uploadFiles(files) {
    var queue = Array.prototype.slice.call(files);
    function next() {
      var f = queue.shift();
      if (!f) return;
      setStatus("Reading " + f.name + "\u2026");
      api("/api/upload?name=" + encodeURIComponent(f.name), { method: "POST", body: f })
        .then(function (r) { setStatus("Added " + f.name + "."); return loadDocs(r.doc.id); })
        .catch(function (e) { setStatus(f.name + ": " + e.message, true); })
        .then(next);
    }
    next();
  }

  var drop = $("#drop"), fileInput = $("#file");
  drop.addEventListener("click", function () { fileInput.click(); });
  drop.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  fileInput.addEventListener("change", function () { if (fileInput.files.length) uploadFiles(fileInput.files); fileInput.value = ""; });
  ["dragenter", "dragover"].forEach(function (t) { drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add("over"); }); });
  ["dragleave", "drop"].forEach(function (t) { drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.remove("over"); }); });
  drop.addEventListener("drop", function (e) { if (e.dataTransfer && e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files); });

  function togglePanel(id) {
    var p = $(id);
    var show = p.hidden;
    $("#note-form").hidden = true;
    $("#url-form").hidden = true;
    p.hidden = !show;
  }
  $("#btn-note").addEventListener("click", function () { togglePanel("#note-form"); });
  $("#btn-url").addEventListener("click", function () { togglePanel("#url-form"); });
  Array.prototype.forEach.call(document.querySelectorAll("[data-close]"), function (b) {
    b.addEventListener("click", function () { $("#note-form").hidden = true; $("#url-form").hidden = true; });
  });

  $("#note-form").addEventListener("submit", function (e) {
    e.preventDefault();
    setStatus("Saving note\u2026");
    api("/api/note", { method: "POST", body: JSON.stringify({ title: $("#note-title").value, text: $("#note-text").value }) })
      .then(function (r) {
        $("#note-title").value = ""; $("#note-text").value = ""; $("#note-form").hidden = true;
        setStatus("Note saved.");
        return loadDocs(r.doc.id);
      })
      .catch(function (err) { setStatus(err.message, true); });
  });

  $("#url-form").addEventListener("submit", function (e) {
    e.preventDefault();
    setStatus("Downloading link\u2026");
    api("/api/url", { method: "POST", body: JSON.stringify({ url: $("#url-input").value }) })
      .then(function (r) {
        $("#url-input").value = ""; $("#url-form").hidden = true;
        setStatus("Added " + r.doc.name + ".");
        return loadDocs(r.doc.id);
      })
      .catch(function (err) { setStatus(err.message, true); });
  });

  // ---------- answers ----------
  function highlight(text, terms) {
    var frag = document.createDocumentFragment();
    var set = new Set((terms || []).map(function (w) { return w.toLowerCase(); }));
    var re = /[\p{L}\p{N}\p{M}]+/gu;
    var last = 0, m;
    while ((m = re.exec(text)) !== null) {
      if (!set.has(m[0].toLowerCase())) continue;
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      frag.appendChild(el("mark", "", m[0]));
      last = m.index + m[0].length;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    return frag;
  }

  function jumpTo(id) {
    var t = document.getElementById(id);
    if (!t) return;
    t.scrollIntoView({ behavior: "smooth", block: "nearest" });
    t.classList.remove("flash");
    void t.offsetWidth;
    t.classList.add("flash");
  }

  function renderAnswerText(box, text, count, turnId) {
    text.split(/\n\s*\n/).forEach(function (para) {
      if (!para.trim()) return;
      var p = el("p");
      var re = /\[(\d+(?:\s*,\s*\d+)*)\]/g;
      var last = 0, m;
      while ((m = re.exec(para)) !== null) {
        p.appendChild(document.createTextNode(para.slice(last, m.index)));
        m[1].split(",").forEach(function (num) {
          var n = parseInt(num, 10);
          if (n >= 1 && n <= count) {
            var chip = el("button", "chip", String(n));
            chip.type = "button";
            chip.title = "Show source " + n;
            chip.addEventListener("click", function () { jumpTo("src-" + turnId + "-" + n); });
            p.appendChild(chip);
          } else {
            p.appendChild(document.createTextNode("[" + n + "]"));
          }
        });
        last = m.index + m[0].length;
      }
      p.appendChild(document.createTextNode(para.slice(last)));
      box.appendChild(p);
    });
  }

  function renderSources(wrap, sources, terms, turnId) {
    if (!sources.length) return;
    wrap.appendChild(el("h3", "", "Sources"));
    sources.forEach(function (s) {
      var card = el("article", "src");
      card.id = "src-" + turnId + "-" + s.n;
      var head = el("div", "src-head");
      head.appendChild(el("span", "num", "[" + s.n + "]"));
      head.appendChild(el("span", "", s.doc_name + (s.page ? ", page " + s.page : "")));
      card.appendChild(head);
      var body = el("div", "src-text");
      body.appendChild(highlight(s.text, terms));
      card.appendChild(body);
      wrap.appendChild(card);
    });
  }

  function scrollDown() { var t = $("#thread"); t.scrollTop = t.scrollHeight; }

  function ask(question) {
    var ids = Array.from(selected);
    if (!ids.length) { setStatus("Tick at least one document to search.", true); return; }
    busy = true; $("#send").disabled = true;
    var empty = $("#empty"); if (empty) empty.remove();
    var turnId = ++turns;
    var turn = el("section", "turn");
    turn.appendChild(el("div", "q", question));
    var a = el("div", "a");
    a.appendChild(el("span", "pending", "Searching your documents\u2026"));
    turn.appendChild(a);
    $("#thread").appendChild(turn);
    scrollDown();

    api("/api/ask", { method: "POST", body: JSON.stringify({ question: question, doc_ids: ids, history: history }) })
      .then(function (r) {
        a.textContent = "";
        renderAnswerText(a, r.answer, r.sources.length, turnId);
        if (r.note) turn.appendChild(el("p", "note", r.note));
        var wrap = el("div", "sources");
        renderSources(wrap, r.sources, r.terms, turnId);
        turn.appendChild(wrap);
        history.push({ q: question, a: r.answer });
        if (history.length > 6) history.shift();
      })
      .catch(function (e) { a.textContent = ""; turn.appendChild(el("p", "note", e.message)); })
      .then(function () { busy = false; $("#send").disabled = false; scrollDown(); $("#q").focus(); });
  }

  var q = $("#q");
  function grow() { q.style.height = "auto"; q.style.height = Math.min(q.scrollHeight, 160) + "px"; }
  q.addEventListener("input", grow);
  q.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#ask-form").requestSubmit ? $("#ask-form").requestSubmit() : $("#send").click(); }
  });
  $("#ask-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var text = q.value.trim();
    if (!text || busy) return;
    q.value = ""; grow();
    ask(text);
  });

  // ---------- startup ----------
  api("/api/status").then(function (s) {
    $("#mode").textContent = s.claude
      ? "Written answers by Claude (" + s.model + "). Your question and the matching passages are sent to Anthropic."
      : "No API key set: you will see the best-matching passages. Add a key in .env for written answers (see README).";
  }).catch(function () {});
  loadDocs().catch(function (e) { setStatus(e.message, true); });
})();
