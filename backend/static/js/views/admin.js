/* ============ 视图：管理员（数据概览 / 用户管理 / 知识库管理 / 对话历史） ============
 * 「问题反馈」单独在 views/admin-feedback.js（AdminFeedbackView）里，这里只做分派。 */
const AdminView = (() => {
  const SYSTEM_ADMIN_ACCOUNT = "23040101";
  /* SVG 图标库（线性，stroke=currentColor） */
  const ICON = {
    user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
    shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>',
    users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
    doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
    chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
    question: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  };

  /* ==================== 数据概览 ==================== */
  function renderOverview(container) {
    container.innerHTML = `
      <div class="stat-grid" id="stat-grid">
        <div style="grid-column:1/-1;text-align:center;padding:40px"><span class="loading-spinner"></span></div>
      </div>
      <div class="quality-grid" id="quality-grid"></div>
      <div class="chart-row">
        <div class="chart-card">
          <h3>近7天提问趋势</h3>
          <div id="chart-trend"></div>
        </div>
        <div class="chart-card">
          <h3>知识库文档占比</h3>
          <div id="chart-donut"></div>
        </div>
      </div>
      <div class="overview-bottom">
        <div class="card" id="usage-card">
          <div class="row-between mb-16">
            <b>文档引用排行</b>
          </div>
          <div id="usage-list"><div class="fb-loading"><span class="loading-spinner"></span></div></div>
        </div>
        <div class="card" id="hot-card">
          <div class="row-between mb-16">
            <b>热门问题</b>
          </div>
          <div id="hot-list"><div class="fb-loading"><span class="loading-spinner"></span></div></div>
        </div>
        <div class="card" id="fb-card" style="display:none">
          <div class="row-between mb-16">
            <b>未命中问题</b>
            <button class="btn btn-sm" id="fb-all">查看全部</button>
          </div>
          <div id="fb-list"></div>
        </div>
      </div>`;
    loadStats();
    loadDocUsage();
  }

  async function loadStats() {
    const grid = document.getElementById("stat-grid");
    try {
      const s = await API.request("GET", "/api/stats/admin/stats");
      const cards = [
        { key: "users",     icon: ICON.user,   cls: "blue",   num: s.users ?? 0,          label: "用户总数", hint: "点击查看用户管理" },
        { key: "users",     icon: ICON.shield, cls: "green",  num: s.admins ?? 0,         label: "管理员数", hint: "点击查看用户管理" },
        { key: "users",     icon: ICON.users,  cls: "orange", num: s.students ?? 0,       label: "学生数",   hint: "点击查看用户管理" },
        { key: "kb",        icon: ICON.folder, cls: "red",    num: s.knowledge_bases ?? 0, label: "知识库数量", hint: "点击查看知识库管理" },
        { key: "documents", icon: ICON.doc,    cls: "teal",   num: s.documents ?? 0,      label: "文档总数", hint: "点击查看全部文档" },
      ];
      grid.innerHTML = cards.map((c) => `
        <div class="stat-card clickable" data-key="${c.key}" title="${c.hint}">
          <div class="stat-info">
            <div class="stat-num">${c.num}</div>
            <div class="stat-label">${c.label}</div>
          </div>
          <div class="stat-icon ${c.cls}">${c.icon}</div>
        </div>`).join("");
      grid.querySelectorAll(".stat-card[data-key]").forEach((card) =>
        card.addEventListener("click", () => App.navigate(card.dataset.key)));

      renderTrend(s.daily_questions || []);
      renderDonut(s.kb_doc_distribution || []);
      renderFeedback(s);
      renderQuality(s);
      renderHotQuestions(s);
    } catch (err) {
      grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1"><p>${Common.escapeHtml(err.message)}</p></div>`;
    }
  }

  /* 概览：反馈区块（最近反馈列表） */
  function renderFeedback(s) {
    const box = document.getElementById("fb-card");
    const listEl = document.getElementById("fb-list");
    if (!box || !listEl) return;
    box.style.display = "";
    const recent = (s.recent_unanswered || []).slice(0, 5);

    const rows = recent.length
      ? recent.map((r) => `
          <div class="fb-row" data-fb="${r.id}">
            <span class="fb-status">${r.status === "pending" ? "待处理" : "已解答"}</span>
            <span class="fb-q" style="flex:1;min-width:0">${Common.escapeHtml(r.question)}</span>
            <span class="muted">${r.status === "resolved" && r.answered_at ? Common.fmtTime(r.answered_at) : Common.fmtTime(r.created_at)}</span>
          </div>`).join("")
      : `<div class="empty-state"><p>暂无反馈，答不上来的问题会自动收在这里</p></div>`;

    listEl.innerHTML = rows;
    listEl.querySelectorAll("[data-fb]").forEach((row) =>
      row.addEventListener("click", () => App.navigate("unanswered")));
    document.getElementById("fb-all").addEventListener("click", () => App.navigate("unanswered"));
  }

  /* 质量指标卡：命中率 / 拒答率 / 平均解决时长 / 评价分布 */
  function renderQuality(s) {
    const el = document.getElementById("quality-grid");
    if (!el) return;
    const hit = s.hit_rate != null ? (s.hit_rate * 100).toFixed(1) + "%" : "—";
    const reject = s.reject_rate != null ? (s.reject_rate * 100).toFixed(1) + "%" : "—";
    const resolve = s.avg_resolve_hours != null
      ? (s.avg_resolve_hours >= 24 ? (s.avg_resolve_hours / 24).toFixed(1) + " 天" : s.avg_resolve_hours + " 小时")
      : "—";
    const totalFb = (s.feedback_up || 0) + (s.feedback_down || 0);
    const upRate = totalFb > 0 ? Math.round((s.feedback_up / totalFb) * 100) : null;
    const totalQuestions = s.total_questions || 0;
    const rejectedAnswers = s.rejected_answers || 0;

    const items = [
      { label: "回答命中率", value: hit, hint: `${s.answered_answers || 0} / ${totalQuestions} 次提问`, cls: "green" },
      { label: "拒答率", value: reject, hint: `${rejectedAnswers} / ${totalQuestions} 次提问`, cls: "red" },
      { label: "平均解决时长", value: resolve, hint: "反馈从提交到解答", cls: "blue" },
      { label: "回答好评率", value: upRate != null ? upRate + "%" : "—", hint: `${s.feedback_up || 0} 赞 / ${totalFb} 次已评价`, cls: "orange" },
    ];
    el.innerHTML = items.map((it) => `
      <div class="quality-item">
        <div class="quality-value ${it.cls}">${it.value}</div>
        <div class="quality-meta">
          <div class="quality-label">${it.label}</div>
          <div class="quality-hint" title="${Common.escapeHtml(it.hint)}">${Common.escapeHtml(it.hint)}</div>
        </div>
      </div>`).join("");
  }

  /* 热门问题：学生被问得最多的问题（原问去重计数），反映高频困惑点 */
  function renderHotQuestions(s) {
    const list = document.getElementById("hot-list");
    if (!list) return;
    const hot = (s.hot_questions || []).slice(0, 5);
    if (!hot.length) {
      list.innerHTML = `<div class="empty-state"><p>暂无提问数据</p></div>`;
      return;
    }
    const maxCount = Math.max(1, ...hot.map((r) => r.count));
    list.innerHTML = hot.map((r, i) => `
      <div class="hot-row">
        <span class="hot-rank ${i < 3 ? "top" : ""}">${i + 1}</span>
        <span class="hot-q" title="${Common.escapeHtml(r.question)}">${Common.escapeHtml(r.question)}</span>
        <span class="hot-count">${r.count} 次</span>
      </div>`).join("");
  }

  /* 文档引用排行：按被回答引用的次数排序，附死资料列表 */
  async function loadDocUsage() {
    const list = document.getElementById("usage-list");
    if (!list) return;
    try {
      const d = await API.request("GET", "/api/stats/admin/doc-usage");
      const ranked = d.ranked || [];
      if (!ranked.length) {
        list.innerHTML = `<div class="empty-state"><p>暂无引用数据，回答产生后会统计到这里</p></div>`;
        return;
      }
      const top = ranked.slice(0, 5);
      const maxCount = Math.max(1, ...top.map((r) => r.count));
      let html = "";
      if (top.length) {
        html += top.map((r, i) => `
          <div class="usage-row">
            <span class="usage-rank ${i < 3 ? "top" : ""}">${i + 1}</span>
            <span class="usage-name" title="${Common.escapeHtml(r.title)}">${Common.escapeHtml(r.title)}</span>
            <span class="usage-bar"><span class="usage-bar-fill" style="width:${Math.round((r.count / maxCount) * 100)}%"></span></span>
            <span class="usage-count">${r.count}</span>
          </div>`).join("");
      } else {
        html += `<div class="muted" style="padding:8px 0 10px;font-size:13px">暂无文档被引用</div>`;
      }
      list.innerHTML = html;
    } catch (err) {
      list.innerHTML = `<div class="empty-state"><p style="color:var(--danger)">${Common.escapeHtml(err.message)}</p></div>`;
    }
  }

  /* 折线图（带面积填充） */
  function renderTrend(daily) {
    const el = document.getElementById("chart-trend");
    if (!el) return;
    // 没数据直接收工，避免空数组上取 x 抛异常导致整页白屏
    if (!daily.length) {
      el.innerHTML = `<div style="padding:40px 0;text-align:center;color:var(--text-3);font-size:13px">最近 7 天还没有提问数据</div>`;
      return;
    }
    const w = 640, h = 240, padL = 40, padR = 20, padT = 20, padB = 34;
    const max = Math.max(4, ...daily.map((d) => d.count));
    const innerW = w - padL - padR;
    const innerH = h - padT - padB;
    const step = innerW / Math.max(1, daily.length - 1);

    const points = daily.map((d, i) => {
      const x = padL + i * step;
      const y = padT + innerH - (d.count / max) * innerH;
      return { x, y, ...d };
    });

    // 网格线
    let grid = "";
    for (let g = 0; g <= 4; g++) {
      const gy = padT + (innerH / 4) * g;
      const val = Math.round(max - (max / 4) * g);
      grid += `<line x1="${padL}" y1="${gy}" x2="${w - padR}" y2="${gy}" stroke="var(--border)" stroke-width="1"/>`;
      grid += `<text x="${padL - 8}" y="${gy + 4}" text-anchor="end" font-size="12" fill="var(--text-3)">${val}</text>`;
    }

    // 折线 path
    const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
    // 面积 path
    const areaPath = linePath + ` L${points[points.length - 1].x.toFixed(1)},${padT + innerH} L${points[0].x.toFixed(1)},${padT + innerH} Z`;

    // 数据点 + x 轴标签
    let dots = "";
    let labels = "";
    const mobile = window.matchMedia("(max-width: 640px)").matches;
    points.forEach((p, i) => {
      dots += `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="4" fill="var(--card)" stroke="var(--primary)" stroke-width="2"><title>${p.date}：${p.count} 次</title></circle>`;
      if (!mobile || i % 2 === 0 || i === points.length - 1) {
        labels += `<text x="${p.x.toFixed(1)}" y="${h - 10}" text-anchor="middle" font-size="12" fill="var(--text-3)">${p.date.slice(5)}</text>`;
      }
    });

    el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:auto" role="img" aria-label="近7天提问趋势">
      <defs>
        <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="var(--primary)" stop-opacity="0.35"/>
          <stop offset="100%" stop-color="var(--primary)" stop-opacity="0.02"/>
        </linearGradient>
      </defs>
      ${grid}
      <path d="${areaPath}" fill="url(#trendFill)"/>
      <path d="${linePath}" fill="none" stroke="var(--primary)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      ${dots}
      ${labels}
    </svg>`;
  }

  /* 环形图（带右侧图例） */
  /* 环形图：只画文档数最多的前 3 个库，其余合并成「其他」，图例不会撑爆卡片 */
  function renderDonut(dist) {
    const el = document.getElementById("chart-donut");
    if (!el) return;
    dist = (dist || []).filter((d) => d.count > 0).sort((a, b) => b.count - a.count);
    if (!dist.length) {
      el.innerHTML = `<div class="empty-state"><p>暂无数据</p></div>`;
      return;
    }

    const MAX_SLICES = 3;
    let slices = dist;
    if (dist.length > MAX_SLICES) {
      const head = dist.slice(0, MAX_SLICES);
      const rest = dist.slice(MAX_SLICES);
      slices = head.concat([{
        name: `其他 ${rest.length} 个库`,
        count: rest.reduce((a, d) => a + d.count, 0),
      }]);
    }

    const colors = ["#17505e", "#b0762c", "#5d7a52", "#8a5a4a", "#4a6b8a", "#7a6a8a", "#6b675f"];
    // 除颜色外再叠一层纹理：色觉障碍用户靠纹理也能区分扇区
    const patterns = ["", "p-hatch", "p-dots", "p-cross", "p-hline", "p-vline", "p-diag"];
    const donutDefs = `<defs>
      <pattern id="p-hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
        <line x1="0" y1="0" x2="0" y2="8" stroke="rgba(255,255,255,.6)" stroke-width="2.2"/>
      </pattern>
      <pattern id="p-dots" width="8" height="8" patternUnits="userSpaceOnUse">
        <circle cx="4" cy="4" r="1.5" fill="rgba(255,255,255,.65)"/>
      </pattern>
      <pattern id="p-cross" width="8" height="8" patternUnits="userSpaceOnUse">
        <path d="M0 0 L8 8 M8 0 L0 8" stroke="rgba(255,255,255,.5)" stroke-width="1.3"/>
      </pattern>
      <pattern id="p-hline" width="8" height="8" patternUnits="userSpaceOnUse">
        <line x1="0" y1="4" x2="8" y2="4" stroke="rgba(255,255,255,.6)" stroke-width="2.2"/>
      </pattern>
      <pattern id="p-vline" width="8" height="8" patternUnits="userSpaceOnUse">
        <line x1="4" y1="0" x2="4" y2="8" stroke="rgba(255,255,255,.6)" stroke-width="2.2"/>
      </pattern>
      <pattern id="p-diag" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
        <line x1="0" y1="0" x2="0" y2="9" stroke="rgba(0,0,0,.22)" stroke-width="3.5"/>
      </pattern>
    </defs>`;
    const total = Math.max(1, slices.reduce((a, d) => a + d.count, 0));
    const cx = 90, cy = 90, r = 70, innerR = 44;

    let startAngle = -Math.PI / 2;
    let arcs = "";
    slices.forEach((d, i) => {
      const angle = (d.count / total) * Math.PI * 2;
      const endAngle = startAngle + angle;
      const largeArc = angle > Math.PI ? 1 : 0;
      const x1 = cx + r * Math.cos(startAngle);
      const y1 = cy + r * Math.sin(startAngle);
      const x2 = cx + r * Math.cos(endAngle);
      const y2 = cy + r * Math.sin(endAngle);
      const x3 = cx + innerR * Math.cos(endAngle);
      const y3 = cy + innerR * Math.sin(endAngle);
      const x4 = cx + innerR * Math.cos(startAngle);
      const y4 = cy + innerR * Math.sin(startAngle);
      const color = colors[i % colors.length];
      const arcPath = `M${x1.toFixed(1)},${y1.toFixed(1)} A${r},${r} 0 ${largeArc} 1 ${x2.toFixed(1)},${y2.toFixed(1)} L${x3.toFixed(1)},${y3.toFixed(1)} A${innerR},${innerR} 0 ${largeArc} 0 ${x4.toFixed(1)},${y4.toFixed(1)} Z`;
      arcs += `<path d="${arcPath}" fill="${color}"><title>${Common.escapeHtml(d.name)}：${d.count}</title></path>`;
      const pat = patterns[i % patterns.length];
      if (pat) arcs += `<path d="${arcPath}" fill="url(#${pat})" pointer-events="none"/>`;
      startAngle = endAngle;
    });

    const legend = slices.map((d, i) => {
      const pat = patterns[i % patterns.length];
      return `
      <div class="donut-legend-item">
        <svg class="donut-legend-dot" viewBox="0 0 10 10" aria-hidden="true">
          <rect width="10" height="10" rx="3" fill="${colors[i % colors.length]}"/>
          ${pat ? `<rect width="10" height="10" rx="3" fill="url(#${pat})"/>` : ""}
        </svg>
        <span style="flex:1">${Common.escapeHtml(d.name)}</span>
        <span style="color:var(--text-3);font-variant-numeric:tabular-nums">${d.count} · ${Math.round((d.count / total) * 100)}%</span>
      </div>`;
    }).join("");

    el.innerHTML = `<div class="donut-wrap">
      <svg viewBox="0 0 180 180" style="width:160px;height:160px" role="img" aria-label="各知识库文档占比">
        ${donutDefs}${arcs}
      </svg>
      <div class="donut-legend" style="flex:1">${legend}</div>
    </div>`;
  }

  /* ==================== 用户管理 ==================== */
  let usersRows = [];      // 当前页
  let usersTotal = 0;
  let usersPage = 1;
  let usersPageSize = 10;   // 可在 10 / 20 之间切换

  function renderUsers(container) {
    container.innerHTML = `
      <div class="page-toolbar">
        <div class="toolbar-left" style="gap:10px">
          <select class="form-control" id="users-field" style="width:110px" aria-label="搜索范围">
            <option value="account">账号</option>
            <option value="name">用户名</option>
            <option value="role">角色</option>
          </select>
          <div class="search-box" id="users-search-box">
            <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            <input class="form-control" id="users-search" placeholder="搜索账号">
            <button type="button" class="search-clear" id="users-search-clear" title="清空"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button>
          </div>
          <span class="list-count" id="users-count" style="margin-left:10px"></span>
        </div>
        <div class="toolbar-right">
          <button class="btn btn-primary" id="user-add"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14"/><path d="M5 12h14"/></svg>新增用户</button>
        </div>
      </div>
      <div class="card">
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>ID</th><th>账号</th><th>用户名</th><th>角色</th><th>提问数</th><th>创建时间</th><th>操作</th></tr></thead>
            <tbody id="users-tbody"><tr><td colspan="7" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr></tbody>
          </table>
        </div>
        <div class="pagination pagination-fixed" id="users-pagination"></div>
      </div>`;

    document.getElementById("user-add").addEventListener("click", () => openUserEditor(null));
    const input = document.getElementById("users-search");
    const syncClear = () => document.getElementById("users-search-box").classList.toggle("has-value", input.value.length > 0);
    const doUserSearch = () => { usersPage = 1; loadUsers(); };
    input.addEventListener("input", syncClear);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); doUserSearch(); }
    });
    Common.attachSearchButton(document.getElementById("users-search-box"), doUserSearch);
    document.getElementById("users-search-clear").addEventListener("click", () => {
      input.value = ""; syncClear(); doUserSearch(); input.focus();
    });
    const userPlaceholders = { account: "搜索账号", name: "搜索用户名", role: "搜索角色（学生/管理员）" };
    document.getElementById("users-field").addEventListener("change", (e) => {
      input.placeholder = userPlaceholders[e.target.value] || "搜索…";
      doUserSearch();
    });
    usersPage = 1;
    loadUsers();
  }

  async function loadUsers() {
    const tbody = document.getElementById("users-tbody");
    if (!tbody) return;
    const keyword = (document.getElementById("users-search")?.value || "").trim();
    const field = document.getElementById("users-field")?.value || "account";
    try {
      const params = new URLSearchParams({
        field,
        limit: String(usersPageSize),
        offset: String((usersPage - 1) * usersPageSize),
      });
      if (keyword) params.set("q", keyword);
      const data = await API.request("GET", "/api/admin/users?" + params.toString());
      usersRows = data.items || [];
      usersTotal = data.total || 0;
      const totalPages = Math.max(1, Math.ceil(usersTotal / usersPageSize));
      if (usersPage > totalPages) { usersPage = totalPages; return loadUsers(); }
      renderUsersTable();
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:36px;color:var(--danger)">${Common.escapeHtml(err.message)}</td></tr>`;
    }
  }

  function renderUsersTable() {
    const tbody = document.getElementById("users-tbody");
    if (!tbody) return;
    const keyword = (document.getElementById("users-search")?.value || "").trim();
    const countEl = document.getElementById("users-count");
    if (countEl) countEl.textContent = keyword ? `匹配 ${usersTotal} 条` : "";
    const me = API.getUser();

    if (!usersRows.length) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:36px;color:var(--text-3)">${keyword ? "没有匹配的用户" : "暂无用户"}</td></tr>`;
    } else {
      const iAmSystem = me && me.account === SYSTEM_ADMIN_ACCOUNT;
      tbody.innerHTML = usersRows.map((u, i) => {
        const isSystem = u.account === SYSTEM_ADMIN_ACCOUNT;
        const canEdit = !isSystem || iAmSystem;
        const badge = isSystem
          ? ""
          : (u.id === me.id ? '<span class="badge badge-student">我</span>' : "");
        return `
        <tr>
          <td data-label="序号">${(usersPage - 1) * usersPageSize + i + 1}</td>
          <td data-label="账号">${Common.escapeHtml(u.account)} ${badge}</td>
          <td data-label="用户名">${Common.escapeHtml(u.username || "-")}</td>
          <td data-label="角色"><span class="role-badge ${isSystem ? "system-admin" : u.role}">${isSystem ? "系统管理员" : (u.role === "admin" ? "管理员" : "学生")}</span></td>
          <td data-label="提问数" class="muted">${u.question_count || 0}</td>
          <td data-label="创建时间" class="muted">${Common.fmtTime(u.created_at)}</td>
          <td data-label="操作">
            ${canEdit ? `<button class="ops-link edit" data-edit="${u.id}">编辑</button>` : ""}
            ${u.id !== me.id && !isSystem ? `<button class="ops-link del" data-del="${u.id}">删除</button>` : ""}
          </td>
        </tr>`;
      }).join("");
    }

    tbody.querySelectorAll("[data-edit]").forEach((b) =>
      b.addEventListener("click", () => openUserEditor(usersRows.find((u) => u.id === Number(b.dataset.edit)))));
    tbody.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () => removeUser(usersRows.find((u) => u.id === Number(b.dataset.del)))));

    Common.renderPagination("users-pagination", usersTotal, usersPage, usersPageSize, (p) => {
      usersPage = p; loadUsers();
    }, {
      sizeOptions: [10, 20],
      onSizeChange: (s) => { usersPageSize = s; usersPage = 1; loadUsers(); },
    });
  }

  function openUserEditor(user) {
    const isEdit = !!user;
    const me = API.getUser();
    const isSystem = isEdit && user.account === SYSTEM_ADMIN_ACCOUNT;
    if (isSystem && (!me || me.account !== SYSTEM_ADMIN_ACCOUNT)) {
      return Common.toast("系统管理员账号仅限本人修改", "error");
    }
    Common.openModal(`
      <div class="modal-header">
        <h3>${isEdit ? "编辑用户" : "新增用户"}</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      ${!isEdit ? `
      <div class="form-group">
        <label>账号</label>
        <input class="form-control" id="u-account" placeholder="请输入账号" autocomplete="off">
      </div>` : ""}
      <div class="form-group">
        <label>用户名</label>
        <input class="form-control" id="u-name" value="${isEdit ? Common.escapeHtml(user.username || "") : ""}" placeholder="请输入用户名">
      </div>
      <div class="form-group">
        <label>角色</label>
        ${isSystem
          ? `<input class="form-control" id="u-role" value="系统管理员" readonly aria-readonly="true">`
          : `<select class="form-control" id="u-role">
              <option value="student" ${isEdit && user.role === "student" ? "selected" : ""}>学生</option>
              <option value="admin" ${isEdit && user.role === "admin" ? "selected" : ""}>管理员</option>
            </select>`}
      </div>
      <div class="form-group">
        <label>${isEdit ? "重置密码" : "初始密码"}</label>
        <input class="form-control" id="u-pwd" type="password" placeholder="请输入密码" autocomplete="new-password">
      </div>
      <div class="modal-footer">
        <button class="btn" data-close>取消</button>
        <button class="btn btn-primary" id="u-save">${isEdit ? "保存" : "创建"}</button>
      </div>
    `, { onOpen: (box) => {
      box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));
      box.querySelector("#u-save").addEventListener("click", async () => {
        const btn = box.querySelector("#u-save");
        const payload = {
          username: box.querySelector("#u-name").value.trim(),
          role: isSystem ? "admin" : box.querySelector("#u-role").value,
        };
        const pwd = box.querySelector("#u-pwd").value;
        if (pwd) payload.new_password = pwd;
        // 新建时的必填校验放在禁用按钮之前，避免提前 return 把按钮卡在禁用态
        let account = "";
        if (!isEdit) {
          account = box.querySelector("#u-account").value.trim();
          if (!account) return Common.toast("请输入账号", "error");
          if (!pwd) return Common.toast("请输入初始密码", "error");
        }
        btn.disabled = true;
        btn.textContent = "保存中…";
        try {
          if (isEdit) {
            await API.request("PATCH", `/api/admin/users/${user.id}`, payload);
          } else {
            await API.request("POST", "/api/admin/users", {
              account, password: pwd, username: payload.username, role: payload.role,
            });
          }
          Common.toast(isEdit ? "已保存" : "已创建", "success");
          Common.closeModal();
          if (!isEdit) usersPage = 1;
          loadUsers();
        } catch (err) {
          Common.toast(err.message, "error");
          btn.disabled = false;
          btn.textContent = isEdit ? "保存" : "创建";
        }
      });
    }});
  }

  async function removeUser(user) {
    if (!user) return;
    if (user.account === SYSTEM_ADMIN_ACCOUNT) {
      return Common.toast("系统管理员账号不能删除", "error");
    }
    // 名下有知识库：必须先转移
    let owned = [];
    try {
      const bases = await API.request("GET", "/api/knowledge/bases");
      owned = (bases || []).filter((b) => Number(b.created_by) === Number(user.id));
    } catch { /* 查询失败按无库处理，后端还会再拦一次 */ }
    if (owned.length) {
      await openTransferModal(user, owned);
      return;
    }
    const ok = await Common.confirmDialog({
      title: "删除用户",
      message: `将删除用户「${Common.escapeHtml(user.username || user.account)}」及其全部会话记录，不可恢复。确认？`,
      okText: "删除",
    });
    if (!ok) return;
    try {
      await API.request("DELETE", `/api/admin/users/${user.id}`);
      Common.toast("已删除", "success");
      loadUsers();
    } catch (err) { Common.toast(err.message, "error"); }
  }

  async function openTransferModal(user, owned) {
    let others = [];
    try {
      others = await API.request("GET", "/api/admin/users/options");
    } catch (err) {
      Common.toast("加载接收人失败：" + err.message, "error");
      return;
    }
    others = (others || []).filter((u) => Number(u.id) !== Number(user.id));
    if (!others.length) {
      Common.toast("没有可接收知识库的其他用户", "error");
      return;
    }
    const me = API.getUser();
    const defaultTo = others.find((u) => me && Number(u.id) === Number(me.id)) || others[0];
    const options = others.map((u) => `
      <option value="${u.id}" ${defaultTo && u.id === defaultTo.id ? "selected" : ""}>
        ${Common.escapeHtml(u.username || u.account)}（${u.role === "admin" ? "管理员" : "学生"}）
      </option>`).join("");
    Common.openModal(`
      <div class="modal-header">
        <h3>转移知识库后删除用户</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      <p style="margin:0 0 12px;line-height:1.7">
        「${Common.escapeHtml(user.username || user.account)}」名下还有
        <strong>${owned.length}</strong> 个知识库：
        ${owned.map((b) => Common.escapeHtml(b.name)).join("、")}
      </p>
      <p class="muted" style="margin:0 0 12px;font-size:13px">
        直接删除会留下无主知识库，学生端看不到这些资料。请先指定接收人。
      </p>
      <div class="form-group">
        <label>知识库接收人</label>
        <select class="form-control" id="transfer-to">${options}</select>
      </div>
      <div class="modal-footer">
        <button class="btn" data-close>取消</button>
        <button class="btn btn-danger" id="transfer-delete">转移并删除</button>
      </div>
    `, { onOpen: (box) => {
      box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));
      box.querySelector("#transfer-delete").addEventListener("click", async () => {
        const to = box.querySelector("#transfer-to").value;
        const btn = box.querySelector("#transfer-delete");
        btn.disabled = true; btn.textContent = "处理中…";
        try {
          await API.request("DELETE", `/api/admin/users/${user.id}?transfer_to=${encodeURIComponent(to)}`);
          Common.toast("已转移知识库并删除用户", "success");
          Common.closeModal();
          loadUsers();
        } catch (err) {
          Common.toast(err.message, "error");
          btn.disabled = false; btn.textContent = "转移并删除";
        }
      });
    }});
  }

  /* ==================== 知识库管理（表格） ==================== */
  let kbRows = [];     // 当前页
  let kbTotal = 0;
  let kbPage = 1;
  let kbPageSize = 10;   // 可在 10 / 20 之间切换
  let kbTimer = null;    // 30s 静默刷新：感知学生端/他端对知识库与知识点的增删，保持两端同步

  function stopKbTimer() {
    if (kbTimer) { clearInterval(kbTimer); kbTimer = null; }
  }

  function kbQueryParams() {
    const keyword = (document.getElementById("kb-search")?.value || "").trim();
    const field = document.getElementById("kb-field")?.value || "name";
    const params = new URLSearchParams({
      field,
      sort: "id",
      limit: String(kbPageSize),
      offset: String((kbPage - 1) * kbPageSize),
    });
    if (keyword) params.set("keyword", keyword);
    return params.toString();
  }

  async function silentRefreshKb() {
    try {
      const data = await API.request("GET", "/api/knowledge/bases/page?" + kbQueryParams());
      kbRows = data.items || [];
      kbTotal = data.total || 0;
      const totalPages = Math.max(1, Math.ceil(kbTotal / kbPageSize));
      if (kbPage > totalPages) { kbPage = totalPages; return loadKbTable(); }
      renderKbTableRows();   // 保留当前搜索词与页码，静默更新
    } catch { /* 静默：网络异常不影响当前页面 */ }
  }

  function renderKbTable(container) {
    stopKbTimer();
    container.innerHTML = `
      <div class="page-toolbar">
        <div class="toolbar-left" style="gap:10px">
          <select class="form-control" id="kb-field" style="width:120px" aria-label="搜索范围">
            <option value="name">名称</option>
            <option value="description">描述</option>
            <option value="creator">创建者</option>
          </select>
          <div class="search-box" id="kb-search-box">
            <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            <input class="form-control" id="kb-search" placeholder="搜索知识库名称">
            <button type="button" class="search-clear" id="kb-search-clear" title="清空"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button>
          </div>
        </div>
        <div class="toolbar-right">
          <button class="btn btn-primary" id="kb-add"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14"/><path d="M5 12h14"/></svg>新增知识库</button>
        </div>
      </div>
      <div class="card">
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>ID</th><th>知识库名称</th><th>描述</th><th>文档数</th><th>分块数</th><th>创建者</th><th>创建时间</th><th>操作</th></tr></thead>
            <tbody id="kb-tbody"><tr><td colspan="8" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr></tbody>
          </table>
        </div>
        <div class="pagination pagination-fixed" id="kb-pagination"></div>
      </div>`;

    document.getElementById("kb-add").addEventListener("click", () => openKbEditor(null));
    const input = document.getElementById("kb-search");
    const box = document.getElementById("kb-search-box");
    const syncClear = () => box.classList.toggle("has-value", input.value.length > 0);
    const doKbSearch = () => { kbPage = 1; loadKbTable(); };
    input.addEventListener("input", syncClear);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); doKbSearch(); }
    });
    Common.attachSearchButton(box, doKbSearch);
    document.getElementById("kb-search-clear").addEventListener("click", () => {
      input.value = ""; syncClear(); doKbSearch(); input.focus();
    });
    const kbPlaceholders = { name: "搜索知识库名称", description: "搜索描述", creator: "搜索创建者" };
    document.getElementById("kb-field").addEventListener("change", (e) => {
      input.placeholder = kbPlaceholders[e.target.value] || "搜索…";
      doKbSearch();
    });
    kbPage = 1;
    loadKbTable();
    // 启动 30s 静默同步（仅在停留在本页时运行）
    kbTimer = setInterval(silentRefreshKb, 30000);
  }

  async function loadKbTable() {
    const tbody = document.getElementById("kb-tbody");
    if (!tbody) return;
    try {
      const data = await API.request("GET", "/api/knowledge/bases/page?" + kbQueryParams());
      kbRows = data.items || [];
      kbTotal = data.total || 0;
      const totalPages = Math.max(1, Math.ceil(kbTotal / kbPageSize));
      if (kbPage > totalPages) { kbPage = totalPages; return loadKbTable(); }
      renderKbTableRows();
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:36px;color:var(--danger)">${Common.escapeHtml(err.message)}</td></tr>`;
    }
  }

  function renderKbTableRows() {
    const tbody = document.getElementById("kb-tbody");
    if (!tbody) return;
    const keyword = (document.getElementById("kb-search")?.value || "").trim();

    if (!kbRows.length) {
      tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:36px;color:var(--text-3)">${keyword ? "没有匹配的知识库" : "暂无知识库"}</td></tr>`;
    } else {
      tbody.innerHTML = kbRows.map((b, i) => `
        <tr>
          <td data-label="序号">${(kbPage - 1) * kbPageSize + i + 1}</td>
          <td data-label="知识库">${Common.escapeHtml(b.name)}</td>
          <td data-label="简介" style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${Common.escapeHtml(b.description || "")}">${Common.escapeHtml(b.description || "-")}</td>
          <td data-label="文档数">${b.document_count || 0}</td>
          <td data-label="分块数">${b.chunk_count || 0}</td>
          <td data-label="创建者">${Common.escapeHtml(b.creator_name || "-")}</td>
          <td data-label="创建时间" class="muted">${Common.fmtTime(b.created_at)}</td>
          <td data-label="操作">
            <button class="ops-link edit" data-edit="${b.id}">编辑</button>
            <button class="ops-link del" data-del="${b.id}">删除</button>
          </td>
        </tr>`).join("");
    }

    tbody.querySelectorAll("[data-edit]").forEach((b) =>
      b.addEventListener("click", () => openKbEditor(kbRows.find((x) => x.id === Number(b.dataset.edit)))));
    tbody.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () => removeKb(kbRows.find((x) => x.id === Number(b.dataset.del)))));

    Common.renderPagination("kb-pagination", kbTotal, kbPage, kbPageSize, (p) => {
      kbPage = p; loadKbTable();
    }, {
      sizeOptions: [10, 20],
      onSizeChange: (s) => { kbPageSize = s; kbPage = 1; loadKbTable(); },
    });
  }

  /* 新增/编辑知识库：编辑态里直接能上传文档 */
  function openKbEditor(base) {
    const isEdit = !!base;
    Common.openModal(`
      <div class="modal-header">
        <h3>${isEdit ? "编辑知识库" : "新增知识库"}</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      <div class="form-group">
        <label>知识库名称</label>
        <input class="form-control" id="kb-name" maxlength="128" value="${isEdit ? Common.escapeHtml(base.name) : ""}" placeholder="请输入知识库名称">
      </div>
      <div class="form-group">
        <label>描述</label>
        <textarea class="form-control" id="kb-desc" rows="3" maxlength="1000" placeholder="简要说明该知识库涵盖的内容…">${isEdit ? Common.escapeHtml(base.description || "") : ""}</textarea>
      </div>
      ${isEdit ? `
      <div class="p-divider"></div>
      <div class="form-group">
        <label>上传文档 <span class="muted">（pdf / txt / md / csv，单个 20MB 以内）</span></label>
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <button type="button" class="btn" id="kb-up-pick">选择文件</button>
          <button type="button" class="btn" id="kb-up-submit" disabled>上传文档</button>
          <span class="muted" id="kb-up-files" style="font-size:13px">未选择文件</span>
        </div>
        <input type="file" id="kb-up-input" accept=".pdf,.txt,.md,.markdown,.csv" multiple style="display:none">
        <div class="form-hint">上传后自动解析入库，并生成对应的展示知识点。</div>
      </div>` : ""}
      <div class="modal-footer">
        <button class="btn" data-close>取消</button>
        <button class="btn btn-primary" id="kb-save">${isEdit ? "保存" : "创建"}</button>
      </div>
    `, { onOpen: (box) => {
      box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));

      // 编辑态：文件选择 + 上传
      if (isEdit) {
        const input = box.querySelector("#kb-up-input");
        const label = box.querySelector("#kb-up-files");
        const submit = box.querySelector("#kb-up-submit");
        box.querySelector("#kb-up-pick").addEventListener("click", () => input.click());
        input.addEventListener("change", () => {
          const files = Array.from(input.files || []);
          label.textContent = files.length
            ? `已选 ${files.length} 个：${files.map((f) => f.name).join("、")}`
            : "未选择文件";
          submit.disabled = files.length === 0;
        });
        submit.addEventListener("click", async () => {
          const files = Array.from(input.files || []);
          if (!files.length) return;
          // 用了改名但还没保存时，按输入框里的名字归档
          const courseName = box.querySelector("#kb-name").value.trim() || base.name;
          const ok = await uploadDocsToBase({ ...base, name: courseName }, files, submit);
          if (ok) {
            Common.closeModal();
            loadKbTable();
            setTimeout(silentRefreshKb, 1500);
          }
        });
      }

      box.querySelector("#kb-save").addEventListener("click", async () => {
        const name = box.querySelector("#kb-name").value.trim();
        if (!name) return Common.toast("请输入知识库名称", "error");
        const description = box.querySelector("#kb-desc").value.trim();
        const btn = box.querySelector("#kb-save");
        btn.disabled = true;
        btn.textContent = isEdit ? "保存中…" : "创建中…";
        try {
          if (isEdit) {
            await API.request("PUT", `/api/knowledge/bases/${base.id}`, { name, description });
            Common.toast("已保存", "success");
            Common.closeModal();
            loadKbTable();
            return;
          }
          const created = await API.request("POST", "/api/knowledge/bases", { name, description });
          Common.toast("已创建，可以接着上传文档", "success");
          Common.closeModal();
          kbPage = 1;
          loadKbTable();
          // 直接打开刚建好的库，接着上传文档
          setTimeout(() => openKbEditor(created), 120);
        } catch (err) {
          Common.toast(err.message, "error");
          btn.disabled = false;
          btn.textContent = isEdit ? "保存" : "创建";
        }
      });
    }});
  }

  /* 往某个知识库上传文档（编辑弹窗里的「上传文档」用它） */
  async function uploadDocsToBase(base, files, btn) {
    const list = files.filter((f) => /\.(pdf|txt|md|markdown|csv)$/i.test(f.name));
    if (!list.length) {
      Common.toast("请选择 pdf / txt / md / csv 文件", "error");
      return 0;
    }
    if (btn) { btn.disabled = true; btn.textContent = "上传中…"; }
    let ok = 0;
    for (const file of list) {
      const form = new FormData();
      form.append("file", file);
      form.append("course", base.name);
      try {
        await API.request("POST", "/api/documents/upload", form, { form: true });
        ok += 1;
      } catch (err) {
        Common.toast(`${file.name}：${err.message}`, "error");
      }
    }
    if (btn) { btn.disabled = false; btn.textContent = "上传文档"; }
    if (ok) Common.toast(`已提交 ${ok} 个文件，正在解析入库…`, "success");
    return ok;
  }

  async function removeKb(base) {
    if (!base) return;
    const ok = await Common.confirmDialog({
      title: "删除知识库",
      message: `将删除知识库「${Common.escapeHtml(base.name)}」及其下 ${base.document_count || 0} 个文档、${base.point_count || 0} 个知识点与向量索引，删除后进入回收站，30 天内可恢复。确认？`,
      okText: "删除",
    });
    if (!ok) return;
    try {
      await API.request("DELETE", `/api/knowledge/bases/${base.id}`);
      Common.toast("已删除", "success");
      loadKbTable();
    } catch (err) { Common.toast(err.message, "error"); }
  }

  /* ==================== 对话历史（全部会话） ==================== */
  let convRows = [];      // 当前页
  let convTotal = 0;
  let convPage = 1;
  let convPageSize = 10;   // 可在 10 / 20 之间切换

  function renderMessages(container, opts) {
    container.innerHTML = `
      <div class="page-toolbar">
        <div class="toolbar-left" style="gap:10px">
          <select class="form-control" id="conv-field" style="width:110px" aria-label="搜索范围">
            <option value="title">会话标题</option>
            <option value="user">提问者</option>
          </select>
          <div class="search-box" id="conv-search-box">
            <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            <input class="form-control" id="conv-search" placeholder="搜索会话标题">
            <button type="button" class="search-clear" id="conv-search-clear" title="清空"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="row-between mb-16">
          <b>问答会话</b>
          <button class="btn btn-sm" id="conv-refresh"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 6"/><path d="M20 4v7h-7"/></svg>刷新</button>
        </div>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>ID</th><th>会话标题</th><th>消息数</th><th>提问者</th><th>创建时间</th><th>操作</th></tr></thead>
            <tbody id="conv-tbody"><tr><td colspan="6" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr></tbody>
          </table>
        </div>
        <div class="pagination pagination-fixed" id="conv-pagination"></div>
      </div>`;

    const input = document.getElementById("conv-search");
    const box = document.getElementById("conv-search-box");
    const syncClear = () => box.classList.toggle("has-value", input.value.length > 0);
    const rerun = () => { convPage = 1; loadConversations(false); };
    input.addEventListener("input", syncClear);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); rerun(); }
    });
    Common.attachSearchButton(box, rerun);
    document.getElementById("conv-search-clear").addEventListener("click", () => {
      input.value = ""; syncClear(); rerun(); input.focus();
    });
    const placeholders = { title: "搜索会话标题", user: "搜索提问者" };
    document.getElementById("conv-field").addEventListener("change", (e) => {
      input.placeholder = placeholders[e.target.value] || "搜索…";
      rerun();
    });
    document.getElementById("conv-refresh").addEventListener("click", () => {
      loadConversations(true).then((ok) =>
        Common.toast(ok ? "已刷新" : "刷新失败，检查一下网络", ok ? "success" : "error"));
    });

    // 从「用户管理」点“看提问”跳过来时，带上搜索条件
    if (opts && opts.search) {
      const fieldSel = document.getElementById("conv-field");
      if (opts.field) fieldSel.value = opts.field;
      input.value = opts.search;
      input.placeholder = placeholders[fieldSel.value] || "搜索…";
      syncClear();
      convPage = 1;
    }

    loadConversations(false);
  }

  /* keepPage=true：刷新/翻页时保留当前页；换了搜索条件则回到第 1 页 */
  async function loadConversations(keepPage) {
    const tbody = document.getElementById("conv-tbody");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr>`;
    if (!keepPage) convPage = 1;
    const params = new URLSearchParams({
      limit: String(convPageSize),
      offset: String((convPage - 1) * convPageSize),
      field: document.getElementById("conv-field")?.value || "title",
    });
    const keyword = (document.getElementById("conv-search")?.value || "").trim();
    if (keyword) params.set("q", keyword);
    try {
      const data = await API.request("GET", `/api/chat/admin/conversations?${params.toString()}`);
      convRows = data.items || [];
      convTotal = data.total || 0;
      renderConvTable();
      return true;
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:36px;color:var(--danger)">${Common.escapeHtml(err.message)}</td></tr>`;
      Common.renderPagination("conv-pagination", 0, 1, convPageSize, () => {}, {});
      return false;
    }
  }

  function renderConvTable() {
    const tbody = document.getElementById("conv-tbody");
    if (!tbody) return;
    const totalPages = Math.max(1, Math.ceil(convTotal / convPageSize));
    if (convPage > totalPages) { convPage = totalPages; return loadConversations(true); }
    const start = (convPage - 1) * convPageSize;
    const keyword = (document.getElementById("conv-search")?.value || "").trim();

    if (!convRows.length) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:36px;color:var(--text-3)">${keyword ? "没有匹配的会话" : "暂无会话"}</td></tr>`;
    } else {
      // 编号 = 当前位置（不是数据库 id）：删掉一条后重新拉取即自动重排
      tbody.innerHTML = convRows.map((c, i) => `
        <tr>
          <td data-label="序号">${start + i + 1}</td>
          <td data-label="会话标题" title="${Common.escapeHtml(c.title)}" style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${Common.escapeHtml(c.title)}</td>
          <td data-label="消息数" class="muted">${c.message_count || 0}</td>
          <td data-label="提问者">${Common.escapeHtml(c.username || c.account || "-")}</td>
          <td data-label="创建时间" class="muted">${Common.fmtTime(c.created_at)}</td>
          <td data-label="操作">
            <button class="ops-link edit" data-view="${c.id}">查看</button>
            <button class="ops-link del" data-del="${c.id}">删除</button>
          </td>
        </tr>`).join("");
    }

    tbody.querySelectorAll("[data-view]").forEach((b) =>
      b.addEventListener("click", () => {
        const idx = convRows.findIndex((c) => c.id === Number(b.dataset.view));
        viewConversation(Number(b.dataset.view), start + idx + 1);
      }));
    tbody.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () => {
        const row = convRows.find((c) => c.id === Number(b.dataset.del));
        removeConversation(Number(b.dataset.del), row ? row.title : "");
      }));

    Common.renderPagination("conv-pagination", convTotal, convPage, convPageSize, (p) => {
      convPage = p;
      loadConversations(true);
    }, {
      sizeOptions: [10, 20],
      onSizeChange: (s) => { convPageSize = s; convPage = 1; loadConversations(false); },
    });
  }

  async function viewConversation(id, no) {
    try {
      const msgs = await API.request("GET", `/api/chat/admin/conversations/${id}/messages`);
      Common.openModal(`
        <div class="modal-header">
          <h3>会话 #${no} · 共 ${msgs.length} 条消息</h3>
          <button class="modal-close" data-close>×</button>
        </div>
        <div style="max-height:56vh;overflow-y:auto;display:flex;flex-direction:column;gap:10px">
          ${msgs.length ? msgs.map((m) => `
            <div class="msg-row ${m.role === "user" ? "" : "msg-assistant"}">
              <div class="msg-role">${m.role === "user" ? "提问" : "助教"}</div>
              <div class="${m.role === "assistant" ? "msg-content md-body" : "msg-content"}">${m.role === "assistant" ? Markdown.render(m.content) : Common.escapeHtml(m.content)}</div>
              <div class="muted" style="font-size:12px;margin-top:6px">${Common.fmtTime(m.created_at)}${m.interrupted ? " · 生成被中断" : ""}</div>
            </div>`).join("") : `<div class="empty-state"><p>该会话暂无消息</p></div>`}
        </div>
        <div class="modal-footer"><button class="btn" data-close>关闭</button></div>
      `, { size: "modal-lg", onOpen: (box) => {
        box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));
      }});
    } catch (err) { Common.toast(err.message, "error"); }
  }

  async function removeConversation(id, title) {
    const ok = await Common.confirmDialog({
      title: "删除会话",
      message: `将删除会话「${Common.escapeHtml(title || "#" + id)}」及其全部消息，不可恢复。确认？`,
      okText: "删除",
    });
    if (!ok) return;
    try {
      await API.request("DELETE", `/api/chat/admin/conversations/${id}`);
      Common.toast("已删除", "success");
      loadConversations(true);
    } catch (err) { Common.toast(err.message, "error"); }
  }

  /* ==================== 分派 ==================== */
  function render(container, sub, opts) {
    stopKbTimer();   // 离开知识库管理即停止轮询
    if (sub === "users") renderUsers(container);
    else if (sub === "overview") renderOverview(container);
    else if (sub === "kb_table") renderKbTable(container);
    else if (sub === "conversations") renderMessages(container, opts);
    else if (sub === "unanswered") AdminFeedbackView.render(container);
  }

  return { render, destroy: stopKbTimer };
})();
