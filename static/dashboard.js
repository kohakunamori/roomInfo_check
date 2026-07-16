(() => {
  const $ = (id) => document.getElementById(id);
  let hours = 168;
  let chart;
  let lastAuthSnapshot = null;
  let lastNovnc = null;

  let pageSize = 10;
  let samplesAll = [];
  let eventsAll = [];
  let panelTab = "events"; // 'events' | 'samples'
  let panelPage = 1;
  let filterType = "";
  let filterStatus = "";
  let filterDate = ""; // yyyy-mm-dd, local
  let brushRange = null; // {from, to} index into current history data
  let historyData = [];

  // Direct noVNC (compose VNC_BIND default 3033) — works without reverse proxy
  // when the browser is on the same machine as Docker.
  const VNC_PORT = Number(window.__ROOMINFO_VNC_PORT__) || 3033;
  const NOVNC_QS = "autoconnect=1&resize=scale&reconnect=true";

  function pageIsLocal() {
    return /^(127\.0\.0\.1|localhost|\[::1\])$/i.test(location.hostname);
  }

  /**
   * Default noVNC URL:
   * - local browser → host:VNC_PORT/vnc.html (direct, no reverse proxy)
   * - remote browser → same-origin /vnc/… (expects reverse proxy path strip)
   */
  function defaultNovncUrl() {
    if (pageIsLocal()) {
      return `http://${location.hostname}:${VNC_PORT}/vnc.html?${NOVNC_QS}`;
    }
    // reverse-proxy path; websockify lives under /vnc/ after nginx strip
    return `/vnc/vnc.html?${NOVNC_QS}&path=vnc/websockify`;
  }

  /**
   * Never send a remote browser to NAS/server loopback.
   * Empty / stale loopback / placeholder URLs fall back to defaultNovncUrl().
   */
  function sanitizeNovncUrl(url) {
    const fallback = defaultNovncUrl();
    if (!url || typeof url !== "string") return fallback;
    const u = url.trim();
    if (!u || u === "#" || u === "about:blank") return fallback;
    const urlIsLoopback =
      /^(https?:)?\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/|$)/i.test(u) ||
      u.includes("127.0.0.1:6080") ||
      u.includes("localhost:6080");
    if (urlIsLoopback && !pageIsLocal()) return fallback;
    return u;
  }

  // Compatibility alias — always re-resolve so local/remote stays correct
  function DEFAULT_NOVNC() {
    return defaultNovncUrl();
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    if (res.status === 401) {
      window.location.href = "/login";
      throw new Error("unauthorized");
    }
    const data = await res.json();
    if (!data.success && res.ok === false) {
      throw new Error(data.message || "request failed");
    }
    return data;
  }

  function fmtNum(v, unit = "") {
    if (v === null || v === undefined || v === "") return "—";
    const n = Number(v);
    if (Number.isNaN(n)) return `${v}${unit}`;
    return `${n.toFixed(2)}${unit}`;
  }

  /** Numeric cell with muted unit suffix (HTML). */
  function fmtNumCell(v, unit) {
    if (v === null || v === undefined || v === "") {
      return "—";
    }
    const n = Number(v);
    if (Number.isNaN(n)) {
      return escapeHtml(String(v));
    }
    return `${escapeHtml(n.toFixed(2))}<span class="num-unit">${escapeHtml(unit)}</span>`;
  }

  /** KPI value with muted unit suffix (HTML). */
  function fmtKpi(v, unit) {
    if (v === null || v === undefined || v === "") return "—";
    const n = Number(v);
    if (Number.isNaN(n)) return escapeHtml(String(v));
    return `${escapeHtml(n.toFixed(2))}<span class="kpi-unit">${escapeHtml(unit)}</span>`;
  }

  function fmtTime(ts) {
    if (!ts) return "—";
    try {
      const d = new Date(ts);
      if (Number.isNaN(d.getTime())) return String(ts);
      return d.toLocaleString();
    } catch {
      return String(ts);
    }
  }

  function fmtTimeShort(ts) {
    if (!ts) return "—";
    try {
      const d = new Date(ts);
      if (Number.isNaN(d.getTime())) return String(ts);
      const pad = (n) => String(n).padStart(2, "0");
      const now = new Date();
      const sameDay =
        d.getFullYear() === now.getFullYear() &&
        d.getMonth() === now.getMonth() &&
        d.getDate() === now.getDate();
      const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
      return sameDay ? `今天 ${hm}` : `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
    } catch {
      return String(ts);
    }
  }

  /** Compact two-line time for table cells (HTML). */
  function fmtTimeCell(ts) {
    if (!ts) return escapeHtml("—");
    try {
      const d = new Date(ts);
      if (Number.isNaN(d.getTime())) return escapeHtml(String(ts));
      const now = new Date();
      const sameYear = d.getFullYear() === now.getFullYear();
      const pad = (n) => String(n).padStart(2, "0");
      const main = sameYear
        ? `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
        : `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
      const rel = relativeTime(d, now);
      return `<div class="time-stack" title="${escapeHtml(d.toLocaleString())}"><span class="time-main">${escapeHtml(main)}</span><span class="time-sub">${escapeHtml(rel)}</span></div>`;
    } catch {
      return escapeHtml(String(ts));
    }
  }

  function relativeTime(d, now = new Date()) {
    const sec = Math.round((d.getTime() - now.getTime()) / 1000);
    const rtf = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });
    const abs = Math.abs(sec);
    if (abs < 60) return rtf.format(Math.round(sec), "second");
    if (abs < 3600) return rtf.format(Math.round(sec / 60), "minute");
    if (abs < 86400) return rtf.format(Math.round(sec / 3600), "hour");
    if (abs < 86400 * 30) return rtf.format(Math.round(sec / 86400), "day");
    return rtf.format(Math.round(sec / (86400 * 30)), "month");
  }

  /** Friendly type labels for the events table. */
  function typeLabel(type) {
    const t = String(type || "");
    const map = {
      auth_success: "认证成功",
      auth_running: "认证中",
      auth_starting: "启动中",
      auth_waiting_mfa: "等待 MFA",
      auth_waiting_host: "待主机",
      auth_failed: "认证失败",
      auth_idle: "空闲",
      auth_start: "启动认证",
      auth_stop: "结束认证",
      novnc: "noVNC",
      session_invalid: "会话失效",
      recharge: "充值",
      alert: "低余额",
      email_sent: "邮件已发",
      email_failed: "邮件失败",
      email_test: "测试邮件",
      query_start: "开始查询",
      query_done: "查询完成",
      query_failed: "查询失败",
      check_ok: "余额正常",
      settings_updated: "设置已存",
      settings_failed: "设置失败",
      startup_failed: "启动失败",
      success: "成功",
      failed: "失败",
      error: "错误",
    };
    if (map[t]) return map[t];
    if (t.startsWith("auth_")) return t.replace(/^auth_/, "认证·");
    return t || "—";
  }

  function typeBadgeClass(type) {
    const t = String(type || "").toLowerCase();
    if (t === "novnc") return "is-novnc";
    if (t === "auth" || t.startsWith("auth_")) {
      if (t.includes("fail") || t.includes("error")) return "is-bad";
      if (t.includes("success")) return "is-ok";
      return "is-auth";
    }
    if (t === "session_invalid" || t === "failed" || t === "error" || t === "query_failed" || t === "startup_failed")
      return "is-bad";
    if (t === "email_sent" || t === "email_test") return "is-email";
    if (t === "email_failed") return "is-bad";
    if (t === "alert") return "is-alert";
    if (t === "recharge" || t === "check_ok" || t === "query_done") return "is-ok";
    if (t.startsWith("settings")) return "is-settings";
    if (t.startsWith("query")) return "is-query";
    if (t === "success" || t === "ok") return "is-ok";
    return "";
  }

  /** Broad category used by the type filter dropdown. */
  function typeCategory(type) {
    const t = String(type || "").toLowerCase();
    if (t === "novnc" || t === "auth" || t.startsWith("auth_") || t === "session_invalid") return "auth";
    if (t.startsWith("email")) return "email";
    if (t === "alert" || t === "recharge" || t === "check_ok") return "alert";
    if (t.startsWith("settings")) return "settings";
    if (t.startsWith("query") || t === "startup_failed") return "query";
    return "";
  }

  /** ok / bad verdict for the status column + filter. */
  function eventStatus(type) {
    const t = String(type || "").toLowerCase();
    if (
      t.includes("fail") ||
      t.includes("error") ||
      t === "session_invalid"
    )
      return "bad";
    return "ok";
  }

  function setSessionBadge(valid, exists) {
    const el = $("session-state");
    if (!el) return;
    if (valid) {
      el.innerHTML = '<span class="badge-ok">会话有效</span>';
    } else if (exists) {
      el.innerHTML = '<span class="badge-bad">会话过期</span>';
    } else {
      el.innerHTML = '<span class="badge-bad">无会话</span>';
    }
  }

  function novncHref(auth) {
    return sanitizeNovncUrl(
      auth?.novnc_url ||
        lastNovnc ||
        $("vnc-open-tab")?.getAttribute("href") ||
        DEFAULT_NOVNC()
    );
  }

  function applyNovncLinks(url) {
    const href = url || DEFAULT_NOVNC();
    lastNovnc = href;
    const a = $("vnc-open-tab");
    if (a) a.href = href;
  }

  /* ── Views: dashboard / auth takeover ─────────────── */
  const VIEW_TITLES = { dashboard: "仪表盘", auth: "认证接管" };
  let activeView = "dashboard";

  function switchView(view) {
    activeView = view;
    for (const v of ["dashboard", "auth"]) {
      const el = $(`view-${v}`);
      if (el) el.hidden = v !== view;
    }
    document.querySelectorAll(".nav-item[data-view]").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.view === view);
    });
    const title = $("topbar-title");
    if (title) title.textContent = VIEW_TITLES[view] || view;
    const crumb = $("crumb-current");
    if (crumb) crumb.textContent = VIEW_TITLES[view] || view;
    // Canvas sizes are 0 while hidden — repaint once the view is visible again.
    if (view === "dashboard") {
      window.requestAnimationFrame(() => {
        renderBrush();
        chart?.resize();
      });
    }
    closeSidebarMobile();
  }

  document.querySelectorAll(".nav-item[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });

  /* ── Sidebar (mobile) ─────────────────────────────── */
  function closeSidebarMobile() {
    $("sidebar")?.classList.remove("is-open");
  }

  $("btn-sidebar-toggle")?.addEventListener("click", () => {
    $("sidebar")?.classList.toggle("is-open");
  });

  /* ── Right panel: tabs / filters / pager ──────────── */
  document.querySelectorAll(".nav-item[data-panel]").forEach((btn) => {
    btn.addEventListener("click", () => {
      setPanelTab(btn.dataset.panel);
      openPanel();
      closeSidebarMobile();
    });
  });

  function openPanel() {
    const panel = $("right-panel");
    if (!panel) return;
    panel.classList.remove("is-collapsed");
    panel.classList.add("is-open");
  }

  function closePanel() {
    const panel = $("right-panel");
    if (!panel) return;
    panel.classList.remove("is-open");
    // desktop: collapse column entirely
    if (window.matchMedia("(min-width: 1181px)").matches) {
      panel.classList.add("is-collapsed");
    }
  }

  $("btn-panel-close")?.addEventListener("click", closePanel);

  function setPanelTab(tab) {
    panelTab = tab;
    panelPage = 1;
    document.querySelectorAll(".panel-tab").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.tab === tab);
    });
    const evPanel = $("panel-events");
    const smPanel = $("panel-samples");
    const filters = $("events-filters");
    if (evPanel) evPanel.hidden = tab !== "events";
    if (smPanel) smPanel.hidden = tab !== "samples";
    if (filters) filters.style.display = tab === "events" ? "" : "none";
    renderPanel();
  }

  document.querySelectorAll(".panel-tab").forEach((btn) => {
    btn.addEventListener("click", () => setPanelTab(btn.dataset.tab));
  });

  $("filter-type")?.addEventListener("change", (e) => {
    filterType = e.target.value;
    panelPage = 1;
    renderPanel();
  });
  $("filter-status")?.addEventListener("change", (e) => {
    filterStatus = e.target.value;
    panelPage = 1;
    renderPanel();
  });
  $("filter-date")?.addEventListener("change", (e) => {
    filterDate = e.target.value || "";
    panelPage = 1;
    renderPanel();
  });
  $("panel-page-size")?.addEventListener("change", (e) => {
    pageSize = Number(e.target.value) || 10;
    panelPage = 1;
    renderPanel();
  });
  $("btn-panel-refresh")?.addEventListener("click", () => {
    refreshEvents().catch(() => {});
    refreshHistory().catch(() => {});
  });

  function pageCount(total) {
    return Math.max(1, Math.ceil((total || 0) / pageSize));
  }

  function clampPage(page, total) {
    const max = pageCount(total);
    const p = Number(page) || 1;
    return Math.min(Math.max(1, p), max);
  }

  function renderPager(el, page, total, onChange) {
    if (!el) return;
    const totalPages = pageCount(total);
    const current = clampPage(page, total);

    el.innerHTML = "";

    const mkBtn = (text, { disabled = false, current: isCur = false, page: target } = {}) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn-quiet btn-sm pager-btn" + (isCur ? " pager-num is-current" : target != null ? " pager-num" : "");
      b.textContent = text;
      b.disabled = disabled;
      if (!disabled && target != null) b.addEventListener("click", () => onChange(target));
      return b;
    };

    el.appendChild(mkBtn("‹", { disabled: current <= 1, page: current - 1 }));

    // Windowed page numbers: 1 … c-1 c c+1 … N
    const nums = new Set([1, totalPages, current - 1, current, current + 1]);
    const ordered = [...nums].filter((n) => n >= 1 && n <= totalPages).sort((a, b) => a - b);
    let prev = 0;
    for (const n of ordered) {
      if (n - prev > 1) {
        const gap = document.createElement("span");
        gap.className = "pager-gap";
        gap.textContent = "…";
        el.appendChild(gap);
      }
      el.appendChild(mkBtn(String(n), { current: n === current, page: n }));
      prev = n;
    }

    el.appendChild(mkBtn("›", { disabled: current >= totalPages, page: current + 1 }));
  }

  function slicePage(items, page) {
    const p = clampPage(page, items.length);
    const start = (p - 1) * pageSize;
    return items.slice(start, start + pageSize);
  }

  function emptyRow(tbody, colspan, title, detail) {
    const tr = document.createElement("tr");
    tr.className = "row-empty";
    tr.innerHTML = `<td colspan="${colspan}"><div class="empty-hint"><strong>${escapeHtml(title)}</strong>${
      detail ? `<span>${escapeHtml(detail)}</span>` : ""
    }</div></td>`;
    tbody.appendChild(tr);
  }

  /** Local yyyy-mm-dd of a timestamp for the date filter. */
  function localDateKey(ts) {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  function filteredEvents() {
    return eventsAll.filter((e) => {
      if (filterType && typeCategory(e.type) !== filterType) return false;
      if (filterStatus && eventStatus(e.type) !== filterStatus) return false;
      if (filterDate && localDateKey(e.ts) !== filterDate) return false;
      return true;
    });
  }

  function renderPanel() {
    if (panelTab === "events") renderEventsTable();
    else renderSamplesTable();
  }

  function renderEventsTable() {
    const body = $("events-body");
    if (!body) return;
    body.innerHTML = "";
    const rows = filteredEvents();

    if (!rows.length) {
      emptyRow(body, 4, "暂无历史事件", "查询 · 邮件 · 认证 · 设置事件会出现在此");
    } else {
      panelPage = clampPage(panelPage, rows.length);
      slicePage(rows, panelPage).forEach((e) => {
        const tr = document.createElement("tr");
        if (e.live) tr.classList.add("row-live");
        const st = eventStatus(e.type);
        tr.innerHTML = `
          <td class="time">${fmtTimeCell(e.ts)}</td>
          <td><span class="type-badge ${typeBadgeClass(e.type)}">${escapeHtml(typeLabel(e.type))}</span></td>
          <td class="note">${e.noteHtml ?? escapeHtml(e.note || "—")}</td>
          <td><span class="status-badge is-${st}">${st === "ok" ? "成功" : "失败"}</span></td>
        `;
        body.appendChild(tr);
      });
    }
    updatePanelFoot(rows.length);
  }

  function renderSamplesTable() {
    const body = $("samples-body");
    if (!body) return;
    body.innerHTML = "";
    if (!samplesAll.length) {
      emptyRow(body, 3, "暂无采样记录", "点「立即查询」或等待定时任务");
    } else {
      panelPage = clampPage(panelPage, samplesAll.length);
      slicePage(samplesAll, panelPage).forEach((r) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td class="time">${fmtTimeCell(r.ts)}</td>
          <td class="num">${fmtNumCell(r.amount, "元")}</td>
          <td class="num">${fmtNumCell(r.electricity, "度")}</td>
        `;
        body.appendChild(tr);
      });
    }
    updatePanelFoot(samplesAll.length);
  }

  function updatePanelFoot(total) {
    const totalEl = $("panel-total");
    if (totalEl) totalEl.textContent = `共 ${total} 条`;
    renderPager($("panel-pager"), panelPage, total, (p) => {
      panelPage = p;
      renderPanel();
    });
    const jump = $("panel-jump");
    if (jump && document.activeElement !== jump) {
      jump.value = String(clampPage(panelPage, total));
      jump.max = String(pageCount(total));
    }
  }

  $("panel-jump")?.addEventListener("change", (e) => {
    const total = panelTab === "events" ? filteredEvents().length : samplesAll.length;
    panelPage = clampPage(Number(e.target.value), total);
    renderPanel();
  });

  /* ── Auth takeover view ───────────────────────────── */
  // Map auth container state → progress step + statuses.
  const FLOW_ORDER = ["start", "portal", "mfa", "export", "exit"];

  function flowIndexFor(state) {
    switch (state) {
      case "starting":
        return 0;
      case "waiting_host":
        return 0;
      case "running":
        return 1;
      case "waiting_mfa":
        return 2;
      case "success":
        return 5; // all done
      case "failed":
        return -2; // special: mark current as failed
      default:
        return -1; // idle / unknown
    }
  }

  function renderFlow(auth) {
    const state = auth?.state || "idle";
    const idx = flowIndexFor(state);
    const list = $("flow-steps");
    if (!list) return;
    // Best-effort per-step timestamps from the auth status file.
    const stepTimes = [
      auth?.started_at || null, // 启动容器
      auth?.updated_at && idx >= 1 ? auth.updated_at : null, // 打开门户
      state === "waiting_mfa" ? auth?.updated_at || null : null, // 等待 MFA
      state === "success" ? auth?.finished_at || auth?.updated_at || null : null, // 导出会话
      state === "success" ? auth?.finished_at || null : null, // 自动退出
    ];
    list.querySelectorAll(".flow-step").forEach((li, i) => {
      li.classList.remove("is-done", "is-active", "is-failed");
      const pill = li.querySelector(".flow-pill");
      const time = li.querySelector("time");
      if (pill) pill.hidden = true;
      if (time) time.textContent = stepTimes[i] ? fmtTimeShort(stepTimes[i]) : "";
      if (idx === -1) return;
      if (idx === -2) {
        // failed: mark first steps done-ish, current failed
        if (i < 2) li.classList.add("is-done");
        else if (i === 2) {
          li.classList.add("is-failed");
          if (pill) {
            pill.hidden = false;
            pill.textContent = "失败";
          }
        }
        return;
      }
      if (i < idx) li.classList.add("is-done");
      else if (i === idx) {
        li.classList.add("is-active");
        if (pill) {
          pill.hidden = false;
          pill.textContent = "进行中";
        }
      }
    });

    const pill = $("auth-flow-pill");
    if (pill) {
      const labels = {
        idle: "认证未启动",
        starting: "容器启动中",
        waiting_host: "等待主机",
        running: "认证进行中",
        waiting_mfa: "等待 MFA",
        success: "认证成功",
        failed: "认证失败",
      };
      pill.textContent = labels[state] || `状态：${state}`;
      pill.className = "pill " + (
        state === "success" ? "pill-ok" :
        state === "failed" ? "pill-danger" :
        state === "idle" ? "pill-info" : "pill-warn"
      );
    }

    const vncState = $("vnc-state");
    if (vncState) {
      const running = ["starting", "running", "waiting_mfa", "waiting_host"].includes(state);
      vncState.textContent = running ? "运行中" : state === "success" ? "已完成" : state === "failed" ? "失败" : "未启动";
      vncState.className = "badge-mini " + (running ? "badge-ok" : state === "failed" ? "badge-bad" : "");
    }

    const dot = $("nav-auth-dot");
    if (dot) {
      const busy = ["starting", "running", "waiting_mfa", "waiting_host"].includes(state);
      dot.classList.toggle("hidden", !busy);
    }
  }

  function showVncEmbed(url) {
    const frame = $("vnc-frame");
    const placeholder = $("vnc-placeholder");
    const href = sanitizeNovncUrl(url || DEFAULT_NOVNC());
    applyNovncLinks(href);
    switchView("auth");
    if (placeholder) placeholder.classList.add("hidden");
    const hint = $("vnc-hint");
    if (hint) {
      hint.textContent =
        "正在按需启动 auth 容器并连接 noVNC…（通常数秒内就绪）";
      hint.classList.remove("error");
    }
    const urlEl = $("vnc-url");
    if (urlEl) urlEl.textContent = href;
    if (frame) frame.src = "about:blank";
    // Poll until upstream is ready — container is on-demand, not always-on.
    waitForNovnc(href, { frame, hint, attempts: 20, intervalMs: 1500 });
  }

  async function waitForNovnc(href, { frame, hint, attempts = 20, intervalMs = 1500 } = {}) {
    let loaded = false;
    for (let i = 0; i < attempts; i++) {
      try {
        const res = await fetch(href, {
          method: "GET",
          credentials: "omit",
          cache: "no-store",
        });
        if (res.ok) {
          if (hint) {
            hint.classList.remove("error");
            hint.textContent =
              "完成登录 / MFA 后会自动关闭 auth 容器。也可点「断开」立即停止。";
          }
          if (frame && !loaded) {
            frame.src = href;
            loaded = true;
          }
          return true;
        }
        if (hint) {
          hint.classList.toggle("error", i > 4);
          hint.textContent =
            `noVNC 尚未就绪（HTTP ${res.status}）· 重试 ${i + 1}/${attempts}…` +
            (i > 4 ? " 若持续失败请确认 roominfo-auth-ctl 在运行。" : "");
        }
      } catch (_) {
        if (hint) {
          hint.classList.toggle("error", i > 4);
          hint.textContent = `等待 noVNC 启动… ${i + 1}/${attempts}`;
        }
      }
      await new Promise((r) => setTimeout(r, intervalMs));
    }
    if (hint) {
      hint.classList.add("error");
      hint.textContent =
        "noVNC 长时间未就绪。请确认 roominfo-auth-ctl 在运行，或在 NAS 执行：docker compose --profile auth up -d roominfo-auth";
    }
    // Last attempt: still point iframe so operator can hard-refresh later.
    if (frame && !loaded) frame.src = href;
    return false;
  }

  function hideVncEmbed() {
    const frame = $("vnc-frame");
    const placeholder = $("vnc-placeholder");
    if (frame) frame.src = "about:blank";
    if (placeholder) placeholder.classList.remove("hidden");
  }

  $("btn-vnc-fullscreen")?.addEventListener("click", () => {
    document.querySelector(".vnc-frame-wrap")?.classList.toggle("is-fullscreen");
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      document.querySelector(".vnc-frame-wrap")?.classList.remove("is-fullscreen");
      hideSettingsModal();
    }
  });

  /* ── Session banner + topbar pills ────────────────── */
  let bannerDismissed = false;

  function setSessionBanner(valid, exists, last) {
    const banner = $("session-banner");
    const pill = $("pill-session");
    const broken = !valid;
    if (pill) pill.classList.toggle("hidden", !broken);
    if (!banner) return;
    banner.classList.toggle("hidden", !broken || bannerDismissed);
    if (!broken) {
      bannerDismissed = false;
      return;
    }
    const text = $("session-banner-text");
    const why = last?.message || (exists ? "Cookie 会话无效" : "缺少 session 文件");
    if (text) {
      text.textContent =
        `当前校园门户会话已过期（${why}），请点击「刷新登录」重新认证。`;
    }
  }

  $("btn-banner-close")?.addEventListener("click", () => {
    bannerDismissed = true;
    $("session-banner")?.classList.add("hidden");
  });

  function setAlertPill(latest, threshold) {
    const pill = $("pill-alert");
    if (!pill) return;
    const amount = Number(latest?.amount);
    const th = Number(threshold);
    const low = Number.isFinite(amount) && Number.isFinite(th) && amount <= th;
    pill.classList.toggle("hidden", !low);
  }

  /** Sidebar 事件日志 badge: recent failures/alerts (last 24h). */
  function updateNavBadge() {
    const badge = $("nav-alert-badge");
    if (!badge) return;
    const dayAgo = Date.now() - 86400000;
    const count = eventsAll.filter((e) => {
      if (e.live) return false;
      const t = new Date(e.ts).getTime();
      if (!Number.isFinite(t) || t < dayAgo) return false;
      const ty = String(e.type || "").toLowerCase();
      return eventStatus(ty) === "bad" || ty === "alert";
    }).length;
    badge.classList.toggle("hidden", count === 0);
    badge.textContent = count > 99 ? "99+" : String(count);
  }

  /* ── User chip dropdown ───────────────────────────── */
  $("user-chip-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    const menu = $("user-menu");
    if (!menu) return;
    menu.hidden = !menu.hidden;
    $("user-chip-btn")?.setAttribute("aria-expanded", menu.hidden ? "false" : "true");
  });
  document.addEventListener("click", (e) => {
    const menu = $("user-menu");
    if (menu && !menu.hidden && !$("user-chip")?.contains(e.target)) {
      menu.hidden = true;
      $("user-chip-btn")?.setAttribute("aria-expanded", "false");
    }
  });

  function setOnlineState(ok) {
    const dot = $("online-dot");
    const text = $("online-text");
    if (dot) dot.classList.toggle("is-off", !ok);
    if (text) text.textContent = ok ? "在线" : "离线";
  }

  /* ── Live auth rows merged into events ────────────── */
  function buildAuthLiveRows(auth, novnc) {
    const rows = [];
    if (auth && (auth.state || auth.message || auth.updated_at)) {
      const parts = [];
      if (auth.message) parts.push(String(auth.message));
      if (auth.error) parts.push(`error=${auth.error}`);
      if (auth.room_name) parts.push(`room=${auth.room_name}`);
      if (auth.session_valid != null) parts.push(`valid=${auth.session_valid}`);
      rows.push({
        ts: auth.updated_at || auth.finished_at || auth.started_at || null,
        type: `auth_${auth.state || "idle"}`,
        noteHtml: escapeHtml(parts.join(" · ") || "—"),
        live: true,
      });
    }
    if (novnc) {
      rows.push({
        ts: auth?.updated_at || null,
        type: "novnc",
        noteHtml: `<a href="${escapeHtml(novnc)}" target="_blank" rel="noopener">${escapeHtml(novnc)}</a>`,
        live: true,
      });
    }
    return rows;
  }

  /* ── Status refresh ───────────────────────────────── */
  async function refreshStatus() {
    const { data } = await api("/api/status");
    const latest = data.latest || {};
    if ($("room-name")) $("room-name").textContent = latest.room_name || "—";
    if ($("amount")) $("amount").innerHTML = fmtKpi(latest.amount, "元");
    if ($("electricity")) $("electricity").innerHTML = fmtKpi(latest.electricity, "kWh");
    const sampleTs = fmtTimeShort(latest.ts);
    if ($("amount-ts")) $("amount-ts").textContent = sampleTs;
    if ($("elec-ts")) $("elec-ts").textContent = sampleTs;
    setSessionBadge(data.session_valid, data.session_file_exists);
    const roomState = $("room-state");
    if (roomState) {
      roomState.textContent = data.session_valid ? "在线" : "离线";
      roomState.className = "badge-mini " + (data.session_valid ? "badge-ok" : "badge-bad");
    }

    const last = data.last_run || {};
    if ($("last-check")) $("last-check").textContent = fmtTimeShort(last.at) || "—";

    // Sidebar system status
    if ($("svc-last-sample")) {
      $("svc-last-sample").textContent = fmtTimeShort(data.history_stats?.latest_ts);
    }
    const mail = $("svc-mail");
    if (mail) {
      const conf = data.settings?.smtp_auth_configured && data.settings?.smtp_from;
      mail.textContent = conf ? "正常" : "未配置";
      mail.className = "badge-mini " + (conf ? "badge-ok" : "badge-warn");
    }
    if ($("svc-threshold")) $("svc-threshold").textContent = `≤ ${data.threshold} 元`;
    if ($("svc-interval")) $("svc-interval").textContent = `每 ${data.interval_minutes} 分钟`;

    const auth = data.auth || {};
    lastAuthSnapshot = auth;
    const novnc = sanitizeNovncUrl(
      data.novnc_url || auth.novnc_url || DEFAULT_NOVNC()
    );
    applyNovncLinks(novnc);
    setSessionBanner(data.session_valid, data.session_file_exists, last);
    setAlertPill(latest, data.threshold);
    setOnlineState(data.session_valid);
    renderFlow(auth);
    // Keep live auth/noVNC rows in the events table without a full events fetch.
    mergeEvents(window.__roominfoEvents || [], auth, novnc);
  }

  function mergeEvents(events, auth, novnc) {
    const live = buildAuthLiveRows(auth || lastAuthSnapshot, novnc || lastNovnc);
    const history = (events || []).map((e) => ({
      ts: e.ts,
      type: e.type,
      note: e.note,
      live: false,
    }));
    eventsAll = [...live, ...history];
    updateNavBadge();
    if (panelTab === "events") renderPanel();
  }

  /* ── Chart + derived stats ────────────────────────── */
  /** Build asymmetric axis bounds so dual series don't occupy the same vertical band. */
  function dualAxisRange(values, band = "mid") {
    const nums = (values || [])
      .map((v) => Number(v))
      .filter((n) => Number.isFinite(n));
    if (!nums.length) return {};
    let min = Math.min(...nums);
    let max = Math.max(...nums);
    if (min === max) {
      const d = Math.max(Math.abs(min) * 0.08, 1);
      min -= d;
      max += d;
    }
    const span = max - min;
    // Expand empty space on one side so the line sits lower (bottom) or higher (top).
    if (band === "bottom") {
      return {
        min: min - span * 0.12,
        max: max + span * 0.72,
      };
    }
    if (band === "top") {
      return {
        min: min - span * 0.72,
        max: max + span * 0.12,
      };
    }
    return {
      min: min - span * 0.18,
      max: max + span * 0.18,
    };
  }

  /** Range deltas + daily burn rate for the derive strip below the chart. */
  function renderDerived(data) {
    const nums = (key) =>
      data.map((r) => Number(r[key])).filter((n) => Number.isFinite(n));
    const amounts = nums("amount");
    const elec = nums("electricity");
    const setText = (id, text) => {
      const el = $(id);
      if (el) el.textContent = text;
    };
    if (data.length < 2) {
      setText("d-amount-delta", "—");
      setText("d-elec-delta", "—");
      setText("d-daily-avg", "—");
      setText("d-days-left", "—");
      return;
    }
    const first = data[0];
    const lastRow = data[data.length - 1];
    const spanMs = new Date(lastRow.ts) - new Date(first.ts);
    const spanDays = spanMs / 86400000;

    const dAmount = amounts.length >= 2 ? amounts[amounts.length - 1] - amounts[0] : null;
    const dElec = elec.length >= 2 ? elec[elec.length - 1] - elec[0] : null;
    setText("d-amount-delta", dAmount == null ? "—" : `${dAmount >= 0 ? "+" : ""}${dAmount.toFixed(2)} 元`);
    setText("d-elec-delta", dElec == null ? "—" : `${dElec >= 0 ? "+" : ""}${dElec.toFixed(1)} kWh`);

    let dailyAvg = null;
    if (dElec != null && dElec < 0 && spanDays > 0.2) {
      dailyAvg = -dElec / spanDays;
    }
    setText("d-daily-avg", dailyAvg == null ? "—" : `${dailyAvg.toFixed(2)} kWh`);

    const currentElec = elec.length ? elec[elec.length - 1] : null;
    if (dailyAvg && dailyAvg > 0 && currentElec != null) {
      setText("d-days-left", `${(currentElec / dailyAvg).toFixed(1)} 天`);
    } else {
      setText("d-days-left", "—");
    }
  }

  async function refreshHistory() {
    const q = hours > 0 ? `?hours=${hours}&limit=5000` : `?hours=0&limit=5000`;
    const { data } = await api(`/api/history${q}`);
    historyData = data;
    brushRange = null; // new range/data → reset zoom window
    renderChart();
    renderBrush();
    renderDerived(data);
    samplesAll = [...data].reverse();
    if (panelTab === "samples") renderPanel();
  }

  /** Slice of history currently shown in the main chart (brush window). */
  function windowedData() {
    if (!brushRange) return historyData;
    const { from, to } = brushRange;
    return historyData.slice(from, to + 1);
  }

  function renderChart() {
    const data = windowedData();
    const labels = data.map((r) => fmtTime(r.ts));
    const amounts = data.map((r) => r.amount);
    const elec = data.map((r) => r.electricity);
    const amountScale = dualAxisRange(amounts, "bottom");
    const elecScale = dualAxisRange(elec, "top");

    // Subtitle: 当前余额 ¥xx，剩余电量 xx kWh（今天 21:48）
    const lastRow = data[data.length - 1];
    const sub = $("chart-sub");
    if (sub) {
      sub.textContent = lastRow
        ? `当前余额 ¥${fmtNum(lastRow.amount)}，剩余电量 ${fmtNum(lastRow.electricity)} kWh（${fmtTimeShort(lastRow.ts)}）`
        : "暂无采样数据";
    }

    const ctx = $("chart");
    if (ctx) {
      if (chart) chart.destroy();
      chart = new Chart(ctx, {
        type: "line",
        data: {
          labels,
          datasets: [
            {
              label: "账户余额 (元)",
              data: amounts,
              borderColor: "#2f6fed",
              backgroundColor: (c) => {
                const { ctx: g, chartArea } = c.chart;
                if (!chartArea) return "rgba(47,111,237,0.08)";
                const grad = g.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                grad.addColorStop(0, "rgba(47,111,237,0.18)");
                grad.addColorStop(1, "rgba(47,111,237,0.01)");
                return grad;
              },
              fill: true,
              tension: 0.28,
              yAxisID: "y",
              pointRadius: 0,
              pointHoverRadius: 4,
              borderWidth: 2.4,
              order: 2,
            },
            {
              label: "剩余电量 (kWh)",
              data: elec,
              borderColor: "#0f8a6b",
              backgroundColor: (c) => {
                const { ctx: g, chartArea } = c.chart;
                if (!chartArea) return "rgba(15,138,107,0.07)";
                const grad = g.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                grad.addColorStop(0, "rgba(15,138,107,0.16)");
                grad.addColorStop(1, "rgba(15,138,107,0.01)");
                return grad;
              },
              fill: true,
              tension: 0.28,
              yAxisID: "y1",
              pointRadius: 0,
              pointHoverRadius: 4,
              borderWidth: 2.4,
              borderDash: [6, 4],
              order: 1,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          layout: { padding: { top: 4, right: 4, left: 2, bottom: 0 } },
          scales: {
            y: {
              type: "linear",
              position: "left",
              min: amountScale.min,
              max: amountScale.max,
              title: {
                display: true,
                text: "余额 · 元",
                color: "#2f6fed",
                font: { family: "Outfit", size: 11, weight: "600" },
                padding: { bottom: 4 },
              },
              ticks: {
                color: "#2f6fed",
                maxTicksLimit: 6,
                font: { family: "JetBrains Mono", size: 11 },
                callback: (v) => Number(v).toFixed(1),
              },
              grid: { color: "rgba(47,111,237,0.08)" },
              border: { display: false },
            },
            y1: {
              type: "linear",
              position: "right",
              min: elecScale.min,
              max: elecScale.max,
              title: {
                display: true,
                text: "电量 · kWh",
                color: "#0f8a6b",
                font: { family: "Outfit", size: 11, weight: "600" },
                padding: { bottom: 4 },
              },
              ticks: {
                color: "#0f8a6b",
                maxTicksLimit: 6,
                font: { family: "JetBrains Mono", size: 11 },
                callback: (v) => Number(v).toFixed(1),
              },
              grid: { drawOnChartArea: false },
              border: { display: false },
            },
            x: {
              ticks: {
                color: "#8b92a0",
                maxTicksLimit: 7,
                font: { family: "JetBrains Mono", size: 10 },
              },
              grid: { color: "rgba(26,29,35,0.05)" },
              border: { display: false },
            },
          },
          plugins: {
            legend: {
              labels: {
                color: "#5a616e",
                boxWidth: 12,
                boxHeight: 8,
                usePointStyle: false,
                font: { family: "Outfit", size: 12 },
              },
            },
            tooltip: {
              backgroundColor: "#1a1d23",
              titleFont: { family: "Outfit", size: 12 },
              bodyFont: { family: "JetBrains Mono", size: 11 },
              padding: 10,
              cornerRadius: 8,
              displayColors: true,
              callbacks: {
                label(ctx) {
                  const v = ctx.parsed.y;
                  if (v == null || Number.isNaN(v)) return `${ctx.dataset.label}: —`;
                  const unit = ctx.dataset.yAxisID === "y1" ? " kWh" : " 元";
                  return `${ctx.dataset.label}: ${Number(v).toFixed(2)}${unit}`;
                },
              },
            },
          },
        },
      });
    }
  }

  /* ── Brush strip: drag on mini chart to zoom the main chart ── */
  let brushDrag = null; // {startX, curX} in canvas px

  function renderBrush() {
    const strip = $("brush-strip");
    const canvas = $("brush-canvas");
    if (!strip || !canvas) return;
    const data = historyData;
    strip.hidden = data.length < 3;
    if (strip.hidden) return;

    const dpr = window.devicePixelRatio || 1;
    const w = strip.clientWidth - 12; // padding
    const h = 46;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    const g = canvas.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);

    // amount sparkline
    const nums = data.map((r) => Number(r.amount)).map((n) => (Number.isFinite(n) ? n : null));
    const valid = nums.filter((n) => n != null);
    if (!valid.length) return;
    let min = Math.min(...valid);
    let max = Math.max(...valid);
    if (min === max) { min -= 1; max += 1; }
    const x = (i) => (i / (data.length - 1)) * w;
    const y = (v) => h - 4 - ((v - min) / (max - min)) * (h - 8);

    g.beginPath();
    let started = false;
    nums.forEach((v, i) => {
      if (v == null) return;
      if (!started) { g.moveTo(x(i), y(v)); started = true; }
      else g.lineTo(x(i), y(v));
    });
    g.strokeStyle = "rgba(47,111,237,0.75)";
    g.lineWidth = 1.4;
    g.stroke();
    g.lineTo(x(data.length - 1), h);
    g.lineTo(0, h);
    g.closePath();
    g.fillStyle = "rgba(47,111,237,0.10)";
    g.fill();

    // selection window
    let selFrom = 0;
    let selTo = data.length - 1;
    if (brushDrag) {
      const [a, b] = [brushDrag.startX, brushDrag.curX].sort((p, q) => p - q);
      selFrom = Math.round((a / w) * (data.length - 1));
      selTo = Math.round((b / w) * (data.length - 1));
    } else if (brushRange) {
      selFrom = brushRange.from;
      selTo = brushRange.to;
    }
    if (brushDrag || brushRange) {
      const x1 = x(Math.max(0, selFrom));
      const x2 = x(Math.min(data.length - 1, selTo));
      g.fillStyle = "rgba(15,138,107,0.14)";
      g.fillRect(x1, 0, Math.max(2, x2 - x1), h);
      g.strokeStyle = "rgba(15,138,107,0.8)";
      g.lineWidth = 1;
      g.strokeRect(x1 + 0.5, 0.5, Math.max(2, x2 - x1) - 1, h - 1);
    }
  }

  function brushEventX(e) {
    const canvas = $("brush-canvas");
    const rect = canvas.getBoundingClientRect();
    const cx = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
    return Math.min(Math.max(0, cx), rect.width);
  }

  function brushCommit() {
    const canvas = $("brush-canvas");
    if (!brushDrag || !canvas || historyData.length < 3) {
      brushDrag = null;
      return;
    }
    const w = canvas.getBoundingClientRect().width;
    const [a, b] = [brushDrag.startX, brushDrag.curX].sort((p, q) => p - q);
    brushDrag = null;
    // Tiny drag = click → clear zoom
    if (b - a < 6) {
      brushRange = null;
    } else {
      const n = historyData.length - 1;
      const from = Math.max(0, Math.round((a / w) * n));
      const to = Math.min(n, Math.round((b / w) * n));
      brushRange = to - from >= 2 ? { from, to } : null;
    }
    renderChart();
    renderBrush();
    renderDerived(windowedData());
  }

  (() => {
    const strip = $("brush-strip");
    if (!strip) return;
    strip.addEventListener("mousedown", (e) => {
      if (historyData.length < 3) return;
      const cx = brushEventX(e);
      brushDrag = { startX: cx, curX: cx };
      renderBrush();
      e.preventDefault();
    });
    window.addEventListener("mousemove", (e) => {
      if (!brushDrag) return;
      brushDrag.curX = brushEventX(e);
      renderBrush();
    });
    window.addEventListener("mouseup", () => {
      if (brushDrag) brushCommit();
    });
    window.addEventListener("resize", () => renderBrush());
  })();

  async function refreshEvents() {
    const { data } = await api("/api/events?limit=500");
    window.__roominfoEvents = data || [];
    mergeEvents(window.__roominfoEvents, lastAuthSnapshot, lastNovnc);
  }

  async function refreshAll() {
    await Promise.all([refreshStatus(), refreshHistory(), refreshEvents()]);
  }

  /* ── Actions: query / auth ────────────────────────── */
  async function startAuthFlow(button) {
    if (button) button.disabled = true;
    try {
      const { data } = await api("/api/auth/start", { method: "POST", body: "{}" });
      await refreshStatus();
      showVncEmbed(novncHref(data));
      // Watch for success → auto hide embed (container self-stops shortly after).
      watchAuthTerminal();
    } catch (e) {
      alert(e.message || e);
    } finally {
      if (button) button.disabled = false;
    }
  }

  let authWatchTimer = null;
  function watchAuthTerminal() {
    if (authWatchTimer) clearInterval(authWatchTimer);
    let ticks = 0;
    authWatchTimer = setInterval(async () => {
      ticks += 1;
      try {
        await refreshStatus();
        const st = lastAuthSnapshot?.state;
        if (st === "success") {
          clearInterval(authWatchTimer);
          authWatchTimer = null;
          const hint = $("vnc-hint");
          if (hint) {
            hint.classList.remove("error");
            hint.textContent = "登录成功，auth 容器即将自动关闭以省资源。";
          }
          window.setTimeout(() => hideVncEmbed(), 4000);
        } else if (st === "failed" || st === "idle") {
          // Keep panel open on fail so user can read; stop watching after timeout.
          if (st === "idle" || ticks > 200) {
            clearInterval(authWatchTimer);
            authWatchTimer = null;
          }
        }
      } catch (_) {
        /* ignore transient */
      }
      if (ticks > 200) {
        clearInterval(authWatchTimer);
        authWatchTimer = null;
      }
    }, 3000);
  }

  $("btn-query")?.addEventListener("click", async () => {
    $("btn-query").disabled = true;
    try {
      await api("/api/query", { method: "POST", body: "{}" });
      await refreshAll();
    } catch (e) {
      alert(e.message || e);
    } finally {
      $("btn-query").disabled = false;
    }
  });

  $("btn-auth-start")?.addEventListener("click", () => startAuthFlow($("btn-auth-start")));
  $("btn-auth-start-2")?.addEventListener("click", () => startAuthFlow($("btn-auth-start-2")));

  $("btn-auth-stop")?.addEventListener("click", async () => {
    try {
      await api("/api/auth/stop", { method: "POST", body: "{}" });
      hideVncEmbed();
      await refreshStatus();
    } catch (e) {
      alert(e.message || e);
    }
  });

  /* ── Settings modal ───────────────────────────────── */
  function showSettingsModal() {
    const modal = $("settings-modal");
    if (!modal) return;
    modal.hidden = false;
    loadSettingsForm().catch((e) => console.warn(e));
    closeSidebarMobile();
  }

  function hideSettingsModal() {
    const modal = $("settings-modal");
    if (modal) modal.hidden = true;
  }

  $("nav-settings")?.addEventListener("click", showSettingsModal);
  $("btn-settings-close")?.addEventListener("click", hideSettingsModal);
  $("btn-settings-cancel")?.addEventListener("click", hideSettingsModal);
  $("settings-modal")?.addEventListener("click", (e) => {
    if (e.target === $("settings-modal")) hideSettingsModal();
  });

  function setSettingsMsg(text, kind) {
    const el = $("settings-msg");
    if (!el) return;
    if (!text) {
      el.hidden = true;
      el.textContent = "";
      el.classList.remove("is-ok", "is-err");
      return;
    }
    el.hidden = false;
    el.textContent = text;
    el.classList.toggle("is-ok", kind === "ok");
    el.classList.toggle("is-err", kind === "err");
  }

  async function loadSettingsForm() {
    const { data } = await api("/api/settings");
    const set = (id, v) => {
      const el = $(id);
      if (el) el.value = v ?? "";
    };
    set("set-smtp-from", data.smtp_from || "");
    set("set-smtp-from-name", data.smtp_from_name || "roominfo");
    set("set-smtp-server", data.smtp_server || "");
    set("set-smtp-port", data.smtp_port ?? 465);
    set("set-smtp-auth", ""); // never echo secret
    const ssl = $("set-smtp-ssl");
    if (ssl) ssl.checked = !!data.smtp_use_ssl;
    set("set-recipients", (data.recipients || []).join("\n"));
    set("set-room-id", data.room_id || "");
    set("set-threshold", data.low_balance_threshold ?? 20);
    set("set-interval", data.check_interval_minutes ?? 60);
    const hint = $("set-smtp-auth-hint");
    if (hint) {
      hint.textContent = data.smtp_auth_configured
        ? "授权码已配置，留空保持不变（保存后不会回显）"
        : "授权码为只写入字段，保存后将不会回显";
    }
    setSettingsMsg("", null);
    return data;
  }

  function collectSettingsPayload() {
    const recipientsRaw = $("set-recipients")?.value || "";
    return {
      smtp_from: $("set-smtp-from")?.value?.trim() || "",
      smtp_from_name: $("set-smtp-from-name")?.value?.trim() || "roominfo",
      smtp_server: $("set-smtp-server")?.value?.trim() || "",
      smtp_port: Number($("set-smtp-port")?.value || 465),
      smtp_use_ssl: !!$("set-smtp-ssl")?.checked,
      smtp_auth_code: $("set-smtp-auth")?.value || "",
      recipients: recipientsRaw,
      room_id: $("set-room-id")?.value?.trim() || "",
      low_balance_threshold: Number($("set-threshold")?.value || 20),
      check_interval_minutes: Number($("set-interval")?.value || 60),
    };
  }

  $("settings-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("btn-settings-save");
    if (btn) btn.disabled = true;
    setSettingsMsg("保存中…", null);
    try {
      const payload = collectSettingsPayload();
      const { data } = await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      setSettingsMsg(
        `已保存 · 阈值 ${data.low_balance_threshold} 元 · 收件 ${
          (data.recipients || []).length
        } 人`,
        "ok"
      );
      // clear auth input after successful save so it is not re-submitted
      const auth = $("set-smtp-auth");
      if (auth) auth.value = "";
      const hint = $("set-smtp-auth-hint");
      if (hint) {
        hint.textContent = data.smtp_auth_configured
          ? "授权码已配置，留空保持不变（保存后不会回显）"
          : "授权码为只写入字段，保存后将不会回显";
      }
      await refreshAll();
    } catch (err) {
      setSettingsMsg(err.message || String(err), "err");
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  $("btn-settings-test")?.addEventListener("click", async () => {
    const btn = $("btn-settings-test");
    if (btn) btn.disabled = true;
    setSettingsMsg("发送测试邮件…", null);
    try {
      // Save first so test uses latest form values (auth code included if typed)
      const payload = collectSettingsPayload();
      await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      const auth = $("set-smtp-auth");
      if (auth) auth.value = "";
      const { data, success } = await api("/api/settings/test-email", {
        method: "POST",
        body: "{}",
      });
      if (success || data?.ok) {
        setSettingsMsg(
          `测试邮件已发送 · ${
            (data.results || []).map((r) => r.email).join(", ") || "ok"
          }`,
          "ok"
        );
      } else {
        setSettingsMsg(data?.message || "发送失败", "err");
      }
      await refreshEvents();
    } catch (err) {
      setSettingsMsg(err.message || String(err), "err");
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  /* ── Time range chips ─────────────────────────────── */
  document.querySelectorAll(".chip[data-hours]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      document.querySelectorAll(".chip[data-hours]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      hours = Number(btn.dataset.hours || 168);
      panelPage = 1;
      await refreshHistory();
    });
  });

  refreshAll();
  setInterval(refreshStatus, 15000);
  setInterval(refreshHistory, 60000);
  setInterval(refreshEvents, 60000);
})();
