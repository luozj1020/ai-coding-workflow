# AI Coding Workflow 跨模型信息传递优化规划

## 1. 背景

当前 AI Coding Workflow 通过 Codex、Claude Code、Spark、本地工具和人工审查完成以下循环：

```text
OBSERVE → ROUTE → PLAN → EXECUTE → VERIFY → REVIEW
```

该工作流能够将规划、实现、验证和审查分配给不同能力与成本的模型，但跨模型交接会产生显著的信息成本：

1. 发送模型需要将内部理解序列化为任务卡、报告或结构化结果。
2. 接收模型需要重新读取代码、任务目标和证据，恢复任务上下文。
3. 已经验证或排除的信息可能在下一阶段丢失。
4. 不同模型可能重复调查相同代码、假设和失败原因。
5. Reviewer 可能重新审查已经冻结且未受本轮修改影响的决策。
6. Revision 阶段可能重新发送完整任务，而不是只发送失败差量。
7. 随着任务卡不断补充信息，传输内容逐渐膨胀，进一步增加 token、延迟和审阅成本。

当前优化重点主要是限制任务卡大小、复用上下文、选择合适的 Reviewer 层级和减少无效模型调用。下一阶段需要将工作流从“文档交接系统”升级为“模型无关的状态同步系统”。

------

## 2. 核心问题

当前交接方式近似为：

```text
模型 A 内部理解
    ↓
任务卡 / 报告 / 摘要
    ↓
模型 B 重新读取并重建理解
```

每次交接都会产生四类成本：

# [ C_{\text{handoff}}

C_{\text{serialization}}
+
C_{\text{reconstruction}}
+
C_{\text{rediscovery}}
+
C_{\text{handoff-revision}}
]

其中：

- (C_{\text{serialization}})：发送模型整理任务卡、摘要和报告的成本；
- (C_{\text{reconstruction}})：接收模型重新读取任务和代码的成本；
- (C_{\text{rediscovery}})：接收模型重复发现已有事实的成本；
- (C_{\text{handoff-revision}})：因遗漏、误解或状态过期导致的返工成本。

目前工作流能够记录模型调用、任务卡大小、Review Packet 大小、总耗时和 diff 复用率，但尚未直接测量这些交接成本。

------

## 3. 规划目标

### 3.1 总体目标

将跨模型协作方式从：

```text
完整文本交接
```

逐步改造成：

```text
共享状态 + 增量事件 + 按需上下文 + 可验证回执
```

目标架构：

```text
                    ┌────────────────────┐
Repository / Tools ─▶│ Workflow State Bus │
                    │                    │
                    │ State IR           │
                    │ Event Log          │
                    │ Evidence Objects   │
                    │ Acceptance Graph   │
                    └────────┬───────────┘
                             │
                   Context Broker / Delta
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
           Planner         Builder        Reviewer
              │              │              │
              └──── ACK / State Update ─────┘
```

### 3.2 具体目标

1. 减少重复发送的上下文。
2. 减少接收模型重新读取代码的数量。
3. 保存已经接受的决策和已经排除的假设。
4. Revision 只传递失败项和新证据。
5. Reviewer 只审查状态差量和未覆盖验收项。
6. 在模型执行前检测关键约束是否被正确接收。
7. 将交接成本纳入任务路由和模型选择。
8. 保证所有共享状态都可追溯、可验证、可失效和可恢复。

------

## 4. 非目标

本规划暂不尝试实现以下能力：

1. 不实现闭源模型之间的 hidden-state 映射。
2. 不实现 Codex、Claude 或其他服务模型的 KV-cache 共享。
3. 不依赖模型供应商开放内部激活接口。
4. 不试图完整保存模型的全部推理过程。
5. 不要求所有任务都进入多模型工作流。
6. 不用更大的任务卡替代状态同步机制。
7. 不让模型输出成为仓库事实或测试事实的唯一来源。

长期可以研究跨模型激活映射和公共 latent space，但当前工程重点是构建可用于闭源模型的外部公共状态。

------

## 5. 设计原则

### 5.1 单一事实源

代码、测试、用户约束和工具输出应分别承担不同类型的事实：

| 信息类型   | 主要载体                   |
| ---------- | -------------------------- |
| 用户目标   | Goal Contract              |
| 不可变约束 | Constraint Registry        |
| 已冻结方案 | Decision Ledger            |
| 代码事实   | Repository Evidence        |
| 行为正确性 | Tests / Validation         |
| 实际修改   | Diff                       |
| 已排除方向 | Rejected Hypothesis Ledger |
| 当前进度   | Workflow State             |
| 阶段交接   | State Delta                |
| 接收确认   | Handoff ACK                |

