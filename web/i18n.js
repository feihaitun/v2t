// 界面语言：默认中文；点右上角的按钮切换成英文。
// 后台和页面里的文字都以中文为准，英文对照表在 web/i18n/en.json（中文原文 → 英文）。
// 带 {0}、{1} 的条目是模板，数字、文件名等会原样带过去。
(function () {
  "use strict";
  const CJK = /[一-鿿]/;
  const SKIP = "script,style,textarea,[data-no-i18n]";
  const ATTRS = ["placeholder", "title", "aria-label"];
  const SUFFIX_START = /^\s*(?:，|；|（|→|·)/;
  const MAX_LEN = 600;
  // 行首的小图标和序号（a. b. → ▶ ✔ ✘ ✅ ⚠️ 等）不翻译，原样保留
  const MARKER = /^(?:(?:[a-z]\.|\d+\.|[→▶✔✘⏭■·✅❌🎉]|⚠️|ℹ️)\s*)+/u;
  const norm = (s) => s.replace(/\s+/g, " ").trim();
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  const startQuery = location.search; // app.js 之后会清掉网址里的参数，所以先记下来
  let lang = "zh";
  let ready = false;
  let exact = new Map(), full = [], suffix = [], prefix = [];

  function compile(zh, en, tail) {
    const parts = zh.split(/\{(\d+)\}/);
    const groups = {};
    let src = tail ? "^([\\s\\S]+?)\\s*" : "^";
    let g = tail ? 1 : 0, literal = 0;
    for (let i = 0; i < parts.length; i++) {
      if (i % 2 === 0) { src += esc(parts[i]); literal += parts[i].length; }
      else { src += "([\\s\\S]+?)"; groups[parts[i]] = ++g; }
    }
    return { re: new RegExp(src + "$"), en, groups, literal };
  }

  function build(catalog) {
    exact = new Map(); full = []; suffix = []; prefix = [];
    for (const [zh, en] of Object.entries(catalog)) {
      const zhN = norm(zh);
      const hasPlaceholder = /\{\d+\}/.test(zh);
      if (!hasPlaceholder) exact.set(zhN, norm(en));
      else full.push(compile(zhN, norm(en), false));
      if (SUFFIX_START.test(zh)) suffix.push(compile(zhN, en, true));
      if (!hasPlaceholder && /[：:]\s*$/.test(zh)) prefix.push({ key: zhN, en: en.replace(/\s+$/, "") });
    }
    const byLiteral = (a, b) => b.literal - a.literal;
    full.sort(byLiteral); suffix.sort(byLiteral);
    prefix.sort((a, b) => b.key.length - a.key.length);
    ready = true;
  }

  function fill(item, m, depth) {
    return item.en.replace(/\{(\d+)\}/g, (_, n) => {
      const idx = item.groups[n];
      return idx ? trSub(m[idx], depth + 1) : "";
    });
  }

  function trSub(s, depth) {
    if (!CJK.test(s)) return s;
    const lead = s.match(/^\s*/)[0];
    let trail = s.match(/\s*$/)[0];
    const out = trCore(norm(s), depth);
    // 「标题：」后面紧跟着别的元素时，英文冒号后要补一个空格
    if (!trail && /:$/.test(out) && /：$/.test(s)) trail = " ";
    return lead + out + trail;
  }

  function trCore(core, depth) {
    if (depth > 5 || core.length > MAX_LEN) return core;
    const hit = exact.get(core);
    if (hit !== undefined) return hit;
    for (const p of full) {
      const m = p.re.exec(core);
      if (m) return fill(p, m, depth);
    }
    const mk = MARKER.exec(core);
    if (mk && mk[0].length < core.length) {
      const rest = trSub(core.slice(mk[0].length), depth + 1);
      return mk[0] + rest;
    }
    for (const p of suffix) {
      const m = p.re.exec(core);
      if (m) return trSub(m[1], depth + 1) + fill(p, m, depth);
    }
    for (const p of prefix) {
      if (core.startsWith(p.key)) {
        const rest = core.slice(p.key.length).trim();
        if (rest) return p.en + " " + trSub(rest, depth + 1);
      }
    }
    const colon = core.indexOf("：");
    if (colon > 0 && colon < core.length - 1) {
      const out = trSub(core.slice(0, colon), depth + 1) + ": " + trSub(core.slice(colon + 1), depth + 1);
      if (out !== core) return out;
    }
    for (const sep of [" → ", " · "]) {
      if (core.includes(sep)) {
        const parts = core.split(sep), out = parts.map((x) => trSub(x, depth + 1));
        if (out.join(sep) !== core) return out.join(sep);
      }
    }
    return core;
  }

  function tr(s) {
    if (lang !== "en" || !ready || typeof s !== "string" || !CJK.test(s)) return s;
    return trSub(s, 0);
  }

  // ---- 页面上的文字 ----
  const textRec = new WeakMap();
  function skipped(node, attr) {
    const el = node.nodeType === 1 ? node : node.parentElement;
    return !el || !!el.closest(attr ? "script,style,[data-no-i18n]" : SKIP);
  }
  function xText(node) {
    if (skipped(node)) return;
    const cur = node.data, rec = textRec.get(node);
    if (rec && rec.en === cur) return;
    if (!CJK.test(cur)) { if (rec) textRec.delete(node); return; }
    const en = tr(cur);
    if (en !== cur) { textRec.set(node, { zh: cur, en }); node.data = en; }
    else if (rec) textRec.delete(node);
  }
  function xAttr(el, name) {
    if (skipped(el, true)) return;
    const store = el.__i18n || (el.__i18n = {});
    const cur = el.getAttribute(name), rec = store[name];
    if (cur === null || (rec && rec.en === cur)) return;
    if (!CJK.test(cur)) { delete store[name]; return; }
    const en = tr(cur);
    if (en !== cur) { store[name] = { zh: cur, en }; el.setAttribute(name, en); }
    else delete store[name];
  }
  function xTree(root) {
    if (root.nodeType === 3) return xText(root);
    if (root.nodeType !== 1 || skipped(root, true)) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let n = root; n; n = walker.nextNode()) {
      if (n.nodeType === 3) xText(n);
      else for (const a of ATTRS) if (n.hasAttribute(a)) xAttr(n, a);
    }
  }
  function restoreAll() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let n = walker.currentNode; n; n = walker.nextNode()) {
      if (n.nodeType === 3) {
        const rec = textRec.get(n);
        if (rec && n.data === rec.en) n.data = rec.zh;
        textRec.delete(n);
      } else if (n.__i18n) {
        for (const [name, rec] of Object.entries(n.__i18n)) {
          if (n.getAttribute(name) === rec.en) n.setAttribute(name, rec.zh);
        }
        n.__i18n = {};
      }
    }
  }

  let zhTitle = document.title;
  const observer = new MutationObserver((records) => {
    if (lang !== "en" || !ready) return;
    for (const r of records) {
      if (r.type === "characterData") xText(r.target);
      else if (r.type === "attributes") xAttr(r.target, r.attributeName);
      else r.addedNodes.forEach(xTree);
    }
  });

  async function setLang(next, persist) {
    if (next === "en" && !ready) {
      try {
        const res = await fetch("/static/i18n/en.json", { cache: "no-store" });
        build(await res.json());
      } catch (err) { console.error("v2t i18n: 英文对照表加载失败", err); next = "zh"; }
    }
    lang = next;
    if (persist) { try { localStorage.setItem("v2t.lang", lang); } catch (_) {} }
    document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
    const btn = document.getElementById("langBtn");
    if (btn) {
      btn.textContent = lang === "en" ? "中文" : "English";
      btn.setAttribute("aria-label", lang === "en" ? "切换到中文 / Switch to Chinese" : "Switch to English / 切换到英文");
    }
    observer.disconnect();
    if (lang === "en") {
      xTree(document.body);
      document.title = tr(zhTitle);
      observer.observe(document.body, { subtree: true, childList: true, characterData: true,
                                        attributes: true, attributeFilter: ATTRS });
    } else {
      restoreAll();
      document.title = zhTitle;
    }
    document.dispatchEvent(new CustomEvent("v2t-lang", { detail: lang }));
  }

  function init() {
    let want = "zh";
    try {
      const q = new URLSearchParams(startQuery).get("lang");
      want = q === "en" || q === "zh" ? q : (localStorage.getItem("v2t.lang") || "zh");
    } catch (_) {}
    const btn = document.getElementById("langBtn");
    if (btn) btn.addEventListener("click", () => setLang(lang === "en" ? "zh" : "en", true));
    if (want === "en") setLang("en", false);
  }

  window.v2tI18n = { tr, get lang() { return lang; }, setLang };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
