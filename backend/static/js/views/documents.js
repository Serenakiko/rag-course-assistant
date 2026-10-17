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
        ${API.getUser()?.role === "admin" ? `
        <div class="toolbar-right">
          <button class="btn btn-primary" id="doc-upload">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14"/><path d="M5 12h14"/></svg>上传文档
          </button>
        </div>` : ""}
      </div>
      <div class="card">
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr><th>ID</th><th>文件名</th><th>所属知识库</th><th>类型</th><th>大小</th><th>上传时间</th><th>操作</th></tr>
            </thead>
            <tbody id="doc-tbody"><tr><td colspan="7" style="text-align:center;padding:36px"><span class="loading-spinner"></span></td></tr></tbody>
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

    const upBtn = document.getElementById("doc-upload");
    if (upBtn) upBtn.addEventListener("click", openDocUpload);

    loadDocuments();
  }

  /* 上传文档：先选知识库，再选文件（学生端的弹窗 + 一个库下拉） */
  async function openDocUpload() {
    let bases = [];
    try {
      bases = await API.request("GET", "/api/knowledge/bases");
    } catch (err) {
      Common.toast(err.message, "error");
      return;
    }
    const list = bases || [];
    const onlyNew = list.length === 0;   // 一个库都没有时，直接停在「新建」上
    const options = list
      .map((b) => `<option value="${Common.escapeHtml(b.name)}">${Common.escapeHtml(b.name)}</option>`)
      .join("");
    Common.openModal(`
      <div class="modal-header">
        <h3>上传文档</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      <div class="form-group">
        <label>上传到知识库</label>
        <select class="form-control" id="doc-up-course" style="max-width:240px">${options}<option value="__new__"${onlyNew ? " selected" : ""}>＋ 新建知识库…</option></select>
        <input class="form-control${onlyNew ? "" : " hidden"}" id="doc-up-newname" maxlength="128" placeholder="输入新知识库名称" style="margin-top:8px;max-width:240px">
        <div class="form-hint">选「＋ 新建知识库」时，会上传前先建好这个库。</div>
      </div>
      <div class="form-group">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <button type="button" class="btn" id="doc-up-pick">选择文件</button>
          <span class="muted" id="doc-up-files">未选择文件</span>
        </div>
        <input type="file" id="doc-up-input" accept=".pdf,.txt,.md,.markdown,.csv" multiple style="display:none">
        <div class="form-hint">支持 pdf、txt、md、csv 格式，单个文件最大 20MB</div>
      </div>
      <div class="modal-footer">
        <button class="btn" data-close>取消</button>
        <button class="btn btn-primary" id="doc-up-submit" disabled>上传</button>
      </div>
    `, { onOpen: (box) => {
      const input = box.querySelector("#doc-up-input");
      const label = box.querySelector("#doc-up-files");
      const submit = box.querySelector("#doc-up-submit");
      const courseSel = box.querySelector("#doc-up-course");
      const newName = box.querySelector("#doc-up-newname");
      const syncNewName = () => {
        const isNew = courseSel.value === "__new__";
        newName.classList.toggle("hidden", !isNew);
        if (isNew) newName.focus();
      };
      syncNewName();
      courseSel.addEventListener("change", syncNewName);
      box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));
      box.querySelector("#doc-up-pick").addEventListener("click", () => input.click());
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
        let course = courseSel.value;
        if (course === "__new__") {
          course = newName.value.trim();
          if (!course) return Common.toast("请输入新知识库名称", "error");
          try {
            await API.request("POST", "/api/knowledge/bases", { name: course, description: "" });
          } catch (err) {
            return Common.toast(`建库失败：${err.message}`, "error");
          }
        }
        submit.disabled = true;
        submit.textContent = "上传中…";
        let ok = 0;
        for (const file of files.filter((f) => /\.(pdf|txt|md|markdown|csv)$/i.test(f.name))) {
          const form = new FormData();
          form.append("file", file);
          form.append("course", course);
          try {
            await API.request("POST", "/api/documents/upload", form, { form: true });
            ok += 1;
          } catch (err) {
            Common.toast(`${file.name}：${err.message}`, "error");
          }
        }
        if (ok) Common.toast(`已提交 ${ok} 个文件，正在解析入库…`, "success");
        Common.closeModal();
        loadDocuments();
      });
    }});
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
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:36px;color:var(--danger)">${Common.escapeHtml(err.message)}</td></tr>`;
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
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:36px;color:var(--text-3)">${keyword ? "没有匹配的文档" : "暂无文档"}</td></tr>`;
    } else {
      tbody.innerHTML = pageRows.map((d, i) => `
        <tr>
          <td>${start + i + 1}</td>
          <td title="${Common.escapeHtml(d.error || "")}" style="max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${Common.escapeHtml(d.filename)}${d.status === "failed" ? ' <span class="file-type-badge" style="background:#f7ebe8;color:#a4442f">解析失败</span>' : d.status === "processing" ? ' <span class="file-type-badge">解析中</span>' : ""}</td>
          <td>${Common.escapeHtml(d.course || "-")}</td>
          <td><span class="file-type-badge">${Common.escapeHtml(d.file_type.toUpperCase())}</span></td>
          <td>${Common.fmtSize(d.file_size)}</td>
          <td class="muted">${Common.fmtTime(d.created_at)}</td>
          <td>
            ${d.status === "failed" ? `<button class="ops-link" data-retry="${d.id}">重新解析</button>` : ""}
            <button class="ops-link del" data-del="${d.id}">删除</button>
          </td>
        </tr>`).join("");
    }

    tbody.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () => removeDoc(Number(b.dataset.del))));
    tbody.querySelectorAll("[data-retry]").forEach((b) =>
      b.addEventListener("click", () => retryDoc(Number(b.dataset.retry))));

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

  async function retryDoc(id) {
    try {
      await API.request("POST", `/api/documents/${id}/reparse`);
      Common.toast("已重新提交解析", "success");
      loadDocuments();
    } catch (err) { Common.toast(err.message, "error"); }
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
