/* ============ 主应用装配：导航、路由、状态检测 ============ */
const App = (() => {
  // 线性 SVG 图标（stroke=currentColor，颜色跟随菜单项文字色）
  const ICON = {
    home: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>',
    workbench: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>',
    knowledge: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
    documents: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
    chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
    history: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    overview: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
    users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    unanswered: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
    user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
  };

  const NAV = [
    { group: "学习", roles: ["student"], items: [
      { key: "workbench", icon: ICON.home, label: "首页" },
      { key: "knowledge", icon: ICON.knowledge, label: "知识库" },
      { key: "chat", icon: ICON.chat, label: "智能问答" },
      { key: "feedback", icon: ICON.history, label: "我的反馈" },
      { key: "settings", icon: ICON.user, label: "个人中心" },
    ]},
    { group: "管理台", roles: ["admin"], items: [
      { key: "overview", icon: ICON.overview, label: "数据概览" },
      { key: "kb", icon: ICON.knowledge, label: "知识库管理" },
      { key: "documents", icon: ICON.documents, label: "文档管理" },
      { key: "chat", icon: ICON.chat, label: "智能问答" },
      { key: "unanswered", icon: ICON.unanswered, label: "问题反馈" },
      { key: "history", icon: ICON.history, label: "对话历史" },
      { key: "users", icon: ICON.users, label: "用户管理" },
    ]},
  ];

  const TITLES = {
    workbench: "首页",
    knowledge: "知识库",
    chat: "智能问答",
    history: "对话历史",
    settings: "个人中心",
    overview: "数据概览",
    users: "用户管理",
    kb: "知识库管理",
    documents: "文档管理",
    unanswered: "问题反馈",
    feedback: "我的反馈",
  };

  let currentView = null;

  function init() {
    if (!API.getToken() || !API.getUser()) {
      API.goLogin();
      return;
    }
    // 角色主题：统一靛蓝
    document.body.classList.add("role-" + (API.getUser().role === "admin" ? "admin" : "student"));
    refreshUserInfo();
    renderNav();
    document.getElementById("logout-btn").addEventListener("click", () => {
      API.clearSession();
      window.location.replace("/static/index.html");
    });
    // 点击头像直接弹"更换头像"
    const avatarEl = document.getElementById("user-avatar");
    avatarEl.title = "点击更换头像";
    avatarEl.addEventListener("click", (e) => {
      e.stopPropagation();
      ProfileView.openAvatarModal();
    });
    checkLlmStatus();
    setInterval(checkLlmStatus, 60000);
    navigate(API.getUser().role === "admin" ? "overview" : "workbench");
    startFeedbackPolling();  // 学生端：未读红点 + 新反馈 toast
  }

  function refreshUserInfo() {
    const user = API.getUser();
    document.getElementById("user-avatar").innerHTML = Common.avatarContent(user);
    document.getElementById("user-name").textContent = user.real_name || user.username;
    const roleEl = document.getElementById("user-role");
    if (roleEl) roleEl.style.display = "none";  // 侧边栏用户区不显示角色小字
  }

  function renderNav() {
    const nav = document.getElementById("sidebar-nav");
    const role = API.getUser().role;
    nav.innerHTML = NAV.map((group) => {
      const items = group.items.filter((it) => !group.roles || group.roles.includes(role));
      if (!items.length) return "";
      return items.map((it) => `
        <button class="nav-item" data-key="${it.key}">
          <span class="nav-icon">${it.icon}</span><span>${it.label}</span>
          ${it.key === "feedback" ? `<span class="nav-badge" data-badge="feedback" style="display:none"></span>` : ""}
        </button>`).join("");
    }).join("");

    nav.querySelectorAll(".nav-item").forEach((btn) => {
      btn.addEventListener("click", () => navigate(btn.dataset.key));
    });
  }

  function navigate(key) {
    // 销毁旧视图
    if (currentView === "chat") ChatView.destroy();
    if (currentView === "documents" || currentView === "kb") DocumentsView.destroy();

    document.querySelectorAll(".nav-item").forEach((b) =>
      b.classList.toggle("active", b.dataset.key === key));
    document.getElementById("page-title").textContent = TITLES[key] || "智答系统";
    const container = document.getElementById("main-content");
    container.innerHTML = "";

    switch (key) {
      case "workbench": WorkbenchView.render(container); break;
      case "knowledge": KnowledgeView.render(container); break;
      case "chat": ChatView.render(container); break;
      case "settings": ProfileView.render(container); break;
      case "kb":
        // 管理员的"知识库管理"走表格视图；学生端"知识库"走卡片视图
        if (API.getUser().role === "admin") AdminView.render(container, "kb_table");
        else KnowledgeView.render(container);
        break;
      case "documents": DocumentsView.render(container); break;
      case "overview": AdminView.render(container, "overview"); break;
      case "users": AdminView.render(container, "users"); break;
      case "unanswered": AdminView.render(container, "unanswered"); break;
      case "feedback":
        if (API.getUser().role === "student") FeedbackView.render(container);
        else AdminView.render(container, "unanswered");
        break;
      case "history":
        if (API.getUser().role === "admin") AdminView.render(container, "conversations");
        else HistoryView.render(container);
        break;
      default: container.innerHTML = `<div class="empty-state"><p>页面不存在</p></div>`;
    }
    currentView = key;
  }

  /* ---------- 大模型连接状态 ---------- */
  async function checkLlmStatus() {
    const el = document.getElementById("llm-status");
    const text = document.getElementById("llm-status-text");
    if (!el) return;
    el.className = "llm-status checking";
    text.textContent = "检测中…";
    try {
      const resp = await API.request("GET", "/api/llm/status");
      el.className = "llm-status " + (resp.ok ? "ok" : "bad");
      text.textContent = resp.ok ? "大模型已连接" : resp.message;
      el.title = resp.message || "";
    } catch {
      el.className = "llm-status bad";
      text.textContent = "服务连接失败";
    }
  }

  /* ---------- 学生端反馈通知：轮询未读角标 ---------- */
  let feedbackTimer = null;
  let lastUnread = 0;
  async function refreshFeedbackBadge() {
    if (API.getUser()?.role !== "student") return;
    try {
      const items = await API.request("GET", "/api/chat/unanswered/mine");
      const unread = items.filter((it) => it.status === "resolved" && !it.read).length;
      const badge = document.querySelector('[data-badge="feedback"]');
      if (badge) {
        badge.textContent = unread > 0 ? String(unread) : "";
        badge.style.display = unread > 0 ? "inline-flex" : "none";
      }
      if (unread > lastUnread && unread > 0 && !document.hidden) {
        Common.toast(`你有 ${unread} 条反馈已解决，点击「我的反馈」查看`, "success");
      }
      lastUnread = unread;
    } catch { /* 网络异常静默，下个周期重试 */ }
  }
  function startFeedbackPolling() {
    refreshFeedbackBadge();
    feedbackTimer = setInterval(refreshFeedbackBadge, 60_000);
  }

  return { init, refreshUserInfo, navigate, refreshFeedbackBadge };
})();

document.addEventListener("DOMContentLoaded", () => App.init());
