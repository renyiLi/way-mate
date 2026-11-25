#!/usr/bin/env node
/**
 * 壳的回归脚本：自己起静态服务、自己拉 headless Chrome、跑一组真实交互断言。
 *
 *   node web/check.mjs            # 跑全部用例
 *   node web/check.mjs --filter patch
 *   CHROME_PATH=/path/to/chrome node web/check.mjs
 *
 * 零依赖：用 node 内置 http + WebSocket（Node 20+），CDP 驱动 Chrome。
 * 退出码 0 表示全绿；1 表示有用例失败（会逐条打印）。
 */

import { createServer } from "node:http";
import { readFile, stat, mkdtemp, rm, cp, mkdir, writeFile } from "node:fs/promises";
import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, extname, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(WEB, "..");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".woff2": "font/woff2",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".md": "text/markdown; charset=utf-8"
};

const execFileAsync = promisify(execFile);

async function runPython(args) {
  try {
    const { stdout, stderr } = await execFileAsync(process.env.PYTHON || "python", args, { cwd: ROOT });
    return { code: 0, stdout, stderr };
  } catch (error) {
    return { code: error.code || 1, stdout: error.stdout || "", stderr: error.stderr || String(error) };
  }
}

function findChrome() {  if (process.env.CHROME_PATH && existsSync(process.env.CHROME_PATH)) return process.env.CHROME_PATH;
  const candidates = [
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium"
  ];
  return candidates.find((path) => existsSync(path)) || null;
}

/* ---------------- 静态服务（含 fixture 注入） ---------------- */

async function startServer() {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    try {
      if (url.pathname === "/web/__fixture") {
        const html = await buildFixture(url.searchParams.get("case") || "missing-patch");
        res.writeHead(200, { "content-type": MIME[".html"], "cache-control": "no-store" });
        res.end(html);
        return;
      }
      const filePath = join(ROOT, decodeURIComponent(url.pathname));
      if (!filePath.startsWith(ROOT)) {
        res.writeHead(403).end("forbidden");
        return;
      }
      const info = await stat(filePath).catch(() => null);
      const target = info && info.isDirectory() ? join(filePath, "index.html") : filePath;
      const body = await readFile(target);
      res.writeHead(200, { "content-type": MIME[extname(target)] || "application/octet-stream", "cache-control": "no-store" });
      res.end(body);
    } catch {
      res.writeHead(404, { "content-type": "text/plain; charset=utf-8" }).end("not found");
    }
  });
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  return { server, port: server.address().port };
}

function escapeForInlineScript(json) {
  return json.replace(/</g, "\\u003c").replace(/\u2028/g, "\\u2028").replace(/\u2029/g, "\\u2029");
}

async function buildFixture(name) {
  const html = await readFile(join(WEB, "index.html"), "utf8");
  const data = JSON.parse(await readFile(join(WEB, "data", "site.json"), "utf8"));

  if (name === "missing-patch") {
    const run = data.runs.find((item) => item.gate) || data.runs[0];
    run.patch = { path: "runs/patches/" + run.run_id + ".json", available: false, reason: "missing" };
    data.__fixture = { case: name, run_id: run.run_id };
  } else if (name === "with-patch") {
    const run = data.runs.find((item) => item.gate) || data.runs[0];
    run.patch = {
      path: "runs/patches/" + run.run_id + ".json",
      available: true,
      files: [{ path: "tabulate/__init__.py", chars: 24, content: "def parse_kv(text):\n    pass\n" }],
      rationale: "fixture 里塞的假补丁，只用来验证渲染路径",
      truncated: false
    };
    data.__fixture = { case: name, run_id: run.run_id };
  }

  const injection =
    '<script>window.__DBW_DATA__ = ' + escapeForInlineScript(JSON.stringify(data)) + ";</script>";
  return html.replace('<script src="data/site.js"></script>', injection);
}

/* ---------------- CDP ---------------- */

function connect(url) {
  return new Promise((resolveConnect, reject) => {
    const socket = new WebSocket(url);
    socket.onopen = () => resolveConnect(socket);
    socket.onerror = () => reject(new Error("CDP websocket failed"));
  });
}

function makeClient(socket) {
  let nextId = 0;
  const pending = new Map();
  const exceptions = [];
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.method === "Runtime.exceptionThrown") {
      exceptions.push(message.params.exceptionDetails.text || "exception");
      return;
    }
    if (message.id && pending.has(message.id)) {
      const { resolveCall, rejectCall } = pending.get(message.id);
      pending.delete(message.id);
      message.error ? rejectCall(new Error(JSON.stringify(message.error))) : resolveCall(message.result);
    }
  };
  return {
    exceptions,
    send(method, params = {}) {
      return new Promise((resolveCall, rejectCall) => {
        const id = ++nextId;
        pending.set(id, { resolveCall, rejectCall });
        socket.send(JSON.stringify({ id, method, params }));
      });
    }
  };
}