任务卡只是共享状态的人类可读渲染结果，不再是唯一事实源。

### 5.2 只传差量

接收方已经持有的状态不应重复发送。

```text
STATE-N + DELTA-(N+1) = STATE-(N+1)
```

只有以下内容进入下一次交接：

- 新决策；
- 新证据；
- 失效决策；
- 已解决问题；
- 新开放问题；
- 验收状态变化；
- 下一步动作。

### 5.3 按需拉取上下文

Planner 不应预先猜测 Builder 所需的全部代码上下文。

执行模型首先获得：

- 目标；
- 约束；
- 入口符号；
- 决策；
- 验收标准；
- Evidence Reference。

然后通过 Context Broker 主动请求符号、调用路径、测试、构建规则或日志。

### 5.4 冻结已接受信息

已经接受且未受本轮修改影响的决策，不应在后续阶段重新讨论。

状态转换：

```text
proposed → accepted → frozen → invalidated
```

只有满足明确条件时才能失效：

- 新证据与原决策冲突；
- 用户规格发生改变；
- 仓库状态使证据失效；
- 实现证明原决策不可行；
- Reviewer 提供明确反例。

### 5.5 证据优先

所有重要事实应绑定来源和内容哈希。

模型推断必须与工具事实区分：

```text
user-specified
repository-observed
tool-observed
model-inferred
model-proposed
reviewer-accepted
```

### 5.6 默认保持模型和会话连续

角色变化不应自动触发模型切换。

优先顺序：

1. 同一会话继续执行；
2. 同一模型新会话恢复状态；
3. 同类 Builder 接手；
4. 只有出现语义阻塞、风险升级或模型失败时才跨模型交接。

------

## 6. 总体数据模型

### 6.1 Workflow State IR

新增：

```text
.ai-workflow/runs/<run-id>/WORKFLOW_STATE.json
```

示例结构：

```json
{
  "schema_version": 1,
  "state_id": "sha256:...",
  "parent_state_id": "sha256:...",
  "task_id": "T-17",
  "phase": "implementation",

  "repository": {
    "base_commit": "...",
    "head_commit": "...",
    "worktree_state_hash": "...",
    "changed_files": []
  },

  "goal": {
    "id": "G-1",
    "statement": "Preserve control users during graph rewrite",
    "acceptance_ids": ["AC-1", "AC-2"]
  },

  "constraints": [],
  "facts": [],
  "decisions": [],
  "hypotheses": [],
  "rejected_hypotheses": [],
  "assumptions": [],
  "open_questions": [],
  "evidence_refs": [],

  "acceptance_status": {},

  "next_action": {
    "owner": "execution-builder",
    "operation": "implement frozen decision D-3",
    "allowed_paths": []
  }
}
```

### 6.2 Workflow Event Log

新增：

```text
.ai-workflow/runs/<run-id>/WORKFLOW_EVENTS.jsonl
```

每条事件描述一次状态变化：

```json
{
  "event_id": "EV-28",
  "timestamp": "2026-07-20T10:00:00Z",
  "actor": "claude-builder",
  "event_type": "decision-added",
  "base_state_id": "sha256:...",
  "payload": {
    "decision_id": "D-8"
  }
}
```

推荐事件类型：

```text
fact-added
decision-proposed
decision-accepted
decision-frozen
decision-invalidated
hypothesis-added
hypothesis-rejected
question-opened
question-resolved
evidence-added
acceptance-updated
context-requested
context-served
handoff-created
handoff-acknowledged
revision-requested
owner-changed
```

### 6.3 Handoff Delta

新增：

```text
.ai-workflow/runs/<run-id>/HANDOFF_DELTA.json
```

示例：

```json
{
  "schema_version": 1,
  "task_id": "T-17",
  "base_state_id": "sha256:state-12",
  "new_state_id": "sha256:state-13",

  "added_facts": ["F-18"],
  "added_decisions": ["D-8"],
  "invalidated_decisions": [],
  "added_evidence": ["E-21"],
  "rejected_hypotheses": ["H-7"],
  "resolved_questions": ["Q-3"],
  "new_open_questions": ["Q-5"],
  "changed_acceptance": ["AC-3"],

  "next_action": {
    "owner": "execution-builder",
    "operation": "repair control-edge rewrite",
    "allowed_paths": ["src/optimizer.cc"]
  }
}
```

