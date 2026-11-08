/* ============ 主题切换：浅色 / 深色 两态切换，持久化到 localStorage ============ */
const ThemeCenter = (() => {
  const KEY = "app-theme";               // 存储值：light | dark
  const SUN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M4.93 4.93l1.41 1.41"/><path d="M17.66 17.66l1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="M6.34 17.66l-1.41 1.41"/><path d="M19.07 4.93l-1.41 1.41"/></svg>';
  const MOON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';

  function isDark() {
    const pref = localStorage.getItem(KEY);
    if (pref === "light") return false;
    if (pref === "dark") return true;
    // 首次无记录：跟随系统偏好
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function apply() {
    const dark = isDark();
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    const btn = document.getElementById("theme-btn");
    if (btn) {
      btn.innerHTML = dark ? MOON : SUN;  // 状态指示：晚上（深色）月亮，白天（浅色）太阳
      btn.title = dark ? "切换到浅色模式" : "切换到深色模式";
      btn.setAttribute("aria-label", btn.title);
    }
  }

  function init() {
    apply();
    const btn = document.getElementById("theme-btn");
    if (!btn) return;
    btn.addEventListener("click", () => {
      localStorage.setItem(KEY, isDark() ? "light" : "dark");
      apply();
    });
  }

  return { init, apply };
})();

/* ============ 全局通知：右上角铃铛 + 未读角标（点击直达对应页面，无下拉） ============ */
const NotifyCenter = (() => {
  const BELL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';
  let unread = 0;      // 未读角标数
  let pollTimer = null;

  function init() {
    const host = document.getElementById("header-notify");
    if (!host) return;
    host.innerHTML = `
      <button type="button" class="notify-btn" id="notify-btn" aria-label="通知" title="查看通知">
        ${BELL}<span class="notify-dot" id="notify-dot" style="display:none"></span>
      </button>`;
    // 点击铃铛直达对应页面：管理员→问题反馈，学生→我的反馈
    document.getElementById("notify-btn").addEventListener("click", () => {
      App.navigate(API.getUser()?.role === "admin" ? "unanswered" : "feedback");
    });
    refresh();
    pollTimer = setInterval(refresh, 30000);  // 每 30s 拉一次角标
  }

  async function refresh() {
    try {
      const role = API.getUser()?.role;
      if (role === "admin") {
        // 管理员：待处理且尚未查看的反馈数（后端 unread 字段，全量口径）
        const data = await API.request("GET", "/api/admin/unanswered?status=pending&limit=1");
        unread = data.unread || 0;
      } else {
        // 学生：已解决但尚未查看的回答数（后端新增 unread 字段，全量口径）
        const data = await API.request("GET", "/api/chat/unanswered/mine?limit=1");
        unread = data.unread || 0;
      }
      renderDot();
    } catch { /* 静默失败，不打断页面 */ }
  }

  function renderDot() {
    const dot = document.getElementById("notify-dot");
    if (!dot) return;
    if (unread > 0) {
      dot.textContent = unread > 99 ? "99+" : String(unread);
      dot.style.display = "";
    } else {
      dot.style.display = "none";
    }
  }

  function destroy() {
    if (pollTimer) clearInterval(pollTimer);
  }

  return { init, refresh, destroy };
})();

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
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
    unanswered: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
    user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
    bell: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>',
  };

  // 侧边栏按角色任务组织：学生看学习闭环，管理员看问答运营闭环。
  // 个人中心统一放进底部账号区，不再重复占用导航项。
  const NAV = [
    { group: "", roles: ["student"], items: [
      { key: "workbench", icon: ICON.home, label: "学习首页" },
      { key: "chat", icon: ICON.chat, label: "智能问答" },
    ]},
    { group: "学习记录", roles: ["student"], items: [
      { key: "history", icon: ICON.history, label: "全部会话" },
      { key: "feedback", icon: ICON.unanswered, label: "答疑进度" },
    ]},
    { group: "课程资料", roles: ["student"], items: [
      { key: "knowledge", icon: ICON.knowledge, label: "课程知识库" },
      { key: "documents", icon: ICON.documents, label: "我的资料" },
    ]},
    { group: "", roles: ["admin"], items: [
      { key: "overview", icon: ICON.overview, label: "运营概览" },
    ]},
    { group: "问答运营", roles: ["admin"], items: [
      { key: "unanswered", icon: ICON.unanswered, label: "未命中问题" },
      { key: "history", icon: ICON.history, label: "会话审阅" },
      { key: "chat", icon: ICON.chat, label: "问答自测" },
    ]},
    { group: "内容管理", roles: ["admin"], items: [
      { key: "content", icon: ICON.knowledge, label: "知识库与文档" },
      { key: "recycle", icon: ICON.trash, label: "回收站" },
    ]},
    { group: "系统管理", roles: ["admin"], items: [
      { key: "users", icon: ICON.users, label: "用户管理" },
    ]},
  ];

  const ROLE_LABELS = {
    student: "学生",
    admin: "管理员",
  };

  const TITLES = {
    student: {
      workbench: "学习首页",
      knowledge: "课程知识库",
      documents: "我的资料",
      history: "全部会话",
      feedback: "答疑进度",
      chat: "智能问答",
      settings: "个人中心",
    },
    admin: {
      overview: "运营概览",
      unanswered: "未命中问题",
      feedback: "未命中问题",
      history: "会话审阅",
      chat: "问答自测",
      content: "知识库与文档",
      recycle: "回收站",
      users: "用户管理",
      settings: "个人中心",
      kb: "知识库管理",
      documents: "文档管理",
    },
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
    setupMobileNav();
    ThemeCenter.init();
    const mustChangePassword = !!API.getUser().must_change_password;
    if (!mustChangePassword) NotifyCenter.init();
    // 退出登录：点一下弹确认框；长按 0.65 秒直接退出，不弹任何提示
    const logoutBtn = document.getElementById("logout-btn");
    function doLogout() {
      API.clearSession();
      window.location.replace("/static/index.html");
    }
    let holdTimer = null;
    let held = false;
    const HOLD_MS = 650;

    logoutBtn.addEventListener("pointerdown", () => {
      held = false;
      logoutBtn.classList.add("holding");
      holdTimer = setTimeout(() => {
        held = true;
        logoutBtn.classList.remove("holding");
        doLogout();
      }, HOLD_MS);
    });
    const cancelHold = () => {
      clearTimeout(holdTimer);
      logoutBtn.classList.remove("holding");
    };
    logoutBtn.addEventListener("pointerup", cancelHold);
    logoutBtn.addEventListener("pointerleave", cancelHold);
    logoutBtn.addEventListener("pointercancel", cancelHold);
    logoutBtn.addEventListener("blur", cancelHold);

    logoutBtn.addEventListener("click", async () => {
      if (held) { held = false; return; }  // 长按已经退出了，忽略随后的 click
      const ok = await Common.confirmDialog({
        title: "退出登录",
        message: "退出后需重新登录才能继续使用，确定退出吗？",
        okText: "退出登录",
        icon: "logout",
        danger: false,  // 退出不是破坏性操作，按钮跟随图标用主色
      });
      if (!ok) return;
      doLogout();
    });
    const profileBtn = document.getElementById("user-profile-btn");
    if (profileBtn) {
      profileBtn.addEventListener("click", () => navigate("settings"));
    }
    navigate(
      mustChangePassword
        ? "settings"
        : (API.getUser().role === "admin" ? "overview" : "workbench")
    );
    if (mustChangePassword) {
      setTimeout(() => ProfileView.openRequiredPasswordChange(), 0);
    }
  }

  function refreshUserInfo() {
    const user = API.getUser();
    const role = user.role === "admin" ? "admin" : "student";
    document.getElementById("user-avatar").innerHTML = Common.avatarContent(user);
    const displayName = user.username || user.account;
    document.getElementById("user-name").textContent = displayName;
    const profileBtn = document.getElementById("user-profile-btn");
    if (profileBtn) profileBtn.setAttribute("aria-label", `${displayName}，进入个人中心`);
    const roleEl = document.getElementById("user-role");
    if (roleEl) {
      roleEl.textContent = ROLE_LABELS[role];
      roleEl.style.display = "";
    }
  }

  function navItemHtml(it) {
    return `
        <button type="button" class="nav-item" data-key="${it.key}" title="${it.label}" aria-label="${it.label}">
          <span class="nav-icon">${it.icon}</span><span class="nav-label">${it.label}</span>
        </button>`;
  }

  function renderNav() {
    const nav = document.getElementById("sidebar-nav");
    const role = API.getUser().role;
    nav.innerHTML = NAV.map((group) => {
      const items = group.items.filter((it) => !group.roles || group.roles.includes(role));
      if (!items.length) return "";
      const head = group.group ? `<div class="nav-group-title">${group.group}</div>` : "";
      return head + items.map(navItemHtml).join("");
    }).join("");

    document.querySelectorAll(".nav-item").forEach((btn) => {
      btn.addEventListener("click", () => navigate(btn.dataset.key));
    });
  }

  function navigate(key, opts) {
    // 销毁旧视图
    if (currentView === "chat") ChatView.destroy();
    if (currentView === "content" || currentView === "documents" || currentView === "kb") {
      ContentView.destroy();
    }

    const role = API.getUser().role;
    // 兼容旧入口：管理员的 kb / documents 都落在「知识库与文档」上，高亮同一项
    const activeKey = role === "admin" && (key === "kb" || key === "documents") ? "content" : key;
    document.querySelectorAll(".nav-item").forEach((b) => {
      const active = b.dataset.key === activeKey;
      b.classList.toggle("active", active);
      if (active) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
    const profileBtn = document.getElementById("user-profile-btn");
    if (profileBtn) {
      const active = key === "settings";
      profileBtn.classList.toggle("active", active);
      if (active) profileBtn.setAttribute("aria-current", "page");
      else profileBtn.removeAttribute("aria-current");
    }
    document.getElementById("page-title").textContent =
      (TITLES[role] && TITLES[role][key]) || "智答系统";
    const container = document.getElementById("main-content");
    container.innerHTML = "";

    switch (key) {
      case "workbench": WorkbenchView.render(container); break;
      case "knowledge": KnowledgeView.render(container, opts); break;
      case "chat": ChatView.render(container, opts); break;
      case "settings": ProfileView.render(container); break;
      case "content": ContentView.render(container); break;
      case "kb":
        // 旧入口兜底：管理员的“知识库管理”并进「知识库与文档」的知识库 tab
        if (API.getUser().role === "admin") ContentView.render(container, { tab: "kb" });
        else KnowledgeView.render(container);
        break;
      case "documents":
        // 学生看自己的文档；管理员的文档并进「知识库与文档」的文档 tab
        if (API.getUser().role === "admin") ContentView.render(container, { tab: "docs" });
        else DocumentsView.render(container);
        break;
      case "overview": AdminView.render(container, "overview"); break;
      case "recycle": RecycleView.render(container); break;
      case "users": AdminView.render(container, "users"); break;
      case "unanswered": AdminView.render(container, "unanswered"); break;
      case "feedback":
        if (API.getUser().role === "student") FeedbackView.render(container);
        else AdminView.render(container, "unanswered");
        break;
      case "history":
        if (API.getUser().role === "admin") AdminView.render(container, "conversations", opts);
        else HistoryView.render(container);
        break;
      default: container.innerHTML = `<div class="empty-state"><p>页面不存在</p></div>`;
    }
    currentView = key;
    // 切页后刷新通知角标（读反馈/处理问题后未读数会变）
    NotifyCenter.refresh();
  }

  // 未读数量只由顶部通知铃铛提示，侧边栏保持纯导航。
  /* ---------- 窄屏抽屉导航（≤768px） ---------- */
  function setupMobileNav() {
    const toggle = document.getElementById("nav-toggle");
    const backdrop = document.getElementById("nav-backdrop");
    const sidebar = document.getElementById("sidebar");
    if (!toggle || !backdrop || !sidebar) return;

    const isOpen = () => document.body.classList.contains("nav-open");
    const mobileQuery = window.matchMedia("(max-width: 768px)");
    const syncAccessibility = () => {
      if (mobileQuery.matches) {
        sidebar.setAttribute("aria-hidden", String(!isOpen()));
        if (isOpen()) sidebar.removeAttribute("inert");
        else sidebar.setAttribute("inert", "");
      } else {
        sidebar.removeAttribute("inert");
        sidebar.removeAttribute("aria-hidden");
      }
    };
    const setOpen = (open, restoreFocus = true) => {
      document.body.classList.toggle("nav-open", open);
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "关闭菜单" : "打开菜单");
      syncAccessibility();
      if (open) {
        const first = sidebar.querySelector(".nav-item.active") || sidebar.querySelector(".nav-item");
        if (first) setTimeout(() => first.focus(), 30);
      } else if (restoreFocus) {
        toggle.focus();
      }
    };

    toggle.addEventListener("click", () => setOpen(!isOpen()));
    backdrop.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && isOpen()) setOpen(false);
    });
    sidebar.addEventListener("click", (e) => {
      if (!e.target.closest(".nav-item") || !isOpen()) return;
      setOpen(false, false);
      const main = document.getElementById("main-content");
      if (main) setTimeout(() => main.focus(), 60);
    });
    mobileQuery.addEventListener("change", syncAccessibility);
    window.addEventListener("resize", () => {
      if (window.innerWidth > 768 && isOpen()) setOpen(false, false);
    });
    syncAccessibility();
  }

  return { init, refreshUserInfo, renderNav, navigate };
})();

document.addEventListener("DOMContentLoaded", () => App.init());
