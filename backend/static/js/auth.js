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

  async function submit(mode, form) {
    const btn = form.querySelector("button[type=submit]");
    const label = btn.querySelector(".btn-label");
    const data = Object.fromEntries(new FormData(form).entries());
    if (!/^\d{1,8}$/.test(data.username.trim())) {
      Common.toast("账号需为最多 8 位数字", "error");
      return;
    }
    if (data.password.length < 6) {
      Common.toast("密码至少 6 位", "error");
      return;
    }
    if (mode === "register" && data.password !== data.confirm_password) {
      Common.toast("两次输入的密码不一致", "error");
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
      Common.toast(err.message, "error");
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
