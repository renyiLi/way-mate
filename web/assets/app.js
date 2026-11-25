/* 随行交付台 · 外壳渲染与路由
   数据来自 window.__DBW_DATA__（web/data/site.js，由 web/build-data.py 生成）。
   页面不发起网络请求，也不自己判定"是否可交付"——只读 all_green。
   路由用 hash，file:// 下同样可用： #/  #/tasks  #/tasks/<id>  #/runs  #/runs/<id> */

(function () {
  "use strict";

  const DATA = window.__DBW_DATA__ || null;
  const GATES = ["tests", "lint", "api", "diff"];
  const HOME_ROWS = 8;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  const OUTCOME = {
    accepted: { label: "交付成立", dot: "dot-pass" },
    rejected: { label: "驳回", dot: "dot-fail" },
    escalated: { label: "升级人工", dot: "dot-hold" },
    budget_exhausted: { label: "预算耗尽", dot: "dot-hold" },
    error: { label: "运行错误", dot: "dot-fail" }
  };

  const $ = (selector, root) => (root || document).querySelector(selector);
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function esc(value) {
    return String(value === null || value === undefined ? "" : value).replace(
      /[&<>"']/g,
      (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])
    );
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
    return m ? m[2] + "-" + m[3] + " " + m[4] + ":" + m[5] : iso;
  }

  function fmtClock(iso) {
    if (!iso) return "—";
    const m = /T(\d{2}):(\d{2}):(\d{2})/.exec(iso);
    return m ? m[1] + ":" + m[2] + ":" + m[3] : iso;
  }

  function fmtNum(value) {
    if (typeof value !== "number" || !isFinite(value)) return "—";
    return value.toLocaleString("en-US");
  }

  function fmtDuration(ms) {
    if (typeof ms !== "number" || !isFinite(ms)) return "—";
    if (ms < 1000) return ms + " ms";
    if (ms < 60000) return (ms / 1000).toFixed(1) + " s";
    return Math.round(ms / 60000) + " min";
  }

  function stripRepo(url) {
    if (!url) return "—";
    return String(url).replace(/^https?:\/\/(www\.)?github\.com\//, "");
  }

  function modelLabel(run) {
    if (run.provider && run.model) return run.provider + " / " + run.model;
    return run.model || run.provider || "—";
  }

  function outcomeMeta(value) {
    return OUTCOME[value] || { label: "未记录", dot: "" };
  }

  /* 闸门值：只格式化 metrics，不重算判定 */
  function gateValue(name, metrics) {
    const m = metrics || {};
    if (name === "tests") {
      const c = m.counts || {};
      if (typeof c.passed === "number" && typeof c.failed === "number") return c.passed + " passed · " + c.failed + " failed";
      if (typeof c.passed === "number") return c.passed + " passed";
      if (typeof c.failed === "number") return c.failed + " failed";
      if (typeof m.exit_code === "number") return "exit " + m.exit_code;
      return "—";
    }
    if (name === "lint") {
      if (typeof m.new === "number") return "new " + m.new + " / max " + (m.max_new === undefined ? "—" : m.max_new);
      return typeof m.count === "number" ? m.count + " 条问题" : "—";
    }
    if (name === "api") {
      const frozen = Array.isArray(m.frozen) ? m.frozen.length : 0;
      const missing = Array.isArray(m.missing) ? m.missing.length : 0;
      if (!frozen) return "未冻结";
      return frozen - missing + " / " + frozen + " 在";
    }
    if (name === "diff") {
      if (typeof m.files === "number") return m.files + " 文件 · " + (m.lines || 0) + " 行";
      return "—";
    }
    return "—";
  }

  /* ---------------- 共用片段 ---------------- */

  function signatureCell(run) {
    if (!run.gate) return '<span class="muted">—</span>';
    const marks = run.gate.results
      .map((item) => '<i class="' + (item.ok ? "ok" : "no") + '" title="' + esc(item.name + (item.ok ? " 通过" : " 未通过")) + '"></i>')
      .join("");
    return '<span class="sig">' + marks + "</span>";
  }

  function outcomeCell(run) {
    const meta = OUTCOME[run.outcome];
    if (!meta) return '<span class="muted">未记录</span>';
    return '<i class="dot ' + meta.dot + '"></i>' + meta.label;
  }

  function runRow(run, index) {
    const hidden = typeof index === "number" && index >= HOME_ROWS ? ' class="run-row is-hidden"' : ' class="run-row"';
    return (
      "<tr" + hidden + ' data-run-id="' + esc(run.run_id) + '">' +
      '<td class="nowrap"><a class="row-link" href="#/runs/' + encodeURIComponent(run.run_id) + '">' + esc(fmtTime(run.started_at)) + "</a></td>" +
      '<td class="mono"><span class="cell-clip" title="' + esc(run.task_id || "") + '">' + esc(run.task_id || "—") + "</span></td>" +
      '<td class="mono"><span class="cell-clip clamp-model" title="' + esc(modelLabel(run)) + '">' + esc(modelLabel(run)) + "</span></td>" +
      '<td class="mono">' + esc(run.protocol || run.backend || "—") + "</td>" +
      "<td>" + signatureCell(run) + "</td>" +
      "<td>" + outcomeCell(run) + "</td>" +
      '<td class="num">' + fmtNum(run.tokens) + "</td>" +
      '<td class="num">' + esc(fmtDuration(run.duration_ms)) + "</td>" +
      "</tr>"
    );
  }

  function runsTable(runs, options) {
    const opts = options || {};
    if (!runs.length) return '<p class="empty">' + (opts.empty || "没有运行记录。") + "</p>";
    return (
      '<div class="table-scroll"><table class="runs">' +
      '<caption class="sr-only">' + esc(opts.caption || "交付运行记录") + "</caption>" +
      "<thead><tr>" +
      '<th scope="col">时间</th><th scope="col">任务</th><th scope="col">模型</th><th scope="col">协议 / 后端</th>' +
      '<th scope="col">闸门</th><th scope="col">结果</th><th scope="col" class="num">token</th><th scope="col" class="num">耗时</th>' +
      "</tr></thead><tbody>" +
      runs.map((run, index) => runRow(run, opts.limit ? index : undefined)).join("") +
      "</tbody></table></div>" +
      '<p class="scroll-hint">← 横向滑动看完整列 →</p>'
    );
  }

  function taskCard(task, withLink) {
    const runs = task.runs || { total: 0, accepted: 0 };
    const title = withLink
      ? '<a href="#/tasks/' + encodeURIComponent(task.id) + '">' + esc(task.title) + "</a>"
      : esc(task.title);
    const contractBlock = task.contract_yaml
      ? '<details class="contract"><summary>契约原文 · ' + esc(task.contract_source || task.source) + "</summary><pre><code>" + esc(task.contract_yaml) + "</code></pre></details>"
      : '<p class="task-warn">没有读到 contract.yaml</p>';
    return (
      '<article class="task">' +
      '<div class="task-head">' +
      '<span class="task-id">' + esc(task.id) + "</span>" +
      '<span class="chip chip-tier">' + esc(task.tier || "未分级") + "</span>" +
      '<span class="task-runs">' + runs.total + " 次运行 · " + runs.accepted + " 成立</span>" +
      "</div>" +
      "<h3>" + title + "</h3>" +
      '<p class="task-stmt">' + esc(task.statement) + "</p>" +
      '<dl class="task-meta">' + taskMeta(task) + "</dl>" +
      contractBlock +
      "</article>"
    );
  }

  function taskMeta(task) {
    const contract = task.contract || {};
    const dod = contract.doD || {};
    const diff = dod.diff || {};
    const rows = [["仓库", esc(stripRepo(task.repo))]];

    if (task.target_test) rows.push(["验收", '<span class="mono">' + esc(task.target_test) + "</span>", "wide"]);
    const budget = [];
    if (typeof diff.max_files === "number") budget.push(diff.max_files + " 文件");
    if (typeof diff.max_lines === "number") budget.push(diff.max_lines + " 行");
    if (Array.isArray(diff.forbid) && diff.forbid.length) budget.push("禁 " + diff.forbid.join("、"));
    if (budget.length) rows.push(["预算", '<span class="mono">' + esc(budget.join(" / ")) + "</span>"]);

    const verified = task.verified || {};
    if (verified.red_without_fix && verified.green_with_fix) {
      rows.push(["自检", '<span class="mono">' + esc(verified.red_without_fix + " → " + verified.green_with_fix) + "</span>", "wide"]);
    }
    if (task.pull_request) rows.push(["上游", "PR #" + esc(task.pull_request)]);

    return rows
      .map(([key, value, cls]) => '<div class="' + (cls || "") + '"><dt>' + key + "</dt><dd>" + value + "</dd></div>")
      .join("");
  }

  function crumbs(items) {
    return (
      '<nav class="crumbs" aria-label="面包屑">' +
      items
        .map((item, index) =>
          item.href
            ? '<a href="' + item.href + '">' + esc(item.label) + "</a>" + (index < items.length - 1 ? "<i>/</i>" : "")
            : "<span>" + esc(item.label) + "</span>"
        )
        .join("") +
      "</nav>"
    );
  }

  function notFound(title, back) {
    return (
      '<p class="empty">找不到' + esc(title) + "。" +
      (back ? ' <a href="' + back + '">回到列表</a>' : "") +
      "</p>"
    );
  }

  /* ---------------- 首页 ---------------- */

  const board = $("#board");
  const rows = Array.from(document.querySelectorAll(".check"));
  const failCount = $("#fail-count");
  const boardSub = $("#board-sub");
  const seal = $("#seal");
  const boardTask = $("#board-task");
  const boardMeta = $("#board-meta");
  const replayBtn = $("#replay");

  function pickBoardRun() {
    if (!DATA || !Array.isArray(DATA.runs) || !DATA.runs.length) return null;
    const latest = DATA.runs[0];
    if (latest.gate) return latest;
    return DATA.runs.find((run) => run.gate) || latest;
  }

  const boardRun = pickBoardRun();
  const resultByName = {};
  if (boardRun && boardRun.gate) {
    boardRun.gate.results.forEach((item) => {
      resultByName[item.name] = item;
    });
  }

  rows.forEach((row, index) => {
    row.dataset.gate = GATES[index];
    row.querySelector(".check-name").textContent = GATES[index];
  });

  function fillBoard() {
    if (!boardRun) return;
    boardTask.textContent = boardRun.task_id || boardRun.run_id;
    boardMeta.textContent = [fmtTime(boardRun.started_at), modelLabel(boardRun)].filter(Boolean).join(" · ");
    if (!boardRun.gate) {
      rows.forEach((row) => {
        row.dataset.state = "idle";
        row.querySelector(".check-value").textContent = "—";
      });
      failCount.textContent = "—";
      boardSub.textContent = "这次运行没有闸门报告";
      return;
    }
    rows.forEach((row) => {
      const result = resultByName[row.dataset.gate];
      row.dataset.state = result ? (result.ok ? "pass" : "fail") : "idle";
      row.querySelector(".check-value").textContent = result ? gateValue(result.name, result.metrics) : "—";
    });
    const fails = boardRun.gate.results.filter((item) => !item.ok).length;
    failCount.textContent = String(fails);
    boardSub.textContent = boardRun.gate.all_green ? "4 项全绿 · 交付成立" : fails + " 项未通过 · 交付不成立";
    seal.textContent = boardRun.gate.all_green ? "可交付" : "不予交付";
    seal.classList.toggle("is-wide", !boardRun.gate.all_green);
    seal.classList.add("is-on");
    board.classList.add("has-verdict");
  }

  let replaying = false;

  async function replay() {
    if (!boardRun || !boardRun.gate || replaying) return;
    const paced = !reduceMotion.matches;
    replaying = true;

    board.classList.remove("has-verdict");
    seal.classList.remove("is-on");
    failCount.textContent = "—";
    boardSub.textContent = "回放记录的判定…";
    rows.forEach((row) => {
      row.dataset.state = "idle";
      row.querySelector(".check-value").textContent = "…";
    });

    for (const row of rows) {
      const result = resultByName[row.dataset.gate];
      if (!result) {
        row.dataset.state = "idle";
        row.querySelector(".check-value").textContent = "—";
        continue;
      }
      row.dataset.state = "running";
      row.querySelector(".check-value").textContent = "判定中";
      await sleep(paced ? 380 : 0);
      row.dataset.state = result.ok ? "pass" : "fail";
      row.querySelector(".check-value").textContent = gateValue(result.name, result.metrics);
      await sleep(paced ? 110 : 0);
    }

    const fails = boardRun.gate.results.filter((item) => !item.ok).length;
    failCount.textContent = String(fails);
    boardSub.textContent = boardRun.gate.all_green ? "4 项全绿 · 交付成立" : fails + " 项未通过 · 交付不成立";
    seal.textContent = boardRun.gate.all_green ? "可交付" : "不予交付";
    seal.classList.toggle("is-wide", !boardRun.gate.all_green);
    seal.classList.add("is-on");
    board.classList.add("has-verdict");
    replaying = false;
  }

  function renderRecentFails() {
    const wrap = $("#recent-fails");
    if (!wrap) return;
    const fails = DATA.runs
      .filter((run) => run.outcome === "rejected" || (run.gate && !run.gate.all_green))
      .slice(0, 5);
    if (!fails.length) {
      wrap.innerHTML = '<p class="runs-note">最近没有失败记录。</p>';
      return;
    }
    wrap.innerHTML =
      '<h3 class="sub-title">最近失败 <span class="count">' + fails.length + "</span></h3>" +
      '<ol class="fail-list">' +
      fails
        .map((run) => {
          const bad = run.gate ? run.gate.results.filter((item) => !item.ok).map((item) => item.name) : [];
          return (
            '<li><a href="#/runs/' + encodeURIComponent(run.run_id) + '">' +
            '<span class="fail-time">' + esc(fmtTime(run.started_at)) + "</span>" +
            '<span class="fail-task">' + esc(run.task_id || "—") + "</span>" +
            '<span class="fail-model">' + esc(modelLabel(run)) + "</span>" +
            (bad.length
              ? '<span class="fail-gates">' + bad.map((name) => '<i class="fail-gate">' + esc(name) + "</i>").join("") + "</span>"
              : '<span class="fail-gates"></span>') +
            "</a></li>"
          );
        })
        .join("") +
      "</ol>";
  }

  function renderHome() {
    if (!DATA) return;

    const facts = $("#facts");
    if (facts) {
      const s = DATA.summary;
      facts.innerHTML = [
        ["任务", s.tasks],
        ["运行", s.runs],
        ["交付成立", s.outcomes.accepted],
        ["闸门判定", s.runs_with_gate]
      ]
        .map(([label, value]) => "<div><dt>" + label + "</dt><dd>" + value + "</dd></div>")
        .join("");
    }

    const tasks = $("#tasks-list");
    if (tasks) {
      tasks.innerHTML = DATA.tasks.length
        ? DATA.tasks.map((task) => taskCard(task, true)).join("")
        : '<p class="empty">tasks/ 下还没有任务。</p>';
    }

    (DATA.gate_stats || []).forEach((item) => {
      const node = document.querySelector('[data-gate-stat="' + item.name + '"]');
      if (node) node.textContent = item.evaluated + " 次判定 · 失败 " + item.failed;
    });

    const body = $("#runs-body");
    if (body) {
      if (!DATA.runs.length) {
        body.innerHTML =
          '<tr><td colspan="8" class="table-empty">还没有运行记录。先跑一次 <span class="mono">python scripts/run_once.py …</span>，再执行 <span class="mono">python web/build-data.py</span>。</td></tr>';
      } else {
        body.innerHTML = DATA.runs
          .slice(0, HOME_ROWS)
          .map((run) => runRow(run))
          .join("");
      }
    }

    renderRecentFails();

    const note = $("#runs-note");
    if (note) {
      note.textContent = DATA.runs.length
        ? "显示最近 " + Math.min(HOME_ROWS, DATA.runs.length) + " 次，共 " + DATA.runs.length + " 次 · " + DATA.summary.runs_with_gate + " 次带闸门判定"
        : "等待第一次运行";
    }

    const meta = $("#foot-meta");
    if (meta) {
      meta.textContent = "runs/audit.jsonl · " + DATA.generated_from.audit_events + " 事件 · 最近 " + fmtTime(DATA.generated_from.latest_run_at);
    }
  }

  function initBoard() {
    if (!boardRun) {
      if (boardTask) boardTask.textContent = "还没有运行记录";
      if (boardSub) boardSub.textContent = "先跑一次 scripts/run_once.py";
      if (replayBtn) replayBtn.hidden = true;
      return;
    }
    fillBoard();
    if (replayBtn) replayBtn.addEventListener("click", replay);
  }

  /* ---------------- 视图：任务 ---------------- */

  function viewTasks() {
    const tasks = DATA.tasks;
    return (
      '<section class="band">' +
      '<div class="wrap band-grid">' +
      '<p class="clause"><span>tasks</span>任务</p>' +
      '<div class="band-body">' +
      "<h2>" + tasks.length + " 个任务</h2>" +
      '<p class="lead">每个任务都对应一个真实仓库、一个上游 PR 和一份契约。点进去看契约细节，以及这个任务上的全部运行。</p>' +
      (tasks.length
        ? '<div class="tasks">' + tasks.map((task) => taskCard(task, true)).join("") + "</div>"
        : '<p class="empty">tasks/ 下还没有任务。先用 <span class="mono">scripts/prepare_task.py</span> 准备一个。</p>') +
      "</div></div></section>"
    );
  }

  function contractRows(contract) {
    if (!contract) return '<p class="empty">没有读到 contract.yaml。</p>';
    const dod = contract.doD || {};
    const rows = [];

    (dod.tests || []).forEach((test) => {
      rows.push(["验收命令", '<span class="mono">' + esc(test.cmd) + "</span>", test.timeout_s ? "超时 " + test.timeout_s + " s" : ""]);
    });
    if (dod.lint) {
      rows.push(["lint", "新增上限 " + esc(dod.lint.max_new) + " · 工具 " + esc(dod.lint.tool || "—")]);
    }
    if (dod.api) {
      const frozen = dod.api.frozen || [];
      rows.push(["api 冻结", frozen.length ? frozen.map(esc).join("<br>") : "未冻结"]);
    }
    (dod.exact || []).forEach((item) => {
      rows.push([
        "精确断言",
        '<span class="mono">' + esc(item.exception || "—") + " · contains " + esc(item.contains || "—") + "</span>",
        esc(item.source || "")
      ]);
    });
    if (dod.diff) {
      const forbid = (dod.diff.forbid || []).join("、") || "无";
      rows.push(["diff 预算", esc(dod.diff.max_files) + " 文件 / " + esc(dod.diff.max_lines) + " 行 · 禁 " + esc(forbid)]);
    }
    if (contract.loop) {
      rows.push([
        "循环",
        "修复 " + esc(contract.loop.repair_rounds) + " 轮 · 停止 " + esc(contract.loop.stop_on) + " · 用尽 " + esc(contract.loop.on_exhausted)
      ]);
    }

    return (
      '<dl class="kv is-single">' +
      rows
        .map(([key, value, note]) => "<div><dt>" + key + "</dt><dd>" + value + (note ? '<span class="kv-note">' + note + "</span>" : "") + "</dd></div>")
        .join("") +
      "</dl>"
    );
  }

  function modelStats(runs) {
    const groups = new Map();
    runs.forEach((run) => {
      /* 按模型名归组，不按 provider/model 拼接：同一次模型在个别 run 里缺 provider，
         拼起来会把它拆成两行（tabulate-366 上就是这样） */
      const key = run.model || run.provider || "—";
      const group = groups.get(key) || { label: key, total: 0, accepted: 0, tokens: 0, tokenRuns: 0, ms: 0, msRuns: 0 };
      group.total += 1;
      if (run.outcome === "accepted") group.accepted += 1;
      if (typeof run.tokens === "number") {
        group.tokens += run.tokens;
        group.tokenRuns += 1;
      }
      if (typeof run.duration_ms === "number") {
        group.ms += run.duration_ms;
        group.msRuns += 1;
      }
      groups.set(key, group);
    });
    return Array.from(groups.values()).sort((x, y) => y.total - x.total || x.label.localeCompare(y.label));
  }

  function modelTable(stats) {
    return (
      '<div class="table-scroll"><table class="runs model-table">' +
      '<caption class="sr-only">这个任务上各模型的表现</caption>' +
      '<thead><tr><th scope="col">模型</th><th scope="col" class="num">运行</th><th scope="col" class="num">成立</th>' +
      '<th scope="col" class="num">平均 token</th><th scope="col" class="num">平均耗时</th></tr></thead><tbody>' +
      stats
        .map(
          (group) =>
            '<tr><td class="mono">' + esc(group.label) + "</td>" +
            '<td class="num">' + group.total + "</td>" +
            '<td class="num">' + group.accepted + "</td>" +
            '<td class="num">' + (group.tokenRuns ? fmtNum(Math.round(group.tokens / group.tokenRuns)) : "—") + "</td>" +
            '<td class="num">' + (group.msRuns ? esc(fmtDuration(Math.round(group.ms / group.msRuns))) : "—") + "</td>" +
            "</tr>"
        )
        .join("") +
      "</tbody></table></div>"
    );
  }

  function runTimeline(runs) {
    const timed = runs
      .map((run) => ({ run, at: Date.parse(run.started_at) }))
      .filter((item) => !isNaN(item.at))
      .sort((x, y) => x.at - y.at);
    if (timed.length < 2) return "";

    const start = timed[0].at;
    const end = timed[timed.length - 1].at;
    const span = end - start || 1;
    const slots = new Map();

    const placed = timed.map(({ run, at }) => {
      const pct = ((at - start) / span) * 100;
      const key = Math.round(pct);
      const index = slots.get(key) || 0;
      slots.set(key, index + 1);
      return { run, pct, index };
    });

    const maxStack = Math.max.apply(null, Array.from(slots.values()));
    const height = 30 + maxStack * 11 + 22;

    const dots = placed
      .map(({ run, pct, index }) => {
        const cls =
          run.outcome === "accepted" ? "is-pass" : run.outcome === "rejected" ? "is-fail" : "is-unknown";
        const label =
          fmtTime(run.started_at) + " · " + (run.task_id || run.run_id) + " · " +
          modelLabel(run) + " · " + outcomeMeta(run.outcome).label;
        return (
          '<a class="tl-dot ' + cls + '" style="left:' + pct.toFixed(2) + "%;top:" + (26 + index * 11) + 'px" ' +
          'href="#/runs/' + encodeURIComponent(run.run_id) + '" ' +
          'title="' + esc(label) + '" aria-label="' + esc(label) + '"></a>'
        );
      })
      .join("");

    return (
      '<div class="run-timeline-scroll">' +
      '<div class="run-timeline" role="group" aria-label="这个任务上的运行时间轴" style="height:' + height + 'px">' +
      '<div class="run-timeline-track" aria-hidden="true"></div>' +
      dots +
      '<p class="run-timeline-end run-timeline-end--start">' + esc(fmtTime(timed[0].run.started_at)) + "</p>" +
      '<p class="run-timeline-end run-timeline-end--end">' + esc(fmtTime(timed[timed.length - 1].run.started_at)) + "</p>" +
      "</div></div>" +
      '<p class="run-timeline-legend">' +
      '<i class="tl-dot is-pass"></i>交付成立 ' +
      '<i class="tl-dot is-fail"></i>驳回 ' +
      '<i class="tl-dot is-unknown"></i>未记录 · 同一时刻多次运行会往下叠，点圆点进详情' +
      "</p>"
    );
  }

  function viewTask(id) {
    const task = DATA.tasks.find((item) => item.id === id);
    if (!task) {
      return (
        '<section class="band"><div class="wrap band-grid"><p class="clause"><span>task</span>任务详情</p><div class="band-body">' +
        crumbs([{ label: "概览", href: "#/" }, { label: "任务", href: "#/tasks" }, { label: id }]) +
        notFound("任务 " + id, "#/tasks") +
        "</div></div></section>"
      );
    }
    const related = DATA.runs.filter((run) => run.task_id === task.id);
    const runs = task.runs || { total: 0, accepted: 0, rejected: 0 };
    const stats = modelStats(related);

    return (
      '<section class="band">' +
      '<div class="wrap band-grid">' +
      '<p class="clause"><span>task</span>任务详情</p>' +
      '<div class="band-body">' +
      crumbs([{ label: "概览", href: "#/" }, { label: "任务", href: "#/tasks" }, { label: task.id }]) +
      '<p class="print-head">随行交付台 · 任务 ' + esc(task.id) + " · 数据快照 " + esc(fmtTime(DATA.generated_from.latest_run_at)) + " · runs/audit.jsonl</p>" +
      "<h2>" + esc(task.title) + "</h2>" +
      '<p class="lead">' + esc(task.statement) + "</p>" +
      '<div class="chips chips-inline">' +
      '<span class="chip">' + esc(task.id) + "</span>" +
      '<span class="chip chip-tier">' + esc(task.tier || "未分级") + "</span>" +
      (task.pull_request ? '<span class="chip">PR #' + esc(task.pull_request) + "</span>" : "") +
      "</div>" +
      '<h3 class="sub-title">契约</h3>' +
      contractRows(task.contract) +
      (task.contract_yaml
        ? '<details class="contract"><summary>contract.yaml 原文 · ' + esc(task.contract_source) + "</summary><pre><code>" + esc(task.contract_yaml) + "</code></pre></details>"
        : "") +
      '<div class="sub-head">' +
      '<h3 class="sub-title">这个任务上的运行 <span class="count">' + runs.total + "</span></h3>" +
      (related.length > 1
        ? '<a class="link-more" href="#/runs/compare?a=' + encodeURIComponent(related[1].run_id) +
          "&b=" + encodeURIComponent(related[0].run_id) + '" title="快捷键 c">对比最近两次</a>'
        : "") +
      "</div>" +
      runTimeline(related) +
      runsTable(related, {
        caption: task.id + " 的运行记录",
        empty: "这个任务还没有运行记录。"
      }) +
      (stats.length
        ? '<h3 class="sub-title">按模型 <span class="count">' + stats.length + "</span></h3>" + modelTable(stats)
        : "") +
      '<p class="runs-note">' + runs.accepted + " 次交付成立 · " + runs.rejected + " 次驳回 · " + runs.unknown + " 次未记录</p>" +
      "</div></div></section>"
    );
  }

  /* ---------------- 视图：运行 ---------------- */

  function filterRuns(q, outcome) {
    const needle = (q || "").trim().toLowerCase();
    return DATA.runs.filter((run) => {
      if (outcome === "unknown") {
        if (run.outcome) return false;
      } else if (outcome && outcome !== "all") {
        if (run.outcome !== outcome) return false;
      }
      if (!needle) return true;
      const haystack = [run.run_id, run.task_id, run.model, run.provider, run.protocol, run.backend, run.outcome]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.indexOf(needle) !== -1;
    });
  }

  function runsHash(q, outcome) {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (outcome && outcome !== "all") params.set("outcome", outcome);
    const qs = params.toString();
    return "#/runs" + (qs ? "?" + qs : "");
  }

  function runsCountText(list, q, outcome) {
    const filtered = Boolean(q) || (outcome && outcome !== "all");
    if (!filtered) {
      return "共 " + DATA.runs.length + " 次 · " + DATA.summary.runs_with_gate + " 次带闸门判定";
    }
    const parts = ["筛出 " + list.length + " 次 / 共 " + DATA.runs.length + " 次"];
    if (q) parts.push("关键字「" + q + "」");
    if (outcome && outcome !== "all") {
      parts.push("结果 " + (outcome === "unknown" ? "未记录" : outcomeMeta(outcome).label));
    }
    return parts.join(" · ");
  }

  function filterButtons(s, outcome) {
    const items = [
      ["all", "全部", s.runs],
      ["accepted", "交付成立", s.outcomes.accepted],
      ["rejected", "驳回", s.outcomes.rejected],
      ["unknown", "未记录", s.outcomes.unknown]
    ];
    return (
      '<div class="filters" role="group" aria-label="按结果筛选">' +
      items
        .map(
          ([value, label, count]) =>
            '<button class="filter' + (value === outcome ? " is-on" : "") + '" type="button" data-filter="' + value + '" ' +
            'aria-pressed="' + (value === outcome) + '">' + label + " <b>" + count + "</b></button>"
        )
        .join("") +
      "</div>"
    );
  }

  function viewRuns(route) {
    const q = route.params.get("q") || "";
    const outcome = route.params.get("outcome") || "all";
    const s = DATA.summary;
    const list = filterRuns(q, outcome);
    return (
      '<section class="band">' +
      '<div class="wrap band-grid">' +
      '<p class="clause"><span>runs</span>运行</p>' +
      '<div class="band-body">' +
      "<h2>运行记录</h2>" +
      '<p class="lead">按时间倒序。失败的运行也留着——那是数据，不是错误。点任意一行看闸门判定与事件时间线；表内可用 ↑↓ 移动、Enter 打开、<kbd class="kbd">/</kbd> 搜索。</p>' +
      '<div class="run-tools">' +
      '<label class="search"><span class="sr-only">搜索运行</span>' +
      '<input id="run-search" type="search" placeholder="搜索 run_id / 任务 / 模型" value="' + esc(q) + '" autocomplete="off" spellcheck="false">' +
      "<kbd>/</kbd></label>" +
      filterButtons(s, outcome) +
      "</div>" +
      '<div class="runs-headline">' +
      '<p class="runs-count">' + runsCountText(list, q, outcome) + "</p>" +
      '<button class="btn btn-ghost btn-small" id="export-csv" type="button">导出 CSV</button>' +
      "</div>" +
      '<div id="runs-view-body">' + runsTable(list, { caption: "筛选后的运行记录", empty: "这个筛选下没有记录。" }) + "</div>" +
      "</div></div></section>"
    );
  }

  function renderRunList() {
    const route = parseRoute();
    const body = $("#runs-view-body");
    if (route.name !== "runs" || !body) return;
    const q = route.params.get("q") || "";
    const outcome = route.params.get("outcome") || "all";
    const list = filterRuns(q, outcome);

    body.innerHTML = runsTable(list, {
      caption: "筛选后的运行记录",
      empty: "这个筛选下没有记录。换个关键字，或点「全部」。"
    });
    const count = $(".runs-count");
    if (count) count.textContent = runsCountText(list, q, outcome);
    document.querySelectorAll(".filter").forEach((btn) => {
      const on = (btn.dataset.filter || "all") === outcome;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-pressed", String(on));
    });
    mountRowLinks();
    mountRunsKeyboard();
  }

  function mountRunsTools() {
    const search = $("#run-search");
    if (!search) return;
    let timer = null;
    search.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const route = parseRoute();
        const outcome = route.params.get("outcome") || "all";
        history.replaceState(null, "", runsHash(search.value.trim(), outcome));
        renderRunList();
      }, 140);
    });
  }

  function mountRunsKeyboard() {
    const tbody = document.querySelector("#runs-view-body tbody");
    if (!tbody || tbody.dataset.kbdBound) return;
    tbody.dataset.kbdBound = "1";
    tbody.addEventListener("keydown", (event) => {
      const link = event.target.closest(".row-link");
      if (!link) return;
      const links = Array.from(tbody.querySelectorAll(".row-link"));
      const index = links.indexOf(link);
      let next = null;
      if (event.key === "ArrowDown") next = links[index + 1];
      else if (event.key === "ArrowUp") next = links[index - 1];
      else if (event.key === "Home") next = links[0];
      else if (event.key === "End") next = links[links.length - 1];
      if (next) {
        event.preventDefault();
        next.focus();
      }
    });
  }

  function eventSummary(event) {
    const parts = [];
    switch (event.event) {
      case "run_start":
        parts.push(event.interface, event.protocol, [event.provider, event.model].filter(Boolean).join(" / "));
        break;
      case "model_request":
        parts.push(event.seat, event.phase, event.model);
        break;
      case "model_response":
        parts.push(event.seat, event.model, event.tokens ? fmtNum(event.tokens) + " tokens" : "", event.latency_ms ? fmtDuration(event.latency_ms) : "", event.degraded ? "降级" : "");
        break;
      case "gate_report":
        parts.push(
          event.attempt ? "第 " + event.attempt + " 次" : "",
          event.gate_total + " 项",
          event.gate_failed ? "失败 " + event.gate_failed : "全绿"
        );
        break;
      case "runtime_run":
        parts.push(event.backend, event.model, event.calls ? event.calls + " 次调用" : "", event.tokens ? fmtNum(event.tokens) + " tokens" : "", event.stop_reason ? "停止：" + event.stop_reason : "");
        break;
      case "run_end":
        parts.push(
          event.outcome ? outcomeMeta(event.outcome).label : "",
          event.rounds ? event.rounds + " 轮" : "",
          event.events ? event.events + " 事件" : "",
          event.tokens ? fmtNum(event.tokens) + " tokens" : ""
        );
        break;
      default:
        break;
    }
    return parts.filter(Boolean).join(" · ");
  }

  function timelineItem(event) {
    const bad = (event.event === "gate_report" && event.gate_failed > 0) || (event.event === "run_end" && event.outcome && event.outcome !== "accepted");
    return (
      "<li class=\"" + (bad ? "is-bad" : "") + "\">" +
      '<time class="tl-time">' + esc(fmtClock(event.ts)) + "</time>" +
      '<span class="tl-event">' + esc(event.event) + "</span>" +
      '<span class="tl-body">' + esc(eventSummary(event)) + "</span>" +
      "</li>"
    );
  }

  function gateDetailRows(gate) {
    return (
      '<div class="gate-lines">' +
      gate.results
        .map((item) => {
          const state = item.ok ? "pass" : "fail";
          return (
            '<div class="gate-line" data-state="' + state + '">' +
            '<span class="lamp" aria-hidden="true"></span>' +
            '<span class="check-name">' + esc(item.name) + "</span>" +
            '<span class="gate-line-value">' + esc(gateValue(item.name, item.metrics)) + "</span>" +
            '<p class="gate-line-detail">' + esc(item.detail || "") + "</p>" +
            "</div>"
          );
        })
        .join("") +
      "</div>"
    );
  }

  function shortModel(value) {
    if (!value) return null;
    return String(value).split("/").pop();
  }

  function seatsText(seats) {
    if (!seats) return "—";
    const parts = [];
    if (seats.proposer) parts.push("提案 " + shortModel(seats.proposer));
    if (seats.proposer_b) parts.push("提案 B " + shortModel(seats.proposer_b));
    if (seats.reviewer) parts.push("复核 " + shortModel(seats.reviewer));
    if (seats.arbiter) parts.push("裁决 " + shortModel(seats.arbiter));
    return parts.join(" · ") || "—";
  }

  function patchSection(run) {
    if (!run.patch) return "";
    const patch = run.patch;
    if (!patch.available) {
      const reason = patch.reason === "unreadable" ? "文件存在但读不出来" : "文件不存在";
      return (
        '<h3 class="sub-title">补丁</h3>' +
        '<p class="patch-missing">' + reason + "：<span class=\"mono\">" + esc(patch.path) + "</span>。" +
        (patch.reason === "missing" ? "旧运行可能没有落盘，或 runs/ 被清理过。" : "") +
        "</p>"
      );
    }
    const files = (patch.files || [])
      .map((file) => {
        const autoOpen = (patch.files || []).length === 1 && file.chars <= 6000;
        return (
          '<details class="patch-file"' + (autoOpen ? " open" : "") +
          "><summary><span class=\"mono\">" + esc(file.path) + "</span>" +
          '<span class="patch-size">' + fmtNum(file.chars) + " 字符</span></summary>" +
          '<pre class="patch-code"><code>' + esc(file.content) + "</code></pre></details>"
        );
      })
      .join("");
    return (
      '<h3 class="sub-title">补丁 <span class="count">' + (patch.files || []).length + " 个文件</span></h3>" +
      (patch.rationale ? '<p class="patch-rationale">' + esc(patch.rationale) + "</p>" : "") +
      '<div class="patch-files">' + files + "</div>" +
      (patch.truncated ? '<p class="runs-note">内容较长，已截断显示（单文件 30k / 合计 60k 字符上限）。</p>' : "")
    );
  }

  function viewRun(id) {
    const run = DATA.runs.find((item) => item.run_id === id);
    if (!run) {
      return (
        '<section class="band"><div class="wrap band-grid"><p class="clause"><span>run</span>运行详情</p><div class="band-body">' +
        crumbs([{ label: "概览", href: "#/" }, { label: "运行", href: "#/runs" }, { label: id }]) +
        notFound("运行 " + id, "#/runs") +
        "</div></div></section>"
      );
    }
    const meta = outcomeMeta(run.outcome);
    const previous = previousRunFor(run);
    const kv = [
      ["任务", run.task_id ? '<a href="#/tasks/' + encodeURIComponent(run.task_id) + '">' + esc(run.task_id) + "</a>" : "—"],
      ["结果", run.outcome ? '<i class="dot ' + meta.dot + '"></i>' + meta.label : '<span class="muted">未记录</span>'],
      ["模型", esc(modelLabel(run))],
      ["协议 / 后端", esc(run.protocol || run.backend || "—")],
      ...(run.seats ? [["座位", esc(seatsText(run.seats))]] : []),
      ["token", fmtNum(run.tokens)],
      ["调用", run.calls === null || run.calls === undefined ? "—" : esc(run.calls)],
      ["耗时", esc(fmtDuration(run.duration_ms))],
      ["时间", esc(fmtTime(run.started_at)) + " → " + esc(fmtTime(run.ended_at))],
      ["事件", esc(run.events) + " 条 · 闸门尝试 " + esc(run.gate_attempts) + " 次"]
    ];

    return (
      '<section class="band">' +
      '<div class="wrap band-grid">' +
      '<p class="clause"><span>run</span>运行详情</p>' +
      '<div class="band-body">' +
      crumbs([{ label: "概览", href: "#/" }, { label: "运行", href: "#/runs" }, { label: run.run_id }]) +
      '<p class="print-head">随行交付台 · 运行 ' + esc(run.run_id) + " · 数据快照 " + esc(fmtTime(DATA.generated_from.latest_run_at)) + " · runs/audit.jsonl</p>" +
      "<h2>" + esc(run.task_id || run.run_id) + "</h2>" +
      '<p class="run-id mono">' + esc(run.run_id) + "</p>" +
      '<dl class="kv">' +
      kv.map(([key, value]) => "<div><dt>" + key + "</dt><dd>" + value + "</dd></div>").join("") +
      "</dl>" +
      (previous
        ? '<p class="compare-link"><a class="link-more" href="#/runs/compare?a=' +
          encodeURIComponent(previous.run_id) + "&b=" + encodeURIComponent(run.run_id) +
          '">与上一次同任务运行对比（' + esc(fmtTime(previous.started_at)) + "）</a></p>"
        : "") +
      '<h3 class="sub-title">闸门判定</h3>' +
      (run.gate
        ? '<p class="gate-verdict ' + (run.gate.all_green ? "is-pass" : "is-fail") + '">' +
          (run.gate.all_green ? "all_green · 交付成立" : "未通过 · 交付不成立") +
          "</p>" + gateDetailRows(run.gate)
        : '<p class="empty">这次运行没有闸门报告。</p>') +
      patchSection(run) +
      '<h3 class="sub-title">事件时间线 <span class="count">' + run.timeline.length + "</span></h3>" +
      '<ol class="timeline">' + run.timeline.map(timelineItem).join("") + "</ol>" +
      '<p class="runs-note">数据源 runs/audit.jsonl · 只展示事件元数据，不含模型原文。</p>' +
      "</div></div></section>"
    );
  }

  /* ---------------- 视图：对比 ---------------- */

  function previousRunFor(run) {
    const index = DATA.runs.findIndex((item) => item.run_id === run.run_id);
    for (let i = index + 1; i < DATA.runs.length; i += 1) {
      if (DATA.runs[i].task_id === run.task_id && DATA.runs[i].run_id !== run.run_id) {
        return DATA.runs[i];
      }
    }
    return null;
  }

  function gateOf(run, name) {
    if (!run.gate) return null;
    return run.gate.results.find((item) => item.name === name) || null;
  }

  function gateCell(run, name) {
    const item = gateOf(run, name);
    if (!item) return '<span class="muted">—</span>';
    return '<i class="dot ' + (item.ok ? "dot-pass" : "dot-fail") + '"></i>' + esc(gateValue(item.name, item.metrics));
  }

  function signedDelta(from, to, formatter) {
    if (typeof from !== "number" || typeof to !== "number") return "";
    const diff = to - from;
    if (!diff) return "持平";
    return (diff > 0 ? "+" : "−") + formatter(Math.abs(diff));
  }

  function viewCompare(route) {
    const a = DATA.runs.find((item) => item.run_id === route.params.get("a"));
    const b = DATA.runs.find((item) => item.run_id === route.params.get("b"));

    if (!a || !b) {
      return (
        '<section class="band"><div class="wrap band-grid"><p class="clause"><span>run</span>对比</p><div class="band-body">' +
        crumbs([{ label: "概览", href: "#/" }, { label: "运行", href: "#/runs" }, { label: "对比" }]) +
        notFound("这两次运行", "#/runs") +
        '<p class="runs-note">对比链接需要 ?a=&lt;run_id&gt; 与 ?b=&lt;run_id&gt; 两个参数。</p>' +
        "</div></div></section>"
      );
    }

    const rows = [];
    const push = (label, left, right, changed, note) => rows.push({ label, left, right, changed, note });
    const failCount = (run) => (run.gate ? run.gate.results.filter((item) => !item.ok).length : null);

    push("任务", esc(a.task_id || "—"), esc(b.task_id || "—"), a.task_id !== b.task_id, a.task_id !== b.task_id ? "不同" : "");
    push(
      "结果",
      outcomeCell(a),
      outcomeCell(b),
      a.outcome !== b.outcome,
      a.outcome !== b.outcome ? "不同" : ""
    );
    push("模型", esc(modelLabel(a)), esc(modelLabel(b)), modelLabel(a) !== modelLabel(b), modelLabel(a) !== modelLabel(b) ? "不同" : "");
    push(
      "协议 / 后端",
      esc(a.protocol || a.backend || "—"),
      esc(b.protocol || b.backend || "—"),
      (a.protocol || a.backend) !== (b.protocol || b.backend),
      (a.protocol || a.backend) !== (b.protocol || b.backend) ? "不同" : ""
    );
    push("token", fmtNum(a.tokens), fmtNum(b.tokens), a.tokens !== b.tokens, signedDelta(a.tokens, b.tokens, (n) => n.toLocaleString("en-US")));
    push(
      "调用",
      a.calls === null || a.calls === undefined ? "—" : esc(a.calls),
      b.calls === null || b.calls === undefined ? "—" : esc(b.calls),
      a.calls !== b.calls,
      signedDelta(a.calls, b.calls, String)
    );
    push(
      "耗时",
      esc(fmtDuration(a.duration_ms)),
      esc(fmtDuration(b.duration_ms)),
      a.duration_ms !== b.duration_ms,
      signedDelta(a.duration_ms, b.duration_ms, fmtDuration)
    );
    GATES.forEach((name) => {
      const left = gateOf(a, name);
      const right = gateOf(b, name);
      push(name, gateCell(a, name), gateCell(b, name), Boolean(left && right) && left.ok !== right.ok, left && right && left.ok !== right.ok ? "不同" : "");
    });

    const summaryParts = [
      a.task_id === b.task_id ? "同一任务 " + esc(a.task_id) : "不同任务",
      "结果 " + outcomeMeta(a.outcome).label + " → " + outcomeMeta(b.outcome).label,
      "未通过 " + (failCount(a) === null ? "—" : failCount(a)) + " → " + (failCount(b) === null ? "—" : failCount(b))
    ];

    return (
      '<section class="band">' +
      '<div class="wrap band-grid">' +
      '<p class="clause"><span>run</span>对比</p>' +
      '<div class="band-body">' +
      crumbs([{ label: "概览", href: "#/" }, { label: "运行", href: "#/runs" }, { label: "对比" }]) +
      "<h2>对比两次运行</h2>" +
      '<p class="lead">同行对照：左列 A、右列 B。结果、模型、闸门判定不同时，「差异」列会标出来。</p>' +
      '<p class="compare-summary mono">' + summaryParts.join(" · ") + "</p>" +
      '<div class="compare-actions">' +
      '<a class="link-more" href="#/runs/' + encodeURIComponent(a.run_id) + '">打开 A · ' + esc(fmtTime(a.started_at)) + "</a>" +
      '<a class="link-more" href="#/runs/' + encodeURIComponent(b.run_id) + '">打开 B · ' + esc(fmtTime(b.started_at)) + "</a>" +
      "</div>" +
      '<div class="table-scroll"><table class="runs compare-table">' +
      '<caption class="sr-only">两次运行的逐项对比</caption>' +
      "<thead><tr>" +
      '<th scope="col">属性</th>' +
      '<th scope="col"><span class="th-main">A · ' + esc(fmtTime(a.started_at)) + '</span><span class="th-sub">' + esc(a.run_id) + "</span></th>" +
      '<th scope="col"><span class="th-main">B · ' + esc(fmtTime(b.started_at)) + '</span><span class="th-sub">' + esc(b.run_id) + "</span></th>" +
      '<th scope="col" class="num">差异</th>' +
      "</tr></thead><tbody>" +
      rows
        .map(
          (row) =>
            "<tr" + (row.changed ? ' class="is-diff"' : "") + ">" +
            "<td>" + esc(row.label) + "</td>" +
            "<td>" + row.left + "</td>" +
            "<td>" + row.right + "</td>" +
            '<td class="num">' + (row.changed ? '<span class="diff-flag">' + esc(row.note || "不同") + "</span>" : '<span class="muted">—</span>') + "</td>" +
            "</tr>"
        )
        .join("") +
      "</tbody></table></div>" +
      '<p class="runs-note">对比只读两次运行已有的记录；不重跑、不重算闸门。</p>' +
      "</div></div></section>"
    );
  }

  /* ---------------- 导出 CSV ---------------- */

  function csvCell(value) {
    const text = value === null || value === undefined ? "" : String(value);
    return /[",\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
  }

  function buildRunsCsv(runs) {
    const header = [
      "run_id", "started_at", "ended_at", "task_id", "provider", "model", "protocol", "backend",
      "outcome", "tokens", "calls", "duration_ms", "all_green", "tests", "lint", "api", "diff"
    ];
    const flag = (run, name) => {
      const item = gateOf(run, name);
      return item ? (item.ok ? "pass" : "fail") : "";
    };
    const lines = [header.join(",")];
    runs.forEach((run) => {
      lines.push(
        [
          run.run_id, run.started_at, run.ended_at, run.task_id, run.provider, run.model,
          run.protocol, run.backend, run.outcome, run.tokens, run.calls, run.duration_ms,
          run.gate ? String(run.gate.all_green) : "",
          flag(run, "tests"), flag(run, "lint"), flag(run, "api"), flag(run, "diff")
        ]
          .map(csvCell)
          .join(",")
      );
    });
    return lines.join("\r\n") + "\r\n";
  }

  function downloadCsv(runs) {
    const blob = new Blob(["\ufeff" + buildRunsCsv(runs)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "runs-" + new Date().toISOString().slice(0, 10) + ".csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  /* ---------------- 路由 ---------------- */

  function parseRoute() {
    const raw = (location.hash || "").replace(/^#/, "");
    const cut = raw.indexOf("?");
    const path = cut === -1 ? raw : raw.slice(0, cut);
    const params = new URLSearchParams(cut === -1 ? "" : raw.slice(cut + 1));
    const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
    if (!parts.length) return { name: "home", params };
    if (parts[0] === "tasks") return parts[1] ? { name: "task", id: parts[1], params } : { name: "tasks", params };
    if (parts[0] === "runs") {
      if (parts[1] === "compare") return { name: "compare", params };
      return parts[1] ? { name: "run", id: parts[1], params } : { name: "runs", params };
    }
    return { name: "home", params };
  }

  function renderView(route) {
    switch (route.name) {
      case "tasks":
        return viewTasks();
      case "task":
        return viewTask(route.id);
      case "runs":
        return viewRuns(route);
      case "run":
        return viewRun(route.id);
      case "compare":
        return viewCompare(route);
      default:
        return "";
    }
  }

  function mountRowLinks() {
    document.querySelectorAll("tr.run-row").forEach((tr) => {
      if (tr.dataset.bound) return;
      tr.dataset.bound = "1";
      tr.addEventListener("click", (event) => {
        if (event.target.closest("a")) return;
        location.hash = "#/runs/" + encodeURIComponent(tr.dataset.runId);
      });
    });
  }

  let lastPath = "";

  function routeTitle(route) {
    switch (route.name) {
      case "tasks":
        return "任务 · 随行交付台";
      case "task":
        return route.id + " · 任务 · 随行交付台";
      case "runs":
        return "运行 · 随行交付台";
      case "run":
        return route.id + " · 运行 · 随行交付台";
      case "compare":
        return "对比 · 随行交付台";
      default:
        return "随行交付台 — 模型可以争论，闸门说了算";
    }
  }

  function bindAppEvents(route) {
    const app = $("#app");
    if (!app) return;
    app.querySelectorAll(".filter").forEach((btn) => {
      btn.addEventListener("click", () => {
        const current = parseRoute();
        const q = current.params.get("q") || "";
        location.hash = runsHash(q, btn.dataset.filter || "all");
      });
    });
    if (route.name === "runs") {
      mountRunsTools();
      mountRunsKeyboard();
    }
    const exportBtn = $("#export-csv");
    if (exportBtn) {
      exportBtn.addEventListener("click", () => {
        const current = parseRoute();
        downloadCsv(filterRuns(current.params.get("q") || "", current.params.get("outcome") || "all"));
      });
    }
    mountRowLinks();
  }

  function render() {
    const route = parseRoute();
    const home = $("#home");
    const app = $("#app");
    const isHome = route.name === "home";
    const path = route.name + ":" + (route.id || "");

    if (home) home.hidden = !isHome;
    if (app) {
      app.hidden = isHome;
      if (!isHome) {
        app.innerHTML = renderView(route);
        bindAppEvents(route);
      }
    }

    document.querySelectorAll(".nav a").forEach((link) => {
      const target = link.dataset.route;
      const on =
        (target === "home" && isHome) ||
        (target === "tasks" && (route.name === "tasks" || route.name === "task")) ||
        (target === "runs" && (route.name === "runs" || route.name === "run" || route.name === "compare"));
      link.classList.toggle("is-active", on);
      link.setAttribute("aria-current", on ? "page" : "false");
    });

    document.title = routeTitle(route);

    /* 只有换了页面才回到顶部；切筛选、改关键字不动滚动位置 */
    if (path !== lastPath) {
      window.scrollTo(0, 0);
      if (!isHome && app) {
        const heading = app.querySelector("h2");
        if (heading) {
          heading.setAttribute("tabindex", "-1");
          heading.focus({ preventScroll: true });
        }
      }
    }
    lastPath = path;
  }

  /* ---------------- 启动 ---------------- */

  if (!DATA) {
    const app = $("#app");
    if (app) {
      document.querySelectorAll(".nav a").forEach((link) => (link.hidden = true));
      app.hidden = false;
      const home = $("#home");
      if (home) home.hidden = true;
      app.innerHTML =
        '<section class="band"><div class="wrap band-grid"><p class="clause"><span>setup</span>还没数据</p>' +
        '<div class="band-body"><h2>先生成数据</h2>' +
        '<p class="lead">页面读的是 <span class="mono">web/data/site.js</span>，它由构建脚本生成。</p>' +
        '<pre class="code-block">python web/build-data.py</pre>' +
        '<p class="runs-note">生成后刷新这一页。</p></div></div></section>';
    }
    return;
  }

  renderHome();
  initBoard();

  /* 打印预览：?print=1 时把 print.css 当普通样式加载，用屏幕看纸上效果 */
  const printLink = document.querySelector('link[media="print"]');
  if (printLink && new URLSearchParams(location.search).has("print")) printLink.media = "all";

  /* 全局快捷键：/ 聚焦搜索（只在运行列表），Esc 清空，c 对比（任务/运行详情） */
  document.addEventListener("keydown", (event) => {
    const tag = (event.target.tagName || "").toLowerCase();
    const typing = tag === "input" || tag === "textarea" || event.target.isContentEditable;
    const plain = !event.metaKey && !event.ctrlKey && !event.altKey;

    if (event.key === "/" && plain && !typing) {
      if (parseRoute().name !== "runs") return;
      const search = $("#run-search");
      if (search) {
        event.preventDefault();
        search.focus();
        search.select();
      }
      return;
    }

    if (event.key === "c" && plain && !typing) {
      const route = parseRoute();
      if (route.name === "run") {
        const run = DATA.runs.find((item) => item.run_id === route.id);
        const previous = run ? previousRunFor(run) : null;
        if (previous) {
          event.preventDefault();
          location.hash = "#/runs/compare?a=" + encodeURIComponent(previous.run_id) + "&b=" + encodeURIComponent(run.run_id);
        }
      } else if (route.name === "task") {
        const related = DATA.runs.filter((item) => item.task_id === route.id);
        if (related.length > 1) {
          event.preventDefault();
          location.hash = "#/runs/compare?a=" + encodeURIComponent(related[1].run_id) + "&b=" + encodeURIComponent(related[0].run_id);
        }
      }
      return;
    }

    if (event.key === "Escape" && event.target.id === "run-search") {
      if (event.target.value) {
        event.target.value = "";
        event.target.dispatchEvent(new Event("input"));
      } else {
        event.target.blur();
      }
    }
  });

  render();

  window.addEventListener("hashchange", render);

  if (boardRun && boardRun.gate) {
    const isHome = parseRoute().name === "home";
    if (isHome) setTimeout(replay, reduceMotion.matches ? 0 : 650);
  }

  if (seal) {
    let stamps = 0;
    seal.addEventListener("click", () => {
      if (!seal.classList.contains("is-on")) return;
      seal.classList.remove("is-on");
      void seal.offsetWidth;
      seal.classList.add("is-on");
      stamps += 1;
      const meta = $("#foot-meta");
      if (meta) meta.dataset.stamps = String(stamps);
    });
  }
})();
