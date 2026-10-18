/* ============ 视图：对话历史（学生自己的会话）
 * 服务端分页 + 标题搜索（GET /api/chat/sessions?q=&limit=&offset=），
 * 列表按创建时间倒序，ID 列显示的是「从最早数起」的序号 total-offset-i（不是数据库主键）。
 * 底部固定分页：共 N 条 + 10/20 条每页 + 页码。
 */
const HistoryView = (() => {
  let rows = [];       // 当前这一页（服务端已按 id 倒序）
  let total = 0;       // 匹配总数
  let page = 1;
  let pageSize = 10;
  let query = "";      // 标题关键字（交给服务端过滤）
  let searchTimer = null;

  function render(container) {
    container.innerHTML = `
      <div class="card">
        <div class="row-between mb-16">
          <b>我的问答会话</b>
          <div class="row" style="gap:10px">
            <input class="form-control" id="history-search" type="search" placeholder="搜索会话标题"
                   autocomplete="off" style="width:200px" aria-label="搜索会话">
            <button class="btn btn-sm" id="history-new"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14"/><path d="M5 12h14"/></svg>发起新问答</button>
            <button class="btn btn-sm" id="history-refresh"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 6"/><path d="M20 4v7h-7"/></svg>刷新</button>
          </div>
        </div>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>ID</th><th>会话标题</th><th>消息数</th><th>创建时间</th><th>操作</th></tr></thead>
            <tbody id="history-tbody"><tr><td colspan="5" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr></tbody>
          </table>
        </div>
        <div class="pagination pagination-fixed" id="history-pagination"></div>
      </div>`;

    document.getElementById("history-search").addEventListener("input", (e) => {
      query = e.target.value.trim();
      page = 1;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => load(true), 250);
    });
    document.getElementById("history-new").addEventListener("click", () => App.navigate("chat"));
    document.getElementById("history-refresh").addEventListener("click", () => {
      load(true).then((ok) =>
        Common.toast(ok ? "已刷新" : "刷新失败，检查一下网络", ok ? "success" : "error"));
    });
    load(false);
  }

  /* keepPage=true：刷新时保留当前页（页码超界会自动回退） */
  async function load(keepPage) {
    const tbody = document.getElementById("history-tbody");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr>`;
    if (!keepPage) page = 1;
    const params = new URLSearchParams({ limit: String(pageSize), offset: String((page - 1) * pageSize) });
    if (query) params.set("q", query);
    try {
      const data = await API.request("GET", `/api/chat/sessions?${params.toString()}`);
      total = (data && data.total) || 0;
      rows = (data && data.items) || [];
      renderRows();
      return true;
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:36px;color:var(--danger)">${Common.escapeHtml(err.message)}</td></tr>`;
      Common.renderPagination("history-pagination", 0, 1, pageSize, () => {}, {});
      return false;
    }
  }

  function renderRows() {
    const tbody = document.getElementById("history-tbody");
    if (!tbody) return;
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    if (page > totalPages) {   // 删掉最后一页的最后一条时往回退
      load(true);
      return;
    }
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:36px;color:var(--text-3)">${query ? "没有匹配的会话" : "暂无会话，点击「发起新问答」开始第一次提问"}</td></tr>`;
      Common.renderPagination("history-pagination", total, page, pageSize, (p) => {
        page = p;
        load(true);
      }, {
        sizeOptions: [10, 20],
        onSizeChange: (s) => { pageSize = s; page = 1; load(true); },
      });
      return;
    }

    const start = (page - 1) * pageSize;   // 倒序列表里的全局偏移
    // ID 列 = 从最早那条数起的序号：最新一页的编号最大，和以前的 1..n 一致
    const noOf = (i) => total - (start + i);

    tbody.innerHTML = rows.map((c, i) => `
      <tr>
        <td>${noOf(i)}</td>
        <td title="${Common.escapeHtml(c.title)}" style="max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${Common.escapeHtml(c.title)}</td>
        <td class="muted">${c.message_count || 0}</td>
        <td class="muted">${Common.fmtTime(c.created_at)}</td>
        <td>
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
      load(true);
    }, {
      sizeOptions: [10, 20],
      onSizeChange: (s) => { pageSize = s; page = 1; load(true); },
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
      message: `将删除会话「${Common.escapeHtml(session ? session.title : "#" + id)}」及其全部消息，不可恢复。确认？`,
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
