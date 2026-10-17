const ChatView = (() => {
  let currentConvId = null;
  let abortController = null;
  let streamSeq = 0;  // 生成序号：切换会话中止旧生成后，旧回调不再操作界面
  let pendingConvId = null;  // 记录"生成未完成"的会话：返回该会话时自动重新生成

  function render(container) {
    container.innerHTML = `
      <div class="chat-layout">
        <aside class="chat-side">
          <button class="chat-new" id="chat-new">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            <span>新对话</span>
          </button>
          <div style="padding:0 14px 8px">
            <select class="form-control" id="chat-kb-select" style="width:100%;padding:7px 10px;font-size:13px">
              <option value="">全部知识库</option>
            </select>
          </div>
          <div class="chat-sessions" id="chat-sessions"></div>
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
      if (abortController) {
        pendingConvId = currentConvId;  // 记录未完成生成的会话，便于返回时补生成
        abortController.abort();  // 先停止正在进行的生成
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
      if (abortController) {  // 生成中：点击按钮 = 停止生成
        abortController.abort();
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
        if (!abortController) doSend();  // 生成中按 Enter 不触发停止，避免误触
      }
    });

    loadSessions();
    // 知识库下拉：加载已有知识库选项；切换后按对应库刷新历史会话
    API.request("GET", "/api/knowledge/courses").then((courses) => {
      const sel = document.getElementById("chat-kb-select");
      if (!sel) return;
      (courses || []).forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c; opt.textContent = c;
        sel.appendChild(opt);
      });
    }).catch(() => {});
    document.getElementById("chat-kb-select").addEventListener("change", () => {
      if (abortController) {
        pendingConvId = currentConvId;
        abortController.abort();
      }
      currentConvId = null;
      loadSessions();
    });
  }

  async function loadSessions(opts) {
    const listEl = document.getElementById("chat-sessions");
    if (!listEl) return;
    const keepCurrent = !!(opts && opts.keepCurrent);  // 提问后刷新：不覆盖当前对话界面
    const kbSel = document.getElementById("chat-kb-select");
    const kb = kbSel && kbSel.value ? kbSel.value : "";
    let sessions = [];
    try {
      sessions = await API.request("GET", "/api/chat/sessions" + (kb ? `?kb=${encodeURIComponent(kb)}` : ""));
    } catch { /* 静默 */ }
    renderSessionList(sessions);
    if (sessions.length === 0) {
      if (!keepCurrent) showWelcome();
    } else if (!currentConvId || !sessions.some((s) => s.id === currentConvId)) {
      if (!keepCurrent) openSession(sessions[0].id);
    }
  }

  function renderSessionList(sessions) {
    const listEl = document.getElementById("chat-sessions");
    if (!listEl) return;
    listEl.innerHTML = sessions.map((s) => `
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

  async function openSession(id) {
    if (abortController) {
      pendingConvId = currentConvId;  // 切换会话时记录未完成生成的会话
      abortController.abort();  // 先停止当前生成，避免占用模型
    }
    currentConvId = id;
    document.querySelectorAll(".session-item").forEach((el) =>
      el.classList.toggle("active", Number(el.dataset.id) === id));
    const messagesEl = document.getElementById("chat-messages");
    if (!messagesEl) return;
    try {
      const msgs = await API.request("GET", `/api/chat/sessions/${id}/messages`);
      messagesEl.innerHTML = "";
      msgs.forEach((m) => renderMessage(m.role, m.content, m.sources));
      scrollBottom();
      // 返回“生成未完成”的会话：最后一条是提问但没有回答时，自动重新生成
      if (id === pendingConvId) {
        pendingConvId = null;
        const last = msgs[msgs.length - 1];
        if (last && last.role === "user") {
          send(last.content, { silentUser: true });
        }
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
        if (abortController) abortController.abort();  // 删除生成中的会话时停止生成
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

  function renderMessage(role, content, sources) {
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
    const messagesEl = document.getElementById("chat-messages");
    const sendBtn = document.getElementById("send-btn");
    const inputEl = document.getElementById("chat-input");
    const seq = ++streamSeq;  // 标记本次生成；切换会话后旧生成的回调将不再操作界面
    const silentUser = !!(opts && opts.silentUser);  // 补生成模式：不重复渲染提问、不新建会话

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

    const assistantDiv = document.createElement("div");
    assistantDiv.className = "msg assistant";
    assistantDiv.innerHTML = `
      <div class="msg-avatar">AI</div>
      <div class="msg-body">
        <div class="bubble md-body"><span class="typing-dots"><span></span><span></span><span></span></span></div>
      </div>`;
    messagesEl.appendChild(assistantDiv);
    scrollBottom();

    const bubble = assistantDiv.querySelector(".bubble");
    let fullText = "";
    let renderTimer = null;
    let hadError = false;
    let errorMsg = "";

    const scheduleRender = () => {
      if (renderTimer) return;
      renderTimer = setTimeout(() => {
        bubble.innerHTML = Markdown.render(fullText);
        scrollBottom();
        renderTimer = null;
      }, 90);
    };

    // 生成中：发送按钮变为可点击的“停止生成”
    sendBtn.title = "停止生成";
    sendBtn.style.background = "var(--danger)";
    sendBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';

    try {
      // 按当前选择的知识库检索回答（不选则为全部知识库）
      const kbSel = document.getElementById("chat-kb-select");
      const knowledgeBases = kbSel && kbSel.value ? [kbSel.value] : [];
      abortController = await API.streamAsk(convId, text, {
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
      }, { knowledgeBases });
    } catch (err) {
      if (seq !== streamSeq) return;
      hadError = true;
      errorMsg = err.message;
      bubble.innerHTML = `<span style="color:var(--danger)">${Common.escapeHtml(err.message)}</span>`;
    } finally {
      if (seq !== streamSeq) return;  // 已被新生成/切换会话取代，不恢复界面状态
      sendBtn.title = "发送";
      sendBtn.style.background = "";
      sendBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3.4 20.4l17.45-7.48a1 1 0 0 0 0-1.84L3.4 3.6a.993.993 0 0 0-1.39.91L2 9.12c0 .5.37.93.87.99L17 12 2.87 13.88c-.5.07-.87.5-.87 1l.01 4.61c0 .71.73 1.2 1.39.91z"/></svg>';
      abortController = null;
      inputEl.focus();
    }
  }

  function scrollBottom() {
    const el = document.getElementById("chat-messages");
    if (el) el.scrollTop = el.scrollHeight;
  }

  function destroy() {
    if (abortController) abortController.abort();
    currentConvId = null;
  }

  return { render, destroy };
})();
