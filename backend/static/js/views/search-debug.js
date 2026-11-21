/* 检索测试：展示检索链路候选与门控决策，只读。 */
const SearchDebugView = (() => {
  const MAX_QUESTION = 500;
  const HINT = "font-size:12.5px;line-height:1.6;color:var(--text-3)";

  let loading = false;

  /* ------------------------------------------------------------ 渲染骨架 */

  function render(container) {
    container.innerHTML = `
      <div class="card">
        <form id="rd-form" autocomplete="off">
          <div class="page-toolbar" style="flex-wrap:wrap;gap:12px;align-items:flex-end">
            <div class="form-group" style="flex:1 1 320px;min-width:240px;margin:0">
              <label for="rd-question">检索问题</label>
              <input class="form-control" id="rd-question" name="question"
                     maxlength="${MAX_QUESTION}" placeholder="例如：进程和线程有什么区别？">
            </div>
            <button class="btn btn-primary" type="submit" id="rd-submit">开始检索</button>
          </div>
          <div class="form-group" style="margin-top:14px">
            <label>课程范围</label>
            <div id="rd-courses"><span style="${HINT}">课程加载中…</span></div>
            <p style="${HINT};margin-top:6px">不勾选表示全库检索；可多选。</p>
          </div>
          <p style="${HINT}">调试接口只返回检索链路本身：top-k 候选（含低于阈值的）、rerank 前后名次与阈值门控决策，不调用大模型。</p>
        </form>
      </div>
      <div id="rd-result">
        <div class="card"><div class="empty-state">
          <p>输入问题后点「开始检索」</p>
          <p style="${HINT}">将展示候选得分、rerank 得分、是否通过阈值门控和耗时</p>
        </div></div>
      </div>`;

    container.querySelector("#rd-form").addEventListener("submit", onSubmit);
    loadCourses();
  }

  /* ------------------------------------------------------------ 课程多选 */

  async function loadCourses() {
    const box = document.getElementById("rd-courses");
    if (!box) return;
    try {
      const data = await API.request("GET", "/api/knowledge/bases");
      const list = (Array.isArray(data) ? data : []).filter((b) => b && b.name);
      if (!list.length) {
        box.innerHTML = `<span style="${HINT}">暂无知识库（课程）可选，将按全库检索</span>`;
        return;
      }
      box.innerHTML = `<div style="display:flex;flex-wrap:wrap;gap:8px 20px">
        ${list
          .map(
            (b) => `<label style="display:inline-flex;align-items:center;gap:6px;font-weight:normal;cursor:pointer">
              <input type="checkbox" class="rd-course" value="${Common.escapeHtml(b.name)}">
              <span>${Common.escapeHtml(b.name)}</span>
            </label>`
          )
          .join("")}
      </div>`;
    } catch (err) {
      box.innerHTML = `<span style="font-size:12.5px;color:var(--danger)">课程加载失败：${Common.escapeHtml(err.message)}</span>`;
    }
  }

  /* ------------------------------------------------------------ 发起检索 */

  async function onSubmit(event) {
    event.preventDefault();
    if (loading) return;

    const input = document.getElementById("rd-question");
    const question = (input.value || "").trim();
    if (!question) {
      Common.toast("请输入检索问题");
      input.focus();
      return;
    }
    if (question.length > MAX_QUESTION) {
      Common.toast(`问题最多 ${MAX_QUESTION} 字`);
      return;
    }

    const courses = Array.from(
      document.querySelectorAll("#rd-courses input.rd-course:checked")
    ).map((el) => el.value);

    const params = new URLSearchParams();
    params.append("question", question);
    courses.forEach((name) => params.append("courses", name));

    const resultBox = document.getElementById("rd-result");
    const button = document.getElementById("rd-submit");
    loading = true;
    if (button) { button.disabled = true; button.textContent = "检索中…"; }
    resultBox.innerHTML = `<div class="card"><div class="fb-loading">
      <span class="loading-spinner"></span><p style="${HINT};margin-top:10px">正在向量化并检索…</p>
    </div></div>`;

    try {
      const data = await API.request(
        "GET",
        `/api/admin/retrieval/debug?${params.toString()}`
      );
      // 请求返回前用户可能已切走视图，节点被替换就不要再写
      if (resultBox.isConnected) resultBox.innerHTML = renderResult(data);
    } catch (err) {
      if (resultBox.isConnected) {
        resultBox.innerHTML = `<div class="card"><div class="empty-state">
          <p style="color:var(--danger)">检索调试失败：${Common.escapeHtml(err.message)}</p>
          <p style="${HINT}">若提示「检索服务暂时不可用」，请检查向量库（Chroma）与 embedding 模型（Ollama）是否就绪。</p>
        </div></div>`;
      }
    } finally {
      loading = false;
      if (button && button.isConnected) {
        button.disabled = false;
        button.textContent = "开始检索";
      }
    }
  }

  /* ------------------------------------------------------------ 结果渲染 */

  const REASON_TEXT = {
    above_threshold: "最高相似度达到阈值，可以作答",
    below_threshold: "最高相似度低于阈值，会走「未命中」→ 转人工/反馈",
    no_candidates: "没有检索到任何候选（课程范围为空或知识库无有效向量）",
  };

  function fmtScore(value) {
    return typeof value === "number" ? value.toFixed(4) : "—";
  }

  function fmtMs(value) {
    return typeof value === "number" ? `${value} ms` : "—";
  }

  function renderResult(data) {
    if (!data || !data.question) {
      return `<div class="card"><div class="empty-state"><p>返回数据为空</p></div></div>`;
    }
    return `${renderDecision(data)}${renderCandidates(data)}${renderReranked(data)}`;
  }

  function renderDecision(data) {
    const decision = data.decision || {};
    const timings = data.timings_ms || {};
    const answerable = decision.answerable === true;
    const reason = REASON_TEXT[decision.reason] || decision.reason || "未知";
    const badge = answerable
      ? `<span class="badge badge-ready">可回答</span>`
      : `<span class="badge badge-failed">不可回答</span>`;
    const courseText = (data.courses || []).length
      ? data.courses.map((c) => Common.escapeHtml(c)).join("、")
      : "全库";

    return `
      <div class="card" style="margin-top:16px">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
          <div>
            <div style="font-size:13px;color:var(--text-3);margin-bottom:4px">决策摘要</div>
            <div style="font-weight:600;line-height:1.6">
              ${Common.escapeHtml(data.question)}
            </div>
            <div style="${HINT};margin-top:4px">课程范围：${courseText}</div>
          </div>
          <div>${badge}</div>
        </div>
        <div class="fb-stat-grid rd-stat-grid" style="margin-top:14px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
          <div class="stat-card">
            <div class="stat-hint">阈值</div>
            <div class="stat-num">${fmtScore(data.threshold)}</div>
            <div class="stat-hint">UNMATCHED_SIMILARITY</div>
          </div>
          <div class="stat-card">
            <div class="stat-hint">最高相似度</div>
            <div class="stat-num">${fmtScore(decision.top_score)}</div>
            <div class="stat-hint">top_score</div>
          </div>
          <div class="stat-card">
            <div class="stat-hint">向量化</div>
            <div class="stat-num">${fmtMs(timings.embed)}</div>
            <div class="stat-hint">embed</div>
          </div>
          <div class="stat-card">
            <div class="stat-hint">向量查询</div>
            <div class="stat-num">${fmtMs(timings.query)}</div>
            <div class="stat-hint">Chroma query</div>
          </div>
          <div class="stat-card">
            <div class="stat-hint">重排</div>
            <div class="stat-num">${fmtMs(timings.rerank)}</div>
            <div class="stat-hint">rerank</div>
          </div>
          <div class="stat-card">
            <div class="stat-hint">检索合计</div>
            <div class="stat-num">${fmtMs(data.retrieval_ms)}</div>
            <div class="stat-hint">query + rerank</div>
          </div>
        </div>
        <p style="${HINT};margin-top:12px">
          判定原因：<span style="color:${answerable ? "var(--success)" : "var(--warning, #d97706)"}">${Common.escapeHtml(reason)}</span>
          <span style="margin-left:8px">（reason: <code>${Common.escapeHtml(decision.reason || "")}</code>）</span>
        </p>
        <p style="${HINT};margin-top:6px">
          注：<code>passed_threshold</code> 表示该候选的相似度是否达到阈值（当前门控按语义最高分判断），
          表里列出的本来都是进入最终 top-k 的候选，
          <code>rerank_score</code> 是向量分 0.95 + 词法分 0.05 的混合分，门控仍看 <code>score</code>。
        </p>
      </div>`;
  }

  function renderCandidates(data) {
    const rows = data.candidates || [];
    if (!rows.length) {
      return `
        <div class="card" style="margin-top:16px">
          <div class="empty-state">
            <p>没有候选</p>
            <p style="${HINT}">可能是勾选的课程范围为空，或该范围内还没有可用向量。</p>
          </div>
        </div>`;
    }
    const hasRerank = rows.some((row) => row.rerank_score != null);
    return `
      <div class="card" style="margin-top:16px">
        <div style="display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap">
          <div style="font-weight:600">候选表（top-k，共 ${rows.length} 条）</div>
          <div style="${HINT}">${hasRerank ? "已启用 rerank" : "rerank 未启用或未产生分数"}</div>
        </div>
        <div class="table-wrap" style="margin-top:10px">
          <table class="table">
            <thead>
              <tr>
                <th style="width:52px">#</th>
                <th style="width:86px">score</th>
                <th style="width:96px">rerank</th>
                <th style="width:86px">门控</th>
                <th style="width:110px">课程</th>
                <th>来源</th>
                <th style="width:46%">预览（前 120 字）</th>
              </tr>
            </thead>
            <tbody>
              ${rows
                .map((row) => {
                  const source = [row.title, row.filename].filter(Boolean).join(" · ");
                  const chunk = row.chunk_id
                    ? `<div style="${HINT}"><code>${Common.escapeHtml(row.chunk_id)}</code></div>`
                    : "";
                  return `<tr>
                    <td>${row.rank}</td>
                    <td>${fmtScore(row.score)}</td>
                    <td>${fmtScore(row.rerank_score)}</td>
                    <td>${
                      row.passed_threshold
                        ? `<span class="badge badge-ready">通过</span>`
                        : `<span class="badge badge-failed">未过阈值</span>`
                    }</td>
                    <td>${Common.escapeHtml(row.course || "未分类")}</td>
                    <td>
                      <div>${Common.escapeHtml(source || "—")}</div>
                      <div style="${HINT}">doc_id: ${row.doc_id == null ? "—" : row.doc_id}</div>
                      ${chunk}
                    </td>
                    <td style="white-space:pre-wrap;word-break:break-word">${Common.escapeHtml(row.preview || "")}</td>
                  </tr>`;
                })
                .join("")}
            </tbody>
          </table>
        </div>
      </div>`;
  }

  function renderReranked(data) {
    const rows = data.reranked || [];
    if (!rows.length) {
      return `
        <div class="card" style="margin-top:16px">
          <div style="font-weight:600">重排变化</div>
          <p style="${HINT};margin-top:6px">名次没有变化（或 rerank 未启用），候选顺序与向量相似度一致。</p>
        </div>`;
    }
    return `
      <div class="card" style="margin-top:16px">
        <div style="font-weight:600">重排变化（${rows.length} 条名次变动）</div>
        <div class="table-wrap" style="margin-top:10px">
          <table class="table">
            <thead>
              <tr>
                <th>chunk_id</th>
                <th style="width:110px">重排前</th>
                <th style="width:110px">重排后</th>
                <th style="width:110px">变化</th>
              </tr>
            </thead>
            <tbody>
              ${rows
                .map((row) => {
                  const delta = (row.rank_before || 0) - (row.rank_after || 0);
                  const deltaText =
                    delta > 0 ? `↑ 升 ${delta}` : delta < 0 ? `↓ 降 ${Math.abs(delta)}` : "—";
                  return `<tr>
                    <td><code>${Common.escapeHtml(row.chunk_id || "—")}</code></td>
                    <td>${row.rank_before}</td>
                    <td>${row.rank_after}</td>
                    <td>${deltaText}</td>
                  </tr>`;
                })
                .join("")}
            </tbody>
          </table>
        </div>
      </div>`;
  }

  return { render };
})();

