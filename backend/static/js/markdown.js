/* ============ 轻量 Markdown 渲染（离线可用，无第三方依赖） ============ */
const Markdown = (() => {
  // 只放行安全协议，挡掉 javascript: / data: 这类可执行 URL
  const SAFE_URL = /^(https?:|mailto:|\/|#)|^[^:]*$/i;

  function inline(text) {
    // 先整体转义，之后的正则只在纯文本上工作，输出标签全部由本函数拼接
    let t = Common.escapeHtml(text);
    // 行内代码
    t = t.replace(/`([^`]+)`/g, (_, code) => `<code>${code}</code>`);
    // 图片
    t = t.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+[^)\s]+)?\)/g,
      (_, alt, src) => (SAFE_URL.test(src)
        ? `<img src="${src}" alt="${alt}" style="max-width:100%;border-radius:8px">`
        : `![${alt}](${src})`));
    // 链接
    t = t.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
      (_, label, href) => (SAFE_URL.test(href)
        ? `<a href="${href}" target="_blank" rel="noopener">${label}</a>`
        : `[${label}](${href})`));
    // 粗体
    t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    // 斜体
    t = t.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    // 调用方用 \n 拼接多行，这里统一换成换行标签
    return t.replace(/\n/g, "<br>");
  }

  function render(md) {
    if (!md) return "";
    const lines = String(md).replace(/\r\n/g, "\n").split("\n");
    const html = [];
    let i = 0;
    let listType = null;

    const closeList = () => {
      if (listType) { html.push(`</${listType}>`); listType = null; }
    };

    while (i < lines.length) {
      let line = lines[i];

      // 代码块
      if (/^```/.test(line)) {
        closeList();
        const lang = line.replace(/^```/, "").trim();
        const buf = [];
        i++;
        while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++; // 跳过结束 ```
        const code = buf.join("\n").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        html.push(`<pre><code${lang ? ` class="lang-${Common.escapeHtml(lang)}"` : ""}>${code}</code></pre>`);
        continue;
      }

      // 标题
      const h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) {
        closeList();
        const level = h[1].length;
        html.push(`<h${level}>${inline(h[2])}</h${level}>`);
        i++;
        continue;
      }

      // 引用
      if (/^>\s?/.test(line)) {
        closeList();
        const buf = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) { buf.push(lines[i].replace(/^>\s?/, "")); i++; }
        html.push(`<blockquote>${inline(buf.join("\n"))}</blockquote>`);
        continue;
      }

      // 无序列表
      if (/^\s*[-*+]\s+/.test(line)) {
        if (listType !== "ul") { closeList(); html.push("<ul>"); listType = "ul"; }
        html.push(`<li>${inline(line.replace(/^\s*[-*+]\s+/, ""))}</li>`);
        i++;
        continue;
      }

      // 有序列表
      if (/^\s*\d+[.、)]\s+/.test(line)) {
        if (listType !== "ol") { closeList(); html.push("<ol>"); listType = "ol"; }
        html.push(`<li>${inline(line.replace(/^\s*\d+[.、)]\s+/, ""))}</li>`);
        i++;
        continue;
      }

      // 表格
      if (line.includes("|") && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1]) && lines[i + 1].includes("-")) {
        closeList();
        const parseRow = (r) => r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const headers = parseRow(line);
        i += 2; // 跳过分隔行
        let t = "<table><thead><tr>" + headers.map((h) => `<th>${inline(h)}</th>`).join("") + "</tr></thead><tbody>";
        while (i < lines.length && lines[i].includes("|")) {
          const cells = parseRow(lines[i]);
          t += "<tr>" + cells.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>";
          i++;
        }
        t += "</tbody></table>";
        html.push(t);
        continue;
      }

      // 空行
      if (!line.trim()) {
        closeList();
        i++;
        continue;
      }

      // 分隔线
      if (/^\s*([-*_]\s*){3,}$/.test(line)) {
        closeList();
        html.push("<hr>");
        i++;
        continue;
      }

      // 普通段落（合并连续行）
      closeList();
      const buf = [line];
      i++;
      while (i < lines.length && lines[i].trim() && !/^#{1,4}\s/.test(lines[i]) && !/^```/.test(lines[i]) && !/^\s*[-*+]\s+/.test(lines[i]) && !/^\s*\d+[.、)]\s+/.test(lines[i]) && !/^>\s?/.test(lines[i])) {
        buf.push(lines[i]);
        i++;
      }
      html.push(`<p>${inline(buf.join("\n"))}</p>`);
    }
    closeList();
    return html.join("\n");
  }

  return { render };
})();
