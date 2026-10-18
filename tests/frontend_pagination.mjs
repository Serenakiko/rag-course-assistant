/* 前端分页条回归测试（不依赖浏览器）
 *
 *   node tests/frontend_pagination.mjs
 *
 * 只测 Common.renderPagination 生成的 HTML：它是所有列表页底部共用的那一块，
 * 「管理员端又冒出一行 · 第 1/2 页」「每页条数下拉又回来了」这类回退，
 * 靠人眼点五个页面才发现太亏。
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const commonSrc = fs.readFileSync(
  path.join(here, "..", "backend", "static", "js", "common.js"),
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

/** 在假 DOM 里跑一遍 renderPagination，返回生成的 HTML。 */
function render({ role, total = 23, current = 1, pageSize = 10, opts = {} }) {
  const el = {
    innerHTML: "",
    querySelectorAll: () => [],
    querySelector: () => null,
  };
  const sandbox = {
    document: {
      getElementById: () => el,
      querySelectorAll: () => [],
      createElement: () => ({ style: {}, classList: { add() {}, toggle() {} }, appendChild() {} }),
    },
    API: { getUser: () => ({ role }) },
    setTimeout: () => 0,
    clearTimeout: () => {},
    console,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  // common.js 顶层是 const，不会挂到 global 上，手动带出来
  vm.runInContext(commonSrc + "\n;globalThis.__Common = Common;", sandbox);
  sandbox.__Common.renderPagination("pager", total, current, pageSize, () => {}, opts);
  return el.innerHTML;
}

console.log("分页条：");

// 管理员：任何列表页都不显示「第 x/y 页」
const adminHtml = render({ role: "admin", opts: { sizeOptions: [10, 20] } });
check("管理员端不出现「第 1/3 页」", !adminHtml.includes("第 1/3 页"));
check("管理员端仍显示共 23 条", adminHtml.includes("共 23 条"));
check("管理员端仍有每页条数下拉", adminHtml.includes("10条/页"));

// 学生端默认显示（历史遗留行为）
const studentHtml = render({ role: "student" });
check("学生端默认显示「第 1/3 页」", studentHtml.includes("第 1/3 页"));

// 显式关掉（学生端反馈页 / 知识库页 / 文档页都是这样）
const studentHidden = render({ role: "student", opts: { hidePageInfo: true } });
check("hidePageInfo 生效时不显示页码信息", !studentHidden.includes("第 1/3 页"));
check("hidePageInfo 生效时仍显示总数", studentHidden.includes("共 23 条"));

// 只有一页时不该出现「第 1/1 页」
const singlePage = render({ role: "student", total: 5 });
check("只有一页时不显示「第 1/1 页」", !singlePage.includes("第 1/1 页"));

// 翻页按钮：首/末页的箭头状态
check("第一页的「‹」是禁用的", /data-page="0"[^>]*disabled/.test(studentHtml));
check("第一页的「›」可以点", /data-page="2"(?!\d)[^>]*>›/.test(studentHtml));
const lastPageHtml = render({ role: "student", current: 3 });
check("最后一页的「›」是禁用的", /data-page="4"[^>]*disabled/.test(lastPageHtml));

if (failures) {
  console.error(`\n${failures} 项失败`);
  process.exit(1);
}
console.log("\n全部分页断言通过");
