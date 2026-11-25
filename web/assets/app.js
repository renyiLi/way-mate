/* 随行交付台 · 外壳渲染
   数据来自 window.__DBW_DATA__（web/data/site.js，由 web/build-data.py 生成）。
   页面不发起任何网络请求，也不自己判定"是否可交付"——只读 all_green。 */

(function () {
  "use strict";

  const DATA = window.__DBW_DATA__ || null;
  const GATES = ["tests", "lint", "api", "diff"];
  const FIRST_PAGE = 12;
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
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
    return match ? match[2] + "-" + match[3] + " " + match[4] + ":" + match[5] : iso;
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

  /* 闸门值：只格式化 metrics，不重算判定 */
  function gateValue(name, metrics) {
    const m = metrics || {};
    if (name === "tests") {
      const counts = m.counts || {};
      if (typeof counts.passed === "number" && typeof counts.failed === "number") {
        return counts.passed + " passed · " + counts.failed + " failed";
      }
      if (typeof counts.passed === "number") return counts.passed + " passed";
      if (typeof counts.failed === "number") return counts.failed + " failed";
      if (typeof m.exit_code === "number") return "exit " + m.exit_code;
      return "—";
    }
    if (name === "lint") {
      if (typeof m.new === "number") {
        return "new " + m.new + " / max " + (m.max_new === undefined ? "—" : m.max_new);
      }
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

  function pickBoardRun() {
    if (!DATA || !Array.isArray(DATA.runs) || !DATA.runs.length) return null;
    const latest = DATA.runs[0];
    if (latest.gate) return latest;
    const withGate = DATA.runs.find((run) => run.gate);
    return withGate || latest;
  }

  /* ---------------- 判定板 ---------------- */

  const board = $("#board");
  const rows = Array.from(document.querySelectorAll(".check"));
  const failCount = $("#fail-count");
  const boardSub = $("#board-sub");
  const seal = $("#seal");
  const boardTask = $("#board-task");
  const boardMeta = $("#board-meta");
  const replayBtn = $("#replay");

  const boardRun = pickBoardRun();
  const resultByName = {};
  if (boardRun && boardRun.gate) {
    boardRun.gate.results.forEach((item, index) => {
      resultByName[item.name] = item;
      if (!GATES.includes(item.name) && index < 4) GATES[index] = item.name;
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
      row.querySelector(".check-value").textContent = result
        ? gateValue(result.name, result.metrics)
        : "—";
    });
    const fails = boardRun.gate.results.filter((item) => !item.ok).length;
    failCount.textContent = String(fails);
    boardSub.textContent = boardRun.gate.all_green ? "4 项全绿 · 交付成立" : fails + " 项未通过 · 交付不成立";
    seal.textContent = boardRun.gate.all_green ? "可交付" : "不予交付";
    seal.classList.toggle("is-wide", !boardRun.gate.all_green);
    seal.classList.add("is-on");
    board.classList.add("has-verdict");
  }

  let running = false;

  async function replay() {
    if (!boardRun || !boardRun.gate || running) return;
    const paced = !reduceMotion.matches;
    running = true;

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
    running = false;
  }

  /* ---------------- 首屏数字 ---------------- */

  function renderFacts() {
    const wrap = $("#facts");
    if (!wrap || !DATA) return;
    const summary = DATA.summary;
    const cells = [
      ["任务", summary.tasks],
      ["运行", summary.runs],
      ["交付成立", summary.outcomes.accepted],
      ["闸门判定", summary.runs_with_gate]
    ];
    wrap.innerHTML = cells
      .map(([label, value]) => "<div><dt>" + label + "</dt><dd>" + value + "</dd></div>")
      .join("");
  }

  /* ---------------- 任务 ---------------- */

  function taskMeta(task) {
    const contract = task.contract || {};
    const dod = contract.doD || {};
    const diff = dod.diff || {};
    const rowsOut = [];

    rowsOut.push(["仓库", esc(stripRepo(task.repo))]);
    if (task.target_test) {
      rowsOut.push(["验收", '<span class="mono">' + esc(task.target_test) + "</span>", "wide"]);
    }

    const budget = [];
    if (typeof diff.max_files === "number") budget.push(diff.max_files + " 文件");
    if (typeof diff.max_lines === "number") budget.push(diff.max_lines + " 行");
    if (Array.isArray(diff.forbid) && diff.forbid.length) budget.push("禁 " + diff.forbid.join("、"));
    if (budget.length) rowsOut.push(["预算", '<span class="mono">' + esc(budget.join(" / ")) + "</span>"]);

    const verified = task.verified || {};
    if (verified.red_without_fix && verified.green_with_fix) {
      rowsOut.push([
        "自检",
        '<span class="mono">' + esc(verified.red_without_fix + " → " + verified.green_with_fix) + "</span>",
        "wide"
      ]);
    }
    if (task.pull_request) rowsOut.push(["上游", "PR #" + esc(task.pull_request)]);

    return rowsOut
      .map(([key, value, cls]) => '<div class="' + (cls || "") + '"><dt>' + key + "</dt><dd>" + value + "</dd></div>")
      .join("");
  }

  function renderTasks() {
    const wrap = $("#tasks-list");
    if (!wrap || !DATA) return;
    if (!DATA.tasks.length) {
      wrap.innerHTML = '<p class="empty">tasks/ 下还没有任务。先用 <span class="mono">scripts/prepare_task.py</span> 准备一个。</p>';
      return;
    }
    wrap.innerHTML = DATA.tasks
      .map((task) => {
        const runs = task.runs || { total: 0, accepted: 0 };
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
          "<h3>" + esc(task.title) + "</h3>" +
          '<p class="task-stmt">' + esc(task.statement) + "</p>" +
          '<dl class="task-meta">' + taskMeta(task) + "</dl>" +
          contractBlock +
          "</article>"
        );
      })
      .join("");
  }

  /* ---------------- 闸门统计 ---------------- */

  function renderGateStats() {
    if (!DATA) return;
    (DATA.gate_stats || []).forEach((item) => {
      const node = document.querySelector('[data-gate-stat="' + item.name + '"]');
      if (!node) return;
      node.textContent = item.evaluated + " 次判定 · 失败 " + item.failed;
    });
  }

  /* ---------------- 运行记录 ---------------- */

  function outcomeCell(run) {
    const meta = OUTCOME[run.outcome];
    if (!meta) return '<span class="muted">未记录</span>';
    return '<i class="dot ' + meta.dot + '"></i>' + meta.label;
  }

  function signatureCell(run) {
    if (!run.gate) return '<span class="muted">—</span>';
    const marks = run.gate.results
      .map((item) => '<i class="' + (item.ok ? "ok" : "no") + '" title="' + esc(item.name + (item.ok ? " 通过" : " 未通过")) + '"></i>')
      .join("");
    return '<span class="sig">' + marks + "</span>";
  }

  function renderRuns() {
    const body = $("#runs-body");
    if (!body || !DATA) return;

    if (!DATA.runs.length) {
      body.innerHTML =
        '<tr><td colspan="8" class="table-empty">还没有运行记录。先跑一次 <span class="mono">python scripts/run_once.py …</span>，再执行 <span class="mono">python web/build-data.py</span>。</td></tr>';
      return;
    }

    body.innerHTML = DATA.runs
      .map((run, index) => {
        return (
          '<tr class="' + (index >= FIRST_PAGE ? "is-hidden" : "") + '">' +
          '<td class="nowrap">' + esc(fmtTime(run.started_at)) + "</td>" +
          '<td class="mono">' + esc(run.task_id || "—") + "</td>" +
          '<td class="mono">' + esc(modelLabel(run)) + "</td>" +
          '<td class="mono">' + esc(run.protocol || run.backend || "—") + "</td>" +
          "<td>" + signatureCell(run) + "</td>" +
          "<td>" + outcomeCell(run) + "</td>" +
          '<td class="num">' + fmtNum(run.tokens) + "</td>" +
          '<td class="num">' + esc(fmtDuration(run.duration_ms)) + "</td>" +
          "</tr>"
        );
      })
      .join("");

    const note = $("#runs-note");
    const more = $("#runs-more");
    const total = DATA.runs.length;
    if (note) {
      note.textContent =
        "显示最近 " + Math.min(FIRST_PAGE, total) + " 次，共 " + total + " 次 · " +
        DATA.summary.runs_with_gate + " 次带闸门判定";
    }
    if (more && total > FIRST_PAGE) {
      more.hidden = false;
      more.textContent = "展开全部 " + total + " 次";
      more.addEventListener("click", () => {
        const expanded = more.dataset.expanded === "1";
        document.querySelectorAll("#runs-body tr.is-hidden").forEach((tr) => {
          tr.classList.toggle("is-hidden", expanded);
        });
        more.dataset.expanded = expanded ? "0" : "1";
        more.textContent = expanded ? "展开全部 " + total + " 次" : "收起";
      });
    }
  }

  /* ---------------- 页脚与空态 ---------------- */

  function renderFooter() {
    const meta = $("#foot-meta");
    if (!meta || !DATA) return;
    const source = DATA.generated_from;
    meta.textContent =
      "数据 runs/audit.jsonl · " + source.audit_events + " 事件 · 最近运行 " + fmtTime(source.latest_run_at);
  }

  function renderNoData() {
    const boardTaskNode = $("#board-task");
    if (boardTaskNode) boardTaskNode.textContent = "还没有生成数据";
    if (boardSub) boardSub.textContent = "运行 python web/build-data.py 后刷新";
    if (replayBtn) replayBtn.hidden = true;
    const body = $("#runs-body");
    if (body) {
      body.innerHTML =
        '<tr><td colspan="8" class="table-empty">缺少 <span class="mono">web/data/site.js</span>。在仓库根目录执行 <span class="mono">python web/build-data.py</span>，再刷新这一页。</td></tr>';
    }
    const tasks = $("#tasks-list");
    if (tasks) {
      tasks.innerHTML = '<p class="empty">还没有数据。先执行 <span class="mono">python web/build-data.py</span>。</p>';
    }
    const note = $("#runs-note");
    if (note) note.textContent = "等待构建数据";
  }

  /* ---------------- 启动 ---------------- */

  if (!DATA) {
    renderNoData();
    return;
  }

  renderFacts();
  renderTasks();
  renderGateStats();
  renderRuns();
  renderFooter();

  if (boardRun) {
    fillBoard();
    if (replayBtn) replayBtn.addEventListener("click", replay);
    if (boardRun.gate) {
      setTimeout(replay, reduceMotion.matches ? 0 : 650);
    }
  } else {
    if (boardTask) boardTask.textContent = "还没有运行记录";
    if (boardSub) boardSub.textContent = "先跑一次 scripts/run_once.py";
    if (replayBtn) replayBtn.hidden = true;
  }

  if (seal) {
    let stamps = 0;
    seal.addEventListener("click", () => {
      if (!seal.classList.contains("is-on")) return;
      seal.classList.remove("is-on");
      void seal.offsetWidth;
      seal.classList.add("is-on");
      stamps += 1;
      const footMeta = $("#foot-meta");
      if (footMeta) footMeta.dataset.stamps = String(stamps);
    });
  }

  /* ---------------- 导航高亮 ---------------- */

  const navLinks = new Map(
    Array.from(document.querySelectorAll(".nav a")).map((a) => [a.getAttribute("href").slice(1), a])
  );
  const sections = Array.from(document.querySelectorAll("main > section[id]"));

  if (sections.length) {
    let ticking = false;
    const updateActive = () => {
      ticking = false;
      const line = 140;
      let current = sections[0];
      for (const section of sections) {
        if (section.getBoundingClientRect().top <= line) current = section;
      }
      navLinks.forEach((link, id) => link.classList.toggle("is-active", id === current.id));
    };
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(updateActive);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    updateActive();
  }
})();
