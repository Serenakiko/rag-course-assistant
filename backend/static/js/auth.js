(() => {
  if (API.getToken() && API.getUser()) {
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

  // 账号固定 8 位数字，密码至少 6 位
  const USERNAME_RE = /^\d{8}$/;
  const PWD_MIN = 6;

  // 逐项校验，返回第一条不通过的提示；null 表示全部通过
  function validate(mode, data) {
    if (mode === "register" && !(data.real_name || "").trim()) {
      return { title: "用户名不能为空", message: "请输入用户名，它会显示在问答界面和后台管理里。" };
    }
    const username = (data.username || "").trim();
    if (!username) {
      return { title: "账号不能为空", message: "请输入 8 位数字账号。" };
    }
    if (!USERNAME_RE.test(username)) {
      const why = /^\d+$/.test(username)
        ? `账号需要正好 8 位数字，当前是 ${username.length} 位。`
        : "账号只能由数字组成，不能包含字母、空格或符号。";
      return { title: "账号格式不正确", message: why };
    }
    if (!data.password) {
      return { title: "密码不能为空", message: `请输入密码，长度至少 ${PWD_MIN} 位。` };
    }
    if (data.password.length < PWD_MIN) {
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

  async function submit(mode, form) {
    const btn = form.querySelector("button[type=submit]");
    const label = btn.querySelector(".btn-label");
    const data = Object.fromEntries(new FormData(form).entries());

    const bad = validate(mode, data);
    if (bad) {
      await Common.alertDialog({ type: "warning", title: bad.title, message: bad.message });
      return;
    }

    btn.disabled = true;
    btn.classList.add("loading");
    label.textContent = mode === "login" ? "登录中…" : "注册中…";
    try {
      if (mode === "login") {
        const resp = await API.request("POST", "/api/auth/login", {
          username: data.username.trim(),
          password: data.password,
        });
        API.saveSession(resp.token, resp.user);
        Common.toast("登录成功，欢迎回来！", "success");
        setTimeout(() => window.location.replace("/static/app.html"), 400);
      } else {
        await API.request("POST", "/api/auth/register", {
          username: data.username.trim(),
          password: data.password,
          real_name: (data.real_name || "").trim(),
        });
        Common.toast("注册成功，请登录", "success");
        setTimeout(() => window.location.replace("/static/index.html?username=" + encodeURIComponent(data.username.trim())), 400);
      }
    } catch (err) {
      await Common.alertDialog({
        type: "error",
        title: mode === "login" ? "登录失败" : "注册失败",
        message: err.message || "请稍后重试。",
      });
    } finally {
      btn.disabled = false;
      btn.classList.remove("loading");
      label.textContent = mode === "login" ? "登 录" : "注 册";
    }
  }

  const loginForm = document.getElementById("login-form");
  const registerForm = document.getElementById("register-form");
  if (loginForm) {
    loginForm.addEventListener("submit", (e) => { e.preventDefault(); submit("login", loginForm); });
    const params = new URLSearchParams(window.location.search);
    const uname = params.get("username");
    if (uname) {
      loginForm.querySelector('[name="username"]').value = uname;
      loginForm.querySelector('[name="password"]').focus();
    }
  }
  if (registerForm) {
    registerForm.addEventListener("submit", (e) => { e.preventDefault(); submit("register", registerForm); });
  }
})();
