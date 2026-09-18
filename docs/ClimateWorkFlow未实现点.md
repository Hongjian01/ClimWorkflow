# ClimWorkflow 未实现点与演进建议

**位置：**与 [ClimateWorkFlow面试问题.md](./ClimateWorkFlow面试问题.md) 同目录。  
**来源：**面试深挖（2026-09-04）中核对代码/规格后确认的缺口；并收录简历口径里已标明未做的项。  
**用途：**面试诚实边界、排期取舍；不是 SPEC 变更清单。若要把某条升级为需求，先改 `docs/climate-agent/SPEC.md` 再实现。

---

## 如何读本表

| 字段 | 含义 |
|---|---|
| **重要程度** | 对正确性、可运营性或面试被追问的影响：高 / 中 / 低 |
| **建议优先级** | P0 当前就该补 · P1 下一阶段建议做 · P2 有明确场景再做 · P3 明确不做或长期延后 |
| **建议动作** | 做 / 暂缓 / 不做 |

P0 表示「现在缺了会在真实使用或面试里说不清」，不等于「必须立刻开工」。本仓库 v0.1 目标仍是可恢复工作流，不是人机审批产品。

---

## A. 本次深挖新确认（主路径交互）

### A1. 主路径无业务审查点，首条消息后无法改口

**现状：**Day 23 已落地最小停顿：`climate_plan_steps.confirmed` + 事件 `plan_confirmed`；未确认 `climate_acquire_data` 硬拒绝（`CLIMATE_INVALID_TRANSITION` / `plan_unconfirmed`）。Skill 要求 plan 后展示四步与拟定 mode 并结束本轮。`full_auto` 下模型仍可能自己 `confirmed=true`，硬闸门只保证「没有确认事件就不能下」。

**重要程度：**高（产品期望 vs 实际能力差一截，面试必问「能中途改需求吗」）  
**建议优先级：**P1 剩余：TUI checkpoint UI、第九确认工具、`full_auto` 强制停  
**建议动作：**规格 Day 23 已立且实现 PASS；完整工单 / TUI 按钮另开日

**建议形态：**plan 成功后暂停，把 DAG / 拟定 `mode` 展示给用户，确认或改口后再 acquire。不要做成完整工单系统。规格见 [DAY_23_G2_PLAN_CONFIRM_BEFORE_ACQUIRE.md](../climate-agent/daily/DAY_23_G2_PLAN_CONFIRM_BEFORE_ACQUIRE.md)。

**未做时的口径：**防跳步/幂等/WAL 管执行正确，不管人机改需求；改数据源应新开 run，或杀进程后走恢复（若 acquire 已 start 则不能再整表换 plan）。

---

### A2. 「全 pending 可重 plan」只是状态机窗口，主路径用不上

**现状：**`accept_plan` 允许在所有业务 step 仍为 `pending` 时整表替换 DAG。Day 23 闸门让该窗口对用户可达：确认前或确认后但尚未 start acquire 都可以整表换 plan；换 plan 后旧 `plan_confirmed` 作废。模型若在同轮连点确认+acquire，窗口仍可能立刻关掉。

**重要程度：**中（容易把代码能力讲成已支持的改需求功能）  
**建议优先级：**P1（与 A1 捆绑；TUI 入口另开日）  
**建议动作：**规格已立；P0 闸门 PASS；不要做成 insert step（A3）

**说明：**没有 A1 的停顿，A2 对用户不可达。有 A1 之后，改口才走一次完整的 `climate_plan_steps` 新数组，而不是 insert 单个 step。

---

### A3. 没有「往 DAG 里插入一个 step」的补丁 API

**现状：**只能提交 4～32 条的完整 `steps` 整表替换；不能 `append(acquire-cds)`。

**重要程度：**低（有整表替换就够 MVP）  
**建议优先级：**P3  
**建议动作：**不做

整表替换更易校验四类动作、无环、report 可达 inspect/plot。补丁式加边会把 DAG 校验拆碎。

---

### A4. Compact 后续跑「必须先 read_context」只有 Skill 软约束

**现状：**没有拦截器规定 Compact 之后下一个工具必须是 `climate_read_context`。写工具本来从磁盘 load；跳步会被状态机拒绝。模型不读也能「碰巧」调对。

**重要程度：**中（恢复叙事要诚实；硬缺口在「猜错」而非「磁盘真相」）  
**建议优先级：**P2  
**建议动作：**暂缓

若做：Eval 加「Compact 后首工具必须为 read_context」硬断言，或 Runtime 在 compact 后注入一条系统提示/强制工具。不要在只读工具里偷偷修 WAL。

---

### A5. 模型不作为：只回文本则本轮结束，磁盘冻结

