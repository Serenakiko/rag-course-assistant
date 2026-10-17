/* ============ 视图：知识库与文档（管理台，一个页面两个 tab） ============
 * 文档是知识库的子资源：补资料时不用在「知识库管理」和「文档管理」两个菜单之间来回跳。
 * 两个 tab 复用原来的表格视图（AdminView 的知识库表格 / DocumentsView），
 * 这里只负责 tab 切换与定时器回收。
 */
const ContentView = (() => {
  let tab = "kb";   // kb = 知识库列表；docs = 全部文档

  function render(container, opts) {
    tab = (opts && opts.tab) || "kb";
    container.innerHTML = `
      <div class="fb-tabs" id="content-tabs">
        <button type="button" class="fb-tab" data-tab="kb">知识库</button>
        <button type="button" class="fb-tab" data-tab="docs">全部文档</button>
      </div>
      <div id="content-panel"></div>`;

    container.querySelectorAll("#content-tabs .fb-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (btn.dataset.tab === tab) return;
        tab = btn.dataset.tab;
        renderPanel(container);
      });
    });
    renderPanel(container);
  }

  function renderPanel(container) {
    // 切 tab 前先停掉上一个 tab 的轮询/静默刷新，避免在隐藏的 DOM 上继续跑
    AdminView.destroy();
    DocumentsView.destroy();

    container.querySelectorAll("#content-tabs .fb-tab").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tab);
    });

    const panel = document.getElementById("content-panel");
    if (!panel) return;
    if (tab === "docs") DocumentsView.render(panel);
    else AdminView.render(panel, "kb_table");
  }

  function destroy() {
    AdminView.destroy();
    DocumentsView.destroy();
  }

  return { render, destroy };
})();
