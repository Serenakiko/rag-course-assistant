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
  const convMeta = new Map(); // id -> 会话概要：当前会话被挤出这一页时还能钉在最上面
  const SIDEBAR_LIMIT = 20;   // 侧边栏只取这么多条，再多去「对话历史」页
  const SEARCH_LIMIT = 50;    // 搜索结果最多取这么多条

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

  // 中止当前生成（切换会话 / 新建对话 / 删除会话 / 点「停止生成」）。
  // 顺手把 activeSeq 立刻放开：紧接着的补生成不会被这一轮的收尾卡住。
  function stopStream() {
    if (abortController) abortController.abort();
    abortController = null;
    if (activeSeq) {
      activeSeq = 0;
      setSending(false);
    }
  }

  function render(container) {
    container.innerHTML = `
      <div class="chat-layout">
        <aside class="chat-side">
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

    loadSessions();
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

  /* 只取当前这一页 + 总数：以前是全量拉下来再前端切，会话一多就要传几百 KB */
  async function fetchSessionPage() {
    const q = sessionQuery.trim();
    const params = new URLSearchParams({ limit: String(q ? SEARCH_LIMIT : SIDEBAR_LIMIT), offset: "0" });
    if (q) params.set("q", q);
    try {
      const data = await API.request("GET", `/api/chat/sessions?${params.toString()}`);
      sessionItems = (data && data.items) || [];
      sessionTotal = (data && data.total) || 0;
      sessionItems.forEach((it) => convMeta.set(it.id, it));
      // 这个 Map 只用来「把正在看的会话钉在列表顶部」，没必要把几百个会话全记着
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
          deleteSession(Number(item.dataset.id));
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
    // 底部入口常驻：会话不足 20 条时以前这块是空的，列表又被容器底边截断，
    // 最后一条看着像丢了。现在始终给一个明确的「查看全部会话」出口。
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
      pendingConvId = currentConvId;  // 切换会话时记录未完成生成的会话
      stopStream();  // 先停止当前生成，避免占用模型
    }
    currentConvId = id;
    document.querySelectorAll(".session-item").forEach((el) =>
      el.classList.toggle("active", Number(el.dataset.id) === id));
    const messagesEl = document.getElementById("chat-messages");
    if (!messagesEl) return;
    try {
      const msgs = await API.request("GET", `/api/chat/sessions/${id}/messages`);
      messagesEl.innerHTML = "";
      msgs.forEach((m) => renderMessage(m.role, m.content, m.sources, m.interrupted));
      scrollBottom();
      // 返回“生成未完成”的会话：补生成。带 regenerate，后端不再重复落一条提问。
      const last = msgs[msgs.length - 1];
      if (last && last.role === "user" && !resumedConvs.has(id)) {
        // 只有提问没有回答的会话（生成中断 / 失败没落库）：进来自动补一条回答。
        // 以前只在本次页面里记着 pendingConvId，刷新后就永远停在空白。
        resumedConvs.add(id);
        pendingConvId = null;
        send(last.content, { silentUser: true, regenerate: true });
      } else if (last && last.role === "assistant" && last.interrupted && !resumedConvs.has(id)) {
        // 上次生成到一半断了（刷新页面 / 切走 / 超时），半截答案既不能续写也没法用：
        // 自动重新生成一遍（后端 regenerate 会先删掉这条半截回答）。
        resumedConvs.add(id);
        pendingConvId = null;
        const lastUserMsg = [...msgs].reverse().find((m) => m.role === "user");
        if (lastUserMsg) {
          if (messagesEl.lastElementChild) messagesEl.lastElementChild.remove();
          send(lastUserMsg.content, { silentUser: true, regenerate: true });
        }
      } else if (id === pendingConvId && last && last.role === "assistant") {
        // 中断时半截回答已经落库，先去掉了这条再从头生成，避免出现两条回答
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
      try {
        await API.request("PATCH", `/api/chat/sessions/${id}`, { title });
        Common.closeModal();
        loadSessions();
      } catch (err) {
        Common.toast("重命名失败：" + err.message, "error");
      }
    };
  }

  async function deleteSession(id) {
    const ok = await Common.confirmDialog({
      title: "删除会话",
      message: "删除后该会话的对话记录将无法恢复，确定删除吗？",
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
    const items = sources.map((s) => `
      <div class="src-item">
        <div class="src-item-head">
          <span class="src-title">${Common.escapeHtml(s.title || "未知来源")}</span>
          ${s.course ? `<span class="src-course">${Common.escapeHtml(s.course)}</span>` : ""}
        </div>
        <div class="src-snippet">${Common.escapeHtml(s.content || "")}</div>
      </div>`).join("");
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
    bodyEl.appendChild(wrap);
  }

  function renderMessage(role, content, sources, interrupted) {
    const messagesEl = document.getElementById("chat-messages");
    if (!messagesEl) return;
    const div = document.createElement("div");
    div.className = "msg " + role;
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
    }
    return div;
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
        <h2>你好，${Common.escapeHtml(API.getUser()?.real_name || "同学")}</h2>
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
    // 一轮没生成完就不允许再发。以前两轮能同时在跑：后一轮把前一轮顶掉，
    // 前一轮既没答案也没收尾，气泡就一直转圈（下个问题答了、上个永远没有）。
    if (activeSeq) return;
    const messagesEl = document.getElementById("chat-messages");
    const inputEl = document.getElementById("chat-input");
    const seq = ++streamSeq;  // 标记本次生成；切换会话后旧生成的回调将不再操作界面
    activeSeq = seq;
    const silentUser = !!(opts && opts.silentUser);  // 补生成模式：不重复渲染提问、不新建会话
    const regenerate = !!(opts && opts.regenerate);  // 重新生成：后端不重复落提问、先删旧回答
    let bubble = null;
    let hadError = false;
    let errorMsg = "";

    setSending(true);  // 立刻切成「停止生成」：建连这段空窗期再点，只会停止，不会又发一轮

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
          <div class="bubble md-body"><span class="typing-dots"><span></span><span></span><span></span></span></div>
        </div>`;
      messagesEl.appendChild(assistantDiv);
      scrollBottom();

      bubble = assistantDiv.querySelector(".bubble");
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
          const errHtml = `<span style="color:var(--danger)">${Common.escapeHtml(msg)}</span>`;
          bubble.innerHTML = fullText
            ? `${Markdown.render(fullText)}<div style="margin-top:10px">${errHtml}</div>`
            : errHtml;
          scrollBottom();
        },
        onUnmatched: (evt) => {
          if (seq !== streamSeq) return;
          const body = assistantDiv.querySelector(".msg-body");
          if (!body || body.querySelector(".msg-note")) return;
          const tip = document.createElement("div");
          tip.className = "msg-note";
          tip.innerHTML = "该问题暂未在知识库中命中，已自动提交跟进；解决后可在侧边栏「我的反馈」中查看";
          body.appendChild(tip);
          scrollBottom();
        },
        onSources: (sources) => {
          if (seq !== streamSeq) return;
          renderSources(assistantDiv.querySelector(".msg-body"), sources);
          scrollBottom();
        },
        onDone: () => {
          if (seq !== streamSeq) return;
          if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; }
          if (!hadError) {
            bubble.innerHTML = fullText ? Markdown.render(fullText)
              : `<span style="color:var(--text-3)">已停止生成。</span>`;
          } else if (!fullText) {
            bubble.innerHTML = `<span style="color:var(--danger)">${Common.escapeHtml(errorMsg)}</span>`;
          }
          // 出错且已有部分内容时，保留 onError 中“部分内容 + 错误提示”的渲染结果
          scrollBottom();
        },
      }, {
        regenerate,
        // 空闲超时必须**大于**后端的 LLM_TIMEOUT(180s)：这里等的是「一个字节都
        // 没收到」，而排队段有 10 秒一次的心跳、生成段没有。冷启动加载本地模型
        // 时首个 token 可能要一两分钟，120 秒会先把还在正常生成的请求掐掉。
        idleTimeout: 200_000,
        // 总超时 = 后端排队上限(120s) + LLM_TIMEOUT(180s) + 余量：排队超时由
        // 后端给明确错误，这里只兜底「连接彻底不动了」这种真断网的情况
        totalTimeout: 320_000,
      });

      abortController = req.controller;  // 同步拿到 controller：从这一刻起「停止生成」真的能停
      await req.done;  // 本轮彻底结束（正常 / 出错 / 被停止）后才收尾
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
