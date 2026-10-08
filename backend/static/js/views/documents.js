/* ============ 视图：文档管理（筛选 + 上传 + 表格） ============ */
const DocumentsView = (() => {
  let pollTimer = null;
  let docCache = [];
  let docPage = 1;
  let docPageSize = 10;

  function render(container) {
    container.innerHTML = `
      <div class="page-toolbar">
        <div class="toolbar-left">
          <select class="form-control" id="doc-course-filter" style="max-width:240px">
            <option value="">选择知识库筛选</option>
          </select>
        </div>
        <div class="toolbar-right">
          <button class="btn btn-light-blue" id="doc-upload-btn">↑ 上传文档</button>
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

    document.getElementById("doc-upload-btn").addEventListener("click", openUploadModal);

    // 加载知识库下拉
    API.request("GET", "/api/knowledge/courses").then((courses) => {
      const sel = document.getElementById("doc-course-filter");
      courses.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c; opt.textContent = c;
        sel.appendChild(opt);
      });
    }).catch(() => {});

    document.getElementById("doc-course-filter").addEventListener("change", () => {
      docPage = 1; renderDocTableRows();
    });

    loadDocuments();
  }

  function openUploadModal() {
    const curCourse = document.getElementById("doc-course-filter")?.value || "";
    API.request("GET", "/api/knowledge/courses")
      .then((courses) => showUploadModal(courses || [], curCourse))
      .catch(() => showUploadModal([], curCourse));
  }

  function showUploadModal(courses, curCourse) {
    const opts = courses.length
      ? courses.map((c) =>
          `<option value="${Common.escapeHtml(c)}" ${c === curCourse ? "selected" : ""}>${Common.escapeHtml(c)}</option>`).join("")
      : `<option value="">暂无知识库</option>`;
    Common.openModal(`
      <div class="modal-header">
        <h3>上传文档</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      <div class="form-group">
        <label>选择知识库</label>
        <div style="display:flex;gap:8px;align-items:center">
          <select class="form-control" id="up-course" style="flex:1">${opts}</select>
          <button type="button" class="btn" id="up-new-base" style="white-space:nowrap">＋ 新建知识库</button>
        </div>
        <div id="up-new-box" style="display:none;margin-top:10px;background:#f8fafc;border:1px solid #eef2f7;border-radius:10px;padding:12px 14px">
          <label style="display:block;margin-bottom:6px;font-weight:500;color:var(--text-2);font-size:13px">新知识库名称</label>
          <div style="display:flex;gap:8px">
            <input class="form-control" id="up-new-name" placeholder="例如：计算机网络" style="flex:1">
            <button type="button" class="btn btn-primary" id="up-new-ok" style="white-space:nowrap">创建</button>
          </div>
        </div>
      </div>
      <div class="form-group">
        <label>选择文件</label>
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <button type="button" class="btn" id="up-pick">选择文件</button>
          <span class="muted" id="up-files" style="font-size:13px"></span>
        </div>
        <input type="file" id="up-file-input" accept=".pdf,.txt,.md,.markdown,.csv" multiple style="display:none">
        <div class="muted" style="margin-top:8px;font-size:12.5px">支持 pdf、txt、md、csv 格式，最大 20MB</div>
      </div>
      <div class="modal-footer">
        <button class="btn" data-close>取消</button>
        <button class="btn btn-primary" id="up-submit" disabled>上传</button>
      </div>
    `, { onOpen: (box) => {
      const input = box.querySelector("#up-file-input");
      const newBox = box.querySelector("#up-new-box");
      const newName = box.querySelector("#up-new-name");
      box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));

      async function refreshCourses(selected) {
        const list = await API.request("GET", "/api/knowledge/courses");
        const sel = box.querySelector("#up-course");
        sel.innerHTML = list.length
          ? list.map((c) =>
              `<option value="${Common.escapeHtml(c)}" ${c === selected ? "selected" : ""}>${Common.escapeHtml(c)}</option>`).join("")
          : `<option value="">暂无知识库</option>`;
      }

      box.querySelector("#up-new-base").addEventListener("click", () => {
        const show = newBox.style.display === "none";
        newBox.style.display = show ? "" : "none";
        if (show) newName.focus();
      });
      async function createBase() {
        const name = newName.value.trim();
        if (!name) { Common.toast("请输入知识库名称", "error"); return; }
        const okBtn = box.querySelector("#up-new-ok");
        okBtn.disabled = true;
        try {
          await API.request("POST", "/api/knowledge/bases", { name, description: "" });
          Common.toast("知识库已创建", "success");
          await refreshCourses(name);
          newName.value = "";
          newBox.style.display = "none";
        } catch (err) {
          Common.toast(err.message === "已存在同名知识库"
            ? "同名知识库已存在，直接在列表中选择即可"
            : err.message, "error");
        } finally {
          okBtn.disabled = false;
        }
      }
      box.querySelector("#up-new-ok").addEventListener("click", createBase);
      newName.addEventListener("keydown", (e) => { if (e.key === "Enter") createBase(); });

      box.querySelector("#up-pick").addEventListener("click", () => input.click());
      input.addEventListener("change", () => {
        const files = Array.from(input.files || []);
        box.querySelector("#up-files").textContent = files.length
          ? `已选 ${files.length} 个文件：${files.map((f) => f.name).join("、")}`
          : "";
        box.querySelector("#up-submit").disabled = files.length === 0;
      });
      box.querySelector("#up-submit").addEventListener("click", async () => {
        const files = Array.from(input.files || []);
        if (!files.length) return;
        const course = box.querySelector("#up-course").value;
        Common.closeModal();
        await uploadFiles(files, course);
      });
    }});
  }

  async function uploadFiles(files, courseOverride) {
    const list = Array.from(files).filter((f) => {
      const ok = /\.(pdf|txt|md|markdown|csv)$/i.test(f.name);
      if (!ok) Common.toast(`跳过不支持的文件：${f.name}`, "error");
      return ok;
    });
    if (!list.length) return;
    const course = courseOverride !== undefined ? courseOverride : (document.getElementById("doc-course-filter")?.value || "");
    for (const file of list) {
      const form = new FormData();
      form.append("file", file);
      if (course) form.append("course", course);
      try {
        await API.request("POST", "/api/documents/upload", form, { form: true });
        Common.toast(`已提交：${file.name}，正在解析入库…`, "success");
      } catch (err) {
        Common.toast(`${file.name}：${err.message}`, "error");
      }
    }
    loadDocuments();
    startPolling();
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

  function renderDocTableRows() {
    const tbody = document.getElementById("doc-tbody");
    if (!tbody) return;
    const course = document.getElementById("doc-course-filter")?.value || "";
    const filtered = course ? docCache.filter((d) => (d.course || "") === course) : docCache;
    const totalPages = Math.max(1, Math.ceil(filtered.length / docPageSize));
    if (docPage > totalPages) docPage = totalPages;
    const start = (docPage - 1) * docPageSize;
    const pageRows = filtered.slice(start, start + docPageSize);

    if (!pageRows.length) {
      tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:36px;color:var(--text-3)">暂无文档</td></tr>`;
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
    stopPolling();
    pollTimer = setInterval(() => {
      loadDocuments().catch(() => stopPolling());
    }, 2000);
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function destroy() { stopPolling(); }

  return { render, destroy };
})();
