/* ============ 独立分享页：免登录只读渲染 ============
 * 页面由 GET /share/{token} 返回，内容来自 GET /api/share/{token}。
 * 该接口不要求登录，也不返回 user_id / feedback 等内部字段。
 * 本文件不依赖 app.js / api.js / common.js，可单独在移动端打开。
 */
(() => {
  "use strict";

  const root = document.getElementById("share-root");
  const themeBtn = document.getElementById("share-theme");
  const THEME_KEY = "app-theme";

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /* 渲染 Markdown；markdown.js 未加载时退化为转义后的纯文本，避免整页空白 */
  function renderMarkdown(text) {
    if (window.Markdown && typeof window.Markdown.render === "function") {
      try {
        return window.Markdown.render(text || "");
      } catch {
        /* 落到纯文本兜底 */
      }
    }
    return `<p>${escapeHtml(text || "").replace(/\n/g, "<br>")}</p>`;
  }

  /* 后端 SQLite 返回的时间无时区标记，值为 UTC；统一按 UTC 解析再转本地 */
  function fmtTime(iso) {
    if (!iso) return "-";
    const raw = String(iso);
    const hasTz = /(Z|[+-]\d{2}:?\d{2})\s*$/i.test(raw);
    const d = new Date(hasTz ? raw : raw + "Z");
    if (isNaN(d)) return raw;
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
      `${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function roleLabel(role) {
    return role === "assistant" ? "助教" : "学生";
  }

  function tokenFromPath() {
    const parts = window.location.pathname.split("/").filter(Boolean);
    const index = parts.indexOf("share");
    const raw = index >= 0 ? parts[index + 1] : parts[parts.length - 1];
    if (!raw) return "";
    try {
      return decodeURIComponent(raw);
    } catch {
      return raw;
    }
  }

  function shell(inner, { notice = "" } = {}) {
    return `
      ${notice ? `<div class="share-notice">${escapeHtml(notice)}</div>` : ""}
      <div class="share-card">${inner}</div>`;
  }

  function renderError(message, detail) {
    document.title = "分享内容 · 智答系统";
    root.innerHTML = shell(`
      <div class="share-error">
        <h2>${escapeHtml(message)}</h2>
        <p>${escapeHtml(detail || "请向分享者确认链接是否仍然有效。")}</p>
        <a class="btn btn-sm" href="/">进入智答系统</a>
      </div>`);
  }

  function sourcesHtml(sources) {
    if (!Array.isArray(sources) || !sources.length) return "";
    const items = sources.map((src) => {
      const title = escapeHtml(src.title || "未知来源");
      const course = String(src.course || "").trim();
      const section = String(src.section || "").trim();
      const page = src.page;
      const meta = [];
      if (course) meta.push(`课程：${course}`);
      if (section) meta.push(`位置：${section}`);
      if (page !== null && page !== undefined && page !== "") meta.push(`第 ${page} 页`);
      const content = String(src.content || "").trim();
      return `
        <div class="share-source">
          <b>${title}</b>${meta.length ? `（${escapeHtml(meta.join("；"))}）` : ""}
          ${content ? `<div class="quote">${escapeHtml(content)}</div>` : ""}
        </div>`;
    }).join("");
    return `
      <details class="share-sources">
        <summary>引用来源（${sources.length}）</summary>
        ${items}
      </details>`;
  }

  function renderConversation(data) {
    const conv = data.conversation || {};
    const messages = Array.isArray(conv.messages) ? conv.messages : [];
    const rows = messages.map((msg) => {
      const isAssistant = msg.role === "assistant";
      return `
        <article class="share-msg">
          <div class="share-msg-role ${isAssistant ? "assistant" : "user"}">
            <span class="dot" aria-hidden="true"></span>${roleLabel(msg.role)}
            <span class="share-msg-time">${escapeHtml(fmtTime(msg.created_at))}</span>
          </div>
          <div class="share-msg-body">${renderMarkdown(msg.content)}</div>
          ${sourcesHtml(msg.sources)}
          ${msg.interrupted ? `<div class="share-msg-time" style="margin-top:8px">该回答生成过程中被中断。</div>` : ""}
        </article>`;
    }).join("");
    document.title = `${conv.title || "会话"} · 智答系统分享`;
    root.innerHTML = shell(`
      <h1 class="share-title">${escapeHtml(conv.title || "未命名会话")}</h1>
      <div class="share-sub">
        创建于 ${escapeHtml(fmtTime(conv.created_at))} · ${messages.length} 条消息
      </div>
      <hr class="share-divider">
      ${rows || `<div class="share-empty">该会话暂无消息。</div>`}`,
      { notice: `本页由授权链接分享 · 有效期至 ${fmtTime(data.expires_at)}` });
  }

  function renderKnowledge(data) {
    const kb = data.knowledge_base || {};
    const points = Array.isArray(kb.points) ? kb.points : [];
    const rows = points.map((point) => {
      const tags = String(point.tags || "").split(",").map((t) => t.trim()).filter(Boolean);
      const meta = [];
      if (point.course) meta.push(`课程：${point.course}`);
      if (tags.length) meta.push(`标签：${tags.join("、")}`);
      meta.push(`更新于 ${fmtTime(point.updated_at)}`);
      return `
        <section class="share-point">
          <h2>${escapeHtml(point.title || "未命名知识点")}</h2>
          <div class="share-point-meta">${escapeHtml(meta.join(" · "))}</div>
          <div class="share-msg-body">${renderMarkdown(point.content)}</div>
        </section>`;
    }).join("");
    document.title = `${kb.name || "知识库"} · 智答系统分享`;
    root.innerHTML = shell(`
      <h1 class="share-title">${escapeHtml(kb.name || "未命名知识库")}</h1>
      <div class="share-sub">
        ${kb.description ? `${escapeHtml(kb.description)}<br>` : ""}
        ${points.length} 个知识点 · 更新于 ${escapeHtml(fmtTime(kb.updated_at))}
      </div>
      <hr class="share-divider">
      ${rows || `<div class="share-empty">该知识库暂无知识点。</div>`}`,
      { notice: `本页由授权链接分享 · 有效期至 ${fmtTime(data.expires_at)}` });
  }

  async function load() {
    const token = tokenFromPath();
    if (!token) {
      renderError("链接不完整", "缺少分享令牌，请复制完整链接。");
      return;
    }
    let resp;
    try {
      resp = await fetch(`/api/share/${encodeURIComponent(token)}`, {
        method: "GET",
        credentials: "omit",
        headers: { Accept: "application/json" },
      });
    } catch {
      renderError("加载失败", "网络请求失败，请稍后重试。");
      return;
    }
    if (resp.status === 404) {
      renderError("分享链接不存在或已失效", "链接可能已过期、被撤销，或分享内容已被删除。");
      return;
    }
    if (!resp.ok) {
      renderError("加载失败", `服务返回 ${resp.status}，请稍后重试。`);
      return;
    }
    let data;
    try {
      data = await resp.json();
    } catch {
      renderError("加载失败", "分享内容格式不正确。");
      return;
    }
    if (data.kind === "knowledge_base") renderKnowledge(data);
    else if (data.kind === "conversation") renderConversation(data);
    else renderError("无法识别的分享类型", "分享内容格式不正确。");
  }

  function initTheme() {
    if (!themeBtn) return;
    themeBtn.addEventListener("click", () => {
      const dark = document.documentElement.getAttribute("data-theme") === "dark";
      const next = dark ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem(THEME_KEY, next); } catch { /* 隐私模式下忽略 */ }
    });
  }

  initTheme();
  load();
})();