### 6.4 Handoff ACK

新增：

```text
.ai-workflow/runs/<run-id>/HANDOFF_ACK.json
```

示例：

```json
{
  "schema_version": 1,
  "state_id": "sha256:state-13",
  "receiver": "claude-session-28",

  "understood_goal_id": "G-1",
  "accepted_constraints": ["C-1", "C-2", "C-3"],
  "accepted_decisions": ["D-3", "D-8"],
  "open_questions": ["Q-5"],

  "planned_first_action": {
    "operation": "inspect existing control-edge tests",
    "target": "GraphOptimizerTest"
  },

  "additional_context_requested": ["CTX-Q-12"],
  "contradictions": []
}
```

Harness 负责检查：

- 是否缺少冻结约束；
- 是否遗漏当前决策；
- 是否基于正确状态；
- 是否计划修改禁止路径；
- 是否存在决策冲突；
- 是否需要补充上下文。

ACK 必须保持极短，避免形成新的 Planner 阶段。

------

## 7. Rejected Hypothesis Ledger

新增：

```text
.ai-workflow/runs/<run-id>/REJECTED_HYPOTHESES.json
```

示例：

```json
{
  "schema_version": 1,
  "items": [
    {
      "id": "H-17",
      "statement": "Failure is caused by node ordering",
      "status": "rejected",
      "reason": "The failing test reproduces with stable node order",
      "evidence_refs": ["E-43"],
      "reopen_when": "Remote graph order differs from local order",
      "producer": "claude-builder"
    }
  ]
}
```

用途：

1. 阻止后续模型重复探索已排除方向。
2. 允许在满足特定条件时重新开启假设。
3. 测量不同模型重复调查相同假设的频率。
4. 为 Reviewer 提供简洁的根因调查历史。
5. 在 Revision 阶段只暴露仍有效的候选根因。

------

## 8. Evidence Object Store

### 8.1 当前问题

现有 Context Cache 主要保存一个受字节限制的文本 blob。该方式存在以下问题：

- 不区分符号、日志、测试和构建规则；
- 大内容只能截断；
- 无法独立失效单个对象；
- 无法知道接收方已经读取过哪些内容；
- 相同证据可能在多个 Packet 中重复内联。

### 8.2 目标设计

新增：

```text
.ai-workflow/objects/
```

采用内容寻址：

```text
.ai-workflow/objects/ab/cdef1234.json
```

对象示例：

```json
{
  "schema_version": 1,
  "object_id": "sha256:abcdef1234",
  "kind": "symbol-slice",

  "repository": {
    "commit": "...",
    "path": "src/optimizer.cc"
  },

  "selector": {
    "symbol": "GraphOptimizer::Optimize",
    "start_line": 120,
    "end_line": 245
  },

  "producer": {
    "tool": "lsp",
    "version": "..."
  },

  "dependency_hashes": {
    "file_hash": "...",
    "symbol_hash": "..."
  },

  "content": "..."
}
```

推荐对象类型：

```text
symbol-slice
file-slice
call-path
callers
callees
test-definition
test-result
build-rule
compiler-error
runtime-error
diff-hunk
repository-fact
decision-record
acceptance-record
```

### 8.3 失效规则

当以下信息变化时，对象自动失效：

- 文件内容哈希变化；
- 符号哈希变化；
- repository commit 变化；
- build configuration 变化；
- validation command 变化；
- worktree state hash 变化。

对于与变化无关的对象，应继续复用。

------

## 9. Pull-based Context Broker

新增命令：

```bash
python scripts/context-broker.py request \
  --state WORKFLOW_STATE.json \
  --query context-query.json \
  --output context-response.json
```

请求结构：

```json
{
  "state_id": "sha256:state-13",
  "requester": "claude-session-28",

  "query": {
    "intent": "locate-control-edge-contract",
    "symbols": ["GraphOptimizer::Optimize"],
    "include": [
      "definition",
      "callers",
      "tests",
      "build-rules"
    ],
    "max_bytes": 12000
  }
}
```

返回：

```json
{
  "context_id": "CTX-82",
  "state_id": "sha256:state-13",

  "objects": [
    {
      "object_id": "sha256:...",
      "kind": "symbol-slice",
      "path": "src/optimizer.cc",
      "symbol": "GraphOptimizer::Optimize"
    }
  ],

  "cache": {
    "requested": 5,
    "hits": 4,
    "generated": 1
  }
}
```

