/* ============ 视图：知识库（总览卡片 → 知识点列表，所有登录用户可维护） ============
 * 权限规则：
 *   - 新增知识库 / 新增知识点：所有登录用户
 *   - 编辑 / 删除知识点、编辑或删除知识库：创建者本人 或 管理员
 */
const KnowledgeView = (() => {
  let currentBase = null;   // null=总览视图；否则为当前知识库对象
  let kbCache = [];         // 当前页知识库
  let kbTotal = 0;
  let kbPage = 1;
  let kbPageSize = 12;      // 前端知识库固定每页 12 条
  let kpRows = [];          // 当前页知识点
  let kpTotal = 0;
  let kpPage = 1;
  let kpPageSize = 12;

  function canManage(creatorId) {
    const user = API.getUser();
    if (!user) return false;
    if (user.role === "admin") return true;
    return creatorId != null && Number(creatorId) === Number(user.id);
  }

  /* 搜索栏 HTML（带图标 / 清空按钮），用于知识库与知识点搜索 */
  function searchBoxHtml(id, placeholder) {
    return `
      <div class="search-box" id="${id}-box">
        <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6b675f" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
        <input class="form-control" id="${id}" placeholder="${placeholder}">
        <button type="button" class="search-clear" id="${id}-clear" title="清空"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button>
      </div>`;
  }

  /* ==================== 渲染入口 ==================== */
  function render(container) {
    if (currentBase) renderDetail(container);
    else renderOverview(container);
  }

  /* ==================== 一级：知识库总览 ==================== */
  function renderOverview(container) {
    container.innerHTML = `
      <div class="page-toolbar">
        <div class="filters" style="gap:10px">
          <select class="form-control" id="kb-field" style="width:110px" aria-label="搜索范围">
            <option value="name">名称</option>
            <option value="description">简介</option>
          </select>
          ${searchBoxHtml("kb-search", "搜索知识库名称")}
        </div>
        <button class="btn btn-primary" id="kb-add"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14"/><path d="M5 12h14"/></svg>新建知识库</button>
      </div>
      <div class="kb-grid" id="kb-grid" style="margin-bottom:64px">
        <div style="grid-column:1/-1;text-align:center;padding:40px"><span class="loading-spinner"></span></div>
      </div>
      <div class="pagination pagination-fixed" id="kb-pagination"></div>`;

    const searchInput = document.getElementById("kb-search");
    const searchWrap = document.getElementById("kb-search-box");
    const syncClear = () => searchWrap.classList.toggle("has-value", searchInput.value.length > 0);
    const doSearch = () => { syncClear(); kbPage = 1; loadBases(); };
    searchInput.addEventListener("input", syncClear);
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); doSearch(); }
    });
    Common.attachSearchButton(searchWrap, doSearch);
    document.getElementById("kb-search-clear").addEventListener("click", () => {
      searchInput.value = ""; syncClear(); doSearch(); searchInput.focus();
    });
    const kbPlaceholders = { name: "搜索知识库名称", description: "搜索知识库简介" };
    document.getElementById("kb-field").addEventListener("change", (e) => {
      searchInput.placeholder = kbPlaceholders[e.target.value] || "搜索…";
      doSearch();
    });
    document.getElementById("kb-add").addEventListener("click", () => openBaseEditor(null));
    kbPage = 1;
    loadBases();
  }

  async function loadBases() {
    const grid = document.getElementById("kb-grid");
    if (!grid) return;
    const keyword = document.getElementById("kb-search")?.value.trim() || "";
    const field = document.getElementById("kb-field")?.value || "name";
    try {
      const params = new URLSearchParams({
        field,
        limit: String(kbPageSize),
        offset: String((kbPage - 1) * kbPageSize),
      });
      if (keyword) params.set("keyword", keyword);
      const data = await API.request("GET", "/api/knowledge/bases/page?" + params.toString());
      kbCache = data.items || [];
      kbTotal = data.total || 0;
      const totalPages = Math.max(1, Math.ceil(kbTotal / kbPageSize));
      if (kbPage > totalPages) { kbPage = totalPages; return loadBases(); }
      renderKbGrid();
    } catch (err) {
      grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1"><p>${Common.escapeHtml(err.message)}</p></div>`;
    }
  }

  function renderKbGrid() {
    const grid = document.getElementById("kb-grid");
    if (!grid) return;
    const keyword = document.getElementById("kb-search")?.value.trim() || "";

    if (!kbCache.length) {
      grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#8b867d" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
        <p>${keyword ? "没搜到相关的知识库，换个关键词试试" : "还没有知识库"}</p>
        ${!keyword ? `<p class="mt-8">点击右上角「新建知识库」，创建第一个课程知识库</p>` : ""}
      </div>`;
      Common.renderPagination("kb-pagination", 0, 1, kbPageSize, () => {});
      return;
    }

    grid.innerHTML = kbCache.map((b) => `
      <div class="kb-card" data-open="${b.id}">
        <div class="kb-card-head">
          <div class="kb-name">${Common.escapeHtml(b.name)}</div>
        </div>
        <div class="kb-desc ${b.description ? "" : "kb-desc-empty"}">${Common.escapeHtml(b.description || "暂无简介")}</div>
        <div class="kb-foot">
          <span class="kb-count">${b.point_count} 个知识点</span>
          ${canManage(b.created_by) ? `
            <div class="kb-ops">
              <button class="btn btn-sm" data-edit="${b.id}">编辑</button>
              <button class="btn btn-sm btn-danger" data-del="${b.id}">删除</button>
            </div>` : ""}
        </div>
      </div>`).join("");

    grid.querySelectorAll("[data-open]").forEach((card) =>
      card.addEventListener("click", (e) => {
        if (e.target.closest("button")) return; // 按钮事件单独处理
        openBase(Number(card.dataset.open));
      }));
    grid.querySelectorAll("[data-edit]").forEach((b) =>
      b.addEventListener("click", () => {
        const base = kbCache.find((x) => Number(x.id) === Number(b.dataset.edit));
        if (base) openBaseEditor(base);
      }));
    grid.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", () => {
        const base = kbCache.find((x) => Number(x.id) === Number(b.dataset.del));
        if (base) removeBase(base);
      }));

    Common.renderPagination("kb-pagination", kbTotal, kbPage, kbPageSize, (p) => {
      kbPage = p; loadBases();
    });
  }

  function openBase(id) {
    API.request("GET", "/api/knowledge/bases").then((bases) => {
      const base = bases.find((x) => Number(x.id) === Number(id));
      if (!base) { Common.toast("知识库不存在", "error"); return; }
      currentBase = base;
      kpPage = 1;
      renderDetail(document.getElementById("main-content"));
    }).catch((err) => Common.toast(err.message, "error"));
  }

  /* ---------- 知识库 新增 / 编辑 ---------- */
  function openBaseEditor(base) {
    const isEdit = !!base;
    Common.openModal(`
      <div class="modal-header">
        <h3>${isEdit ? "编辑知识库" : "新建知识库"}</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      <div class="form-group">
        <label>知识库名称 <span class="muted">（如：编译原理）</span></label>
        <input class="form-control" id="kb-name" maxlength="128" value="${isEdit ? Common.escapeHtml(base.name) : ""}" placeholder="请输入知识库名称">
      </div>
      <div class="form-group">
        <label>简介 <span class="muted">（可选）</span></label>
        <textarea class="form-control" id="kb-desc" rows="3" maxlength="1000" placeholder="简要说明该知识库涵盖的内容…">${isEdit ? Common.escapeHtml(base.description || "") : ""}</textarea>
      </div>
      <div class="modal-footer">
        <button class="btn" data-close>取消</button>
        <button class="btn btn-primary" id="kb-save">${isEdit ? "保存" : "创建"}</button>
      </div>
    `, { onOpen: (box) => {
      box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));
      box.querySelector("#kb-save").addEventListener("click", async () => {
        const name = box.querySelector("#kb-name").value.trim();
        if (!name) return Common.toast("请输入知识库名称", "error");
        const description = box.querySelector("#kb-desc").value.trim();
        const btn = box.querySelector("#kb-save");
        btn.disabled = true; btn.textContent = "保存中…";
        try {
          let created = null;
          if (isEdit) await API.request("PUT", `/api/knowledge/bases/${base.id}`, { name, description });
          else created = await API.request("POST", "/api/knowledge/bases", { name, description });
          Common.toast("保存成功", "success");
          Common.closeModal();
          if (!isEdit) kbPage = 1;
          loadBases();
          // 新建完成后直接接着传资料，不用再进详情页找上传
          if (created) setTimeout(() => showKpUploadModal(created), 120);
        } catch (err) {
          Common.toast(err.message, "error");
          btn.disabled = false; btn.textContent = isEdit ? "保存" : "创建";
        }
      });
    }});
  }

  async function removeBase(base) {
    const ok = await Common.confirmDialog({
      title: "删除知识库",
      message: `将删除知识库「${Common.escapeHtml(base.name)}」及其下 ${base.point_count} 个知识点与向量索引，删除后进入回收站，30 天内可由管理员恢复。确认？`,
      okText: "删除",
    });
    if (!ok) return;
    try {
      await API.request("DELETE", `/api/knowledge/bases/${base.id}`);
      Common.toast("已删除", "success");
      loadBases();
    } catch (err) { Common.toast(err.message, "error"); }
  }

  /* ==================== 二级：知识库详情（只展示知识点） ==================== */
  function renderDetail(container) {
    const base = currentBase;
    container.innerHTML = `
      <div class="kb-detail-head">
        <h2 class="kb-detail-title">${Common.escapeHtml(base.name)}</h2>
      </div>
      <div class="page-toolbar">
        <div class="toolbar-left" style="gap:10px">
          <button class="btn" id="kb-back">返回</button>
          ${searchBoxHtml("kp-search", "搜索知识点")}
        </div>
        <div class="toolbar-right">
          <button class="btn btn-primary" id="kp-upload"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5"/><path d="m5 12 7-7 7 7"/></svg>上传文档</button>
        </div>
      </div>
      <div class="kp-grid" id="kp-grid"><div style="grid-column:1/-1;text-align:center;padding:40px"><span class="loading-spinner"></span></div></div>
      <div class="pagination pagination-fixed" id="kp-pagination"></div>`;

    document.getElementById("kb-back").addEventListener("click", () => {
      currentBase = null;
      renderOverview(container);
    });

    document.getElementById("kp-upload").addEventListener("click", openKpUploadModal);

    const kpSearch = document.getElementById("kp-search");
    const kpWrap = document.getElementById("kp-search-box");
    const syncKpClear = () => kpWrap.classList.toggle("has-value", kpSearch.value.length > 0);
    const doKpSearch = () => { syncKpClear(); kpPage = 1; loadPoints(); };
    kpSearch.addEventListener("input", syncKpClear);
    kpSearch.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); doKpSearch(); }
    });
    Common.attachSearchButton(kpWrap, doKpSearch);
    document.getElementById("kp-search-clear").addEventListener("click", () => {
      kpSearch.value = ""; syncKpClear(); doKpSearch(); kpSearch.focus();
    });
    loadPoints();
  }

  /* ---------- 上传文档弹窗（学生端精简版：只选文件，归属当前知识库） ---------- */
  function openKpUploadModal() {
    showKpUploadModal();
  }

  function showKpUploadModal(targetBase) {
    const target = targetBase || currentBase;
    if (!target) return;
    Common.openModal(`
      <div class="modal-header">
        <h3>上传文档</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      <div class="form-group">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <button type="button" class="btn" id="up-pick">选择文件</button>
        </div>
        <input type="file" id="up-file-input" accept=".pdf,.txt,.md,.markdown,.csv" multiple style="display:none">
        <div class="form-hint">上传到「${Common.escapeHtml(target.name)}」，支持 pdf、txt、md、csv 格式，最大 20MB</div>
      </div>
      <div class="modal-footer">
        <button class="btn" data-close>取消</button>
        <button class="btn btn-primary" id="up-submit" disabled>上传</button>
      </div>
    `, { onOpen: (box) => {
      const input = box.querySelector("#up-file-input");
      box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));

      box.querySelector("#up-pick").addEventListener("click", () => input.click());
      input.addEventListener("change", () => {
        const files = Array.from(input.files || []);
        box.querySelector("#up-submit").disabled = files.length === 0;
      });
      box.querySelector("#up-submit").addEventListener("click", async () => {
        const files = Array.from(input.files || []);
        if (!files.length) return;
        Common.closeModal();
        await uploadToBase(files, target.name);
      });
    }});
  }

  /* 上传文档到指定知识库，入库后刷新知识点列表 */
  async function uploadToBase(files, courseOverride) {
    const course = courseOverride !== undefined ? courseOverride : (currentBase ? currentBase.name : "");
    if (!course) {
      Common.toast("请先选择知识库再上传", "error");
      return;
    }
    const list = files.filter((f) => /\.(pdf|txt|md|markdown|csv)$/i.test(f.name));
    if (!list.length) return;
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
    // 等文档解析入库后再刷新：在详情页刷知识点，在列表页刷卡片计数
    setTimeout(() => { if (currentBase) loadPoints(); else loadBases(); }, 2000);
  }

  async function loadPoints() {
    const grid = document.getElementById("kp-grid");
    if (!grid || !currentBase) return;
    const keyword = document.getElementById("kp-search")?.value.trim() || "";
    try {
      const params = new URLSearchParams({
        course: currentBase.name,
        limit: String(kpPageSize),
        offset: String((kpPage - 1) * kpPageSize),
      });
      if (keyword) params.set("keyword", keyword);
      const data = await API.request("GET", "/api/knowledge/points/page?" + params.toString());
      kpRows = data.items || [];
      kpTotal = data.total || 0;
      const totalPages = Math.max(1, Math.ceil(kpTotal / kpPageSize));
      if (kpPage > totalPages) { kpPage = totalPages; return loadPoints(); }
      renderPoints(keyword);
    } catch (err) {
      grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1"><p>${Common.escapeHtml(err.message)}</p></div>`;
    }
  }

  function renderPoints(keyword) {
    const grid = document.getElementById("kp-grid");
    if (!grid) return;

    if (!kpRows.length) {
      grid.innerHTML = keyword
        ? `<div class="empty-state" style="grid-column:1/-1"><p>没搜到相关的知识点，换个关键词试试</p></div>`
        : `<div class="empty-state" style="grid-column:1/-1">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#8b867d" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          <p>这个知识库还没有知识点</p>
          <p class="mt-8">点击右上角「上传文档」添加课程资料</p>
        </div>`;
      Common.renderPagination("kp-pagination", 0, 1, kpPageSize, () => {});
      return;
    }

    grid.innerHTML = kpRows.map((p) => `
      <div class="kp-card" data-view="${p.id}" title="点击查看完整内容">
        <div class="kp-head">
          <div class="kp-title">${Common.escapeHtml(p.title)}</div>
        </div>
        <div class="kp-content">${Common.escapeHtml(p.content)}</div>
        <div class="kp-foot">
          <div class="kp-tags">
            ${(p.tags || "").split(",").filter(Boolean).map((t) => `<span class="kp-tag">${Common.escapeHtml(t)}</span>`).join("")}
          </div>
          ${p.updater_name ? `<div class="muted" style="font-size:12px;flex:none" title="最后一次修改">改：${Common.escapeHtml(p.updater_name)} · ${Common.fmtTime(p.updated_at)}</div>` : ""}
        </div>
        <div class="kp-ops-row">
          ${canManage(p.created_by) ? `<button class="btn btn-sm" data-edit="${p.id}">编辑</button>
          <button class="btn btn-sm btn-danger" data-del="${p.id}">删除</button>` : ""}
        </div>
      </div>`).join("");

    // 卡片只展示前几行，点一下打开详情看完整内容（编辑/删除按钮不触发）
    grid.querySelectorAll(".kp-card[data-view]").forEach((card) => {
      card.addEventListener("click", (e) => {
        if (e.target.closest("[data-edit],[data-del]")) return;
        const point = kpRows.find((x) => Number(x.id) === Number(card.dataset.view));
        if (point) openPointDetail(point);
      });
    });
    grid.querySelectorAll("[data-edit]").forEach((b) => {
      b.addEventListener("click", () => {
        const point = kpRows.find((x) => Number(x.id) === Number(b.dataset.edit));
        if (point) openPointEditor(point);
      });
    });
    grid.querySelectorAll("[data-del]").forEach((b) => {
      b.addEventListener("click", () => {
        const point = kpRows.find((x) => Number(x.id) === Number(b.dataset.del));
        if (point) removePoint(point);
      });
    });

    Common.renderPagination("kp-pagination", kpTotal, kpPage, kpPageSize, (p) => {
      kpPage = p; loadPoints();
    });
  }

  /* 知识点详情：卡片里是截断的正文，这里给完整内容 */
  function openPointDetail(point) {
    const box = Common.openModal(`
      <div class="modal-header">
        <h3>${Common.escapeHtml(point.title)}</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      <div class="detail-meta">
        <div><span>所属知识库</span><b>${Common.escapeHtml(point.course || "-")}</b></div>
        <div><span>标签</span><b>${Common.escapeHtml(point.tags || "-")}</b></div>
        <div><span>更新时间</span><b>${Common.fmtTime(point.updated_at)}</b></div>
        <div><span>最后修改</span><b>${Common.escapeHtml(point.updater_name || point.creator_name || "-")}</b></div>
      </div>
      <div class="detail-text">${Common.escapeHtml(point.content || "")}</div>`,
      { size: "modal-lg" });
    box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));
  }

  /* ---------- 知识点 新增 / 编辑 ---------- */
  function openPointEditor(point) {
    const isEdit = !!point;
    Common.openModal(`
      <div class="modal-header">
        <h3>${isEdit ? "编辑知识点" : "添加知识点"}</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      <div class="form-group">
        <label>标题</label>
        <input class="form-control" id="kp-title" maxlength="255" value="${isEdit ? Common.escapeHtml(point.title) : ""}" placeholder="如：什么是二叉排序树">
      </div>
      <div class="row">
        <div class="form-group" style="flex:1">
          <label>所属知识库</label>
          <select class="form-control" id="kp-course"></select>
        </div>
        <div class="form-group" style="flex:1">
          <label>标签 <span class="muted">（逗号分隔）</span></label>
          <input class="form-control" id="kp-tags" value="${isEdit ? Common.escapeHtml(point.tags || "") : ""}" placeholder="如：树,查找">
        </div>
      </div>
      <div class="form-group">
        <label>内容</label>
        <textarea class="form-control" id="kp-content" rows="8" placeholder="填写知识点的完整讲解内容…">${isEdit ? Common.escapeHtml(point.content) : ""}</textarea>
      </div>
      <div class="modal-footer">
        <button class="btn" data-close>取消</button>
        <button class="btn btn-primary" id="kp-save">保存</button>
      </div>
    `, { onOpen: async (box) => {
      box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));
      // 课程下拉：默认当前知识库
      const sel = box.querySelector("#kp-course");
      try {
        const bases = await API.request("GET", "/api/knowledge/bases");
        if (!bases.length) {
          sel.innerHTML = '<option value="">（还没有知识库）</option>';
          Common.toast("还没有知识库，请先新建一个再添加知识点", "error");
        } else {
          const current = isEdit ? point.course : (currentBase ? currentBase.name : "");
          sel.innerHTML = bases.map((b) => {
            const val = Common.escapeHtml(b.name);
            return `<option value="${val}" ${b.name === current ? "selected" : ""}>${val}</option>`;
          }).join("");
        }
      } catch { /* 忽略 */ }
      box.querySelector("#kp-save").addEventListener("click", async () => {
        const title = box.querySelector("#kp-title").value.trim();
        const content = box.querySelector("#kp-content").value.trim();
        if (!title) return Common.toast("请输入标题", "error");
        if (!content) return Common.toast("请输入内容", "error");
        const course = box.querySelector("#kp-course").value;
        if (!course) return Common.toast("请选择所属知识库", "error");
        const payload = {
          title,
          content,
          course,
          tags: box.querySelector("#kp-tags").value.trim(),
        };
        const btn = box.querySelector("#kp-save");
        btn.disabled = true; btn.textContent = "保存中…";
        try {
          if (isEdit) await API.request("PUT", `/api/knowledge/points/${point.id}`, payload);
          else await API.request("POST", "/api/knowledge/points", payload);
          Common.toast("保存成功", "success");
          Common.closeModal();
          loadPoints();
        } catch (err) {
          Common.toast(err.message, "error");
          btn.disabled = false; btn.textContent = "保存";
        }
      });
    }});
  }

  async function removePoint(point) {
    const ok = await Common.confirmDialog({
      title: "删除知识点",
      message: `删除知识点「${Common.escapeHtml(point.title)}」后，将从知识库和向量索引中移除，确认？`,
      okText: "删除",
    });
    if (!ok) return;
    try {
      await API.request("DELETE", `/api/knowledge/points/${point.id}`);
      Common.toast("已删除", "success");
      loadPoints();
    } catch (err) { Common.toast(err.message, "error"); }
  }

  return { render };
})();
