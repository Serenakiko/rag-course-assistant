/* 移动端抽屉兜底：app.js 已内置同款逻辑，这里只在它没绑定/未生效时才切换，避免双触发。
 * 从 app.html 的内联脚本拆出来，配合 CSP script-src 'self'。 */
(function () {
  var body = document.body;
  var toggle = document.getElementById("nav-toggle");
  var backdrop = document.getElementById("nav-backdrop");
  if (!toggle) return;
  var wasOpen = null;
  var syncAria = function (open) {
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", open ? "关闭菜单" : "打开菜单");
  };
  document.addEventListener(
    "click",
    function (e) {
      if (e.target === toggle || toggle.contains(e.target)) {
        wasOpen = body.classList.contains("nav-open");
      }
    },
    true
  );
  toggle.addEventListener("click", function () {
    if (wasOpen === null) return;
    var handled = body.classList.contains("nav-open") !== wasOpen;
    if (handled) {
      wasOpen = null;
      return;
    }
    var open = !wasOpen;
    wasOpen = null;
    body.classList.toggle("nav-open", open);
    syncAria(open);
  });
  if (backdrop)
    backdrop.addEventListener("click", function () {
      body.classList.remove("nav-open");
      syncAria(false);
    });
})();
