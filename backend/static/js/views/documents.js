/* ============ 视图：文档管理（搜索 + 表格） ============ */
const DocumentsView = (() => {
  let pollTimer = null;
  let pollCount = 0;   // 轮询次数上限：解析卡住时别让页面无限请求
  let docCache = [];
  let docPage = 1;
  let docPageSize = 10;

  function render(container) {
    container.innerHTML = `
      <div class="page-toolbar">
        <div class="toolbar-left" style="gap:10px">
          <select class="form-control" id="doc-field" style="width:132px" aria-label="搜索范围">
            <option value="filename">文件名</option>
            <option value="course">知识库</option>
            <option value="type">类型</option>
          </select>
          <div class="search-box" id="doc-search-box" style="width:300px">
            <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6f6a61" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            <input class="form-control" id="doc-search" placeholder="搜索文件名">
            <button type="button" class="search-clear" id="doc-search-clear" title="清空">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
            </button>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr><th>ID</th><th>文件名</th><th>所属知识库</th><th>类型</th><th>大小</th><th>分块数</th><th>上传时间</th><th>操作</th></tr>
            </thead>
            <tbody id="doc-tbody"><tr><td colspan="8" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr></tbody>
          </table>
        </div>
        <div class="pagination pagination-fixed" id="doc-pagination"></div>
      </div>`;

    const input = document.getElementById("doc-search");
    const box = document.getElementById("doc-search-box");
    const syncClear = () => box.classList.toggle("has-value", input.value.length > 0);
    const doDocSearch = () => { docPage = 1; renderDocTableRows(); };
    input.addEventListener("input", syncClear);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); doDocSearch(); }
    });
    Common.attachSearchButton(box, doDocSearch);
    document.getElementById("doc-search-clear").addEventListener("click", () => {
      input.value = ""; syncClear(); doDocSearch(); input.focus();
    });
    const docPlaceholders = { filename: "搜索文件名", course: "搜索知识库", type: "搜索类型（pdf / md）" };
    document.getElementById("doc-field").addEventListener("change", (e) => {
      input.placeholder = docPlaceholders[e.target.value] || "搜索…";
      doDocSearch();
    });

    loadDocuments();
  }

  async function loadDocuments() {
    const tbody = document.getElementById("doc-tbody");
    if (!tbody) return;
    try {
      docCache = await API.request("GET", "/api/documents");
      renderDocTableRows();
      if (docCache.some((d) => d.status === "processing")) startPolling();
      else stopPolling();
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:36px;color:var(--danger)">${Common.escapeHtml(err.message)}</td></tr>`;
    }
  }

  function filteredDocs() {
    const keyword = (document.getElementById("doc-search")?.value || "").trim().toLowerCase();
    if (!keyword) return docCache;
    const field = document.getElementById("doc-field")?.value || "filename";
    return docCache.filter((d) => {
      if (field === "course") return (d.course || "").toLowerCase().includes(keyword);
      if (field === "type") return (d.file_type || "").toLowerCase().includes(keyword);
      return (d.filename || "").toLowerCase().includes(keyword);
    });
  }

  function renderDocTableRows() {
    const tbody = document.getElementById("doc-tbody");
    if (!tbody) return;
    const keyword = (document.getElementById("doc-search")?.value || "").trim();
    const filtered = filteredDocs();
    const totalPages = Math.max(1, Math.ceil(filtered.length / docPageSize));
    if (docPage > totalPages) docPage = totalPages;
    const start = (docPage - 1) * docPageSize;
    const pageRows = filtered.slice(start, start + docPageSize);

    if (!pageRows.length) {
      tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:36px;color:var(--text-3)">${keyword ? "没有匹配的文档" : "暂无文档"}</td></tr>`;
    } else {
      tbody.innerHTML = pageRows.map((d, i) => `
        <tr>
          <td>${start + i + 1}</td>
          <td title="${Common.escapeHtml(d.error || "")}" style="max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${Common.escapeHtml(d.filename)}</td>
          <td>${Common.escapeHtml(d.course || "-")}</td>
          <td><span class="file-type-badge">${Common.escapeHtml(d.file_type.toUpperCase())}</span></td>
          <td>${Common.fmtSize(d.file_size)}</td>
          <td>${d.chunk_count || "-"}</td>
          <td class="muted">${Common.fmtTime(d.created_at)}</td>
          <td><button class="ops-link del" data-del="${d.id}">删除</button></td>
        </tr>`).join("");
    }

    tbody.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () => removeDoc(Number(b.dataset.del))));

    // 分页
    const pagEl = document.getElementById("doc-pagination");
    if (pagEl) {
      let pages = "";
      for (let p = 1; p <= totalPages; p++) {
        pages += `<button class="page-btn ${p === docPage ? "active" : ""}" data-page="${p}">${p}</button>`;
      }
      pagEl.innerHTML = `
        <span class="total">共 ${filtered.length} 条</span>
        <select id="doc-page-size">
          <option value="10" ${docPageSize === 10 ? "selected" : ""}>10条/页</option>
          <option value="20" ${docPageSize === 20 ? "selected" : ""}>20条/页</option>
        </select>
        <button class="page-btn" data-page="${docPage - 1}" ${docPage <= 1 ? "disabled" : ""}>‹</button>
        ${pages}
        <button class="page-btn" data-page="${docPage + 1}" ${docPage >= totalPages ? "disabled" : ""}>›</button>`;
      pagEl.querySelectorAll("[data-page]").forEach((b) => {
        b.addEventListener("click", () => {
          const p = Number(b.dataset.page);
          if (p >= 1 && p <= totalPages && p !== docPage) { docPage = p; renderDocTableRows(); }
        });
      });
      const sizeSel = pagEl.querySelector("#doc-page-size");
      if (sizeSel) {
        sizeSel.addEventListener("change", () => {
          const v = Number(sizeSel.value);
          if (v && v !== docPageSize) { docPageSize = v; docPage = 1; renderDocTableRows(); }
        });
      }
    }
  }

  async function removeDoc(id) {
    const ok = await Common.confirmDialog({
      title: "删除文档",
      message: "将删除该文档及其全部向量索引，删除后不可恢复。确认？",
      okText: "删除",
    });
    if (!ok) return;
    try {
      await API.request("DELETE", `/api/documents/${id}`);
      Common.toast("已删除", "success");
      loadDocuments();
    } catch (err) { Common.toast(err.message, "error"); }
  }

  function startPolling() {
    if (pollTimer) return;  // 已在轮询：不要重置计数，否则永远到不了上限
    pollCount = 0;
    pollTimer = setInterval(() => {
      pollCount += 1;
      if (pollCount > 30) {  // 60 秒还没解析完，停止轮询
        stopPolling();
        Common.toast("文档解析时间较长，稍后刷新页面查看结果", "error");
        return;
      }
      loadDocuments();
    }, 2000);
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function destroy() { stopPolling(); }

  return { render, destroy };
})();
