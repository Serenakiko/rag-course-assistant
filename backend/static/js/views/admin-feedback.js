/* ============ 视图：问题反馈（管理台） ============
 * 统计卡 + 四个 tab（全部 / 处理中 / 已解决 / 按问题聚类） + 生成回答、删除。
 * 从 views/admin.js 里拆出来的独立模块：原来 admin.js 一个文件 1200 多行，
 * 改「每页几条」这种小事也要在四个模块之间翻。这里只依赖全局 Common / API /
 * Markdown / App，由 AdminView.render(container, "unanswered") 委托进来。
 */
const AdminFeedbackView = (() => {
  /* ==================== 反馈（学生提问未答上的问题） ==================== */
  let uqRows = [];      // 当前页
  let uqTotal = 0;
  let uqPage = 1;
  let uqPageSize = 10;   // 默认一页 10 条，可在 10 / 20 之间切换（四个 tab 共用，底部条统一）
  let uqStatus = "";
  let clusterRows = [];          // 聚类分组（一次拉全，前端分页）
  let clusterPage = 1;

  function renderUnanswered(container) {
    container.innerHTML = `
      <div class="fb-stat-grid" id="uq-stats"></div>
      <div class="fb-tabs" id="uq-tabs">
        <button class="fb-tab active" data-tab="">全部</button>
        <button class="fb-tab" data-tab="pending">处理中</button>
        <button class="fb-tab" data-tab="resolved">已解决</button>
        <button class="fb-tab" data-tab="clusters">按问题聚类</button>
      </div>
      <div id="uq-list"><div class="fb-loading"><span class="loading-spinner"></span></div></div>
      <div class="pagination pagination-fixed" id="uq-pagination"></div>`;

    document.getElementById("uq-tabs").addEventListener("click", (e) => {
      const btn = e.target.closest(".fb-tab");
      if (!btn) return;
      uqStatus = btn.dataset.tab;
      uqPage = 1;
      document.querySelectorAll(".fb-tab").forEach((b) =>
        b.classList.toggle("active", b === btn));
      if (uqStatus === "clusters") loadClusters();
      else loadUnanswered();
    });
    uqPage = 1;
    loadUnanswered();
  }

  async function loadUnanswered() {
    const list = document.getElementById("uq-list");
    if (!list) return;
    list.innerHTML = `<div class="fb-loading"><span class="loading-spinner"></span></div>`;
    try {
      const params = new URLSearchParams({
        limit: String(uqPageSize),
        offset: String((uqPage - 1) * uqPageSize),
      });
      if (uqStatus) params.set("status", uqStatus);
      const [data, stats] = await Promise.all([
        API.request("GET", `/api/admin/unanswered?${params.toString()}`),
        API.request("GET", "/api/admin/unanswered/stats").catch(() => null),
      ]);
      uqRows = data.items || [];
      uqTotal = data.total || 0;
      const totalPages = Math.max(1, Math.ceil(uqTotal / uqPageSize));
      if (uqPage > totalPages) { uqPage = totalPages; return loadUnanswered(); }
      renderUqStats(stats);
      renderUqList();
    } catch (err) {
      list.innerHTML = `<div class="fb-empty"><p style="color:var(--danger)">${Common.escapeHtml(err.message)}</p></div>`;
      // 拉不到数据就别留着上一页的「共 N 条 ‹ 1 ›」，否则点页码会一直在报错
      const pag = document.getElementById("uq-pagination");
      if (pag) pag.innerHTML = "";
    }
  }

  /* 聚类视图：问法不同但意思相近的问题并成一组，按人数排 */
  async function loadClusters() {
    const list = document.getElementById("uq-list");
    if (!list) return;
    list.innerHTML = `<div class="fb-loading"><span class="loading-spinner"></span></div>`;
    try {
      const [data, stats] = await Promise.all([
        API.request("GET", "/api/admin/unanswered/clusters?status=pending"),
        API.request("GET", "/api/admin/unanswered/stats").catch(() => null),
      ]);
      renderUqStats(stats);
      clusterRows = data.items || [];
      clusterPage = 1;
      renderClusters();
    } catch (err) {
      list.innerHTML = `<div class="fb-empty"><p style="color:var(--danger)">${Common.escapeHtml(err.message)}</p></div>`;
      const pag = document.getElementById("uq-pagination");
      if (pag) pag.innerHTML = "";
    }
  }

  /* 聚类分组可能几十条：和「全部 / 已解决」一样底部给分页（数据一次拉全，前端切页） */
  /* 底部条换每页条数后，按当前 tab 重画：列表走接口重新拉，聚类在本地重新切片 */
  function renderListByTab() {
    if (uqStatus === "clusters") { clusterPage = 1; renderClusters(); }
    else { uqPage = 1; loadUnanswered(); }
  }

  function renderClusters() {
    const list = document.getElementById("uq-list");
    if (!list) return;
    if (!clusterRows.length) {
      list.innerHTML = `<div class="fb-empty"><p>没有待处理的问题，聚类里暂时没东西可看</p></div>`;
      Common.renderPagination("uq-pagination", 0, 1, uqPageSize, () => {}, {
        sizeOptions: [10, 20],
        onSizeChange: (s) => { uqPageSize = s; renderListByTab(); },
      });
      return;
    }

    const totalPages = Math.max(1, Math.ceil(clusterRows.length / uqPageSize));
    if (clusterPage > totalPages) clusterPage = totalPages;
    const startIdx = (clusterPage - 1) * uqPageSize;
    const pageRows = clusterRows.slice(startIdx, startIdx + uqPageSize);

    list.innerHTML = pageRows.map((g) => `
      <div class="fb-item">
        <div class="fb-head">
          <span class="fb-badge" style="background:#e7eff1;color:#17505e">${g.count} 人问过</span>
          <b class="fb-q" title="${Common.escapeHtml(g.question)}">${Common.escapeHtml(g.question)}</b>
          <span class="fb-time">${Common.fmtTime(g.latest_at)}</span>
        </div>
        <div class="fb-meta">
          ${g.course ? `知识库：${Common.escapeHtml(g.course)}` : "未关联知识库"}
          ${g.creators && g.creators.length ? ` · 提问者：${g.creators.map((c) => Common.escapeHtml(c)).join("、")}` : ""}
        </div>
        ${g.count > 1
          ? `<div class="fb-answer" style="background:#faf9f7;line-height:1.9">${g.variants.map((v) => `<div style="font-size:13px">· ${Common.escapeHtml(v)}</div>`).join("")}</div>`
          : `<div class="fb-wait-tip"><span>只有一个人问过</span></div>`}
      </div>`).join("");

    Common.renderPagination("uq-pagination", clusterRows.length, clusterPage, uqPageSize, (p) => {
      clusterPage = p;
      renderClusters();
    }, {
      sizeOptions: [10, 20],
      onSizeChange: (s) => { uqPageSize = s; renderListByTab(); },
    });
  }

  /* 与学生端「我的反馈」同款统计卡：白色卡片 + 浅色图标块 + 数字/标签 */
  function renderUqStats(stats) {
    const el = document.getElementById("uq-stats");
    if (!el) return;
    const pending = stats && stats.pending != null
      ? stats.pending
      : 0;
    const resolved = stats && stats.resolved != null
      ? stats.resolved
      : 0;
    const ICONS = {
      clock: `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>`,
      check: `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>`,
    };
    el.innerHTML = `
      <div class="stat-card">
        <div class="stat-icon blue">${ICONS.clock}</div>
        <div class="stat-info"><div class="stat-num">${pending}</div><div class="stat-label">待处理</div></div>
      </div>
      <div class="stat-card">
        <div class="stat-icon green">${ICONS.check}</div>
        <div class="stat-info"><div class="stat-num">${resolved}</div><div class="stat-label">已解决</div></div>
      </div>`;
  }

  /* 卡片式列表：与学生端「我的反馈」同款，右侧补充管理员操作（生成回答 / 删除） */
  function renderUqList() {
    const list = document.getElementById("uq-list");
    if (!list) return;

    if (!uqRows.length) {
      list.innerHTML = `<div class="fb-empty">
        <div class="fb-empty-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></div>
        <p>${uqStatus === "pending"
          ? "没有待处理的问题反馈"
          : uqStatus === "resolved"
            ? "还没有已解决的反馈"
            : "还没有收到问题反馈"}</p>
      </div>`;
      Common.renderPagination("uq-pagination", 0, 1, uqPageSize, () => {}, {
        sizeOptions: [10, 20],
        onSizeChange: (s) => { uqPageSize = s; renderListByTab(); },
      });
      return;
    }
    list.innerHTML = uqRows.map((q) => `
      <div class="fb-item">
        <div class="fb-head">
          ${q.status === "resolved" ? `<span class="fb-badge fb-badge-done">已解决</span>` : ""}
          <b class="fb-q" title="${Common.escapeHtml(q.question)}">${Common.escapeHtml(q.question)}</b>
          <span class="fb-time">${Common.fmtTime(q.status === "resolved" ? q.answered_at : q.created_at)}</span>
        </div>
        <div class="fb-meta">提问人：${Common.escapeHtml(q.creator_name || "-")}</div>
        ${q.status === "resolved"
          ? `<div class="fb-answer md-body">${Markdown.render(q.answer)}</div>`
          : `<div class="fb-wait-tip"><span>待补充资料并生成回答，解决后学生端会收到通知</span></div>`}
        <div class="fb-ops">
          <button class="btn btn-sm" data-gen="${q.id}">${q.status === "resolved" ? "重新生成" : "生成回答"}</button>
          <button class="btn btn-sm btn-danger" data-del="${q.id}">删除</button>
        </div>
      </div>`).join("");

    list.querySelectorAll("[data-gen]").forEach((b) =>
      b.addEventListener("click", () => openGenerateGuide(uqRows.find((x) => x.id === Number(b.dataset.gen)))));
    list.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () => removeUq(uqRows.find((x) => x.id === Number(b.dataset.del)))));

    Common.renderPagination("uq-pagination", uqTotal, uqPage, uqPageSize, (p) => {
      uqPage = p; loadUnanswered();
    }, {
      sizeOptions: [10, 20],
      onSizeChange: (s) => { uqPageSize = s; renderListByTab(); },
    });
  }

  /* 生成回答引导：先确认知识库已上传相关内容，再生成 */
  function openGenerateGuide(q) {
    if (!q) return;
    Common.openModal(`
      <div class="modal-header" style="margin-bottom:14px">
        <h3 style="font-size:17px;margin:0">生成回答</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      <div style="background:#faf9f7;border:1px solid #e6e3dc;border-radius:10px;padding:14px 16px;font-size:15px;line-height:1.75">${Common.escapeHtml(q.question)}</div>
      <div class="modal-footer" style="margin-top:20px;justify-content:flex-end;gap:10px">
        <button class="btn" data-go-doc>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
          去上传文档
        </button>
        <button class="btn" data-do-gen>
          我已上传，生成
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
        </button>
      </div>
    `, { onOpen: (box) => {
      box.style.maxWidth = "460px";
      box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));
      box.querySelector("[data-go-doc]").addEventListener("click", () => {
        Common.closeModal();
        App.navigate("documents");
      });
      box.querySelector("[data-do-gen]").addEventListener("click", () => {
        Common.closeModal();
        doRegenerate(q.id);
      });
    }});
  }

  async function doRegenerate(qid) {
    try {
      const r = await API.request("POST", `/api/admin/unanswered/${qid}/regenerate`);
      if (r.resolved) Common.toast("回答已生成，学生那边能看到了", "success");
      else Common.toast(r.message || "知识库还没有收录相关内容，先添加资料再生成", "error");
      loadUnanswered();
    } catch (err) { Common.toast(err.message, "error"); }
  }

  async function removeUq(q) {
    if (!q) return;
    const ok = await Common.confirmDialog({
      title: "删除记录",
      message: `将删除问题「${Common.escapeHtml(q.question)}」的记录，确认？`,
      okText: "删除",
    });
    if (!ok) return;
    try {
      await API.request("DELETE", `/api/admin/unanswered/${q.id}`);
      Common.toast("已删除", "success");
      loadUnanswered();
    } catch (err) { Common.toast(err.message, "error"); }
  }


  return { render: renderUnanswered };
})();
