/* 前端 Markdown 渲染回归（不依赖浏览器）
 *
 *   node tests/frontend_markdown.mjs
 *
 * 回答正文会用它渲染，模型输出不可信：漏一次转义就是 XSS。
 * 这里钉住「HTML 一律转义、危险协议不生成链接、格式标记仍能生效」三条。
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(
  path.join(here, "..", "backend", "static", "js", "markdown.js"),
  "utf8",
);

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function load() {
  const sandbox = { Common: { escapeHtml }, console };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  // markdown.js 顶层是 const，不会挂到 global 上，手动带出来
  vm.runInContext(src + "\n;globalThis.__Markdown = Markdown;", sandbox);
  return sandbox.__Markdown;
}

const Markdown = load();

let failures = 0;
function check(name, cond) {
  if (cond) {
    console.log("  ok   " + name);
    return;
  }
  failures += 1;
  console.error("  FAIL " + name);
}

console.log("Markdown 渲染：");

// 1. 原始 HTML 必须转义，不能原样进入 DOM
const raw = Markdown.render("<script>alert(1)</script>");
check("脚本标签被转义", raw.includes("&lt;script&gt;") && !raw.includes("<script>"));
check("img onerror 被转义", !Markdown.render('<img src=x onerror="alert(1)">').includes("<img src=x"));

// 2. 危险协议不生成链接
const jsLink = Markdown.render("[点我](javascript:alert(1))");
check("javascript: 不生成 <a>", !jsLink.includes('<a href="javascript'));
check("javascript: 原样保留为文本", jsLink.includes("javascript:alert(1)"));

const okLink = Markdown.render("[文档](https://example.com/a)");
check("https 链接正常生成", okLink.includes('<a href="https://example.com/a" target="_blank" rel="noopener">'));

const relLink = Markdown.render("[相对](/static/app.html)");
check("站内相对链接正常生成", relLink.includes('href="/static/app.html"'));

// 3. 图片
const img = Markdown.render("![图](/static/img/logo.svg)");
check("图片正常生成且限宽", img.includes('<img src="/static/img/logo.svg"') && img.includes("max-width:100%"));

// 4. 行内格式
check("粗体", Markdown.render("**重点**").includes("<strong>重点</strong>"));
check("行内代码", Markdown.render("`TCP`").includes("<code>TCP</code>"));
check("行内代码里的尖括号被转义", Markdown.render("`<b>`").includes("&lt;b&gt;"));

// 5. 代码块：内容整体转义，语言名也转义
const fence = Markdown.render('```html\n<b>x</b>\n```');
check("代码块内容被转义", fence.includes("&lt;b&gt;x&lt;/b&gt;"));
check("代码块带上语言 class", fence.includes('class="lang-html"'));

// 6. 结构：标题 / 列表 / 引用 / 表格
check("一级标题", Markdown.render("# 标题").includes("<h1>标题</h1>"));
check("无序列表", Markdown.render("- 甲\n- 乙").includes("<ul>"));
check("有序列表", Markdown.render("1. 甲\n2. 乙").includes("<ol>"));
check("引用", Markdown.render("> 引文").includes("<blockquote>引文</blockquote>"));
const table = Markdown.render("| 项 | 值 |\n| --- | --- |\n| a | 1 |");
check("表格生成表头", table.includes("<th>项</th>"));
check("表格生成单元格", table.includes("<td>a</td>"));
check("表格单元格里的 HTML 被转义", Markdown.render("| a |\n| --- |\n| <b>x</b> |").includes("&lt;b&gt;"));

// 7. 段落合并 + 空输入
check("连续行合并成一个段落", Markdown.render("第一行\n第二行").includes("<p>第一行<br>第二行</p>"));
check("空输入返回空串", Markdown.render("") === "" && Markdown.render(null) === "");

if (failures) {
  console.error(`\n${failures} 项失败`);
  process.exit(1);
}
console.log("\n全部 Markdown 断言通过");
