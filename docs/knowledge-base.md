# 今日知识总结（jev-ui-test）

> 本文件把今天（2026-10-25）围绕 jev-ui-test 讨论过的知识点汇总成一份速查。
> 内容来源：会话中的问答与实测，原始证据分散在 AGENTS.md、docs/、报告与代码注释里。

## 目录

1. [一句话定位](#1-一句话定位)
2. [一条用例的完整执行流程](#2-一条用例的完整执行流程)
3. [核心纪律：动作不重试、决策可重发](#3-核心纪律动作不重试决策可重发)
4. [决策作废：四种现场](#4-决策作废四种现场)
5. [遮挡判断](#5-遮挡判断)
6. [snapshot.js：七道工序 + 三份指纹](#6-snapshotjs七道工序--三份指纹)
7. [步数预算：四道闸](#7-步数预算四道闸)
8. [模块调用关系](#8-模块调用关系)
9. [inspector（demo.py）](#9-inspectordemopy)
10. [实测数据](#10-实测数据)
11. [用例是怎么生成的](#11-用例是怎么生成的)
12. [产研全流程怎么测](#12-产研全流程怎么测)
13. [需求/功能说明如何标准化](#13-需求功能说明如何标准化)
14. [环境与故障排查](#14-环境与故障排查)
15. [agent.py 设计精华](#15-agentpy-设计精华)

---

## 1. 一句话定位

- **输入是一个自然语言目标**，站点专用步骤计划与写死字段是**用例**（`cases/*.yaml`）的事，不在策略里。
- 一次请求同时选出**操作 + 目标**，只消费被选中操作的那一个目标分支。
- 目标必须落在**已观测元素 + 受支持操作**上；模型绝不产出选择器或可执行代码。
- `TYPE_TEXT` 才调文本模型；截图可选、模型不消费；独立验证最终结果（模型选 `DONE` 不等于成功）。

## 2. 一条用例的完整执行流程

8 个阶段，真正花钱的只有第 4 阶段（决策）：

```
收集期 ─▶ 起跑 ─▶ 前置 ─▶ 开浏览器 ─▶ 主循环(决策/执行 ×N) ─▶ 收尾 ─▶ 断言 ─▶ 报告
  免费                          ★ 每次决策一次 API 调用
```

| 阶段 | 关键点 |
|---|---|
| 收集期 | `pytest_generate_tests` 扫 YAML、变量替换、缺变量打 `_skip`（带原因）、id 用 `case["id"]` |
| 起跑 | `allure.dynamic.*` 元数据 + `attach_environment` |
| 前置 | 起浏览器**之前**注入 cookie（一条用例一个身份）；`setup:` 走接口备前置 |
| 开浏览器 | `Browser` 连 CDP、注入 cookie、导航、首屏稳定等待、`observe()` 存首屏 |
| 主循环 | 决策（`predict`）→ 附件 → 执行（`act`）→ 三道校验 → 执行后观察 |
| 收尾 | `_settle_before_verdict` → 停录屏（必须在 close 之前）→ `close` → 清帧目录 |
| 断言 | 只对**终局页面**求值；`check` 出证据、`combine` 出判定、`assert` 在最后 |
| 报告 | 失败重跑、清理（判定之后）、`allure generate` + 装时间轴插件 |

## 3. 核心纪律：动作不重试、决策可重发

**判据只有一条：这个操作有没有可能已经改变过外部世界。**

| 重来的东西 | 有副作用 | 能否重来 | 留痕字段 |
|---|---|---|---|
| HTTP 请求（连接失败/超时/429） | 无 | ✅ 3 次 | `transport_attempts` |
| 决策（响应不合法） | 无 | ✅ 3 次 | `decision_attempts` |
| 页面观察（`Runtime.evaluate`） | 无 | ✅ 重试 | 等待记录 |
| **点击/输入/选择** | **有** | ❌ **绝不** | — |
| 整条用例（pytest 级） | 有，但换干净起点 | ⚠️ 有条件（`--reruns`） | 报告显示 rerun |

三个概念别混：

| 概念 | 发生在 | 判据 | 处理 |
|---|---|---|---|
| **重发** | 决策请求后、动作前 | 响应不合法 | 重发 ≤3 次 |
| **作废** | 动作执行那一刻 | 新鲜度/遮挡/没值 | 丢弃决策，重新观察 |
| **白做** | 动作执行后 | 页面语义未变 | 记 history + 记 discards |

关键代码事实：

- `agent.py:108` `state["decision"] = None` —— **先把决策消费掉再动手**，重试不可能双击。
- 用例级重跑安全的前提：前置数据用唯一名（`jev-wf_<run_id>_<case_id>`）+ 清理只认两种标题形态。
- 一次 47s 的决策 = 客户端 25s 超时 + 一次传输层重发（`25 + 0.5 + ≈22`），靠「传输尝试」字段归因。

## 4. 决策作废：四种现场

**作废 = 模型选好了，但动手那一刻被拒，浏览器一行没执行。** 报告特征：有「执行」步骤但无
`执行请求`/`执行返回` 附件，只有参数 `备注 = 页面已过期，本次执行被丢弃（动作未落地）`。

| 现场 | 触发 | 处置 |
|---|---|---|
| 页面在决策→执行之间变了 | `fingerprint` 不一致 → `StalePage` | 重观察，不牵连目标 |
| 目标不可用（遮挡/移除/禁用） | 命中测试没过 → `TargetUnavailable` | `target_level=True`，够 3 次剔除该目标 |
| 文本模型说没值 | `field_text` 返回 `{"text": null}` → `NoTextValue` | 作废但没写入，重来安全 |
| 想收工但页面变了 | `DONE/BLOCKED` 且 `!fresh` | 「完成」作废，回去重选 |

真实案例（`新建流程并进入流转设置界面`）：第 3 步点开「路径类型」下拉成功，第 4/5/6 步连续选同一目标
被拒（`Target changed or is covered. Observe again.`），第 8 步候选表里该目标 0/83 已被扣下。

作废后的三层反馈：① 进 `discards`（不进 history）；② 达阈值从候选表扣掉（`REFUSED_TARGET_THRESHOLD=3`）；
③ 提示词声明「被扣的目标不会再出现，用提供的其它选项」。

## 5. 遮挡判断

**`covered` 是观测时由 `snapshot.js` 的 `geometry()` 算好带回的，`_drop_covered()` 只读标记做减法。**

核心判据：**候选点上最顶层元素是不是我（或我的后代）**：

```js
covered: !e.contains(document.elementFromPoint(x, y))
```

四个要点：

1. 用 `contains` 不是 `===`（可点区域常是子 `<span>`）；
2. 逐个**客户矩形中心**试（换行元素包围盒中心会落在行间空隙）；
3. iframe 要过两道（内层命中自己 + 外层命中这个 frame）；
4. 只标注不剔除（执行前会 `scrollIntoView` 再测一次）。

`_drop_covered` 的减法规则：

- 按**操作类型**判：某类还有未遮挡的，才剔该类里遮挡的；
- 某类全遮挡 / 全候选都遮挡 → **一个不剔**（让模型还有得选）；
- `scroll`/`wait` 等无 `node` 的伪动作永远保留。

| | `_refused_targets` | `_drop_covered` |
|---|---|---|
| 证据 | 事后（浏览器拒过 3 次） | 事前（同次观察的几何） |
| 生效时机 | 攒到 3 次才剔 | 立即 |
| 作用 | 剔「确实点不了的」 | 剔「看起来点不了的」 |

三个易混概念：**不可见**（不进候选）／**被遮挡**（进候选标 `covered`）／**不可用**（disabled，跳过）。
`covered` 会进候选给模型看，但**不进指纹**（它是几何派生量，不代表语义变化）。

## 6. snapshot.js：七道工序 + 三份指纹

`snapshot.js` 是 IIFE，被 `Runtime.evaluate` 整份注入执行一次，返回「这一帧页面」。
候选表上限 250 条，正文截 6000 字，iframe 下钻深度 3 层。

七道工序：① 建身份+清死节点（`window.__jevFast` 跨调用复用 node id）→ ② 扫同源 frame 建坐标换算 →
③ `geometry()` 算点哪儿/被没被压 → ④ `name()` 九层取名（含 `rowLabel`）→ ⑤ 主循环 `collect()`
（`selector` + safe/visible/disabled/role/geometry）→ ⑥ 三遍补充（叶子自定义按钮、可点整行、iframe 富文本）→
⑦ 抽正文（TreeWalker，视口内，6000 字）→ ⑧ 合成 `scroll_up/down`+`wait` 伪动作、编号 `e1..eN`、截断。

三份指纹：

| 指纹 | 构成 | 判什么 |
|---|---|---|
| `marker` | 标题+正文+**动作语义**（剥 rect/covered） | 整页变没变 |
| `page_key` | 所有表单控件 value/checked/selectedIndex（含 iframe） | 表单变没变 |
| `guards[node]` | id+role+name+value+disabled+aria+所在行文字 | 这个元素变没变 |

它**不做**：不点东西、不 dispatchEvent、不判该做什么、不发网络、不读密码框、不消费截图。

## 7. 步数预算：四道闸

模型不会自动停，四道闸拦四种失控方式：

| 闸 | 判据 | 拦住 |
|---|---|---|
| 动作预算 | `len(history) >= max_steps` | 一直点、页面一直变、到不了目标 |
| 决策预算 | `len(decisions) >= max_steps * 2` | 一直想、从来没落地（全被作废） |
| 墙钟超时 | `now - started > timeout_s` | 每步很慢（重页面/模型慢） |
| 连续无变化 | 最近 3 条 `page_changed` 全 False 且非 wait | 点了没反应的空转 |

- 决策预算 = 动作预算 ×2，因为一次有效动作平均花 1.5–2 次决策；它同时是**成本上限**。
- 触顶两种形态：库里抛异常 = broken；框架那道墙 = 优雅停止 + 备注。
- **预算只负责停下来，不负责判对错**；`blocked` ≠ 失败，`done` ≠ 成功，对错永远由终局断言决定。
- ⚠️ 实测坑：`SCROLL` 永远 `page_changed=True`（`scrollY` 在 marker 里），所以「连续无变化」这道闸
  拦不住"滚来滚去"的空转——Google Flights 那轮 42 次滚动就是这么耗光 60 步的。

## 8. 模块调用关系

```
入口：demo.py（inspector）  |  tests/conftest.py + test_nl_cases.py（pytest）
              │                              │
              │                    framework/loader → runner（编排层）→ reporting/video/encode
              │                              │        └→ assertions + judge → model
              └────────────┬─────────────────┘
                           ▼
              库本体：agent.py → browser.py / model.py / questions.py
                       browser.py → snapshot.js（注入执行）
```

依赖纪律（全部单向、无环）：

- **库本体不 import framework、不 import allure**（`agent/browser/model` 里 0 处）。
- **browser 与 model 互不认识**，`agent.py` 是唯一同时持有两者者。
- framework 一个共同底座 `config.py`；唯二「回头看」`model` 的是 `judge`（复用决策端点做 noul）
  和 `reporting`（只为 `decision_endpoint` 写进环境附件）。
- E9 子树（`e9_config/login/api/hrm/workflow/setup/cleanup`）可整棵拔掉，`demo.py` 一条不碰。
- `report_plugin` 不在运行期链路上，只有 `scripts/install_report_plugin.py` 在 `allure generate` 之后调。

## 9. inspector（demo.py）

三个状态按钮就是流水线三步：

```
Start demo        idle → ready        看一帧（不花钱、不决策）
Choose next       ready → predicted   问模型（★ 花钱）
Execute choice    predicted → ready   做一次（真碰浏览器）
```

**Start demo = `reset` = `close_browser()` + `new Agent()` + `observe()`**，一分钱不花。它把「观测」单独
拿出来给人看，把「决策」留给按钮。`Run automatically` 内部仍是 `predict → act` 循环（勾 Slow motion 插 450ms）。

会慢/会失败的地方：`wait_until_stable(timeout=60)` 首屏等待最慢；`DevToolsActivePort not found` 其实是
Chrome 窗口被关（极具误导性）；`A browser step is already running`（409）是自动循环还在跑时又点了 Start。

## 10. 实测数据

### 三个 demo 场景（同一套代码、同一个模型）

| | Google Flights | Travel planner | Reading room |
|---|---|---|---|
| 动作 | 60（撞预算） | 4 | 1 |
| 决策 | 64 | 5 | 2 |
| 作废 | 3 | 0 | 0 |
| 输入 token | 334,570 | 17,882 | 2,130 |
| 文本模型 | 2 次 | 1 次 | 0 次 |
| 结果 | ❌ blocked | ⚠️ done（Lisbon 靠运气） | ✅ done（干净） |
| 失败原因 | Google 送到营销页，目标不可达 | 漏点「Find stays」，条件没生效 | — |

结论：**差别不在模型，在目标在当前页面是否可达**。

### 模型与决策（TypeSafe）

- 服务端版本 `jev-1.14.0`，请求里 `"model": "jev-latest"`；端点默认 `https://api.typesafe.ai/v1/systemone`。
- 决策中位 274ms（最长 935ms）；Google 那轮 64 次决策全部 `decision_attempts=1`、`transport_attempts=1`（零重发）。
- 一次决策请求约 2k–4.6k 输入 token；结构：`questions{operation, click_target, type_text_target}` +
  `state{elements, page, recent_actions, recent_discarded_attempts}`；`type_text_target` 在无填控件时整段消失。
- 断言阈值默认 `DEFAULT_AI_THRESHOLD=0.75`；预算 `DEFAULT_MAX_STEPS=60`、`DEFAULT_MAX_DECISIONS=120`、`DEFAULT_TIMEOUT_S=300`。

### 措辞量出来的 noul（别调阈值，改措辞）

| 措辞 | 实测 |
|---|---|
| `「流转设置」是当前选中的那个页签` | 0.10 |
| `当前页面是该流程的流转设置界面` | 0.24 |
| `页面显示的是「UI自动化A」这个流程的流转设置界面` | 0.40 |
| `页面上有「图形编辑」页签` / `「编辑」按钮` | 0.98 / 0.97 |
| 否定式表述 | ~0.47 |
| 复合句（两件事「并且」） | 0.54 |
| workflow_add 断言：旧「页面身份」措辞 → 新「新建流程字样+一条流程名称」 | 0.590 → **0.970**（否页 0.040） |

## 11. 用例是怎么生成的

五步（`nl-case-author` + `e9-graph-query`）：

```
1. 读源材料     xlsx/xmind/docx/csv → 纯文本（read_source.py 写 artifacts/case-source/）
2. 查 E9 图谱   路由表/按钮动作/URL 拼接 → 落到 url / goal / expect
3. 写 YAML      编号→id、名称→name、步骤→goal、预期→expect、前置→login+setup
4. 量断言       正页 + 否页对照（0.97 / 0.04 才算好）
5. 三关校验     load_cases() / pytest -q / ruff check
```

- 字段映射是**机械**的；唯一要「翻译」的是步骤+完成判据 → `goal`（写意图不写坐标、点到元素名、写清何时停）。
- **变量是唯一自动生成的部分**：`run_id`（一次收集一个）→ `request_name_for(run_id, case_id)` 逐用例唯一；
  同一值挂在 `{{ wf_request_name }}`（goal 用）和 `case["_wf_request_name"]`（fixture 用）。
- 图谱**给不了 UI 元素定位**（无 DOM/id/onclick），元素靠 `snapshot.js` 现场观测。
- 实测坑：「保存」≠「提交」（`src:"save"` vs `src:"submit"`）；门户壳 `index.html` ≠ 引擎 `engine.html`。
- 14 条现用例 = 2 demo + 12 e9（`workflow_add` 1 + `workflow_design` 3 + `workflow_submit` 8）。

## 12. 产研全流程怎么测

分层定位：NL UI 用例是 L2（最贵最慢最脆），L0 单元 / L1 接口 / L3 探索性（inspector）各司其职。
**四条全中才上 L2**：高频回归、路径稳定、有确定性观测点、数据可回收。

六阶段：需求澄清（问清验收标准/操作者/中间状态/数据回收/观测点/失败代价）→ 可测性评审（退回三种：
无可观测判据、判据看不见、断的是页面身份）→ 测试设计（等价类/边界值/状态迁移/错误推测）→ 数据与环境
（造数唯一名、一条用例一个身份、回收只认两种标题）→ 自动化落地（五步）→ 门禁与回归（提交时离线/
每日接口子集/发版全量/线上灰度）。

三条纪律：只允许用例级重跑；`reruns` 按预期 pytest 成败定不按负向标签定；生产风险分级上线
（只读 → 草稿 → 全链路真提交）。

## 13. 需求/功能说明如何标准化

落成文档 [requirement-template.md](requirement-template.md)。三条铁律：

1. 验收标准必须描述「用户在屏幕上看见什么」；
2. 一条用例一个操作者（跨人拆成接口前置 + 单人 UI）；
3. 每造一条数据都能说清谁清、怎么清、失败留什么。

十节模板 → YAML 字段映射；验收标准「三态」（URL / 文本 / 元素值）；禁止写法（"正常流转"、"提示弹出来了"、
"当前在 XX 页"）；八项评审清单。

## 14. 环境与故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| TypeSafe 401 | 进程持有旧 key（`demo.py` 用 `setdefault` 只在启动读一次 `.env`） | 重启 inspector |
| TypeSafe 451 | 直连区域限制 | 给进程加 `HTTPS_PROXY=http://127.0.0.1:7897 HTTP_PROXY=…`（端口 7897 不是 7890） |
| 改 `.env` 不生效 | 同上，`setdefault` 不覆盖 | 先 `unset` 已 export 的旧值，再重启 |
| `DevToolsActivePort not found` | Chrome 窗口被关 | 重开 Chrome（独立 profile） |
| 决策几十秒 | 客户端 25s 超时 + 一次传输层重试 | 看报告「传输尝试」>1 |
| 跑 E9 时内网不通 | python 进程带代理后访问 `10.x`/`192.168.x` 被代理 | 显式 `NO_PROXY=<E9 主机>` |

git 相关：本仓库是**多 worktree**（主工作树 `/Users/wb/Documents/AI/jev-ui-test` 占用 `main`，本会话在
`misty-planet` 的 detached HEAD）。在 detached 上提交后，用
`git push origin HEAD:refs/heads/main` 推送（裸 `HEAD:main` 会被拒，要写全 ref）。

---

## 15. agent.py 设计精华

> 这一节是对 `jev_ultrafast/agent.py`（279 行）的逐条拆解。**先纠三个最容易读错的地方，
> 再说六条真正的精华**——因为这三处读错，精华就看不见了。

### 15.1 三个容易读错的地方

**① `history` 不是"成功才记"。**

```python
# agent.py:187-188
# Record execution before observing. A stale post-action observation must not erase the action.
state["history"].append({... "page_changed": None, ...})

state["page"] = state["browser"].observe(...)            # :222
changed = state["page"]["fingerprint"] != page["fingerprint"]
state["history"][-1].update(page_changed=changed, ...)   # :225  事后回填
```

`history.append` **无条件执行，且发生在 observe 之前**。「点了但页面没变」照样进 history，
同时**另外**进一份 `discards`（`:242`，`reason: "Executed, but nothing on the page changed."`）。

**② `DONE` / `BLOCKED` 不是直接标记。**

```python
# agent.py:110-112
if selected in {"DONE", "BLOCKED"}:
    if not state["browser"].fresh(page):
        state["status"] = "ready"
        raise StalePage("Page changed since the decision. Choose again.")
```

「我看到完成了」这个判断，也必须基于一版**没过期**的页面——这是防 agent 提前收工的最后一道闸。

**③ `tick` 不只是串联 `predict + act`。**

```python
# agent.py:98-107
except StalePage:
    state["decision"] = None
    state["status"] = "ready"
    state["page"] = state["browser"].observe(...)
```

它捕获 `StalePage`（含子类 `TargetUnavailable`）后**重新观察并继续**，是库内唯一的自愈路径；
但 `NoTextValue` 不在捕获范围内，会抛给上层。

另外：「录屏」这个词要收一收——`agent.py` 只做**按 `elapsed_ms` 命名落帧**（`:254-257`），
编码成 mp4 是 `framework/encode.py` 的事；`screenshots` 控制的是 `observe()` 要不要截图。

### 15.2 六条精华

**精华 1 · 一条判据贯穿全篇：有没有可能已经改变世界**

```python
# agent.py:142
# 两类都进这个分支，共同点是**没有任何东西被执行过**（这才是"可以重来"的判据）
```

| 情形 | 世界变了吗 | 处置 |
|---|---|---|
| `StalePage`：被新鲜度校验挡下 | 没有 | 作废，重观察 |
| `NoTextValue`：文本生成就失败 | 没有（在 `browser.act` 之前） | 作废，重选 |
| 点击本身 | **可能已经变了** | **绝不重试** |

这条判据同时解释了为什么 `history` 与 `discards` 必须分开——它们不是「日志的两种格式」，
是「世界变没变」的两种结论。

**精华 2 · 先记账、再观察**

顺序反过来（先 observe 再 append），act 之后的观察一旦抛错，这条**已经发生的动作就凭空消失**——
报告里看不到、模型也看不到，于是下一轮它会再去点一次同一个按钮。

> 收益不在顺利时，在**出错那一刻**：动作是真的，观察是可能失败的，两者不能互相抵押。

**精华 3 · `history` 与 `discards` 是两个语义通道，绝不能混**

```python
# agent.py:152-155
# 为什么用【独立列表】而不是塞进 history：history 同时被当"成功计数"用在两处——
# 步数预算（len(history) >= max_steps）与"最近三条 page_changed 都是 False 就判 blocked"。
# 把丢弃混进去，会让空转自动吃满步数预算，还会污染那个连续无变化的判定。
```

一句话：**`history` 是「我做过的」，`discards` 是「我试过但没发生／白费的」。**
混进 `history` 会有两个后果：被拒的尝试**消耗步数预算**；连续三条作废会**误判 blocked**。

分开之后，`discards` 才能以聚合形式喂回模型（`recent_discarded_attempts`），
让模型知道「这条路我试过了」。**这条设计的由来是实测的 87 步空转**（`:150`）。

**精华 4 · `discards` 不只是日志，它携带「处置判据」**

| 字段 | 解决什么 |
|---|---|
| `target_level` | 只有「目标本身不可用」才够次数剔除；**「页面刚好动了」是瞬时的**，重选无妨 |
| `node` + `document` | node id 换文档（导航）后**从 1 重发**，单靠 node 跨文档剔会**误杀新页面的无辜元素** |
| `missing`（`NoTextValue`） | `True` 是「这个字段没值」→ 该剔；`False` 是协议故障 → **与字段无关，不能牵连** |
| `reason` | 原样保留异常文本，报告里直接读到「为什么」 |

```python
# agent.py:180-181
"target_level": isinstance(error, TargetUnavailable)
                or bool(getattr(error, "missing", False)),
```

**「拒绝」和「重试」是两件相反的事，但都比「假装没发生」好。**

**精华 5 · 记录路径永不抛异常**

同一个文件里出现两次同样的克制：

```python
state.setdefault("discards", []).append(...)   # :243  不是 []
"node": action.get("node"),                     # :211  不是 []
```

`:160` 与 `:206` 都写了理由：

> 这里是【记录】路径，而且正处在异常里 / 已经在执行【之后】才跑。在这里抛 KeyError，
> 会把「页面已变」这个真实信号换成一个与之无关的报错——**本仓库最防的那类错报**。

这不是防御性编程的洁癖，是**故障归因的卫生**：出问题时你看到的必须是真问题。

**精华 6 · 决策是一次性消耗品**

```python
# agent.py:108
# Consume once, before any mutation or model call. A retry cannot double-click.
state["decision"] = None
```

在**任何 mutation 或模型调用之前**消费掉。于是就算上层再调一次 `act`，也只会报
`"Observe and choose before acting"`——**物理上没有决策可执行了**。
配合 `:117` 的指纹比对，一次决策只能匹配一帧页面。

**附赠 · 作废时故意不清 `pending_text`**

```python
# agent.py:157-158
# pending_text 刻意【不清】：过期重试复用已生成文本靠的就是它，
# 而清空会让下一次重试白花钱再问一遍文本模型。
```

这是「只有整份助手输入完全一致才复用」的落地：**作废不该把已经花掉的钱也一起作废**。

### 15.3 删掉任一条会坏成什么样

| 删掉 | 后果 |
|---|---|
| history 先记账再观察 | 观察抛错 → 动作凭空消失 → 下一轮重点一次（可能重复提交） |
| history / discards 分开 | 空转自动吃满步数预算 + 误判 blocked |
| `target_level` | 页面瞬时变动被当成「目标坏了」，把好目标剔掉 |
| `node` + `document` | 导航后按 node 剔 → 误杀新页面的无辜元素 |
| `setdefault` / `.get()` | 真实故障被 KeyError 顶替，排查方向全错 |
| 先消费 decision | 同一决策被重复执行 = 双击 / 重复提交 |
| `DONE` 前查 freshness | agent 在页面变了的瞬间宣布完成（假成功） |

---

## 附录：一句话速查

- **动作不重试、决策可重发**：问可以问三遍，做只能做一次；判据是"有没有可能已改变世界"。
- **作废 ≠ 白做**：作废是"这次不算"（不进 history），白做是"这次算但没用"（进 history + discards）。
- **遮挡 = 坐标点上压着别人**：`elementFromPoint` 在客户矩形中心问"你是不是我"，iframe 问两道。
- **预算只管停、不管对错**：`blocked` ≠ 失败，`done` ≠ 成功，对错由终局断言决定。
- **断言先量再写**：光量正页不够，还要量"不该通过的那页"能不能判不过。
- **start demo 不花钱**：它只是看一帧；花钱在 `Choose next`。
- **差别在页面，不在模型**：同一套代码，2,130 token（可达）到 334,570 token（不可达），差 157 倍。
