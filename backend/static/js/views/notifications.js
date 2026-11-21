/* 站内通知：列表 + 未读切换，样式复用 fb-* 组件。 */
const NotificationsView = (() => {
  const PAGE_SIZE = 20;
  const KIND_LABEL = {
    system: "系统",
    answer_ready: "补答",
    feedback_reply: "反馈",
    doc_failed: "解析失败",
    feedback_alert: "差评",
  };

  const isAdmin = () => {
    const user = (typeof API !== "undefined" && API.getUser && API.getUser()) || {};
    return user.role === "admin";
  };

  let rows = [];
  let unread = 0;
  let hasMore = false;
  let unreadOnly = false;
  let loading = false;

  async function render(container) {
    container.innerHTML = `
      <div class="card fb-wrap">
        <div class="fb-tabs" id="nt-tabs">
          <button class="fb-tab active" data-tab="all">全部</button>
          <button class="fb-tab" data-tab="unread">未读<span id="nt-unread">0</span></button>
          <button class="btn btn-sm" id="nt-read-all" style="margin-left:auto">全部已读</button>
        </div>
        <div class="fb-list" id="nt-list"><div class="fb-loading"><span class="loading-spinner"></span></div></div>
        <div class="nt-more" id="nt-more"></div>
      </div>`;

    document.getElementById("nt-tabs").addEventListener("click", (e) => {
      const btn = e.target.closest(".fb-tab");
      if (!btn) return;
      unreadOnly = btn.dataset.tab === "unread";
      document.querySelectorAll("#nt-tabs .fb-tab").forEach((b) =>
        b.classList.toggle("active", b === btn));
      load(true);
    });

    const readAllBtn = document.getElementById("nt-read-all");
    if (readAllBtn) readAllBtn.addEventListener("click", readAll);

    load(true);
  }

  async function load(reset) {
    const list = document.getElementById("nt-list");
    if (!list || loading) return;
    loading = true;
    if (reset) {
      rows = [];
      hasMore = false;
      list.innerHTML = `<div class="fb-loading"><span class="loading-spinner"></span></div>`;
    }
    const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
    if (unreadOnly) params.set("unread_only", "true");
    if (!reset && rows.length) params.set("before_id", String(rows[rows.length - 1].id));
    try {
      const data = await API.request("GET", `/api/notifications?${params.toString()}`);
      const items = (data && data.items) || [];
      if (reset) rows = items;
      else rows = rows.concat(items);
      unread = (data && data.unread) || 0;
      hasMore = !!(data && data.has_more);
      renderList();
      renderMore();
      renderUnread();
    } catch (err) {
      list.innerHTML = `<div class="fb-empty"><p style="color:var(--danger)">${Common.escapeHtml(err.message)}</p></div>`;
    } finally {
      loading = false;
    }
  }

  function renderUnread() {
    const badge = document.getElementById("nt-unread");
    if (badge) badge.textContent = unread > 0 ? String(unread) : "0";
    const readAllBtn = document.getElementById("nt-read-all");
    if (readAllBtn) readAllBtn.disabled = unread === 0;
  }

  function renderList() {
    const list = document.getElementById("nt-list");
    if (!list) return;
    if (!rows.length) {
      list.innerHTML = `<div class="fb-empty">
        <div class="fb-empty-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg></div>
        <p>${unreadOnly ? "没有未读通知，都看完啦～" : (isAdmin() ? "还没有通知。学生差评、文档解析失败时会在这里提醒你。" : "还没有通知。补答完成、反馈有结果时会在这里提醒你。")}</p>
      </div>`;
      return;
    }
    list.innerHTML = rows.map((it) => `
      <div class="fb-item${it.read ? "" : " nt-unread"}" data-id="${it.id}" ${it.link ? 'role="link" tabindex="0"' : ""}>
        <div class="fb-head">
          <span class="fb-badge ${it.read ? "fb-badge-done" : "fb-badge-pending"}">${Common.escapeHtml(KIND_LABEL[it.kind] || it.kind || "通知")}</span>
          <b class="fb-q">${Common.escapeHtml(it.title || "")}</b>
          <span class="fb-time">${Common.fmtTime(it.created_at)}</span>
        </div>
        ${it.body ? `<div class="fb-meta nt-body">${Common.escapeHtml(it.body)}</div>` : ""}
      </div>`).join("");

    list.querySelectorAll(".fb-item").forEach((el) => {
      el.addEventListener("click", () => open(Number(el.dataset.id)));
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open(Number(el.dataset.id));
        }
      });
    });
  }

  function renderMore() {
    const host = document.getElementById("nt-more");
    if (!host) return;
    if (!hasMore) {
      host.innerHTML = rows.length
        ? `<div class="nt-more-done">没有更多了</div>`
        : "";
      return;
    }
    host.innerHTML = `<button class="btn btn-sm" id="nt-load-more">加载更多</button>`;
    document.getElementById("nt-load-more").addEventListener("click", () => load(false));
  }

  async function markRead(id) {
    const item = rows.find((x) => x.id === id);
    if (!item || item.read) return item;
    try {
      const data = await API.request("POST", `/api/notifications/${id}/read`);
      item.read = true;
      unread = (data && typeof data.unread === "number") ? data.unread : unread;
      if (unreadOnly) rows = rows.filter((x) => x.id !== id);
      renderList();
      renderUnread();
      // 铃铛角标立刻同步，而不是等 30s 轮询
      if (typeof NotifyCenter !== "undefined") NotifyCenter.refresh();
      return item;
    } catch (err) {
      Common.toast("标记已读失败：" + err.message, "error");
      return item;
    }
  }

  /* 点击通知：先标已读，再按 link 跳前端路由（如 #/feedback） */
  async function open(id) {
    const item = await markRead(id);
    if (!item || !item.link) return;
    const key = String(item.link).replace(/^#\/?/, "").trim();
    if (!key) return;
    if (typeof App !== "undefined" && App.navigate) {
      App.navigate(key);
    } else {
      window.location.hash = "#/" + key;
    }
  }

  async function readAll() {
    if (unread === 0) return;
    try {
      await API.request("POST", "/api/notifications/read-all");
      rows.forEach((x) => { x.read = true; });
      unread = 0;
      if (unreadOnly) rows = [];
      renderList();
      renderMore();
      renderUnread();
      if (typeof NotifyCenter !== "undefined") NotifyCenter.refresh();
    } catch (err) {
      Common.toast("操作失败：" + err.message, "error");
    }
  }

  return { render };
})();

