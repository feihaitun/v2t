(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // 安全地创建元素：所有文字都走 textContent，不会被当成 HTML 执行
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "text") el.textContent = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  const STEP_ORDER = ["resolve", "subtitles", "media", "audio", "transcribe", "output"];
  const STEP_TITLES = {
    resolve: "认链接", subtitles: "找现成字幕", media: "拿视频/音频",
    audio: "抽音频", transcribe: "语音转文字", output: "整理输出",
  };
  const STATUS_TEXT = { queued: "排队中", running: "进行中", done: "完成", failed: "失败" };
  const STEP_ICON = { done: "✓", failed: "✕", skipped: "–", running: "…" };

  let lastHighlighted = -1;
  const state = {
    dev: false, jobId: null, es: null, lastSeq: 0, events: [], job: null,
    refreshTimer: null, resultFor: null, planTimer: null, historyTimer: null,
    segments: [], liveCount: 0,
  };

  // ---------------------------------------------------------------- 工具
  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (_) { /* ignore */ }
      throw new Error(msg);
    }
    return r.json();
  }
  function fmtTime(sec) {
    if (sec == null || isNaN(sec)) return "?";
    sec = Math.round(sec);
    const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
    const p = (n) => String(n).padStart(2, "0");
    return hh ? `${hh}:${p(mm)}:${p(ss)}` : `${p(mm)}:${p(ss)}`;
  }
  function shortTs(iso) { return iso ? iso.slice(11, 19) : ""; }
  function dateTs(iso) { return iso ? iso.slice(5, 16).replace("T", " ") : ""; }
  function store(key, val) { try { localStorage.setItem(key, val); } catch (_) { /* 无痕模式 */ } }
  function load(key) { try { return localStorage.getItem(key); } catch (_) { return null; } }

  // ---------------------------------------------------------------- 开发模式
  state.dev = load("v2t.dev") === "1";
  $("devToggle").checked = state.dev;
  document.body.classList.toggle("dev", state.dev);
  $("devToggle").addEventListener("change", () => {
    state.dev = $("devToggle").checked;
    store("v2t.dev", state.dev ? "1" : "0");
    document.body.classList.toggle("dev", state.dev);
    if (!state.dev && !$("tabRaw").hidden) document.querySelector('.tab[data-tab="plain"]').click();
    rebuildLog();
    if (state.job) renderJob();
  });

  // ---------------------------------------------------------------- 环境自检
  async function loadDoctor(refresh) {
    const pill = $("doctorPill");
    pill.className = "pill pill-wait";
    pill.textContent = refresh ? "环境自检：重新检查中…" : "环境自检：检查中…";
    try {
      const d = await api("/api/doctor" + (refresh ? "?refresh=1" : ""));
      const bad = d.checks.filter((c) => c.status !== "ok").length;
      pill.className = "pill pill-" + d.status;
      pill.textContent = d.status === "ok" ? "环境自检：全部正常" :
        d.status === "warn" ? `环境自检：能用，${bad} 项要注意` : `环境自检：${bad} 项有问题`;
      const list = $("doctorList");
      list.replaceChildren(...d.checks.map((c) => h("li", {},
        h("div", { class: "d-label" }, (c.status === "ok" ? "✅ " : c.status === "warn" ? "⚠️ " : "❌ ") + c.label),
        h("div", { class: "d-detail" }, c.detail),
        c.status !== "ok" && c.fix ? h("div", { class: "d-fix" }, "怎么修：" + c.fix) : null,
      )), h("li", { class: "muted small" }, "检查时间：" + d.checked_at));
    } catch (e) {
      pill.className = "pill pill-fail";
      pill.textContent = "环境自检失败：" + e.message;
    }
  }
  $("doctorPill").addEventListener("click", () => { $("doctorModal").hidden = false; });
  $("doctorClose").addEventListener("click", () => { $("doctorModal").hidden = true; });
  $("doctorRefresh").addEventListener("click", () => loadDoctor(true));
  $("doctorModal").addEventListener("click", (e) => { if (e.target === $("doctorModal")) $("doctorModal").hidden = true; });

  // 登录状态留在 Chrome，网页只保存这个功能的开关偏好。
  $("optDouyinChrome").checked = load("v2t.douyinChrome") === "1";
  $("optKeep").checked = load("v2t.keepVideo") === "1";
  $("optOfficial").checked = load("v2t.officialOnly") === "1";
  let priorOfficialOptions = null;
  function syncOfficialOption() {
    const on = $("optOfficial").checked;
    if (on) {
      if (!priorOfficialOptions) priorOfficialOptions = { subs: $("optSubs").checked, keep: $("optKeep").checked };
      $("optSubs").checked = true;
      $("optKeep").checked = true;
    } else if (priorOfficialOptions) {
      $("optSubs").checked = priorOfficialOptions.subs;
      $("optKeep").checked = priorOfficialOptions.keep;
      priorOfficialOptions = null;
    }
    $("optSubs").disabled = on;
    $("optKeep").disabled = on;
    $("optThird").disabled = on;
    $("startBtn").textContent = on ? "下载平台原字幕和视频" :
      $("optKeep").checked ? "开始下载视频和字幕" : "开始转文字";
  }
  $("optOfficial").addEventListener("change", () => {
    store("v2t.officialOnly", $("optOfficial").checked ? "1" : "0");
    syncOfficialOption(); renderChromeOption(); schedulePlan();
  });
  syncOfficialOption();
  let chromeConnected = false;
  async function renderChromeOption() {
    const on = $("optDouyinChrome").checked;
    $("chromeSettings").hidden = !on;
    $("chromeHint").textContent = on
      ? ($("optOfficial").checked ? "已开启：从正常登录的 Chrome 查抖音原字幕；没有原字幕也不会转录。" :
        "已开启：直接从正常登录的 Chrome 取视频和字幕；没有原字幕就自动在本机转写。")
      : "已关闭：按原来的访客流程处理，不访问已登录的 Chrome。";
    if (!on) return;
    try {
      chromeConnected = (await api("/api/chrome/status")).connected;
      $("chromeStatus").textContent = chromeConnected ? "✓ Chrome 已连接，可以开始" : "尚未连接：请在 Chrome 打开此网页，并完成下面的一次性设置。";
    } catch (_) { chromeConnected = false; $("chromeStatus").textContent = "无法检查连接，请确认后台服务仍在运行。"; }
  }
  $("optDouyinChrome").addEventListener("change", () => {
    store("v2t.douyinChrome", $("optDouyinChrome").checked ? "1" : "0");
    renderChromeOption(); schedulePlan();
  });
  $("optKeep").addEventListener("change", () => {
    store("v2t.keepVideo", $("optKeep").checked ? "1" : "0");
    syncOfficialOption();
    schedulePlan();
  });
  syncOfficialOption();
  $("openChromeBtn").addEventListener("click", () => api("/api/chrome/open", {method:"POST"}).catch(e => { $("chromeStatus").textContent=e.message; }));
  $("chromeFolderBtn").addEventListener("click", () => api("/api/chrome/folder", {method:"POST"}).catch(e => { $("chromeStatus").textContent=e.message; }));
  setInterval(() => { if ($("optDouyinChrome").checked) renderChromeOption(); }, 3000);
  renderChromeOption();

  // ---------------------------------------------------------------- 预计步骤
  function options() {
    return {
      language: $("optLang").value,
      prefer_subtitles: $("optSubs").checked,
      douyin_chrome: $("optDouyinChrome").checked,
      douyin_cookie_subtitles: false,
      douyin_cookie_profile: "",
      keep_video: $("optKeep").checked,
      official_only: $("optOfficial").checked,
      allow_thirdparty: $("optThird").checked,
      initial_prompt: $("optPrompt").value,
      headed_browser: state.dev && $("optHeaded").checked,
    };
  }

  function schedulePlan() {
    clearTimeout(state.planTimer);
    state.planTimer = setTimeout(updatePlan, 400);
  }

  async function updatePlan() {
    const v = $("input").value.trim();
    if (!v) {
      $("planCard").hidden = true;
      $("startBtn").disabled = true;
      $("inputMsg").textContent = "";
      return;
    }
    try {
      const o = options();
      const plan = await api("/api/plan?" + new URLSearchParams({
        input: v, prefer_subtitles: o.prefer_subtitles, allow_thirdparty: o.allow_thirdparty,
        douyin_chrome: o.douyin_chrome, keep_video: o.keep_video,
        official_only: o.official_only,
      }));
      renderPlan(plan);
      $("startBtn").disabled = false;
      $("inputMsg").textContent = plan.input_type === "url" ? "认出链接：" + plan.value : "本地文件：" + plan.value;
    } catch (e) {
      $("planCard").hidden = true;
      $("startBtn").disabled = true;
      $("inputMsg").textContent = "⚠️ " + e.message;
    }
  }

  function needsBadge(s) {
    return h("span", { class: "needs needs-" + s.needs }, s.needs_label);
  }

  function renderPlan(plan) {
    $("planPlatform").textContent = plan.platform.label;
    $("planWarn").hidden = !plan.warning;
    $("planWarn").textContent = plan.warning ? "⚠️ " + plan.warning : "";
    $("planSteps").replaceChildren(...plan.steps.map((st) => h("li", {},
      h("div", { class: "p-title" }, st.title),
      h("div", { class: "p-explain" }, st.explain),
      st.note ? h("div", { class: "p-note" }, "→ " + st.note) : null,
      st.strategies.length ? h("ol", { class: "strats" }, st.strategies.map((s, i) =>
        h("li", { class: s.enabled ? "" : "disabled" },
          h("span", { class: "s-label" }, `${String.fromCharCode(97 + i)}. ${s.label}`), needsBadge(s),
          s.enabled ? null : h("span", { class: "muted small" }, "  " + s.disabled_reason),
          h("span", { class: "s-explain" }, s.explain)))) : null,
    )));
    $("planCard").hidden = false;
  }

  ["input", "optSubs", "optThird"].forEach((id) => $(id).addEventListener(id === "input" ? "input" : "change", schedulePlan));

  // ---------------------------------------------------------------- 拖文件进来
  const ta = $("input");
  ta.addEventListener("dragover", (e) => { e.preventDefault(); ta.classList.add("drag"); });
  ta.addEventListener("dragleave", () => ta.classList.remove("drag"));
  ta.addEventListener("drop", async (e) => {
    e.preventDefault();
    ta.classList.remove("drag");
    const file = e.dataTransfer.files && e.dataTransfer.files[0];
    if (!file) return;
    $("inputMsg").textContent = `正在把 ${file.name}（${(file.size / 1e6).toFixed(1)}MB）存到本机任务目录…`;
    try {
      const r = await api("/api/upload?name=" + encodeURIComponent(file.name), { method: "PUT", body: file });
      ta.value = r.path;
      updatePlan();
    } catch (err) {
      $("inputMsg").textContent = "⚠️ 上传失败：" + err.message;
    }
  });

  // ---------------------------------------------------------------- 开始
  $("startBtn").addEventListener("click", async () => {
    $("startBtn").disabled = true;
    try {
      if ($("optDouyinChrome").checked && /douyin\.com|iesdouyin\.com/.test($("input").value)) {
        await renderChromeOption();
        if (!chromeConnected) throw new Error("请先连接 Chrome：展开上方「首次连接」完成设置。");
      }
      const job = await api("/api/jobs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ input: $("input").value, options: options() }),
      });
      $("planCard").hidden = true;
      openJob(job.id);
      loadHistory();
    } catch (e) {
      $("inputMsg").textContent = "⚠️ " + e.message;
    } finally {
      $("startBtn").disabled = false;
    }
  });

  // ---------------------------------------------------------------- 任务详情
  function openJob(id) {
    if (state.es) state.es.close();
    Object.assign(state, { jobId: id, lastSeq: 0, events: [], job: null, resultFor: null, segments: [], liveCount: 0 });
    lastHighlighted = -1;
    $("connWarn").hidden = true;
    $("log").replaceChildren();
    $("liveText").replaceChildren();
    $("liveBar").style.width = "0";
    $("resultBox").hidden = true;
    $("liveBox").hidden = true;
    $("jobCard").hidden = false;
    if (location.hash !== "#job=" + id) history.replaceState(null, "", "#job=" + id);
    refreshJob();
    connectStream();
    markHistory();
    $("jobCard").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function connectStream() {
    const id = state.jobId;
    const es = new EventSource(`/api/jobs/${id}/stream?after=${state.lastSeq}`);
    state.es = es;
    es.onmessage = (m) => {
      if (state.jobId !== id) return;
      handleEvent(JSON.parse(m.data));
    };
    es.addEventListener("end", () => {
      es.close();
      refreshJob();
      loadHistory();
    });
    let lostTimer = null;
    es.onopen = () => {
      clearTimeout(lostTimer);
      lostTimer = null;
      if (!$("connWarn").hidden) { $("connWarn").hidden = true; refreshJob(); loadHistory(); }
    };
    es.onerror = () => {
      // 断线时浏览器会自动重连（重复的事件靠 seq 去重）；断太久就告诉你
      if (state.jobId !== id) return;
      if (!lostTimer) lostTimer = setTimeout(() => {
        $("connWarn").textContent = "⚠️ 和后台的连接断了，正在自动重连… 如果一直这样，请看看运行 ./v2t.sh serve 的那个终端窗口是不是被关了，重新运行它即可（中断的任务会显示失败，可以点「重试」）。";
        $("connWarn").hidden = false;
      }, 2500);
    };
  }

  function handleEvent(ev) {
    if (ev.seq <= state.lastSeq) return;
    state.lastSeq = ev.seq;
    state.events.push(ev);
    if (ev.type === "segment") return appendLive(ev);
    if (ev.type === "progress") {
      $("liveBox").hidden = false;
      $("liveBar").style.width = (ev.detail && ev.detail.percent || 0) + "%";
      $("liveProgress").textContent = ev.msg;
    }
    appendLog(ev);
    if (/^(step_|attempt_|job_)/.test(ev.type)) scheduleRefresh();
  }

  function visible(ev) {
    if (ev.type === "segment") return false;
    return state.dev || ev.level !== "debug";
  }

  function appendLog(ev, bulk) {
    if (!visible(ev)) return;
    const log = $("log");
    const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
    const line = h("div", { class: `log-line t-${ev.type} lv-${ev.level}` },
      h("span", { class: "ts" }, shortTs(ev.ts)),
      state.dev && (ev.step || ev.strategy) ? h("span", { class: "tag" }, `[${[ev.step, ev.strategy].filter(Boolean).join("/")}]`) : null,
      ev.msg);
    if (state.dev && ev.detail && !["output", "cmd", "cmd_end"].includes(ev.type)) {
      line.append(h("details", {}, h("summary", {}, "细节"), h("pre", {}, JSON.stringify(ev.detail, null, 2))));
    }
    log.append(line);
    if (!bulk && nearBottom) log.scrollTop = log.scrollHeight;
  }

  function rebuildLog() {
    $("log").replaceChildren();
    state.events.forEach((ev) => appendLog(ev, true));
    $("log").scrollTop = $("log").scrollHeight;
  }

  function appendLive(ev) {
    state.liveCount += 1;
    $("liveBox").hidden = false;
    const box = $("liveText");
    box.append(h("div", {}, `[${fmtTime(ev.detail && ev.detail.start)}] ${ev.msg}`));
    while (box.childElementCount > 200) box.firstChild.remove();
    box.scrollTop = box.scrollHeight;
  }

  function scheduleRefresh() {
    if (state.refreshTimer) return;
    state.refreshTimer = setTimeout(() => { state.refreshTimer = null; refreshJob(); }, 350);
  }

  async function refreshJob() {
    if (!state.jobId) return;
    const id = state.jobId;
    try {
      const job = await api("/api/jobs/" + id);
      if (state.jobId !== id) return;
      state.job = job;
      renderJob();
    } catch (e) {
      $("jobTitle").textContent = "任务读取失败：" + e.message;
    }
  }

  function renderJob() {
    const job = state.job;
    $("jobTitle").textContent = job.title || job.input;
    const meta = [job.platform_label, job.duration ? "时长 " + fmtTime(job.duration) : null,
      "创建于 " + dateTs(job.created_at), job.elapsed ? `用时 ${job.elapsed} 秒` : null,
      state.dev ? "任务编号 " + job.id : null].filter(Boolean);
    $("jobMeta").replaceChildren(...[h("div", {}, meta.join(" · ")),
      job.url && job.url !== job.title ? h("div", {}, job.url) : null].filter(Boolean));
    const badge = $("jobStatus");
    badge.className = "badge " + job.status;
    badge.textContent = STATUS_TEXT[job.status] || job.status;
    $("retryBtn").hidden = !(job.status === "failed" || job.status === "done");

    const how = $("jobHow");
    if (job.result) {
      how.hidden = false;
      how.replaceChildren(h("b", {}, "文字怎么来的："), job.result.how);
    } else {
      how.hidden = true;
    }
    const err = $("jobError");
    err.hidden = !job.error;
    err.replaceChildren();
    if (job.error) {
      err.append(h("div", {}, "没成功：" + job.error),
        h("div", { class: "small" }, "想找人帮忙看的话，把下面这两份记录发给他：",
          h("a", { href: `/api/jobs/${job.id}`, target: "_blank" }, " job.json（每一步的状态）"), " · ",
          h("a", { href: `/api/jobs/${job.id}/files/events.jsonl`, target: "_blank" }, "events.jsonl（后台日志）")));
    }
    $("rawLinks").replaceChildren("原始记录：",
      h("a", { href: `/api/jobs/${job.id}`, target: "_blank" }, "job.json（每一步的状态）"), " · ",
      h("a", { href: `/api/jobs/${job.id}/files/events.jsonl`, target: "_blank" }, "events.jsonl（后台日志）"),
      h("span", { class: "muted" }, `  · 文件夹 data/jobs/${job.id}/`));
    $("tabRaw").replaceChildren(h("pre", {}, JSON.stringify(job, null, 2)));
    renderSteps(job);
    const tr = (job.steps || []).find((s) => s.key === "transcribe");
    $("liveBox").hidden = !(tr && tr.status === "running");
    if (job.status === "done" && state.resultFor !== job.id) renderResult(job);
  }

  function renderSteps(job) {
    const byKey = Object.fromEntries((job.steps || []).map((s) => [s.key, s]));
    const items = STEP_ORDER.map((key, idx) => {
      const s = byKey[key] || { key, title: STEP_TITLES[key], status: job.status === "failed" ? "skipped" : "pending", attempts: [] };
      const notReached = !byKey[key] && job.status === "failed";
      const icon = STEP_ICON[s.status] || String(idx + 1);
      return h("li", { class: "step " + s.status },
        h("span", { class: "step-icon" }, icon),
        h("div", { class: "step-title" }, s.title,
          s.duration != null ? h("span", { class: "step-dur" }, `${s.duration}s`) : null,
          s.status === "running" ? h("span", { class: "step-dur" }, "进行中…") : null),
        s.explain ? h("div", { class: "step-explain" }, s.explain) : null,
        notReached ? h("div", { class: "step-summary muted" }, "没走到这一步") :
          s.summary ? h("div", { class: "step-summary" }, s.summary) : null,
        renderAttemptList(s, job),
      );
    });
    $("jobSteps").replaceChildren(...items);
  }

  // 已经试过的办法 + 还没轮到的办法（灰色，写着「排队中」），让你知道后面还有几种办法可试
  function renderAttemptList(step, job) {
    const tried = step.attempts || [];
    const planned = ((job.plan || {})[step.key] || []);
    const pending = (job.status === "running" && (step.status === "running" || step.status === "pending"))
      ? planned.filter((p) => !tried.some((a) => a.key === p.key)) : [];
    if (!tried.length && !pending.length) return null;
    return h("ul", { class: "attempts" }, tried.map(renderAttempt), pending.map((p) =>
      h("li", { class: "attempt pending" },
        h("div", { class: "attempt-head" }, "○", h("b", {}, p.label),
          h("span", { class: "needs needs-" + p.needs }, p.needs_label),
          h("span", { class: "muted small" }, p.enabled ? "还没轮到（前面的办法失败了才会用）" : p.disabled_reason)))));
  }


  function renderAttempt(a) {
    const icon = { ok: "✅", failed: "❌", skipped: "⏭", running: "⏳", unavailable: "ℹ️" }[a.status] || "•";
    return h("li", { class: "attempt " + a.status },
      h("div", { class: "attempt-head" }, icon, h("b", {}, a.label), h("span", { class: "needs needs-" + a.needs }, a.needs_label),
        a.duration != null ? h("span", { class: "muted small" }, `${a.duration}s`) : null),
      a.message ? h("div", { class: "attempt-msg" }, a.message) :
        a.status === "running" ? h("div", { class: "attempt-msg" }, a.explain) : null,
      state.dev && a.detail ? h("pre", {}, JSON.stringify(a.detail, null, 2)) : null,
    );
  }

  async function getOk(url, kind) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(r.status === 404 ? "文件找不到了" : "服务返回 " + r.status);
    return kind === "json" ? r.json() : r.text();
  }

  async function renderResult(job) {
    const id = job.id;
    state.resultFor = id;
    const files = job.result.files || {};
    const base = `/api/jobs/${id}/files/`;
    const mediaLink = () => {
      if (!files.media) return null;
      const ext = files.media.split(".").pop();
      const isVideo = /^(mp4|mov|webm|mkv|m4v)$/i.test(ext);
      const name = `${(job.title || "media").replace(/[\\/:*?"<>|#\s]+/g, "_").slice(0, 60)}.${ext}`;
      return h("a", { href: base + files.media, download: name }, isVideo ? "下载视频" : "下载音频");
    };
    $("resultTitle").textContent = job.options?.official_only ? "平台原文件" : "文字稿";
    if (!files.json && job.options?.official_only) {
      state.segments = [];
      state.plain = "";
      $("copyBtn").hidden = true;
      document.querySelector("#resultBox .tabs").hidden = true;
      $("tabPlain").hidden = false;
      $("tabSegments").hidden = true;
      $("tabRaw").hidden = true;
      $("resultStats").textContent = "";
      $("downloads").replaceChildren(...[mediaLink()].filter(Boolean));
      $("tabPlain").textContent = job.result.how;
      $("tabSegments").replaceChildren();
      $("playerWrap").replaceChildren();
      $("playerWrap").hidden = !files.media;
      if (files.media) $("playerWrap").append(h("video", { controls: true, preload: "metadata", src: base + files.media }));
      $("resultBox").hidden = false;
      return;
    }
    $("copyBtn").hidden = false;
    document.querySelector("#resultBox .tabs").hidden = false;
    let data;
    let plain = "";
    try {
      data = await getOk(base + files.json, "json");
      if (state.jobId !== id) return;
      plain = await getOk(base + files.txt, "text");
      if (state.jobId !== id) return;
    } catch (e) {
      if (state.jobId !== id) return;
      state.segments = [];
      state.plain = "";
      $("resultStats").textContent = "";
      $("downloads").replaceChildren();
      $("tabSegments").replaceChildren();
      $("playerWrap").hidden = true;
      $("resultBox").hidden = false;
      $("tabPlain").textContent = "文字稿文件读不到了（" + e.message + "，可能被移动或删除了）。可以点「打开文件夹」看看，或点「重试」重新生成。";
      return;
    }
    state.segments = data.segments || [];
    state.plain = plain;
    $("resultStats").textContent = `（${job.result.language_label}，${job.result.segments} 段，${job.result.chars} 字）`;
    const links = ["md", "srt", "txt", "json"].filter((k) => files[k]).map((k) =>
      h("a", { href: base + files[k], download: files[k] }, "下载 " + k));
    if (files.platform_subtitle) links.unshift(h("a", { href: base + files.platform_subtitle,
      download: files.platform_subtitle }, "下载平台原字幕"));
    if (files.media) links.push(mediaLink());
    $("downloads").replaceChildren(...links);

    const wrap = $("playerWrap");
    wrap.replaceChildren();
    wrap.hidden = true;
    if (files.media) {
      const isVideo = /\.(mp4|mov|webm|mkv|m4v)$/i.test(files.media);
      const player = h(isVideo ? "video" : "audio", { controls: true, preload: "metadata", src: base + files.media, id: "player" });
      player.addEventListener("timeupdate", () => highlightSeg(player.currentTime));
      wrap.append(player);
      wrap.hidden = false;
    }

    $("tabPlain").textContent = plain;
    $("tabSegments").replaceChildren(...state.segments.map((s, i) => h("div", { class: "seg", "data-i": i },
      h("button", { onclick: () => seek(s.start), title: "跳到这里播放" }, fmtTime(s.start)),
      h("span", {}, s.text))));
    $("resultBox").hidden = false;
  }

  function seek(t) {
    const p = $("player");
    if (!p) return;
    p.currentTime = t;
    p.play().catch(() => {});
  }

  function highlightSeg(t) {
    const segs = state.segments;
    let idx = -1;
    for (let i = 0; i < segs.length; i++) { if (segs[i].start <= t) idx = i; else break; }
    if (idx === lastHighlighted) return;
    const panel = $("tabSegments");
    const prev = panel.querySelector(".seg.playing");
    if (prev) prev.classList.remove("playing");
    const cur = panel.querySelector(`.seg[data-i="${idx}"]`);
    if (cur) cur.classList.add("playing");
    lastHighlighted = idx;
  }

  document.querySelectorAll(".tab").forEach((btn) => btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    $("tabPlain").hidden = btn.dataset.tab !== "plain";
    $("tabSegments").hidden = btn.dataset.tab !== "segments";
    $("tabRaw").hidden = btn.dataset.tab !== "raw";
  }));

  $("copyBtn").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(state.plain || "");
      $("copyBtn").textContent = "已复制 ✓";
    } catch (_) {
      $("copyBtn").textContent = "复制失败（浏览器不允许）";
    }
    setTimeout(() => { $("copyBtn").textContent = "复制全文"; }, 1800);
  });

  $("revealBtn").addEventListener("click", () => {
    if (state.jobId) api(`/api/jobs/${state.jobId}/reveal`, { method: "POST" }).catch(() => {});
  });
  $("retryBtn").addEventListener("click", async () => {
    if (!state.jobId) return;
    const job = await api(`/api/jobs/${state.jobId}/retry`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      // 重试服从当前 Chrome 开关，不重新开启旧 Cookie 模块。
      body: JSON.stringify({ options: {
        ...($("optThird").checked ? { allow_thirdparty: true } : {}),
        douyin_chrome: $("optDouyinChrome").checked,
      douyin_cookie_subtitles: false,
        douyin_cookie_profile: "",
        prefer_subtitles: $("optSubs").checked,
        keep_video: $("optKeep").checked,
        official_only: $("optOfficial").checked,
      } }),
    });
    openJob(job.id);
    loadHistory();
  });

  // ---------------------------------------------------------------- 历史
  async function loadHistory() {
    let list = [];
    try { list = await api("/api/jobs"); } catch (_) { return; }
    const icon = { done: "✅", failed: "❌", running: "⏳", queued: "🕓" };
    $("history").replaceChildren(...(list.length ? list.map((j) => h("li", {
      "data-id": j.id, class: j.id === state.jobId ? "active" : "", onclick: () => openJob(j.id),
    },
      h("div", { class: "h-title" }, `${icon[j.status] || ""} ${j.title || j.input}`),
      h("div", { class: "h-meta" }, [j.platform_label, dateTs(j.created_at), j.status === "failed" ? "失败" : j.method]
        .filter(Boolean).join(" · ")),
    )) : [h("li", { class: "muted small" }, "还没有任务。贴一个链接试试。")]));
    clearTimeout(state.historyTimer);
    const busy = list.some((j) => j.status === "running" || j.status === "queued");
    state.historyTimer = setTimeout(loadHistory, busy ? 4000 : 15000);
  }
  function markHistory() {
    document.querySelectorAll("#history li[data-id]").forEach((li) =>
      li.classList.toggle("active", li.dataset.id === state.jobId));
  }
  $("refreshHistory").addEventListener("click", loadHistory);

  // ---------------------------------------------------------------- 启动
  loadDoctor(false);
  loadHistory();
  const m = location.hash.match(/^#job=([\w-]+)/);
  if (m) openJob(m[1]);
  window.addEventListener("hashchange", () => {
    const mm = location.hash.match(/^#job=([\w-]+)/);
    if (mm && mm[1] !== state.jobId) { openJob(mm[1]); loadHistory(); }
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadHistory(); });
})();
