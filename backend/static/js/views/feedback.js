/* ============ 视图：我的反馈（学生端） ============ */
/* 待处理 / 已解决 分区展示；统计卡 + Tab 切换；未读高亮；进入即批量标记已读 */
const FeedbackView = (() => {
  let allItems = [];
  let currentTab = "all";

  async function render(container) {
    container.innerHTML = `
      <div class="card fb-wrap">
        <div class="fb-stat-grid" id="fb-stats"></div>
        <div class="fb-tabs" id="fb-tabs">
          <button class="fb-tab active" data-tab="all">全部</button>
          <button class="fb-tab" data-tab="pending">处理中</button>
          <button class="fb-tab" data-tab="resolved">已解决</button>
        </div>
        <div id="fb-list"><div class="fb-loading"><span class="loading-spinner"></span></div></div>
      </div>`;

    document.getElementById("fb-tabs").addEventListener("click", (e) => {
      const btn = e.target.closest(".fb-tab");
      if (!btn) return;
      currentTab = btn.dataset.tab;
      document.querySelectorAll(".fb-tab").forEach((b) =>
        b.classList.toggle("active", b === btn));
      renderList();
    });
    load();
  }

  async function load() {
    const list = document.getElementById("fb-list");
    if (!list) return;
    list.innerHTML = `<div class="fb-loading"><span class="loading-spinner"></span></div>`;
    try {
      allItems = await API.request("GET", "/api/chat/unanswered/mine");
      renderStats();
      renderList();
      // 进入即视为已读：只标记“已解决且未读”的记录，随后刷新侧边栏角标
      const unreadIds = allItems
        .filter((it) => it.status === "resolved" && !it.read)
        .map((it) => it.id);
      if (unreadIds.length) {
        await Promise.all(unreadIds.map((id) =>
          API.request("POST", `/api/chat/unanswered/${id}/read`).catch(() => null)));
        allItems = allItems.map((it) =>
          unreadIds.includes(it.id) ? { ...it, read: true } : it);
        renderStats();
        renderList();
        App.refreshFeedbackBadge();
      }
    } catch (err) {
      list.innerHTML = `<div class="fb-empty"><p style="color:var(--danger)">${Common.escapeHtml(err.message)}</p></div>`;
    }
  }

  function renderStats() {
    const stats = document.getElementById("fb-stats");
    if (!stats) return;
    const pending = allItems.filter((it) => it.status === "pending").length;
    const resolved = allItems.filter((it) => it.status === "resolved").length;
    // 与首页（工作台）统计卡同款样式：白色卡片 + 浅色图标块 + 数字/标签
    const ICONS = {
      clock: `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>`,
      check: `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>`,
    };
    stats.innerHTML = `
      <div class="stat-card">
        <div class="stat-icon blue">${ICONS.clock}</div>
        <div class="stat-info"><div class="stat-num">${pending}</div><div class="stat-label">待处理</div></div>
      </div>
      <div class="stat-card">
        <div class="stat-icon green">${ICONS.check}</div>
        <div class="stat-info"><div class="stat-num">${resolved}</div><div class="stat-label">已解决</div></div>
      </div>`;
  }

  function renderList() {
    const list = document.getElementById("fb-list");
    if (!list) return;
    const items = currentTab === "all"
      ? allItems
      : allItems.filter((it) => it.status === currentTab);
    if (!items.length) {
      list.innerHTML = `<div class="fb-empty">
        <div class="fb-empty-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></div>
        <p>${currentTab === "pending"
          ? "没有待处理的问题，你的提问都能被知识库覆盖～"
          : currentTab === "resolved"
            ? "还没有已解决的反馈，解决后会自动展示在这里"
            : "暂无反馈记录。提问未被知识库覆盖时，会自动提交跟进。"}</p>
        <button class="btn btn-primary btn-sm" id="fb-go-chat">去提问</button>
      </div>`;
      const go = document.getElementById("fb-go-chat");
      if (go) go.addEventListener("click", () => App.navigate("chat"));
      return;
    }
    list.innerHTML = items.map((it) => `
      <div class="fb-item ${it.status === "resolved" && !it.read ? "fb-unread" : ""}" data-id="${it.id}">
        <div class="fb-head">
          ${it.status === "resolved" ? `<span class="fb-badge fb-badge-done">已解决</span>` : ""}
          <b class="fb-q" title="${Common.escapeHtml(it.question)}">${Common.escapeHtml(it.question)}</b>
          <span class="fb-time">${Common.fmtTime(it.status === "resolved" ? it.answered_at : it.created_at)}</span>
        </div>
        ${it.status === "resolved"
          ? `<div class="fb-answer md-body">${Markdown.render(it.answer)}</div>`
          : `<div class="fb-wait-tip"><span>已提交跟进，解决后会通知你</span></div>`}
      </div>`).join("");
  }

  return { render };
})();
