/* ============ 视图：回收站（管理台）============
 * 删除知识库时后端会先落一份快照（库 + 知识点 + 文档元信息，源文件移进 data/trash）。
 * 这里只负责列出快照、按名称/删除人搜索、恢复、彻底删除。
 */
const RecycleView = (() => {
  let items = [];
  let page = 1;
  let pageSize = 10;

  function render(container) {
    container.innerHTML = `
      <div class="page-toolbar">
        <div class="toolbar-left" style="gap:10px">
          <select class="form-control" id="recycle-field" style="width:120px" aria-label="搜索范围">
            <option value="name">名称</option>
            <option value="user">删除人</option>
          </select>
          <div class="search-box" id="recycle-search-box">
            <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            <input class="form-control" id="recycle-search" placeholder="搜索名称">
            <button type="button" class="search-clear" id="recycle-search-clear" title="清空"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>ID</th><th>名称</th><th>内容</th><th>删除人</th><th>删除时间</th><th>剩余</th><th>操作</th></tr></thead>
            <tbody id="recycle-tbody"><tr><td colspan="7" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr></tbody>
          </table>
        </div>
        <div class="pagination pagination-fixed" id="recycle-pagination"></div>
      </div>`;

    const input = document.getElementById("recycle-search");
    const box = document.getElementById("recycle-search-box");
    const syncClear = () => box.classList.toggle("has-value", input.value.length > 0);
    const rerun = () => { page = 1; syncClear(); load(); };
    input.addEventListener("input", syncClear);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); rerun(); }
    });
    Common.attachSearchButton(box, rerun);
    document.getElementById("recycle-search-clear").addEventListener("click", () => {
      input.value = "";
      syncClear();
      rerun();
      input.focus();
    });
    const placeholders = { name: "搜索名称", user: "搜索删除人" };
    document.getElementById("recycle-field").addEventListener("change", (e) => {
      input.placeholder = placeholders[e.target.value] || "搜索…";
      rerun();
    });

    load();
  }

  async function load() {
    const tbody = document.getElementById("recycle-tbody");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr>`;
    const params = new URLSearchParams({
      field: document.getElementById("recycle-field")?.value || "name",
    });
    const keyword = (document.getElementById("recycle-search")?.value || "").trim();
    if (keyword) params.set("q", keyword);
    try {
      const data = await API.request("GET", "/api/admin/recycle?" + params.toString());
      items = data.items || [];
      renderRows();
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:36px;color:var(--danger)">${Common.escapeHtml(err.message)}</td></tr>`;
    }
  }

  function renderRows() {
    const tbody = document.getElementById("recycle-tbody");
    if (!tbody) return;
    const keyword = (document.getElementById("recycle-search")?.value || "").trim();
    const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
    if (page > totalPages) page = totalPages;
    const start = (page - 1) * pageSize;
    const pageRows = items.slice(start, start + pageSize);

    if (!pageRows.length) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:36px;color:var(--text-3)">${keyword ? "没有匹配的记录" : "回收站是空的"}</td></tr>`;
      renderPager();
      return;
    }
    tbody.innerHTML = pageRows.map((it, i) => `
      <tr>
        <td>${start + i + 1}</td>
        <td>${Common.escapeHtml(it.name || "-")}</td>
        <td class="muted">${it.points} 个知识点 · ${it.documents} 个文档</td>
        <td>${Common.escapeHtml(it.deleter_name || "-")}</td>
        <td class="muted">${Common.fmtTime(it.deleted_at)}</td>
        <td class="muted">${it.days_left} 天</td>
        <td>
          <button class="ops-link" data-restore="${it.id}">恢复</button>
          <button class="ops-link del" data-purge="${it.id}">彻底删除</button>
        </td>
      </tr>`).join("");

    tbody.querySelectorAll("[data-restore]").forEach((b) =>
      b.addEventListener("click", () => restore(items.find((x) => x.id === Number(b.dataset.restore)))));
    tbody.querySelectorAll("[data-purge]").forEach((b) =>
      b.addEventListener("click", () => purge(items.find((x) => x.id === Number(b.dataset.purge)))));

    renderPager();
  }

  /* 底部固定的分页条：与用户管理一致（共 N 条 + 每页条数 + 页码） */
  function renderPager() {
    Common.renderPagination("recycle-pagination", items.length, page, pageSize, (p) => {
      page = p;
      renderRows();
    }, {
      sizeOptions: [10, 20],
      onSizeChange: (s) => { pageSize = s; page = 1; renderRows(); },
    });
  }

  async function restore(item) {
    if (!item) return;
    const isDocument = item.kind === "document";
    const ok = await Common.confirmDialog({
      title: isDocument ? "恢复文档" : "恢复知识库",
      message: isDocument
        ? `将恢复文档「${Common.escapeHtml(item.name)}」，恢复后需要重新解析并生成向量，可能需要几秒。`
        : `将恢复「${Common.escapeHtml(item.name)}」：${item.points} 个知识点、${item.documents} 个文档，恢复时要重新生成向量，可能需要几秒。`,
      okText: "恢复",
      danger: false,
    });
    if (!ok) return;
    const btn = document.querySelector(`[data-restore="${item.id}"]`);
    if (btn) { btn.disabled = true; btn.textContent = "恢复中…"; }
    try {
      const r = await API.request("POST", `/api/admin/recycle/${item.id}/restore`);
      handleRestoreResult(item, r);
      load();
    } catch (err) {
      // 恢复失败（409）时后端把 failed 清单放在 detail 里，不是「已恢复但有遗漏」
      const detail = err && err.body && err.body.detail;
      if (detail && typeof detail === "object" && Array.isArray(detail.failed)) {
        handleRestoreResult(item, detail);
        load();
        return;
      }
      Common.toast(err.message, "error");
      if (btn) { btn.disabled = false; btn.textContent = "恢复"; }
    }
  }

  function handleRestoreResult(item, r) {
    // 契约：failed: [{name, reason}]；老契约只有 failed_points / failed_documents 计数
    const failed = Array.isArray(r.failed) ? r.failed.slice() : [];
    if (!failed.length) {
      if (r.failed_points) failed.push({ name: `${r.failed_points} 个知识点`, reason: "向量化失败" });
      if (r.failed_documents) failed.push({ name: `${r.failed_documents} 个文档`, reason: "向量化失败" });
    }
    if (failed.length) {
      showRestoreFailed(item, r, failed);
    } else {
      Common.toast(`已恢复为「${r.name}」：知识点 ${r.points}，文档 ${r.documents}`, "success");
    }
  }

  /* 恢复可能部分失败：把失败条目和原因摆出来，并给一键重试（重试会重新走恢复） */
  function showRestoreFailed(item, r, failed) {
    const nothingRestored = r.ok === false || r.restored === false;
    const title = nothingRestored ? "恢复失败，未做任何改动" : "已恢复，但有内容没恢复完";
    const note = nothingRestored
      ? `「${Common.escapeHtml(r.name || item.name)}」没有恢复。原因：`
      : `「${Common.escapeHtml(r.name || item.name)}」已恢复，知识点 ${r.points || 0}、文档 ${r.documents || 0}。以下条目没能恢复，可以重试：`;
    const box = Common.openModal(`
      <div class="modal-header">
        <h3>${title}</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      <p class="modal-note">${note}</p>
      <div class="restore-failed-list">
        ${failed.map((f) => `
          <div class="restore-failed-row">
            <span class="restore-failed-name">${Common.escapeHtml(f.name || "-")}</span>
            <span class="restore-failed-reason">${Common.escapeHtml(f.reason || "未知原因")}</span>
          </div>`).join("")}
      </div>
      <div class="modal-footer">
        <button class="btn" data-close>稍后处理</button>
        <button class="btn btn-primary" data-retry>重试恢复</button>
      </div>`, { size: "modal-lg" });
    box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));
    box.querySelector("[data-retry]").addEventListener("click", () => {
      Common.closeModal();
      restore(item);
    });
  }

  async function purge(item) {
    if (!item) return;
    const ok = await Common.confirmDialog({
      title: "彻底删除",
      message: `将永久删除「${item.name}」的快照与源文件，恢复不了。确认？`,
      okText: "彻底删除",
    });
    if (!ok) return;
    try {
      await API.request("DELETE", `/api/admin/recycle/${item.id}`);
      Common.toast("已彻底删除", "success");
      load();
    } catch (err) {
      Common.toast(err.message, "error");
    }
  }

  return { render };
})();