Context Broker 应支持：

1. 符号定义定位；
2. caller/callee 查询；
3. 相关测试查找；
4. BUILD、CMake、Bazel target 查找；
5. diff 影响范围查询；
6. 编译错误定位；
7. 最近失败日志定位；
8. 与验收标准相关的代码查询；
9. 已读对象去重；
10. 按角色和任务阶段排序。

------

## 10. Acceptance–Evidence–Diff Graph

新增：

```text
.ai-workflow/runs/<run-id>/ACCEPTANCE_GRAPH.json
```

示例：

```json
{
  "schema_version": 1,
  "acceptance_items": [
    {
      "id": "AC-1",
      "description": "Control users remain connected after rewrite",
      "status": "supported",

      "decision_refs": ["D-4"],
      "implementation_refs": ["DIFF-7"],
      "test_refs": ["TEST-3"],
      "result_refs": ["RESULT-3"],

      "unverified_claims": []
    }
  ]
}
```

Reviewer 的输入应优先变成：

```json
{
  "state_id": "sha256:...",
  "unsupported_acceptance": ["AC-4"],
  "contradictory_evidence": [],
  "changed_decisions": [],
  "new_diff_refs": ["DIFF-12"],
  "new_test_refs": ["TEST-9"]
}
```

Revision 只处理：

- 失败的 Acceptance；
- 对应的 diff；
- 对应的测试；
- 相关决策；
- 新增证据。

不重新发送已经通过且未受影响的验收项。

------

## 11. Review Receipt

新增：

```text
.ai-workflow/runs/<run-id>/REVIEW_RECEIPT.json
```

示例：

```json
{
  "schema_version": 1,
  "review_id": "REV-4",
  "bound_state_id": "sha256:state-18",

  "accepted": ["AC-1", "AC-2"],
  "conditional": ["AC-3"],
  "rejected": [],

  "frozen_decisions_confirmed": ["D-3", "D-4"],
  "new_questions": ["RQ-2"]
}
```

后续 Review 只重新检查：

- 状态发生变化的 Acceptance；
- 条件接受项；
- 新增或失效的 Decision；
- 新 diff；
- 新测试结果；
- 明确未解决的问题。

------

## 12. Ownership Lease

新增：

```text
.ai-workflow/runs/<run-id>/OWNER_LEASE.json
```

示例：

```json
{
  "schema_version": 1,
  "task_id": "T-17",
  "owner": "claude-session-28",

  "lease_scope": [
    "implementation",
    "mechanical-revision",
    "test-repair"
  ],

  "handoff_only_when": [
    "semantic-blocker",
    "risk-escalation",
    "owner-failure",
    "explicit-user-request"
  ],

  "state_id": "sha256:state-13"
}
```

默认策略：

1. 机械 Revision 返回原 Builder。
2. 测试修复优先返回原 Builder。
3. 同一模型能够恢复会话时，不切换模型。
4. 没有新证据时禁止重新调用 Reviewer。
5. 多个小缺口合并成一次 Revision。
6. Reviewer 只提供有界差量。
7. Planner 不参与实现期的普通机械问题。
8. 只有真正的语义阻塞才调用 Advisor。

------

## 13. Handoff Metrics

新增：

```text
.ai-workflow/runs/<run-id>/HANDOFF_EVENTS.jsonl
```

每次交接记录：

```json
{
  "schema_version": 1,
  "task_id": "T-17",
  "handoff_id": "HF-3",

  "sender": "solution-planner",
  "receiver": "execution-builder",

  "base_state_id": "sha256:...",
  "new_state_id": "sha256:...",

  "payload_bytes": 18720,
  "novel_payload_bytes": 4310,
  "repeated_payload_bytes": 14410,

  "receiver_reads_before_first_action": 12,
  "receiver_searches_before_first_action": 5,
  "seconds_to_first_meaningful_action": 146,

  "known_facts_rediscovered": 7,
  "rejected_hypotheses_revisited": 2,
  "missing_constraint_incidents": 1,
  "handoff_caused_revision": true,

  "context_objects_requested": 9,
  "context_cache_hits": 6
}
```

### 13.1 关键指标

#### 传输冗余率

# [ R_{\text{repeat}}

\frac{\text{repeated payload bytes}}
{\text{payload bytes}}
]

#### 重建时间

# [ T_{\text{reconstruct}}

## T_{\text{first meaningful action}}

T_{\text{handoff received}}
]

#### 重复探索率

