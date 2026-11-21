/* 尽早应用主题，避免闪白。
 * 原来是 <head> 里的内联脚本；抽成文件是为了让 CSP 能禁掉不安全内联脚本
 * （script-src 'self' 不需要 'unsafe-inline'）。必须同步加载且放在样式表之前。 */
try {
  var t = localStorage.getItem("app-theme") || "system";
  var d = t === "dark" || (t === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", d ? "dark" : "light");
} catch (e) {}
