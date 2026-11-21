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
      <div class="ov-wrap">
        <div class="ov-metrics" id="ov-metrics"><span class="ov-skeleton"></span></div>
        <div class="ov-quality" id="ov-quality"></div>
        <div class="ov-grid">
          <section class="ov-panel ov-col-8">
            <div class="ov-panel-head">
              <span>近 7 天提问</span>
              <span class="ov-note" id="ov-today">—</span>
            </div>
            <div class="ov-spark" id="ov-spark"></div>
          </section>
          <section class="ov-panel ov-col-4">
            <div class="ov-panel-head"><span>待处理</span></div>
            <div class="ov-list" id="ov-todo"></div>
          </section>
          <section class="ov-panel ov-col-4">
            <div class="ov-panel-head"><span>热门问题</span></div>
            <div class="ov-list" id="ov-hot"></div>
          </section>
          <section class="ov-panel ov-col-4">
            <div class="ov-panel-head">
              <span>最近答不上来</span>
              <button class="btn btn-sm" id="ov-fb-all">全部</button>
            </div>
            <div class="ov-list" id="ov-fb"></div>
          </section>
          <section class="ov-panel ov-col-4">
            <div class="ov-panel-head">
              <span>知识库</span>
              <button class="btn btn-sm" id="ov-kb-manage">管理</button>
            </div>
            <div class="ov-list" id="ov-kb"></div>
          </section>
        </div>
      </div>`;
    loadStats();
  }

  async function loadStats() {
    const box = document.getElementById("ov-metrics");
    try {
      const [s, kbPage] = await Promise.all([
        API.request("GET", "/api/stats/admin/stats"),
        API.request("GET", "/api/knowledge/bases/page?field=name&sort=id&limit=5&offset=0").catch(() => ({ items: [] })),
      ]);
      renderMetrics(s);
      renderQuality(s);
      renderSpark(s);
      renderHot(s);
      renderOverviewKb((kbPage && kbPage.items) || []);
      renderTodo(s);
      renderRecent(s);
    } catch (err) {
      box.innerHTML = `<div class="ov-empty">${Common.escapeHtml(err.message)}</div>`;
    }
  }

  function renderMetrics(s) {
    const items = [
      { label: "用户", value: s.users || 0, go: "users" },
      { label: "管理员", value: s.admins || 0, go: "users" },
      { label: "学生", value: s.students || 0, go: "users" },
      { label: "知识库", value: s.knowledge_bases || 0, go: "content" },
      { label: "文档", value: s.documents || 0, go: "documents" },
      { label: "知识点", value: s.knowledge_points || 0, go: "content" },
    ];
    document.getElementById("ov-metrics").innerHTML = items.map((it, i) => `
      <button class="ov-metric" data-go="${it.go}" ${i === 0 ? 'data-first="1"' : ""}>
        <span class="ov-metric-label">${it.label}</span>
        <span class="ov-metric-value">${it.value}</span>
      </button>`).join("");
    document.querySelectorAll(".ov-metric").forEach((el) =>
      el.addEventListener("click", () => App.navigate(el.dataset.go)));
  }

  /* 质量指标：命中 / 拒答 / 好评 / 平均解决，颜色只做小圆点 */
  function renderQuality(s) {
    const total = s.total_questions || 0;
    const pct = (v) => (total ? `${Math.round((v || 0) * 100)}%` : "—");
    const hours = s.avg_resolve_hours;
    const resolve = hours == null ? "—" : hours >= 24 ? `${(hours / 24).toFixed(1)} 天` : `${Number(hours).toFixed(1)} 小时`;
    const rows = [
      { label: "回答命中率", value: pct(s.hit_rate), tone: "ok" },
      { label: "拒答率", value: pct(s.reject_rate), tone: "warn" },
      (() => {
        const rated = (s.feedback_up || 0) + (s.feedback_down || 0);
        return { label: "回答好评率", value: rated ? pct(s.feedback_up / rated) : "—", tone: "ok" };
      })(),
      { label: "平均解决时长", value: resolve, tone: "muted" },
    ];
    document.getElementById("ov-quality").innerHTML = rows.map((r) => `
      <div class="ov-quality-item">
        <span class="ov-dot tone-${r.tone}"></span>
        <span class="ov-quality-label">${r.label}</span>
        <span class="ov-quality-value">${r.value}</span>
      </div>`).join("");
  }

  /* 近 7 天：细柱，今天实色，其余淡色，不画坐标轴 */
  function renderSpark(s) {
    const spark = document.getElementById("ov-spark");
    const note = document.getElementById("ov-today");
    const daily = s.daily_questions || [];
    if (note) note.textContent = `今日 ${s.today_questions || 0}`;
    if (!daily.some((d) => d.count > 0)) {
      spark.innerHTML = `<div class="ov-empty">最近 7 天还没有提问</div>`;
      return;
    }
    const max = Math.max(...daily.map((d) => d.count));
    const last = daily[daily.length - 1].date;
    spark.innerHTML = `
      <div class="ov-spark-bars">
        ${daily.map((d) => `
          <div class="ov-spark-col${d.date === last ? " is-today" : ""}" title="${d.date} · ${d.count} 次">
            <span class="ov-spark-num">${d.count || ""}</span>
            <span class="ov-spark-bar" style="height:${Math.max(6, Math.round((d.count / max) * 76))}px"></span>
            <span class="ov-spark-date">${d.date.slice(5)}</span>
          </div>`).join("")}
      </div>`;
  }

  function renderHot(s) {
    const box = document.getElementById("ov-hot");
    const hot = (s.hot_questions || []).slice(0, 6);
    if (!hot.length) {
      box.innerHTML = `<div class="ov-empty">暂无提问</div>`;
      return;
    }
    box.innerHTML = hot.map((h, i) => `
      <div class="ov-row ov-row-hot">
        <span class="ov-rank${i < 3 ? " is-top" : ""}">${i + 1}</span>
        <span class="ov-row-title" title="${Common.escapeHtml(h.question)}">${Common.escapeHtml(h.question)}</span>
        <span class="ov-row-meta">${h.count} 次</span>
      </div>`).join("");
  }

  function renderOverviewKb(items) {
    const box = document.getElementById("ov-kb");
    if (!items.length) {
      box.innerHTML = `<div class="ov-empty">还没有知识库</div>`;
    } else {
      const rows = items.map((b) => `
        <div class="ov-row ov-row-kb" data-go="content">
          <span class="ov-row-title">${Common.escapeHtml(b.name)}</span>
          <span class="ov-row-meta">${b.document_count || 0} 篇</span>
          <span class="ov-row-meta">${b.point_count || 0} 个知识点</span>
        </div>`).join("");
      box.innerHTML = `<div class="ov-row ov-row-head"><span>名称</span><span>文档</span><span>知识点</span></div>` + rows;
      box.querySelectorAll(".ov-row-kb").forEach((r) =>
        r.addEventListener("click", () => App.navigate("content")));
    }
    document.getElementById("ov-kb-manage")?.addEventListener("click", () => App.navigate("content"));
  }

  function renderTodo(s) {
    const box = document.getElementById("ov-todo");
    const pendingDocs = Math.max(0, (s.documents || 0) - (s.documents_ready || 0));
    const rows = [
      { label: "待解答问题", value: s.unanswered_pending || 0, go: "unanswered" },
      { label: "文档未解析完成", value: pendingDocs, go: "documents" },
      { label: "学生差评", value: s.feedback_down || 0, go: "history" },
    ];
    box.innerHTML = rows.map((r) => `
      <div class="ov-row ov-row-todo${r.value ? "" : " is-zero"}" data-go="${r.value ? r.go : ""}">
        <span class="ov-row-title">${r.label}</span>
        <span class="ov-row-value">${r.value}</span>
      </div>`).join("");
    box.querySelectorAll(".ov-row-todo").forEach((r) => {
      if (r.dataset.go) r.addEventListener("click", () => App.navigate(r.dataset.go));
    });
  }

  function renderRecent(s) {
    const box = document.getElementById("ov-fb");
    const recent = (s.recent_unanswered || []).slice(0, 6);
    if (!recent.length) {
      box.innerHTML = `<div class="ov-empty">暂无</div>`;
    } else {
      box.innerHTML = recent.map((r) => `
        <div class="ov-row ov-row-fb" data-go="unanswered">
          <span class="ov-dot${r.status === "pending" ? " is-pending" : ""}"></span>
          <span class="ov-row-title" title="${Common.escapeHtml(r.question)}">${Common.escapeHtml(r.question)}</span>
          <span class="ov-row-meta">${Common.fmtTime(r.created_at)}</span>
        </div>`).join("");
      box.querySelectorAll(".ov-row-fb").forEach((r) =>
        r.addEventListener("click", () => App.navigate("unanswered")));
    }
    document.getElementById("ov-fb-all")?.addEventListener("click", () => App.navigate("unanswered"));
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
      message: `将删除用户「${user.username || user.account}」及其全部会话记录，不可恢复。确认？`,
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
      message: `将删除知识库「${base.name}」及其下 ${base.document_count || 0} 个文档、${base.point_count || 0} 个知识点与向量索引，删除后进入回收站，30 天内可恢复。确认？`,
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
          ${msgs.length ? msgs.map((m) => {
            // U7：管理员要能追溯「答案引用哪份资料」「学生对这条什么评价」
            const srcs = Array.isArray(m.sources) ? m.sources : [];
            const fbLabel = m.feedback === "up" ? "👍 有帮助" : m.feedback === "down" ? "👎 没帮助" : "";
            const reason = m.feedback === "down" && m.feedback_reason
              ? ` · 原因：${Common.escapeHtml(m.feedback_reason)}`
              : "";
            const srcHtml = srcs.length ? `
              <div class="admin-msg-sources">${srcs.map((s) => {
                const name = s.filename || s.title || s.course || "来源";
                const docId = s.doc_id ?? s.document_id;
                return docId !== undefined && docId !== null && docId !== ""
                  ? `<a class="src-chip" href="/api/documents/${encodeURIComponent(docId)}/file" target="_blank" rel="noopener" title="打开原文">${Common.escapeHtml(name)}</a>`
                  : `<span class="src-chip">${Common.escapeHtml(name)}</span>`;
              }).join("")}</div>` : "";
            return `
            <div class="msg-row ${m.role === "user" ? "" : "msg-assistant"}">
              <div class="msg-role">${m.role === "user" ? "提问" : "助教"}</div>
              <div class="${m.role === "assistant" ? "msg-content md-body" : "msg-content"}">${m.role === "assistant" ? Markdown.render(m.content) : Common.escapeHtml(m.content)}</div>
              ${srcHtml}
              <div class="muted" style="font-size:12px;margin-top:6px">${Common.fmtTime(m.created_at)}${m.interrupted ? " · 生成被中断" : ""}${fbLabel ? ` · ${fbLabel}${reason}` : ""}${m.rejected ? " · 判定为拒答" : ""}</div>
            </div>`;
          }).join("") : `<div class="empty-state"><p>该会话暂无消息</p></div>`}
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
      message: `将删除会话「${title || "#" + id}」及其全部消息，不可恢复。确认？`,
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
