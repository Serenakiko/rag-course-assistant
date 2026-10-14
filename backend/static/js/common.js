/* ============ 通用工具 ============ */
const Common = (() => {
  function toast(message, type = "") {
    const root = document.getElementById("toast-root");
    if (!root) return;
    const el = document.createElement("div");
    el.className = "toast" + (type ? " toast-" + type : "");
    el.textContent = message;
    root.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transition = "opacity .3s";
      setTimeout(() => el.remove(), 320);
    }, 2800);
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtTime(iso) {
    if (!iso) return "-";
    const raw = String(iso);
    // 后端（SQLite）返回的时间无时区标记，值为 UTC 时刻；
    // 直接 new Date() 会被当作本地时间，导致显示少 8 小时，这里统一按 UTC 解析再转本地
    const hasTz = /(Z|[+-]\d{2}:?\d{2})\s*$/i.test(raw);
    const d = new Date(hasTz ? raw : raw + "Z");
    if (isNaN(d)) return raw;
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function fmtSize(bytes) {
    if (!bytes && bytes !== 0) return "-";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 / 1024).toFixed(2) + " MB";
  }

  /* ---------- 弹窗 ---------- */
  function openModal(html, { size = "", onOpen } = {}) {
    const mask = document.getElementById("modal-mask");
    const box = document.getElementById("modal-box");
    box.className = "modal" + (size ? " " + size : "");
    box.innerHTML = html;
    mask.classList.add("show");
    mask.onclick = (e) => { if (e.target === mask) closeModal(); };
    if (onOpen) onOpen(box);
    return box;
  }

  function closeModal() {
    const mask = document.getElementById("modal-mask");
    mask.classList.remove("show");
    document.getElementById("modal-box").innerHTML = "";
  }

  const ALERT_ICONS = {
    error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 16.5h.01"/></svg>',
    warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>',
    success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="m8.5 12.5 2.5 2.5 4.5-5"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 7.5h.01"/></svg>',
    logout: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/></svg>',
  };

  // 提示弹窗：登录/注册校验、接口报错统一走这里，只有一个「知道了」
  function alertDialog({ title = "提示", message = "", type = "error", okText = "知道了" } = {}) {
    return new Promise((resolve) => {
      const box = openModal(`
        <div class="alert-body">
          <div class="alert-icon ${type}">${ALERT_ICONS[type] || ALERT_ICONS.info}</div>
          <div class="alert-text">
            <div class="alert-title">${escapeHtml(title)}</div>
            <div class="alert-msg">${escapeHtml(message)}</div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-primary" data-ok>${escapeHtml(okText)}</button>
        </div>`);
      const done = () => { closeModal(); resolve(true); };
      box.querySelector("[data-ok]").onclick = done;
      // 点遮罩也算关闭，避免 Promise 一直挂着
      document.getElementById("modal-mask").onclick = (e) => {
        if (e.target.id === "modal-mask") done();
      };
    });
  }

  function confirmDialog({ title = "确认操作", message = "", danger = true, okText = "确认", icon = "" }) {
    return new Promise((resolve) => {
      const kind = icon || (danger ? "warning" : "info");
      const box = openModal(`
        <div class="alert-body">
          <div class="alert-icon ${kind}">${ALERT_ICONS[kind]}</div>
          <div class="alert-text">
            <div class="alert-title">${escapeHtml(title)}</div>
            <div class="alert-msg">${message}</div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn" data-cancel>取消</button>
          <button class="btn ${danger ? "btn-danger" : "btn-primary"}" data-ok>${escapeHtml(okText)}</button>
        </div>
      `);
      const done = (v) => { closeModal(); resolve(v); };
      box.querySelector("[data-cancel]").onclick = () => done(false);
      box.querySelector("[data-ok]").onclick = () => done(true);
      document.getElementById("modal-mask").onclick = (e) => {
        if (e.target.id === "modal-mask") done(false);
      };
    });
  }

  function roleBadge(role) {
    const map = { student: "学生", admin: "管理员" };
    return `<span class="badge badge-${role}">${map[role] || role}</span>`;
  }

  // 头像：上传图 / 预设 emoji / 首字母
  function avatarContent(user) {
    const v = (user && user.avatar) || "";
    if (v.startsWith("emoji:")) return `<span>${escapeHtml(v.slice(6))}</span>`;
    if (v) return `<img src="${escapeHtml(v)}" alt="头像">`;
    const ch = (user && (user.real_name || user.username || "?"))[0];
    return `<span>${escapeHtml(ch)}</span>`;
  }

  function avatarHtml(user, size = 36) {
    return `<div class="avatar" style="width:${size}px;height:${size}px;font-size:${Math.max(13, Math.round(size * 0.42))}px;border-radius:${Math.round(size / 2)}px">${avatarContent(user)}</div>`;
  }

  return { toast, escapeHtml, fmtTime, fmtSize, openModal, closeModal, alertDialog, confirmDialog, roleBadge, avatarContent, avatarHtml };
})();
