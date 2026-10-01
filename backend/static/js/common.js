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

  function confirmDialog({ title = "确认操作", message = "", danger = true, okText = "确认" }) {
    return new Promise((resolve) => {
      const box = openModal(`
        <div class="modal-header">
          <h3>${escapeHtml(title)}</h3>
          <button class="modal-close" data-close>×</button>
        </div>
        <p style="color:var(--text-2);line-height:1.8">${message}</p>
        <div class="modal-footer">
          <button class="btn" data-cancel>取消</button>
          <button class="btn ${danger ? "btn-danger" : "btn-primary"}" data-ok>${escapeHtml(okText)}</button>
        </div>
      `);
      box.querySelector("[data-close]").onclick = () => { closeModal(); resolve(false); };
      box.querySelector("[data-cancel]").onclick = () => { closeModal(); resolve(false); };
      box.querySelector("[data-ok]").onclick = () => { closeModal(); resolve(true); };
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

  return { toast, escapeHtml, fmtTime, fmtSize, openModal, closeModal, confirmDialog, roleBadge, avatarContent, avatarHtml };
})();
