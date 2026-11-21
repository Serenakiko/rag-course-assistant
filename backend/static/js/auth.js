(async () => {
  // Cookie 会话：刷新登录页时先探测一次 /api/auth/me，已登录直接进应用
  let current = null;
  try {
    current = await API.ensureSession();
  } catch {
    // 探测失败（后端没起 / 网络抖动）：留在登录页，提交时才提示具体错误
    current = null;
  }
  if (current) {
    window.location.replace("/static/app.html");
    return;
  }

  document.querySelectorAll(".eye-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = document.getElementById(btn.dataset.target);
      if (!input) return;
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.classList.toggle("show", show);
      input.focus({ preventScroll: true });
      const len = input.value.length;
      input.setSelectionRange(len, len);
    });
  });

  // 账号固定 8 位数字，密码至少 8 位（与后端 schemas 一致）
  const ACCOUNT_RE = /^\d{8}$/;
  const PWD_MIN = 8;

  // 逐项校验，返回第一条不通过的提示；null 表示全部通过
  function validate(mode, data) {
    if (mode === "register" && !(data.username || "").trim()) {
      return { title: "用户名不能为空", message: "请输入用户名，它会显示在问答界面和后台管理里。" };
    }
    const account = (data.account || "").trim();
    if (!account) {
      return { title: "账号不能为空", message: "请输入 8 位数字账号。" };
    }
    if (!ACCOUNT_RE.test(account)) {
      const why = /^\d+$/.test(account)
        ? `账号需要正好 8 位数字，当前是 ${account.length} 位。`
        : "账号只能由数字组成，不能包含字母、空格或符号。";
      return { title: "账号格式不正确", message: why };
    }
    if (!data.password) {
      return { title: "密码不能为空", message: "请输入密码。" };
    }
    // 长度下限只约束「新密码」：登录不卡长度，否则初始化管理员的 6 位种子口令
    // 在前端就被挡住，永远登不进去（后端登录也不校验长度）。
    if (mode === "register" && data.password.length < PWD_MIN) {
      return { title: "密码太短", message: `密码至少需要 ${PWD_MIN} 位，当前只有 ${data.password.length} 位。` };
    }
    if (mode === "register") {
      if (!data.confirm_password) {
        return { title: "请确认密码", message: "请把密码再输入一遍。" };
      }
      if (data.password !== data.confirm_password) {
        return { title: "两次密码不一致", message: "两次输入的密码不一样，请重新确认。" };
      }
    }
    return null;
  }

  // 提交中 / 即将跳转：两者都拦住重复提交。以前成功后会 setTimeout 400ms 再跳转，
  // 而 finally 无条件恢复按钮，这 400ms 里用户再点一次就是重复登录请求。
  let inFlight = false;
  let leaving = false;

  async function submit(mode, form) {
    if (inFlight || leaving) return;
    const btn = form.querySelector("button[type=submit]");
    const label = btn.querySelector(".btn-label");
    const data = Object.fromEntries(new FormData(form).entries());

    const bad = validate(mode, data);
    if (bad) {
      await Common.alertDialog({ type: "warning", title: bad.title, message: bad.message });
      return;
    }

    inFlight = true;
    btn.disabled = true;
    btn.classList.add("loading");
    label.textContent = mode === "login" ? "登录中…" : "注册中…";
    try {
      if (mode === "login") {
        const resp = await API.request("POST", "/api/auth/login", {
          account: data.account.trim(),
          password: data.password,
        });
        // Cookie 已由后端 Set-Cookie 写入；内存里留存 token 只为兼容旧接口
        API.saveSession(resp.access_token || resp.token, resp.user);
        leaving = true;
        Common.toast("登录成功，欢迎回来！", "success");
        setTimeout(() => window.location.replace("/static/app.html"), 400);
      } else {
        await API.request("POST", "/api/auth/register", {
          account: data.account.trim(),
          password: data.password,
          username: (data.username || "").trim(),
        });
        leaving = true;
        Common.toast("注册成功，请登录", "success");
        setTimeout(() => window.location.replace("/static/index.html?account=" + encodeURIComponent(data.account.trim())), 400);
      }
    } catch (err) {
      await Common.alertDialog({
        type: "error",
        title: mode === "login" ? "登录失败" : "注册失败",
        message: err.message || (mode === "login" ? "登录失败，请检查账号和密码是否正确。" : "注册失败，请稍后重试。"),
      });
    } finally {
      inFlight = false;
      // 已经在跳转就不必恢复按钮，避免这 400ms 里被再点一次
      if (!leaving) {
        btn.disabled = false;
        btn.classList.remove("loading");
        label.textContent = mode === "login" ? "登 录" : "注 册";
      }
    }
  }

  const loginForm = document.getElementById("login-form");
  const registerForm = document.getElementById("register-form");
  if (loginForm) {
    loginForm.addEventListener("submit", (e) => { e.preventDefault(); submit("login", loginForm); });
    const params = new URLSearchParams(window.location.search);
    const acct = params.get("account");
    if (acct) {
      loginForm.querySelector('[name="account"]').value = acct;
      loginForm.querySelector('[name="password"]').focus();
    }
  }
  if (registerForm) {
    registerForm.addEventListener("submit", (e) => { e.preventDefault(); submit("register", registerForm); });
  }
})();