# [ R_{\text{rediscovery}}

\frac{\text{known facts rediscovered}}
{\text{total facts discovered}}
]

#### 上下文缓存命中率

# [ R_{\text{cache}}

\frac{\text{context cache hits}}
{\text{context objects requested}}
]

#### 交接返工率

# [ R_{\text{handoff revision}}

\frac{\text{handoff-caused revisions}}
{\text{delegated tasks}}
]

#### 状态保持率

# [ R_{\text{state preservation}}

\frac{\text{correctly acknowledged frozen items}}
{\text{all frozen items}}
]

### 13.2 Handoff Tax

最终将交接成本统一为：

# [ \text{Handoff Tax}

C_{\text{serialization}}
+
C_{\text{reconstruction}}
+
C_{\text{rediscovery}}
+
C_{\text{handoff-induced revision}}
]

------

## 14. 通信感知路由

当前路由应增加以下候选执行模式：

```text
direct-codex
same-model-single-pass
claude-builder-local-acceptance
claude-builder-delta-codex-review
claude-planner-builder-shared-state
full-cross-model-workflow
```

路由决策不只考虑：

- 模型价格；
- 风险；
- 文件数量；
- diff 大小；
- 是否存在确定性验证；
- Codex 工作减少率。

还应考虑：

- 预计 handoff 次数；
- 预计交接负载；
- 历史重建时间；
- 历史重复探索率；
- 同任务类型的状态保持率；
- 同模型首轮成功率；
- 原 Builder 可否继续；
- Context Cache 命中率；
- Reviewer 是否只需要差量证据。

推荐判断式：

# [ \text{Delegation Benefit}

## C_{\text{direct implementation}}

## C_{\text{delegated implementation}}

C_{\text{handoff}}
]

仅当：

[
\text{Delegation Benefit} > 0
]

或用户明确要求使用 Claude-first 配额策略时，才进入跨模型工作流。

------

## 15. Benchmark 设计

### 15.1 对比模式

每个测试任务至少比较：

```text
A. 完整任务卡
B. 压缩任务卡
C. State IR + Delta
D. State IR + Delta + Pull Context
E. 同模型连续执行
```

### 15.2 Benchmark 类型

#### 约束保持

Planner 冻结多个约束，检测 Builder：

- 是否正确 ACK；
- 是否全部遵守；
- 是否发生禁止路径修改。

#### 负知识保持

Planner 已经排除若干假设，检测 Builder：

- 是否重复调查；
- 是否无新证据重新打开；
- 是否因信息缺失回到错误路径。

#### 差量 Revision

只让一个 Acceptance 失败，检测 Revision：

- 是否只处理失败项；
- 是否重读无关文件；
- 是否修改已通过区域；
- 是否重新讨论冻结决策。

#### 状态失效

修改文件或 commit 后，检测：

- 相关 Evidence 是否失效；
- 无关 Evidence 是否继续复用；
- 接收方是否拒绝使用过期状态。

#### Review 增量

在已接受两个 Acceptance 的基础上只改变第三项，检测 Reviewer：

- 是否重复审查前两项；
- 是否只请求变化证据；
- 是否正确绑定 state hash。

#### 会话连续性

比较：

- 同一 Builder Revision；
- 新 Builder 接管；
- 跨模型接管。

评估重建成本差异。

### 15.3 Benchmark 指标

- 任务通过率；
- 首轮成功率；
- 最终 Acceptance 覆盖率；
- 输入 token；
- 输出 token；
- 总模型调用数；
- handoff 数；
- payload bytes；
- novel payload ratio；
- time-to-first-action；
- 文件读取数；
- 搜索次数；
- 重复探索次数；
- Context Cache 命中率；
- handoff-induced revision；
- 最终 diff reuse；
- Codex takeover rate；
- 总活跃时间；
- 总墙钟时间。

------

## 16. 实施阶段

## Phase 0：交接测量

### 目标

先确定当前跨模型信息损耗发生在何处，不立即重构主工作流。

### 交付物

```text
scripts/record-handoff-event.py
scripts/summarize-handoff-metrics.py
schemas/handoff-event.schema.json
tests/test_handoff_metrics.py
```

### 新增指标

- handoff count；
- payload bytes；
- task card bytes；
- review packet bytes；
- time-to-first-meaningful-action；
- receiver reads；
- receiver searches；
- repeated exploration；
- handoff-induced revision。

### 验收标准

