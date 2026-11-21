/* ============ 视图：对话历史（学生自己的会话）
 * 服务端分页 + 标题搜索（GET /api/chat/sessions?q=&limit=&offset=&order=asc），
 * 搜索栏放页头工具栏（与管理员「对话历史」一致），卡片里只留标题和表格。
 * 列表按创建顺序（从旧到新）排，ID 列就是 1..n 的连续序号（不是数据库主键）：
 * 删掉其中一条后重新加载，后面的编号整体前移补位。
 * 底部固定分页：共 N 条 + 每页条数 + ‹ 当前页 ›。
 */
const HistoryView = (() => {
  let rows = [];       // 当前这一页（服务端已按 id 升序）
  let total = 0;       // 匹配总数
  let page = 1;
  let pageSize = 10;
  let query = "";      // 标题关键字（交给服务端过滤）
  let searchTimer = null;
  let loadSeq = 0;     // 请求序号：连点翻页/连续搜索时，只认最后一次的结果

  /* 地址栏参数：q=关键字、page=页码。刷新/分享链接能回到同一屏。 */
  function readParams(opts) {
    const params = (opts && opts.query) || new URLSearchParams();
    const q = (params.get("q") || "").trim();
    const p = parseInt(params.get("page") || "1", 10);
    return { q, page: Number.isFinite(p) && p > 0 ? p : 1 };
  }

  function writeParams() {
    if (typeof App === "undefined" || !App.updateQuery) return;
    App.updateQuery({ q: query, page: page > 1 ? page : "" });
  }

  function render(container, opts) {
    const initial = readParams(opts);
    query = initial.q;
    page = initial.page;
    container.innerHTML = `
      <div class="page-toolbar">
        <div class="toolbar-left" style="gap:10px">
          <div class="search-box" id="history-search-box">
            <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            <input class="form-control" id="history-search" placeholder="搜索会话标题">
            <button type="button" class="search-clear" id="history-search-clear" title="清空"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="row-between mb-16">
          <b>我的问答会话</b>
        </div>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>ID</th><th>会话标题</th><th>消息数</th><th>创建时间</th><th>操作</th></tr></thead>
            <tbody id="history-tbody"><tr><td colspan="5" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr></tbody>
          </table>
        </div>
        <div class="pagination pagination-fixed" id="history-pagination"></div>
      </div>`;

    const input = document.getElementById("history-search");
    const box = document.getElementById("history-search-box");
    input.value = query;
    const syncClear = () => box.classList.toggle("has-value", input.value.length > 0);
    syncClear();
    const rerun = () => {
      query = input.value.trim();
      page = 1;
      writeParams();
      load(false);
    };
    input.addEventListener("input", syncClear);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); rerun(); }
    });
    Common.attachSearchButton(box, rerun);   // 与管理员对话历史页同一套搜索方式
    document.getElementById("history-search-clear").addEventListener("click", () => {
      input.value = ""; syncClear(); rerun(); input.focus();
    });
    load(true);
  }

  /* keepPage=true：刷新时保留当前页（页码超界会自动回退） */
  async function load(keepPage) {
    const tbody = document.getElementById("history-tbody");
    if (!tbody) return;
    const seq = ++loadSeq;   // 旧请求回来时 loadSeq 已变，直接丢弃（否则慢响应会盖掉新结果）
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr>`;
    if (!keepPage) page = 1;
    const params = new URLSearchParams({
      limit: String(pageSize),
      offset: String((page - 1) * pageSize),
      order: "asc",     // 从旧到新：第一页是第 1..N 条，编号才能是 1..n
    });
    if (query) params.set("q", query);
    try {
      const data = await API.request("GET", `/api/chat/sessions?${params.toString()}`);
      if (seq !== loadSeq) return false;   // 已被更新的请求取代（用户又翻页/改关键字）
      total = (data && data.total) || 0;
      rows = (data && data.items) || [];
      renderRows();
      return true;
    } catch (err) {
      if (seq !== loadSeq) return false;   // 旧请求的失败不该覆盖新结果
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:36px;color:var(--danger)">${Common.escapeHtml(err.message)}</td></tr>`;
      Common.renderPagination("history-pagination", 0, 1, pageSize, () => {}, {});
      return false;
    }
  }

  function renderRows() {
    const tbody = document.getElementById("history-tbody");
    if (!tbody) return;
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    if (page > totalPages) {   // 删掉最后一页的最后一条、或链接里的页码越界：退回最后一页再取一次
      page = totalPages;
      writeParams();           // 地址栏同步，否则刷新后又会回到越界页码
      load(true);
      return;
    }
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:36px;color:var(--text-3)">${query ? "没有匹配的会话" : "暂无会话，去「智能问答」发起第一次提问"}</td></tr>`;
      Common.renderPagination("history-pagination", total, page, pageSize, (p) => {
        page = p;
        writeParams();
        load(true);
      }, {
        sizeOptions: [10, 20],
        onSizeChange: (s) => { pageSize = s; page = 1; writeParams(); load(true); },
      });
      return;
    }

    const start = (page - 1) * pageSize;   // 升序列表里的全局偏移
    // ID 列 = 1..n 的连续序号：删掉中间一条后重新加载，后面的编号自动往前补
    const noOf = (i) => start + i + 1;

    tbody.innerHTML = rows.map((c, i) => `
      <tr>
        <td data-label="序号">${noOf(i)}</td>
        <td data-label="会话标题" title="${Common.escapeHtml(c.title)}" style="max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${Common.escapeHtml(c.title)}</td>
        <td data-label="消息数" class="muted">${c.message_count || 0}</td>
        <td data-label="创建时间" class="muted">${Common.fmtTime(c.created_at)}</td>
        <td data-label="操作">
          <div class="ops">
            <button class="btn btn-sm" data-view="${c.id}">查看</button>
            <button class="btn btn-sm btn-danger" data-del="${c.id}">删除</button>
          </div>
        </td>
      </tr>`).join("");

    tbody.querySelectorAll("[data-view]").forEach((b) =>
      b.addEventListener("click", () => {
        const idx = rows.findIndex((c) => c.id === Number(b.dataset.view));
        viewSession(Number(b.dataset.view), noOf(idx));
      }));
    tbody.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () =>
        removeSession(Number(b.dataset.del), rows.find((c) => c.id === Number(b.dataset.del)))));

    Common.renderPagination("history-pagination", total, page, pageSize, (p) => {
      page = p;
      writeParams();
      load(true);
    }, {
      sizeOptions: [10, 20],
      onSizeChange: (s) => { pageSize = s; page = 1; writeParams(); load(true); },
    });
  }
  // 兼容数组 / JSON 字符串两种来源格式
  function srcTitles(m) {
    if (m.role !== "assistant" || !m.sources) return [];
    let arr = m.sources;
    if (typeof arr === "string") {
      try { arr = JSON.parse(arr); } catch { return []; }
    }
    return Array.isArray(arr) ? arr.map((s) => Common.escapeHtml((s && s.title) || "未知来源")) : [];
  }

  function srcLine(m) {
    const titles = srcTitles(m);
    return titles.length ? `<div class="msg-src-mini">参考：${titles.join("、")}</div>` : "";
  }

  async function viewSession(id, no) {
    try {
      const msgs = await API.request("GET", `/api/chat/sessions/${id}/messages`);
      Common.openModal(`
        <div class="modal-header">
          <h3>会话 #${no} · 共 ${msgs.length} 条消息</h3>
          <button class="modal-close" data-close>×</button>
        </div>
        <div style="max-height:56vh;overflow-y:auto;display:flex;flex-direction:column;gap:10px">
          ${msgs.length ? msgs.map((m) => `
            <div class="msg-row ${m.role === "user" ? "" : "msg-assistant"}">
              <div class="msg-role">${m.role === "user" ? "我" : "助教"}</div>
              <div class="msg-content">${Common.escapeHtml(m.content)}</div>
              ${srcLine(m)}
              <div class="muted" style="font-size:12px;margin-top:6px">${Common.fmtTime(m.created_at)}</div>
            </div>`).join("") : `<div class="empty-state"><p>该会话暂无消息</p></div>`}
        </div>
        <div class="modal-footer"><button class="btn" data-close>关闭</button></div>
      `, { size: "modal-lg", onOpen: (box) => {
        box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));
      }});
    } catch (err) { Common.toast(err.message, "error"); }
  }

  async function removeSession(id, session) {
    const ok = await Common.confirmDialog({
      title: "删除会话",
      message: `将删除会话「${session ? session.title : "#" + id}」及其全部消息，不可恢复。确认？`,
      okText: "删除",
    });
    if (!ok) return;
    try {
      await API.request("DELETE", `/api/chat/sessions/${id}`);
      Common.toast("已删除", "success");
      load(true);   // 重新拉取后编号会自动重排
    } catch (err) { Common.toast(err.message, "error"); }
  }

  return { render };
})();
