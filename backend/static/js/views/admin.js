/* ============ 视图：管理员（数据概览 / 用户管理 / 知识库管理 / 对话历史） ============ */
const AdminView = (() => {
  /* SVG 图标库（线性，stroke=currentColor） */
  const ICON = {
    user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
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
      <div class="card mt-16" id="fb-card" style="display:none">
        <div class="row-between mb-16">
          <b>问题反馈</b>
          <button class="btn btn-sm" id="fb-all">查看全部</button>
        </div>
        <div id="fb-list"></div>
      </div>`;
    loadStats();
  }

  async function loadStats() {
    const grid = document.getElementById("stat-grid");
    try {
      const s = await API.request("GET", "/api/stats/admin/stats");
      const cards = [
        { key: "users",     icon: ICON.user,   cls: "blue",   num: s.users,              label: "用户总数" },
        { key: "kb",        icon: ICON.folder, cls: "green",  num: s.knowledge_bases,     label: "知识库数量" },
        { key: "documents", icon: ICON.doc,    cls: "orange", num: s.documents,           label: "文档总数" },
        { key: "history",   icon: ICON.chat,   cls: "red",    num: s.today_conversations, label: "今日提问" },
      ];
      grid.innerHTML = cards.map((c) => `
        <div class="stat-card clickable" data-key="${c.key}" title="点击查看${c.label}">
          <div class="stat-info">
            <div class="stat-num">${c.num}</div>
            <div class="stat-label">${c.label}</div>
          </div>
          <div class="stat-icon ${c.cls}">${c.icon}</div>
        </div>`).join("");
      grid.querySelectorAll(".stat-card[data-key]").forEach((card) =>
        card.addEventListener("click", () => App.navigate(card.dataset.key)));

      renderTrend(s.daily_conversations || []);
      renderDonut(s.kb_doc_distribution || []);
      renderFeedback(s);
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
    const recent = s.recent_unanswered || [];

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

  /* 折线图（带面积填充） */
  function renderTrend(daily) {
    const el = document.getElementById("chart-trend");
    if (!el) return;
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
      grid += `<line x1="${padL}" y1="${gy}" x2="${w - padR}" y2="${gy}" stroke="#e2e8f0" stroke-width="1"/>`;
      grid += `<text x="${padL - 8}" y="${gy + 4}" text-anchor="end" font-size="11" fill="#94a3b8">${val}</text>`;
    }

    // 折线 path
    const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
    // 面积 path
    const areaPath = linePath + ` L${points[points.length - 1].x.toFixed(1)},${padT + innerH} L${points[0].x.toFixed(1)},${padT + innerH} Z`;

    // 数据点 + x 轴标签
    let dots = "";
    let labels = "";
    points.forEach((p, i) => {
      dots += `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="4" fill="#fff" stroke="#3b82f6" stroke-width="2"><title>${p.date}：${p.count} 次</title></circle>`;
      labels += `<text x="${p.x.toFixed(1)}" y="${h - 10}" text-anchor="middle" font-size="11" fill="#94a3b8">${p.date.slice(5)}</text>`;
    });

    el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:auto" role="img" aria-label="近7天提问趋势">
      <defs>
        <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#3b82f6" stop-opacity="0.35"/>
          <stop offset="100%" stop-color="#3b82f6" stop-opacity="0.02"/>
        </linearGradient>
      </defs>
      ${grid}
      <path d="${areaPath}" fill="url(#trendFill)"/>
      <path d="${linePath}" fill="none" stroke="#3b82f6" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      ${dots}
      ${labels}
    </svg>`;
  }

  /* 环形图（带右侧图例） */
  function renderDonut(dist) {
    const el = document.getElementById("chart-donut");
    if (!el) return;
    if (!dist.length || dist.reduce((a, d) => a + d.count, 0) === 0) {
      el.innerHTML = `<div class="empty-state"><p>暂无数据</p></div>`;
      return;
    }
    const colors = ["#3b82f6", "#a3e635", "#64748b", "#f59e0b", "#ef4444", "#06b6d4", "#8b5cf6"];
    const total = Math.max(1, dist.reduce((a, d) => a + d.count, 0));
    const cx = 90, cy = 90, r = 70, innerR = 44;

    let startAngle = -Math.PI / 2;
    let arcs = "";
    dist.forEach((d, i) => {
      const frac = d.count / total;
      const angle = frac * Math.PI * 2;
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
      arcs += `<path d="M${x1.toFixed(1)},${y1.toFixed(1)} A${r},${r} 0 ${largeArc} 1 ${x2.toFixed(1)},${y2.toFixed(1)} L${x3.toFixed(1)},${y3.toFixed(1)} A${innerR},${innerR} 0 ${largeArc} 0 ${x4.toFixed(1)},${y4.toFixed(1)} Z" fill="${color}"><title>${d.name}：${d.count}</title></path>`;
      startAngle = endAngle;
    });

    const legend = dist.map((d, i) => `
      <div class="donut-legend-item">
        <span class="donut-legend-dot" style="background:${colors[i % colors.length]}"></span>
        <span style="flex:1">${Common.escapeHtml(d.name)}</span>
        <span style="color:var(--text-3)">${d.count}</span>
      </div>`).join("");

    el.innerHTML = `<div class="donut-wrap">
      <svg viewBox="0 0 180 180" style="width:160px;height:160px">${arcs}</svg>
      <div class="donut-legend" style="flex:1">${legend}</div>
    </div>`;
  }

  /* ==================== 用户管理 ==================== */
  let usersCache = [];
  let usersPage = 1;
  let usersPageSize = 10;   // 可在 10 / 20 之间切换

  function renderUsers(container) {
    container.innerHTML = `
      <div class="page-toolbar">
        <div class="toolbar-left">
          <div class="search-box" id="users-search-box">
            <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            <input class="form-control" id="users-search" placeholder="搜索账号或用户名">
            <button type="button" class="search-clear" id="users-search-clear" title="清空">✕</button>
          </div>
          <span class="list-count" id="users-count" style="margin-left:10px"></span>
        </div>
        <div class="toolbar-right">
          <button class="btn btn-light-blue" id="user-add">＋ 新增用户</button>
        </div>
      </div>
      <div class="card">
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>ID</th><th>账号</th><th>用户名</th><th>角色</th><th>创建时间</th><th>操作</th></tr></thead>
            <tbody id="users-tbody"><tr><td colspan="6" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr></tbody>
          </table>
        </div>
        <div class="pagination pagination-fixed" id="users-pagination"></div>
      </div>`;

    document.getElementById("user-add").addEventListener("click", () => openUserEditor(null));
    const input = document.getElementById("users-search");
    const syncClear = () => document.getElementById("users-search-box").classList.toggle("has-value", input.value.length > 0);
    input.addEventListener("input", () => { usersPage = 1; renderUsersTable(); syncClear(); });
    document.getElementById("users-search-clear").addEventListener("click", () => {
      input.value = ""; usersPage = 1; renderUsersTable(); syncClear(); input.focus();
    });
    loadUsers();
  }

  async function loadUsers() {
    const tbody = document.getElementById("users-tbody");
    if (!tbody) return;
    try {
      usersCache = (await API.request("GET", "/api/admin/users") || []).slice().sort((a, b) => Number(a.id) - Number(b.id));
      renderUsersTable();
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:36px;color:var(--danger)">${Common.escapeHtml(err.message)}</td></tr>`;
    }
  }

  function renderUsersTable() {
    const tbody = document.getElementById("users-tbody");
    if (!tbody) return;
    const keyword = (document.getElementById("users-search")?.value || "").trim().toLowerCase();
    const filtered = usersCache.filter((u) =>
      !keyword ||
      u.username.toLowerCase().includes(keyword) ||
      (u.real_name || "").toLowerCase().includes(keyword)
    );
    const countEl = document.getElementById("users-count");
    if (countEl) countEl.textContent = keyword ? `匹配 ${filtered.length} 条` : "";
    const totalPages = Math.max(1, Math.ceil(filtered.length / usersPageSize));
    if (usersPage > totalPages) usersPage = totalPages;
    const start = (usersPage - 1) * usersPageSize;
    const pageRows = filtered.slice(start, start + usersPageSize);
    const me = API.getUser();

    if (!pageRows.length) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:36px;color:var(--text-3)">${keyword ? "没有匹配的用户" : "暂无用户"}</td></tr>`;
    } else {
      tbody.innerHTML = pageRows.map((u) => `
        <tr>
          <td>${u.id}</td>
          <td>${Common.escapeHtml(u.username)}${u.id === me.id ? ' <span class="badge" style="background:#eff6ff;color:#2563eb">我</span>' : ""}</td>
          <td>${Common.escapeHtml(u.real_name || "-")}</td>
          <td><span class="role-badge ${u.role}">${u.role === "admin" ? "管理员" : "学生"}</span></td>
          <td class="muted">${Common.fmtTime(u.created_at)}</td>
          <td>
            <button class="ops-link edit" data-edit="${u.id}">编辑</button>
            ${u.id !== me.id ? `<button class="ops-link del" data-del="${u.id}">删除</button>` : ""}
          </td>
        </tr>`).join("");
    }

    tbody.querySelectorAll("[data-edit]").forEach((b) =>
      b.addEventListener("click", () => openUserEditor(usersCache.find((u) => u.id === Number(b.dataset.edit)))));
    tbody.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () => removeUser(usersCache.find((u) => u.id === Number(b.dataset.del)))));

    renderPagination("users-pagination", filtered.length, totalPages, usersPage, (p) => {
      usersPage = p; renderUsersTable();
    }, {
      pageSize: usersPageSize,
      sizeOptions: [10, 20],
      onSizeChange: (s) => { usersPageSize = s; usersPage = 1; renderUsersTable(); },
    });
  }

  function openUserEditor(user) {
    const isEdit = !!user;
    Common.openModal(`
      <div class="modal-header">
        <h3>${isEdit ? "编辑用户" : "新增用户"}</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      ${!isEdit ? `
      <div class="form-group">
        <label>账号</label>
        <input class="form-control" id="u-username" placeholder="请输入账号" autocomplete="off">
      </div>` : ""}
      <div class="form-group">
        <label>用户名</label>
        <input class="form-control" id="u-name" value="${isEdit ? Common.escapeHtml(user.real_name || "") : ""}" placeholder="请输入用户名">
      </div>
      <div class="form-group">
        <label>角色</label>
        <select class="form-control" id="u-role">
          <option value="student" ${isEdit && user.role === "student" ? "selected" : ""}>学生</option>
          <option value="admin" ${isEdit && user.role === "admin" ? "selected" : ""}>管理员</option>
        </select>
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
        const payload = {
          real_name: box.querySelector("#u-name").value.trim(),
          role: box.querySelector("#u-role").value,
        };
        const pwd = box.querySelector("#u-pwd").value;
        if (pwd) payload.new_password = pwd;
        try {
          if (isEdit) {
            await API.request("PATCH", `/api/admin/users/${user.id}`, payload);
          } else {
            const username = box.querySelector("#u-username").value.trim();
            if (!username) return Common.toast("请输入账号", "error");
            if (!pwd) return Common.toast("请输入初始密码", "error");
            await API.request("POST", "/api/admin/users", {
              username, password: pwd, real_name: payload.real_name, role: payload.role,
            });
          }
          Common.toast(isEdit ? "已保存" : "已创建", "success");
          Common.closeModal();
          loadUsers();
        } catch (err) { Common.toast(err.message, "error"); }
      });
    }});
  }

  async function removeUser(user) {
    if (!user) return;
    const ok = await Common.confirmDialog({
      title: "删除用户",
      message: `将删除用户「${Common.escapeHtml(user.username)}」及其全部会话记录，不可恢复。确认？`,
      okText: "删除",
    });
    if (!ok) return;
    try {
      await API.request("DELETE", `/api/admin/users/${user.id}`);
      Common.toast("已删除", "success");
      loadUsers();
    } catch (err) { Common.toast(err.message, "error"); }
  }

  /* ==================== 知识库管理（表格） ==================== */
  let kbCache = [];
  let kbPage = 1;
  let kbPageSize = 10;   // 可在 10 / 20 之间切换
  let kbTimer = null;    // 30s 静默刷新：感知学生端/他端对知识库与知识点的增删，保持两端同步

  function stopKbTimer() {
    if (kbTimer) { clearInterval(kbTimer); kbTimer = null; }
  }

  async function silentRefreshKb() {
    try {
      const bases = await API.request("GET", "/api/knowledge/bases");
      kbCache = (bases || []).slice().sort((a, b) => Number(a.id) - Number(b.id));
      renderKbTableRows();   // 保留当前搜索词与页码，静默更新
    } catch { /* 静默：网络异常不影响当前页面 */ }
  }

  function renderKbTable(container) {
    stopKbTimer();
    container.innerHTML = `
      <div class="page-toolbar">
        <div class="toolbar-left">
          <div class="search-box" id="kb-search-box">
            <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            <input class="form-control" id="kb-search" placeholder="搜索知识库名称">
            <button type="button" class="search-clear" id="kb-search-clear" title="清空">✕</button>
          </div>
        </div>
        <div class="toolbar-right">
          <button class="btn btn-light-blue" id="kb-add">＋ 新增知识库</button>
        </div>
      </div>
      <div class="card">
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>ID</th><th>知识库名称</th><th>描述</th><th>文档数</th><th>创建者</th><th>创建时间</th><th>操作</th></tr></thead>
            <tbody id="kb-tbody"><tr><td colspan="7" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr></tbody>
          </table>
        </div>
        <div class="pagination pagination-fixed" id="kb-pagination"></div>
      </div>`;

    document.getElementById("kb-add").addEventListener("click", () => openKbEditor(null));
    const input = document.getElementById("kb-search");
    input.addEventListener("input", () => { kbPage = 1; renderKbTableRows(); });
    document.getElementById("kb-search-clear").addEventListener("click", () => {
      input.value = ""; kbPage = 1; renderKbTableRows(); input.focus();
    });
    loadKbTable();
    // 启动 30s 静默同步（仅在停留在本页时运行）
    kbTimer = setInterval(silentRefreshKb, 30000);
  }

  async function loadKbTable() {
    const tbody = document.getElementById("kb-tbody");
    if (!tbody) return;
    try {
      const bases = await API.request("GET", "/api/knowledge/bases");
      // 按 ID 升序（1 → n）
      kbCache = (bases || []).slice().sort((a, b) => Number(a.id) - Number(b.id));
      renderKbTableRows();
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:36px;color:var(--danger)">${Common.escapeHtml(err.message)}</td></tr>`;
    }
  }

  function renderKbTableRows() {
    const tbody = document.getElementById("kb-tbody");
    if (!tbody) return;
    const keyword = (document.getElementById("kb-search")?.value || "").trim().toLowerCase();
    const filtered = kbCache.filter((b) =>
      !keyword || b.name.toLowerCase().includes(keyword) || (b.description || "").toLowerCase().includes(keyword)
    );
    const totalPages = Math.max(1, Math.ceil(filtered.length / kbPageSize));
    if (kbPage > totalPages) kbPage = totalPages;
    const start = (kbPage - 1) * kbPageSize;
    const pageRows = filtered.slice(start, start + kbPageSize);

    if (!pageRows.length) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:36px;color:var(--text-3)">${keyword ? "没有匹配的知识库" : "暂无知识库"}</td></tr>`;
    } else {
      tbody.innerHTML = pageRows.map((b) => `
        <tr>
          <td>${b.id}</td>
          <td>${Common.escapeHtml(b.name)}</td>
          <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${Common.escapeHtml(b.description || "")}">${Common.escapeHtml(b.description || "-")}</td>
          <td>${b.point_count}</td>
          <td>${Common.escapeHtml(b.creator_name || "-")}</td>
          <td class="muted">${Common.fmtTime(b.created_at)}</td>
          <td>
            <button class="ops-link edit" data-edit="${b.id}">编辑</button>
            <button class="ops-link del" data-del="${b.id}">删除</button>
          </td>
        </tr>`).join("");
    }

    tbody.querySelectorAll("[data-edit]").forEach((b) =>
      b.addEventListener("click", () => openKbEditor(kbCache.find((x) => x.id === Number(b.dataset.edit)))));
    tbody.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () => removeKb(kbCache.find((x) => x.id === Number(b.dataset.del)))));

    renderPagination("kb-pagination", filtered.length, totalPages, kbPage, (p) => {
      kbPage = p; renderKbTableRows();
    }, {
      pageSize: kbPageSize,
      sizeOptions: [10, 20],
      onSizeChange: (s) => { kbPageSize = s; kbPage = 1; renderKbTableRows(); },
    });
  }

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
      <div class="modal-footer">
        <button class="btn" data-close>取消</button>
        <button class="btn btn-primary" id="kb-save">${isEdit ? "保存" : "创建"}</button>
      </div>
    `, { onOpen: (box) => {
      box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));
      box.querySelector("#kb-save").addEventListener("click", async () => {
        const name = box.querySelector("#kb-name").value.trim();
        if (!name) return Common.toast("请输入知识库名称", "error");
        const description = box.querySelector("#kb-desc").value.trim();
        try {
          if (isEdit) await API.request("PUT", `/api/knowledge/bases/${base.id}`, { name, description });
          else await API.request("POST", "/api/knowledge/bases", { name, description });
          Common.toast("已保存", "success");
          Common.closeModal();
          loadKbTable();
        } catch (err) { Common.toast(err.message, "error"); }
      });
    }});
  }

  async function removeKb(base) {
    if (!base) return;
    const ok = await Common.confirmDialog({
      title: "删除知识库",
      message: `将删除知识库「${Common.escapeHtml(base.name)}」及其下 ${base.point_count} 个知识点与向量索引，删除后不可恢复。确认？`,
      okText: "删除",
    });
    if (!ok) return;
    try {
      await API.request("DELETE", `/api/knowledge/bases/${base.id}`);
      Common.toast("已删除", "success");
      loadKbTable();
    } catch (err) { Common.toast(err.message, "error"); }
  }

  /* ==================== 对话历史（问答对表格） ==================== */
  let msgCache = [];
  let msgPage = 1;
  let msgPageSize = 10;   // 可在 10 / 20 之间切换

  function renderMessages(container) {
    container.innerHTML = `
      <div class="page-toolbar">
        <div class="toolbar-left">
          <select class="form-control" id="msg-kb-filter" style="max-width:240px">
            <option value="">按知识库筛选</option>
          </select>
        </div>
      </div>
      <div class="card">
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>ID</th><th>问题</th><th>知识库</th><th>提问者</th><th>时间</th><th>操作</th></tr></thead>
            <tbody id="msg-tbody"><tr><td colspan="6" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr></tbody>
          </table>
        </div>
        <div class="pagination pagination-fixed" id="msg-pagination"></div>
      </div>`;

    // 加载知识库下拉（基于已有知识库/课程）
    API.request("GET", "/api/knowledge/courses").then((courses) => {
      const sel = document.getElementById("msg-kb-filter");
      if (!sel) return;
      (courses || []).forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c; opt.textContent = c;
        sel.appendChild(opt);
      });
    }).catch(() => {});

    document.getElementById("msg-kb-filter").addEventListener("change", () => {
      msgPage = 1; loadMessages();
    });

    loadMessages();
  }

  async function loadMessages() {
    const tbody = document.getElementById("msg-tbody");
    if (!tbody) return;
    const filter = document.getElementById("msg-kb-filter")?.value || "";
    try {
      msgCache = (await API.request("GET", "/api/chat/admin/messages") || []).slice().sort((a, b) => Number(a.id) - Number(b.id));
      if (filter) msgCache = msgCache.filter((m) => (m.knowledge_base || "") === filter);
      renderMsgTable();
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:36px;color:var(--danger)">${Common.escapeHtml(err.message)}</td></tr>`;
    }
  }

  function renderMsgTable() {
    const tbody = document.getElementById("msg-tbody");
    if (!tbody) return;
    const totalPages = Math.max(1, Math.ceil(msgCache.length / msgPageSize));
    if (msgPage > totalPages) msgPage = totalPages;
    const start = (msgPage - 1) * msgPageSize;
    const pageRows = msgCache.slice(start, start + msgPageSize);

    if (!pageRows.length) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:36px;color:var(--text-3)">暂无对话记录</td></tr>`;
    } else {
      tbody.innerHTML = pageRows.map((m, i) => `
        <tr>
          <td>${start + i + 1}</td>
          <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${Common.escapeHtml(m.question)}">${Common.escapeHtml(m.question)}</td>
          <td>${Common.escapeHtml(m.knowledge_base || "全部知识库")}</td>
          <td>${Common.escapeHtml(m.real_name || m.username)}</td>
          <td class="muted">${Common.fmtTime(m.created_at)}</td>
          <td><button class="ops-link edit" data-detail="${m.id}">详情</button></td>
        </tr>`).join("");
    }

    tbody.querySelectorAll("[data-detail]").forEach((b) =>
      b.addEventListener("click", () => viewMsgDetail(msgCache.find((m) => m.id === Number(b.dataset.detail)))));

    renderPagination("msg-pagination", msgCache.length, totalPages, msgPage, (p) => {
      msgPage = p; renderMsgTable();
    }, {
      pageSize: msgPageSize,
      sizeOptions: [10, 20],
      onSizeChange: (s) => { msgPageSize = s; msgPage = 1; renderMsgTable(); },
    });
  }

  function viewMsgDetail(m) {
    if (!m) return;
    Common.openModal(`
      <div class="modal-header">
        <h3>对话详情</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      <div style="display:flex;flex-direction:column;gap:14px">
        <div>
          <div class="muted mb-8" style="font-weight:600">提问</div>
          <div style="padding:12px 14px;background:#f8fafc;border-radius:10px;line-height:1.7">${Common.escapeHtml(m.question)}</div>
        </div>
        <div>
          <div class="muted mb-8" style="font-weight:600">回答</div>
          <div class="md-body" style="padding:12px 14px;background:#eff6ff;border-radius:10px;line-height:1.7">${Markdown.render(m.answer || "-")}</div>
        </div>
        <div>
          <div class="muted mb-8" style="font-weight:600">知识库</div>
          <div style="font-size:13px">${Common.escapeHtml(m.knowledge_base || "全部知识库")}</div>
        </div>
        <div>
          <div class="muted mb-8" style="font-weight:600">时间</div>
          <div style="font-size:13px">${Common.fmtTime(m.created_at)}</div>
        </div>
      </div>
      <div class="modal-footer"><button class="btn" data-close>关闭</button></div>
    `, { size: "modal-lg", onOpen: (box) => {
      box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));
    }});
  }

  /* ==================== 反馈（学生提问未答上的问题） ==================== */
  let uqCache = [];
  let uqPage = 1;
  let uqPageSize = 10;   // 可在 10 / 20 之间切换
  let uqStatus = "";

  function renderUnanswered(container) {
    container.innerHTML = `
      <div class="fb-stat-grid" id="uq-stats"></div>
      <div class="fb-tabs" id="uq-tabs">
        <button class="fb-tab active" data-tab="">全部</button>
        <button class="fb-tab" data-tab="pending">处理中</button>
        <button class="fb-tab" data-tab="resolved">已解决</button>
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
      loadUnanswered();
    });
    loadUnanswered();
  }

  async function loadUnanswered() {
    const list = document.getElementById("uq-list");
    if (!list) return;
    list.innerHTML = `<div class="fb-loading"><span class="loading-spinner"></span></div>`;
    try {
      const query = uqStatus ? `?status=${encodeURIComponent(uqStatus)}` : "";
      const [data, stats] = await Promise.all([
        API.request("GET", `/api/admin/unanswered${query}`),
        API.request("GET", "/api/admin/unanswered/stats").catch(() => null),
      ]);
      uqCache = (data.items || []).slice();
      renderUqStats(stats);
      renderUqList();
    } catch (err) {
      list.innerHTML = `<div class="fb-empty"><p style="color:var(--danger)">${Common.escapeHtml(err.message)}</p></div>`;
    }
  }

  /* 与学生端「我的反馈」同款统计卡：白色卡片 + 浅色图标块 + 数字/标签 */
  function renderUqStats(stats) {
    const el = document.getElementById("uq-stats");
    if (!el) return;
    const pending = stats && stats.pending != null
      ? stats.pending
      : uqCache.filter((x) => x.status === "pending").length;
    const resolved = stats && stats.resolved != null
      ? stats.resolved
      : uqCache.filter((x) => x.status === "resolved").length;
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
    const totalPages = Math.max(1, Math.ceil(uqCache.length / uqPageSize));
    if (uqPage > totalPages) uqPage = totalPages;
    const start = (uqPage - 1) * uqPageSize;
    const pageRows = uqCache.slice(start, start + uqPageSize);

    if (!pageRows.length) {
      list.innerHTML = `<div class="fb-empty">
        <div class="fb-empty-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></div>
        <p>${uqStatus === "pending"
          ? "没有待处理的问题反馈"
          : uqStatus === "resolved"
            ? "还没有已解决的反馈"
            : "还没有收到问题反馈"}</p>
      </div>`;
      renderPagination("uq-pagination", 0, 1, 1, () => {}, {
        pageSize: uqPageSize, sizeOptions: [10, 20],
        onSizeChange: (s) => { uqPageSize = s; uqPage = 1; renderUqList(); },
      });
      return;
    }
    list.innerHTML = pageRows.map((q) => `
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
      b.addEventListener("click", () => openGenerateGuide(uqCache.find((x) => x.id === Number(b.dataset.gen)))));
    list.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () => removeUq(uqCache.find((x) => x.id === Number(b.dataset.del)))));

    renderPagination("uq-pagination", uqCache.length, totalPages, uqPage, (p) => {
      uqPage = p; renderUqList();
    }, {
      pageSize: uqPageSize,
      sizeOptions: [10, 20],
      onSizeChange: (s) => { uqPageSize = s; uqPage = 1; renderUqList(); },
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
      <div style="background:#f8fafc;border:1px solid #eef2f7;border-radius:10px;padding:14px 16px;font-size:15px;line-height:1.75;color:#0f172a">${Common.escapeHtml(q.question)}</div>
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

  /* ==================== 通用分页渲染 ==================== */
  function renderPagination(elId, total, totalPages, current, onChange, opts) {
    const el = document.getElementById(elId);
    if (!el) return;
    opts = opts || {};
    const sizeOptions = opts.sizeOptions && opts.sizeOptions.length ? opts.sizeOptions : [10];
    const pageSize = opts.pageSize || sizeOptions[0];

    let pages = "";
    for (let p = 1; p <= totalPages; p++) {
      pages += `<button class="page-btn ${p === current ? "active" : ""}" data-page="${p}">${p}</button>`;
    }
    el.innerHTML = `
      <span class="total">共 ${total} 条</span>
      <select class="page-size">
        ${sizeOptions.map((s) => `<option value="${s}" ${s === pageSize ? "selected" : ""}>${s}条/页</option>`).join("")}
      </select>
      <button class="page-btn" data-page="${current - 1}" ${current <= 1 ? "disabled" : ""}>‹</button>
      ${pages}
      <button class="page-btn" data-page="${current + 1}" ${current >= totalPages ? "disabled" : ""}>›</button>`;

    el.querySelectorAll("[data-page]").forEach((b) => {
      b.addEventListener("click", () => {
        const p = Number(b.dataset.page);
        if (p >= 1 && p <= totalPages && p !== current) onChange(p);
      });
    });

    const sizeSel = el.querySelector(".page-size");
    if (sizeSel && typeof opts.onSizeChange === "function") {
      sizeSel.addEventListener("change", () => opts.onSizeChange(Number(sizeSel.value)));
    }
  }

  /* ==================== 分派 ==================== */
  function render(container, sub) {
    stopKbTimer();   // 离开知识库管理即停止轮询
    if (sub === "users") renderUsers(container);
    else if (sub === "overview") renderOverview(container);
    else if (sub === "kb_table") renderKbTable(container);
    else if (sub === "conversations") renderMessages(container);
    else if (sub === "unanswered") renderUnanswered(container);
  }

  return { render };
})();