async function waitForDevtools(port) {
  for (let i = 0; i < 80; i += 1) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (res.ok) return;
    } catch {
      /* 还没起来 */
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 150));
  }
  throw new Error("DevTools 没起来");
}

/* ---------------- 断言 ---------------- */

const results = [];
const only = process.argv.includes("--filter")
  ? process.argv[process.argv.indexOf("--filter") + 1]
  : null;

function record(name, ok, detail) {
  results.push({ name, ok, detail });
  const mark = ok ? "PASS" : "FAIL";
  console.log(`  [${mark}] ${name}${detail ? " — " + detail : ""}`);
}

async function main() {
  const chromePath = findChrome();
  if (!chromePath) {
    console.error("找不到 Chrome。设置 CHROME_PATH 环境变量，或把 Chrome 装到默认位置。");
    process.exit(2);
  }
  if (!existsSync(join(WEB, "data", "site.json"))) {
    console.error("缺少 web/data/site.json。先跑：python web/build-data.py");
    process.exit(2);
  }

  const { server, port } = await startServer();
  const base = `http://127.0.0.1:${port}/web/`;
  const profile = await mkdtemp(join(tmpdir(), "dbw-check-"));
  const debugPort = 9400 + Math.floor(Math.random() * 400);
  const chrome = spawn(chromePath, [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--hide-scrollbars",
    "--mute-audio",
    `--remote-debugging-port=${debugPort}`,
    `--user-data-dir=${profile}`,
    "about:blank"
  ]);

  const cleanup = async () => {
    try { chrome.kill(); } catch { /* 已经退了 */ }
    server.close();
    await rm(profile, { recursive: true, force: true }).catch(() => {});
  };

  try {
    await waitForDevtools(debugPort);
    const targetRes = await fetch(`http://127.0.0.1:${debugPort}/json/new?about:blank`, { method: "PUT" });
    const target = await targetRes.json();
    const socket = await connect(target.webSocketDebuggerUrl);
    const client = makeClient(socket);
    await client.send("Page.enable");
    await client.send("Runtime.enable");

    const navigate = async (url, settle = 900) => {
      await client.send("Page.navigate", { url });
      await new Promise((resolveWait) => setTimeout(resolveWait, settle));
    };
    const evaluate = async (expression) => {
      const result = await client.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) {
        throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
      }
      return result.result.value;
    };

    const cases = [
      ["概览", async () => {
        await navigate(base);
        const value = await evaluate(`(() => ({
          facts: document.querySelectorAll('#facts dd').length,
          checks: document.querySelectorAll('.board .check').length,
          rows: document.querySelectorAll('#runs-body tr').length,
          nav: document.querySelector('.nav a[aria-current="page"]').textContent.trim()
        }))()`);
        return value.facts === 4 && value.checks === 4 && value.rows > 0 && value.nav === "概览"
          ? [true, `facts=${value.facts} checks=${value.checks} rows=${value.rows}`]
          : [false, JSON.stringify(value)];
      }],

      ["任务详情与时间轴", async () => {
        const id = await evaluate(`(() => window.__DBW_DATA__.tasks[0].id)()`);
        await navigate(`${base}#/tasks/${id}`);
        const value = await evaluate(`(() => ({
          title: document.title,
          dots: document.querySelectorAll('.run-timeline a.tl-dot').length,
          rows: document.querySelectorAll('table.runs tbody tr').length,
          modelRows: document.querySelectorAll('.model-table tbody tr').length,
          focus: document.activeElement.tagName,
          nav: document.querySelector('.nav a[aria-current="page"]').textContent.trim()
        }))()`);
        return value.title.startsWith(id) && value.focus === "H2" && value.nav === "任务" && value.rows > 0
          ? [true, `dots=${value.dots} rows=${value.rows} modelRows=${value.modelRows}`]
          : [false, JSON.stringify(value)];
      }],

      ["运行列表筛选与搜索", async () => {
        await navigate(`${base}#/runs?outcome=rejected`);
        const filtered = await evaluate(`(() => ({
          rows: document.querySelectorAll('#runs-view-body tbody tr').length,
          count: document.querySelector('.runs-count').textContent,
          on: document.querySelector('.filter.is-on').dataset.filter
        }))()`);
        const typed = await evaluate(`(async () => {
          const input = document.querySelector('#run-search');
          input.value = 'kimi';
          input.dispatchEvent(new Event('input', { bubbles: true }));
          await new Promise((r) => setTimeout(r, 350));
          return { hash: location.hash, rows: document.querySelectorAll('#runs-view-body tbody tr').length };
        })()`);
        return filtered.on === "rejected" && typed.hash.includes("q=kimi") && typed.rows > 0
          ? [true, `rejected=${filtered.rows} kimi=${typed.rows}`]
          : [false, JSON.stringify({ filtered, typed })];
      }],

      ["键盘：斜杠与上下键", async () => {
        await navigate(`${base}#/runs`);
        const value = await evaluate(`(() => {
          document.body.dispatchEvent(new KeyboardEvent('keydown', { key: '/', bubbles: true }));
          const searchFocused = document.activeElement.id === 'run-search';
          document.activeElement.blur();
          const links = [...document.querySelectorAll('#app .row-link')];
          links[0].focus();
          links[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
          const moved = document.activeElement === links[1];
          document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
          const jumped = document.activeElement === links[links.length - 1];
          return { searchFocused, moved, jumped };
        })()`);
        return value.searchFocused && value.moved && value.jumped ? [true, "焦点正常"] : [false, JSON.stringify(value)];
      }],

      ["运行详情：闸门与时间线", async () => {
        const runId = await evaluate(`(() => { const run = window.__DBW_DATA__.runs.find((item) => item.gate); return run.run_id; })()`);
        await navigate(`${base}#/runs/${runId}`);
        const value = await evaluate(`(() => ({
          gates: document.querySelectorAll('.gate-line').length,
          items: document.querySelectorAll('.timeline li').length,
          hasGate: !!document.querySelector('.gate-verdict')
        }))()`);
        return value.gates === 4 && value.items > 0 && value.hasGate ? [true, `gates=${value.gates} events=${value.items}`] : [false, JSON.stringify(value)];
      }],

      ["运行对比", async () => {
        const ids = await evaluate(`(() => window.__DBW_DATA__.runs.filter((run) => run.gate).slice(0, 2).map((run) => run.run_id))()`);
        await navigate(`${base}#/runs/compare?a=${ids[0]}&b=${ids[1]}`);
        const value = await evaluate(`(() => ({
          rows: document.querySelectorAll('.compare-table tbody tr').length,
          diffs: document.querySelectorAll('.compare-table tbody tr.is-diff').length
        }))()`);
        return value.rows >= 11 ? [true, `rows=${value.rows} diffs=${value.diffs}`] : [false, JSON.stringify(value)];
      }],

      ["CSV 导出", async () => {
        await navigate(`${base}#/runs?outcome=rejected`);
        const value = await evaluate(`(async () => {
          let captured = null;
          const original = URL.createObjectURL;
          URL.createObjectURL = (blob) => { captured = blob; return 'blob:captured'; };
          document.querySelector('#export-csv').click();
          URL.createObjectURL = original;
          if (!captured) return { error: 'no blob' };
          const text = await captured.text();
          const CRLF = String.fromCharCode(13) + String.fromCharCode(10);
          const lines = text.split(CRLF).filter(Boolean);
          return {
            header: lines[0].split(',').length,
            lines: lines.length,
            rows: document.querySelectorAll('#runs-view-body tbody tr').length
          };
        })()`);
        return value.header === 17 && value.lines === value.rows + 1
          ? [true, `header=${value.header} lines=${value.lines}`]
          : [false, JSON.stringify(value)];
      }],

      ["补丁可读（有则验证）", async () => {
        const run = await evaluate(`(() => {
          const item = window.__DBW_DATA__.runs.find((entry) => entry.patch && entry.patch.available);
          return item ? { id: item.run_id, file: item.patch.files[0] ? item.patch.files[0].path : null } : null;
        })()`);
        if (!run) return [true, "快照里还没有补丁文件，跳过"];
        await navigate(`${base}#/runs/${run.id}`);
        const value = await evaluate(`(() => ({
          files: document.querySelectorAll('.patch-file').length,
          name: document.querySelector('.patch-file summary') ? document.querySelector('.patch-file summary').textContent.trim() : null,
          rationale: document.querySelector('.patch-rationale') ? document.querySelector('.patch-rationale').textContent.trim().length : 0
        }))()`);
        return value.files > 0 && value.name && value.name.indexOf(run.file) !== -1
          ? [true, `files=${value.files} rationale=${value.rationale}`]
          : [false, JSON.stringify(value)];
      }],

      ["数据构建：新字段生效与降级补丁", async () => {
        const tmp = await mkdtemp(join(tmpdir(), "dbw-build-"));
        const taskDir = join(tmp, "tasks", "tabulate-366");
        await mkdir(taskDir, { recursive: true });
        await mkdir(join(tmp, "runs", "patches"), { recursive: true });
        for (const name of ["task.json", "contract.yaml"]) {
          await cp(join(ROOT, "tasks", "tabulate-366", name), join(taskDir, name));
        }

        const missingRun = "20260101T000000-aaaaaa";
        const patchedRun = "20260101T000100-bbbbbb";
        const events = [
          { ts: "2026-01-01T00:00:00+08:00", run_id: missingRun, event: "run_start", task_id: "tabulate-366", provider: "deepseek", model: "deepseek-chat" },
          { ts: "2026-01-01T00:00:02+08:00", run_id: missingRun, event: "run_end", outcome: "accepted", tokens: 555, calls: 3, duration_ms: 1234, patch_path: "runs/patches/" + missingRun + ".json" },
          { ts: "2026-01-01T00:01:00+08:00", run_id: patchedRun, event: "run_start", task_id: "tabulate-366", provider: "deepseek", model: "deepseek-chat" },
          { ts: "2026-01-01T00:01:00+08:00", run_id: patchedRun, event: "run_end", outcome: "accepted", tokens: 1, calls: 1, duration_ms: 2, patch_path: "runs/patches/" + patchedRun + ".json" }
        ];
        await writeFile(join(tmp, "runs", "audit.jsonl"), events.map((item) => JSON.stringify(item)).join("\n") + "\n", "utf8");
        await writeFile(
          join(tmp, "runs", "patches", patchedRun + ".json"),
          JSON.stringify({ run_id: patchedRun, files: { "big.py": "x".repeat(40000) }, rationale: "合成补丁" }),
          "utf8"
        );

        const build = await runPython(["web/build-data.py", "--root", tmp, "--out", join(tmp, "web", "data")]);
        if (build.code !== 0) {
          await rm(tmp, { recursive: true, force: true });
          return [false, "build-data 失败: " + String(build.stderr).slice(0, 160)];
        }
        const data = JSON.parse(await readFile(join(tmp, "web", "data", "site.json"), "utf8"));
        const missing = data.runs.find((run) => run.run_id === missingRun) || {};
        const patched = data.runs.find((run) => run.run_id === patchedRun) || {};

        const checks = {
          duration_ms: missing.duration_ms === 1234,
          tokens: missing.tokens === 555,
          calls: missing.calls === 3,
          task_id: missing.task_id === "tabulate-366",
          degraded: Boolean(missing.patch) && missing.patch.available === false,
          degradedPath: Boolean(missing.patch) && String(missing.patch.path).indexOf(missingRun) !== -1,
          readable: Boolean(patched.patch) && patched.patch.available === true,
          truncated: Boolean(patched.patch) && patched.patch.truncated === true &&
            patched.patch.files[0].content.length === 30000
        };
        await rm(tmp, { recursive: true, force: true });

        const bad = Object.keys(checks).filter((key) => !checks[key]);
        return bad.length ? [false, "失败项: " + bad.join(", ")] : [true, Object.keys(checks).length + " 项断言"];
      }],

      ["缺失补丁文件不崩", async () => {
        await navigate(base + "__fixture?case=missing-patch");
        const runId = await evaluate(`(() => window.__DBW_DATA__.__fixture.run_id)()`);
        await navigate(`${base}__fixture?case=missing-patch#/runs/${runId}`);
        const value = await evaluate(`(() => ({
          note: document.querySelector('.patch-missing') ? document.querySelector('.patch-missing').textContent.trim() : null,
          heading: document.querySelector('h2') ? document.querySelector('h2').textContent.trim() : null
        }))()`);
        return Boolean(value.note) && value.note.includes("runs/patches/")
          ? [true, value.note.slice(0, 48)]
          : [false, JSON.stringify(value)];
      }],

      ["打印媒体", async () => {
        await client.send("Emulation.setEmulatedMedia", { media: "print" });
        const hidden = await evaluate(`(() => {
          const topbar = document.querySelector('.topbar');
          return topbar ? getComputedStyle(topbar).display === 'none' : false;
        })()`);
        await client.send("Emulation.setEmulatedMedia", { media: "" });
        return hidden ? [true, "顶栏隐藏"] : [false, "打印时顶栏仍在"];
      }]
    ];

    for (const [name, run] of cases) {
      if (only && !name.includes(only)) continue;
      try {
        const [ok, detail] = await run();
        record(name, ok, detail);
      } catch (error) {
        record(name, false, String(error.message || error));
      }
    }

    if (client.exceptions.length) {
      record("无未捕获异常", false, client.exceptions.join(" | "));
    } else {
      record("无未捕获异常", true, "0 条");
    }
  } finally {
    await cleanup();
  }

  const failed = results.filter((item) => !item.ok);
  console.log(`\n${results.length - failed.length}/${results.length} 通过`);
  process.exit(failed.length ? 1 : 0);
}

main().catch(async (error) => {
  console.error("check 崩了：", error);
  process.exit(1);
});
