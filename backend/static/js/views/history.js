/* ============ 视图：对话历史（学生自己的会话） ============ */
const HistoryView = (() => {
  function render(container) {
    container.innerHTML = `
      <div class="card">
        <div class="row-between mb-16">
          <b>我的问答会话</b>
          <div class="row" style="gap:10px">
            <button class="btn btn-sm" id="history-new">＋ 发起新问答</button>
            <button class="btn btn-sm" id="history-refresh">↻ 刷新</button>
          </div>
        </div>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>ID</th><th>会话标题</th><th>消息数</th><th>创建时间</th><th>操作</th></tr></thead>
            <tbody id="history-tbody"><tr><td colspan="5" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr></tbody>
          </table>
        </div>
      </div>`;

    document.getElementById("history-new").addEventListener("click", () => App.navigate("chat"));
    document.getElementById("history-refresh").addEventListener("click", loadHistory);
    loadHistory();
  }

  async function loadHistory() {
    const tbody = document.getElementById("history-tbody");
    if (!tbody) return;
    try {
      const sessions = await API.request("GET", "/api/chat/sessions");
      if (!sessions.length) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:36px;color:var(--text-3)">暂无会话，点击「发起新问答」开始第一次提问</td></tr>`;
        return;
      }
      tbody.innerHTML = sessions.map((c) => `
        <tr>
          <td>${c.id}</td>
          <td title="${Common.escapeHtml(c.title)}" style="max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${Common.escapeHtml(c.title)}</td>
          <td class="muted">查看详情</td>
          <td class="muted">${Common.fmtTime(c.created_at)}</td>
          <td>
            <div class="ops">
              <button class="btn btn-sm" data-view="${c.id}">查看</button>
              <button class="btn btn-sm btn-danger" data-del="${c.id}">删除</button>
            </div>
          </td>
        </tr>`).join("");

      tbody.querySelectorAll("[data-view]").forEach((b) =>
        b.addEventListener("click", () => viewSession(Number(b.dataset.view))));
      tbody.querySelectorAll("[data-del]").forEach((b) =>
        b.addEventListener("click", () => removeSession(Number(b.dataset.del), sessions.find((c) => c.id === Number(b.dataset.del)))));
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:36px;color:var(--danger)">${Common.escapeHtml(err.message)}</td></tr>`;
    }
  }

  async function viewSession(id) {
    try {
      const msgs = await API.request("GET", `/api/chat/sessions/${id}/messages`);
      const title = msgs.length ? (msgs[0].role === "user" ? msgs[0].content : "") : "";
      Common.openModal(`
        <div class="modal-header">
          <h3>会话 #${id} · 共 ${msgs.length} 条消息</h3>
          <button class="modal-close" data-close>×</button>
        </div>
        <div style="max-height:56vh;overflow-y:auto;display:flex;flex-direction:column;gap:10px">
          ${msgs.length ? msgs.map((m) => `
            <div class="msg-row ${m.role === "user" ? "" : "msg-assistant"}">
              <div class="msg-role">${m.role === "user" ? "👤 我" : "🤖 助教"}</div>
              <div class="msg-content">${Common.escapeHtml(m.content)}</div>
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
      loadHistory();
    } catch (err) { Common.toast(err.message, "error"); }
  }

  return { render };
})();