1. 每次跨模型调用生成一条 Handoff Event。
2. 能按任务类型汇总交接指标。
3. 不影响现有运行结果。
4. 指标缺失时明确标记 `unknown`，不得伪造估计。
5. Preview 模式保持零模型调用。

------

## Phase 1：Workflow State IR

### 目标

建立模型无关的任务状态结构。

### 交付物

```text
schemas/workflow-state.schema.json
schemas/workflow-event.schema.json
scripts/init-workflow-state.py
scripts/apply-workflow-delta.py
scripts/validate-workflow-state.py
scripts/render-task-card-from-state.py
```

### 验收标准

1. 相同输入生成确定性 state hash。
2. 所有状态更新都能追溯到 Event。
3. Task Card 可从 State IR 渲染。
4. 现有 Markdown Task Card 保持兼容。
5. 非法状态转换被拒绝。
6. frozen Decision 不能被静默覆盖。

------

## Phase 2：Delta Handoff 与 ACK

### 目标

使跨模型交接只发送状态变化，并验证接收方理解。

### 交付物

```text
schemas/handoff-delta.schema.json
schemas/handoff-ack.schema.json
scripts/build-handoff-delta.py
scripts/validate-handoff-ack.py
scripts/merge-handoff-ack.py
```

### 验收标准

1. Delta 必须绑定 `base_state_id`。
2. 状态不一致时拒绝执行。
3. ACK 缺失冻结约束时自动补发缺失项。
4. ACK 存在矛盾时停止执行并进入有界修复。
5. ACK 大小有明确上限。
6. 不允许 ACK 退化为长篇任务重述。

------

## Phase 3：Rejected Hypothesis Ledger

### 目标

保存调查中的负知识，减少重复探索。

### 交付物

```text
schemas/rejected-hypothesis.schema.json
scripts/update-hypothesis-ledger.py
scripts/check-revisited-hypothesis.py
tests/test_hypothesis_ledger.py
```

### 验收标准

1. 每个 rejected hypothesis 必须带 Evidence。
2. 每个 rejected hypothesis 可定义 reopen condition。
3. 没有新证据时，重复开启已排除假设会被记录。
4. Reviewer 能查看与当前问题相关的已排除方向。
5. Revision 不默认携带所有历史，只传相关条目。

------

## Phase 4：Evidence Object Store

### 目标

将上下文缓存从单文本 blob 升级为内容寻址对象。

### 交付物

```text
scripts/evidence-store.py
scripts/evidence-invalidate.py
schemas/evidence-object.schema.json
tests/test_evidence_store.py
```

### 验收标准

1. 相同内容复用同一 Object ID。
2. 对象能按 commit、文件和 symbol hash 失效。
3. 支持独立读取单个对象。
4. Packet 默认只包含引用，不重复内联完整内容。
5. 无法读取引用时明确失败，不静默忽略。
6. 可统计 receiver cache hit。

------

## Phase 5：Pull Context Broker

### 目标

让执行模型主动请求所需上下文。

### 交付物

```text
scripts/context-broker.py
schemas/context-query.schema.json
schemas/context-response.schema.json
tests/test_context_broker.py
```

### 验收标准

1. 支持 symbol、caller、callee、test、build rule 查询。
2. 请求和结果均绑定 state hash。
3. 已缓存对象不重复生成。
4. 响应符合 byte budget。
5. 不以截断整文件作为主要压缩方式。
6. 能区分未找到、过期和权限不足。

------

## Phase 6：Acceptance Graph 与 Review Receipt

### 目标

将验收标准、代码修改和验证证据连接起来。

### 交付物

```text
schemas/acceptance-graph.schema.json
schemas/review-receipt.schema.json
scripts/build-acceptance-graph.py
scripts/build-delta-review-packet.py
scripts/validate-review-receipt.py
```

### 验收标准

1. 每个已通过 Acceptance 有至少一个证据引用。
2. Reviewer 默认只收到变化项。
3. 已接受项在 state 未变化时不会重复进入 Review。
4. Revision 只接收失败项的证据子图。
5. Review Receipt 必须绑定 state hash。
6. 新 diff 影响已接受项时自动重新打开对应 Acceptance。

------

## Phase 7：Ownership Lease

### 目标

减少不必要的角色和模型切换。

### 交付物

```text
schemas/owner-lease.schema.json
scripts/select-continuation-owner.py
tests/test_owner_lease.py
```

### 验收标准

