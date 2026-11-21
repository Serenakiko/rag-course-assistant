/* 课程与班级：管理员维护学期/课程/教学班，学生只读我的课程。 */
const CoursesView = (() => {
  const H = () => Common.escapeHtml;
  let semesters = [];
  let courses = [];
  let classes = [];
  let activeCourseId = null;
  let tab = "semesters";

  function isAdmin() {
    const u = API.getUser();
    return u && u.role === "admin";
  }

  async function loadSemesters() {
    const data = await API.request("GET", "/api/courses/semesters");
    semesters = data.items || [];
  }

  async function loadCourses() {
    const data = await API.request("GET", "/api/courses/courses");
    courses = data.items || [];
  }

  async function loadClasses(courseId) {
    activeCourseId = courseId;
    if (!courseId) { classes = []; return; }
    const data = await API.request("GET", `/api/courses/courses/${courseId}/classes`);
    classes = data.items || [];
  }


  /* 统一表单弹窗：标题 + 内容 + 取消/保存 */
  function formModal({ title, bodyHtml, okText = "保存", onSave }) {
    const box = Common.openModal(`
      <h3>${H()(title)}</h3>
      <div class="modal-form">${bodyHtml}</div>
      <div class="modal-footer">
        <button class="btn" data-cancel>取消</button>
        <button class="btn btn-primary" data-ok>${H()(okText)}</button>
      </div>`);
    box.querySelector("[data-cancel]").onclick = () => Common.closeModal();
    box.querySelector("[data-ok]").onclick = async () => {
      const btn = box.querySelector("[data-ok]");
      btn.disabled = true;
      try {
        const ok = await onSave(box);
        if (ok !== false) Common.closeModal(true);
      } finally {
        btn.disabled = false;
      }
    };
    return box;
  }

  /* ---------------- 学生视图 ---------------- */

  async function renderStudent(container) {
    let items = [];
    try {
      const data = await API.request("GET", "/api/courses/my/courses");
      items = data.items || [];
    } catch (err) {
      container.innerHTML = `<div class="empty-state"><p>${H()(err.message)}</p></div>`;
      return;
    }
    if (!items.length) {
      container.innerHTML = `
        <div class="empty-state">
          <p>你还没有加入任何课程</p>
          <p class="muted">请联系任课老师或管理员把你加入教学班。</p>
        </div>`;
      return;
    }
    container.innerHTML = `
      <div class="course-grid">
        ${items.map((c) => `
          <div class="card course-card">
            <div class="row-between">
              <b>${H()(c.name)}</b>
              <span class="badge">${H()(c.code)}</span>
            </div>
            <div class="muted" style="margin:8px 0">${H()(c.description || "暂无课程简介")}</div>
            <div class="course-meta">
              ${c.semester_name ? `<span>学期：${H()(c.semester_name)}</span>` : ""}
              <span>班级：${c.classes.map((x) => H()(x.name)).join("、")}</span>
            </div>
            ${c.knowledge_base ? `
              <button class="btn btn-sm course-kb" data-kb="${H()(c.knowledge_base.name)}">
                查看课程知识库：${H()(c.knowledge_base.name)}
              </button>` : `<div class="muted">该课程暂未绑定知识库</div>`}
          </div>`).join("")}
      </div>`;
    container.querySelectorAll(".course-kb").forEach((btn) => {
      btn.addEventListener("click", () => {
        // 直接跳到问答页并用课程名限定检索范围
        window.location.hash = "#/chat";
        setTimeout(() => {
          const picker = document.getElementById("chat-course-select");
          if (picker) {
            picker.value = btn.dataset.kb;
            picker.dispatchEvent(new Event("change"));
          }
        }, 120);
      });
    });
  }

  /* ---------------- 管理员视图 ---------------- */

  function renderAdmin(container) {
    container.innerHTML = `
      <div class="page-toolbar">
        <div class="fb-tabs" id="courses-tabs">
          <button type="button" class="fb-tab ${tab === "semesters" ? "active" : ""}" data-tab="semesters">学期</button>
          <button type="button" class="fb-tab ${tab === "courses" ? "active" : ""}" data-tab="courses">课程</button>
          <button type="button" class="fb-tab ${tab === "classes" ? "active" : ""}" data-tab="classes">班级与成员</button>
        </div>
        <div class="toolbar-right" id="courses-toolbar"></div>
      </div>
      <div id="courses-body"></div>`;
    container.querySelectorAll("[data-tab]").forEach((btn) => {
      btn.addEventListener("click", () => { tab = btn.dataset.tab; renderAdmin(container); });
    });
    if (tab === "semesters") renderSemesters(container);
    else if (tab === "courses") renderCourses(container);
    else renderClasses(container);
  }

  async function renderSemesters(container) {
    const body = container.querySelector("#courses-body");
    const toolbar = container.querySelector("#courses-toolbar");
    toolbar.innerHTML = `<button class="btn btn-primary" id="sem-add">+ 新建学期</button>`;
    body.innerHTML = `<div class="card"><div class="table-wrap"><table class="table">
      <thead><tr><th>ID</th><th>名称</th><th>起止</th><th>当前</th><th>课程数</th><th>操作</th></tr></thead>
      <tbody id="sem-tbody"><tr><td colspan="6" style="text-align:center;padding:32px"><span class="loading-spinner"></span></td></tr></tbody>
    </table></div></div>`;
    toolbar.querySelector("#sem-add").addEventListener("click", () => editSemester(null, container));
    try {
      await loadSemesters();
    } catch (err) {
      Common.toast(err.message, "error");
      return;
    }
    const tbody = document.getElementById("sem-tbody");
    tbody.innerHTML = semesters.length ? semesters.map((s) => `
      <tr>
        <td>${s.id}</td><td>${H()(s.name)}</td>
        <td>${H()(s.start_date || "—")} ~ ${H()(s.end_date || "—")}</td>
        <td>${s.is_active ? '<span class="badge badge-green">当前</span>' : "—"}</td>
        <td>${s.course_count}</td>
        <td class="row-actions">
          <button class="btn btn-sm" data-edit="${s.id}">编辑</button>
          <button class="btn btn-sm btn-danger" data-del="${s.id}">删除</button>
        </td>
      </tr>`).join("") : `<tr><td colspan="6" class="empty-cell">还没有学期</td></tr>`;
    tbody.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => {
      editSemester(semesters.find((x) => x.id === Number(b.dataset.edit)), container);
    }));
    tbody.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => {
      const row = semesters.find((x) => x.id === Number(b.dataset.del));
      Common.confirmDialog({ title: "删除学期", message: `确定删除「${row.name}」？` }).then(async (ok) => {
        if (!ok) return;
        try {
          await API.request("DELETE", `/api/courses/semesters/${row.id}`);
          Common.toast("已删除");
          renderAdmin(container);
        } catch (err) { Common.toast(err.message, "error"); }
      });
    }));
  }

  function editSemester(row, container) {
    const isNew = !row;
    const html = `
      <div class="form-group"><label>学期名称</label>
        <input class="form-control" id="sem-name" value="${row ? H()(row.name) : ""}" placeholder="如 2026 秋季学期"></div>
      <div class="form-group"><label>开始日期</label>
        <input class="form-control" id="sem-start" type="date" value="${row ? H()(row.start_date) : ""}"></div>
      <div class="form-group"><label>结束日期</label>
        <input class="form-control" id="sem-end" type="date" value="${row ? H()(row.end_date) : ""}"></div>
      <label class="checkbox-line"><input type="checkbox" id="sem-active" ${row && row.is_active ? "checked" : ""}> 设为当前学期</label>`;
    formModal({
      title: isNew ? "新建学期" : "编辑学期",
      bodyHtml: html,
      onSave: async (modal) => {
        const payload = {
          name: modal.querySelector("#sem-name").value.trim(),
          start_date: modal.querySelector("#sem-start").value,
          end_date: modal.querySelector("#sem-end").value,
          is_active: modal.querySelector("#sem-active").checked,
        };
        if (!payload.name) { Common.toast("学期名称不能为空", "error"); return false; }
        try {
          if (isNew) await API.request("POST", "/api/courses/semesters", payload);
          else await API.request("PUT", `/api/courses/semesters/${row.id}`, payload);
          Common.toast("已保存");
          renderAdmin(container);
          return true;
        } catch (err) { Common.toast(err.message, "error"); return false; }
      },
    });
  }

  async function renderCourses(container) {
    const body = container.querySelector("#courses-body");
    const toolbar = container.querySelector("#courses-toolbar");
    toolbar.innerHTML = `<button class="btn btn-primary" id="course-add">+ 新建课程</button>`;
    body.innerHTML = `<div class="card"><div class="table-wrap"><table class="table">
      <thead><tr><th>ID</th><th>编号</th><th>课程</th><th>学期</th><th>绑定知识库</th><th>班级数</th><th>操作</th></tr></thead>
      <tbody id="course-tbody"><tr><td colspan="7" style="text-align:center;padding:32px"><span class="loading-spinner"></span></td></tr></tbody>
    </table></div></div>`;
    toolbar.querySelector("#course-add").addEventListener("click", () => editCourse(null, container));
    try {
      await Promise.all([loadCourses(), loadSemesters(), loadKnowledgeBases()]);
    } catch (err) { Common.toast(err.message, "error"); return; }
    const tbody = document.getElementById("course-tbody");
    tbody.innerHTML = courses.length ? courses.map((c) => `
      <tr>
        <td>${c.id}</td><td>${H()(c.code)}</td><td>${H()(c.name)}</td>
        <td>${H()(c.semester_name || "—")}</td>
        <td>${c.knowledge_base_id ? H()(c.knowledge_base_name) : '<span class="muted">未绑定</span>'}</td>
        <td>${c.class_count}</td>
        <td class="row-actions">
          <button class="btn btn-sm" data-edit="${c.id}">编辑</button>
          <button class="btn btn-sm btn-danger" data-del="${c.id}">删除</button>
        </td>
      </tr>`).join("") : `<tr><td colspan="7" class="empty-cell">还没有课程</td></tr>`;
    tbody.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => {
      editCourse(courses.find((x) => x.id === Number(b.dataset.edit)), container);
    }));
    tbody.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => {
      const row = courses.find((x) => x.id === Number(b.dataset.del));
      Common.confirmDialog({ title: "删除课程", message: `确定删除「${row.name}」？该课程下的班级会一起删除。` }).then(async (ok) => {
        if (!ok) return;
        try {
          await API.request("DELETE", `/api/courses/courses/${row.id}`);
          Common.toast("已删除");
          renderAdmin(container);
        } catch (err) { Common.toast(err.message, "error"); }
      });
    }));
  }

  let knowledgeBases = [];
  async function loadKnowledgeBases() {
    try {
      const data = await API.request("GET", "/api/knowledge/bases");
      knowledgeBases = data.items || data || [];
    } catch { knowledgeBases = []; }
  }

  function editCourse(row, container) {
    const isNew = !row;
    const semOptions = semesters.map((s) => `<option value="${s.id}" ${row && row.semester_id === s.id ? "selected" : ""}>${H()(s.name)}</option>`).join("");
    const kbOptions = knowledgeBases.map((b) => `<option value="${b.id}" ${row && row.knowledge_base_id === b.id ? "selected" : ""}>${H()(b.name)}</option>`).join("");
    const html = `
      <div class="form-group"><label>课程编号</label>
        <input class="form-control" id="course-code" value="${row ? H()(row.code) : ""}" placeholder="如 CS101"></div>
      <div class="form-group"><label>课程名称</label>
        <input class="form-control" id="course-name" value="${row ? H()(row.name) : ""}" placeholder="如 数据结构"></div>
      <div class="form-group"><label>所属学期</label>
        <select class="form-control" id="course-semester"><option value="">未指定</option>${semOptions}</select></div>
      <div class="form-group"><label>绑定知识库</label>
        <select class="form-control" id="course-kb"><option value="">不绑定</option>${kbOptions}</select></div>
      <div class="form-group"><label>课程简介</label>
        <textarea class="form-control" id="course-desc" rows="3">${row ? H()(row.description) : ""}</textarea></div>`;
    formModal({
      title: isNew ? "新建课程" : "编辑课程",
      bodyHtml: html,
      onSave: async (modal) => {
        const payload = {
          code: modal.querySelector("#course-code").value.trim(),
          name: modal.querySelector("#course-name").value.trim(),
          semester_id: modal.querySelector("#course-semester").value ? Number(modal.querySelector("#course-semester").value) : null,
          knowledge_base_id: modal.querySelector("#course-kb").value ? Number(modal.querySelector("#course-kb").value) : null,
          description: modal.querySelector("#course-desc").value.trim(),
        };
        if (!payload.code || !payload.name) { Common.toast("编号与名称不能为空", "error"); return false; }
        try {
          if (isNew) await API.request("POST", "/api/courses/courses", payload);
          else await API.request("PUT", `/api/courses/courses/${row.id}`, payload);
          Common.toast("已保存");
          renderAdmin(container);
          return true;
        } catch (err) { Common.toast(err.message, "error"); return false; }
      },
    });
  }

  async function renderClasses(container) {
    const body = container.querySelector("#courses-body");
    const toolbar = container.querySelector("#courses-toolbar");
    toolbar.innerHTML = "";
    body.innerHTML = `
      <div class="courses-layout">
        <div class="card course-picker">
          <b>选择课程</b>
          <div id="class-course-list" style="margin-top:10px"></div>
        </div>
        <div class="card" style="flex:1">
          <div class="row-between" id="class-head"><b>班级</b><button class="btn btn-sm btn-primary" id="class-add" disabled>+ 新建班级</button></div>
          <div id="class-list" style="margin-top:10px"></div>
          <div id="member-panel" style="margin-top:16px"></div>
        </div>
      </div>`;
    try {
      await loadCourses();
    } catch (err) { Common.toast(err.message, "error"); return; }
    const list = document.getElementById("class-course-list");
    if (!courses.length) { list.innerHTML = `<div class="muted">请先创建课程</div>`; return; }
    if (!activeCourseId) activeCourseId = courses[0].id;
    list.innerHTML = courses.map((c) => `
      <button class="course-pick ${c.id === activeCourseId ? "active" : ""}" data-course="${c.id}">${H()(c.name)}</button>`).join("");
    list.querySelectorAll("[data-course]").forEach((b) => b.addEventListener("click", async () => {
      await loadClasses(Number(b.dataset.course));
      renderClasses(container);
    }));
    await loadClasses(activeCourseId);
    const head = document.getElementById("class-head");
    head.querySelector("#class-add").disabled = false;
    head.querySelector("#class-add").addEventListener("click", () => editClass(container));
    const box = document.getElementById("class-list");
    box.innerHTML = classes.length ? classes.map((c) => `
      <div class="class-row">
        <div><b>${H()(c.name)}</b><span class="muted"> · ${c.member_count} 人</span></div>
        <div class="row-actions">
          <button class="btn btn-sm" data-members="${c.id}">成员</button>
          <button class="btn btn-sm btn-danger" data-delclass="${c.id}">删除</button>
        </div>
      </div>`).join("") : `<div class="muted">该课程下还没有班级</div>`;
    box.querySelectorAll("[data-members]").forEach((b) => b.addEventListener("click", () => renderMembers(Number(b.dataset.members))));
    box.querySelectorAll("[data-delclass]").forEach((b) => b.addEventListener("click", () => {
      const row = classes.find((x) => x.id === Number(b.dataset.delclass));
      Common.confirmDialog({ title: "删除班级", message: `确定删除「${row.name}」？成员会一并移除。` }).then(async (ok) => {
        if (!ok) return;
        try {
          await API.request("DELETE", `/api/courses/classes/${row.id}`);
          Common.toast("已删除");
          renderClasses(container);
        } catch (err) { Common.toast(err.message, "error"); }
      });
    }));
  }

  function editClass(container) {
    const html = `<div class="form-group"><label>班级名称</label>
      <input class="form-control" id="class-name" placeholder="如 2026 秋 1 班"></div>`;
    formModal({
      title: "新建班级",
      bodyHtml: html,
      okText: "创建",
      onSave: async (modal) => {
        const name = modal.querySelector("#class-name").value.trim();
        if (!name) { Common.toast("班级名称不能为空", "error"); return false; }
        try {
          await API.request("POST", `/api/courses/courses/${activeCourseId}/classes`, { name });
          Common.toast("已创建");
          renderClasses(container);
          return true;
        } catch (err) { Common.toast(err.message, "error"); return false; }
      },
    });
  }

  async function renderMembers(classId) {
    const panel = document.getElementById("member-panel");
    panel.innerHTML = `<div class="loading-spinner"></div>`;
    try {
      const data = await API.request("GET", `/api/courses/classes/${classId}/members`);
      const items = data.items || [];
      panel.innerHTML = `
        <div class="row-between" style="margin-bottom:8px">
          <b>班级成员（${items.length}）</b>
          <div style="display:flex;gap:8px">
            <input class="form-control" id="member-account" placeholder="输入学号添加" style="width:180px">
            <button class="btn btn-sm btn-primary" id="member-add">添加</button>
          </div>
        </div>
        <div class="table-wrap"><table class="table">
          <thead><tr><th>学号</th><th>姓名</th><th>加入时间</th><th>操作</th></tr></thead>
          <tbody>${items.length ? items.map((m) => `
            <tr><td>${H()(m.account)}</td><td>${H()(m.username)}</td>
              <td>${H()(Common.fmtTime(m.joined_at))}</td>
              <td><button class="btn btn-sm btn-danger" data-remove="${m.user_id}">移出</button></td></tr>`).join("")
            : `<tr><td colspan="4" class="empty-cell">班级还没有成员</td></tr>`}</tbody>
        </table></div>`;
      document.getElementById("member-add").addEventListener("click", async () => {
        const account = document.getElementById("member-account").value.trim();
        if (!account) return;
        try {
          const res = await API.request("POST", `/api/courses/classes/${classId}/members`, { account });
          Common.toast(res.added ? "已添加" : "该学生已在班级中");
          renderMembers(classId);
        } catch (err) { Common.toast(err.message, "error"); }
      });
      panel.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", async () => {
        try {
          await API.request("DELETE", `/api/courses/classes/${classId}/members/${b.dataset.remove}`);
          Common.toast("已移出");
          renderMembers(classId);
        } catch (err) { Common.toast(err.message, "error"); }
      }));
    } catch (err) {
      panel.innerHTML = `<div class="muted">${H()(err.message)}</div>`;
    }
  }

  function render(container) {
    tab = "semesters";
    activeCourseId = null;
    if (isAdmin()) renderAdmin(container);
    else renderStudent(container);
  }

  return { render };
})();
