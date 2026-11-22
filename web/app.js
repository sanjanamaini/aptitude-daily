/* Aptitude Daily: the page. Plain DOM (text is always set as text, never as HTML). */
(function () {
  "use strict";
  var LET = "ABCDE";
  var S = { status: null, day: null, paper: null, draft: null, key: null, attempt: null, filter: "all",
            timer: null, running: false, elapsed: 0, saveT: null, mfilter: "all", mistakes: null };

  // ------------------------------------------------------------ helpers
  function $(id) { return document.getElementById(id); }
  function h(tag, attrs) {
    var el = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "style") String(v).split(";").forEach(function (d) {  // CSSOM: allowed by the strict CSP, unlike a style attribute
        var i = d.indexOf(":"); if (i > 0) el.style.setProperty(d.slice(0, i).trim(), d.slice(i + 1).trim());
      });
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    });
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el) {  // add(el, kid, kid, ...): strings become text nodes, arrays are flattened
    for (var i = 1; i < arguments.length; i++) {
      var kid = arguments[i];
      if (kid == null || kid === false) continue;
      if (Array.isArray(kid)) { kid.forEach(function (k) { add(el, k); }); continue; }
      el.appendChild(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
    }
  }
  var NS = "http://www.w3.org/2000/svg";
  function s(tag, attrs, text) {
    var el = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    if (text != null) el.textContent = text;
    return el;
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
  function api(path) {
    return fetch(path, { cache: "no-store" }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) { var e = new Error(j.error || r.status); e.status = r.status; throw e; } return j; });
    });
  }
  function post(path, body) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Aptitude": "1" }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) { var e = new Error(j.error || r.status); e.status = r.status; throw e; } return j; }); });
  }
  function toast(msg) {
    var t = $("toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(function () { t.hidden = true; }, 3200);
  }
  function fmtDay(d) {
    var dt = new Date(d + "T12:00:00");
    return dt.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  }
  function fmtTime(sec) {
    sec = Math.max(0, Math.floor(sec)); var m = Math.floor(sec / 60), x = sec % 60;
    return (m < 10 ? "0" : "") + m + ":" + (x < 10 ? "0" : "") + x;
  }
  function pct(x) { return x == null ? "n/a" : Math.round(100 * x) + "%"; }
  function fmtNum(v) {
    if (typeof v !== "number") return String(v);
    return v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
  }
  function sectionName(code) {
    var p = S.paper; if (!p) return code;
    for (var i = 0; i < p.sections.length; i++) if (p.sections[i].code === code) return p.sections[i].name;
    return code;
  }

  // ------------------------------------------------------------ theme
  function theme(set) {
    var root = document.documentElement;
    try { if (set) localStorage.setItem("apt-theme", set); } catch (e) { /* private mode */ }
    var t = set; if (!t) { try { t = localStorage.getItem("apt-theme"); } catch (e) { t = null; } }
    if (t === "light" || t === "dark") root.setAttribute("data-theme", t); else root.removeAttribute("data-theme");
  }
  $("theme-btn").addEventListener("click", function () {
    var dark = document.documentElement.getAttribute("data-theme") === "dark" ||
      (!document.documentElement.getAttribute("data-theme") && window.matchMedia("(prefers-color-scheme: dark)").matches);
    theme(dark ? "light" : "dark");
  });
  theme();

  // ------------------------------------------------------------ status and run pill
  function loadStatus() {
    return api("/api/status").then(function (st) {
      var was = S.status;
      S.status = st;
      var pill = $("runpill"), rt = $("runtext");
      pill.classList.remove("busy", "bad");
      var r = st.run || {};
      if (r.running) { pill.classList.add("busy"); rt.textContent = "Building " + (r.day || "today's set"); }
      else if (r.ok === false && r.day === st.today) { pill.classList.add("bad"); rt.textContent = "Build failed"; }
      else { rt.textContent = "Next set " + (st.next_build || "").replace("T", " "); }
      var open = st.days.filter(function (d) { return d.status === "locked"; }).length;
      $("tab-sets").textContent = st.days.length ? String(st.days.length) : "";
      $("tab-sets").title = open + " not solved";
      // a build just finished while today is open: show it
      if (was && was.run && was.run.running && !r.running && route().name === "today") render();
      else if (r.running && route().name === "today" && !S.paper) render();
      return st;
    });
  }
  $("runpill").addEventListener("click", function () { location.hash = "#/health"; });

  function examChips() {
    var box = clear($("exam-chips"));
    (S.status ? S.status.exams : []).forEach(function (e) {
      var line;
      if (e.deadline && e.deadline_days_left != null && e.deadline_days_left >= 0) line = (e.deadline_label || "deadline") + " in " + e.deadline_days_left + " days";
      else if (e.date && e.days_left != null) line = e.days_left >= 0 ? "exam in " + e.days_left + " days" : "exam done";
      else line = "dates not announced";
      var urgent = e.deadline_days_left != null && e.deadline_days_left >= 0 && e.deadline_days_left <= 21;
      var sub = e.date && e.days_left != null && e.days_left >= 0 && line.indexOf("exam") < 0 ? ", exam in " + e.days_left : "";
      add(box, h("a", { class: "exam" + (urgent ? " urgent" : ""), href: e.url, target: "_blank", rel: "noopener noreferrer", title: e.note || "" },
        h("b", { text: e.name }), h("span", { text: line + sub })));
    });
  }

  // ------------------------------------------------------------ routing
  function route() {
    var p = (location.hash || "#/today").replace(/^#\//, "").split("/");
    if (p[0] === "day" && /^\d{4}-\d{2}-\d{2}$/.test(p[1] || "")) return { name: "today", day: p[1] };
    if (["today", "sets", "progress", "mistakes", "health"].indexOf(p[0]) >= 0) return { name: p[0] };
    return { name: "today" };
  }
  function render() {
    var r = route();
    document.querySelectorAll(".view").forEach(function (v) { v.hidden = v.id !== "view-" + r.name; });
    document.querySelectorAll(".tabs a").forEach(function (a) { a.classList.toggle("on", a.getAttribute("data-tab") === r.name); });
    $("sheetbar").hidden = true;
    if (r.name !== "today") stopTimer(true);
    if (r.name === "today") renderDay(r.day);
    else if (r.name === "sets") renderSets();
    else if (r.name === "progress") renderProgress();
    else if (r.name === "mistakes") renderMistakes();
    else if (r.name === "health") renderHealth();
  }
  window.addEventListener("hashchange", function () { window.scrollTo(0, 0); render(); });

  // ------------------------------------------------------------ today / one day
  function renderDay(day) {
    var st = S.status;
    examChips();
    var banner = clear($("day-banner")), body = clear($("day-body"));
    var target = day || st.today;
    var has = st.days.some(function (d) { return d.day === target; });
    if (!has) {
      S.paper = null;
      $("day-title").textContent = day ? "No set for " + fmtDay(day) : "Today's set";
      $("day-sub").textContent = fmtDay(target);
      var r = st.run || {};
      if (r.running) {
        var steps = h("div", { class: "steps" });
        Object.keys(r.sections || {}).forEach(function (k) { add(steps, h("span", { class: "chip", text: k + ": " + r.sections[k] })); });
        add(banner, h("div", { class: "banner info" }, h("div", null, h("b", { text: "Building today's 50 questions." }),
          h("div", { class: "muted", text: "Claude writes them, then code and a second blind solve check every answer. This takes 10 to 20 minutes; the page updates by itself." }), steps)));
      } else if (r.ok === false && r.day === target) {
        add(banner, h("div", { class: "banner bad" }, h("div", null, h("b", { text: "This morning's build failed." }),
          h("div", { text: r.summary || "" }), h("div", { class: "muted", text: "It retries automatically at the next slot, or build it now." })),
          h("button", { class: "btn primary", text: "Build it now", onclick: buildNow })));
      } else if (!day) {
        add(banner, h("div", { class: "banner" }, h("div", null, h("b", { text: "Today's set is not built yet." }),
          h("div", { class: "muted", text: "It is built every morning at " + st.schedule + ", and at login if the Mac was off." })),
          h("button", { class: "btn primary", text: "Build it now", onclick: buildNow })));
      }
      var open = st.days.filter(function (d) { return d.status === "locked"; });
      if (open.length) {
        add(body, h("div", { class: "card" }, h("h3", { text: "Sets still open" }),
          h("div", { class: "chips" }, open.map(function (d) { return h("a", { class: "chip", href: "#/day/" + d.day, text: fmtDay(d.day) }); }))));
      }
      return;
    }
    S.day = target;
    add(body, h("div", { class: "empty", text: "Loading the set..." }));
    api("/api/paper?day=" + target).then(function (res) {
      S.paper = res.paper; S.draft = res.draft || { answers: {}, flags: [] };
      S.draft.answers = S.draft.answers || {}; S.draft.flags = S.draft.flags || [];
      S.elapsed = S.draft.elapsed_s || 0; S.attempt = res.attempt; S.key = null;
      $("day-title").textContent = (target === st.today ? "Today's set" : "Set") + ", " + fmtDay(target);
      $("day-sub").textContent = S.paper.phase_label + ". " + (S.paper.level_label ? S.paper.level_label + ". " : "") +
        S.paper.questions.length + " questions, suggested " + S.paper.suggested_minutes + " minutes.";
      if (res.unlocked) {
        return api("/api/key?day=" + target).then(function (k) { S.key = k.key; S.attempt = k.attempt; S.meta = k.meta; drawPaper(); });
      }
      drawPaper();
    }).catch(function (e) { clear(body); add(body, h("div", { class: "banner bad", text: "Could not load the set: " + e.message })); });
  }

  function buildNow() {
    post("/api/generate", {}).then(function (r) { toast(r.message || "Building"); setTimeout(loadStatus, 800); })
      .catch(function (e) { toast("Could not start: " + e.message); });
  }

  function isCodeLine(line) {
    return /^( {2,}|\t)/.test(line) || /^(def |for |while |if |elif |else:|print\(|return |class |import |from |[A-Za-z_]\w*(\[[^\]]*\])? ?(=|\+=|-=|\*=|\/\/=) )/.test(line) ||
      /^(BEGIN|END|SET |FOR |WHILE |IF |ELSE|PRINT|function |var |int |let )/i.test(line) && /[=(:]/.test(line);
  }
  function stemNode(text) {
    var box = h("div", { class: "stem" });
    var lines = String(text).split("\n"), buf = [], mode = null;
    function flush() {
      if (!buf.length) return;
      if (mode === "code") add(box, h("pre", { class: "code", text: buf.join("\n") }));
      else add(box, h("div", { text: buf.join("\n") }));
      buf = [];
    }
    lines.forEach(function (ln) {
      var m = isCodeLine(ln) ? "code" : "text";
      if (m !== mode) { flush(); mode = m; }
      buf.push(ln);
    });
    flush();
    return box;
  }

  function drawPaper() {
    var p = S.paper, body = clear($("day-body")), banner = clear($("day-banner"));
    var unlocked = !!S.key;
    if (unlocked) add(body, scoreCard());
    else {
      add(banner, h("div", { class: "banner info" }, h("div", null,
        h("b", { text: "Solve, then submit to open the answer key." }),
        h("div", { class: "muted", text: "Marking as in XAT: +1 right, -0.25 wrong, 0 skipped. Your answers save as you go. Solving on paper? Use \"Solved on paper\" at the bottom, or tell Claude \"solved\" with your answers (1B 2C ...)." }))));
    }
    var jump = h("div", { class: "jump chips" });
    if (unlocked) {
      [["all", "All"], ["wrong", "Wrong"], ["skipped", "Skipped"], ["correct", "Right"]].forEach(function (f) {
        add(jump, h("button", { class: "chip" + (S.filter === f[0] ? " on" : ""), type: "button", text: f[1], onclick: function () { S.filter = f[0]; drawPaper(); } }));
      });
    }
    p.sections.forEach(function (sec) {
      var answered = 0;
      for (var i = sec.first; i <= sec.last; i++) if (S.draft.answers[String(i)]) answered++;
      add(jump, h("a", { class: "chip" + (!unlocked && answered === sec.count ? " done" : ""), href: "#", onclick: function (ev) { ev.preventDefault(); var t = $("sec-" + sec.code); if (t) t.scrollIntoView({ behavior: "smooth" }); } },
        sec.code + " ", h("span", { class: "n", text: unlocked ? sec.first + "-" + sec.last : answered + "/" + sec.count })));
    });
    add(body, jump);
    var shownGroup = {};
    var res = (S.attempt && S.attempt.results) || {};
    p.sections.forEach(function (sec) {
      var qs = p.questions.slice(sec.first - 1, sec.last);
      if (unlocked && S.filter !== "all") qs = qs.filter(function (q) { return (res[String(q.n)] || "skipped") === S.filter || (S.attempt.mode === "self-check" && S.filter === "all"); });
      if (unlocked && S.filter !== "all" && !qs.length) return;
      add(body, h("div", { class: "section-head", id: "sec-" + sec.code }, h("h2", { text: sec.name }),
        h("span", { class: "muted", text: "Questions " + sec.first + " to " + sec.last + ", for " + sec.exams.join(" and ") })));
      qs.forEach(function (q) {
        if (q.group && !shownGroup[q.group] && p.groups[q.group]) { shownGroup[q.group] = 1; add(body, stimNode(p.groups[q.group])); }
        add(body, questionNode(q, unlocked));
      });
    });
    if (!unlocked) { $("sheetbar").hidden = false; updateBar(); }
  }

  function stimNode(g) {
    var kind = g.section === "RC" ? "Passage" : (g.section === "DM" ? "Case" : (g.section === "LR" ? "Puzzle" : "Data"));
    var box = h("div", { class: "stim" + (g.section === "RC" ? " passage" : "") }, h("h3", { text: kind + (g.kind ? ": " + g.kind : "") }));
    if (g.title) add(box, h("div", { class: "title", text: g.title }));
    if (g.text) add(box, h("div", { class: "text", text: g.text }));
    if (g.table) add(box, tableNode(g.table));
    if (g.chart) add(box, chartNode(g.chart));
    if (g.qnums && g.qnums.length) add(box, h("div", { class: "for", text: "For questions " + g.qnums[0] + (g.qnums.length > 1 ? " to " + g.qnums[g.qnums.length - 1] : "") }));
    return box;
  }

  function tableNode(t) {
    var tb = h("table", { class: "data" });
    add(tb, h("thead", null, h("tr", null, t.columns.map(function (c) { return h("th", { text: String(c) }); }))));
    add(tb, h("tbody", null, t.rows.map(function (r) { return h("tr", null, r.map(function (c) { return h("td", { text: fmtNum(c) }); })); })));
    return h("div", { class: "tbl-wrap" }, tb);
  }

  // ------------------------------------------------------------ charts (DI sets)
  function niceStep(raw) {
    if (raw <= 0) return 1;
    var mag = Math.pow(10, Math.floor(Math.log10(raw))), r = raw / mag;
    return (r <= 1 ? 1 : r <= 2 ? 2 : r <= 2.5 ? 2.5 : r <= 5 ? 5 : 10) * mag;
  }
  function chartNode(c) {
    var box = h("div", { class: "chart" }, h("div", { class: "ct", text: c.title || "" }), c.unit ? h("div", { class: "cu", text: "Unit: " + c.unit }) : null);
    var series = c.series || [], labels = c.labels || [];
    if (c.type === "pie") { add(box, pieSvg(labels, series[0] ? series[0].values : [])); return box; }
    if (series.length > 1) {
      add(box, h("div", { class: "legend" }, series.map(function (se, i) { return h("span", null, h("i", { class: "c" + (i % 6 + 1) }), se.name || "Series " + (i + 1)); })));
    }
    add(box, c.type === "line" ? lineSvg(labels, series) : barSvg(labels, series));
    return box;
  }
  function frame(labels, series, W, H, M) {
    var all = [];
    series.forEach(function (se) { (se.values || []).forEach(function (v) { all.push(v); }); });
    var lo = Math.min(0, Math.min.apply(null, all)), hi = Math.max(0, Math.max.apply(null, all));
    var step = niceStep((hi - lo) / 5 || 1);
    lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step; if (hi === lo) hi = lo + step;
    var y = function (v) { return M.t + (H - M.t - M.b) * (1 - (v - lo) / (hi - lo)); };
    var svg = s("svg", { viewBox: "0 0 " + W + " " + H, role: "img" });
    for (var v = lo; v <= hi + step / 2; v += step) {
      svg.appendChild(s("line", { class: "grid", x1: M.l, x2: W - M.r, y1: y(v), y2: y(v) }));
      svg.appendChild(s("text", { x: M.l - 6, y: y(v) + 4, "text-anchor": "end" }, fmtNum(Math.round(v * 1000) / 1000)));
    }
    svg.appendChild(s("line", { class: "axis", x1: M.l, x2: W - M.r, y1: y(0), y2: y(0) }));
    return { svg: svg, y: y };
  }
  function barSvg(labels, series) {
    var W = 720, H = 300, M = { l: 54, r: 12, t: 22, b: 40 };
    var f = frame(labels, series, W, H, M), svg = f.svg, y = f.y;
    var band = (W - M.l - M.r) / Math.max(1, labels.length), n = Math.max(1, series.length);
    var bw = Math.min(40, (band * 0.78) / n - 2);
    labels.forEach(function (lab, i) {
      var x0 = M.l + band * i + (band - (bw + 2) * n + 2) / 2;
      series.forEach(function (se, j) {
        var v = se.values[i], x = x0 + j * (bw + 2), top = y(Math.max(0, v)), bot = y(Math.min(0, v)), r = Math.min(4, bw / 2, bot - top);
        var d = v >= 0
          ? "M" + x + "," + bot + "V" + (top + r) + "Q" + x + "," + top + " " + (x + r) + "," + top + "H" + (x + bw - r) + "Q" + (x + bw) + "," + top + " " + (x + bw) + "," + (top + r) + "V" + bot + "Z"
          : "M" + x + "," + top + "V" + (bot - r) + "Q" + x + "," + bot + " " + (x + r) + "," + bot + "H" + (x + bw - r) + "Q" + (x + bw) + "," + bot + " " + (x + bw) + "," + (bot - r) + "V" + top + "Z";
        var p = s("path", { d: d, class: "c" + (j % 6 + 1) });
        p.appendChild(s("title", {}, lab + (se.name ? ", " + se.name : "") + ": " + fmtNum(v)));
        svg.appendChild(p);
        svg.appendChild(s("text", { class: "val", x: x + bw / 2, y: v >= 0 ? top - 5 : bot + 13, "text-anchor": "middle" }, fmtNum(v)));
      });
      svg.appendChild(s("text", { x: M.l + band * i + band / 2, y: H - M.b + 18, "text-anchor": "middle" }, String(lab)));
    });
    return svg;
  }
  function lineSvg(labels, series) {
    var W = 720, H = 300, M = { l: 54, r: 24, t: 24, b: 40 };
    var f = frame(labels, series, W, H, M), svg = f.svg, y = f.y;
    var step = (W - M.l - M.r) / Math.max(1, labels.length - 1);
    var x = function (i) { return labels.length === 1 ? (W / 2) : M.l + step * i; };
    series.forEach(function (se, j) {
      var d = se.values.map(function (v, i) { return (i ? "L" : "M") + x(i) + "," + y(v); }).join("");
      svg.appendChild(s("path", { d: d, class: "ln c" + (j % 6 + 1) }));
      se.values.forEach(function (v, i) {
        var m = s("circle", { cx: x(i), cy: y(v), r: 4.5, class: "mk c" + (j % 6 + 1) });
        m.appendChild(s("title", {}, labels[i] + (se.name ? ", " + se.name : "") + ": " + fmtNum(v)));
        svg.appendChild(m);
        svg.appendChild(s("text", { class: "val", x: x(i), y: y(v) - 9 - (series.length > 1 ? j * 0 : 0), "text-anchor": "middle" }, fmtNum(v)));
      });
    });
    labels.forEach(function (lab, i) { svg.appendChild(s("text", { x: x(i), y: H - M.b + 18, "text-anchor": "middle" }, String(lab))); });
    return svg;
  }
  function pieSvg(labels, values) {
    var W = 720, H = 300, cx = 180, cy = 150, R = 120;
    var svg = s("svg", { viewBox: "0 0 " + W + " " + H, role: "img" });
    var tot = values.reduce(function (a, b) { return a + b; }, 0) || 1, a0 = -Math.PI / 2;
    values.forEach(function (v, i) {
      var a1 = a0 + 2 * Math.PI * v / tot, large = a1 - a0 > Math.PI ? 1 : 0;
      var d = values.length === 1 ? "M" + cx + "," + (cy - R) + "A" + R + "," + R + " 0 1 1 " + (cx - 0.01) + "," + (cy - R) + "Z"
        : "M" + cx + "," + cy + "L" + (cx + R * Math.cos(a0)) + "," + (cy + R * Math.sin(a0)) + "A" + R + "," + R + " 0 " + large + " 1 " + (cx + R * Math.cos(a1)) + "," + (cy + R * Math.sin(a1)) + "Z";
      var p = s("path", { d: d, class: "slice c" + (i % 6 + 1) });
      p.appendChild(s("title", {}, labels[i] + ": " + fmtNum(v)));
      svg.appendChild(p);
      var am = (a0 + a1) / 2, share = v / tot;
      if (share >= 0.06) svg.appendChild(s("text", { class: "val", x: cx + R * 0.64 * Math.cos(am), y: cy + R * 0.64 * Math.sin(am) + 4, "text-anchor": "middle", fill: "#fff" }, Math.round(share * 1000) / 10 + "%"));
      a0 = a1;
    });
    labels.forEach(function (lab, i) {
      var yy = 40 + i * 28;
      svg.appendChild(s("rect", { x: 350, y: yy - 11, width: 12, height: 12, rx: 3, class: "c" + (i % 6 + 1) }));
      svg.appendChild(s("text", { x: 370, y: yy }, lab + ": " + fmtNum(values[i]) + " (" + Math.round(values[i] / tot * 1000) / 10 + "%)"));
    });
    return svg;
  }

  // ------------------------------------------------------------ one question
  function questionNode(q, unlocked) {
    var n = String(q.n), mine = S.draft.answers[n] || (S.attempt && S.attempt.answers ? S.attempt.answers[n] : "");
    var flagged = S.draft.flags.indexOf(q.n) >= 0;
    var card = h("div", { class: "q" + (flagged && !unlocked ? " flagged" : ""), id: "q" + n });
    var top = h("div", { class: "q-top" }, h("span", { class: "q-n", text: "Q" + n }), h("span", { class: "tag " + q.difficulty, text: q.difficulty }),
      h("span", { class: "q-topic", text: q.topic + (q.subtopic ? ", " + q.subtopic : "") }));
    if (!unlocked) {
      add(top, h("span", { class: "q-tools" }, h("button", { class: "flag-btn" + (flagged ? " on" : ""), type: "button", text: flagged ? "Flagged" : "Flag",
        title: "Mark to come back to", onclick: function () { toggleFlag(q.n); } })));
    }
    add(card, top, stemNode(q.stem));
    var opts = h("div", { class: "opts", role: unlocked ? null : "radiogroup", "aria-label": "Options for question " + n });
    var k = unlocked ? S.key.answers[n] : null, res = unlocked && S.attempt.results ? S.attempt.results[n] : null;
    q.options.forEach(function (o, i) {
      var L = LET[i], cls = "opt";
      if (!unlocked && mine === L) cls += " sel";
      if (unlocked) { if (k.answer === L) cls += " right"; else if (mine === L) cls += " wrong"; }
      add(opts, h("button", { class: cls, type: "button", disabled: unlocked, role: unlocked ? null : "radio", "aria-checked": !unlocked && mine === L ? "true" : "false",
        onclick: unlocked ? null : function () { choose(q.n, L); } }, h("span", { class: "L", text: L }), h("span", { class: "t", text: o })));
    });
    add(card, opts);
    if (unlocked) add(card, explainNode(k, res, mine));
    return card;
  }

  function explainNode(k, res, mine) {
    var tag = res === "correct" ? h("span", { class: "tag ok", text: "right" }) : res === "wrong" ? h("span", { class: "tag bad", text: "you chose " + mine })
      : res === "skipped" ? h("span", { class: "tag skip", text: "skipped" }) : null;
    var how = [];
    if (k.checks && k.checks.code === "pass") how.push("answer recomputed by code");
    if (k.checks && k.checks.solver === "agree") how.push("confirmed by an independent blind solve");
    return h("div", { class: "explain" }, h("div", { class: "h" }, "Answer " + k.answer, tag),
      h("div", { class: "b", text: k.explanation }),
      k.shortcut ? h("div", { class: "sc" }, h("b", { text: "Shortcut: " }), k.shortcut) : null,
      how.length ? h("div", { class: "how", text: "Checked: " + how.join(", ") + "." }) : null);
  }

  function choose(n, L) {
    var key = String(n);
    if (S.draft.answers[key] === L) delete S.draft.answers[key]; else S.draft.answers[key] = L;
    var card = $("q" + key);
    if (card) card.querySelectorAll(".opt").forEach(function (b, i) {
      var on = S.draft.answers[key] === LET[i]; b.classList.toggle("sel", on); b.setAttribute("aria-checked", on ? "true" : "false");
    });
    updateBar(); refreshJump(); saveSoon();
  }
  function toggleFlag(n) {
    var i = S.draft.flags.indexOf(n);
    if (i >= 0) S.draft.flags.splice(i, 1); else S.draft.flags.push(n);
    var card = $("q" + n);
    if (card) {
      var on = S.draft.flags.indexOf(n) >= 0;
      card.classList.toggle("flagged", on);
      var b = card.querySelector(".flag-btn"); b.classList.toggle("on", on); b.textContent = on ? "Flagged" : "Flag";
    }
    updateBar(); saveSoon();
  }
  function refreshJump() {
    var chips = document.querySelectorAll(".jump .chip");
    S.paper.sections.forEach(function (sec, idx) {
      var c = chips[idx]; if (!c) return;
      var answered = 0; for (var i = sec.first; i <= sec.last; i++) if (S.draft.answers[String(i)]) answered++;
      c.classList.toggle("done", answered === sec.count);
      var nEl = c.querySelector(".n"); if (nEl) nEl.textContent = answered + "/" + sec.count;
    });
  }
  function updateBar() {
    var n = S.paper.questions.length, a = Object.keys(S.draft.answers).length;
    $("sb-answered").textContent = a + "/" + n;
    $("sb-flagged").textContent = String(S.draft.flags.length);
    showTime();
  }
  function saveSoon() { clearTimeout(S.saveT); S.saveT = setTimeout(saveDraft, 700); }
  function saveDraft() {
    if (!S.paper || S.key) return;
    post("/api/draft", { day: S.paper.day, answers: S.draft.answers, flags: S.draft.flags, elapsed_s: Math.floor(S.elapsed) })
      .catch(function (e) { if (e.status !== 409) toast("Could not save: " + e.message); });
  }

  // ------------------------------------------------------------ timer
  function showTime() {
    $("sb-time").textContent = fmtTime(S.elapsed);
    var t = $("sb-timer"), lim = (S.paper ? S.paper.suggested_minutes : 110) * 60;
    t.classList.toggle("running", S.running); t.classList.toggle("over", S.elapsed > lim);
    $("sb-timer-lbl").textContent = S.running ? "of " + (S.paper ? S.paper.suggested_minutes : "") + " min, tap to pause" : (S.elapsed ? "paused" : "start timer");
  }
  function startTimer() {
    if (S.running) return; S.running = true; var last = Date.now();
    S.timer = setInterval(function () { var now = Date.now(); S.elapsed += (now - last) / 1000; last = now; showTime();
      if (Math.floor(S.elapsed) % 20 === 0) saveSoon(); }, 1000);
    showTime();
  }
  function stopTimer(silent) { if (!S.running) return; S.running = false; clearInterval(S.timer); if (!silent) { showTime(); saveSoon(); } else saveDraft(); }
  $("sb-timer").addEventListener("click", function () { if (S.running) stopTimer(); else startTimer(); });
  document.addEventListener("visibilitychange", function () { if (document.hidden && S.paper && !S.key) saveDraft(); });

  // ------------------------------------------------------------ submit
  function modal(title, text, ok, onOk) {
    $("modal-title").textContent = title; $("modal-text").textContent = text; $("modal-ok").textContent = ok;
    $("modal").hidden = false; $("modal-ok").focus();
    $("modal-ok").onclick = function () { $("modal").hidden = true; onOk(); };
  }
  $("modal-cancel").addEventListener("click", function () { $("modal").hidden = true; });
  $("modal").addEventListener("click", function (e) { if (e.target === $("modal")) $("modal").hidden = true; });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") $("modal").hidden = true; });

  function submit(mode) {
    stopTimer(true);
    var body = { day: S.paper.day, answers: mode === "self-check" ? {} : S.draft.answers, elapsed_s: Math.floor(S.elapsed), mode: mode };
    post("/api/submit", body).then(function (k) {
      S.key = k.key; S.attempt = k.attempt; S.filter = "all"; $("sheetbar").hidden = true;
      window.scrollTo(0, 0); drawPaper(); loadStatus(); S.mistakes = null;
    }).catch(function (e) { toast("Could not submit: " + e.message); });
  }
  $("sb-submit").addEventListener("click", function () {
    var n = S.paper.questions.length, a = Object.keys(S.draft.answers).length;
    modal("Submit " + a + " of " + n + " answers?",
      (n - a ? (n - a) + " unanswered will count as skipped. " : "") + "Your answers lock and the answer key opens with explanations.", "Submit and show key",
      function () { submit("site"); });
  });
  $("sb-paper").addEventListener("click", function () {
    modal("Show the key without a score?", "Use this if you solved on paper. The key opens now; to get scored, send Claude your answers as 1B 2C 3D ... afterwards.", "Show the key",
      function () { submit("self-check"); });
  });

  // ------------------------------------------------------------ score card
  function scoreCard() {
    var a = S.attempt, card = h("div", { class: "card score-card" });
    if (!a || !a.totals) {
      add(card, h("div", { class: "score-big", text: "Key open" }), h("div", { class: "score-meta" },
        "You unlocked this set without entering answers, so there is no score. To get scored, send Claude your answers as 1B 2C 3D ... (or paste one letter per question in order)."));
      return card;
    }
    var t = a.totals;
    add(card, h("div", null, h("div", { class: "score-big" }, String(t.correct), h("small", { text: " / " + t.total })), h("div", { class: "muted", text: "right" })),
      h("div", { class: "score-meta" }, h("div", null, h("b", { text: "Net " + t.net.toFixed(2) }), " with XAT marking (+1, -0.25). Accuracy " + pct(t.accuracy) + " on the " + t.attempted + " you attempted."),
        h("div", { text: t.wrong + " wrong, " + t.skipped + " skipped" + (a.elapsed_s >= 60 ? ", " + Math.round(a.elapsed_s / 60) + " minutes" : "") + "." })));
    var rows = h("div", { class: "sec-rows" });
    S.paper.sections.forEach(function (sec) {
      var x = a.sections[sec.code]; if (!x) return;
      add(rows, h("div", { class: "sec-row" }, h("span", { text: sec.name }),
        h("div", { class: "bar", title: x.correct + " right, " + x.wrong + " wrong, " + x.skipped + " skipped" },
          h("span", { class: "c", style: "width:" + (100 * x.correct / x.total) + "%" }), h("span", { class: "w", style: "width:" + (100 * x.wrong / x.total) + "%" })),
        h("span", { class: "num", text: x.correct + "/" + x.total + (x.wrong ? ", " + x.wrong + " wrong" : "") })));
    });
    add(card, rows);
    return card;
  }

  // ------------------------------------------------------------ sets
  function renderSets() {
    var box = clear($("sets-list")), st = S.status;
    if (!st.days.length) { add(box, h("div", { class: "empty" }, h("h2", { text: "No sets yet" }), "The first one is built at " + st.schedule + ".")); return; }
    st.days.forEach(function (d) {
      var tag = d.status === "solved" ? h("span", { class: "tag ok", text: d.totals.correct + "/" + d.totals.total + " right, net " + d.totals.net.toFixed(2) })
        : d.status === "unlocked" ? h("span", { class: "tag skip", text: "key open, no score" })
        : h("span", { class: "tag medium", text: d.answered ? d.answered + " answered so far" : "not started" });
      add(box, h("div", { class: "card set-row" }, h("span", { class: "d", text: fmtDay(d.day) }), tag,
        h("span", { class: "grow", text: d.n + " questions. " + (d.phase_label || "") }),
        h("a", { class: "btn sm" + (d.status === "locked" ? " primary" : ""), href: "#/day/" + d.day, text: d.status === "locked" ? "Solve" : "Review" })));
    });
  }

  // ------------------------------------------------------------ progress
  function renderProgress() {
    var box = clear($("progress-body"));
    add(box, h("div", { class: "empty", text: "Loading..." }));
    api("/api/progress").then(function (p) {
      clear(box);
      var scored = p.days.filter(function (d) { return d.totals && d.totals.attempted; });
      if (!scored.length) { add(box, h("div", { class: "empty" }, h("h2", { text: "Nothing scored yet" }), "Submit a set (or send Claude your answers) and your accuracy by section and topic shows here.")); return; }
      var c = 0, att = 0, net = 0;
      scored.forEach(function (d) { c += d.totals.correct; att += d.totals.attempted; net += d.totals.net; });
      add(box, h("div", { class: "tiles" },
        h("div", { class: "tile" }, h("div", { class: "v", text: String(p.solved_days) }), h("div", { class: "l", text: "sets done" })),
        h("div", { class: "tile" }, h("div", { class: "v", text: String(p.streak) }), h("div", { class: "l", text: "day streak" })),
        h("div", { class: "tile" }, h("div", { class: "v", text: pct(att ? c / att : null) }), h("div", { class: "l", text: "accuracy, all scored sets" })),
        h("div", { class: "tile" }, h("div", { class: "v", text: (net / scored.length).toFixed(1) }), h("div", { class: "l", text: "average net per set" }))));
      var secs = h("div", { class: "card" }, h("h3", { text: "By section, last 28 days" }));
      var rows = h("div", { class: "sec-rows" });
      Object.keys(p.sections).forEach(function (code) {
        var x = p.sections[code];
        add(rows, h("div", { class: "sec-row" }, h("span", { text: p.names[code] || code }),
          h("div", { class: "bar" }, h("span", { class: "acc", style: "width:" + (100 * (x.accuracy || 0)) + "%" })),
          h("span", { class: "num", text: pct(x.accuracy) + " of " + x.attempted })));
      });
      add(secs, rows);
      var weak = p.topics.filter(function (t) { return t.attempted >= 1; }).slice(0, 12);
      var tt = h("table", { class: "plain" }, h("thead", null, h("tr", null, h("th", { text: "Topic" }), h("th", { text: "Section" }), h("th", { text: "Right" }), h("th", { text: "Last seen" }))),
        h("tbody", null, weak.map(function (t) { return h("tr", null, h("td", { text: t.topic }), h("td", { text: t.section }), h("td", { class: "num", text: t.correct + " of " + t.attempted + " (" + pct(t.accuracy) + ")" }), h("td", { text: t.last })); })));
      add(box, h("div", { class: "grid2" }, secs, h("div", { class: "card" }, h("h3", { text: "Weakest topics first" }), tt)));
      var hist = h("table", { class: "plain" }, h("thead", null, h("tr", null, h("th", { text: "Day" }), h("th", { text: "Right" }), h("th", { text: "Wrong" }), h("th", { text: "Skipped" }), h("th", { text: "Net" }), h("th", { text: "Minutes" }))),
        h("tbody", null, scored.map(function (d) { return h("tr", null, h("td", null, h("a", { href: "#/day/" + d.day, text: fmtDay(d.day) })), h("td", { class: "num", text: String(d.totals.correct) }),
          h("td", { class: "num", text: String(d.totals.wrong) }), h("td", { class: "num", text: String(d.totals.skipped) }), h("td", { class: "num", text: d.totals.net.toFixed(2) }),
          h("td", { class: "num", text: d.elapsed_s ? String(Math.round(d.elapsed_s / 60)) : "" })); })));
      add(box, h("div", { class: "card", style: "margin-top:14px" }, h("h3", { text: "Every scored set" }), hist));
    }).catch(function (e) { clear(box); add(box, h("div", { class: "banner bad", text: e.message })); });
  }

  // ------------------------------------------------------------ mistakes
  function renderMistakes() {
    var box = clear($("mistakes-list"));
    var go = function (list) {
      S.mistakes = list;
      $("tab-mistakes").textContent = list.length ? String(list.length) : "";
      var f = clear($("mistake-filter")), codes = [];
      list.forEach(function (m) { if (codes.indexOf(m.section) < 0) codes.push(m.section); });
      [["all", "All"]].concat(codes.map(function (c) { return [c, c]; })).forEach(function (x) {
        add(f, h("button", { class: "chip" + (S.mfilter === x[0] ? " on" : ""), type: "button", onclick: function () { S.mfilter = x[0]; go(list); } },
          x[1] + " ", h("span", { class: "n", text: String(x[0] === "all" ? list.length : list.filter(function (m) { return m.section === x[0]; }).length) })));
      });
      clear(box);
      var shown = list.filter(function (m) { return S.mfilter === "all" || m.section === S.mfilter; });
      if (!shown.length) { add(box, h("div", { class: "empty" }, h("h2", { text: "No mistakes to redo" }), "Wrong and skipped questions from scored sets collect here.")); return; }
      shown.forEach(function (m) { add(box, mistakeNode(m)); });
    };
    if (S.mistakes) go(S.mistakes); else api("/api/mistakes").then(go).catch(function (e) { add(box, h("div", { class: "banner bad", text: e.message })); });
  }
  function mistakeNode(m) {
    var card = h("div", { class: "q" });
    add(card, h("div", { class: "q-top" }, h("span", { class: "q-n", text: m.section }), h("span", { class: "tag " + m.difficulty, text: m.difficulty }),
      h("span", { class: "q-topic", text: m.topic + ", " + fmtDay(m.day) + ", Q" + m.n }),
      h("span", { class: "q-tools" }, h("span", { class: "tag " + (m.result === "wrong" ? "bad" : "skip"), text: m.result === "wrong" ? "you chose " + m.mine : "skipped" }))));
    if (m.group) {
      var d = h("details", null, h("summary", { text: m.section === "RC" ? "Show the passage" : "Show the set" }));
      add(d, stimNode(m.group)); add(card, d);
    }
    add(card, stemNode(m.stem));
    var opts = h("div", { class: "opts" }), picked = null;
    var reveal = function () {
      opts.querySelectorAll(".opt").forEach(function (b, i) { b.disabled = true; if (LET[i] === m.answer) b.classList.add("right"); else if (LET[i] === picked) b.classList.add("wrong"); });
      btn.hidden = true;
      add(card, explainNode({ answer: m.answer, explanation: m.explanation, shortcut: m.shortcut }, null, null));
    };
    m.options.forEach(function (o, i) {
      add(opts, h("button", { class: "opt", type: "button", onclick: function () { picked = LET[i]; reveal(); } }, h("span", { class: "L", text: LET[i] }), h("span", { class: "t", text: o })));
    });
    var btn = h("button", { class: "btn sm reveal", type: "button", text: "Show the answer", onclick: reveal });
    add(card, opts, btn);
    return card;
  }

  // ------------------------------------------------------------ health
  function renderHealth() {
    var box = clear($("health-body"));
    api("/api/health").then(function (hh) {
      var r = hh.run || {};
      var kv = h("dl", { class: "kv" },
        h("dt", { text: "Next build" }), h("dd", { text: (hh.next_build || "").replace("T", " ") + " (daily at " + hh.schedule + (hh.retries.length ? "; retries at " + hh.retries.join(", ") + " if the set is missing" : "") + "; also at login)" }),
        h("dt", { text: "Last build" }), h("dd", { text: r.running ? "running since " + r.started : (r.summary || "none yet") + (r.finished ? " (" + r.finished.replace("T", " ") + ")" : "") }),
        h("dt", { text: "Claude models" }), h("dd", { text: "writes with " + hh.models.generate + ", blind-solves with " + hh.models.solve }));
      var top = h("div", { class: "card" }, h("h3", { text: "Daily build" }), kv);
      if (r.running && r.sections) add(top, h("div", { class: "chips", style: "margin-top:10px" }, Object.keys(r.sections).map(function (k) { return h("span", { class: "chip", text: k + ": " + r.sections[k] }); })));
      var hasToday = S.status.days.some(function (d) { return d.day === S.status.today; });
      if (!r.running && !hasToday) add(top, h("div", { style: "margin-top:12px" }, h("button", { class: "btn primary", text: "Build today's set now", onclick: buildNow })));
      add(box, top);
      hh.builds.forEach(function (b) {
        var stages = b.rejected_by_stage || {};
        var card = h("div", { class: "card", style: "margin-top:12px" }, h("h3", { text: fmtDay(b.day) }),
          h("dl", { class: "kv" },
            h("dt", { text: "Published" }), h("dd", { text: b.published + " of " + b.planned + " planned" }),
            h("dt", { text: "Checks" }), h("dd", { text: b.code_checked + " answers recomputed by code; " + b.solver_agreed + " confirmed by a blind solve" }),
            h("dt", { text: "Drafts dropped" }), h("dd", { text: b.rejected + (b.rejected ? " (" + Object.keys(stages).map(function (k) { return stages[k] + " at " + k; }).join(", ") + ")" : "") }),
            h("dt", { text: "Build time" }), h("dd", { text: Math.round((b.seconds || 0) / 60) + " minutes, " + b.claude_calls + " Claude calls" })));
        if (b.rejections && b.rejections.length) {
          var d = h("details", { style: "margin-top:10px" }, h("summary", { text: "Why drafts were dropped" }));
          add(d, h("table", { class: "plain" }, h("tbody", null, b.rejections.map(function (x) {
            return h("tr", null, h("td", { text: x.section }), h("td", { text: x.stage }), h("td", { text: x.why }), h("td", { class: "muted", text: x.stem }));
          }))));
          add(card, d);
        }
        add(box, card);
      });
      add(box, h("div", { class: "card", style: "margin-top:12px" }, h("h3", { text: "Today's log" }), h("pre", { class: "log", text: (hh.log || []).join("\n") || "(empty)" })));
    }).catch(function (e) { add(box, h("div", { class: "banner bad", text: e.message })); });
  }

  // ------------------------------------------------------------ start
  loadStatus().then(render).catch(function (e) { add($("day-body"), h("div", { class: "banner bad", text: "The aptitude server is not answering: " + e.message })); });
  setInterval(function () { loadStatus().catch(function () {}); }, 10000);
  api("/api/mistakes").then(function (l) { $("tab-mistakes").textContent = l.length ? String(l.length) : ""; }).catch(function () {});
})();
