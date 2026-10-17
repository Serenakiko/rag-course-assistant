/* ============ 视图：个人中心 ============ */
const ProfileView = (() => {

  const EYE_ON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/><path d="M4 4l16 16"/></svg>';

  function render(container, editing = false) {
    const user = API.getUser();
    const roleText = user.role === "admin" ? "管理员" : "学生";
    const ro = editing ? "" : "readonly";
    container.innerHTML = `
      <div style="max-width:520px;margin:0 auto">
        <div class="card p-card">
          <div class="p-id">
            <div id="p-avatar-view">${Common.avatarHtml(user, 76)}</div>
            <button type="button" class="p-avatar-change" id="p-avatar-btn">更换头像</button>
          </div>

          <div class="p-form">
            <div class="form-group">
              <label>账号</label>
              <input class="form-control" id="p-username" value="${Common.escapeHtml(user.username)}" maxlength="8" autocomplete="off" ${ro}>
            </div>
            <div class="form-group">
              <label>用户名</label>
              <input class="form-control" id="p-name-box" value="${Common.escapeHtml(user.real_name || "")}" maxlength="32" autocomplete="off" ${ro}>
            </div>
            <div class="form-group">
              <label>角色</label>
              <input class="form-control" value="${roleText}" readonly>
            </div>

            <div class="p-divider"></div>

            <div class="form-group">
              <label>原密码</label>
              <div class="p-pwd-wrap">
                <input class="form-control p-pwd-input" id="p-old" type="password" placeholder="请输入原密码" autocomplete="new-password" ${ro}>
                <button type="button" class="p-pwd-toggle" data-toggle="p-old" aria-label="显示或隐藏密码">${EYE_ON}</button>
              </div>
            </div>
            <div class="form-group">
              <label>新密码</label>
              <div class="p-pwd-wrap">
                <input class="form-control p-pwd-input" id="p-new" type="password" placeholder="至少 6 位" autocomplete="new-password" ${ro}>
                <button type="button" class="p-pwd-toggle" data-toggle="p-new" aria-label="显示或隐藏密码">${EYE_ON}</button>
              </div>
            </div>
            <div class="form-group">
              <label>确认新密码</label>
              <div class="p-pwd-wrap">
                <input class="form-control p-pwd-input" id="p-new2" type="password" placeholder="再输入一次新密码" autocomplete="new-password" ${ro}>
                <button type="button" class="p-pwd-toggle" data-toggle="p-new2" aria-label="显示或隐藏密码">${EYE_ON}</button>
              </div>
            </div>

            <div class="p-card-actions">
              ${editing
                ? `<button class="btn" id="p-cancel">取消</button><button class="btn btn-primary" id="p-save">保存</button>`
                : `<button class="btn" id="p-edit">编辑资料</button>`}
            </div>
          </div>
        </div>
      </div>`;

    document.querySelectorAll(".p-pwd-toggle").forEach((btn) => {
      btn.addEventListener("click", () => {
        const inp = document.getElementById(btn.dataset.toggle);
        const show = inp.type === "password";
        inp.type = show ? "text" : "password";
        btn.innerHTML = show ? EYE_OFF : EYE_ON;
      });
    });

    document.getElementById("p-avatar-btn").addEventListener("click", () => openAvatarModal());

    if (!editing) {
      document.getElementById("p-edit").addEventListener("click", () => render(container, true));
      return;
    }

    document.getElementById("p-cancel").addEventListener("click", () => render(container, false));

    document.getElementById("p-save").addEventListener("click", async () => {
      const username = document.getElementById("p-username").value.trim();
      const realName = document.getElementById("p-name-box").value.trim();
      const oldPwd = document.getElementById("p-old").value;
      const newPwd = document.getElementById("p-new").value;
      const newPwd2 = document.getElementById("p-new2").value;

      if (!/^\d{8}$/.test(username)) return Common.toast("账号必须是 8 位数字", "error");

      const wantsPwd = !!(oldPwd || newPwd || newPwd2);
      if (wantsPwd) {
        if (!oldPwd || !newPwd) return Common.toast("修改密码请填写原密码和新密码", "error");
        if (newPwd.length < 6) return Common.toast("新密码至少 6 位", "error");
        if (newPwd !== newPwd2) return Common.toast("两次输入的新密码不一致", "error");
      }

      const btn = document.getElementById("p-save");
      btn.disabled = true;
      btn.textContent = "保存中…";
      try {
        const u = await API.request("PUT", "/api/auth/profile", { username, real_name: realName });
        API.saveSession(API.getToken(), { ...API.getUser(), ...u });
        App.refreshUserInfo();

        if (wantsPwd) {
          await API.request("POST", "/api/auth/change-password", { old_password: oldPwd, new_password: newPwd });
          Common.toast("密码已更新，请重新登录", "success");
          setTimeout(() => API.goLogin(), 900);
          return;
        }
        Common.toast("已保存", "success");
        render(container, false);
      } catch (err) {
        Common.toast(err.message, "error");
        btn.disabled = false;
        btn.textContent = "保存";
      }
    });
  }

  /* 更换头像弹窗：个人中心「更换头像」与侧边栏头像共用 */
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
        const main = document.getElementById("p-avatar-view");
        if (main) main.innerHTML = Common.avatarHtml(u, 76);
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
