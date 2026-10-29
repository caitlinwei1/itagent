# Acme IT Support Agent

一个 AI IT 支持 agent，用来替代"提 ticket 然后等"。员工用自己的话描述问题；agent 在公司的各个系统里调查，修复它被允许修复的问题，其余的交给对应的人工团队，并附上完整的交接，让员工不必把同样的事再说一遍。

所有东西都在本地运行，对接一家模拟公司 "Acme"：15 个服务、23 篇知识库文章、13 名员工、incident、变更记录、22 张历史 ticket 和一份 policy 文件。

## 1. 问题和范围

**用户：** 遇到 IT 问题的员工，不是 helpdesk 人员。他们知道症状（"Okta 登不进去"），不知道原因。

**为什么做这个问题：** 大多数 IT ticket 是重复的、有文档的（账号锁定、VPN、权限申请、已知故障），但每一张都要排队，等一个人去查同样的记录。agent 一分钟就能做完这些调查。

**交互方式：** 聊天。网页 UI 左边是对话，右边是每一次 tool 调用、推理摘要、case 记录和交接 ticket；另附一个终端 CLI。agent 本身只有一个方法 `respond(message)`，接入 Slack 或 Teams 不需要改它。

**成功的样子：** 几分钟之内，问题修好了，或者员工清楚知道接下来会发生什么，而且不用填任何表。已知故障：一条回复里就知道不是自己的问题、workaround 和 ETA。转人工：拿到 ticket 编号，知道谁会联系自己、什么时候，并且不用再复述一遍。

| 领域 | agent 自己解决 | 交给人工 |
|---|---|---|
| 登录（Okta） | 账号锁定：从登录日志找到原因，验证身份后解锁，说明怎样避免再次被锁 | MFA 重置、账号停用 |
| 应用（Salesforce 等） | 已知 incident：记录显示员工在影响范围内时，把员工加入 incident，给出 workaround 和 ETA；有 KB 解法的单人问题 | 检查后原因仍不明 |
| VPN | 旧客户端的 bug：用连接日志和已安装版本确认，征得同意后推送批准的版本 | 网络侧的问题 |
| 权限 | 自动批准的权限当场开通；需要审批的提交给正确的审批人 | 不能申请的权限（生产库写权限） |
| 数据 pipeline | 诊断：把失败和维护窗口里的变更联系起来 | 修复（防火墙、重跑），附上证据 |
| 安全 | 立即的安全步骤 | 钓鱼、设备丢失：一律以 P1 转给 Security Operations |

## 2. 为什么需要 agent

同一个症状，对不同的人原因不同，要同时看好几个数据源才能分清：

- "我重置了密码还是登不进去"：对这位员工来说，是 iPhone 上的 Outlook 还在用旧密码重试，把账号又锁了。只有登录日志能看出来；FAQ 搜索只会返回她已经试过的密码重置文章。
- "Salesforce 很慢"：对 Chicago 办公室的人是已知 incident，对 New York 的人是浏览器问题。答案取决于 incident 的影响范围和员工的位置。
- "上周五以后 Jenkins 一直超时"：需要变更记录、任务的报错，以及说明哪种报错对应哪项变更的 runbook。

规则引擎要求每条路径提前写好，遇到新问题就失效；基于 KB 的聊天机器人看不到员工的记录，也不能动手。agent 决定**下一步查什么、问什么**；每个 case 都不能变的部分（谁能做什么、身份是否已验证、什么算证据、交接格式、优先级）由 code 决定。

## 3. 架构和设计决定

```
员工 ──► web / CLI ──► HelpdeskAgent.respond() ──────────────────────► 回复
                            │  对话 + case 记录（由 code 维护）
                            ▼
                      Claude（tool use 循环）
                            │ tool 调用
                            ▼
            tool runtime：校验输入、失败重试一次、读取结果分配 evidence id
                  │ 读 tool                              │ 写 tool
                  ▼                                      ▼
      KB · 状态/incident/变更 · 员工目录 ·       policy engine（policy.yaml + 身份状态）
      诊断 · 历史 ticket（经 facts.py）           allow / needs_approval / human_only / forbidden
      发送前的回复检查：引用的 KB 文章确实读过；ticket 编号确实存在
```

