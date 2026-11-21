const ChatView = (() => {
  let currentConvId = null;
  let abortController = null;
  let streamSeq = 0;  // 生成序号：切换会话中止旧生成后，旧回调不再操作界面
  let activeSeq = 0;  // 正在进行的那一轮序号，0 = 空闲。同一会话不允许并发提问
  let pendingConvId = null;  // 记录"生成未完成"的会话：返回该会话时自动重新生成
  const resumedConvs = new Set();  // 本次页面里已自动补过回答的会话，避免反复重试
  let sessionQuery = "";      // 侧边栏搜索框的关键字（服务端按标题过滤）
  let sessionItems = [];      // 当前这一页会话（最近 20 条，或搜索结果）
  let sessionTotal = 0;       // 匹配总数（不搜索时就是全部会话数）
  let searchTimer = null;     // 搜索防抖
  let closeChatDrawer = () => {};
  const convMeta = new Map(); // id -> 会话概要：当前会话被挤出这一页时还能钉在最上面
  const SIDEBAR_LIMIT = 20;   // 侧边栏只取这么多条，再多去「对话历史」页
  const SEARCH_LIMIT = 50;    // 搜索结果最多取这么多条
  const MSG_PAGE = 50;        // 会话消息分页：先取最近 50 条，更早的按需加载
  let selectedCourse = "";    // 聊天页课程筛选：空 = 全部课程

  /* 拒答原因：后端 reason 可能是代号，也可能是一整句中文说明 */
  const UNMATCHED_REASONS = {
    empty_base: "这门课的知识库还是空的，暂时没有可检索的资料",
    no_content: "这门课还没有上传可检索的资料",
    no_hit: "没有检索到能回答这个问题的资料",
    out_of_scope: "这个问题不在课程知识库的覆盖范围内",
    not_answerable: "资料里没有讲到这个知识点",
    low_score: "检索到的资料和这个问题关系不大",
    failed: "检索/生成时出了点问题",
  };

  function unmatchedReason(evt) {
    const r = evt && evt.reason;
    if (!r) return "该问题暂未在知识库中命中";
    if (UNMATCHED_REASONS[r]) return UNMATCHED_REASONS[r];
    return typeof r === "string" && /[\u4e00-\u9fa5]/.test(r)
      ? r
      : "该问题暂未在知识库中命中";
  }

  const ICON_SEND = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3.4 20.4l17.45-7.48a1 1 0 0 0 0-1.84L3.4 3.6a.993.993 0 0 0-1.39.91L2 9.12c0 .5.37.93.87.99L17 12 2.87 13.88c-.5.07-.87.5-.87 1l.01 4.61c0 .71.73 1.2 1.39.91z"/></svg>';
  const ICON_STOP = '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';

  // 发送按钮的两种状态：生成中是可点击的「停止生成」
  function setSending(on) {
    const sendBtn = document.getElementById("send-btn");
    if (!sendBtn) return;
    sendBtn.title = on ? "停止生成" : "发送";
    sendBtn.style.background = on ? "var(--danger)" : "";
    sendBtn.innerHTML = on ? ICON_STOP : ICON_SEND;
  }

  // 中止当前生成（切换会话 / 新建对话 / 删除会话 / 点「停止生成」）
  function stopStream() {
    if (abortController) abortController.abort();
    abortController = null;
    if (activeSeq) {
      activeSeq = 0;
      setSending(false);
    }
  }

  function render(container, opts) {
    container.innerHTML = `
      <div class="chat-layout">
        <button type="button" class="chat-side-toggle" id="chat-side-toggle"
          aria-label="打开会话列表" aria-controls="chat-side-panel" aria-expanded="false">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h10"/></svg>
          <span>会话</span>
        </button>
        <button type="button" class="chat-side-backdrop" id="chat-side-backdrop" aria-label="关闭会话列表"></button>
        <aside class="chat-side" id="chat-side-panel" aria-label="会话列表">
          <button type="button" class="chat-side-close" id="chat-side-close" aria-label="关闭会话列表">×</button>
          <button class="chat-new" id="chat-new">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            <span>新对话</span>
          </button>
          <div class="chat-search">
            <input id="chat-search" type="search" placeholder="搜索会话标题" autocomplete="off" aria-label="搜索会话">
          </div>
          <div class="nav-group-title" style="padding:2px 14px 6px">会话记录</div>
          <div class="chat-sessions-wrap">
            <div class="chat-sessions" id="chat-sessions"></div>
            <div class="chat-sessions-fade" aria-hidden="true"></div>
          </div>
          <div class="chat-more" id="chat-more"></div>
        </aside>
        <section class="chat-main">
          <div class="chat-filter">
            <label class="chat-filter-label" for="chat-course-filter">检索范围</label>
            <select class="form-control" id="chat-course-filter" aria-label="选择课程知识库" disabled>
              <option value="">全部课程</option>
            </select>
            <span class="chat-filter-hint" id="chat-filter-hint"></span>
          </div>
          <div class="sr-only" id="chat-announcer" role="status" aria-live="polite"></div>
          <div class="chat-messages" id="chat-messages"></div>
          <div class="chat-input-area">
            <div class="chat-input-box">
              <textarea id="chat-input" rows="1" placeholder="输入你的问题，Enter 发送"></textarea>
              <button class="send-btn" id="send-btn" title="发送">
                <svg viewBox="0 0 24 24" fill="currentColor"><path d="M3.4 20.4l17.45-7.48a1 1 0 0 0 0-1.84L3.4 3.6a.993.993 0 0 0-1.39.91L2 9.12c0 .5.37.93.87.99L17 12 2.87 13.88c-.5.07-.87.5-.87 1l.01 4.61c0 .71.73 1.2 1.39.91z"/></svg>
              </button>
            </div>
          </div>
        </section>
      </div>`;

    const inputEl = document.getElementById("chat-input");
    const sendBtn = document.getElementById("send-btn");

    const layoutEl = container.querySelector(".chat-layout");
    const sideEl = document.getElementById("chat-side-panel");
    const sideToggle = document.getElementById("chat-side-toggle");
    const sideClose = document.getElementById("chat-side-close");
    const sideBackdrop = document.getElementById("chat-side-backdrop");
    const mobileChat = window.matchMedia("(max-width: 900px)");
    const syncChatDrawer = (open, restoreFocus = true) => {
      const expanded = mobileChat.matches && open;
      layoutEl.classList.toggle("chat-side-open", expanded);
      sideToggle.setAttribute("aria-expanded", String(expanded));
      sideToggle.setAttribute("aria-label", expanded ? "关闭会话列表" : "打开会话列表");
      if (mobileChat.matches) {
        sideEl.setAttribute("aria-hidden", String(!expanded));
        if (expanded) sideEl.removeAttribute("inert");
        else sideEl.setAttribute("inert", "");
      } else {
        sideEl.removeAttribute("inert");
        sideEl.removeAttribute("aria-hidden");
      }
      if (expanded) {
        const first = sideEl.querySelector(".session-item.active, .chat-new");
        if (first) setTimeout(() => first.focus(), 30);
      } else if (restoreFocus && mobileChat.matches) {
        sideToggle.focus();
      }
    };
    closeChatDrawer = () => syncChatDrawer(false, false);
    sideToggle.addEventListener("click", () =>
      syncChatDrawer(!layoutEl.classList.contains("chat-side-open")));
    sideClose.addEventListener("click", () => syncChatDrawer(false));
    sideBackdrop.addEventListener("click", () => syncChatDrawer(false));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && layoutEl.classList.contains("chat-side-open")) {
        syncChatDrawer(false);
      }
    });
    mobileChat.addEventListener("change", () => syncChatDrawer(false, false));
    syncChatDrawer(false, false);

    document.getElementById("chat-search").addEventListener("input", (e) => {
      sessionQuery = e.target.value;
      // 服务端搜索：每敲一个字都发请求太浪费，防抖 250ms
      clearTimeout(searchTimer);
      searchTimer = setTimeout(fetchSessionPage, 250);
    });

    document.getElementById("chat-new").addEventListener("click", async () => {
      const hasMessages = document.querySelectorAll("#chat-messages .msg").length > 0;
      if (!hasMessages) {
        Common.toast("已经是一个新对话了，直接输入问题就行。");
        return;
      }
      const ok = await Common.confirmDialog({
        title: "新建对话",
        message: "当前对话会保留在左侧列表，随时可以打开。",
        okText: "新建对话",
        danger: false,
      });
      if (!ok) return;
      if (activeSeq) {
        pendingConvId = currentConvId;  // 记录未完成生成的会话，便于返回时补生成
        stopStream();  // 先停止正在进行的生成
      }
      currentConvId = null;
      document.querySelectorAll(".session-item").forEach((el) => el.classList.remove("active"));
      showWelcome();
    });

    inputEl.addEventListener("input", () => {
      inputEl.style.height = "auto";
      inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + "px";
    });

    const doSend = () => {
      if (activeSeq) {  // 生成中：点击按钮 = 停止生成，不会又发一轮
        stopStream();
        return;
      }
      const text = inputEl.value.trim();
      if (!text) return;
      inputEl.value = "";
      inputEl.style.height = "auto";
      send(text);
    };
    sendBtn.addEventListener("click", doSend);
    inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (!activeSeq) doSend();  // 生成中按 Enter 不发新问题（两轮重叠会让上一轮丢答案）
      }
    });

    // 课程筛选：接口可用就填真实知识库列表，不可用就保持「全部课程」占位并禁用
    const courseSel = document.getElementById("chat-course-filter");
    courseSel.addEventListener("change", () => {
      selectedCourse = courseSel.value;
      const hint = document.getElementById("chat-filter-hint");
      if (hint) hint.textContent = selectedCourse ? `只检索《${selectedCourse}》` : "";
    });
    loadCourseFilter();

    // 从首页「继续上次对话」等入口带会话 id 进来：直接打开该会话
    if (opts && opts.convId) currentConvId = opts.convId;

    loadSessions();
  }

  /* 课程/知识库筛选下拉：拉不到列表就保留「全部课程」占位并禁用，不假装能筛 */
  async function loadCourseFilter() {
    const sel = document.getElementById("chat-course-filter");
    if (!sel) return;
    try {
      const bases = await API.request("GET", "/api/knowledge/bases");
      const list = Array.isArray(bases) ? bases : [];
      if (!list.length) return;
      sel.innerHTML = `<option value="">全部课程</option>` +
        list.map((b) => `<option value="${Common.escapeHtml(b.name)}">${Common.escapeHtml(b.name)}</option>`).join("");
      sel.disabled = false;
      if (selectedCourse && !list.some((b) => b.name === selectedCourse)) selectedCourse = "";
      sel.value = selectedCourse;
    } catch { /* 接口不可用：保持占位并禁用 */ }
  }

  /* 点「换个问法」/相关知识点：把内容填回输入框，让用户接着改，而不是重发原问题 */
  function fillChatInput(text) {
    const inputEl = document.getElementById("chat-input");
    if (!inputEl) return;
    inputEl.value = text;
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + "px";
    inputEl.placeholder = "换个问法再试一次：说具体一点，或换个关键词";
    inputEl.focus();
  }

  /* 左侧会话列表：没有会话显示欢迎页（带示例问题），有会话默认打开最近一个 */
  async function loadSessions(opts) {
    const listEl = document.getElementById("chat-sessions");
    if (!listEl) return;
    const keepCurrent = !!(opts && opts.keepCurrent);  // 提问后刷新：不覆盖当前对话界面
    await fetchSessionPage();
    if (keepCurrent) return;

    // 进入页面停在欢迎页（带示例问题）；只有你刚打开过的会话才自动恢复
    if (currentConvId) {
      openSession(currentConvId);
    } else {
      currentConvId = null;
      showWelcome();
    }
  }

  /* 只取当前这一页 + 总数（服务端分页） */
  async function fetchSessionPage() {
    const q = sessionQuery.trim();
    const params = new URLSearchParams({ limit: String(q ? SEARCH_LIMIT : SIDEBAR_LIMIT), offset: "0" });
    if (q) params.set("q", q);
    try {
      const data = await API.request("GET", `/api/chat/sessions?${params.toString()}`);
      sessionItems = (data && data.items) || [];
      sessionTotal = (data && data.total) || 0;
      sessionItems.forEach((it) => convMeta.set(it.id, it));
      // 这个 Map 只用来「把正在看的会话钉在列表顶部」
      if (convMeta.size > 200) {
        const keep = new Set(sessionItems.map((it) => it.id));
        if (currentConvId) keep.add(currentConvId);
        [...convMeta.keys()].forEach((id) => { if (!keep.has(id)) convMeta.delete(id); });
      }
    } catch {
      sessionItems = [];
      sessionTotal = 0;
    }
    renderSessionList();
  }

  /* 侧边栏只渲染当前这一页（最近 20 条 / 搜索结果）：会话一多一长串根本翻不动。
     想看全部、做管理就去「对话历史」页（那边有服务端分页和删除）。 */
  function renderSessionList() {
    const listEl = document.getElementById("chat-sessions");
    if (!listEl) return;
    const q = sessionQuery.trim();
    let visible = sessionItems.slice();
    // 正在看的老会话被挤出这一页时钉在最上面，否则它在列表里"消失"了
    if (!q && currentConvId && !visible.some((s) => s.id === currentConvId)) {
      const cur = convMeta.get(currentConvId);
      if (cur) visible = [cur, ...visible];
    }

    if (!visible.length) {
      listEl.innerHTML = `<div class="session-empty">${
        q ? "没有匹配的会话" : "还没有会话，直接在下面提问就行"
      }</div>`;
    } else {
      listEl.innerHTML = visible.map((s) => `
        <div class="session-item ${s.id === currentConvId ? "active" : ""}" data-id="${s.id}">
          <div class="s-title" title="${Common.escapeHtml(s.title)}">${Common.escapeHtml(s.title)}</div>
          <div class="s-meta">
            <span class="s-time">${Common.fmtTime(s.created_at)}</span>
            <span class="s-ops">
              <button class="s-op" data-op="rename" title="重命名">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
              </button>
              <button class="s-op danger" data-op="delete" title="删除">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              </button>
            </span>
          </div>
        </div>`).join("");

      listEl.querySelectorAll(".session-item").forEach((item) => {
        item.addEventListener("click", (e) => {
          if (e.target.closest("[data-op]")) return;
          openSession(Number(item.dataset.id));
        });
        item.querySelector('[data-op="rename"]').addEventListener("click", (e) => {
          e.stopPropagation();
          renameSession(Number(item.dataset.id));
        });
        item.querySelector('[data-op="delete"]').addEventListener("click", (e) => {
          e.stopPropagation();
          deleteSession(Number(item.dataset.id), s.title);
        });
      });
    }

    renderSessionFooter(q);
  }

  // 列表下方的说明行：「查看全部」入口 / 搜索结果条数
  function renderSessionFooter(q) {
    const footEl = document.getElementById("chat-more");
    if (!footEl) return;
    if (q) {
      footEl.innerHTML = `<div class="session-hint">匹配 ${sessionTotal} 条${
        sessionTotal > sessionItems.length ? `（只显示前 ${sessionItems.length} 条）` : ""
      }</div>`;
      return;
    }
    // 底部入口常驻，始终给「查看全部会话」出口
    footEl.innerHTML =
      `<button type="button" class="session-more" id="chat-more-btn">` +
      (sessionTotal > sessionItems.length
        ? `查看全部 ${sessionTotal} 条 ›`
        : `查看全部会话${sessionTotal ? `（${sessionTotal} 条）` : ""} ›`) +
      `</button>`;
    document.getElementById("chat-more-btn").addEventListener("click", () => App.navigate("history"));
  }

  async function openSession(id) {
    if (activeSeq) {
      pendingConvId = currentConvId;  // 记录未完成生成的会话
      stopStream();
    }
    currentConvId = id;
    closeChatDrawer();
    document.querySelectorAll(".session-item").forEach((el) =>
      el.classList.toggle("active", Number(el.dataset.id) === id));
    const messagesEl = document.getElementById("chat-messages");
    if (!messagesEl) return;
    try {
      // 新接口分页返回 {items, has_more}；老接口直接是数组，两种都兼容
      const data = await API.request("GET", `/api/chat/sessions/${id}/messages?limit=${MSG_PAGE}`);
      const msgs = Array.isArray(data) ? data : ((data && data.items) || []);
      const hasMore = !Array.isArray(data) && !!(data && data.has_more);
      messagesEl.innerHTML = "";
      msgs.forEach((m) => renderMessage(m.role, m.content, m.sources, m.interrupted, m.id, m.feedback));
      if (hasMore && msgs.length) prependLoadMore(messagesEl, id, msgs[0].id);
      scrollBottom();
      // 返回「生成未完成」的会话：带 regenerate 补生成
      const last = msgs[msgs.length - 1];
      if (last && last.role === "user" && !resumedConvs.has(id)) {
        // 只有提问没有回答的会话，进来自动补一条
        resumedConvs.add(id);
        pendingConvId = null;
        send(last.content, { silentUser: true, regenerate: true });
      } else if (last && last.role === "assistant" && last.interrupted && !resumedConvs.has(id)) {
        // 半截回答没法续写，重新生成一遍
        resumedConvs.add(id);
        pendingConvId = null;
        const lastUserMsg = [...msgs].reverse().find((m) => m.role === "user");
        if (lastUserMsg) {
          if (messagesEl.lastElementChild) messagesEl.lastElementChild.remove();
          send(lastUserMsg.content, { silentUser: true, regenerate: true });
        }
      } else if (id === pendingConvId && last && last.role === "assistant") {
        // 半截回答已落库，删掉这条再从头生成，避免两条回答
        pendingConvId = null;
        const lastUser = [...msgs].reverse().find((m) => m.role === "user");
        if (lastUser) {
          if (messagesEl.lastElementChild) messagesEl.lastElementChild.remove();
          send(lastUser.content, { silentUser: true, regenerate: true });
        }
      } else if (id === pendingConvId) {
        pendingConvId = null;
      }
    } catch {
      showWelcome();
    }
  }

  /* 更早的消息按需加载：用当前第一条消息的 id 作为 before_id 往前翻 */
  function prependLoadMore(messagesEl, convId, firstId) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-sm load-more-msgs";
    btn.textContent = "加载更早的消息";
    messagesEl.prepend(btn);
    btn.addEventListener("click", async () => {
      if (btn.disabled) return;
      btn.disabled = true;
      btn.textContent = "加载中…";
      const anchor = messagesEl.querySelector(".msg");
      try {
        const data = await API.request("GET",
          `/api/chat/sessions/${convId}/messages?limit=${MSG_PAGE}&before_id=${firstId}`);
        const older = Array.isArray(data) ? data : ((data && data.items) || []);
        const prevHeight = messagesEl.scrollHeight;
        older.forEach((m) => {
          const el = renderMessage(m.role, m.content, m.sources, m.interrupted, m.id, m.feedback);
          if (el && anchor) messagesEl.insertBefore(el, anchor);
        });
        // 往上补内容后保持当前阅读位置不跳
        messagesEl.scrollTop += messagesEl.scrollHeight - prevHeight;
        const more = !Array.isArray(data) && !!(data && data.has_more);
        if (more && older.length) {
          firstId = older[0].id;
          btn.disabled = false;
          btn.textContent = "加载更早的消息";
        } else {
          btn.remove();
        }
      } catch (err) {
        btn.disabled = false;
        btn.textContent = "加载更早的消息";
        Common.toast("加载更早的消息失败：" + err.message, "error");
      }
    });
  }

  async function renameSession(id) {
    const box = Common.openModal(`
      <div class="modal-header">
        <h3>重命名会话</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      <input class="form-control" id="rename-input" maxlength="40" placeholder="输入新名称">
      <div class="modal-footer">
        <button class="btn" data-cancel>取消</button>
        <button class="btn btn-primary" data-ok>保存</button>
      </div>`);
    const input = box.querySelector("#rename-input");
    const current = document.querySelector(`.session-item[data-id="${id}"] .s-title`);
    if (current) input.value = current.textContent;
    input.focus();
    input.select();
    box.querySelector("[data-close]").onclick = () => Common.closeModal();
    box.querySelector("[data-cancel]").onclick = () => Common.closeModal();
    box.querySelector("[data-ok]").onclick = async () => {
      const title = input.value.trim();
      if (!title) return;
      const okBtn = box.querySelector("[data-ok]");
      okBtn.disabled = true;
      okBtn.textContent = "保存中…";
      try {
        await API.request("PATCH", `/api/chat/sessions/${id}`, { title });
        Common.closeModal();
        loadSessions();
      } catch (err) {
        Common.toast("重命名失败：" + err.message, "error");
        okBtn.disabled = false;
        okBtn.textContent = "保存";
      }
    };
  }

  async function deleteSession(id, title) {
    const ok = await Common.confirmDialog({
      title: "删除会话",
      message: `将删除会话「${title || "#" + id}」及其全部消息，不可恢复。确认？`,
      okText: "删除",
    });
    if (!ok) return;
    try {
      await API.request("DELETE", `/api/chat/sessions/${id}`);
      if (currentConvId === id) {
        stopStream();  // 删除生成中的会话时停止生成
        currentConvId = null;
      }
      loadSessions();
    } catch (err) {
      Common.toast("删除失败：" + err.message, "error");
    }
  }

  // ---------- 引用来源 ----------
  // 兼容两种后端返回：数组，或未解析的 JSON 字符串
  function normalizeSources(sources) {
    if (Array.isArray(sources)) return sources;
    if (typeof sources === "string") {
      try {
        const arr = JSON.parse(sources);
        return Array.isArray(arr) ? arr : [];
      } catch { return []; }
    }
    return [];
  }

  const ICON_DOC = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
  const ICON_ARROW = '<svg class="src-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>';

  // 在消息气泡下方挂一个「参考了 N 份资料」，默认收起
  function renderSources(bodyEl, sources) {
    sources = normalizeSources(sources);
    if (!bodyEl || !sources.length) return;
    if (bodyEl.querySelector(".msg-sources")) return;  // 补生成时不重复插入
    const wrap = document.createElement("div");
    wrap.className = "msg-sources";
    const items = sources.map((s) => {
      const documentName = s.filename || s.document || s.title || "未知来源";
      const chapter = s.section || s.chapter || "";
      const page = s.page ?? s.page_number ?? "";
      const chunk = s.chunk_index ?? s.chunk ?? "";
      // doc_id 是打开原文的关键；知识点条目可能没有原文文件，就不做成可点
      const docId = s.doc_id ?? s.document_id;
      const canOpen = docId !== undefined && docId !== null && docId !== "";
      return `
      <div class="src-item${canOpen ? " src-item-link" : ""}"${canOpen ? ` data-doc="${Common.escapeHtml(String(docId))}" title="点击打开原文"` : ` title="该来源没有原文文件（知识点/片段），无法打开"`}>
        <div class="src-item-head">
          <span class="src-title">${Common.escapeHtml(documentName)}</span>
          ${s.course ? `<button type="button" class="src-course" data-course="${Common.escapeHtml(s.course)}" title="查看《${Common.escapeHtml(s.course)}》知识库">${Common.escapeHtml(s.course)}</button>` : ""}
        </div>
        <div class="src-meta">
          ${chapter ? `<span>章节：${Common.escapeHtml(chapter)}</span>` : ""}
          ${page !== "" ? `<span>页码：${Common.escapeHtml(page)}</span>` : ""}
          ${chunk !== "" ? `<span>分块：${Common.escapeHtml(Number(chunk) + 1)}</span>` : ""}
        </div>
        <div class="src-snippet"><b>命中片段</b>${Common.escapeHtml(s.content || "")}</div>
      </div>`;
    }).join("");
    wrap.innerHTML = `
      <button type="button" class="src-toggle">
        ${ICON_DOC}<span>参考了 ${sources.length} 份资料</span>${ICON_ARROW}
      </button>
      <div class="src-list hidden">${items}</div>`;
    const btn = wrap.querySelector(".src-toggle");
    const list = wrap.querySelector(".src-list");
    btn.addEventListener("click", () => {
      const opened = !list.classList.toggle("hidden");
      btn.classList.toggle("open", opened);
    });
    // 点击来源里的课程名，跳到对应知识库（从答案回溯到课程资料）
    wrap.querySelectorAll(".src-course").forEach((c) => {
      c.addEventListener("click", (e) => {
        e.stopPropagation();
        App.navigate("knowledge", { course: c.dataset.course });
      });
    });
    // 来源卡可点：打开文档原文（新窗口，带鉴权走同源接口）
    wrap.querySelectorAll(".src-item[data-doc]").forEach((item) => {
      item.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        const id = item.dataset.doc;
        if (!id) return;
        window.open(`/api/documents/${encodeURIComponent(id)}/file`, "_blank", "noopener");
      });
    });
    bodyEl.appendChild(wrap);
  }

  function renderMessage(role, content, sources, interrupted, id, feedback) {
    const messagesEl = document.getElementById("chat-messages");
    if (!messagesEl) return;
    const div = document.createElement("div");
    div.className = "msg " + role;
    if (id != null) div.dataset.msgId = id;
    div.innerHTML = `
      <div class="msg-avatar">${role === "user" ? Common.avatarContent(API.getUser()) : "AI"}</div>
      <div class="msg-body">
        <div class="bubble md-body">${Markdown.render(content)}</div>
      </div>`;
    messagesEl.appendChild(div);
    if (role === "assistant") {
      renderSources(div.querySelector(".msg-body"), sources);
      if (interrupted) {
        const note = document.createElement("div");
        note.className = "msg-note";
        note.textContent = "这条回答生成时被中断了，内容可能不完整";
        div.querySelector(".msg-body").appendChild(note);
      }
      // 评价按钮：只有落库后（有 message_id）才可评；历史消息带 feedback 状态
      if (id != null) {
        renderFeedbackBar(div.querySelector(".msg-body"), id, feedback);
      }
    }
    return div;
  }

  /* 回答下方的点赞/点踩条：点一下提交评价，高亮当前选择 */
  const ICON_UP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>';
  const ICON_DOWN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zM17 2h3a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-3"/></svg>';

  /* 点踩原因：预设最常见的几类，也允许自己写，允许跳过直接提交 */
  const DOWN_REASONS = [
    { v: "inaccurate", label: "内容不准确" },
    { v: "irrelevant", label: "答非所问" },
    { v: "incomplete", label: "回答不完整" },
    { v: "unreadable", label: "排版/表述难读" },
  ];

  function askDownReason(onSubmit) {
    const box = Common.openModal(`
      <div class="modal-header">
        <h3>这条回答哪里不好？</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      <div class="fb-reasons" id="fb-reasons">
        ${DOWN_REASONS.map((r) => `<button type="button" class="fb-reason" data-reason="${r.v}">${r.label}</button>`).join("")}
      </div>
      <div class="form-group" style="margin-top:12px">
        <label for="fb-reason-extra">补充说明 <span class="muted">（可选）</span></label>
        <textarea class="form-control" id="fb-reason-extra" rows="3" maxlength="200" placeholder="具体哪里不对？例如：公式写错了、例子和问题无关"></textarea>
      </div>
      <div class="modal-footer">
        <button class="btn" data-skip>跳过，直接提交</button>
        <button class="btn btn-primary" data-ok>提交</button>
      </div>`);
    let picked = "";
    const buttons = [...box.querySelectorAll(".fb-reason")];
    const extra = box.querySelector("#fb-reason-extra");
    buttons.forEach((b) => b.addEventListener("click", () => {
      picked = picked === b.dataset.reason ? "" : b.dataset.reason;
      buttons.forEach((x) => x.classList.toggle("active", x.dataset.reason === picked));
    }));
    box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));
    box.querySelector("[data-skip]").onclick = () => { Common.closeModal(); onSubmit(""); };
    box.querySelector("[data-ok]").onclick = () => {
      const label = (DOWN_REASONS.find((r) => r.v === picked) || {}).label || "";
      const text = extra.value.trim();
      Common.closeModal();
      onSubmit([label, text].filter(Boolean).join("；"));
    };
  }

  function renderFeedbackBar(bodyEl, id, feedback) {
    if (!bodyEl || bodyEl.querySelector(".msg-feedback")) return;
    const bar = document.createElement("div");
    bar.className = "msg-feedback";
    bar.innerHTML = `
      <span class="mf-label">这条回答有帮助吗？</span>
      <button type="button" class="mf-btn" data-v="up" title="有帮助">${ICON_UP}</button>
      <button type="button" class="mf-btn" data-v="down" title="没帮助">${ICON_DOWN}</button>`;
    const setActive = (v) => {
      bar.querySelectorAll(".mf-btn").forEach((b) =>
        b.classList.toggle("active", b.dataset.v === v));
      const label = bar.querySelector(".mf-label");
      if (label) label.textContent = v === "up" ? "感谢反馈！" : v === "down" ? "已记录，我们会改进" : "这条回答有帮助吗？";
    };
    if (feedback) setActive(feedback);
    const submit = async (value, reason) => {
      // 新契约：{value:"up"|"down", reason?}；取消评价时 value 为 null
      const payload = { value: value || null };
      if (value === "down" && reason) payload.reason = reason;
      try {
        await API.request("POST", `/api/chat/messages/${id}/feedback`, payload);
        setActive(value);
        if (!value) bar.querySelector(".mf-label").textContent = "这条回答有帮助吗？";
      } catch (err) {
        Common.toast("评价失败：" + err.message, "error");
      }
    };
    bar.querySelectorAll(".mf-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const v = btn.dataset.v;
        // 再点同一档 = 取消评价，退回未评状态
        const next = btn.classList.contains("active") ? "" : v;
        if (next === "down") {
          // 点踩先问原因（可跳过），带上 reason，管理员才能定位差答
          askDownReason((reason) => submit("down", reason));
          return;
        }
        submit(next, "");
      });
    });
    bodyEl.appendChild(bar);
  }

  /* 回答生成完了但保存失败：不能当正常完成，标「未保存」并给重试入口 */
  function showSaveFailed(assistantDiv, message, onRetry) {
    const body = assistantDiv.querySelector(".msg-body");
    if (!body || body.querySelector(".msg-save-failed")) return;
    const note = document.createElement("div");
    note.className = "msg-note msg-save-failed";
    note.innerHTML = `
      <div><b>这条回答未保存</b>：${Common.escapeHtml(message || "保存时出了点问题")}。刷新后这条回答会消失，建议重试。</div>
      <div class="msg-unmatched-ops"><button type="button" class="btn btn-sm" data-retry-save>重新生成并保存</button></div>`;
    body.appendChild(note);
    note.querySelector("[data-retry-save]").addEventListener("click", () => {
      note.remove();
      if (!activeSeq) onRetry();
    });
  }

  function showWelcome() {
    const messagesEl = document.getElementById("chat-messages");
    if (!messagesEl) return;
    const demos = [
      "请用通俗的话解释一下 TCP 三次握手的过程",
      "进程和线程有什么区别？",
      "什么是冯·诺依曼结构？",
      "栈和队列有什么区别？各自适合什么场景？",
    ];
    messagesEl.innerHTML = `
      <div class="chat-welcome">
        <div class="w-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg></div>
        <h2>你好，${Common.escapeHtml(API.getUser()?.username || "同学")}</h2>
        <p>我是你的课程智能助教，会基于已有知识库回答你的问题，帮你梳理课程知识点、解析学习资料。</p>
        <div class="demo-questions">
          ${demos.map((q) => `<button type="button" class="demo-question">${Common.escapeHtml(q)}</button>`).join("")}
        </div>
      </div>`;
    messagesEl.querySelectorAll(".demo-question").forEach((btn) => {
      btn.addEventListener("click", () => {
        const inputEl = document.getElementById("chat-input");
        if (!inputEl) return;
        inputEl.value = btn.textContent.trim();
        inputEl.style.height = "auto";
        inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + "px";
        inputEl.focus();
      });
    });
  }

  async function ensureConversation(title) {
    if (currentConvId) return currentConvId;
    const conv = await API.request("POST", "/api/chat/sessions", { title: title || "新会话" });
    currentConvId = conv.id;
    return conv.id;
  }

  async function send(text, opts) {
    // 一轮没生成完就不允许再发
    if (activeSeq) return;
    const messagesEl = document.getElementById("chat-messages");
    const inputEl = document.getElementById("chat-input");
    const seq = ++streamSeq;  // 标记本次生成；切换会话后旧生成的回调不再操作界面
    activeSeq = seq;
    const silentUser = !!(opts && opts.silentUser);  // 补生成模式：不重复渲染提问
    const regenerate = !!(opts && opts.regenerate);  // 重新生成：后端不重复落提问、先删旧回答
    const courses = opts && opts.courses
      ? opts.courses
      : (selectedCourse ? [selectedCourse] : []);  // 空数组 = 全部可见知识库
    let bubble = null;
    let hadError = false;
    let errorMsg = "";
    let saveError = "";  // 收到 save_failed：回答生成成功但没落库

    setSending(true);  // 立刻切成「停止生成」

    try {
      let convId;
      if (silentUser) {
        convId = currentConvId;
      } else {
        try {
          convId = await ensureConversation(text.replace(/\s+/g, " ").slice(0, 20));
          loadSessions({ keepCurrent: true });  // 刷新列表，但不打断当前对话
        } catch (err) {
          Common.toast("会话创建失败：" + err.message, "error");
          return;
        }
        if (messagesEl.querySelector(".chat-welcome")) messagesEl.innerHTML = "";
        renderMessage("user", text);
      }
      if (!convId) {
        Common.toast("会话状态异常，刷新页面后再试一次。", "error");
        return;
      }

      const assistantDiv = document.createElement("div");
      assistantDiv.className = "msg assistant";
      assistantDiv.innerHTML = `
        <div class="msg-avatar">AI</div>
        <div class="msg-body">
          <div class="chat-phase" role="status" aria-live="polite">
            <span class="chat-phase-dot"></span>
            <span class="chat-phase-text">正在准备回答</span>
            <span class="chat-phase-time"></span>
          </div>
          <div class="bubble md-body"><span class="typing-dots"><span></span><span></span><span></span></span></div>
        </div>`;
      messagesEl.appendChild(assistantDiv);
      scrollBottom();

      bubble = assistantDiv.querySelector(".bubble");
      const phaseEl = assistantDiv.querySelector(".chat-phase");
      const phaseText = assistantDiv.querySelector(".chat-phase-text");
      const phaseTime = assistantDiv.querySelector(".chat-phase-time");
      const phaseLabels = {
        retrieval: "检索资料",
        answerability: "判断可答性",
        generation: "生成回答",
      };
      const setPhase = (evt) => {
        const label = phaseLabels[evt.phase] || "处理中";
        phaseEl.dataset.status = evt.status || "running";
        if (evt.status === "running") {
          phaseText.textContent = `正在${label}`;
          phaseTime.textContent = "";
        } else {
          phaseText.textContent = evt.status === "error" ? `${label}失败` : `${label}完成`;
          phaseTime.textContent = evt.elapsed_ms != null
            ? `${(evt.elapsed_ms / 1000).toFixed(1)} 秒`
            : "";
        }
      };
      let fullText = "";
      let renderTimer = null;

      const scheduleRender = () => {
        if (renderTimer) return;
        renderTimer = setTimeout(() => {
          bubble.innerHTML = Markdown.render(fullText);
          scrollBottom();
          renderTimer = null;
        }, 90);
      };

      const announce = (msg) => {
        const el = document.getElementById("chat-announcer");
        if (el) el.textContent = msg;
      };

      const req = API.streamAsk(convId, text, {
        onToken: (t) => {
          if (seq !== streamSeq) return;  // 已切换到其他会话，忽略旧生成
          fullText += t;
          scheduleRender();
        },
        onError: (msg) => {
          if (seq !== streamSeq) return;
          hadError = true;
          errorMsg = msg;
          phaseEl.dataset.status = "error";
          phaseText.textContent = "回答失败";
          phaseTime.textContent = "";
          announce("回答失败：" + msg);
          const errHtml = `<span style="color:var(--danger)">${Common.escapeHtml(msg)}</span>`;
          bubble.innerHTML = fullText
            ? `${Markdown.render(fullText)}<div style="margin-top:10px">${errHtml}</div>`
            : errHtml;
          scrollBottom();
        },
        onUnmatched: (evt) => {
          if (seq !== streamSeq) return;
          const body = assistantDiv.querySelector(".msg-body");
          if (!body || body.querySelector(".msg-unmatched")) return;
          // related_points 是新字段；recommended 是旧字段，两个都兼容
          const points = Array.isArray(evt.related_points) && evt.related_points.length
            ? evt.related_points
            : (Array.isArray(evt.recommended) ? evt.recommended : []);
          const tip = document.createElement("div");
          tip.className = "msg-note msg-unmatched";
          tip.innerHTML = `
            <div>${Common.escapeHtml(unmatchedReason(evt))}。已自动提交跟进，可在左侧「答疑进度」里查看。</div>
            ${points.length
              ? `<div class="msg-unmatched-points"><b>你可能想问：</b>${points.map((p) =>
                  `<button type="button" class="link-like" data-point="${Common.escapeHtml(p)}">${Common.escapeHtml(p)}</button>`).join("")}</div>`
              : ""}
            <div class="msg-unmatched-ops"><button type="button" class="btn btn-sm" data-rephrase>换个问法</button></div>`;
          body.appendChild(tip);
          tip.querySelectorAll("[data-point]").forEach((b) =>
            b.addEventListener("click", () => fillChatInput(b.dataset.point)));
          tip.querySelector("[data-rephrase]").addEventListener("click", () => fillChatInput(""));
          phaseEl.dataset.status = "unmatched";
          phaseText.textContent = "知识库未命中";
          phaseTime.textContent = "";
          scrollBottom();
        },
        onPhase: (evt) => {
          if (seq !== streamSeq) return;
          setPhase(evt);
        },
        onSources: (sources) => {
          if (seq !== streamSeq) return;
          renderSources(assistantDiv.querySelector(".msg-body"), sources);
          scrollBottom();
        },
        onSaved: (msgId) => {
          if (seq !== streamSeq) return;
          assistantDiv.dataset.msgId = msgId;
          renderFeedbackBar(assistantDiv.querySelector(".msg-body"), msgId, "");
        },
        onSaveFailed: (evt) => {
          if (seq !== streamSeq) return;
          saveError = (evt && evt.message) || "保存时出了点问题";
        },
        onDone: () => {
          if (seq !== streamSeq) return;
          if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; }
          if (!hadError) {
            bubble.innerHTML = fullText ? Markdown.render(fullText)
              : `<span style="color:var(--text-3)">已停止生成。</span>`;
            if (!phaseEl.dataset.status || phaseEl.dataset.status === "running") {
              phaseEl.dataset.status = "done";
              phaseText.textContent = "回答完成";
            }
            // 流式正文每 90ms 重绘一次，直接给正文加 aria-live 会让读屏反复打断，
            // 所以在完成时用隐藏的播报区整句念一遍
            announce("回答完成：" + fullText.replace(/\s+/g, " ").slice(0, 80));
            // 生成成功但没落库：明确标「未保存」，不要显示成正常完成
            if (saveError) showSaveFailed(assistantDiv, saveError, () => {
              send(text, { silentUser: true, regenerate: true });
            });
          } else if (!fullText) {
            bubble.innerHTML = `<span style="color:var(--danger)">${Common.escapeHtml(errorMsg)}</span>`;
          }
          // 出错且已有部分内容时，保留 onError 中“部分内容 + 错误提示”的渲染结果
          scrollBottom();
        },
      }, {
        regenerate,
        courses,
        // 空闲超时 > 后端 LLM_TIMEOUT(180s)：排队段有 10 秒心跳，生成段没有，
        // 冷启动首 token 可能一两分钟
        idleTimeout: 200_000,
        // 总超时 = 排队(120s) + 改写(8s) + 可答性(45s) + 生成(180s) + 余量
        totalTimeout: 380_000,
      });

      abortController = req.controller;  // 同步拿到 controller，从这一刻起「停止生成」能停
      await req.done;  // 本轮彻底结束后才收尾
    } catch (err) {
      hadError = true;
      errorMsg = err.message;
      if (bubble) {
        bubble.innerHTML = `<span style="color:var(--danger)">${Common.escapeHtml(err.message)}</span>`;
      } else {
        Common.toast("发送失败：" + err.message, "error");
      }
    } finally {
      // 只有自己仍是当前这一轮才复位，避免把后一轮的状态清掉
      if (activeSeq === seq) {
        activeSeq = 0;
        abortController = null;
        setSending(false);
        if (inputEl) inputEl.focus();
      }
    }
  }

  function scrollBottom() {
    const el = document.getElementById("chat-messages");
    if (el) el.scrollTop = el.scrollHeight;
  }

  function destroy() {
    stopStream();
    currentConvId = null;
  }

  return { render, destroy };
})();
