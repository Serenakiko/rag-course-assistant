/* ============ 视图：我的反馈（学生端） ============ */
/* 待处理 / 已解决 分区展示；统计卡 + Tab 切换；未读高亮；点击条目后标记已读。
 * 分页在服务端做：以前一次最多拉 50 条再在前端切片，第 51 条以后学生永远看不到，
 * 统计卡的数字也只按这 50 条算。现在每页按需取，两个统计数字由后端按全量返回。 */
const FeedbackView = (() => {
  let rows = [];          // 当前这一页
  let total = 0;          // 当前 Tab 的匹配总数
  let counts = { pending: 0, resolved: 0 };   // 全量计数（统计卡用）
  let currentTab = "all";
  let pageSize = 10;      // 每页 10 条（分页条可切 10 / 20），三条 Tab 共用
  let page = 1;

  async function render(container) {
    container.innerHTML = `
      <div class="card fb-wrap">
        <div class="fb-stat-grid" id="fb-stats"></div>
        <div class="fb-tabs" id="fb-tabs">
          <button class="fb-tab active" data-tab="all">全部</button>
          <button class="fb-tab" data-tab="pending">处理中</button>
          <button class="fb-tab" data-tab="resolved">已解决</button>
        </div>
        <div class="fb-list" id="fb-list"><div class="fb-loading"><span class="loading-spinner"></span></div></div>
        <div class="pagination pagination-fixed" id="fb-pagination"></div>
      </div>`;

    document.getElementById("fb-tabs").addEventListener("click", (e) => {
      const btn = e.target.closest(".fb-tab");
      if (!btn) return;
      currentTab = btn.dataset.tab;
      page = 1;   // 换 Tab 回到第一页
      document.querySelectorAll(".fb-tab").forEach((b) =>
        b.classList.toggle("active", b === btn));
      load();
    });
    load();
  }

  async function load() {
    const list = document.getElementById("fb-list");
    if (!list) return;
    list.innerHTML = `<div class="fb-loading"><span class="loading-spinner"></span></div>`;
    const params = new URLSearchParams({
      limit: String(pageSize),
      offset: String((page - 1) * pageSize),
    });
    if (currentTab !== "all") params.set("status", currentTab);
    try {
      const data = await API.request("GET", `/api/chat/unanswered/mine?${params.toString()}`);
      total = (data && data.total) || 0;
      rows = (data && data.items) || [];
      counts = {
        pending: (data && data.pending) || 0,
        resolved: (data && data.resolved) || 0,
      };
      const totalPages = Math.max(1, Math.ceil(total / pageSize));
      if (page > totalPages) { page = totalPages; return load(); }   // 删完最后一页往回退
      renderStats();
      renderList();
    } catch (err) {
      list.innerHTML = `<div class="fb-empty"><p style="color:var(--danger)">${Common.escapeHtml(err.message)}</p></div>`;
    }
  }

  function renderStats() {
    const stats = document.getElementById("fb-stats");
    if (!stats) return;
    // 与首页（工作台）统计卡同款样式：白色卡片 + 浅色图标块 + 数字/标签
    const ICONS = {
      clock: `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>`,
      check: `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>`,
    };
    stats.innerHTML = `
      <div class="stat-card">
        <div class="stat-icon blue">${ICONS.clock}</div>
        <div class="stat-info"><div class="stat-num">${counts.pending}</div><div class="stat-label">待处理</div></div>
      </div>
      <div class="stat-card">
        <div class="stat-icon green">${ICONS.check}</div>
        <div class="stat-info"><div class="stat-num">${counts.resolved}</div><div class="stat-label">已解决</div></div>
      </div>`;
  }

  function renderList() {
    const list = document.getElementById("fb-list");
    if (!list) return;
    if (!rows.length) {
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
      renderPager();
      return;
    }
    list.innerHTML = rows.map((it) => `
      <div class="fb-item ${it.status === "resolved" && !it.read ? "fb-unread" : ""}" data-id="${it.id}">
        <div class="fb-head">
          ${it.status === "resolved" ? `<span class="fb-badge fb-badge-done">已解决</span>` : ""}
          ${it.status === "resolved" && !it.read ? `<span class="fb-badge fb-badge-new" title="点击这条反馈标为已读">新回答</span>` : ""}
          <b class="fb-q" title="${Common.escapeHtml(it.question)}">${Common.escapeHtml(it.question)}</b>
          <span class="fb-time">${Common.fmtTime(it.status === "resolved" ? it.answered_at : it.created_at)}</span>
        </div>
        ${it.status === "resolved"
          ? `<div class="fb-answer md-body">${Markdown.render(it.answer)}</div>`
          : `<div class="fb-wait-tip"><span>已提交跟进，解决后会通知你</span></div>`}
      </div>`).join("");

    // 点击这条反馈才标已读：进页面就全标掉的话，未读高亮和角标等于白做
    list.querySelectorAll(".fb-item").forEach((el) =>
      el.addEventListener("click", () => markRead(Number(el.dataset.id))));
    renderPager();
  }

  function renderPager() {
    Common.renderPagination("fb-pagination", total, page, pageSize, (p) => {
      page = p;
      load();
    }, {
      sizeOptions: [10, 20],
      hidePageInfo: true,   // 只留「共 N 条」，不显示「第 1/2 页」
      onSizeChange: (s) => { pageSize = s; page = 1; load(); },
    });
  }

  async function markRead(id) {
    const item = rows.find((x) => x.id === id);
    if (!item || item.status !== "resolved" || item.read) return;
    try {
      await API.request("POST", `/api/chat/unanswered/${id}/read`);
      item.read = true;
      renderList();
    } catch { /* 忽略：下次再点 */ }
  }

  return { render };
})();
