# 11/25/14 · 换上强模型后，那条 0/3 任务被一次做出——结论要改

**日期**：2026-11-25（Asia/Shanghai）
**任务**：`tasks/moreitertools-1248`（`deepseek-chat` 单轮 0/3）
**脚本**：`scripts/run_once.py --provider ark --timeout 420`

## 目的

11/25/13 的结论是“56 行 4 处的修复超出模型能力”。这是一个**只对一个模型成立**的结论，
必须用更强的模型验证，否则写进论文就是错的。

## 结果

| 模型 | 结果 | 改动 | 说明 |
|---|---|---|---|
| `deepseek-chat`（此前） | ❌ 0/3 | 19 行 | 改对一半 |
| **`doubao-seed-2-1-pro-260915`** | ✅ **1/1 通过** | **63 行** | 四闸门全绿 |
| `kimi-k3-1-pro-261024` | ❌ 提案不合法 | — | JSON 被输出预算截断，不是能力问题 |

doubao pro 的闸门报告：

```
[PASS] tests  全部通过；通过 2
[PASS] lint   问题总数 0（新增 0）
[PASS] api    契约未冻结接口
[PASS] diff   改动 1 个文件 / 63 行（上限 3 / 120）
```

## 结论修正

**1. 难度不是任务的绝对属性，是「任务 × 模型」的属性。**

同一条任务：`deepseek-chat` 三次全败，`doubao-seed-2-1-pro` 一次做成。
所以任务集里的“易/中/难”必须绑定模型才有意义，论文里要写成
“对 deepseek-chat 属难档”，不能写成“难档任务”。

**2. 11/25/13 的“三种流程都失败”要加限定。**

那句话对 `deepseek-chat` 成立。但 doubao pro 用**最朴素的一条路径**（单轮）就过了，
说明当时失败的不是流程设计，是模型能力。

**3. 反过来，这是个支持闸门的证据。**

同一条任务、四个模型/流程，闸门给出的判定全部正确：
失败的三次都被判为不可交付（且区分了“测试红”与“提案不合法”），
成功的一次被判为可交付。**没有一个误判。**

## kimi 的失败要单独说明

它产出的 JSON 是完整的 `edits` 结构，但被截断在字符串中间：

```
无法解析为 JSON，前 200 字符：'{"edits":[{"path":"more_itertools/more.py","search":"    def __contains__(self, elem):…
```

`kimi-k3-1-pro` 的输出预算是瓶颈（60k 输入的补丁生成），不是它不会做。
下次要用 `--max-tokens` 加大后重试；这也印证了 `docs/接口协议.md` 里对 kimi 记录的限制。

## 对论文的影响

第 6 章的“难度分层”必须重写为：

| 任务 | deepseek-chat | doubao-seed-2-1-pro |
|---|---|---|
| moreitertools-1248 | 0/3 | **1/1** |

并且要说明：**“协作有没有用”这个问题，必须在同一模型下比较才成立**——
此前 2 人协议的对照组是同为 deepseek 系的两个模型，这个前提是站得住的。

## 局限

1. doubao pro 只跑了 1 次，不能给出通过率，只能说“能做成”；
2. kimi 因输出截断未得到有效结果，需加大预算重跑；
3. 未测 `deepseek-v4-pro`（该型号名在 Ark 返回 404，可能已下线）。

## 复现

```powershell
python scripts/test_connection.py --provider ark --models doubao-seed-2-1-pro-260915
python scripts/prepare_task.py --task tasks/moreitertools-1248
python scripts/run_once.py --task tasks/moreitertools-1248 --provider ark --model doubao-seed-2-1-pro-260915 --timeout 420
```
