/* 导出与分享：会话/知识库导出，生成可撤销分享链接。 */
const ExportShareView = (() => {
  const state = {
    kind: "conversation",
    isAdmin: false,
    targets: [],
  };

  function el(id) {
    return document.getElementById(id);
  }

  function selectedTargetId() {
    const select = el("es-target");
    const value = select ? Number(select.value) : NaN;
    return Number.isInteger(value) && value > 0 ? value : null;
  }

  function fill(root, inModal = false) {
    const user = (typeof API !== "undefined" && API.getUser && API.getUser()) || {};
    state.isAdmin = user.role === "admin";
    state.kind = "conversation";
    state.targets = [];

    root.innerHTML = `
      <style>
        .es-field { max-width: none; }
        .es-field select { width: 100%; }
        .es-secondary { display: flex; gap: 8px; margin-top: 16px; }
        .es-secondary .btn { flex: 1; }
        .es-create { margin-top: 10px; }
        .es-created { display: flex; gap: 10px; align-items: flex-end; margin-top: 4px; padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-2); }
        .es-created[hidden] { display: none; }
        .es-created-text { flex: 1; min-width: 0; }
        .es-created-title { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
        .es-created-actions { display: flex; gap: 8px; flex-shrink: 0; }
        @media (max-width: 640px) {
          .es-field { max-width: none; }
          .es-created { flex-direction: column; align-items: stretch; }
          .es-created-actions .btn { flex: 1; }
        }
      </style>
      ${inModal ? "" : `
      <div class="row-between" style="margin-bottom:12px">
        <span class="muted" style="font-size:13px">导出 Markdown / JSON，或生成可撤销的只读分享链接。</span>
      </div>`}

      <section>
        <div class="form-group es-field">
          <label for="es-kind">内容类型</label>
          <select class="form-control" id="es-kind">
            <option value="conversation">会话记录</option>
            ${state.isAdmin ? `<option value="knowledge_base">知识库</option>` : ""}
          </select>
        </div>
        <div class="form-group es-field">
          <label for="es-target">选择内容</label>
          <select class="form-control" id="es-target" disabled>
            <option value="">加载中…</option>
          </select>
        </div>
        <div class="form-group es-field">
          <label for="es-expires">分享有效期</label>
          <select class="form-control" id="es-expires">
            <option value="24">24 小时</option>
            <option value="72" selected>3 天（72 小时）</option>
            <option value="168">7 天</option>
            <option value="720">30 天</option>
          </select>
        </div>
        <div class="es-created" id="es-created" hidden>
          <div class="es-created-text">
            <div class="es-created-title">分享链接已创建</div>
            <input class="form-control" id="es-created-url" readonly aria-label="分享链接">
          </div>
          <div class="es-created-actions">
            <button type="button" class="btn btn-sm" id="es-created-copy">复制</button>
            <button type="button" class="btn btn-sm" id="es-created-close">关闭</button>
          </div>
        </div>
        <div class="es-secondary">
          <button type="button" class="btn" id="es-export-md" disabled>导出 Markdown</button>
          <button type="button" class="btn" id="es-export-json" disabled>导出 JSON</button>
        </div>
        <button type="button" class="btn btn-primary btn-block es-create" id="es-create" disabled>创建分享链接</button>
      </section>`;

    el("es-kind").addEventListener("change", (e) => {
      state.kind = e.target.value === "knowledge_base" ? "knowledge_base" : "conversation";
      syncKindUI();
      loadTargets();
    });
    el("es-target").addEventListener("change", syncButtons);
    el("es-export-md").addEventListener("click", () => exportContent("md"));
    el("es-export-json").addEventListener("click", () => exportContent("json"));
    el("es-create").addEventListener("click", createShare);
    el("es-created-copy").addEventListener("click", async () => {
      const ok = await copyText(el("es-created-url").value);
      Common.toast(ok ? "链接已复制" : "复制失败，请手动选择链接复制", ok ? "success" : "error");
    });
    el("es-created-close").addEventListener("click", () => {
      el("es-created").hidden = true;
    });
    syncKindUI();
    syncButtons();
    loadTargets();
  }

  function syncKindUI() {
    const jsonBtn = el("es-export-json");
    if (jsonBtn) jsonBtn.hidden = state.kind !== "conversation";
    const kind = el("es-kind");
    if (kind) kind.value = state.kind;
  }

  function syncButtons() {
    const hasTarget = selectedTargetId() !== null;
    ["es-export-md", "es-export-json", "es-create"].forEach((id) => {
      const btn = el(id);
      if (btn) btn.disabled = !hasTarget;
    });
  }

  async function loadTargets() {
    const select = el("es-target");
    if (!select) return;
    const requestKind = state.kind;
    select.disabled = true;
    select.innerHTML = `<option value="">加载中…</option>`;
    state.targets = [];
    try {
      if (requestKind === "conversation") {
        const data = await API.request("GET", "/api/chat/sessions?limit=100&offset=0&order=desc");
        state.targets = (data.items || []).map((item) => ({
          id: item.id,
          title: item.title || `会话 #${item.id}`,
          meta: `${item.message_count || 0} 条消息`,
          created_at: item.created_at,
        }));
      } else {
        const data = await API.request("GET", "/api/knowledge/bases/page?limit=100&offset=0&sort=updated");
        state.targets = (data.items || []).map((item) => ({
          id: item.id,
          title: item.name || `知识库 #${item.id}`,
          meta: `${item.point_count || 0} 个知识点`,
        }));
      }
    } catch (err) {
      if (el("es-target") !== select) return; // 视图已切走
      select.innerHTML = `<option value="">加载失败：${Common.escapeHtml(err.message)}</option>`;
      select.disabled = false;
      syncButtons();
      return;
    }
    // 用户可能在等待期间切换了类型，丢弃过期结果
    if (requestKind !== state.kind) return;
    if (!state.targets.length) {
      select.innerHTML = `<option value="">${requestKind === "conversation" ? "还没有可导出的会话" : "还没有知识库"}</option>`;
    } else {
      select.innerHTML = state.targets.map((item) =>
        `<option value="${item.id}">#${item.id} ${Common.escapeHtml(item.title)}（${Common.escapeHtml(item.meta)}）</option>`
      ).join("");
    }
    select.disabled = false;
    syncButtons();
  }

  function exportContent(format) {
    const id = selectedTargetId();
    if (id === null) {
      Common.toast("请先选择要导出的内容", "warning");
      return;
    }
    const url = state.kind === "knowledge_base"
      ? `/api/export/knowledge/${id}.md`
      : `/api/export/conversations/${id}.${format === "json" ? "json" : "md"}`;
    // 同源下载依赖登录 Cookie；用临时 <a> 触发，避免新窗口被拦截
    const a = document.createElement("a");
    a.href = url;
    a.rel = "noopener";
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    a.remove();
    Common.toast(format === "json" ? "正在导出 JSON…" : "正在导出 Markdown…");
  }

  async function createShare() {
    const id = selectedTargetId();
    if (id === null) {
      Common.toast("请先选择要分享的内容", "warning");
      return;
    }
    const hours = Number(el("es-expires").value) || 72;
    const btn = el("es-create");
    btn.disabled = true;
    try {
      const data = await API.request("POST", "/api/share", {
        kind: state.kind,
        id,
        expires_hours: hours,
      });
      const url = window.location.origin + data.url;
      el("es-created").hidden = false;
      el("es-created-url").value = url;
      const copied = await copyText(url);
      Common.toast(copied ? "分享链接已创建并复制" : "分享链接已创建", "success");
      document.dispatchEvent(new CustomEvent("share:created"));
    } catch (err) {
      Common.alertDialog({ title: "创建分享失败", message: err.message });
    } finally {
      syncButtons();
    }
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch {
      /* 继续走 execCommand 兜底 */
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    area.style.opacity = "0";
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

  function render(container) {
    fill(container, false);
  }

  function openModal() {
    if (document.querySelector("#es-modal-body")) return; // 已在弹窗里
    const box = Common.openModal(
      `<div class="modal-header">
         <h3>导出与分享</h3>
         <button class="modal-close" data-close>×</button>
       </div>
       <div id="es-modal-body"></div>`
    );
    const body = box.querySelector("#es-modal-body");
    fill(body, true);
    box.querySelectorAll("[data-close]").forEach((b) =>
      b.addEventListener("click", () => Common.closeModal())
    );
  }

  return { render, openModal };
})();

/* ---------- 主会话接线（需手工接入，本补丁不改 app.html / app.js / app.css） ----------
 * 视图 id / 路由 key：export
 * 导航文案：导出与分享
 * 路由 hash：#/export
 *
 * 1) backend/static/app.html：在 app.js 之前的 views 脚本区加一行
 *    <script src="/static/js/views/export-share.js?v=1"></script>
 * 2) backend/static/js/app.js 的 NAV 数组中增加一项（学生、管理员都可见）：
 *    学生可放在「课程资料」组，管理员可放在「内容管理」组：
 *    { key: "export", label: "导出与分享" }
 * 3) app.js 的 TITLES 中给学生、管理员各加：export: "导出与分享"
 * 4) app.js 的 navigate() switch 中加：
 *    case "export": ExportShareView.render(container); break;
 * 5) 完成后访问 #/export；刷新会由现有 hash 路由回到本页。
 */
