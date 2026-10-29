(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const VIEWS = ["overview", "ask", "add"];
  const state = { source: "real", platform: "", brand: "", view: "overview", info: null, meta: null, token: 0 };
  const asked = [];
  let pending = false;

  // Per-platform wording for the add form.
  const PLAT = {
    instagram: { likes: "", views: "videos and reels only", shares: "Instagram does not show shares: leave empty", url: "Looks like instagram.com/p/… or instagram.com/reel/…" },
    facebook: { likes: "total reactions, all types", views: "video views", shares: "", url: "Open the post, click its date, and copy the address bar." },
    tiktok: { likes: "", views: "the plays number", shares: "", url: "Looks like tiktok.com/@brand/video/…" },
  };
  const PLAIN = [
    [/post_url/g, "post link"], [/post_date/g, "post date"], [/post_time_pkt/g, "time"], [/collected_on/g, "day you read the numbers"],
    [/media_type/g, "type of post"], [/followers_on_page/g, "followers"], [/collector name is empty/g, "your name is empty"],
    [/config\/social_brands\.json/g, "the brand list"],
  ];
  const plain = (t) => PLAIN.reduce((s, [re, to]) => s.replace(re, to), t);

  // ---------- helpers. Everything that comes from data is added with textContent, never as HTML.
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function safeHref(href) {
    try {
      const u = new URL(href);
      return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
    } catch { return null; }
  }
  async function call(path, opts) {
    const res = await fetch(path, opts);
    let data = {};
    try { data = await res.json(); } catch { /* not JSON */ }
    if (!res.ok) throw new Error(data.error || "The server could not do that.");
    return data;
  }
  const getJSON = (path, params) => {
    const qs = new URLSearchParams(Object.entries(params || {}).filter(([, v]) => v));
    return call(path + (qs.toString() ? "?" + qs : ""));
  };
  const postJSON = (path, body) => call(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const scope = () => ({ source: state.source, platform: state.platform, brand: state.brand });
  const platName = (k) => (state.info && state.info.platform_status.find((p) => p.key === k) || {}).name || k;

  // ---------- shared renderers
  function renderBars(block) {
    const wrap = el("div", "block");
    wrap.appendChild(el("h4", null, block.title));
    const list = el("div", "bars");
    const max = Math.max(...block.items.map((i) => i.value), 0) || 1;
    block.items.forEach((item, idx) => {
      const row = el("div", "bar-row" + (item.highlight ? " best" : "") + (item.flag ? " flag" : ""));
      row.style.setProperty("--i", idx);
      const label = el("div", "bar-label", item.label);
      if (item.note) label.appendChild(el("small", null, item.flag ? item.note + " · small sample" : item.note));
      const track = el("div", "track");
      const fill = el("div", "fill");
      fill.style.width = Math.max((item.value / max) * 100, 1.5) + "%";
      track.appendChild(fill);
      row.append(label, track, el("div", "bar-val", item.display));
      list.appendChild(row);
    });
    wrap.appendChild(list);
    return wrap;
  }

  function renderTable(block) {
    const wrap = el("div", "block scroll");
    const table = el("table");
    const head = el("tr");
    block.columns.forEach((c) => head.appendChild(el("th", c.align === "right" ? "r" : null, c.label)));
    table.appendChild(el("thead")).appendChild(head);
    const body = el("tbody");
    block.rows.forEach((r) => {
      const tr = el("tr");
      block.columns.forEach((c) => {
        const td = el("td", c.align === "right" ? "r" : null);
        const v = r[c.key];
        if (v && typeof v === "object") {
          const href = v.href ? safeHref(v.href) : null;
          if (href) {
            const a = el("a", null, v.text);
            a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
            td.appendChild(a);
          } else td.textContent = v.text;
        } else td.textContent = v === undefined || v === null ? "" : v;
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    table.appendChild(body);
    wrap.appendChild(table);
    return wrap;
  }

  const renderBlock = (b) => (b.type === "bars" ? renderBars(b) : renderTable(b));

  function notesList(notes) {
    const ul = el("ul", "notes");
    notes.forEach((n) => ul.appendChild(el("li", n.level === "warn" ? "warn" : "info", n.text)));
    return ul;
  }

  // ---------- overview
  function renderKpis(kpis) {
    const box = $("kpis");
    box.replaceChildren();
    kpis.forEach((k) => {
      const t = el("div", "kpi");
      t.appendChild(el("div", "k", k.label));
      t.appendChild(el("div", "v" + (/^[\d.,]+$/.test(k.value) ? "" : " text"), k.value));
      t.appendChild(el("div", "h", k.hint));
      box.appendChild(t);
    });
  }

  function renderPlats(list) {
    const box = $("plats");
    box.replaceChildren();
    const canAdd = state.source === "real";
    list.forEach((p) => {
      if (p.rows) {
        const b = el("button", "plat" + (state.platform === p.key ? " on" : ""));
        b.type = "button";
        b.setAttribute("aria-pressed", state.platform === p.key ? "true" : "false");
        const n = el("div", "n", p.name);
        if (p.sources.length) n.appendChild(el("span", "pill", p.sources[0]));
        b.append(n, el("div", "c", p.rows.toLocaleString() + (p.rows === 1 ? " post" : " posts")),
          el("div", "m", p.brands + (p.brands === 1 ? " brand" : " brands")), el("div", "m", p.date_from === p.date_to ? p.date_from : p.date_from + " to " + p.date_to));
        b.addEventListener("click", () => setFilter("platform", state.platform === p.key ? "" : p.key));
        box.appendChild(b);
      } else {
        const d = el("div", "plat none");
        d.append(el("div", "n", p.name), el("div", "c", "No data yet"));
        const manual = ["instagram", "facebook", "tiktok"].includes(p.key);
        d.appendChild(el("div", "m", manual ? "Type posts in by hand or import a spreadsheet." : "Collected with the collect.py script."));
        if (manual && canAdd) {
          const go = el("button", "go", "Add " + p.name + " posts");
          go.type = "button";
          go.addEventListener("click", () => { go_("add"); setAddPlatform(p.key); });
          d.appendChild(go);
        }
        box.appendChild(d);
      }
    });
  }

  function renderCard(sec) {
    const a = sec.answer;
    const card = el("article", "card" + (sec.id === "top" || sec.id === "next" ? " wide" : ""));
    card.id = "sec-" + sec.id;
    card.append(el("h3", null, sec.title), el("p", "help", sec.help), el("p", "lead", a.text));
    const bars = a.blocks.filter((b) => b.type === "bars");
    const tables = a.blocks.filter((b) => b.type === "table");
    (bars.length ? bars : tables.slice(0, 1)).forEach((b) => card.appendChild(renderBlock(b)));
    const extra = bars.length ? tables : tables.slice(1);
    const caution = a.notes.find((n) => n.level === "warn");
    if (caution) card.appendChild(el("p", "caution", caution.text));
    const rest = a.notes.filter((n) => n !== caution && n.level !== "method");
    const method = a.notes.filter((n) => n.level === "method");
    if (extra.length || rest.length || method.length) {
      const d = el("details");
      d.appendChild(el("summary", null, "More detail and cautions"));
      extra.forEach((b) => d.appendChild(renderTable(b)));
      if (rest.length) d.appendChild(notesList(rest));
      method.forEach((n) => d.appendChild(el("p", "method", n.text)));
      card.appendChild(d);
    }
    const foot = el("div", "card-foot");
    foot.appendChild(a.evidence ? el("div", "tag", a.evidence) : el("span"));
    const ask_ = el("button", "go", "Ask about this");
    ask_.type = "button";
    ask_.addEventListener("click", () => { go_("ask"); ask(sec.question); });
    foot.appendChild(ask_);
    card.appendChild(foot);
    return card;
  }

  function emptyPanel() {
    const box = el("div", "empty");
    const filtered = state.platform || state.brand;
    if (filtered) {
      box.append(el("h3", null, "No posts match these filters"), el("p", null, "Try another platform or brand, or clear the filters."));
      const b = el("button", "primary", "Clear filters");
      b.type = "button";
      b.addEventListener("click", () => { state.platform = state.brand = ""; fillFilters(); refresh(); });
      box.appendChild(b);
    } else {
      box.append(el("h3", null, "No posts yet"),
        el("p", null, "Add posts you copy from Instagram, Facebook or TikTok pages, or switch to demo data to see how every screen looks with made-up numbers."));
      const row = el("div", "actions");
      row.style.justifyContent = "center";
      const add = el("button", "primary", "Add data");
      add.type = "button";
      add.addEventListener("click", () => go_("add"));
      const demo = el("button", "ghost", "Show demo data");
      demo.type = "button";
      demo.addEventListener("click", () => setSource("demo"));
      row.append(add, demo);
      box.appendChild(row);
    }
    return box;
  }

  async function loadOverview() {
    const token = ++state.token;
    const cards = $("cards");
    cards.replaceChildren(el("div", "skeleton"), el("div", "skeleton"), el("div", "skeleton"), el("div", "skeleton"));
    try {
      const d = await getJSON("/api/dashboard", scope());
      if (token !== state.token) return;
      $("kpis").replaceChildren();
      if (!d.empty) renderKpis(d.kpis);
      renderPlats(d.platform_status);
      cards.replaceChildren();
      if (d.empty) cards.appendChild(emptyPanel());
      else d.sections.forEach((s) => cards.appendChild(renderCard(s)));
    } catch (e) {
      if (token !== state.token) return;
      const box = el("div", "empty");
      box.append(el("h3", null, "Could not load the overview"), el("p", null, e.message + " Check that the terminal running chatbot/server.py is still open."));
      cards.replaceChildren(box);
    }
  }

  // ---------- chat
  function renderAnswer(a, failed) {
    const card = el("article", "msg bot" + (failed ? " failed" : ""));
    card.appendChild(el("p", "lead", a.text));
    (a.blocks || []).forEach((b) => card.appendChild(renderBlock(b)));
    const plainNotes = (a.notes || []).filter((n) => n.level !== "method");
    if (plainNotes.length) card.appendChild(notesList(plainNotes));
    const method = (a.notes || []).filter((n) => n.level === "method");
    if (method.length) {
      const d = el("details", "method");
      d.appendChild(el("summary", null, "How this was tested"));
      method.forEach((n) => d.appendChild(el("p", null, n.text)));
      card.appendChild(d);
    }
    if (a.evidence) card.appendChild(el("div", "tag", a.evidence));
    if (a.follow_ups && a.follow_ups.length) {
      const row = el("div", "followups");
      a.follow_ups.forEach((q) => {
        const b = el("button", "fu", q);
        b.type = "button";
        b.addEventListener("click", () => ask(q));
        row.appendChild(b);
      });
      card.appendChild(row);
    }
    return card;
  }

  const scrollTo = (n) => n.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });

  async function ask(question) {
    question = (question || "").trim();
    if (!question || pending) return;
    const log = $("log"), input = $("q"), send = $("send");
    pending = true; send.disabled = true;
    asked.push(question);
    const start = $("start");
    if (start && start.parentNode) start.remove();
    const mine = el("div", "msg me", question);
    log.appendChild(mine);
    input.value = "";
    const wait = el("article", "msg bot pending");
    const dots = el("span", "dots");
    dots.append(el("i"), el("i"), el("i"));
    wait.append(dots, el("span", null, "Checking the data"));
    log.appendChild(wait);
    scrollTo(mine);
    let card;
    try {
      const data = await postJSON("/api/ask", { question, ...scope() });
      card = renderAnswer(data, false);
    } catch (e) {
      card = renderAnswer({ text: e.message, notes: [{ level: "warn", text: "If the server stopped, restart it with python chatbot/server.py." }] }, true);
    }
    wait.replaceWith(card);
    scrollTo(mine);
    pending = false; send.disabled = false; input.focus({ preventScroll: true });
  }

  function clearChat(keepHint) {
    const log = $("log");
    const start = el("section", "start");
    start.id = "start";
    start.append(el("h2", null, "Ask anything about the posts"),
      el("p", null, "Answers are calculated from the data, not guessed. Each one shows how many posts it rests on, so you can judge how far to trust it."));
    const box = el("div", "starters");
    box.id = "starters";
    start.appendChild(box);
    log.replaceChildren(start);
    asked.length = 0;
    fillStarters();
    if (keepHint) $("q").focus({ preventScroll: true });
  }

  function fillStarters() {
    const box = $("starters");
    if (!box || !state.info) return;
    box.replaceChildren();
    state.info.suggestions.forEach((q) => {
      const b = el("button", "starter", q);
      b.type = "button";
      b.addEventListener("click", () => ask(q));
      box.appendChild(b);
    });
  }

  function updateScope() {
    $("scope").textContent = "Answering from: " + (state.source === "demo" ? "demo data" : "my data") + " · " +
      (state.platform ? platName(state.platform) : "all platforms") + " · " + (state.brand || "all brands") + ". Change this with the filters above.";
  }

  // ---------- filters, source, banner
  function fillFilters() {
    const info = state.info;
    const ps = $("f-platform"), bs = $("f-brand");
    ps.replaceChildren(new Option("All platforms", ""));
    info.platform_status.forEach((p) => {
      const o = new Option(p.name + (p.rows ? " (" + p.rows.toLocaleString() + ")" : " (no data yet)"), p.key);
      o.disabled = !p.rows;
      ps.appendChild(o);
    });
    if (!info.platform_status.some((p) => p.key === state.platform && p.rows)) state.platform = "";
    ps.value = state.platform;
    bs.replaceChildren(new Option("All brands", ""));
    info.brand_list.forEach((b) => bs.appendChild(new Option(b, b)));
    if (!info.brand_list.includes(state.brand)) state.brand = "";
    bs.value = state.brand;
  }

  function setFilter(kind, value) {
    state[kind] = value;
    $("f-" + kind).value = value;
    refresh();
  }

  function refresh() {
    updateScope();
    if (state.view === "overview") loadOverview();
  }

  function updateBanner() {
    const b = $("banner");
    if (state.source !== "demo") { b.hidden = true; return; }
    b.replaceChildren();
    b.appendChild(el("strong", null, "Demo data. "));
    b.appendChild(document.createTextNode("The brands are fictional and the numbers are made up, only to show how the screens look. Do not use them for conclusions."));
    const back = el("button", "ghost", "Switch to my data");
    back.type = "button";
    back.addEventListener("click", () => setSource("real"));
    b.appendChild(back);
    b.hidden = false;
  }

  async function setSource(src) {
    if (src === state.source && state.info) return;
    state.source = src;
    state.platform = state.brand = "";
    document.querySelectorAll("[data-source]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.source === src ? "true" : "false"));
    updateBanner();
    await loadInfo();
    clearChat(false);
    lockAdd();
    refresh();
  }

  async function loadInfo() {
    state.info = await getJSON("/api/info", { source: state.source });
    fillFilters();
    fillStarters();
    updateScope();
  }

  // ---------- add data
  const form = () => $("add-form");
  function lockAdd() {
    const locked = state.source !== "real";
    $("add-lock").hidden = !locked;
    document.querySelectorAll("#view-add input, #view-add select, #view-add textarea, #view-add button").forEach((x) => { x.disabled = locked; });
  }

  function setAddPlatform(key) {
    const f = form();
    if (f.elements.platform) { f.elements.platform.value = key; applyHints(); }
  }

  function applyHints() {
    const f = form();
    const h = PLAT[f.elements.platform.value] || PLAT.instagram;
    $("l-likes").textContent = h.likes ? "(" + h.likes + ")" : "";
    $("l-views").textContent = h.views ? "(" + h.views + ")" : "";
    f.elements.shares.placeholder = h.shares || "12";
    $("h-url").textContent = h.url;
  }

  function initAdd() {
    const f = form(), m = state.meta;
    const fill = (sel, items, label) => { sel.replaceChildren(); items.forEach((i) => sel.appendChild(new Option(label(i), i.key !== undefined ? i.key : i))); };
    fill(f.elements.platform, m.manual_platforms, (p) => p.name);
    fill(f.elements.brand, m.brands, (b) => b);
    fill(f.elements.media_type, m.media_types, (t) => t.label);
    f.elements.collected_on.value = m.today;
    f.elements.collected_on.max = m.today;
    f.elements.post_date.max = m.today;
    f.elements.platform.addEventListener("change", applyHints);
    applyHints();
    f.addEventListener("submit", (e) => { e.preventDefault(); saveOne(); });
    $("import-btn").addEventListener("click", importFile);
    lockAdd();
  }

  function showResult(box, kind, title, problems, actions) {
    box.replaceChildren();
    const r = el("div", "result " + kind);
    r.appendChild(el("strong", null, title));
    if (problems && problems.length) {
      const ul = el("ul");
      problems.forEach((p) => ul.appendChild(el("li", null, plain(p))));
      r.appendChild(ul);
    }
    if (actions) r.appendChild(actions);
    box.appendChild(r);
    r.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "nearest" });
  }

  async function saveOne() {
    const f = form(), box = $("add-result"), btn = $("add-save");
    const fields = Object.fromEntries(new FormData(f).entries());
    const need = [["collector", "your name"], ["post_url", "the post link"], ["post_date", "the post date"]].filter(([k]) => !String(fields[k] || "").trim());
    if (need.length) return showResult(box, "bad", "Fill in " + need.map((n) => n[1]).join(", ") + " first.");
    btn.disabled = true;
    try {
      const out = await postJSON("/api/add_post", { source: state.source, fields });
      if (!out.ok) return showResult(box, "bad", "This post was not saved.", out.problems);
      const actions = el("div", "actions");
      const again = el("button", "ghost", "Add another post");
      again.type = "button";
      again.addEventListener("click", () => {
        ["post_url", "post_date", "post_time_pkt", "caption", "likes", "comments", "shares", "views"].forEach((k) => { f.elements[k].value = ""; });
        box.replaceChildren();
        f.elements.post_url.focus();
      });
      const see = el("button", "primary", "See the overview");
      see.type = "button";
      see.addEventListener("click", () => go_("overview"));
      actions.append(again, see);
      showResult(box, "ok", "Saved. You now have " + out.total_manual.toLocaleString() + " hand-entered " + (out.total_manual === 1 ? "post." : "posts."), null, actions);
      loadInfo().catch(() => {});
    } catch (e) {
      showResult(box, "bad", "Could not save.", [e.message]);
    } finally { btn.disabled = state.source !== "real"; }
  }

  async function importFile() {
    const box = $("import-result"), file = $("file").files[0], btn = $("import-btn");
    if (!file) return showResult(box, "bad", "Choose a CSV file first.");
    if (file.size > 1_400_000) return showResult(box, "bad", "That file is too large. Split it into smaller files.");
    btn.disabled = true;
    try {
      const text = await file.text();
      const out = await postJSON("/api/import", { source: state.source, filename: file.name, text });
      if (!out.ok) return showResult(box, "bad", "The file was not imported.", out.problems);
      const title = out.saved + (out.saved === 1 ? " post saved." : " posts saved.") + (out.rejected ? " " + out.rejected + " rows were not saved." : "");
      showResult(box, out.rejected && !out.saved ? "bad" : "ok", title, out.warning ? [out.warning] : null);
      if (out.rejects && out.rejects.length) {
        const wrap = el("div", "block scroll");
        const t = el("table");
        const head = el("tr");
        ["Row", "Post link", "Why it was not saved"].forEach((h) => head.appendChild(el("th", null, h)));
        t.appendChild(el("thead")).appendChild(head);
        const body = el("tbody");
        out.rejects.forEach((r) => {
          const tr = el("tr");
          const link = (r.post_url || "").replace(/^https?:\/\/(www\.)?/, "");
          tr.append(el("td", "r", String(r.row)), el("td", null, link.length > 28 ? link.slice(0, 27) + "…" : link), el("td", null, plain(r.problems)));
          body.appendChild(tr);
        });
        t.appendChild(body);
        wrap.appendChild(t);
        box.firstChild.appendChild(wrap);
        box.firstChild.appendChild(el("p", "hint", "Fix those rows in your file and import it again. Posts already saved are not saved twice."));
      }
      if (out.saved) loadInfo().catch(() => {});
    } catch (e) {
      showResult(box, "bad", "Could not import.", [e.message]);
    } finally { btn.disabled = state.source !== "real"; }
  }

  // ---------- views
  const currentView = () => { const h = location.hash.replace("#", ""); return VIEWS.includes(h) ? h : "overview"; };

  function showView(v, focus) {
    state.view = v;
    VIEWS.forEach((n) => { $("view-" + n).hidden = n !== v; });
    document.querySelectorAll(".tabs a").forEach((a) => {
      if (a.dataset.view === v) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    if (v === "overview") loadOverview();
    if (v === "ask") updateScope();
    if (focus) {
      window.scrollTo(0, 0);
      const h = $("h-" + v);
      if (h) h.focus({ preventScroll: true });
    }
  }

  function go_(v) {
    if (location.hash !== "#" + v) location.hash = v;
    if (state.view !== v) showView(v, true);
  }

  // ---------- start
  async function init() {
    document.querySelectorAll("[data-source]").forEach((b) => b.addEventListener("click", () => setSource(b.dataset.source)));
    $("f-platform").addEventListener("change", (e) => setFilter("platform", e.target.value));
    $("f-brand").addEventListener("change", (e) => setFilter("brand", e.target.value));
    $("composer").addEventListener("submit", (e) => { e.preventDefault(); ask($("q").value); });
    $("q").addEventListener("keydown", (e) => {
      if (e.key === "ArrowUp" && !e.target.value && asked.length) { e.preventDefault(); e.target.value = asked[asked.length - 1]; }
    });
    $("clear").addEventListener("click", () => { if (!pending) clearChat(true); });
    window.addEventListener("hashchange", () => { if (currentView() !== state.view) showView(currentView(), true); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden && state.info) loadInfo().catch(() => {}); });
    try {
      [state.meta] = await Promise.all([getJSON("/api/meta"), loadInfo()]);
      initAdd();
      clearChat(false);
    } catch (e) {
      $("cards").replaceChildren(Object.assign(el("div", "empty"), {}));
      $("cards").firstChild.append(el("h3", null, "Could not reach the data server"), el("p", null, "Start it with python chatbot/server.py and reload this page."));
      return;
    }
    showView(currentView(), false);
  }
  init();
})();
