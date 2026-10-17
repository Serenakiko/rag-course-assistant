/* ============ 视图：回收站（管理台）============
 * 删除知识库时后端会先落一份快照（库 + 知识点 + 文档元信息，源文件移进 data/trash）。
 * 这里只负责列出快照、按名称/删除人搜索、恢复、彻底删除。
 */
const RecycleView = (() => {
  let items = [];

  function render(container) {
    container.innerHTML = `
      <div class="page-toolbar">
        <div class="toolbar-left" style="gap:10px">
          <select class="form-control" id="recycle-field" style="width:120px" aria-label="搜索范围">
            <option value="name">名称</option>
            <option value="user">删除人</option>
          </select>
          <div class="search-box" id="recycle-search-box">
            <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#8b867d" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
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
      </div>`;

    const input = document.getElementById("recycle-search");
    const box = document.getElementById("recycle-search-box");
    const syncClear = () => box.classList.toggle("has-value", input.value.length > 0);
    const rerun = () => { syncClear(); load(); };
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
    if (!items.length) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:36px;color:var(--text-3)">${keyword ? "没有匹配的记录" : "回收站是空的"}</td></tr>`;
      return;
    }
    tbody.innerHTML = items.map((it, i) => `
      <tr>
        <td>${i + 1}</td>
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
  }

  async function restore(item) {
    if (!item) return;
    const ok = await Common.confirmDialog({
      title: "恢复知识库",
      message: `将恢复「${Common.escapeHtml(item.name)}」：${item.points} 个知识点、${item.documents} 个文档，恢复时要重新生成向量，可能需要几秒。`,
      okText: "恢复",
      danger: false,
    });
    if (!ok) return;
    const btn = document.querySelector(`[data-restore="${item.id}"]`);
    if (btn) { btn.disabled = true; btn.textContent = "恢复中…"; }
    try {
      const r = await API.request("POST", `/api/admin/recycle/${item.id}/restore`);
      const dull = [];
      if (r.failed_points) dull.push(`${r.failed_points} 个知识点向量化失败`);
      if (r.failed_documents) dull.push(`${r.failed_documents} 个文档向量化失败`);
      Common.toast(
        `已恢复为「${r.name}」：知识点 ${r.points}，文档 ${r.documents}` +
          (dull.length ? `（${dull.join("，")}，可稍后重新解析）` : ""),
        dull.length ? "error" : "success"
      );
      load();
    } catch (err) {
      Common.toast(err.message, "error");
      if (btn) { btn.disabled = false; btn.textContent = "恢复"; }
    }
  }

  async function purge(item) {
    if (!item) return;
    const ok = await Common.confirmDialog({
      title: "彻底删除",
      message: `将永久删除「${Common.escapeHtml(item.name)}」的快照与源文件，恢复不了。确认？`,
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
