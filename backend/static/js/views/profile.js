/* ============ 视图：个人中心 ============ */
const ProfileView = (() => {

  const EYE_ON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/><path d="M4 4l16 16"/></svg>';

  function render(container) {
    const user = API.getUser();
    container.innerHTML = `
      <div style="max-width:520px;margin:0 auto">
          <div class="card p-card">
            <div class="row-between p-card-head">
              <div style="font-size:15px;font-weight:600;color:var(--text-1)">个人信息</div>
              <button class="btn btn-sm btn-light-blue" id="p-edit">编辑资料</button>
            </div>
            <div class="p-card-section">
              <div style="display:flex;flex-direction:column;align-items:center">
                <div id="p-avatar-main">${Common.avatarHtml(user, 88)}</div>
                <div class="mt-8"><button class="btn btn-sm btn-light-blue" id="p-avatar">更换头像</button></div>
              </div>
              <div style="margin-top:18px">
                <div class="form-group"><label>账号</label><input class="form-control" id="p-username" value="${Common.escapeHtml(user.username)}" readonly></div>
                <div class="form-group"><label>用户名</label><input class="form-control" id="p-name-box" value="${Common.escapeHtml(user.real_name || "")}" readonly></div>
                <div class="form-group"><label>角色</label><input class="form-control" value="${user.role === "admin" ? "管理员" : "学生"}" readonly></div>
              </div>
            </div>
          </div>

          <div class="card p-card" style="margin-top:16px">
            <div class="p-card-head">
              <div style="font-size:15px;font-weight:600;color:var(--text-1)">安全设置</div>
            </div>
            <div class="p-card-section">
              <div class="form-group">
                <label>原密码</label>
                <div class="p-pwd-wrap">
                  <input class="form-control p-pwd-input" id="p-old" type="password" placeholder="请输入原密码" autocomplete="new-password">
                  <button type="button" class="p-pwd-toggle" data-toggle="p-old" aria-label="显示或隐藏密码">${EYE_ON}</button>
                </div>
              </div>
              <div class="form-group">
                <label>新密码</label>
                <div class="p-pwd-wrap">
                  <input class="form-control p-pwd-input" id="p-new" type="password" placeholder="请输入新密码" autocomplete="new-password">
                  <button type="button" class="p-pwd-toggle" data-toggle="p-new" aria-label="显示或隐藏密码">${EYE_ON}</button>
                </div>
              </div>
              <div class="form-group">
                <label>确认密码</label>
                <div class="p-pwd-wrap">
                  <input class="form-control p-pwd-input" id="p-new2" type="password" placeholder="请确认新密码" autocomplete="new-password">
                  <button type="button" class="p-pwd-toggle" data-toggle="p-new2" aria-label="显示或隐藏密码">${EYE_ON}</button>
                </div>
              </div>
              <div style="display:flex;justify-content:flex-end">
                <button class="btn btn-light-blue" id="p-pwd-save">修改密码</button>
              </div>
            </div>
          </div>
        </div>`;

    document.getElementById("p-avatar").addEventListener("click", () => openAvatarModal());

    let editing = false;
    document.getElementById("p-edit").addEventListener("click", async () => {
      const btn = document.getElementById("p-edit");
      const uName = document.getElementById("p-username");
      const rName = document.getElementById("p-name-box");
      if (!editing) {
        editing = true;
        uName.readOnly = false;
        rName.readOnly = false;
        btn.textContent = "保存";
        uName.focus();
        return;
      }
      const username = uName.value.trim();
      const realName = rName.value.trim();
      if (!username) return Common.toast("账号不能为空", "error");
      try {
        const u = await API.request("PUT", "/api/auth/profile", { username, real_name: realName });
        API.saveSession(API.getToken(), { ...API.getUser(), ...u });
        App.refreshUserInfo();
        Common.toast("资料已更新", "success");
        render(container);
      } catch (err) { Common.toast(err.message, "error"); }
    });

    document.querySelectorAll(".p-pwd-toggle").forEach((btn) => {
      btn.addEventListener("click", () => {
        const inp = document.getElementById(btn.dataset.toggle);
        const show = inp.type === "password";
        inp.type = show ? "text" : "password";
        btn.innerHTML = show ? EYE_OFF : EYE_ON;
      });
    });

    document.getElementById("p-pwd-save").addEventListener("click", async () => {
      const oldPwd = document.getElementById("p-old").value;
      const newPwd = document.getElementById("p-new").value;
      const newPwd2 = document.getElementById("p-new2").value;
      if (!oldPwd || !newPwd) return Common.toast("请填写完整", "error");
      if (newPwd.length < 6) return Common.toast("新密码至少 6 位", "error");
      if (newPwd !== newPwd2) return Common.toast("两次输入的新密码不一致", "error");
      try {
        await API.request("POST", "/api/auth/change-password", { old_password: oldPwd, new_password: newPwd });
        Common.toast("密码已更新，请重新登录", "success");
        setTimeout(() => API.goLogin(), 800);
      } catch (err) { Common.toast(err.message, "error"); }
    });
  }

  /* 点击头部/侧边栏头像时弹出"更换头像"弹窗（与设置页同一套逻辑） */
  function openAvatarModal() {
    const user = API.getUser();
    Common.openModal(`
      <div class="modal-header">
        <h3>更换头像</h3>
        <button class="modal-close" data-close>×</button>
      </div>
      <div class="row" style="gap:16px">
        <div id="av-preview">${Common.avatarHtml(user, 64)}</div>
        <div>
          <div class="row" style="gap:10px">
            <label class="btn" style="cursor:pointer">上传图片<input type="file" id="av-file" accept="image/png,image/jpeg,image/gif,image/webp" hidden></label>
            <button class="btn" id="av-reset" ${user.avatar ? "" : "disabled"}>恢复默认</button>
          </div>
          <div class="muted mt-8">支持 png/jpg/gif/webp，不超过 2MB</div>
        </div>
      </div>
      <div class="modal-footer"><button class="btn" data-close>关闭</button></div>
    `, { onOpen: (box) => {
      box.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", Common.closeModal));

      const saveUser = (u) => {
        API.saveSession(API.getToken(), { ...API.getUser(), ...u });
        App.refreshUserInfo();
        const preview = box.querySelector("#av-preview");
        if (preview) preview.innerHTML = Common.avatarHtml(u, 64);
        const main = document.getElementById("p-avatar-main");
        if (main) main.innerHTML = Common.avatarHtml(u, 88);
        const resetBtn = box.querySelector("#av-reset");
        if (resetBtn) resetBtn.disabled = !u.avatar;
      };

      const setAvatar = async (value) => {
        try {
          const u = await API.request("PUT", "/api/auth/profile", { avatar: value });
          saveUser(u);
          Common.toast("头像已更新", "success");
        } catch (err) { Common.toast(err.message, "error"); }
      };

      box.querySelector("#av-reset").addEventListener("click", () => setAvatar(""));
      box.querySelector("#av-file").addEventListener("change", async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        if (file.size > 2 * 1024 * 1024) return Common.toast("图片不能超过 2MB", "error");
        const fd = new FormData();
        fd.append("file", file);
        try {
          const u = await API.request("POST", "/api/auth/avatar", fd, { form: true });
          saveUser(u);
          Common.toast("头像已更新", "success");
        } catch (err) { Common.toast(err.message, "error"); }
        e.target.value = "";
      });
    }});
  }

  return { render, openAvatarModal };
})();
