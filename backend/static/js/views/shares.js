/* 分享管理：列出已创建的分享链接，支持复制、打开与撤销。 */
const SharesView = (() => {
  const PAGE_SIZE = 10;
  const KIND_LABEL = { conversation: "会话", knowledge_base: "知识库" };
  let items = [];
  let page = 1;
  let kind = "";
  let keyword = "";

  const el = (id) => document.getElementById(id);

  function isAdmin() {
    const user = (API.getUser && API.getUser()) || {};
    return user.role === "admin";
  }

  function render(container) {
    container.innerHTML = `
      <div class="page-toolbar">
        <div class="toolbar-left" style="gap:10px">
          <select class="form-control" id="shares-kind" style="width:130px" aria-label="类型">
            <option value="">全部类型</option>
            <option value="conversation">会话</option>
            ${isAdmin() ? '<option value="knowledge_base">知识库</option>' : ""}
          </select>
          <div class="search-box" id="shares-search-box">
            <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            <input class="form-control" id="shares-search" placeholder="搜索内容">
            <button type="button" class="search-clear" id="shares-search-clear" title="清空"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button>
          </div>
        </div>
        <div class="toolbar-right">
          <button class="btn btn-sm" id="shares-export"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>导出</button>
        </div>
      </div>
      <div class="card">
        <div class="row-between mb-16">
          <b>我的分享</b>
          <span class="muted" id="shares-count" style="font-size:13px"></span>
        </div>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>类型</th><th>内容</th><th>状态</th><th>有效期至</th><th>创建时间</th><th>操作</th></tr></thead>
            <tbody id="shares-tbody"><tr><td colspan="6" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr></tbody>
          </table>
        </div>
        <div class="pagination pagination-fixed" id="shares-pagination"></div>
      </div>`;

    const input = el("shares-search");
    const box = el("shares-search-box");
    const syncClear = () => box.classList.toggle("has-value", input.value.length > 0);
    input.addEventListener("input", syncClear);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        keyword = input.value.trim();
        page = 1;
        load();
      }
    });
    Common.attachSearchButton(box, () => {
      keyword = input.value.trim();
      page = 1;
      load();
    });
    el("shares-search-clear").addEventListener("click", () => {
      input.value = "";
      keyword = "";
      page = 1;
      syncClear();
      load();
      input.focus();
    });
    el("shares-kind").addEventListener("change", (e) => {
      kind = e.target.value;
      page = 1;
      load();
    });
    el("shares-export").addEventListener("click", () => ExportShareView.openModal());
    el("shares-tbody").addEventListener("click", onAction);
    document.addEventListener("share:created", onShareCreated);

    load();
  }

  function onShareCreated() {
    if (!el("shares-tbody")) {
      document.removeEventListener("share:created", onShareCreated);
      return;
    }
    page = 1;
    load();
  }

  async function load() {
    const tbody = el("shares-tbody");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr>`;
    try {
      const data = await API.request("GET", "/api/share?limit=100&offset=0");
      items = data.items || [];
      if (!el("shares-tbody")) return;
      renderRows();
    } catch (err) {
      if (!el("shares-tbody")) return;
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:36px;color:var(--danger)">${Common.escapeHtml(err.message)}</td></tr>`;
      el("shares-pagination").innerHTML = "";
    }
  }

  function filtered() {
    const q = keyword.toLowerCase();
    return items.filter((item) => {
      if (kind && item.kind !== kind) return false;
      if (!q) return true;
      return String(item.target_title || "").toLowerCase().includes(q);
    });
  }

  function renderRows() {
    const tbody = el("shares-tbody");
    const rows = filtered();
    const total = rows.length;
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (page > totalPages) page = totalPages;
    el("shares-count").textContent = total ? `共 ${total} 条` : "";
    const slice = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
    tbody.innerHTML = slice.length
      ? slice.map(rowHtml).join("")
      : `<tr><td colspan="6" style="text-align:center;padding:36px;color:var(--text-3)">${keyword || kind ? "没有匹配的分享" : "还没有分享链接，点右上角「导出」创建"}</td></tr>`;
    Common.renderPagination("shares-pagination", total, page, PAGE_SIZE, (p) => {
      page = p;
      renderRows();
    });
  }

  function statusOf(item) {
    if (item.revoked) return { cls: "disabled", text: "已撤销" };
    if (item.expired) return { cls: "failed", text: "已过期" };
    return { cls: "enabled", text: "有效" };
  }

  function rowHtml(item) {
    const status = statusOf(item);
    return `
      <tr>
        <td>${Common.escapeHtml(KIND_LABEL[item.kind] || item.kind)}</td>
        <td>
          <div class="es-target-title">${Common.escapeHtml(item.target_title || "（内容已删除）")}</div>
          <div class="es-target-id">#${item.target_id}</div>
        </td>
        <td><span class="status-badge ${status.cls}">${status.text}</span></td>
        <td>${Common.escapeHtml(Common.fmtTime(item.expires_at))}</td>
        <td>${Common.escapeHtml(Common.fmtTime(item.created_at))}</td>
        <td>
          <div class="es-row-actions">
            <button type="button" class="btn btn-sm" data-copy="${Common.escapeHtml(item.token)}">复制</button>
            <a class="btn btn-sm" href="${Common.escapeHtml(item.url)}" target="_blank" rel="noopener">打开</a>
            ${item.revoked ? "" : `<button type="button" class="btn btn-sm btn-danger" data-revoke="${Common.escapeHtml(item.token)}">撤销</button>`}
          </div>
        </td>
      </tr>`;
  }

  async function onAction(event) {
    const copyBtn = event.target.closest("[data-copy]");
    if (copyBtn) {
      const ok = await copyText(window.location.origin + "/share/" + copyBtn.dataset.copy);
      Common.toast(ok ? "链接已复制" : "复制失败，请手动复制", ok ? "success" : "error");
      return;
    }
    const revokeBtn = event.target.closest("[data-revoke]");
    if (revokeBtn) await revoke(revokeBtn.dataset.revoke);
  }

  async function revoke(token) {
    const item = items.find((s) => s.token === token);
    const ok = await Common.confirmDialog({
      title: "撤销分享链接",
      message: `撤销后「${item ? item.target_title : "该分享"}」的链接立即失效且不可恢复。确认撤销？`,
      okText: "撤销",
    });
    if (!ok) return;
    try {
      await API.request("DELETE", "/api/share/" + encodeURIComponent(token));
      Common.toast("分享链接已撤销", "success");
      load();
    } catch (err) {
      Common.alertDialog({ title: "撤销失败", message: err.message });
    }
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch {
      /* 回退到 execCommand */
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    area.remove();
    return ok;
  }

  return { render };
})();