1. 机械 Revision 默认返回原 Builder。
2. 测试修复默认返回原 Builder。
3. 没有语义阻塞时不调用 Advisor。
4. 没有新证据时不重复调用 Reviewer。
5. 模型切换必须记录明确原因。
6. 会话恢复失败时才能降级为新会话。

------

## Phase 8：通信感知路由

### 目标

将 Handoff Tax 纳入所有权和工作流选择。

### 交付物

```text
scripts/estimate-handoff-tax.py
scripts/calibrate-handoff-routing.py
route-task.py updates
workflow_economics.py updates
tests/test_handoff_routing.py
```

### 验收标准

1. Router 能选择同模型连续执行。
2. Router 能绕过不经济的跨模型工作流。
3. 历史样本不足时输出 `unknown` 或 `canary`。
4. 不以未经验证的模型估计覆盖确定性事实。
5. 路由结果能解释主要成本组成。
6. 用户显式所有权选择仍具有最高优先级。

------

## 17. 文件规划

建议新增目录：

```text
schemas/
  workflow-state.schema.json
  workflow-event.schema.json
  handoff-delta.schema.json
  handoff-ack.schema.json
  handoff-event.schema.json
  evidence-object.schema.json
  rejected-hypothesis.schema.json
  context-query.schema.json
  context-response.schema.json
  acceptance-graph.schema.json
  review-receipt.schema.json
  owner-lease.schema.json
```

建议新增脚本：

```text
scripts/
  init-workflow-state.py
  apply-workflow-delta.py
  validate-workflow-state.py
  render-task-card-from-state.py

  build-handoff-delta.py
  validate-handoff-ack.py
  merge-handoff-ack.py

  record-handoff-event.py
  summarize-handoff-metrics.py

  update-hypothesis-ledger.py
  check-revisited-hypothesis.py

  evidence-store.py
  evidence-invalidate.py
  context-broker.py

  build-acceptance-graph.py
  build-delta-review-packet.py
  validate-review-receipt.py

  select-continuation-owner.py
  estimate-handoff-tax.py
  calibrate-handoff-routing.py
```

单次运行目录：

```text
.ai-workflow/runs/<run-id>/
  WORKFLOW_STATE.json
  WORKFLOW_EVENTS.jsonl

  HANDOFF_DELTA.json
  HANDOFF_ACK.json
  HANDOFF_EVENTS.jsonl

  REJECTED_HYPOTHESES.json
  ACCEPTANCE_GRAPH.json
  REVIEW_RECEIPT.json
  OWNER_LEASE.json
```

全局对象缓存：

```text
.ai-workflow/objects/
.ai-workflow/cache/receiver-state/
.ai-workflow/economics-history.jsonl
```

------

## 18. 兼容策略

### 18.1 与现有 Task Card 兼容

初期保持：

```text
Task JSON
   ↓
Workflow State IR
   ↓
现有 Task Card Markdown
```

现有 Claude 调度逻辑仍读取 Markdown，但 Markdown 改为 State IR 的确定性渲染。

### 18.2 与现有 Context Packet 兼容

阶段性保留 L0/L1/L2：

- L0 继续承载目标文件和符号；
- L1 改为 Evidence Object Reference；
- L2 仅作为兼容和诊断模式；
- 默认模式逐步迁移为 Pull Context。

### 18.3 与现有 Review 兼容

初期同时生成：

```text
full-review-packet.md
delta-review-packet.json
```

通过 Benchmark 验证后，再将差量 Review 设为默认。

### 18.4 与旧运行记录兼容

旧记录缺失新增字段时：

```json
{
  "handoff_metrics_available": false,
  "reason": "legacy-run"
}
```

不得将缺失值按零处理。

------

## 19. 风险与缓解措施

### 风险一：状态协议本身变得过于复杂

缓解：

- 第一版只支持 Goal、Constraint、Decision、Evidence、Question、Next Action；
- 其他字段按真实任务需要逐步增加；
- 所有 schema 都设置体积预算；
- 避免复制模型自由文本推理过程。

### 风险二：ACK 增加新的模型调用成本

缓解：

- ACK 与执行调用放在同一次模型响应中；
- 使用固定短 JSON；
- 只检查冻结约束和首个动作；
- Express Lane 可跳过 ACK。

### 风险三：模型不稳定地产生结构化状态

缓解：

- 使用严格 JSON Schema；
- 本地工具负责计算 hash 和状态合并；
- 模型只能提出事件，不能直接重写完整 State；
- 非法事件拒绝应用。

### 风险四：Evidence Object 失效逻辑不正确

缓解：