| 部分 | 决定什么 |
|---|---|
| Claude（`agent.py`、`prompts.py`） | 查什么、问什么、怎么解释 |
| Facts（`facts.py`） | 关于员工的每一个事实，比如已安装版本、已有权限、incident 是否覆盖员工 |
| Policy engine（`policy.py`、`data/policy.yaml`） | 一个动作能否执行、是否需要审批或人工 |
| Case 记录（`case.py`） | case 状态（只在 `transition()` 里改变）、每种结局的条件、ticket 的团队和优先级 |

主要的设计决定：

- **约束放在 code 里，选择留给 model。** code 守住不变量（policy、身份、证据、交接格式），路径由 model 选择。全程用状态机控制的设计被放弃了，因为它遇到没人预料到的问题就会失效。
- **每个事实、每个决定只有一个负责的地方。** policy 和 tool 调用同一个 fact 函数，所以 agent 被告知的内容和 code 允许的操作不会矛盾。离线 test 检查这一点。
- **改动需要员工的原话。** 任何改动都要员工用自己的话同意这一项改动，验证码不算同意；`resolved` 需要员工在最后一次改动之后说"好了"的原话。
- **tool 输出是文字，并说明自己的局限。** 写入结果写明做了什么、读回的结果和没有改什么；不是实时的记录写明时间。
- **单 agent、手写循环**（Anthropic SDK，不用框架），每一步都看得见、讲得清。
- **可观测性和成本：** 每个 case 一份 JSONL trace；网页通过 SSE 实时显示每一步；prompt caching 让大部分输入 token 走缓存。Model 是 `claude-sonnet-5-5`，可用 `AGENT_MODEL` 切换。

## 4. 自己解决还是转人工

边界写在 `data/policy.yaml` 里，不在 prompt 里：

| 决定 | 例子 | agent 怎么做 |
|---|---|---|
| `allow`（加前提条件） | 解锁账号（已验证、确实被锁）；开通自动批准的权限；推送批准的版本（员工同意）；加入覆盖员工所在地的 incident | 执行，并读回结果 |
| `needs_approval` | Snowflake PROD 读权限 | 提交给正确的审批人，以 `pending_approval` 结案 |
| `human_only` | MFA 重置、账号停用、防火墙、生产任务重跑 | 转给 policy 指定的团队 |
| `forbidden` | 本地管理员权限 | 解释原因，提供批准的途径（Self Service） |

agent 也会在这些情况下转人工：安全事件、检查后原因不明、证据矛盾、数据源一直失败、修复无效、员工要求找人。

**交接**（`var/tickets/IT-xxxxx.json`，UI 里也会显示）包括：员工说过的每一句话的原文；身份是否已验证；系统、症状和截止时间；agent 已经做了什么；每条发现及其 evidence id；还在考虑和已排除的原因；支持工程师需要回答的具体问题；已给出的 workaround；由 code 计算的优先级。model 只写摘要和判断，其余由 code 从 case 记录里填。

## 5. 数据源（模拟）

| 数据源 | 文件 | 用途 |
|---|---|---|
| 知识库和 runbook | `data/kb/*.md` | 有依据的修复步骤（其中一篇已废弃，用来测试过滤） |
| 服务状态、incident、变更记录 | `data/services.json`、`incidents.json`、`changes.json` | 已知故障及其影响范围；"最近改了什么" |
| 员工目录和诊断数据 | `data/users.json` | 部门、设备和版本、权限、账号状态、登录和 VPN 日志 |
| 历史 ticket | `data/tickets.json` | 可能原因的参考（其中一张埋了 prompt injection） |
| Policy 和权限目录 | `data/policy.yaml` | agent 能做什么、需要什么前提、谁来审批 |

每个示例问题都需要至少两个数据源才能答对，而且有些数据源互相矛盾（状态页说 Jenkins 正常，但每次运行都失败）。

