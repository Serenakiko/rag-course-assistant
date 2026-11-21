/* 注册 Service Worker：/api/ 请求由 sw.js 直接放行，不进缓存。
 * 从页面内联脚本拆出来，配合 CSP script-src 'self'。 */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", function () {
    navigator.serviceWorker
      .register("/static/sw.js", { scope: "/static/" })
      .catch(function () {});
  });
}

/* 离线页的「重新加载」：offline.html 不再用内联 onclick（CSP 会拦）。 */
document.addEventListener("click", function (event) {
  var target = event.target;
  if (target && target.id === "offline-reload") {
    window.location.reload();
  }
});
