/* ============ API 封装：认证、请求、SSE 流式问答 ============ */
const API = (() => {
  const TOKEN_KEY = "rag_token";
  const USER_KEY = "rag_user";

  function getToken() { return localStorage.getItem(TOKEN_KEY) || ""; }

  function getUser() {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || "null"); }
    catch { return null; }
  }

  function saveSession(token, user) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  }

  function clearSession() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }

  function goLogin() {
    clearSession();
    window.location.href = "/static/index.html";
  }

  async function request(method, url, body, { form = false } = {}) {
    const headers = {};
    const token = getToken();
    if (token) headers["Authorization"] = "Bearer " + token;
    let payload;
    if (form) {
      payload = body; // FormData
    } else if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    let resp;
    try {
      resp = await fetch(url, { method, headers, body: payload });
    } catch {
      throw new Error("网络请求失败，请确认后端服务已启动");
    }
    // 登录/注册接口的 401 是「账号密码错误」，不是会话过期，
    // 顺手跳转会重载页面，把要提示的弹窗一起冲掉
    const isAuthEntry = url.startsWith("/api/auth/login") || url.startsWith("/api/auth/register");
    if (resp.status === 401 && !isAuthEntry) {
      goLogin();
      throw new Error("登录已过期，请重新登录");
    }
    const text = await resp.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!resp.ok) {
      // 422 是校验失败，detail 是 Pydantic 的数组，直接展示会变成一串 JSON
      let msg;
      if (resp.status === 422) {
        msg = "提交的内容不符合要求，请检查后重试";
      } else if (data && data.detail) {
        msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      } else {
        msg = `请求失败（${resp.status}）`;
      }
      throw new Error(msg);
    }
    return data;
  }

  /**
   * 流式问答：POST /api/chat/ask，按 SSE 事件回调。
   * handlers: { onToken, onSources, onError, onUnmatched, onDone }
   * opts: { idleTimeout, totalTimeout }（毫秒）——长时间无内容 / 总时长超限时自动中止，
   *       避免网络或模型异常导致页面一直卡在“生成中”；
   *       regenerate——重新生成（后端不重复保存提问，先删掉旧回答）
   *
   * 返回 { controller, done }：
   *   controller —— 同步拿到，建连阶段（还没收到响应头）也能 abort；
   *   done —— 这一轮彻底结束时 resolve，不会 reject，错误统一走 onError。
   */
  function streamAsk(conversationId, message, handlers, opts = {}) {
    const idleTimeout = opts.idleTimeout != null ? opts.idleTimeout : 60_000;  // 60s 无新内容
    const totalTimeout = opts.totalTimeout != null ? opts.totalTimeout : 180_000; // 总时长 3 分钟
    const controller = new AbortController();
    let finished = false;
    let idleTimer = null;
    let totalTimer = null;
    let resolveDone;
    const done = new Promise((resolve) => { resolveDone = resolve; });

    const finish = () => {
      if (finished) return;
      finished = true;
      clearTimeout(idleTimer);
      clearTimeout(totalTimer);
      if (handlers.onDone) handlers.onDone();
      resolveDone();
    };
    const fail = (msg) => {
      if (finished) return;
      if (handlers.onError) handlers.onError(msg);
    };
    const resetIdle = () => {
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        fail("长时间没收到模型响应，已经停下来了。稍后重新发一次试试。");
        controller.abort();
      }, idleTimeout);
    };

    resetIdle();  // 等首个响应头期间也算空闲计时
    totalTimer = setTimeout(() => {
      fail("回答生成时间太长，已经停下来了。稍后重新发一次试试。");
      controller.abort();
    }, totalTimeout);

    (async () => {
      try {
        const resp = await fetch("/api/chat/ask", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + getToken(),
          },
          body: JSON.stringify({
            conversation_id: conversationId,
            message,
            regenerate: !!opts.regenerate,
          }),
          signal: controller.signal,
        });
        if (resp.status === 401) { goLogin(); throw new Error("登录已过期"); }
        if (!resp.ok) {
          let detail = "流式请求失败";
          try { const j = await resp.json(); detail = j.detail || detail; } catch { /* ignore */ }
          throw new Error(detail);
        }
        const reader = resp.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";
        resetIdle();

        while (true) {
          const { done: streamDone, value } = await reader.read();
          if (streamDone) break;
          resetIdle();  // 任何数据到达都重置空闲计时
          buffer += decoder.decode(value, { stream: true });
          let idx;
          while ((idx = buffer.indexOf("\n\n")) >= 0) {
            const raw = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            const lines = raw.split("\n").filter((l) => l.startsWith("data: "));
            for (const line of lines) {
              try {
                const evt = JSON.parse(line.slice(6));
                if (evt.type === "token" && handlers.onToken) handlers.onToken(evt.content);
                else if (evt.type === "sources" && handlers.onSources) handlers.onSources(evt.sources);
                else if (evt.type === "unmatched" && handlers.onUnmatched) handlers.onUnmatched(evt);
                else if (evt.type === "error" && handlers.onError) handlers.onError(evt.message);
              } catch { /* 忽略无法解析的事件 */ }
            }
          }
        }
        finish();
      } catch (err) {
        if (err && err.name === "AbortError") finish();
        else { fail((err && err.message) || "流式读取失败"); finish(); }
      }
    })();

    return { controller, done };
  }

  return { getToken, getUser, saveSession, clearSession, goLogin, request, streamAsk };
})();
