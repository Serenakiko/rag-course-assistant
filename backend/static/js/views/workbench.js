/* ============ 视图：学生首页（个人概览 + 快捷入口） ============ */
const WorkbenchView = (() => {
  /* 单色线性图标（灰色，不带彩色） */
  const S = {
    chat: `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>`,
    send: `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`,
    book: `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>`,
    file: `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
    check: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>`,
    user: `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,
    arrow: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>`,
  };

  function render(container) {
    const user = API.getUser();
    const now = new Date();
    const md = `${now.getMonth() + 1}/${now.getDate()}`;

    container.innerHTML = `
      <div class="wb-page">
      <div class="card wb-hero">
        <div class="row" style="gap:20px;align-items:center">
          <div style="flex:1;min-width:0">
            <div class="wb-hero-title">欢迎使用智答系统</div>
            <div class="wb-hero-sub">您好，${Common.escapeHtml(user.real_name || user.username)}，今天想学点什么？</div>
            <div class="wb-hero-desc">基于 RAG 技术的智能课程助教平台，结合课程知识库，为你的学习提供精准的问答支持。</div>
          </div>
          <div class="wb-hero-side">
            <div class="wb-hero-info"><b>${md}</b><span>今日日期</span></div>
          </div>
        </div>
      </div>

      <div class="stat-grid" id="wb-stat-grid"><div style="grid-column:1/-1;text-align:center;padding:40px"><span class="loading-spinner"></span></div></div>

      <div class="wb-section">
        <div class="wb-section-title">核心功能</div>
      </div>
      <div class="wb-func-grid">
        <div class="wb-func-card" data-go="knowledge">
          <div class="wb-func-icon orange">${S.book}</div>
          <div style="flex:1;min-width:0">
            <div class="wb-func-name">知识库</div>
            <div class="wb-func-desc">查看课程资料与知识点，随时查阅</div>
          </div>
        </div>
        <div class="wb-func-card" data-go="chat">
          <div class="wb-func-icon blue">${S.chat}</div>
          <div style="flex:1;min-width:0">
            <div class="wb-func-name">智能问答</div>
            <div class="wb-func-desc">向课程助教提问，实时获取回答</div>
          </div>
        </div>
        <div class="wb-func-card" data-go="settings">
          <div class="wb-func-icon green">${S.user}</div>
          <div style="flex:1;min-width:0">
            <div class="wb-func-name">个人中心</div>
            <div class="wb-func-desc">管理个人信息与账户安全</div>
          </div>
        </div>
      </div>

      <div class="card wb-fb-card" id="wb-resolved">
        <div class="row-between mb-16">
          <b>我的反馈</b>
          <button class="btn btn-sm" id="wb-fb-all">查看全部</button>
        </div>
        <div class="wb-fb-list" id="wb-resolved-list"><div class="fb-loading"><span class="loading-spinner"></span></div></div>
      </div>
      </div>`;

    document.getElementById("wb-fb-all").addEventListener("click", () => App.navigate("feedback"));

    container.querySelectorAll(".wb-func-card").forEach((card) => {
      card.addEventListener("click", () => App.navigate(card.dataset.go));
    });

    wireRefresh();
    loadStats();
  }

  /* 回到本页 / 窗口重新获得焦点时重拉一次：提问完切回来，数字要跟着变 */
  let refreshWired = false;
  function wireRefresh() {
    if (refreshWired) return;
    refreshWired = true;
    const refresh = () => {
      if (document.getElementById("wb-stat-grid")) loadStats();
    };
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
    window.addEventListener("focus", refresh);
  }

  async function loadStats() {
    // 我的反馈：与管理员数据概览反馈区块同款行式列表（状态徽标 + 问题 + 时间），点击进入反馈页
    try {
      // 接口返回 {total, pending, resolved, items}，列表在 items 里（曾经这里直接
      // 当数组用，改成对象之后这块一渲染就报「slice is not a function」）
      const data = await API.request("GET", "/api/chat/unanswered/mine?limit=50");
      // 服务端已按提交时间倒序（created_at desc），前几条就是最近的。
      // 以前这里再按时间排一次，但接口当时是按 id 取「最新 50 条」，两者顺序
      // 不一致时会取错「最近 5 条」——排序口径统一交给后端。
      const items = (data && data.items) || [];
      const listEl = document.getElementById("wb-resolved-list");
      if (listEl) {
        if (!items || !items.length) {
          listEl.innerHTML = `<div class="wb-fb-empty">还没有提交过反馈。提问时如果知识库里没有相关内容，会自动记到这里；管理员补完资料后，你能在这里看到回答。</div>`;
        } else {
          listEl.innerHTML = items.slice(0, 5).map((r) => `
            <div class="fb-row" data-fb="${r.id}">
              <span class="fb-status">${r.status === "resolved" ? "已解决" : "处理中"}</span>
              <span class="fb-q" style="flex:1;min-width:0">${Common.escapeHtml(r.question)}</span>
              <span class="muted" style="flex:none">${Common.fmtTime(r.status === "resolved" ? r.answered_at : r.created_at)}</span>
            </div>`).join("");
          listEl.querySelectorAll("[data-fb]").forEach((row) =>
            row.addEventListener("click", () => App.navigate("feedback")));
        }
      }
    } catch (err) {
      const listEl = document.getElementById("wb-resolved-list");
      if (listEl) listEl.innerHTML = `<div class="wb-fb-empty">反馈加载失败：${Common.escapeHtml(err.message)}</div>`;
    }

    try {
      // 四张卡都是学生自己的东西；加时间戳避免浏览器拿缓存里的旧数字
      const s = await API.request("GET", "/api/stats/me?_=" + Date.now());
      // ?? 0 兜底：后端字段还没更新时也显示 0，不出现 undefined
      const cards = [
        { icon: S.chat, num: s.conversations ?? 0, label: "我的会话", cls: "blue", go: "history" },
        { icon: S.send, num: s.questions ?? 0, label: "提问消息", cls: "green", go: "chat" },
        { icon: S.file, num: s.my_documents ?? 0, label: "我的文档", cls: "orange", go: "documents" },
        { icon: S.book, num: s.knowledge_bases ?? 0, label: "知识库", cls: "red", go: "knowledge" },
      ];
      const grid = document.getElementById("wb-stat-grid");
      grid.innerHTML = cards.map((c) => `
        <div class="stat-card clickable" data-go="${c.go}" title="点击查看${c.label}">
          <div class="stat-icon ${c.cls}">${c.icon}</div>
          <div class="stat-info"><div class="stat-num">${c.num}</div><div class="stat-label">${c.label}</div></div>
        </div>`).join("");
      grid.querySelectorAll(".stat-card[data-go]").forEach((card) =>
        card.addEventListener("click", () => App.navigate(card.dataset.go)));
    } catch (err) {
      document.getElementById("wb-stat-grid").innerHTML =
        `<div class="empty-state" style="grid-column:1/-1"><p>${Common.escapeHtml(err.message)}</p></div>`;
    }
  }

  return { render };
})();
