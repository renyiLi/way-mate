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
      "</tbody></table></div>"
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

    return (
      '<section class="band">' +
      '<div class="wrap band-grid">' +
      '<p class="clause"><span>task</span>任务详情</p>' +
      '<div class="band-body">' +
      crumbs([{ label: "概览", href: "#/" }, { label: "任务", href: "#/tasks" }, { label: task.id }]) +
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
      '<h3 class="sub-title">这个任务上的运行 <span class="count">' + runs.total + "</span></h3>" +
      runsTable(related, {
        caption: task.id + " 的运行记录",
        empty: "这个任务还没有运行记录。"
      }) +
      '<p class="runs-note">' + runs.accepted + " 次交付成立 · " + runs.rejected + " 次驳回 · " + runs.unknown + " 次未记录</p>" +
      "</div></div></section>"
    );
  }

  /* ---------------- 视图：运行 ---------------- */

  function viewRuns() {
    const s = DATA.summary;
    return (
      '<section class="band">' +
      '<div class="wrap band-grid">' +
      '<p class="clause"><span>runs</span>运行</p>' +
      '<div class="band-body">' +
      "<h2>全部 " + s.runs + " 次运行</h2>" +
      '<p class="lead">按时间倒序。失败的运行也留着——那是数据，不是错误。点任意一行看这次运行的闸门判定与事件时间线。</p>' +
      '<div class="filters" role="group" aria-label="按结果筛选">' +
      '<button class="filter is-on" type="button" data-filter="all">全部 <b>' + s.runs + "</b></button>" +
      '<button class="filter" type="button" data-filter="accepted">交付成立 <b>' + s.outcomes.accepted + "</b></button>" +
      '<button class="filter" type="button" data-filter="rejected">驳回 <b>' + s.outcomes.rejected + "</b></button>" +
      '<button class="filter" type="button" data-filter="unknown">未记录 <b>' + s.outcomes.unknown + "</b></button>" +
      "</div>" +
      '<div id="runs-view-body">' + runsTable(DATA.runs, { caption: "全部运行记录" }) + "</div>" +
      "</div></div></section>"
    );
  }

  function applyFilter(value) {
    const body = $("#runs-view-body");
    if (!body) return;
    const list = value === "all" ? DATA.runs : value === "unknown"
      ? DATA.runs.filter((run) => !run.outcome)
      : DATA.runs.filter((run) => run.outcome === value);
    body.innerHTML = runsTable(list, {
      caption: "筛选后的运行记录",
      empty: "这个筛选下没有记录。"
    });
    document.querySelectorAll(".filter").forEach((btn) => {
      btn.classList.toggle("is-on", btn.dataset.filter === value);
    });
    mountRowLinks();
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
    const kv = [
      ["任务", run.task_id ? '<a href="#/tasks/' + encodeURIComponent(run.task_id) + '">' + esc(run.task_id) + "</a>" : "—"],
      ["结果", run.outcome ? '<i class="dot ' + meta.dot + '"></i>' + meta.label : '<span class="muted">未记录</span>'],
      ["模型", esc(modelLabel(run))],
      ["协议 / 后端", esc(run.protocol || run.backend || "—")],
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
      "<h2>" + esc(run.task_id || run.run_id) + "</h2>" +
      '<p class="run-id mono">' + esc(run.run_id) + "</p>" +
      '<dl class="kv">' +
      kv.map(([key, value]) => "<div><dt>" + key + "</dt><dd>" + value + "</dd></div>").join("") +
      "</dl>" +
      '<h3 class="sub-title">闸门判定</h3>' +
      (run.gate
        ? '<p class="gate-verdict ' + (run.gate.all_green ? "is-pass" : "is-fail") + '">' +
          (run.gate.all_green ? "all_green · 交付成立" : "未通过 · 交付不成立") +
          "</p>" + gateDetailRows(run.gate)
        : '<p class="empty">这次运行没有闸门报告。</p>') +
      '<h3 class="sub-title">事件时间线 <span class="count">' + run.timeline.length + "</span></h3>" +
      '<ol class="timeline">' + run.timeline.map(timelineItem).join("") + "</ol>" +
      '<p class="runs-note">数据源 runs/audit.jsonl · 只展示事件元数据，不含模型原文。</p>' +
      "</div></div></section>"
    );
  }

  /* ---------------- 路由 ---------------- */

  function parseRoute() {
    const raw = (location.hash || "").replace(/^#/, "");
    const parts = raw.split("/").filter(Boolean).map(decodeURIComponent);
    if (!parts.length) return { name: "home" };
    if (parts[0] === "tasks") return parts[1] ? { name: "task", id: parts[1] } : { name: "tasks" };
    if (parts[0] === "runs") return parts[1] ? { name: "run", id: parts[1] } : { name: "runs" };
    return { name: "home" };
  }

  function renderView(route) {
    switch (route.name) {
      case "tasks":
        return viewTasks();
      case "task":
        return viewTask(route.id);
      case "runs":
        return viewRuns();
      case "run":
        return viewRun(route.id);
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

  function render() {
    const route = parseRoute();
    const home = $("#home");
    const app = $("#app");
    const isHome = route.name === "home";

    if (home) home.hidden = !isHome;
    if (app) {
      app.hidden = isHome;
      if (!isHome) {
        app.innerHTML = renderView(route);
        const filters = app.querySelectorAll(".filter");
        filters.forEach((btn) => btn.addEventListener("click", () => applyFilter(btn.dataset.filter)));
        mountRowLinks();
      }
    }

    document.querySelectorAll(".nav a").forEach((link) => {
      const target = link.dataset.route;
      const on =
        (target === "home" && isHome) ||
        (target === "tasks" && (route.name === "tasks" || route.name === "task")) ||
        (target === "runs" && (route.name === "runs" || route.name === "run"));
      link.classList.toggle("is-active", on);
    });

    window.scrollTo(0, 0);
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