- 依赖 commit、file hash 和 symbol hash；
- 无法确认有效性时视为 stale；
- 关键 Review 不依赖缓存对象作为唯一证据。

### 风险五：过度锁定错误决策

缓解：

- Frozen Decision 允许通过新证据显式失效；
- 每个 Decision 保存依据和影响范围；
- Reviewer 可以提出 invalidation，而不是静默重写。

### 风险六：状态管理成本超过收益

缓解：

- 首先实施 Phase 0 测量；
- Express Lane 保持简单；
- 只有跨模型任务启用完整状态协议；
- 路由依据历史 Handoff Tax 自动绕过低收益流程。

### 风险七：Context Broker 查询本身消耗大量 token

缓解：

- Broker 由本地工具执行，不使用模型完成普通定位；
- 优先使用 LSP、CodeGraph、Zoekt、git 和构建元数据；
- 对模型查询设置次数和字节预算；
- 查询结果进入对象缓存。

------

## 20. 成功标准

完成本规划后，期望在中等规模跨模型编码任务上达到：

| 指标                               | 目标           |
| ---------------------------------- | -------------- |
| 重复 payload bytes                 | 降低 50% 以上  |
| 接收方首次动作前文件读取           | 降低 30% 以上  |
| 已排除假设重复调查                 | 降低 60% 以上  |
| Reviewer 重复审查已通过 Acceptance | 降低 70% 以上  |
| Handoff-induced revision           | 降低 30% 以上  |
| Context Cache 命中率               | 达到 60% 以上  |
| ACK 关键约束保持率                 | 达到 95% 以上  |
| 最终任务通过率                     | 不低于现有基线 |
| Claude diff reuse                  | 不低于现有基线 |
| 单任务模型切换次数                 | 明显下降       |

所有目标都应通过真实任务 Benchmark 验证，而不是只通过单元测试证明。

------

## 21. 推荐优先级

### P0：立即实施

1. Handoff Event 与 reconstruction metrics；
2. Workflow State IR 最小 schema；
3. Handoff Delta；
4. Rejected Hypothesis Ledger。

### P1：高收益实施

1. Handoff ACK；
2. Acceptance–Evidence–Diff Graph；
3. Review Receipt；
4. 原 Builder Revision 连续性。

### P2：基础设施升级

1. Evidence Object Store；
2. Pull Context Broker；
3. Receiver Cache；
4. 精细化失效策略。

### P3：路由优化

1. Handoff Tax 估算；
2. 通信感知路由；
3. 基于历史任务类型的模型选择；
4. 自动选择单模型或多模型工作流。

### P4：长期研究

1. 开源模型之间的激活映射；
2. 固定 latent slot 通信；
3. KV-cache adapter；
4. coding-state public latent space；
5. 结构化外部状态与神经 latent 的联合通信。

------

## 22. 最小可行版本

第一版不需要一次实现完整 State Bus。

建议 MVP 只新增四个文件：

```text
WORKFLOW_STATE.json
WORKFLOW_EVENTS.jsonl
HANDOFF_DELTA.json
HANDOFF_ACK.json
```

最小状态只包含：

```text
goal
constraints
accepted_decisions
rejected_hypotheses
open_questions
evidence_refs
acceptance_status
next_action
repository_state_hash
```

最小流程：

```text
1. Planner 生成 State Event
2. 本地工具应用 Event 并生成 State
3. Harness 根据 State 生成 Delta
4. Builder 返回 ACK
5. Harness 检查约束和状态 ID
6. Builder 执行
7. Builder 只提交新 Event
8. Reviewer 只读取变化项
```

这个 MVP 已经能够验证以下核心假设：

> 相比不断压缩和扩展任务卡，模型共享状态并传递差量，能否显著减少跨模型上下文重建成本。

------

## 23. 最终方向

Skill Harness 的长期定位不应只是：

> 为不同模型编写更完整的任务卡。

而应升级为：

> 为多个模型维护同一个可验证、可恢复、可增量同步的任务状态。

最终工作流应尽量满足：

```text
代码承载事实
测试承载行为
决策账本承载冻结方案
负知识账本承载已排除方向
证据对象承载可验证上下文
状态事件承载变化
Handoff Delta 承载交接
ACK 承载理解校验
Review Receipt 承载已接受范围
Router 根据 Handoff Tax 决定是否值得切换模型
```

在无法访问闭源模型内部激活和 KV cache 的现实条件下，这是一条最可行的低损耗跨模型通信路径。