## 6. 假设和取舍

- 员工不需要 Okta 就能打开 helpdesk 聊天（比如在手机上用 Slack）。查记录不需要额外验证；账号和权限的改动需要短信验证码（模拟）。网页上的员工选择框代表这次登录。
- agent 只能看到当前登录员工自己的记录。
- 模拟数据是静态 JSON，每段对话一份独立副本。
- 没有后台通知：加入 incident 不会在修好时通知员工，agent 也不会这样承诺。
- 记录和员工说法矛盾时，agent 不选边，交给人判断。
- KB 检索用 BM25：只有 23 篇文章时，embedding 的收益不大。
- 依据检查分两层：能用 code 判断的每条回复发出前都检查；需要语义判断的放在离线 eval，避免给每条回复增加一次 model 调用。

## 7. 如何运行

需要 Python 3.10+ 和一个 Anthropic API key。

```bash
cd it-helpdesk-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # 然后把你的 key 填进 .env

python -m helpdesk.web      # 打开 http://127.0.0.1:8000
```

在网页上选择以哪位员工的身份登录（每位员工都有一个示例问题），然后输入。**Fault injection** 菜单可以让某个 tool 超时或失败。验证码会以黄色系统通知出现，把它输入到聊天里即可。

终端：`python -m helpdesk.cli --trace`（`--user u-maya` 跳过选择；`/case` 打印 case 记录）。

Test：`python -m pytest -q`。Eval：`python -m evals.run_evals --repeats 3`。

## 8. 如何评估

1. **单元 test**（`tests/`，53 个，不调用 model）：policy 决定、身份验证、同意、结案条件、故障注入、交接、优先级，以及"每个决定只有一个负责的地方"。
2. **场景 eval**（`evals/scenarios.yaml`，18 个场景）：每个场景由一个模拟员工（Claude Haiku，带人设和隐藏信息）和 agent 对话到结束，然后：
   - **code 检查**最终状态：结局、团队、优先级、执行的动作和禁止的动作、必须出现和不能出现的内容；
   - **judge model**（Claude Opus）检查回复里的每一句话在 tool 结果里有没有依据，并给诊断、诚实程度、交接质量和员工体验打分；
   - 每个场景跑 3 次，报告 pass^3。

场景覆盖五个示例问题，以及 MFA、账号停用、本地管理员的压力、钓鱼、设备丢失、含糊的描述、邮箱满了、办公室故障、非 IT 请求、状态页不可用、prompt injection、要求找人，以及把验证码当成同意的陷阱。

结果（Claude Sonnet 5.5，18 个场景 × 3）：

| 指标 | 结果 |
|---|---|
| 安全：没有执行禁止的动作，不编造 incident，识别 prompt injection | 54/54 |
| 任务完成：结局、团队、执行的动作 | 53/54 |
| 回复有依据（judge） | 52/54 |

平均每段对话 $0.04，约 19 秒。judge 判为失败的运行，我都会对照 trace 复核。

## 9. 如果有更多时间

- 在 CI 里跑 eval，按 prompt 版本跟踪每个场景的 pass^k。
- 把 agent 的 tool 列表交给 judge，让模拟员工的测试结果写进场景设定，提高 eval 的准确度。
- KB 文章质量的闭环：agent 已经记录没有答案的搜索和有问题的文章，把它们转给文章负责人。
- 主动跟进：incident 修好后通知员工，员工说没好就重新打开 case。
- 降低延迟：简单的问题用更低的 effort。
- 真实系统对接、SSO 和审计。见 `docs/design_note.md`。

## 代码结构

```
helpdesk/   agent.py（循环）、facts.py（员工事实）、tools.py（14 个 tool）、policy.py、case.py、
            prompts.py、world.py（模拟公司）、trace.py、web.py + static/、cli.py
data/       模拟公司数据和 policy
evals/      scenarios.yaml、run_evals.py（模拟员工、code 检查、judge）
tests/      单元 test
docs/design_note.md   上生产的说明
```
