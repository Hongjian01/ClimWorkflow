# ClimWorkflow 面试问题

后续所有与 ClimWorkflow / 个人 Agent 项目相关的面试题与参考答案，统一维护在本文件。

未实现项见同目录 [ClimateWorkFlow未实现点.md](./ClimateWorkFlow未实现点.md)。  
口述背诵稿（90 秒 / 3 分钟）见 [ClimateWorkFlow面试口述稿.md](./ClimateWorkFlow面试口述稿.md)。

**目录**

1. [项目选型](#1-项目是如何进行选型的为什么选择-openharness-而不是-langgraph--deep-agents--claude-agent-sdk)
2. [Function Calling / 工具接口](#2-function-calling--工具接口设计本项目高发题)
3. [单 Agent vs 多 Agent](#3-单-agent-vs-多-agent本项目高发题)
4. [ReAct](#4-react本项目高发题)
5. [Compact 与 Memory](#5-压缩compact与记忆memory本项目高发--易踩坑)
6. [Agent 健壮性](#6-agent-健壮性设计本项目高发题)
7. [中断恢复：read_context 与真正续跑](#7-中断恢复read_context-与真正续跑本项目高发题)
8. [WAL 与幂等流程图](#8-wal-与幂等流程图)
9. [工具挂起、假并行与 to_thread](#9-工具挂起假并行与-tothread本项目高发题)

流程图使用纯文本画出，避免 Markdown 预览渲染 Mermaid 失败时从该处起整页空白。

---

## 1. 项目是如何进行选型的？为什么选择 OpenHarness，而不是 LangGraph / Deep Agents / Claude Agent SDK？

### 参考答案（完整版）

ClimWorkflow 要把气候分析从人工脚本，改成「自然语言目标 → 多步 Tool Calling → 数据获取/检查/绘图/报告」的长链路 Agent。难点不只是调模型，而是：工具参数要可校验、执行顺序要能防跳步、外部 CDS 下载会失败/限流、进程中断后业务状态还要可恢复，同时不能执行任意代码。选型时我看的是：**谁能提供成熟的工具循环与权限/Hook 扩展点，同时允许我把领域状态和科学工具约束做在业务层。**

**选 OpenHarness 的第一点，是它的定位更接近 Agent Harness，而不是单纯 Prompt SDK。**  
它已经具备 QueryEngine 工具循环、Tool Registry、Permission、Hook、Skill、Memory/Compact。我可以直接注册 Climate Tool，复用「模型规划 → 工具执行 → 结果回填」主循环，把精力放在气候领域能力上：RunContext、状态机、WAL 恢复、CDS 元数据/多候选、路径沙箱。这和项目目标匹配：先有可控执行骨架，再补领域确定性。

**第二点，是扩展边界清晰。**  
我们明确不改 QueryEngine 语义。通用循环归 Runtime，业务权威状态归 `.climate/` 的版本化 Context。这样上游升级风险低，也逼着自己分清「聊天摘要」和「run/step/artifact 是否真成功」。对长任务来说，这个分层比再选一个只会编排对话的框架更重要。

**和其他方案对比：**

- **相对 LangGraph：LangGraph 强在显式 State Graph、Checkpoint、Interrupt/Resume，很适合审批流、多轮槽位、人机确认等复杂可控流程。但 ClimWorkflow 的主路径相对固定（init→plan→acquire→inspect→plot→report），关键不是多 Agent 条件路由，而是类型化工具 + 磁盘权威状态 + 外部数据可靠性**。若用 LangGraph，我还要从头接 Tool 权限、Hook、Skill，并把气候状态 Schema、幂等、路径安全再建模一遍，前期成本更高。业务恢复我们最终也是在 Climate Repository 层做的，并不依赖图引擎 checkpoint。  
**取舍：**如果以后要强 HITL（例如每次写库前人工确认）或复杂分支编排，会重新评估 LangGraph；当前阶段 OpenHarness 更合适。
- **相对 Deep Agents：Deep Agents 更像面向长任务的高层 Harness（规划、todo、文件工作区、子代理委托），适合「读很多文档再写研究报告」。ClimWorkflow 更需要窄工具面**：不允许任意 Shell/代码，CDS 请求要元数据校验，产物要规则门禁。高层 Harness 开箱快，但对「科学数据工具契约、失败码、显式降级审计」的控制力不如在 OpenHarness 上自建领域工具来得直接，也容易偏重。
- **相对 Claude Agent SDK：**它同样是完整 Harness（内置文件/Shell 工具、权限、Hook），和 OpenHarness 思路接近。但项目需要 OpenAI 兼容多模型（实际评测用过 DeepSeek 等），且气候场景要严格限制高权限工具，避免模型「写脚本搞定」。Claude Agent SDK 生态绑定更强；OpenHarness 开源可 fork，工具面可以由我收敛成 Climate Tool，更符合「有边界的领域 Agent」。

**成本与代价也要承认：**  
OpenHarness 不替你解决业务状态设计。文件锁、乐观版本、WAL、幂等、CDS 重试/候选、路径脱敏，都要自己按规格落地和验证。框架省的是 Agent Loop，不省领域工程。

**一句话收束：**  
选 OpenHarness，是因为项目需要的是「成熟工具循环 + 权限/Hook 扩展点 + 可自建领域状态」，而不是 LangGraph 那种重图编排，也不是 Deep Agents/Claude Agent SDK 那种更通用或更绑定生态的长任务/编码 Harness。气象是验证场景，选型标准是把不确定的模型判断，放进可约束、可恢复、可审计的执行过程里。

### 30 秒缩短版

> 我们要的是长链路气候 Agent：类型化工具、防跳步、CDS 可失败、状态可恢复，且不能任意执行代码。OpenHarness 提供现成工具循环、权限和 Hook，我把业务状态和气候工具做在扩展层。LangGraph 更适合复杂图编排与 Interrupt，对本项目主路径偏重；Deep Agents 偏通用长任务委托；Claude Agent SDK 更绑 Claude 且默认工具面更宽。所以选可 fork 的 OpenHarness，把复杂度留在领域层。

### 回答注意

1. 说「基于 OpenHarness 扩展」，不要说「我做了 OpenHarness」。
2. 被问「那为何不自己用 LangGraph 做 checkpoint」时：答「业务 checkpoint 在 RunContext/WAL，图编排不是当前瓶颈」。
3. 被问「以后会换吗」：答「流程变复杂或强 HITL 时会评估 LangGraph；现在证据支持 OpenHarness + 领域状态」。

---

## 2. Function Calling / 工具接口设计（本项目高发题）

> 说明：Function Calling 不一定每场都问，但简历写了 Tool Calling、Pydantic Schema、七/八个 Climate Tool，**中大厂 Agent 岗大概率会问到工具设计**；其中 **Pydantic 会问，但通常问「你怎么用它约束模型」**，很少单独考 Pydantic 语法百科。

### 2.0 会不会问 Pydantic？

**会，但多半是手段不是考点。**

常见问法：

- 工具参数怎么约束？为什么用 Pydantic / JSON Schema？
- `extra="forbid"` 有什么用？
- Schema 校验失败后模型怎么纠错？
- Pydantic 校验够不够？为什么还要业务校验（DAG、mode 互斥、元数据目录）？

建议口径：

> Pydantic 负责把工具入参变成可校验的 JSON Schema，发给模型并在执行前 `model_validate`；`extra="forbid"` 拒绝未知字段。但它只保证类型和字段约束，跨字段语义、执行顺序、CDS allowlist 还要业务层校验。失败时返回稳定错误码给模型，而不是 traceback。

---

### 2.1 本项目最可能被问到的问题清单

#### A. 原理与流程（偏通用，结合本项目答）

1. Function Calling / Tool Calling 是怎么工作的？
2. 模型为什么会 Function Calling？训练大致分哪几阶段？（预训练 / SFT / 对齐；知概念即可）
3. OpenHarness 里一次工具调用经过哪些步骤？（Hook → schema → Permission → execute → ToolResult）
4. 工具 schema 从哪里来？谁做校验？

#### B. 接口设计（结合 ClimWorkflow，高频）

1. 为什么拆成 7/8 个工具，而不是一个大 `run_climate_pipeline`？
2. 每个工具参数多不多？怎么控制模型填参失败？
3. `climate_acquire_data` 为什么用 `mode` + 互斥字段，而不是很多可选参数堆一起？
4. 为什么禁止模型执行任意 Python/Shell，只给 Climate Tool？
5. 只读工具和写工具怎么区分？为什么 validate / read_context 是只读？
6. 工具命名和 description 怎么写才利于模型选对工具？

#### C. 失败、校验与重试（高频，对标图中 2.2）

1. 参数很多 / 缺必填 / 调错时机时，怎么降低失败率？
2. Schema 过了但业务不合法怎么办？（例：未 inspect 就 plot）
3. 工具失败后谁重试：模型重试，还是工具内重试？
4. CDS 超时/限流怎么处理？和普通参数错误有何不同？
5. 错误信息如何回传给模型才有用？（稳定错误码 + 脱敏 message + 可操作 details）

#### D. Pydantic / Schema（中频，简历点到就会问）

1. 为什么用 Pydantic？和手写 dict 校验比有什么好处？
2. `extra="forbid"`、Field 约束、跨字段 `model_validator` 各解决什么？
3. JSON Schema 导出后模型仍胡填，你怎么发现和修？
4. Pydantic 能否替代权限和路径沙箱？为什么不能？

#### E. 与编排/状态的交界（中高频）

1. 仅靠 Prompt 能防止跳步吗？为什么还要状态机/DAG？
2. ToolResult 成功长什么样？失败长什么样？
3. 同输入重放为什么要幂等？和 Function Calling 有什么关系？

---

### 2.2 优先准备的参考答案（先背这几题）

#### Q1. 你们工具调用链路是怎样的？

> 模型在系统侧拿到各工具的 JSON Schema；若决定调用，返回 tool name + arguments。Runtime 侧先过 Hook，再 Pydantic 校验，再 Permission，再真正 execute，最后把 ToolResult 回填对话，进入下一轮。ClimWorkflow 不改这条主循环，只提供 Climate Tool 实现和更严的业务校验。

#### Q2. 为什么拆多个工具，而不是一个大工具？

> 单一大工具参数爆炸，模型更容易漏填、错填，也难做权限分级和局部重试。我们按职责拆成 init/plan/acquire/inspect/plot/report/read（外加只读 validate）：单工具参数更少、失败可定位到步骤，并可在状态机里约束顺序。这和「少参数、单一职责」的工具设计原则一致。

#### Q3. 参数错误/调用失败怎么处理？

> 三层：  
> 1）Schema：Pydantic 类型、必填、`extra="forbid"`；  
> 2）业务：mode 互斥、DAG 依赖、元数据目录、路径沙箱；  
> 3）反馈：统一 JSON envelope + 稳定错误码，脱敏后回给模型，让它改参或改工具，而不是看 Python traceback。  
> CDS 仅对 timeout/rate-limit 在工具内有界重试；参数错误不盲目重试。

#### Q4. 会问到「Function Calling 怎么训练出来的」吗？怎么答？

> 可能作为概念题。答三阶段即可：预训练学语言；SFT 学何时调用、何格式；对齐（如 RLHF）让调用行为更符合人类预期。然后立刻拐回工程：训练决定模型上限，线上稳定性更靠 Schema、校验、错误回传和工具拆分。

#### Q5. Pydantic 具体在项目里做什么？

> 每个 Climate Tool 有对应 Input Model，`extra="forbid"`。Schema 导出给模型；执行前 validate。例如 acquire 的 mode 与 path/cds_request 互斥，用模型层 + 业务层一起兜住。Pydantic 过了还不够：未完成依赖的 step、非法 CDS 变量、路径逃逸，仍会在业务层失败。

---

### 2.3 回答策略

1. **先讲本项目机制，再补通用理论**（避免只会背「六条优化建议」）。
2. **主动对比**：Prompt 约束 vs Schema 约束 vs 状态机约束。
3. **承认边界**：没有靠 Function Calling 解决长任务恢复；恢复靠 RunContext。
4. 若被问「你有没有自己训过 Function Calling 模型」：诚实说没有，项目侧是工程约束与评测冒烟。

---

## 3. 单 Agent vs 多 Agent（本项目高发题）

> ClimWorkflow 现状是 **单 Agent + 多工具**：一个模型负责理解与编排，Climate Tool 是确定性执行单元，共享同一份 RunContext。简历未写 Multi-Agent，面试常问「为什么不做多 Agent」。

### 3.0 先定口径（必背）

> 我们选单 Agent。气候链路是工具密集、强顺序依赖的工作流：acquire → inspect → plot → report，共享同一 run 状态。多 Agent 会引入角色通信、上下文不一致和错误放大，却不一定提升完成率。工具分工不等于 Agent 分工。

---

### 3.1 本项目最可能被问到的问题清单

#### A. 项目选型（最高频）

1. 你们是单 Agent 还是多 Agent？依据是什么？
2. 为什么不把 acquire / inspect / plot / report 做成多个 Agent？
3. 「7 个 Tool」和「多 Agent」有什么区别？
4. 什么条件下你会升级成多 Agent？
5. 有没有做过单 vs 多 Agent 对比实验？没有的话如何论证选型合理？

#### B. 通用架构认知（图中快测题，结合项目答）

1. 多 Agent 常见架构有哪些？各适合什么？（中心化 Supervisor、分层、对等协作等）
2. 为什么独立实体的多 Agent 容易放大错误？怎么缓解？
3. Agent 架构怎么设计，才能避免拍脑袋？
4. 中心化架构过度依赖中心 Agent 有什么问题？如何缓解？
5. 哪些场景更适合单 Agent？为什么？
6. 金融分析 / Web 导航 / Workflow / 博弈规划分别更适合哪种？**对应到 ClimWorkflow 呢？**

#### C. 结合 ClimWorkflow 机制深挖（中高频）

1. 单 Agent 如何防止「一个模型既规划又乱调用」导致失控？
2. 共享 RunContext 对单 Agent 意味着什么？多 Agent 下状态怎么同步会更难？
3. CDS 下载失败、限流、显式降级，放在单 Agent 里如何处理？拆成多 Agent 会更好吗？
4. Skill（climate-ds）在单 Agent 里扮演什么角色？算不算第二个 Agent？
5. 如果面试官说「业界都在推 Multi-Agent」，你怎么回应？
6. 单 Agent 上下文很快塞满、噪声变多、注意力下降，你们怎么应对？是否因此必须上多 Agent？

---

### 3.2 优先准备的参考答案

#### Q1. 为什么 ClimWorkflow 用单 Agent？

> 任务类型接近 **Workflow + 工具密集顺序调用**：步骤依赖明确，中间产物（dataset/profile/plot/report）必须挂在同一 run 上。单 Agent 负责自然语言理解和工具选择，顺序与合法性由 DAG/状态机兜底。此时多 Agent 的分工收益有限，却增加通信和状态同步成本。资料里也提到：单 Agent 基线已经够用时，盲目上多 Agent 可能降收益。

#### Q2. 为什么不把每个步骤做成一个 Agent？

> inspect/plot 不是「需要独立目标与记忆的智能体」，而是**带契约的工具**。若拆成多 Agent，还要解决：谁持有权威状态、失败后如何回滚、如何避免 A Agent 以为成功而 B Agent 读到旧 Context。我们把智能留在单循环，把确定性留在 Tool + Repository，更贴合可恢复工作流。

#### Q3. 多 Agent 常见架构？你为何没选？

> 常见有：  
>
> - **中心化 Supervisor**：主 Agent 调度子 Agent；  
> - **分层**：规划层 / 执行层；  
> - **对等协作**：多角色讨论后汇总。  
> ClimWorkflow 若硬套 Supervisor，子 Agent 大多只是调一个工具，会变成「套娃 Tool Calling」，复杂度和延迟上升。真正要 Supervisor 的信号是：出现并行子目标、独立知识域、或需要长时间自主探索。

#### Q4. 多 Agent 为何容易放大错误？本项目如何规避？

> 每个 Agent 都可能误解目标、传错中间结论；拓扑越复杂，错误越易串联。规避思路：减少不必要角色、共享权威状态、对副作用做校验与幂等、能量化时做单/多对比。ClimWorkflow 用单 Agent + 磁盘 RunContext + 硬错误码，从结构上减少「口口相传」式失真。

#### Q5. 什么时候你会考虑多 Agent？

> 例如：  
> 1）元数据检索 / 文献综述与数值下载可并行；  
> 2）报告撰写需要独立长文生成 Agent，且与数据执行隔离；  
> 3）明确的 HITL 审批 Agent 与执行 Agent 分离。  
> 上线前会先保留单 Agent 基线，再对比成功率、步数、成本与失败归因；没有收益就不拆。

#### Q6. ClimWorkflow 更像资料里的哪类场景？

> 更像 **Workflow**：强顺序、可恢复、工具密集。不像需要多角色讨论的博弈规划，也不像强并行 Web 导航。因此单 Agent + 工具编排更合适。

#### Q7. 没有对比实验怎么答辩？

> 诚实说：未做严格 Multi-Agent A/B。选型依据是任务结构（顺序依赖、共享状态、窄工具面）与工程目标（可恢复、可审计）。若投入多 Agent，预期成本上升点是状态同步与错误传播，而主路径并行度有限。后续若加并行子任务，会补对比。

#### Q8. 单 Agent 上下文塞满、噪声多，怎么办？要不要因此上多 Agent？

> **要准备，而且本项目有现成答法。**  
> 问题真实存在：多轮 ToolResult、失败堆栈、中间解释会占满上下文，模型更容易忽略早期约束。但 **ClimWorkflow 的解法不是先拆多 Agent，而是把权威状态移出聊天。**  
> 具体：  
> 1）**权威状态在 RunContext**：run/step/artifact/version 落盘；会话重启或 compact 后先 `climate_read_context`，不靠模型记忆“做到哪一步”。  
> 2）**上下文治理复用 Runtime**：Compact/Memory 只做导航与摘要，不是业务真相。  
> 3）**控制进入上下文的噪声**：ToolResult 用结构化 JSON、有界输出、脱敏；禁止把大表/全网格塞回对话。  
> 4）**Skill 约束**：遇错先读 Context，禁止凭 compact 摘要猜成功。  
> 多 Agent 能通过隔离上下文缓解噪声，但会引入同步与错误传播；对本项目这种共享 run 的顺序工作流，先做 Context 外置和结果有界，通常比拆角色更划算。若未来并行长文生成与数据执行互相污染，再考虑分离 Agent 上下文。

---

### 3.3 回答策略

1. **先定性本项目：单 Agent + 多 Tool，不是 Multi-Agent。**
2. **用任务结构论证，不吹「单 Agent 一定更先进」。**
3. **会说多 Agent 架构名词，但落回 ClimWorkflow 为何不需要。**
4. **给出可升级条件 + 要用数据验证**，体现工程判断力。
5. **被问上下文爆炸时：先答 RunContext 外置，再答 Compact/有界 ToolResult；最后才提多 Agent 隔离是备选。**

---

## 4. ReAct（本项目高发题）

> OpenHarness 的 QueryEngine 工具循环本质是 **ReAct 风格**（Reason → Act → Observe → Reason）。但 ClimWorkflow **不是纯自由 ReAct**：模型在循环里选工具，业务顺序与成败由 DAG/状态机/Repository 约束。面试口径：**ReAct 负责编排，确定性执行负责落地。**

### 4.0 先定口径（必背）

> ReAct 的核心是推理与行动交替：模型判断下一步，调用工具，观察结果，再继续判断。ClimWorkflow 复用 Runtime 的 ReAct 式 Tool Loop；同时用 plan DAG、状态机和 RunContext，把「想怎么做」限制在「允许怎么做」里，避免纯 ReAct 常见的跳步、幻觉成功和不可恢复。

---

### 4.1 本项目最可能被问到的问题清单

#### A. 概念与实现（真题同款，必准备）

1. ReAct 的核心思想是什么？如何实现？
2. Reason 和 Act 分别指什么？Reflection 在哪一步？
3. 一次 ReAct 循环在工程上怎么跑？（tool-call → 执行 → 回填 → 再调用模型）
4. 循环何时结束？max_turns 用尽怎么办？

#### B. 结合 ClimWorkflow（最高频）

1. 你们项目是 ReAct 吗？和教科书 ReAct 有何不同？
2. 为什么不做成纯固定 Workflow，还要保留 ReAct 循环？
3. 为什么不做成纯 ReAct，而要加 plan/状态机？
4. Plan-and-Execute、ReAct、固定 Workflow 三者如何取舍？ClimWorkflow 属于哪种？
5. ClimWorkflow 里 Act 对应哪些工具？Observe 的结果长什么样？
6. 「模型以为成功了」时，ReAct 靠什么纠偏？你们靠什么？

#### C. 深挖与对比（中高频）

1. ReAct 的优缺点是什么？长任务下最大风险是什么？
2. ReAct 和 Function Calling 是什么关系？
3. Compact 之后 ReAct 历史被压缩，如何保证还能续跑？
4. 若面试官问「请画一下你们的 ReAct 循环」，你怎么画？
5. 如果把状态机去掉、只留 ReAct，会发生什么？

---

### 4.2 优先准备的参考答案

#### Q1. ReAct 的核心思想是什么？如何实现？（通用 + 落到本项目）

**思路：**先定义 Reasoning + Acting，再讲工程循环，最后用 OpenHarness/ClimWorkflow 举例，避免只背名词。

> ReAct = Reasoning + Acting：让 Agent **交替**进行推理与行动，而不是一次性靠参数记忆答完。  
>
> - **Reason：**规划下一步、判断信息是否够、反思工具结果是否可用。  
> - **Act：**选择工具并生成调用参数；框架执行后把结果作为观察值写回对话。  
> 工程实现：框架把用户问题、历史、工具 Schema 发给模型 → 模型返回结构化 tool-call → 框架执行 → 结果回填 → 再调模型；直到模型给出最终文本、不再调工具，或触达 max_turns。  
> 在 ClimWorkflow 中，这条循环由 OpenHarness QueryEngine 承载；Climate Tool 是 Act 的具体动作。

#### Q2. 你们是纯 ReAct 吗？

> **是 ReAct 式循环，不是纯自由 ReAct。**  
> 模型仍按 ReAct 方式多轮选工具；但 plan 必须落成合法 DAG，step 转换受状态机约束，权威进度在 RunContext。等于：**ReAct 负责“下一步调用谁”，状态机负责“这一步允不允许、结果算不算数”。**

#### Q3. 为什么还要 ReAct，而不是纯写死流水线？

> 用户目标是自然语言，参数（区域、变量、sample/local/cds、图表列）需要模型理解后填入工具。完全写死流水线会失去灵活性。ReAct 循环让模型能在失败后改参、换 mode 或先 read_context 再继续；硬约束则防止它跳过 inspect 直接 plot。

#### Q4. ReAct vs Plan-and-Execute vs 固定 Workflow，你们怎么选？

> - **纯 ReAct：**灵活，但长链路易漂、难恢复。  
> - **Plan-and-Execute：**先计划再执行，计划质量要求高。  
> - **固定 Workflow：**最稳，但自然语言适配差。  
> ClimWorkflow 是混合：**Agent（ReAct 循环）填参与选步 + 持久化 plan/状态机执行**。先 `climate_plan_steps` 固化计划，再在循环中逐步 Act，类似轻量 Plan-and-Execute，执行期仍是 ReAct 工具环。

#### Q5. Observe 之后如何“反思”？你们靠模型还是靠系统？

> 两层：  
> 1）**模型反思：**看到 ToolResult 的 ok/error 后决定重试或换工具。  
> 2）**系统硬约束：**非法顺序直接拒绝；CDS 仅对超时/限流有界重试；成功与否以 Context 版本和 artifact 为准，不靠模型口头总结。  
> 所以 Reflection 不完全交给 Prompt，关键状态以磁盘为准。

#### Q6. ReAct 和 Function Calling 的关系？

> Function Calling 是 Act 的现代接口形态（结构化 tool-call）；ReAct 是「推理—行动—观察」的循环范式。没有 Function Calling 也可以 ReAct（文本里写 Action），但本项目用的是 Schema 化 Tool Calling 实现 Act。

#### Q7. 上下文压缩后 ReAct 还怎么工作？

> 对话可能被 Compact，ReAct 历史会丢细节；因此续跑必须先 `climate_read_context` 恢复权威状态，再进入新的 Reason/Act。这是单 Agent ReAct 在长任务上的补丁，也是我们强调 RunContext 的原因。

#### Q8. 画一下 ClimWorkflow 的 ReAct 循环（口述版）

```text
用户目标
  → 模型 Reason：选 climate_init / plan / acquire ...
  → Act：结构化 tool-call
  → Runtime：Hook → Pydantic → Permission → execute
  → Observe：ToolResult(JSON) 回填
  → 模型再 Reason
  → ... 直到报告完成或 read_context 确认 completed
并行约束：状态机/DAG 可在 Act 前直接拒绝非法调用
```

---

### 4.3 回答策略

1. **先答教科书 ReAct，再立刻说「我们是 ReAct + 状态约束」。**
2. **不要把 OpenHarness Loop 说成自己实现的 ReAct 框架。**
3. **主动对比纯 ReAct 的漂移问题，引出 RunContext/DAG 的价值。**
4. **被问实现细节时，能说出 tool-call → 校验 → 执行 → 回填 → 再推理。**

---

## 5. 压缩（Compact）与记忆（Memory）（本项目高发 + 易踩坑）

> 资料里常问「长短期记忆如何实现？上下文满了怎么压缩？」  
> **推荐答法：OpenHarness 已提供 Memory + Compact，ClimWorkflow 在其上补 RunContext，三者分工协作——可以也应该结合起来答。**  
> 不要说「项目没有记忆」；准确说法是：**会话记忆与压缩复用 Runtime；业务记忆自研在 RunContext；二者配合，不互相替代。**

### 5.0 先定口径（必背）

> **OpenHarness + ClimWorkflow 的记忆/压缩分层：**  
>
>
> | 层级             | 来源            | 记什么                          | 权威吗          |
> | -------------- | ------------- | ---------------------------- | ------------ |
> | 对话上下文          | Runtime ReAct | 当前多轮 messages                | 否，会被 Compact |
> | **Compact**    | OpenHarness   | 上下文满时压缩历史，保留摘要/附件/carry-over | 否，只减负        |
> | **Memory**     | OpenHarness   | 项目级 memory 文件，会话导航与摘要        | 否，辅助连续对话     |
> | **RunContext** | ClimWorkflow  | run/step/artifact/version/事件 | **是，业务唯一权威** |
>
>
> Skill 要求：Compact/Memory 之后续跑，**必须先 `climate_read_context`**，禁止凭摘要猜 step 已成功。  
> SPEC 原则（DEC-007）：**Memory/compact 只提供导航提示；权威恢复只读 Context。**

### 5.0.1 结合 OpenHarness 的统一答法（30～60 秒，建议背）

> 我们的记忆和压缩是 **Runtime 能力 + 领域状态** 两层结合，不是从零造，也不是只有 RunContext。  
> **短期：**OpenHarness 的对话上下文 + **Compact**。上下文满了，Compact 把早期轮次压成结构化摘要，让 ReAct 还能继续推理，但 Compact **不负责**记录「acquire 是否已成功」。  
> **会话/项目层：**OpenHarness **Memory** 用原子写持久化项目 memory，帮助跨轮次保留导航信息和摘要，但它 **不是** Climate 的业务状态机。  
> **长期/业务层：**ClimWorkflow 把权威状态放在 workspace 内 **RunContext**——step 状态、artifact 路径、版本号、错误码。会话重启、Compact 之后，Agent 按 Skill 先 `climate_read_context`，从磁盘恢复，再决定下一工具。  
> 所以：**Memory/Compact 解决「聊下去」；RunContext 解决「做对、做完、可恢复」。** 这是结合 OpenHarness 做的分工，而不是重复造 Memory。

---

### 5.1 本项目最可能被问到的问题清单

#### A. 概念区分（最高频，易答错）

1. 你们有没有记忆系统？长短期记忆怎么实现？
2. Memory、对话历史、RunContext 三者区别是什么？
3. 为什么业务状态不放聊天里，而要单独 RunContext？
4. Compact 之后模型还怎么知道做到哪一步？
5. 会话重启 / 多轮恢复时，信息从哪来？

#### B. 压缩策略（高频）

1. 上下文对话满了怎么办？Compact 做了什么、没做什么？
2. Compact 会不会把关键工具结果压丢？你们如何规避？
3. 有没有做摘要、滚动窗口、向量记忆？为什么有/没有？
4. ToolResult 很大时（inspect profile、CDS 结果）怎么控制进上下文的大小？

#### C. 设计与取舍（中高频，对标 cc/hermes/openclaw 类追问）

1. 为什么不自研分层记忆（短期会话 + 长期画像）？
2. 如果让你加记忆系统，你会怎么设计？（开放题）
3. RunContext 算不算一种「长期记忆」？和 RAG/Memory 有何不同？
4. multiturn_recovery 场景如何证明恢复不依赖 Memory？

#### D. 结合机制深挖

1. `climate_read_context` 和 Memory 各干什么？为何 read 是只读权威源？
2. max_turns 用尽、Compact 之后，下一步 Agent 应该怎么做？
3. 若面试官要求「设计长短期记忆」，ClimWorkflow 哪些已有、哪些该补？

---

### 5.2 优先准备的参考答案

#### Q1. 你们有没有记忆系统？长短期怎么划分？

> **有，OpenHarness Memory/Compact + ClimWorkflow RunContext 结合。**  
>
> - **短期（会话推理）：**对话 messages；满了由 OpenHarness **Compact** 压缩。  
> - **中期（项目 Memory）：**OpenHarness **Memory** 持久化项目级摘要/导航。  
> - **长期（业务执行）：****RunContext** 记 run/step/artifact，跨 Compact、跨会话仍权威。  
> **Memory/Compact 解决「聊下去」；RunContext 解决「做对、做完、可恢复」。**

#### Q2. Memory vs 对话 vs RunContext？

> - **对话：**模型当下看见的上下文，会被截断/Compact（OpenHarness）。  
> - **Memory：**OpenHarness 项目 memory，偏摘要与导航，不承载 step 成败。  
> - **RunContext：**ClimWorkflow 权威状态——哪个 step succeeded、artifact 路径与 hash、最后错误码。  
> **三者配合：Memory/Compact 辅助连续对话；续跑与验收只看 RunContext。**

#### Q3. 上下文满了怎么压缩？Compact 后怎么续跑？

> 上下文满时 Runtime 触发 **Compact**：保留结构化摘要、关键附件与 carry-over metadata，缩短历史。  
> ClimWorkflow **不依赖 Compact 记业务进度**。Compact 后新会话应：  
> 1）先 `climate_read_context` 读磁盘；  
> 2）根据 step 状态决定下一工具；  
> 3）禁止用 compact summary 代替「inspect 已成功」的证明。  
> 注意：`read_context` 只是把进度放进窗口，**不等于已经续跑**；真正执行见第 7 节。

#### Q4. 为什么不自研 cc/hermes/openclaw 那种 Memory？

> 当前痛点是 **工作流状态可恢复**，不是用户画像或多轮闲聊。RunContext + WAL 已覆盖「长任务断点续跑」。  
> 自研分层 Memory 会增加一致性问题（Memory 摘要 vs Context 事实冲突）。  
> **若后续要做：**会在 RunContext 之上加「用户偏好/常用区域」类长期字段，或独立 Memory 模块，且以 Context 为准做冲突降权——这是设计方向，当前 MVP 未做。

#### Q5. 开放题：若让你设计记忆系统，你怎么答？（2 分钟版）

> 先分三类信息：  
> 1）**任务状态**（必持久、强一致）→ 已有 RunContext；  
> 2）**会话摘要**（可丢细节、辅助推理）→ Compact + 可选 Memory；  
> 3）**用户偏好**（弱一致、可过期）→ 未来可加长期 Memory，不与 step 状态混存。  
> 压缩策略：ToolResult **有界**（inspect 不全表、Trace 脱敏）；重要结论写入 Context 事件，而不是只留在聊天里。  
> 验收：multiturn_recovery——销毁会话对象后仅凭磁盘 Context 能继续完成 report。

#### Q6. RunContext 算不算长期记忆？

> 算 **业务执行记忆（durable execution state）**，不算 **语义记忆/RAG**。  
> 它记的是「这次 run 做了什么、产物在哪」，不是「用户喜欢什么图表风格」或「知识库条文」。  
> 面试里可以说：**我们用 Context 外置解决 Compact 丢状态，而不是用向量记忆糊过去。**

#### Q7. 被质疑「项目没有记忆系统设计」怎么回应？

> 若指「没有自研 Mem0/用户画像/向量长期记忆」，**承认 MVP 未做**。  
> 若指「完全没有记忆与压缩」，**应结合 OpenHarness 展开**：  
> Runtime 已有 **Memory + Compact** 管会话连续性；ClimWorkflow 用 **RunContext** 管业务权威；Skill 规定 Compact 后必须 `read_context`。  
> 这是 **Harness 能力 + 领域状态外置** 的组合设计，不是没做记忆，而是避免把 step 成败写进 Memory 摘要造成失真。

---

### 5.3 回答策略

1. **主动说 OpenHarness 已有 Memory + Compact**，再讲 ClimWorkflow 如何用 RunContext 补业务权威——这是加分项。
2. **别混淆：**Memory/Compact ≠ 业务 Context；三者协作，不互相替代。
3. **主动挂钩：**与第 3 节「上下文爆炸」、第 4 节「ReAct 续跑」、第 7 节「read_context vs 真正续跑」同一套故事。
4. **开放设计题：**在 OpenHarness Memory 之上加用户偏好/常用参数即可；**RunContext 仍保持权威**。
5. **诚实边界：**用户画像、向量长期记忆——**当前未做，可作为演进**；不要说 OpenHarness Memory 是你实现的。

---

## 6. Agent 健壮性设计（本项目高发题）

> 资料框架：**工具失败 / 模型失败 / 推理与控制流失败** → 分类 → 局部恢复 → 故障隔离 → 优雅降级 → 安全停止。  
> **完整答法必须两层结合：**  
> 1）**OpenHarness Runtime 健壮性**（工具循环、权限、Hook、预算、会话治理）；  
> 2）**ClimWorkflow 领域健壮性**（RunContext、状态机、CDS 重试/降级、幂等）。  
> 只说业务层会显得「自己在造 Runtime」；只说 OpenHarness 会显得「没做领域设计」。

### 6.0 先定口径（必背）

> **先分类，再处理；能恢复就受控恢复；不能恢复就隔离/降级；确实不安全就停。**  
> 健壮性 = **Harness 管「怎么安全地调工具/调模型」** + **ClimWorkflow 管「业务是否真成功、能否续跑」**。

### 6.0.1 两层分工（必背表）

| 层级 | 来源 | 健壮性能力 | 在本项目中的作用 |
|---|---|---|---|
| **工具循环** | OpenHarness QueryEngine | Hook → Schema → Permission → execute → ToolResult | Climate Tool 走统一六步管道，不在业务里另造 loop |
| **执行前拦截** | OpenHarness Hook | `PRE_TOOL_USE` 匹配阻断 | 非法 write_report 在 execute 前拦截，零副作用（HOOK-001） |
| **权限边界** | OpenHarness Permission | default/plan/full_auto；只读放行、写工具确认、敏感路径拒绝 | `.cdsapirc` 全模式拒绝；Climate 写工具受权限约束 |
| **Schema 校验** | OpenHarness + Pydantic | `model_validate` 在执行前 | 框架层先拦类型/字段；业务层再拦 DAG/mode/元数据 |
| **推理预算** | OpenHarness | `max_turns` 上限，`MaxTurnsExceeded` | 防止无限 ReAct；Eval 冻结 `max_turns=200` |
| **会话治理** | OpenHarness Memory/Compact | 压缩历史、项目 memory | 失败后新会话还能聊；**业务续跑靠 read_context** |
| **基础设施** | OpenHarness utils | `atomic_write_text`、文件锁 | Climate Repository/WAL 复用，防半写/并发丢更新 |
| **配置预检** | OpenHarness | `oh --dry-run` | 启动前发现 auth/MCP/工具配置问题，避免空跑 |
| **业务状态** | ClimWorkflow | RunContext、WAL、状态机、幂等 | 中断恢复、防跳步、同输入重放 |
| **外部依赖** | ClimWorkflow | CDS backoff、≤3 候选、显式 fallback | 工具层容错与可审计降级 |
| **质量门禁** | ClimWorkflow | 错误码、validate、offline 硬断言 | 接口成功 ≠ 任务成功 |

**一句话：**OpenHarness 提供 **安全执行壳**；ClimWorkflow 提供 **可恢复业务真相**。

---

### 6.1 本项目最可能被问到的问题清单

#### A. 总览（NVIDIA / 宇树科技同款）

1. Agent 遇到异常失败，如何保持连续、健壮运行？
2. 外部工具调用失败后，如何做容错或降级？
3. 健壮性是 OpenHarness 做的还是你们做的？两层如何配合？
4. 你们 Demo 和「生产级健壮性」差在哪？

#### B. OpenHarness Runtime 层（新增，常被忽略）

5. 一次工具调用在 OpenHarness 里经过哪些健壮性检查？
6. Permission 的 default/plan/full_auto 如何影响失败与降级？
7. PRE_TOOL_USE Hook 和 POST_TOOL_USE 在容错上有什么区别？
8. `max_turns` 用尽后系统怎么办？和 ClimWorkflow 恢复如何衔接？
9. OpenHarness 的 Pydantic 校验与 Climate 业务校验分别防什么？
10. Memory/Compact 在「失败后继续对话」里起什么作用？
11. `oh --dry-run` 算不算健壮性设计？解决什么问题？

#### C. 工具调用失败 — 领域层（17.2）

12. 工具失败如何分类？确定性错误 vs 瞬时故障怎么处理？
13. CDS 超时/429 怎么重试？和普通参数错误有何不同？
14. 有没有熔断器？CDS 长时间不可用怎么办？
15. 降级策略是什么？会不会伪造成功结果？
16. CDS 窄多候选（≤3）和 sample fallback 区别？
17. ToolResult 错误格式长什么样？

#### D. 模型服务失败（18.3）

18. 模型 API 200 但参数乱填，OpenHarness + ClimWorkflow 如何发现？
19. 有没有备用模型切换？（Runtime 可换 provider，项目是否验证？）
20. 如何区分「接口不可用」和「模型能力退化」？

#### E. 推理与控制流失败

21. 模型跳步、越依赖、重复写，Runtime 与领域层各防什么？
22. 进程中断、残留 `running` step 怎么处理？
23. 有副作用的重试如何避免重复执行？
24. 什么时候该停、该转人工？

#### F. 端到端生命周期

25. 描述一次 CDS 失败从 OpenHarness 管道到 Climate 降级的完整链路。
26. 失败时状态保存在哪？用户/模型各看到什么？
27. `climate_read_context` 把 JSON 放进窗口，算不算已经恢复？真正续跑发生在哪？（详见第 7 节）

#### G. 工具挂起与事件循环（详见第 9 节）

28. 工具不报错也不返回，Agent loop 会不会卡死？OpenHarness 有没有统一工具超时？
29. 这算框架严重 bug 吗？灵活性是不是交给了各工具自己实现？
30. 为什么说同步阻塞会把 `gather` 变成假并行？为什么不先卸载，框架墙钟也触发不了？

---

### 6.2 已实现 vs 未实现（分层对照表）

#### OpenHarness Runtime 层（复用，非你实现）

| 能力 | 状态 | 说明 |
|---|---|---|
| 六步工具管道（Hook/Schema/Permission/execute） | ✅ 复用 | QueryEngine 统一承载 |
| PRE_TOOL_USE 执行前阻断 | ✅ 复用+验证 | Climate Hook 场景硬断言 |
| Permission 模式 + 敏感路径 | ✅ 复用+扩展 | 含 `.cdsapirc` |
| 框架层 Pydantic 校验 | ✅ 复用 | execute 前 `model_validate` |
| max_turns 推理预算 | ✅ 复用 | 默认构造器 8，Eval 可配置 200 |
| Memory / Compact | ✅ 复用 | 会话连续；不承载业务真相 |
| 原子写 / 文件锁 | ✅ 复用 | Climate Repository 基于此 |
| dry-run 配置预检 | ✅ 复用 | 减少「配置错导致整链失败」 |
| 备用模型网关 + 自动降权 | ❌/弱 | 可换 provider/profile，无验证过的 failover 策略 |
| POST Hook 回滚已执行工具 | ❌ | SPEC 明确 POST 不能当回滚边界 |
| QueryEngine 全局工具超时 | ❌ | `await tool.execute` 死等；`settings.timeout` 只管模型 HTTP |
| 单工具自管超时（如 bash） | ✅ 复用 | 子进程 `wait_for`；**不覆盖**同步 CDS `retrieve()` |
| 单工具串行 / 多工具 `gather` | ✅ 复用 | 只按个数分流，不按只读/写入分流 |

#### ClimWorkflow 领域层（扩展）

| 能力 | 状态 | 说明 |
|---|---|---|
| 结构化错误码 + `retryable` | ✅ | 25+ 稳定码，统一 envelope |
| CDS 有界 backoff + ≤3 候选 + 显式 fallback | ✅ | 可审计，不静默伪造 |
| RunContext / WAL / 中断恢复 / 幂等 | ✅ | 业务可续跑 |
| DAG / 状态机防跳步 | ✅ | 非法调用拒绝 |
| 元数据 catalog + 产物 validate（G5） | ✅ | 执行前/后质量门禁 |
| 独立熔断器组件 | ❌ | 仅有重试上限等价行为 |
| 生产 HITL / 线上质量告警 | ❌/弱 | Permission 有概念，无完整工单流 |
| acquire 卸载 + retrieve 墙钟 + `.part` 稳定发布 | ✅ | Day 21：`to_thread`、`CLIMATE_EXTERNAL_TIMEOUT`；不改 QueryEngine |
| inspect/plot 子进程隔离（Windows TUI） | ✅ | 见第 9.11 节：HDF5 GIL、TkAgg；真实 CDS 冷启动已跑通 |

---

### 6.3 优先准备的参考答案

#### Q1. 外部工具调用失败后，如何做容错与降级？（两层版）

> **OpenHarness 层（执行壳）：**  
> 工具失败先过统一管道。`PRE_TOOL_USE` Hook 可在 execute 前阻断；Pydantic 在执行前校验参数；Permission 拒绝越权或敏感路径访问。失败以 ToolResult 回填模型，进入下一轮 ReAct，而不是进程崩溃。  
> **ClimWorkflow 层（业务语义）：**  
> ToolResult 内是结构化 `code/retryable/details`。确定性错误（schema、DAG、元数据、路径）不重试；CDS timeout/429 在同一 step 内最多 3 次 backoff；仍失败可试 ≤3 合法候选；只有显式 `allow_sample_fallback` 才 sample，并写审计字段。  
> **合起来：**Runtime 保证「安全地调用与回传」；领域层保证「该不该重试、能不能降级、能不能算成功」。

#### Q2. Agent 异常时如何保持连续运行？（两层版）

> - **Runtime：**ReAct 循环 + `max_turns` 预算；用尽抛 `MaxTurnsExceeded`，可开新会话；Memory/Compact 让对话还能继续。  
> - **ClimWorkflow：**权威进度在 RunContext；新会话先 `climate_read_context`，从磁盘续跑。  
> - **基础设施：**原子写/文件锁避免 Context 半写；WAL 处理 active-run 事务中断。  
> **连续** = 会话可继续 + 业务可恢复，不是无限重试。

#### Q3. OpenHarness 在健壮性里具体做了什么？（Runtime 专答）

> 1）**执行前治理：**Hook、Pydantic、Permission 三道闸，把大量失败拦在 execute 之前。  
> 2）**推理预算：**`max_turns` 防止 Agent 死循环耗 token。  
> 3）**权限分级：**plan 模式可阻断写操作，相当于一种「降级到需确认」。  
> 4）**会话治理：**Compact/Memory 在上下文爆炸后仍能对话。  
> 5）**工程原语：**原子写、文件锁供领域层做持久化恢复。  
> 6）**启动预检：**dry-run 提前发现配置/鉴权问题。  
> 我不把这些说成自己实现的 Runtime，但 ClimWorkflow 是**建立在这些能力之上**做领域容错。

#### Q4. PRE Hook 和 POST Hook 在容错上差在哪？

> **PRE_TOOL_USE：**在 execute 前阻断，Context 和文件系统零变化——适合输出路径 guard、危险写拦截。  
> **POST_TOOL_USE：**副作用已发生，**不能当回滚边界**（SPEC DEC-006）。  
> 所以 Climate 的 Hook guard 必须用 PRE；健壮性设计要假设「执行后无法撤销」。

#### Q5. max_turns 用尽和 ClimWorkflow 恢复怎么衔接？

> Runtime 停止当前 ReAct 循环；**不猜测**业务状态。  
> 用户新开会话或继续输入时，Skill 要求先 `climate_read_context`，从 RunContext 看哪些 step 已成功，再决定 acquire/plot/report。  
> 这就是「推理预算在 Runtime，业务真相在 Context」的配合。

#### Q6. 模型 API 成功但参数/顺序错了，两层如何发现？

> **Runtime 层：**Pydantic 拦截明显非法参数；Permission 拦截越权路径。  
> **领域层：**DAG/状态机拦截跳步；元数据 catalog 拦截非法 CDS 请求；validate 拦截产物不合格；Eval 硬断言验证最终 completed 与 artifact。  
> 所以不只看模型接口成功率，还看 **Context 最终状态 + 规则校验**。

#### Q7. 描述完整失败链路（OpenHarness + Climate）

```text
模型 tool-call
  → OpenHarness PRE Hook（可阻断，execute=0）
  → Pydantic model_validate（确定性参数错误）
  → PermissionChecker（越权/敏感路径）
  → Climate tool execute
      → 元数据/catalog（确定性）
      → CDS：backoff → 候选 → 可选显式 fallback
      → 状态机/DAG/幂等/原子写
  → ToolResult(envelope) 回填
  → 模型再推理；或 max_turns 用尽
  → 若新会话：read_context 从 RunContext 续跑
```

#### Q8. 健壮性上 OpenHarness 做了、ClimWorkflow 没做的？

> OpenHarness 提供通用执行壳，但**不替你做**：CDS 科学请求校验、业务 DAG、WAL 业务事务、显式 fallback 审计、offline 硬断言。  
> 也没做：验证过的备用模型 failover、生产熔断器、完整 HITL——这些是 Runtime 可扩展、项目未落地的部分。  
> 另：**没有全局工具超时**。工具挂起时 loop 会冻住；Day 21 在气候下载侧用 `to_thread` + 墙钟补上，详见第 9 节。

---

### 6.4 回答策略

1. **任何健壮性问题都先分两层：**Runtime 执行壳 + 领域业务层。  
2. **OpenHarness 用「复用/验证过」表述**，不说成自己写的 QueryEngine。  
3. **用 CDS 例子串起** Hook → Schema → Permission → backoff → fallback → read_context。  
4. **主动说 POST Hook 不能回滚**，体现读过 Runtime 边界。  
5. **未做项诚实列出**，并说明 Runtime 已预留什么、领域层还缺什么。  
6. **被追问「工具卡住怎么办」**：先说这是 Runtime 缺口 + 领域适配缺陷，不是 QueryEngine 把成功算失败；展开第 9 节，不要改口成「我改了 OpenHarness 循环」。

### 6.5 30 秒缩短版（两层健壮性）

> 健壮性分两层：OpenHarness 提供工具循环里的 Hook、Pydantic、Permission、max_turns、Memory/Compact 和原子写原语，保证安全执行和推理预算；ClimWorkflow 在其上做了 RunContext 持久化、状态机防跳步、CDS 有界重试与显式降级、幂等和中断恢复。Runtime 管怎么调，领域层管算不算成功、能不能续跑。还没做完整熔断和备用模型自动降权，但失败不会静默当成功。

---

## 7. 中断恢复：read_context 与真正续跑（本项目高发题）

> 高频追问：「模型调了 `climate_read_context`，JSON 进了上下文窗口，恢复就完成了吗？」  
> **推荐口径：没有。读 Context 只恢复处境感知；真正续跑发生在下一次写工具进入 `_prepare_mutation` 之后。**

### 7.0 先定口径（必背）

> `read_context` 是只读观察：从磁盘取出有界、脱敏的进度视图，作为 ToolResult 回填对话，供模型 Reason。它不改 step、不重跑下载、不补 WAL。  
> 真正把任务接着做下去的，是之后那次合法的业务写工具（如 `climate_acquire_data`）：再次读盘、修 WAL/中断 step、按状态机重试、写产物。  
> **窗口里的 JSON 不是权威源。** 写工具不信任聊天记录，仍以 `.climate/` 的 RunContext 为准。

两层对照：

| 层 | 谁做 | 恢复的是什么 | 磁盘会不会往前走 |
|---|---|---|---|
| **认知恢复（软）** | `climate_read_context` | 模型知道 `run_id`、哪步成功/失败、产物路径 | 否 |
| **执行恢复（硬）** | 写工具入口 `_prepare_mutation` + 该 step 的业务逻辑 | WAL、残留 `running`、从失败 step 重试 | 是（仅当该步合法） |

Skill 要求 Compact/重启后先 `read_context`，这是软引导。硬约束是：调错工具不能假成功；不调工具则本轮结束、磁盘冻结。

---

### 7.1 本项目最可能被问到的问题清单

1. `read_context` 把 JSON 放进窗口，算不算已经恢复？
2. 恢复时为什么不把 `.nc` / 全表塞进 prompt？
3. 读完 Context 后模型仍跳步（该 acquire 却调了 init / plan / inspect / plot）怎么办？
4. 读完 Context 后模型不返回任何工具调用怎么办？
5. `_prepare_mutation` 是什么？为什么说中断修复发生在写工具入口？
6. Skill 规定先 `read_context`，模型不遵守有没有硬拦截器？
7. 另起一次 `climate_init_workflow` 算不算把旧 run 续上？

---

### 7.2 主路径：观察 ≠ 续跑

场景：进程在 `acquire` 下载到一半时崩溃。磁盘上 run=`running`，acquire step 曾是 `running`（下次写入口会打成 `failed` + `CLIMATE_INTERRUPTED`），没有正式 dataset。

```text
1. 模型调 climate_read_context          ← 只观察
   ToolResult：status、steps、artifacts、last_error
   ↓
   这段 JSON 进入上下文，供 Reason 用
   ↓
2. 模型根据视图决定下一步，例如：
   climate_acquire_data(step_id="acquire", mode="cds", ...)
   ↓
3. 这个写工具才真正「接着干」：
   读磁盘 Context（不信任聊天里那份 JSON）
   做 WAL / 中断 step 恢复
   按状态机从失败 step 重试
   下载、写产物、更新 version
```

`read_context` 默认不带 events；`include_events=true` 时最多最近 `event_limit` 条（默认 100，上限 1000）。返回的是进度表，不是 ERA5 文件。后续 acquire/inspect/plot 按 artifact 相对路径自己读文件，不经过 LLM 上下文。

---

### 7.3 读完之后的几种偏离

`read_context` 不能保证模型下一步一定调对。系统保证的是：**调错了不能把任务做成假成功。**

```text
read_context     → 让模型看见进度（软）
调错工具         → DAG/状态机拒绝假成功；错误回填，可再试（硬）
另起 init        → 可能开新 run，不是续跑（产品边界）
不调工具         → 本轮结束，磁盘冻结，等用户下一句（硬缺口：不作为）
真正续跑         → 某次写工具进入 _prepare_mutation 修盘，再执行该 step
```

#### Q1. 该调 `acquire`，却向前跳到 inspect / plot / report？

仍会先走写工具入口 `_prepare_mutation`（顺带把残留 `running` 打成 `INTERRUPTED`），再做依赖检查。前置 acquire 不是 `succeeded` → `CLIMATE_DEPENDENCY_NOT_READY`（plot 还可能是「没有已检查的 dataset」）。不写产物，run 不会 `completed`。错误回填后 ReAct 可以再调 `climate_acquire_data`。

#### Q2. 向后重跑 `climate_plan_steps`？

plan 也走 `_prepare_mutation`。中断恢复后 acquire 已是 `failed`，不再全是 `pending`。`accept_plan`：**只要有业务 step 已开始（含 failed），不得整表替换 plan** → `CLIMATE_INVALID_TRANSITION`。原 DAG 不被幻觉 plan 覆盖。

#### Q3. 向后调 `climate_init_workflow`？

init **不走** `_prepare_mutation`，语义是「建新 run / 显式 resume」，不是「当前 run 的下一步」。

| 模型怎么调 init | 系统会怎样 |
|---|---|
| 带上**已有** `run_id` | `CLIMATE_RUN_EXISTS`，旧进度不动 |
| `resume_run_id` = 当前已是 active | 「已是 active」，拒绝 |
| **不带 run_id**（或给一个新 UUID） | **会新建空 run 并切走 active** |

最后一种不是跳步被拒，而是另开一单。旧 run 仍在磁盘 `run_ids` 里，要接回必须显式 resume。面试不要说成「任何乱调都会被状态机挡死」。

#### Q4. 读完后模型不返回任何工具调用？

OpenHarness `run_query`：assistant 消息没有 `tool_uses` 就认为本轮说完（`stop_reason: tool_uses_empty`），不再 execute Climate 工具。磁盘原样冻结。Skill 不能强迫必须再调工具。`max_turns` 管的是「调工具太多」，不管「过早停」。

> 状态机防的是**错误副作用**，不是防**不作为**。没有「run 未完成禁止收工」的 HITL。续跑依赖新的用户输入 + 模型再次 Tool Calling。

#### Q5. `_prepare_mutation` 是什么？

不是模型能调的工具，而是 `plan` / `acquire` / `inspect` / `plot` / `report` 干正事之前共用的内部准备函数（写工具入口）：

1. `recover_active_run_transactions()`：按文件事实补 WAL 或回滚，避免两个 active run。
2. 解析 `run_id`（缺省用 index 的 `active_run_id`），`load_run` 磁盘。
3. `recover_interrupted_steps()`：残留 `running` → `failed` + `CLIMATE_INTERRUPTED`，清理 `.part`。意图 ≠ 完成。

因此：修复不是 `read_context` 做的，也不是「JSON 进窗口就 magically resume」。只要下一次会改状态的业务工具被调到（哪怕调错成 inspect），一进门先修磁盘，再决定这枪业务允不允许打。

| 调用 | 走不走 `_prepare_mutation` | 会不会修中断 step |
|---|---|---|
| `read_context` / `validate` | 否 | 否（有 WAL 只报 `CLIMATE_RECOVERY_REQUIRED`） |
| `plan` / `acquire` / `inspect` / `plot` / `report` | 是 | 是 |
| `init` | 否（只恢复 active-run WAL） | 不处理旧 run 里残留的 `running` step |

---

### 7.4 总流程图

（预览用文本图，避免 Mermaid 渲染失败导致后文空白。）

```text
Compact / 进程重启
        |
        v
climate_read_context  ----只读观察----  .climate/RunContext
        |
        v
ToolResult 进入窗口（供 Reason）
        |
        v
        +----------- 模型下一步调谁？ -----------+
        |                                      |
调对 acquire                    向前跳步 inspect/plot/report
        |                                      |
        v                                      v
climate_acquire_data              仍进 _prepare_mutation（顺带修盘）
        |                                      |
        v                                      v
写工具入口 _prepare_mutation          DAG 拒绝 DEPENDENCY_NOT_READY
        |                                      |
        v                                      |
修 WAL + running→INTERRUPTED                  |
        |                                      |
        v                                      |
按状态机重试失败 step                           |
        |                                      |
        v                                      |
下载 / 写产物 / version+1                      |
        |                                      |
        v                                      |
   任务真正续跑  --写回-->  RunContext          |
                                               |
        +-- 向后重 plan -----------------------+
        |  climate_plan_steps
        |  仍进 _prepare_mutation
        |  已开始则 INVALID_TRANSITION
        |
        +-- 另起 init（新 id / 不带 id）------+
        |  新 run 成为 active，不是续跑
        |
        +-- init 已有 run_id -----------------+
        |  CLIMATE_RUN_EXISTS
        |
        +-- 只回文本、不调工具 ---------------+
           本轮结束，磁盘冻结，等用户下一句

跳步 / 重 plan / 重复 init 失败后：错误回填，模型可再试。
```

读图顺序：

1. **上排（软）**：Compact/重启 → `read_context` → JSON 进窗口。磁盘不会因此往前走。
2. **菱形**：分叉发生在「模型决定下一个 tool-call」，不是发生在读盘。
3. **绿路（硬续跑）**：调对写工具才进 `_prepare_mutation`，再读磁盘、修盘、重试、写产物。
4. **红路（硬拒绝）**：向前跳步或向后再 plan：可能顺带修盘，但业务被拒；错误回填后可再选。
5. **橙路（边界 / 缺口）**：另起 init 可能开新 run；不调工具则循环结束、磁盘冻结。

---

### 7.5 45 秒口述版

> Compact 或重启后，权威状态在磁盘 RunContext，不在聊天里。Skill 让模型先 `climate_read_context`，这只是把进度表放进窗口，让它知道调谁。真正续跑是下一次写工具：入口 `_prepare_mutation` 修 WAL、把残留 running 打成 INTERRUPTED，再按状态机重试。如果它跳步去 plot，依赖检查会拒绝，不会假成功；如果它不调工具，本轮结束、磁盘冻结，要等用户下一句。另起一个新 init 是开新单，不是把旧 run 续上。

### 7.6 回答注意

1. 不要说「调用 `read_context` = 恢复完成」。
2. 不要说「任何乱调都会被挡死」——另起 init 可以切走 active。
3. 主动承认：先 `read_context` 是 Skill 软约束，没有 Compact 后强制下一工具必须是 read 的拦截器；写工具本来就从磁盘 load。
4. 主动承认：不作为（只回文本）没有硬闸，这是相对生产 HITL 的缺口。
5. 挂钩第 5 节 Compact/Memory、第 6 节健壮性：Memory 管聊下去，RunContext + 写工具管做完。

---

## 8. WAL 与幂等流程图

> WAL 管 **active run 的两个 JSON**（Context + index）中途崩溃。  
> 幂等管 **同一个 step_id 被再打一枪**（同 hash 重放 / 不同 hash 冲突）。  
> 二者都不是第 7 节的「聊天恢复」；中断后续跑时两者常叠在一起。

### 8.1 WAL：创建 active run 与按文件事实恢复

`climate_init_workflow` 新建 run 时必须同时改 `context.json` 和 `index.json`。先写 marker，再改文件；崩溃后看「新 Context 是否有效」，不听 marker 嘴上的 bool。只读工具只报 `CLIMATE_RECOVERY_REQUIRED`，不修盘。

```text
[正常落盘]

init 新建 run
  -> 持锁 workspace 再 run
  -> 写 WAL marker
  -> 原子写 context.json          <-- 崩溃点
  -> marker: context 已写         <-- 崩溃点
  -> 原子写 index.json            <-- 崩溃点
  -> marker: index 已写           <-- 崩溃点
  -> 删除 marker
  -> active 切换完成


[崩溃后]

下次写工具入口 recover_active_run_transactions
  -> 新 Context 有效？
        是：补写 index 指向新 run -> 删 marker -> 仍只有一个 active
        否：index 指回 old_active -> 删 marker -> 仍只有一个 active

只读 read_context / validate
  -> 发现 marker 则返回 RECOVERY_REQUIRED（不修盘）
```

读图：

1. 绿路：marker → Context → 标已写 → index → 标已写 → 删 marker。  
2. 自测「Context 写完、index 没写就崩」：新 Context 有效 → 补 index → 删 marker。  
3. 刚写完 marker 就崩：无有效 Context → 指回旧 active。  
4. `read_context` 发现 marker 只报错，不走补写。

WAL **不管** acquire 下载中途的 `.part`；那是 `running` → `CLIMATE_INTERRUPTED`。

### 8.2 幂等：同一 acquire step 的三种再调用

规范化输入做 SHA-256。`replay_or_start_step` 按 **当前 status + 新 hash** 分支。一次成功的 acquire 会写盘两次：`start`（version+1）再 `success`（version+1）。同输入重放不加 version。

```text
climate_acquire_data
  -> canonical_input_hash
  -> 看 step.status

     pending（第一次）
       -> start：status=running，钉上 hash，version+1
       -> 写 .part
       -> fsync + os.replace 正式文件
       -> success：记 artifact，version+1
       -> 产物成为权威

     succeeded 且 hash 相同
       -> 幂等重放：返回旧 result，不写盘，version 不变

     succeeded 且 hash 不同
       -> IDEMPOTENCY_CONFLICT，不覆盖已发布产物

     failed（上次失败）
       -> retry：再进 running，允许换 hash
       -> 再走 .part 发布
```

读图：

1. **第一次 sample：** pending → running（v=3）→ `.part` 发布 → succeeded（v=4）。  
2. **同参数再调：** 重放，sample.csv 不动。ReAct 重复点同一按钮是安全的。  
3. **已成功再改 cds：** 冲突，防 inspect 已看过的数据被偷换。  
4. **失败后再换参：** 走 retry，不是冲突。

与第 7 节：崩溃后续跑若再打已成功的 acquire，同 hash 走重放，不同 hash 走冲突。

---

## 9. 工具挂起、假并行与 to_thread（本项目高发题）

> 现场：CDS 网页约 25 秒已 Successful，本地 `.part` 已是合法 NetCDF，TUI 里 `climate_acquire_data` 一直 Running。  
> **推荐口径：QueryEngine 没有把成功算成失败；工具不返回，循环就一直 `await`。这是 Runtime 缺口 + 气候工具把同步 `retrieve()` 塞进事件循环。Day 21 修在 ClimWorkflow，不改 `query.py`。**

### 9.0 先定口径（必背）

> OpenHarness 的工具契约是：`execute` 必须返回 `ToolResult`。超时、杀进程、要不要卸到线程，**默认交给各工具自己实现**，QueryEngine 只死等。  
> `bash` 自己写了 `timeout_seconds`；CDS `retrieve()` 是同步阻塞，漏写超时就会把整个 Agent loop 冻住。  
> `settings.timeout` 管的是**调模型 HTTP**，不管工具。`max_turns` 只在一个回合**已经结束**后计数，卡在工具内部时加不上。  
> Day 21：`asyncio.to_thread` 把下载卸出事件循环 + retrieve 墙钟 + `.part` 稳定则发布。禁止把「给所有工具套 `wait_for`」说成已做。

### 9.1 本项目最可能被问到的问题清单

1. 工具超时挂起、不报错也不返回，Agent loop 会不会卡死？
2. OpenHarness 有没有统一的工具超时？`settings.timeout` / `max_turns` 能不能救？
3. 这算框架严重 bug 吗？还是把灵活性交给了工具？
4. 有没有必要在框架做一层统一兜底？
5. 单工具和多工具执行策略一样吗？有什么问题？
6. 同步阻塞为什么会把并行变成假并行？
7. 为什么说「同步 IO 必须自己卸载，否则全局超时也触发不了」？
8. 你们怎么修的？为什么不改 QueryEngine？

### 9.2 现场拆成两句话

1. **网页 Successful ≠ Agent 成功。** 完成条件曾绑在 `retrieve()` 返回上，而不是合法 `.part` 已落盘。  
2. **TUI 和工具共用一根事件循环。** `async execute` 里直接调同步 `retrieve()`，循环线程被占死：界面不刷、stop 进不去、下一回合走不到。

这不是 OpenHarness 把成功判失败。`QueryEngine._execute_tool_call` 只做 `await tool.execute(...)`，工具没回来，循环就停在这一行。

### 9.3 形象例子（店里只有一个店员）

把事件循环想成：**店里只有你一个店员。** 点单、收银、看计时器、接下一位，全靠你。

客人点了「去仓库取一箱货」（CDS 下载）。

- **错误做法：**你自己钻进仓库，把箱子搬完才出来。这就是在 `async def execute` 里直接调同步 `retrieve()`。柜台没人：TUI 一直 Running，stop 没人处理。
- **正确做法：**喊后厨去搬（`asyncio.to_thread`），你回到柜台。后厨在另一根线上搬（操作系统线程），你还能收银、接下一位、看手表。

**假并行：**这时第二位客人要「看说明书」（同回合的 `inspect`）。框架写了 `asyncio.gather(acquire, inspect)`，本意是两桌一起。可你人还在仓库，第二位连开口机会都没有。菜单写着并行，实际是「acquire 不走完，谁都别想动」。

**超时为什么是空的：**你在手腕上设了 3 分钟闹钟（`asyncio.wait_for(..., timeout=180)`）。闹钟响了要**站在柜台的你**去取消。你人在仓库埋头搬，听不见。3 分钟到了，闹钟在空柜台响，没人管。所以只给 QueryEngine 加墙钟、不把同步 IO 挪走，超时纸面上有、运行时触发不了。

你们那天：仓库其实 25 秒就已经搬完了（网页 Successful，`.part` 在磁盘上），但你还在仓库门口等「门什么时候关上」（`retrieve()` 收尾、连接不关）。柜台一直空着，所以既不能收工成功，也不能报超时。

Day 21 两步对应两个角色：

1. **`to_thread`：**人回到柜台（循环能转）。  
2. **墙钟 + `.part` 稳定就发布：**后厨也别无限干；文件已经合法就收工，否则到点报 `CLIMATE_EXTERNAL_TIMEOUT`。

没有第 1 步，第 2 步的闹钟也可能响不了；没有第 2 步，后厨可能一直不回来，Agent 还是等。

### 9.4 假并行：技术定义（可接例子后背）

asyncio 的「并行」不是雇了两个厨师，而是**一根循环线程上的协作**：谁碰到 `await` 就让出，别人才能跑。`gather(A, B)` 的前提是 A、B 都会让出。

`cdsapi.retrieve()` 是普通同步函数，下载完之前不 `await`。循环线程停在这一行：

- **单工具：**TUI、stop、下一回合全冻住。  
- **多工具：**`gather` 只调度协程。有一个占死循环，其它工具也跑不动。看起来并行，其实全家排队。

`to_thread` 把 `retrieve()` 丢到线程池：循环重新能处理 `await`，这时 `gather` 才是真交错，超时和 stop 才有机会被处理。

### 9.5 为什么不卸载，框架超时等于没写

`wait_for` 到期要取消，也是事件循环到点后插入一条回调。循环被同步 `retrieve()` 堵死，回调排不进去，180 秒到了也不触发。

分层必须是：

1. **先卸载**（`to_thread` / 子进程），循环还能转；  
2. **再墙钟**，到期把这次 tool call 收成错误；  
3. **工具内尽量真正停掉工作**（杀进程、关连接），否则会漏线程/漏连接。

只做第 2 步、不做第 1 步：全局兜底是空的。这就是「同步 IO 必须自己卸载」。

### 9.6 OpenHarness：单工具 vs 多工具

**有策略差异，但分界只看个数，不看是否安全。** 超时两边都没有。

| | 同一回合 1 个 tool call | 同一回合 ≥2 个 |
|---|---|---|
| 执行 | 串行 `await _execute_tool_call` | `asyncio.gather(..., return_exceptions=True)` |
| 事件 | Started → 执行 → 马上 Completed（TUI 能边跑边刷） | 先发完全部 Started，**全部结束**再批量 Completed |
| 失败 | try/except 收成错误 ToolResult | `return_exceptions=True`：一个抛异常不能取消兄弟协程，否则下一轮缺 `tool_result`，Anthropic 会拒（有回归测试） |
| 超时 | 无 | 无；还要等最慢的那个，挂起被放大 |
| 共用 | 都进同一个 `_execute_tool_call`（Hook / Schema / Permission / `await execute`） | 同左 |

系统提示鼓励模型把独立调用放进同一条回复。框架**不按只读/写入分流**。两个 `grep` 并行是对的；`climate_acquire_data` 和 `inspect` 同回合、或两次写入同一文件，就会抢状态。气候层靠 `workspace.lock` 自保，框架不管。

实现上还有：完成事件要等最慢的（UI 问题）；两个写入同时 `permission_prompt` 可能打架（`full_auto` 下不明显）。这些不是「不该并发」，而是缺安全分类和缺「谁都必须能结束」。

### 9.7 这是严重 bug 吗？要不要框架统一兜底？

**不算严重 bug，是 Runtime 缺口。** Bug 是「按契约做错了」（成功算失败、取消了不杀进程）。这里循环行为是对的：工具说会 `await` 完再返回，框架就等到返回。

可以理解成：**框架把「这次调用怎么结束」交给工具。** 换来的是灵活（子进程、HTTP、REPL 超时语义不同）；代价是某个工具漏写超时，loop 只能干等。不是「框架帮你兜底、工具可覆盖」，而是「框架只等结果，兜底由工具作者负责」。

**有必要做一层很薄的框架兜底，但不要替代工具自己的超时。**

| 层 | 谁做 | 解决什么 |
|---|---|---|
| 工具内超时 | 各工具 | 精确停掉工作（杀进程、关连接），错误码准确 |
| 框架墙钟 | QueryEngine（OpenHarness **当前没有**） | 保证 loop 一定能回来 |
| 隔离 | 同步 IO 必须 `to_thread` / 子进程 | 循环不被占死；否则墙钟也触发不了 |

对 ClimWorkflow：先在 `climate_acquire_data` 做超时和 `to_thread`（Day 21）。给 OpenHarness 加全局墙钟是另一份 Runtime 贡献，不是这次取数挂起的最小修复，也违反「`query.py` 无 Climate diff」。

### 9.8 那几项该修？（建议口径）

结合这次卡死和 Day 21 边界：

| 问题 | 现在修吗 | 理由 |
|---|---|---|
| 同步阻塞 → 假并行 / TUI 冻住 | **立刻修（领域层）** | 根因。CDS-007：`to_thread` 仅卸下载 |
| 挂起被放大（无墙钟） | **立刻修（领域层）** | CDS-006 墙钟 + CDS-008 `.part` 稳定则发布 |
| 并发条件太粗（只看个数） | **先不做** | 主路径本就是 acquire→inspect→plot→report；写入已有 `workspace.lock`；改 QueryEngine 越界 |
| 完成事件等最慢的 | **先不做** | 只影响 TUI 刷新早晚 |
| 权限确认打架 | **先不做** | 评测/TUI 常用 `full_auto` |

以后若给 OpenHarness 提 PR：先契约「同步 IO 必须自己卸载」→ 再薄墙钟 → 再写入串行/只读并行、`permission_prompt` 加锁。

### 9.9 参考答案

#### Q1. 工具挂起，loop 会卡死吗？框架有超时吗？（完整版）

> **会卡死。** OpenHarness QueryEngine 对 `tool.execute` 是死等，没有统一工具超时。`bash` 等个别工具自己写了 `timeout_seconds`；`settings.timeout` 管模型 HTTP；`max_turns` 管已结束的回合。气候下载走同步 `retrieve()`，等于绕开这些保护。TUI stop 在循环被占满时也可能进不去。  
> 这不是循环算错成功/失败，是契约不完整：框架默认工具会结束。Day 21 在 `climate_acquire_data` 里 `to_thread` + 墙钟，超时映射 `CLIMATE_EXTERNAL_TIMEOUT`，合法 `.part` 稳定则发布；**不改 QueryEngine**。

#### Q2. 假并行和「必须卸载」用 60 秒怎么讲？

> 店里只有一个店员，就是事件循环。自己进仓库搬货，柜台没人，第二位客人（另一个 tool）也没人理——`gather` 看起来并行，其实是假的。超时闹钟戴在店员手腕上，人在仓库就听不见，所以只给框架加 `wait_for` 没用。要把搬货交给后厨（`to_thread`），店员回到柜台，闹钟和下一单才有意义。我们网页已经 Successful、`.part` 已在磁盘，卡的是店员还堵在仓库门口等关门。

#### 30 秒缩短版

> OpenHarness 没有全局工具超时，loop 会干等 `execute`。灵活性在各工具：`bash` 有超时，CDS 同步 `retrieve` 没有，就会冻住 TUI。asyncio 并行靠 `await` 让出；同步阻塞占死循环，`gather` 是假并行，框架 `wait_for` 也触发不了。我们用 `to_thread` 卸下载，再做墙钟和 `.part` 稳定发布，不改 QueryEngine。

### 9.10 回答注意

1. **说「复用 QueryEngine」**，不要说「我给 OpenHarness 加了全局超时」。  
2. **主动分层：**框架缺口（无统一超时、无强制卸载）vs 领域缺陷（同步 `retrieve` 塞进 `async execute`、完成条件绑客户端返回）。  
3. **别把 `settings.timeout` 说成工具超时。**  
4. **别承诺改了 cdsapi / 改了 `query.py`。**  
5. **被问「为何不框架统一 to_thread」：**不同类型工具 IO 模型不同；ARCH 要求 Climate 不改 QueryEngine；最小修复在下载适配层。  
6. **挂钩：**第 6 节健壮性（执行壳 vs 业务真相）、第 2 节 CDS 只对 timeout/429 有界重试（工具得先能返回错误，重试才存在）、第 7 节中断恢复（挂起留下 `running` + `.part`，resume 只读，真正续跑仍走写工具）。  
7. **被问「Day 21 之后为什么 TUI 还卡」：**见 9.11。`to_thread` 挡不住占 GIL 的 C 扩展；Windows 上还不能对仍打开的 `.part` 做 `os.replace` / `Dataset(path)`。

### 9.11 真 CDS TUI：卡点从 acquire 挪到 inspect/plot 之后怎么收的

Day 21 之后，网页 Successful、正式 `.nc` 已经在磁盘上，TUI 仍可能一直 Running。根因不是「循环又把成功判失败」，而是 **Windows 上科学库会把整进程冻住，线程超时等不到**：

1. **发布阶段不能打开 NetCDF。** retrieve 还握着 `.part` 写句柄时，`os.replace` 会失败；若再对 staging/`dest` 调 `Dataset(path)`，HDF5 占住 GIL，`join(timeout)` 和 `asyncio.to_thread` 都回不来，context 写不出 `succeeded`。做法：共享读出字节、只校验 magic/体积、staging 原子改名；正式文件已合法则跳过第二次 retrieve。
2. **inspect 不能在 TUI 进程里开 HDF5。** 同一进程里哪怕把 `Dataset` 丢进线程，GIL 仍被 C 扩展拿走，8 秒超时等于没写。做法：父进程不 `import netCDF4`，把文件拷到临时路径，子进程解析；超时杀子进程。Windows 默认 GBK 会把中文错误打成非法 UTF-8，子进程必须写 UTF-8 JSON，并关掉句柄继承（TUI/Node 的管道否则会把 HDF5 再卡死）。
3. **plot 不能在 TUI 线程里 `import matplotlib`。** Windows 默认 TkAgg，再放到 `to_thread` 里会和终端抢界面线程，inspect 通了画图又转圈。做法：子进程强制 `MPLBACKEND=Agg`；超时则退回已有 SVG 路径。

现场验证（2026-09-19）：自然语言冷启动新 run `f11d0e28-…`，一次会话 `init → plan → acquire(cds) → inspect → plot(PNG) → report → validate`，status=`completed`。上一份 `b4e8cd64-…` 是修隔离前分段跑通的，不能当成「从未中断的冷启动」。

#### Q3. 为什么卸到线程还是卡？

> 店员把搬货交给后厨（`to_thread`），但后厨用的起重机（netCDF4/HDF5）会把整栋店的电闸拉掉（GIL）。柜台上的闹钟还是响不了。正确隔离是再开一间独立仓库（子进程）：卡死只卡子进程，到点可以杀掉，柜台还在。画图同理，Tk 窗口和 TUI 抢同一个前台，必须 Agg 子进程。