/* ==================== 主会话接线清单（本文件不改 app.html / app.js / app.css） ====================
 *
 * 1) backend/static/app.html：在 views 脚本区末尾加一行
 *      <script src="/static/js/views/notifications.js?v=1"></script>
 *    （放在 app.js 之前、feedback.js 附近均可；app.js 在 DOMContentLoaded 时才会调用视图）
 *
 * 2) backend/static/js/app.js：
 *    a. TITLES.student / TITLES.admin 各加一项：notifications: "通知"
 *    b. NAV：学生组「学习记录」、管理员组「问答运营」各加
 *         { key: "notifications", icon: ICON.bell, label: "通知" }
 *    c. navigate() 的 switch 里加一个分支：
 *         case "notifications": NotificationsView.render(container); break;
 *    （不需要 destroy：本视图没有定时器/流式连接）
 *
 * 3) 顶部铃铛（可选但建议）：当前 NotifyCenter 点击直达 feedback/unanswered，
 *    未读数来自 unanswered 接口。可改为：
 *      - 点击 → App.navigate("notifications")
 *      - refresh() 的学生分支改为 GET /api/notifications?limit=1 取 data.unread
 *        （answer_ready / feedback_reply 已经覆盖「补答完成」这件事；
 *         若想保留 pending 提示，可用 unread + pending 之和，但会重复计数）
 *    若要下拉面板：在 header-notify 里渲染最近 5 条（GET /api/notifications?limit=5）
 *    + 「查看全部」跳 notifications 视图，点击条目复用本文件的「标已读 + 跳 link」逻辑。
 *
 * 4) 未读高亮：本文件给未读条目加了 class="nt-unread"（与 .fb-item 同级），
 *    app.css 里没有这条规则；如需左侧色条/浅底，请在 app.css 补：
 *      .fb-item.nt-unread { background: rgba(59,130,246,.06); }
 *      .nt-body { white-space: pre-wrap; }
 *      .nt-more { display:flex; justify-content:center; padding:12px 0; }
 *      .nt-more-done { color: var(--text-muted, #888); font-size: 12px; text-align:center; }
 *    （不补也能用，只是没有未读底色和「加载更多」居中）
 * ====================================================================================== */