**现状：**`tool_uses` 为空则循环正常结束（`stop_reason: tool_uses_empty`）。run 未 `completed` 也不会禁止收工。`max_turns` 只管「调太多」，不管「过早停」。

**重要程度：**中（长任务可能口头说完、产物没有）  
**建议优先级：**P2  
**建议动作：**暂缓

若做：run 非终态时拒绝「纯文本收工」，或 CLI 提示「未完成，是否继续」。这是轻量 HITL，不是审批流。

---

### A6. 另起 `init`（新 UUID / 不带 id）会切走 active，不是续跑

**现状：**重复已有 `run_id` 会 `CLIMATE_RUN_EXISTS`；裸 init 会新建并切换 `active_run_id`。旧 run 仍在磁盘，但默认后续工具打到新 run。

**重要程度：**中（误开新单会让人以为旧任务丢了）  
**建议优先级：**P2  
**建议动作：**暂缓

若做：存在未完成 active run 时，裸 init 改为明确错误（需 `resume_run_id` 或 `force_new`），避免幻觉「重新开始」。

---

### A7. Permission `plan` 模式 ≠ 改业务目标 HITL

**现状：**plan 模式约束写工具确认/阻断，不能用来「看完 DAG 再改 sample→CDS」。

**重要程度：**低（名词易混，讲清即可）  
**建议优先级：**P3  
**建议动作：**不做（文档/面试口径即可）

---

## B. 健壮性与运行时（面试文档 6.2 已列，此处给优先级）

### B1. 生产级 HITL / 线上质量告警

**现状：**无工单、无「确认后改 plan」、无完成率/失败码告警。与 A1 同一方向，范围更大。

**重要程度：**高（若对标生产 Agent 平台）  
**建议优先级：**P1（先做 A1 最小停顿；告警更后）  
**建议动作：**暂缓完整工单；告警 P2

个人项目/实习演示：A1 一条 checkpoint 比整套 HITL 更划算。

---

### B2. 独立熔断器

**现状：**CDS 仅超时/限流最多 3 次退避；无「连续失败后一段时间内不再打 CDS」。

**重要程度：**中（配额与打爆外部 API）  
**建议优先级：**P2  
**建议动作：**暂缓

真实持续调用 CDS 再做。当前有界重试 + 显式 fallback 对 MVP 足够。

---

### B3. 备用模型自动 failover

**现状：**可换 provider/profile，无验证过的自动降权/切换策略。

**重要程度：**中（可用性）  
**建议优先级：**P2  
**建议动作：**暂缓

Runtime 可扩展；领域层不必先做。面试承认「能换模型，没做自动 failover」。

---

### B4. POST_TOOL_USE 回滚已执行工具

**现状：**SPEC DEC-006：POST 只观测，不能当回滚。副作用已发生。

**重要程度：**低（设计如此，不是漏做）  
**建议优先级：**P3  
**建议动作：**不做

继续用 PRE 拦截 + `.part`/WAL/状态机。不要把 POST 讲成事务回滚。

---

## C. 明确非目标（不要当成缺口去补）

| 项 | 重要程度（若被问） | 建议优先级 | 建议动作 |
|---|---|---|---|
| 论文式自由 PLAN-AGENT（IVT/SPI/TC 新 action） | 中（和 G5 边界） | P3 | 不做 |
| 任意 Python/Shell 代码沙箱 | 高（安全） | P3 | 不做 |
| Selenium 抓 CDS 门户 | 低 | P3 | 不做 |
| Climate-Agent-Bench-85 / 论文 Report Score | 中 | P3 | 不做 |
| Multi-Agent 拆 acquire/inspect | 中 | P3 | 暂缓；顺序工作流无收益 |
| 自研用户画像 / 向量长期记忆 | 低 | P3 | 不做；Memory 用 Runtime |
| 通用 DAG 调度器 / Web UI / 分布式队列 | 低 | P3 | 不做 |

---

## 建议落地顺序（若要做）

```text
P1  A1 plan 后最小确认点  →  顺带让 A2 窗口对用户可见
P2  A5 未完成禁止纯文本收工 或 CLI 提醒继续
P2  A6 未完成 run 存在时裸 init 需显式确认
P2  A4 Compact 后首工具 read_context 的 Eval/提示
P2  B2/B3 有真实持续流量或 SLA 再做
P3  其余不做或只改口径
```

面试一句话：

> 执行层（状态机、幂等、WAL、沙箱）是闭环的；缺的是人机停顿和「不作为」门闩。用户改需求不是 bug，是当前单循环产品范围外；要支持就加 plan 后 checkpoint，或对新目标开新 run。
