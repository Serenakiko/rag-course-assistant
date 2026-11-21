/* 双侧边栏导航回归（不依赖浏览器）
 *
 *   node tests/frontend_navigation.mjs
 *
 * 菜单是学生对系统工作流的第一层认知。这里钉住角色专属命名、分组顺序、
 * 账号入口不再重复占导航，以及收起成图标栏后仍有可读的名称。
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const appSrc = fs.readFileSync(
  path.join(here, "..", "backend", "static", "js", "app.js"),
  "utf8",
);

let failures = 0;
function check(name, cond) {
  if (cond) {
    console.log("  ok   " + name);
    return;
  }
  failures += 1;
  console.error("  FAIL " + name);
}

function element(extra = {}) {
  const attrs = {};
  return {
    innerHTML: "",
    textContent: "",
    title: "",
    style: {},
    hidden: false,
    classList: { add() {}, remove() {}, toggle() {} },
    setAttribute(name, value) { attrs[name] = String(value); },
    getAttribute(name) { return attrs[name] ?? null; },
    removeAttribute(name) { delete attrs[name]; },
    addEventListener() {},
    querySelectorAll: () => [],
    querySelector: () => null,
    ...extra,
  };
}

function loadApp(role) {
  const nav = element();
  const elements = {
    "sidebar-nav": nav,
    "user-avatar": element(),
    "user-name": element(),
    "user-role": element(),
  };
  const sandbox = {
    console,
    // app.js 的路由用 URLSearchParams 存筛选参数，沙箱里要显式给（vm 上下文没有全局 Web API）
    URLSearchParams,
    setTimeout: () => 0,
    clearTimeout() {},
    setInterval: () => 0,
    clearInterval() {},
    document: {
      body: { classList: { add() {}, toggle() {} } },
      addEventListener() {},
      getElementById: (id) => elements[id] || null,
      querySelectorAll: () => [],
      querySelector: () => null,
    },
    window: {
      location: { replace() {}, hash: "", search: "" },
      history: { replaceState() {} },
      addEventListener() {},
      matchMedia: () => ({ matches: false, addEventListener() {} }),
    },
    localStorage: {
      getItem: () => null,
      setItem() {},
    },
    API: {
      getUser: () => ({ role, username: "测试用户", account: "test" }),
      getToken: () => "token",
    },
    Common: {
      avatarContent: () => "测",
      escapeHtml: (value) => String(value),
    },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(appSrc + "\n;globalThis.__App = App;", sandbox);
  return { App: sandbox.__App, elements, nav };
}

function hasButton(html, key, label) {
  return (
    html.includes(`data-key="${key}"`) &&
    html.includes(`title="${label}"`) &&
    html.includes(`aria-label="${label}"`)
  );
}

console.log("双侧边栏导航：");

const student = loadApp("student");
student.App.renderNav();
student.App.refreshUserInfo();
const studentHtml = student.nav.innerHTML;

check("学生端首项是首页", studentHtml.indexOf('title="首页"') < studentHtml.indexOf('title="智能问答"'));
check("学生学习记录使用「通知」", hasButton(studentHtml, "notifications", "通知"));
check("学生学习记录使用「我的提问」", hasButton(studentHtml, "feedback", "我的提问"));
check("学生学习记录使用「历史会话」", hasButton(studentHtml, "history", "历史会话"));
check("学生学习记录有「分享管理」", hasButton(studentHtml, "shares", "分享管理"));
check("学生课程资料使用「我的课程」", hasButton(studentHtml, "courses", "我的课程"));
check("学生课程资料使用「课程资料」", hasButton(studentHtml, "knowledge", "课程资料"));
check("学生端不再保留无上传权限的「我的资料」", !studentHtml.includes('data-key="documents"'));
check("学生端不再重复放置个人中心导航", !studentHtml.includes('data-key="settings"'));
check("学生端不混入管理员入口", !studentHtml.includes("待解答问题") && !studentHtml.includes("用户管理"));
check("学生端分组标题是静态文本", studentHtml.includes("nav-group-title") && !studentHtml.includes("nav-group-toggle"));
check("学生端导航项明确声明按钮类型", studentHtml.includes('type="button"'));
check("学生端账号区显示角色", student.elements["user-role"].textContent === "学生");
check("学生端侧边栏不显示数量角标", !studentHtml.includes("data-nav-badge"));

const admin = loadApp("admin");
admin.App.renderNav();
admin.App.refreshUserInfo();
const adminHtml = admin.nav.innerHTML;

check("管理员首项是数据概览", adminHtml.indexOf('title="数据概览"') < adminHtml.indexOf('title="待解答问题"'));
check("管理员使用「待解答问题」", hasButton(adminHtml, "unanswered", "待解答问题"));
check("管理员有通知入口", hasButton(adminHtml, "notifications", "通知"));
check("管理员使用「会话记录」", hasButton(adminHtml, "history", "会话记录"));
check("管理员使用「问答测试」", hasButton(adminHtml, "chat", "问答测试"));
check("管理员有「分享管理」", hasButton(adminHtml, "shares", "分享管理"));
check("管理员保留「知识库管理」", hasButton(adminHtml, "content", "知识库管理"));
check("管理员使用「检索测试」", hasButton(adminHtml, "search-debug", "检索测试"));
check("管理员端不再重复放置个人中心导航", !adminHtml.includes('data-key="settings"'));
check("管理员端不混入学生学习入口", !adminHtml.includes("我的提问") && !adminHtml.includes("课程资料"));
check("管理员端分组顺序符合运营闭环", adminHtml.indexOf("答疑运营") < adminHtml.indexOf("内容管理"));
check("管理员端未命中问题不显示待处理数量", !adminHtml.includes("data-nav-badge"));
check("管理员端账号区显示角色", admin.elements["user-role"].textContent === "管理员");

if (failures) {
  console.error(`\n${failures} 项失败`);
  process.exit(1);
}
console.log("\n全部导航断言通过");
