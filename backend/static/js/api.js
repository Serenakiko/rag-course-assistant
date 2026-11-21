/* ============ API 封装：认证、请求、SSE 流式问答 ============ */
const API = (() => {
  // 会话由后端 HttpOnly Cookie 维持，JS 读不到 token；这里只在内存里缓存
  // user / token。页面刷新后内存清空，由 ensureSession() 调 /api/auth/me
  // 让浏览器自动带上 Cookie 恢复登录态，不再读写 localStorage。
  let memoryToken = "";
  let currentUser = null;
  let sessionPromise = null;

  function getToken() { return memoryToken; }

  function getUser() { return currentUser; }

  function saveSession(token, user) {
    if (token) memoryToken = token;
    if (user) currentUser = user;
  }

  function clearSession() {
    memoryToken = "";
    currentUser = null;
    sessionPromise = null;
  }

  function goLogin() {
    clearSession();
    window.location.replace("/static/index.html");
  }

  /**
   * 探测当前会话（Cookie 由浏览器自动携带）。
   * 返回 user；未登录返回 null。并发调用共享同一个 Promise。
   */
  function ensureSession() {
    if (currentUser) return Promise.resolve(currentUser);
    if (!sessionPromise) {
      sessionPromise = request("GET", "/api/auth/me", undefined, { skipAuthRedirect: true })
        .then((user) => { currentUser = user; return user; })
        .catch((err) => {
          // 探测失败不缓存：否则后端重启/网络抖动一次，整个页面第二次探测直接拿旧结果
          sessionPromise = null;
          // 401 才是「真的没登录」；网络不通、5xx 不应该把人踢回登录页
          if (err && err.status === 401) return null;
          const e = new Error(err && err.message ? err.message : "无法确认登录状态");
          e.sessionProbeFailed = true;
          throw e;
        });
    }
    return sessionPromise;
  }

  /* 退出登录：通知后端删 Cookie，再清本地内存；网络失败也照样退出。 */
  async function logout() {
    try {
      await request("POST", "/api/auth/logout", undefined, { skipAuthRedirect: true });
    } catch {
      /* 忽略：本地会话仍然要清掉 */
    }
    clearSession();
  }

  async function request(method, url, body, { form = false, skipAuthRedirect = false } = {}) {
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
      // same-origin：同源请求带上 HttpOnly 会话 Cookie
      resp = await fetch(url, { method, headers, body: payload, credentials: "same-origin" });
    } catch (err) {
      if (err && err.name === "AbortError") throw err;
      const e = new Error("网络请求失败，请确认后端服务已启动");
      e.status = 0;   // 0 = 连不上后端，供调用方区分「没登录」
      throw e;
    }
    // 登录/注册接口的 401 是「账号密码错误」；探测会话/登出的 401 也不跳转
    const isAuthEntry =
      url.startsWith("/api/auth/login") ||
      url.startsWith("/api/auth/register") ||
      url.startsWith("/api/auth/me") ||
      url.startsWith("/api/auth/logout");
    if (resp.status === 401 && !isAuthEntry && !skipAuthRedirect) {
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
      const e = new Error(msg);
      e.status = resp.status;
      // 保留完整响应体：个别接口的错误里带结构化信息（如回收站恢复的
      // failed 清单放在 detail 里），调用方需要拿它渲染明细
      e.body = data;
      throw e;
    }
    return data;
  }

  /**
   * 流式问答：POST /api/chat/ask，按 SSE 事件回调。
   * handlers: { onToken, onSources, onError, onUnmatched, onPhase, onDone, onSaved, onSaveFailed }
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
    // 总时长必须比后端 LLM_TIMEOUT（180s）**略长**：两者相等时，前端会在后端收尾前后
    // 抢跑 abort，最后一段 token 和 saved 事件（message_id）被丢掉，而 AbortError 走
    // finish() 不报错，用户以为正常结束，被截掉的那半句就当没发生。
    const totalTimeout = opts.totalTimeout != null ? opts.totalTimeout : 380_000; // 排队 + 判定 + 生成 + 余量
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
      // 置 finished：后端超时是「先发 error 事件再 close」，前端 evt.type==="error"
      // 直接调 onError，若不拦后续 token，「已超时」提示会与继续吐字同时出现，
      // 且 idle 超时 + 异常会重复触发 onError。
      finished = true;
      clearTimeout(idleTimer);
      clearTimeout(totalTimer);
      if (handlers.onError) handlers.onError(msg);
      resolveDone();
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
        const streamHeaders = { "Content-Type": "application/json" };
        // 内存里有 token 就照旧带上，兼容未升级的部署；Cookie 会由浏览器自动携带
        if (getToken()) streamHeaders["Authorization"] = "Bearer " + getToken();
        const resp = await fetch("/api/chat/ask", {
          method: "POST",
          headers: streamHeaders,
          credentials: "same-origin",
          body: JSON.stringify({
            conversation_id: conversationId,
            message,
            regenerate: !!opts.regenerate,
            courses: opts.courses || [],
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
                if (evt.type === "phase" && handlers.onPhase) handlers.onPhase(evt);
                else if (evt.type === "token" && handlers.onToken) handlers.onToken(evt.content);
                else if (evt.type === "sources" && handlers.onSources) handlers.onSources(evt.sources);
                else if (evt.type === "unmatched" && handlers.onUnmatched) handlers.onUnmatched(evt);
                else if (evt.type === "saved" && handlers.onSaved) handlers.onSaved(evt.message_id);
                // 回答生成完了但没落库：不能当正常完成，交给页面标「未保存」并给重试入口
                else if (evt.type === "save_failed" && handlers.onSaveFailed) handlers.onSaveFailed(evt);
                else if (evt.type === "error") {
                  // 后端超时/失败：收到 error 后立即中止读取，避免「已超时」提示
                  // 与后续 token 同时出现、onError 被重复触发。
                  fail(evt.message);
                  controller.abort();
                  return;
                }
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

  return {
    getToken, getUser, saveSession, clearSession, goLogin,
    ensureSession, logout, request, streamAsk,
  };
})();