/* ============ 主会话接线清单（本文件未改 app.html / app.js） ============
 * 1) app.html：在 admin-feedback.js 之后、app.js 之前加脚本（v 从 1 起，按需递增）：
 *      <script src="/static/js/views/search-debug.js?v=1"></script>
 * 2) app.js 的 NAV「系统管理」（roles: ["admin"]）里加一项，视图 key 建议 "search-debug"：
 *      { key: "search-debug", icon: ICON.unanswered, label: "检索调试" }
 *    ICON 里没有放大镜，可复用 ICON.unanswered / ICON.knowledge，或新增：
 *      search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
 *        stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/>
 *        <line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>'
 * 3) app.js 的 TITLES.admin 里加标题：
 *      "search-debug": "检索调试",
 * 4) app.js 的 navigate(key) switch 里、default 之前加分发（第 348 行前）：
 *      case "search-debug":
 *        SearchDebugView.render(container);
 *        break;
 * 5) 本视图不依赖 hash 参数；key 会被 syncHash 自动写成 "#/search-debug"，
 *    viewFromHash 的 [a-z][a-z-]* 正则已能匹配，无需额外改路由正则。
 * 视图 id / 导航 key 建议统一用 "search-debug"；容器沿用 #main-content，无需新增 DOM 节点。
 */
