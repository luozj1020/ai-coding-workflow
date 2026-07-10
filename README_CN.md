# AI Coding Workflow Skill

一个可复用的 Codex / Claude Code 工作流技能，用于将多智能体编码工作流安装到软件仓库中。

[English](README.md) | 中文

## 功能说明

ai-coding-workflow 可以为仓库自动配置：
- `AGENTS.md` - 所有智能体的共享规则
- `CLAUDE.md` - Claude Code 执行规则
- 任务卡和证据包模板
- Codex + Claude Code 工作流的安全调度/审查/循环脚本
- 默认可选开启的 Codex Spark 辅助脚本，用 `gpt-5.3-codex-spark` 做任务规模分类、任务卡审查、计划拆分、验证规划、失败归因、证据检查或极小范围隔离 micro-builder 工作
- Execution profiles：默认省 token 的 balanced、完整上下文的 safe，以及显式大仓加速的 fast-large-repo
- 大型仓库调度选项：受管 worktree 复用，以及减少昂贵的未跟踪文件扫描
- 本地验证 gate，以及从任务卡 validation fenced block 自动抽取命令
- Builder / Checker-Test 任务模式，用于分离实现和验证职责
- Direction / Boundary Acknowledgement 方向/边界确认门，以及防反复确认规则
- 幂等更新的托管块（managed blocks）

## 常用动作

| 动作 | 时机 | 命令 |
|------|------|------|
| **安装 Skill** | 每台电脑一次 | `python scripts/install_for_codex.py` |
| **更新 Skill** | 拉取新版本后 | `python scripts/update_skill.py --bootstrap-current` |
| **引导项目** | 每个仓库一次 | `python scripts/install_workflow.py .` |
| **本地控制面引导** | 不希望提交 workflow 控制面文件的仓库 | `python scripts/install_workflow.py . --local-only` |
| **刷新项目 workflow** | 已经引导过的仓库 | `python scripts/install_workflow.py . --update-workflow-files` |

安装 Skill 只会让 Codex 发现该 workflow，不会自动在目标仓库创建或刷新 `ai/` 目录。已经引导过的项目会保留本地的 `ai/dispatch-to-claude.sh`、`ai/task-card-template.md` 等 workflow 副本。更新 Skill 后，需要使用 `update_skill.py --bootstrap-current` 或 `install_workflow.py . --update-workflow-files` 刷新这些本地副本。

如果目标仓库只想本地使用 `ai/`、`AGENTS.md`、`CLAUDE.md` 和 `.worktrees/`，不希望把这些控制面文件提交到业务仓库，使用 `--local-only`。它会把这些路径写入 `.git/info/exclude`，不会修改 `.gitignore`；`doctor_workflow.py` 会把这种配置识别为 local-only ignore mode。

## 仓库结构

```
ai-coding-workflow/
  README.md              ← 英文文档
  README_CN.md           ← 中文文档（本文件）
  LICENSE                ← MIT 许可证
  .gitignore
  SKILL.md              ← Codex 发现的技能入口
  agents/
    openai.yaml         ← OpenAI/Codex 技能元数据
  assets/
    AGENTS.md           ← 智能体规则模板
    CLAUDE.md           ← Claude Code 规则模板
    README.md           ← 本地使用指南模板
    task-card-template.md
    evidence-packet-template.md
    plan-task-template.md
    plan-findings-template.md
    plan-progress-template.md
  references/
    loop-model.md       ← 循环状态机和停止条件
    operating-model.md  ← 智能体角色和交接模型
    review-policy.md    ← 代码审查分工
    mcp-policy.md       ← 信息检索顺序
    benchmark-policy.md ← 质量 / 速度 / 成本 / 稳定性评估
  scripts/
    install_workflow.py ← 引导仓库
    install_for_codex.py← 安装技能供 Codex 发现
    update_skill.py     ← 便捷更新 Skill，并可选更新当前项目 workflow
    dispatch-to-claude.sh← 向 Claude Code 分发任务卡
    check-worktree.sh   ← 运行只检查不修改的验证并写入 checker report
    locate-code.py      ← 低 token 代码定位器，带有受限 CodeGraph 回退
    review-with-codex.sh← 向 Codex/GPT 发送证据审查
    run-codex-spark.sh  ← 可选 gpt-5.3-codex-spark 辅助运行器
    run-parallel-loop.sh← 实验性并行派发辅助脚本
    run-loop.sh         ← 可选循环运行器（调度 + 审查）
    doctor_workflow.py  ← 调度/审查循环就绪检查（只读）
    code-search-service.py ← 可选 Zoekt/Sourcegraph 设置和诊断
    clean_runtime.py    ← 预览/清理已忽略的运行时产物
    install_context_tools.py ← 检查/安装上下文工具（LSP、代码检查）
    summarize-loop-run.py ← 汇总 workflow 质量、速度、成本和稳定性
    init-plan.py        ← 创建 ai/plans/<task-id>/ 计划文件
    session-catchup.py  ← 根据计划和 artifacts 生成 resume-context.md
```

---

## 场景 A：在新电脑安装 Skill

将 Skill 安装到用户级 Codex skills 目录。每台电脑只需执行一次。

### Windows PowerShell

```powershell
git clone https://github.com/luozj1020/ai-coding-workflow.git
cd ai-coding-workflow
python .\scripts\install_for_codex.py
```

或手动安装：

```powershell
git clone https://github.com/luozj1020/ai-coding-workflow.git

$dst = "$env:USERPROFILE\.codex\skills\ai-coding-workflow"
Remove-Item -Recurse -Force $dst -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\skills" | Out-Null
Copy-Item -Recurse -Force ".\ai-coding-workflow" $dst
```

### macOS / Linux

```bash
git clone https://github.com/luozj1020/ai-coding-workflow.git
cd ai-coding-workflow
python scripts/install_for_codex.py
```

或手动安装：

```bash
git clone https://github.com/luozj1020/ai-coding-workflow.git
mkdir -p ~/.codex/skills
rm -rf ~/.codex/skills/ai-coding-workflow
cp -R ai-coding-workflow ~/.codex/skills/ai-coding-workflow
```

然后重启 Codex。

安装器会在安装完成后打印精确的项目引导命令。也可以在这个 Skill 仓库的克隆目录中，用一条命令完成“安装 Skill + 引导目标项目”：

```powershell
python .\scripts\install_for_codex.py --bootstrap-repo E:\path\to\your-project
```

```bash
python scripts/install_for_codex.py --bootstrap-repo /path/to/your-project
```

日常更新可以使用更短的 wrapper：

```bash
python scripts/update_skill.py
python scripts/update_skill.py --bootstrap-current
python scripts/update_skill.py --pull --bootstrap-repo /path/to/your-project
```

`python scripts/update_skill.py` 只更新用户级 Codex Skill。`--bootstrap-current` 和 `--bootstrap-repo` 会额外使用 `--update-workflow-files` 刷新目标仓库本地 workflow 文件，因此旧项目也能拿到新的 dispatcher、review prompt、模板和辅助脚本行为。

如果从已安装的 Skill 入口运行，但希望用另一个克隆目录作为更新源：

```bash
python ~/.codex/skills/ai-coding-workflow/scripts/update_skill.py \
  --source /path/to/ai-coding-workflow \
  --bootstrap-current
```

安装 Skill 时，安装器会执行只读的上下文智能检查：
- LSP 工具，例如 `pyright`、`typescript-language-server`、`gopls`、`rust-analyzer`。
- CodeGraph CLI 是否可用。
- 使用 `--bootstrap-current` 或 `--bootstrap-repo` 时，目标仓库是否已经有 `.codegraph/` 索引目录。
- 可选 Zoekt / Sourcegraph 代码搜索服务是否可用。

安装器只打印建议，不会自动安装 LSP 工具，也不会自动运行 `codegraph init`。如需查看 LSP 安装建议，可运行 `python ~/.codex/skills/ai-coding-workflow/scripts/install_context_tools.py`；如需为某个仓库启用 CodeGraph，请在目标仓库内显式运行 `codegraph init`。

交互式安装 skill 时，安装器会询问是否配置可选代码搜索服务。非交互安装会跳过提示。也可以显式控制：

```bash
python scripts/install_for_codex.py --code-search-services ask
python scripts/install_for_codex.py --code-search-services skip
python scripts/install_for_codex.py --code-search-services check
```

大型仓库里应先使用有边界的代码定位器，而不是把宽问题直接交给 CodeGraph：

```bash
python ai/locate-code.py "需要修改的符号或行为" --path src --max-files 12
```

`locate-code.py` 使用 `git ls-files` 加 `rg`/`git grep` 生成候选文件、短 snippet 和精确读取命令。CodeGraph 仍适合具体符号和调用路径，但不再作为大型仓库的默认宽定位器。如果 Zoekt 已安装并完成索引，`--backend auto` 会先使用 Zoekt，再回退到 lexical search。已有 Sourcegraph 服务时，可通过 `SOURCEGRAPH_URL` 接入。CodeGraph 的 `auto` 模式会在 tracked file 数量超过阈值时跳过 CodeGraph；只有具体文件/符号查询才使用 `--codegraph try --codegraph-timeout 12`。

可选索引搜索设置：

```bash
python ai/code-search-service.py doctor
python ai/code-search-service.py install-zoekt --yes
python ai/code-search-service.py index-zoekt --repo . --yes
AI_CODE_LOCATOR_BACKEND=auto python ai/locate-code.py "需要修改的符号或行为"
```

Sourcegraph 被视为外部/自托管服务，不是默认本地依赖。运行 `python ai/code-search-service.py sourcegraph-plan` 查看 Docker Compose 指南；服务可用后设置 `SOURCEGRAPH_URL`，需要鉴权时再设置 `SOURCEGRAPH_TOKEN`。

**测试是否生效：**

```
Use ai-coding-workflow to explain how to install the workflow in this repo.
```

如果 Codex 能回答并引用此 Skill 的安装器，说明 Skill 已生效。

---

## 场景 B：引导新项目

Skill 安装完成后，引导任意仓库。每个项目只需执行一次。这一步会在项目中创建 `ai/dispatch-to-claude.sh` 以及其他本地工作流文件。

### Windows PowerShell

```powershell
cd E:\path\to\your-new-project
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py .
```

### macOS / Linux

```bash
cd /path/to/your-new-project
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py .
```

这会在项目中生成或更新以下文件：

```
AGENTS.md
CLAUDE.md
ai/task-card-template.md
ai/evidence-packet-template.md
ai/plan-task-template.md
ai/plan-findings-template.md
ai/plan-progress-template.md
ai/README.md
ai/dispatch-to-claude.sh
ai/check-worktree.sh
ai/code-search-service.py
ai/locate-code.py
ai/review-with-codex.sh
ai/run-codex-spark.sh
ai/run-parallel-loop.sh
ai/run-loop.sh
ai/doctor_workflow.py
ai/clean_runtime.py
ai/install_context_tools.py
ai/summarize-loop-run.py
ai/benchmark-loop-runs.py
ai/init-spec.py
ai/plan-to-task-cards.py
ai/init-plan.py
ai/session-catchup.py
.worktrees/.gitkeep
```

---

## 更新现有项目

再次运行相同命令。安装程序使用托管块来保留项目特定规则：

```powershell
# Windows
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py .
```

```bash
# macOS / Linux
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py .
```

默认情况下，`ai/` 下已经存在的 plain workflow 文件不会被覆盖。如果它们和已安装 Skill 不一致，安装器会报告 `outdated`。更新 Skill 后，要刷新已引导项目，请运行：

```bash
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py . --update-workflow-files
```

或者在 Skill 克隆目录中运行：

```bash
python scripts/update_skill.py --bootstrap-current
```

---

## 日常工作流程

工作流是一个显式循环：**观察  ->  计划  ->  调度  ->  执行  ->  验证  ->  审查  ->  学习  ->  重复。**

**核心原则：** Codex 负责设计和审查。Claude 负责编辑。工具优先收集低 token 证据。Codex 保持在低 token 上下文预算内；宽泛读取和多文件工作委托给 Claude。Claude 返回压缩证据（摘要 + 产物路径），而非粘贴大段日志。

对于非平凡修改，优先把 Claude 工作拆成两个角色：

- **Builder Claude** 负责按任务卡完成限定范围内的实现，并报告实现方向。除非任务卡明确允许窄范围 sanity check，否则不写 acceptance tests，也不运行大型测试套件。
- **Checker/Test Claude** 在 Codex 接受 Builder 方向后运行。它负责编写或更新被指派的测试、执行验证命令并报告证据，不做大范围实现重写。

任务卡可以要求 Claude 在编辑前执行 **Direction / Boundary Acknowledgement**。Claude 需要复述目标、范围、明确不做的边界、可能触碰的文件、验收标准理解、测试职责、困惑和风险。这是一个门禁，不是反复讨论循环：除非 Codex 实质性改变目标、范围、边界或风险，每个任务或阶段最多允许一次阻塞确认。Codex 必须给出唯一最终决策：proceed、narrow-once/re-dispatch、split 或 stop。

对于目标还不够明确的功能、UX、API 或数据模型改动，先写一个短 spec：

```bash
python ai/init-spec.py "功能或改动名称"
```

spec 用来记录期望行为、非目标、验收面、约束、备选方案和风险。任务卡中填写 `Spec Gate` 并链接该 spec。`ai/init-plan.py` 会创建带有 `### Task N: ...` 小节的 `task_plan.md`；评审这些小节后，可以生成小范围任务卡：

```bash
python ai/plan-to-task-cards.py ai/plans/PROJ-123/task_plan.md
```

bugfix 和 regression 修复前填写 `Root Cause Gate`。验收关键行为需要测试先行时，填写 `Test-First / TDD Contract`，明确生产代码修改前的 red evidence 和实现后的 green evidence。声明分支 ready 前，填写 `Finish Branch Gate`，记录新鲜验证和 artifact 分类。

阶段权责必须显式写清楚：

| 阶段 | Codex 负责 | Claude 负责 |
|------|------------|-------------|
| Observe / Plan | 证据、范围、任务卡、验收标准、职责门禁 | 除非被派发探索任务，否则不参与 |
| Builder Execute | 观察进度和审查实现方向 | 限定范围实现、更新进度、报告方向 |
| Direction Review | 决定等待、修订、拆分、派发 checker-test，或在阈值满足时接管 | 报告 blocker，避免反复确认 |
| Checker/Test | 派发验证任务并审查证据质量 | 被指派的测试、验证命令和失败证据 |
| Final Review | accept / revise / split / reject；人工合并保持独立 | 除非再次派发，否则不参与 |

小型低风险修改可以走 Codex-only fast path，不必派发 Claude。仅当改动局部、预计最多触及两个小文件、不需要广域上下文、没有 public API / 数据模型 / 安全 / 迁移 / 权限 / 并发 / 跨模块契约风险，并且有窄验证或明确的跳过验证理由时才使用。需要记录为什么没有派发 Claude、触及文件、验证证据，以及什么条件会升级回 Claude。只要 scope 扩大或出现不确定性，就停止 fast path，回到 task-card + Claude dispatch。

当 Claude 看起来卡住时，先归因再判断：任务卡歧义、混合角色任务、dirty source/stale HEAD、权限或审批拦截、长时间验证、缺少进度产物、外部环境，还是确实无进展。

权限或审批拦截包括 sandbox 写入被拒、禁止修改的文件、CLI 未认证、网络受限命令、需要人工批准的命令，以及任务卡明确写出的“不要读取或修改”路径。这类情况应写入 progress/report 产物，并按环境或编排 blocker 处理；只有在 Claude 忽略了可用的合规路径时，才应归因为 Claude 执行问题。

dirty source 或 stale HEAD 也应按同类逻辑处理：它会阻止可靠委托，但本身不是 Codex 接管实现的理由。应先恢复委托路径，例如提交已接受阶段、stash/patch 未提交改动、刷新 workflow 文件、从更新后的 HEAD 重新派发、请求明确的 dirty-source 派发批准，或停止等待人工处理。

**步骤 1：初始化项目**（一次性）

```powershell
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py .
```

**步骤 2：创建任务卡**（在 Codex 中  -  观察 + 计划）

```
Use ai-coding-workflow to create a task card for implementing <功能>.
```

对于有明确完成标准的循环任务，请填写任务卡中的 `Goal Loop Contract`。优先写清楚 success signal、最大尝试次数、重复失败停止阈值、无改进停止阈值、回归停止规则、必须提供的证据和 benchmark tags。宽泛或有歧义的工作先填 `Spec Gate`，bugfix/regression 修复先填 `Root Cause Gate`，需要 red-green 证据时填 `Test-First / TDD Contract`，声明 ready for merge 前填 `Finish Branch Gate`。需要更强模型、Codex reviewer 或人工专家在高风险工作前给建议时，填写 `Advisor Gate`，记录咨询时机、调用上限、输出预算、结果可见性、冲突调和和 fallback 行为。`Unknowns` 则用于记录 blindspot scan、会改变架构的问题、参考样例，以及 Claude 偏离原计划时应记录到哪里。

dispatch 默认使用 `balanced` execution profile：compact Claude task card、brief prompt、fresh worktree、完整 diff evidence。这会减少 prompt/task-card token，同时保留审查证据链。完整 Codex planning card 仍会复制为 `TASK_CARD_FULL.md`。

对于有歧义或高风险的任务，使用 `safe` 恢复 standard prompt 和非 compact execution card：

```bash
CLAUDE_CODE_EXECUTION_PROFILE=safe \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

只有在填写 large-repo gate 并接受证据取舍后，才使用 `fast-large-repo`：

```bash
CLAUDE_CODE_EXECUTION_PROFILE=fast-large-repo \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

`fast-large-repo` 会使用 managed reuse worktree、跳过无关 untracked 扫描，并写入 summary diff evidence 而不是完整 patch 文本。它不会 reset 源仓库。如果 `.worktrees/reuse/claude-managed` 已存在，请先保留或审查其中证据，再显式加入 `CLAUDE_CODE_REUSE_WORKTREE_RESET=1`，只 reset 这个受管 worktree。

大型仓库派发前应填写 `Claude Context Packet`。它应该很小、面向执行：目标文件/模块、相关符号、source-of-truth 示例、Claude 不应读取或修改的路径、已知约束，以及窄验证命令。如果这个 packet 不完整，Claude 应 stop-and-report，而不是重新广域扫描整个仓库。

**默认可选开启：在执行规划中使用 Codex Spark**

如果你的 Codex 额度中 `gpt-5.3-codex-spark` 和强模型额度分开计算，适合的任务可以让 `Codex Spark Gate` 保持 `auto`。Spark 是辅助层，不是默认替代 Claude；优先用更便宜的 Spark 额度判断任务规模和路由，再消耗更贵的 Codex/Claude 强模型上下文。已经知道所需辅助角色时，优先显式传 `--mode`；只有需要低风险自动路由时才用 `auto`。如果 CLI、模型权限、auth、网络、Spark 额度不可用，或本地 helper 因 app-server 初始化需要写权限而失败，helper 会写入 auto-disabled report 并返回 0，让主 Claude/Codex 流程继续：

- `auto`：默认角色选择。普通派发前解析为 `task-size-classifier`，Checker/Test 任务解析为 `validation-planner`，失败/无报告 artifacts 解析为 `failure-triage`，diff artifacts 解析为 `review-only`，report/evidence artifacts 解析为 `evidence-checker`。
- `task-size-classifier`：判断任务是 tiny/small/medium/large/unknown，并建议 `codex-fast-path`、`spark-review-only`、`spark-micro-builder`、`claude-builder`、`checker-test`、`spec-first` 或 `human-clarification`。
- `review-only`：快速只读审查任务卡或实现方向。
- `task-card-audit`：派发前检查缺失 gate、职责混合、验收不清和可能导致 Claude 卡住的风险。
- `plan-splitter`：建议更小的 Builder/Checker 任务卡，或可并行的独立切片。
- `validation-planner`：给出精确、低噪音验证命令，不运行广域测试。
- `failure-triage`：在 Claude 卡住/失败后读取有界 artifact 摘要，建议 wait / re-dispatch / narrow / takeover。
- `evidence-checker`：已有 artifacts 后快速检查证据质量。
- `micro-builder`：仅用于任务卡明确允许的极小范围修改，并在 helper 创建的隔离 worktree 中执行；任务卡必须允许 Spark 修改源码、限制为一两个小文件、排除公共 API/契约风险，并给出精确窄验证。

默认 auto 选择只读辅助角色：

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md
```

当 `auto` 解析为 `task-size-classifier` 时，helper 会在 Spark artifact 目录中用 `workspace-write` sandbox 启动 Codex。这样本地 helper 初始化有可写工作目录，但不会给源仓库写权限，且该模式仍禁止修改源代码。

运行证据检查：

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode evidence-checker \
  --artifact .worktrees/claude-<id>.report.md \
  --artifact .worktrees/claude-<id>.checker-report.md
```

派发前运行任务卡审查或验证规划：

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode task-card-audit
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode validation-planner
```

对失败/卡住运行做归因：

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode failure-triage \
  --artifact .worktrees/claude-<id>.status.txt \
  --artifact .worktrees/claude-<id>.progress.log
```

只有任务卡明确允许时，才运行极小范围隔离修改：

```bash
bash ai/run-codex-spark.sh ai/task-cards/PROJ-123.md --mode micro-builder --sandbox workspace-write
```

Spark artifacts 会写入 `.worktrees/codex-spark-*`，包括 `codex-spark.report.md`、`codex-spark.prompt.md`、`codex-spark.result.txt`、`codex-spark.stderr.log`、`codex-spark.artifacts.txt`、`codex-spark.worktree-status.txt`，以及可选的 `codex-spark.diff`。helper 不会静默回退到 GPT-5.5 或其他强模型。只有当 Spark 不可用也应该成为硬失败时，才使用 `--require-spark`。

Spark 输出是建议。把 `accepted_suggestions`、`ignored_suggestions`、`conflicts_with_claude`、`conflicts_with_local_evidence` 和 `acceptance_satisfied_by_spark` 写入 Spark follow-up 表。Spark 不能独立满足验收，不能替代 Claude Builder 责任，也不能批准 Codex 最终 review。

**大型仓库 / 慢文件系统**

如果大型项目里 `git worktree add`、文件系统读取、dispatcher status/diff 收集很慢，先在任务卡里填写 `Worktree / Large Repo Strategy Gate`。默认保留完整证据。当 gate 接受 managed reuse 和 summary evidence 取舍时，优先使用显式 fast profile：

```bash
CLAUDE_CODE_EXECUTION_PROFILE=fast-large-repo \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

如果只想手动开启 managed reuse：

```bash
CLAUDE_CODE_WORKTREE_STRATEGY=reuse-managed \
CLAUDE_CODE_REUSE_WORKTREE_RESET=1 \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

这只会复用 `.worktrees/reuse/claude-managed`，并且只 reset/clean 这个受管 worktree，绝不会 reset/clean 源仓库。
bootstrap 也会确保 workflow runtime artifacts 被忽略：

```gitignore
/.worktrees/*
!/.worktrees/.gitkeep
```

如果未跟踪文件扫描或未跟踪文件 patch 生成太慢，可以使用：

```bash
CLAUDE_CODE_LARGE_REPO_MODE=1 \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

large-repo mode 会保留 tracked/staged diff 证据，但跳过昂贵的无关 untracked 扫描和 untracked patch 证据。使用前应在任务卡中记录这个证据取舍。

如果只想跳过完整 patch 文本、保留 worktree 供审查：

```bash
CLAUDE_CODE_EVIDENCE_MODE=summary \
bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

**实验性：并行派发**

对于文件/模块范围互不重叠的独立任务卡，在每张任务卡中填写 `Parallel Execution Gate`，然后运行：

```bash
bash ai/run-parallel-loop.sh --max-concurrency 2 \
  ai/task-cards/PROJ-123-a.md \
  ai/task-cards/PROJ-123-b.md
```

helper 会并发运行多个 `dispatch-to-claude.sh`，并写入 `.worktrees/parallel-*/parallel-summary.md`、`parallel-events.jsonl`、`parallel-manifest.tsv` 和每个任务的 dispatch 日志。默认情况下，任务卡必须写明 `Parallel allowed? | yes`，否则拒绝派发；多个任务的 `Allowed files/modules` 有重叠时也会拒绝，除非显式传入 `--allow-overlap`。

这只是派发层并行，不会自动合并 worktree，不替代 Codex review，也不会让冲突实现变安全。每个 diff 仍需串行审查；共享 API、数据模型、全局配置等改动应走普通单任务流程，或单独创建人工 reconcile 任务卡。

**可选：为长任务创建持久计划文件**

```bash
python ai/init-plan.py PROJ-123
```

这会创建 `ai/plans/PROJ-123/task_plan.md`、`findings.md` 和 `progress.md`。如果上下文丢失或执行了 `/clear`，可生成恢复上下文：

```bash
python ai/session-catchup.py --plan PROJ-123
```

**步骤 3：调度 Builder Claude**（调度 + 执行）

```
Use the coding executor workflow. Execute this task card and return an evidence packet.
```

对于实现任务，将任务卡模式设为 `builder`。Builder Claude 负责限定范围内的代码修改和进度汇报。如果需要测试，应在任务卡中说明 Builder Claude 完成实现证据后停止，后续由 Codex 单独派发 `checker-test` 任务。

这会在 `.worktrees/` 下生成以下产物：

**代理行为：** `dispatch-to-claude.sh` 默认会在运行 Claude Code 前清理常见代理环境变量（`HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 及其小写形式）。这样 Codex 可以继续使用当前 shell 的代理，而 Claude Code 默认直连。若 Claude Code 必须继承代理，请运行：

```bash
CLAUDE_CODE_PROXY_MODE=inherit bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

**网络诊断：** dispatcher 默认不检查网络状态。若需要记录 Claude 进程及其子进程的元数据级 socket 快照，可运行：

```bash
CLAUDE_CODE_NETWORK_MONITOR=1 bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

这会生成 `*.network.log`，内容包括代理模式、已脱敏的代理设置、诊断工具可用性，以及每次心跳的 socket 状态摘要，例如 `established`、`syn_sent`、`close_wait`。它不会捕获 packet 内容、prompt、request body 或 token。如需额外连通性探测，可设置 `CLAUDE_CODE_NETWORK_HEALTHCHECK_URL`；dispatcher 会运行有边界的 `curl -I` healthcheck，并只把状态/输出写入 network log。

| 产物 | 说明 |
|------|------|
| `*.result.json` | Claude 原始 JSON 输出 |
| `*.status.txt` | Claude 标准错误 / 执行日志 |
| `*.network.log` | 启用 `CLAUDE_CODE_NETWORK_MONITOR=1` 时的可选元数据级网络诊断 |
| `*.diffstat.txt` | 已跟踪文件的 `git diff --stat` |
| `*.diff` | 完整差异，包含未跟踪实现文件 |
| `*.checker-report.md` | `ai/check-worktree.sh` 生成的只检查不修改验证报告 |
| `*.checker-logs/` | checker 命令的完整日志 |
| `*.source-status.txt` | 调度前源仓库状态 |
| `*.worktree-status.txt` | 执行后工作树状态 |
| `*.untracked.txt` | 未跟踪文件列表和 patch 证据 |
| `*.usage.txt` | Claude Token/费用使用摘要 |
| `*.report.md` | Claude 修改报告，供人工和 Codex 审查 |
| `*.claude-progress.md` | Claude 自报的里程碑进度，用于状态展示和审查证据 |
| `*.pid` | 该次调度记录的 Claude 子进程 PID |
| `*.progress.log` | 调度心跳、超时和完成日志 |
| `*.review.txt` | 持久化的 Codex 审查输出 |
| `*.codex-events.jsonl` | 可用时记录的 Codex 原始 JSON 事件 |
| `*.codex-usage.txt` | 可用时记录的 Codex 审查 Token/费用摘要 |

Claude 运行期间，`*.progress.log` 会同时记录产物增长和实现工作树变化。`ai/watch-claude.sh` 与 `ai/status-claude.sh` 会展示部分工作树的 diffstat/status。最初几个等待回合里，如果工作树仍在变化，应先对照任务卡审查部分 diff；若修改方向符合 plan，就继续等待 Claude 完成。只有当部分实现已经偏离 plan、风险过高，或不再产生有效进展时，才考虑中断 Claude。

如果任务卡要求 Direction / Boundary Acknowledgement，Claude 应先写出确认内容再编辑。若该确认是阻塞式审批，Codex 需要给出一次最终决策后 Claude 才继续。Codex 给出 `proceed` 后，Claude 应继续执行任务，不应围绕同一事项反复请求确认。

**步骤 4：Codex 审查实现方向**（审查）

```
Use ai-coding-workflow to review this execution evidence packet and diff. Decide accept / revise / split / reject.
```

要将 checker、token/费用和仓库状态证据纳入审查：

```bash
bash ai/review-with-codex.sh ai/task-cards/PROJ-123.md \
  .worktrees/claude-<id>.result.json \
  .worktrees/claude-<id>.diff \
  .worktrees/claude-<id>.checker-report.md \
  .worktrees/claude-<id>.usage.txt \
  .worktrees/claude-<id>.source-status.txt \
  .worktrees/claude-<id>.worktree-status.txt \
  .worktrees/claude-<id>.untracked.txt
```

如果 Builder 结果符合计划且需要验证，Codex 应再派发一个 `checker-test` 模式任务卡。Checker/Test Claude 编写或更新被指派的测试、运行指定验证命令并报告结果。随后 Codex 执行最终审查；风险较高时，Codex 可以再运行一次二次验证。

**步骤 5：循环或合并**

- 如果 **accept**：人工审查并合并。
- 如果 **revise**：更新任务卡的修订说明，回到步骤 3。
- 如果 **split**：分解为子任务卡。
- 如果 **reject**：重新规划。

**可选：使用循环运行器**

```bash
bash ai/run-loop.sh ai/task-cards/PROJ-123.md 5
```

循环运行器自动执行步骤 3-5，在接受、达到最大迭代次数或人工干预时停止。它还会写入 `.worktrees/loop-<timestamp>/loop-usage-summary.md`，汇总可用的 Claude 和 Codex 使用量。它不会自动合并。

**只检查验证：** 安装后的项目包含 `ai/check-worktree.sh`。优先运行任务卡里的精确验证命令：

```bash
bash ai/check-worktree.sh --task-card ai/task-cards/PROJ-123.md --no-discover --command 'tests=pytest tests/test_target.py'
```

dispatcher 会在 Claude 结束后记录 checker report，但默认关闭广域 discover，避免与当前任务无关的 pytest/ruff/mypy 噪音。需要 dispatcher 复跑精确命令时，传入 `CLAUDE_CODE_CHECKER_COMMANDS=$'tests=pytest tests/test_target.py'`；只有任务卡明确允许广域项目检查时，才设置 `CLAUDE_CODE_CHECKER_DISCOVER=1`。

当传入 `--task-card` 时，checker 也会读取任务卡中的 validation fenced block：

```bash validation
bazel test //path/to:target
```

如果任务卡写明 `Local validation allowed? | no`，checker 会把 artifact collection 报告为 `OK`，把 validation 报告为 `SKIPPED by policy`；它不会运行命令，也不代表测试通过。适用于用户或仓库策略明确禁止本地测试的场景；报告里应只给出人类或 CI 可运行的命令。

**项目测试分层：** 这个 workflow 项目的测试分为快速检查和较慢的集成覆盖。按改动范围选择最小验证层级：

```bash
# Smoke：shell 语法和 whitespace
bash -n scripts/*.sh
git diff --check

# 日常编辑默认
python -m pytest -m "not slow"

# 按改动区域运行相关测试
python -m pytest tests/test_run_codex_spark.py tests/test_check_worktree.py

# 发布前或提交前完整信心
python -m pytest tests
```

标记为 `slow` 的测试会反复创建临时仓库、worktree 或运行 installer。它们应该在发布前、或修改 dispatcher/worktree/install 行为时运行，不适合每次小文档或 helper 改动后都跑。

**Workflow 质量汇总：** `ai/run-loop.sh` 还会写入 `.worktrees/loop-<timestamp>/loop-quality-summary.md` 和 `.json`。也可以手动汇总已有运行：

```bash
python ai/summarize-loop-run.py .worktrees/loop-<timestamp> \
  --output .worktrees/loop-<timestamp>/loop-quality-summary.md \
  --json-output .worktrees/loop-<timestamp>/loop-quality-summary.json
```

汇总报告会固定输出 `Spark Status` 和 `Claude Evidence Classification` 两段。Spark 字段记录 enabled/invoked 状态、mode、model、artifact path、exit code、auto-disable reason、sandbox 和 strong-model fallback 状态。Claude evidence 会分类为 `diff + valid report`、`no report but diff accepted`、`diff without report`、`acknowledgement only`、`seeded report only`、`fallback report`、`valid report without diff` 或 `no useful progress`。

**Workflow benchmark 汇总：** 要把多次 loop run 聚合成轻量 living benchmark：

```bash
python ai/benchmark-loop-runs.py .worktrees/loop-* \
  --output .worktrees/workflow-benchmark.md \
  --json-output .worktrees/workflow-benchmark.json
```

benchmark 会聚合每次运行的 decision、quality score、elapsed time、dispatch 阶段耗时、token/cost、stability findings，并读取任务卡和报告中的 loop type、benchmark tags、advisor usage、Spark invocation/auto-disable/fallback 状态、Spark task-size classification / routing / confidence 与 parallel-dispatch usage。dispatch 阶段耗时包括 Claude startup、Claude execution、checker time 和 artifact finalization，前提是 progress log 中存在这些事件。

**追加式 loop 事件：** `ai/run-loop.sh` 会写入 `.worktrees/loop-<timestamp>/loop-events.jsonl`，记录 run start、iteration start、dispatch complete、review complete、decision、revision task created 和 stop reason。它保留恢复上下文，不重写旧观察。

**结构化进度记忆：** Claude 会维护包含稳定字段的 `CLAUDE_PROGRESS.md`：Goal、Current Phase、Next Check、Blocker、Last Update。这样长任务能持续锚定目标，又不需要把大日志塞回 prompt。

---

## Windows 注意事项

### PowerShell UTF-8 设置

Windows PowerShell 的控制台代码页, `$OutputEncoding` 和子进程编码不一致时，容易把中文等非 ASCII 文本写成乱码或 `?`。在 PowerShell 里编辑或生成中文文档前，先 dot-source helper：

```powershell
. .\scripts\pwsh-utf8.ps1
```

在已安装 workflow 的项目里，使用：

```powershell
. .\ai\pwsh-utf8.ps1
```

如需对后续 PowerShell 会话生效，可选择写入 profile：

```powershell
. .\ai\pwsh-utf8.ps1 -Persist
```

该 helper 会设置 console input/output encoding, `$OutputEncoding`, `PYTHONUTF8`, `PYTHONIOENCODING` 和 code page `65001`。优先使用它，不要临时手写 `chcp` 或用包含中文的 PowerShell here-string 做文本替换。

在 Windows 上，PATH 中的 `bash` 可能解析为 WSL 而非 Git Bash。如果 WSL 没有默认发行版，直接调用 `bash -n` 会失败。这并不意味着脚本无效。

安装程序（`install_workflow.py`）会显式搜索 Git Bash，当 bash 不可用时报告 `WARN_SKIPPED`，不会将其视为硬性失败。

**解决方案：**
1. 安装 Git for Windows，确保 `C:\Program Files\Git\bin` 在 PATH 中位于 WSL 之前
2. 安装 WSL 发行版（`wsl --install -d Ubuntu`）
3. 通过安装程序验证，而不是直接运行 `bash -n`

---

## 调度可观测性

`dispatch-to-claude.sh` 在 Claude Code 运行期间会在 `.worktrees/` 下写入 PID 和心跳日志：

- `.worktrees/claude-<id>.pid` 记录 Claude 子进程 PID。
- `.worktrees/claude-<id>.progress.log` 记录启动、心跳、超时和完成事件。
- `CLAUDE_CODE_HEARTBEAT_SECONDS` 控制心跳频率，默认 `30`。
- `CLAUDE_CODE_TIMEOUT_SECONDS` 控制最长运行时间，默认 `600` 秒；设为 `0` 可禁用超时。
- `CLAUDE_CODE_NO_OUTPUT_TIMEOUT_SECONDS` 可选地在 result/status/report/progress 产物长期无变化时停止 Claude；默认 `0` 为禁用，仅在需要快速失败时设为正数。

`dispatch-to-claude.sh` 会在 Claude 启动后和完成摘要中直接打印可复制的 `Watch Progress` 和 `Watch Details` 命令，用户无需打开文档或 artifact 文件就能在 Codex CLI 查看进度。

`watch-claude.sh` 默认展示低成本状态面板：运行状态、elapsed/quiet 秒数、基于 checklist 的进度条、最新里程碑、artifact 大小和简短 stuck-run 分析。除非传入 `--details`，或同一运行已经连续多次出现可疑快照，否则不会打印完整 progress/status/network 尾部。默认升级规则是连续 3 次可疑快照；可用 `--escalation-confirmations` 或 `CLAUDE_CODE_MONITOR_ESCALATION_CONFIRMATIONS` 调整。

`watch-claude.sh` 和 `status-claude.sh` 还会打印机器可读监控字段（`monitor_level`、`action`、`evidence_state`、quiet/elapsed 秒数，以及可用时的 suspect count）。Codex 应优先读取这些低 token 字段，再决定是否展开完整 status、progress 或 network tail。

监控优先级应保守编排，尽量避免误杀 Claude：

1. L0：先看紧凑版 `watch-claude.sh` heartbeat/progress。
2. L1：当 worktree 有变化时审查 partial diff；若符合任务卡就继续等待。
3. L2：连续多次可疑快照后，再调用 `status-claude.sh` 或 watch details。
4. L3：超过 interrupt window 后，再综合 progress、status、diff、process 和可选 network 诊断。
5. L4：只有多个证据源都表明有效进展不太可能时，才使用 `kill-claude.sh`。

即使 Claude 超时或非零退出，调度器仍会尽量收集 diffstat、diff、未跟踪文件、usage fallback、worktree status 和 fallback report。

对于复杂或多次修订的任务，在任务卡中添加 `## Execution Phases` 表。Claude 必须将它作为外层执行合同，在阶段边界更新进度，并在长时间验证或跨过停止门之前写入 `CLAUDE_REPORT.md`。
```bash
CLAUDE_CODE_TIMEOUT_SECONDS=600 CLAUDE_CODE_HEARTBEAT_SECONDS=15 \
  bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

---

## 控制面例外

默认角色分工是：Codex 负责规划/审查，Claude Code 负责修改。例外情况是 workflow 控制面自身损坏：如果 dispatcher、installer、review script 或 loop runner 阻止了安全分发或证据收集，Codex 可以在记录任务卡和验证证据后做窄范围热修。该例外只用于恢复 workflow 本身；普通产品/代码修改仍应回到 Claude Code 执行。

## Claude 调度运维

当 Claude 运行缓慢、卡住或需要清理时，可以使用以下辅助脚本：

```bash
# 查看最近一次 Claude 运行状态，或传入具体 claude-<timestamp> id
bash ai/status-claude.sh
bash ai/status-claude.sh claude-20260701-093934

# 只停止该 dispatch 的 PID artifact 记录的 Claude 进程
bash ai/kill-claude.sh claude-20260701-093934

# 移除已停止的 worktree，同时保留 .worktrees/claude-<id>.* 证据 artifact
bash ai/cleanup-worktree.sh claude-20260701-093934

# 只预览某一次已停止 dispatch 及其相邻运行时产物
python ai/clean_runtime.py --task-id claude-20260701-093934

# 只删除这一次 dispatch 的运行时产物
python ai/clean_runtime.py --task-id claude-20260701-093934 --apply
```

`cleanup-worktree.sh` 会在记录的 Claude PID 仍存活时拒绝运行。仅当 `git worktree remove` 因损坏或 dirty worktree 需要时才使用 `--force`。
`clean_runtime.py --task-id ...` 适合大仓库恢复场景，因为它避免广域 root artifact 清理，并保留其他 dispatch。

---

## 安全策略

以下操作需要**明确的人工批准**才能执行：

- 破坏性命令（如 `rm -rf`、`DROP TABLE`、`git push --force`、`git reset --hard`）
- 文件删除
- 数据库迁移
- 认证或授权变更
- 计费或支付变更
- 部署或基础设施变更
- 公共 API 表面变更
- 密钥或凭据编辑（API 密钥、令牌、密码）
- 生产数据变更

智能体不得擅自执行上述操作。如有疑问，请停止并询问人工。

---

## 验证安装

运行以下命令确认安装成功：

```powershell
# Windows PowerShell
mkdir $env:TEMP\ai-workflow-test
cd $env:TEMP\ai-workflow-test
git init
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py .
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py .
```

```bash
# macOS / Linux
mkdir /tmp/ai-workflow-test
cd /tmp/ai-workflow-test
git init
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py .
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py .
```

预期结果：
- AGENTS.md 存在
- CLAUDE.md 存在
- ai/ 目录存在
- ai/doctor_workflow.py 存在
- .worktrees/.gitkeep 存在
- 第二次运行报告文件未变/已跳过

**运行工作流 doctor 检查就绪状态：**

```bash
python ai/doctor_workflow.py
```

如果 doctor 报告 `Project workflow is not bootstrapped`，按它打印的 bootstrap 命令先引导项目。仓库没有本地 `ai/` 工作流目录时，不能直接运行 `bash ai/dispatch-to-claude.sh ...`。

**清理运行时产物：**

```bash
# 预览将要删除的内容（干运行）
python ai/clean_runtime.py

# 实际删除产物
python ai/clean_runtime.py --apply

# 大仓库：只预览某一次已停止 dispatch 及其相邻产物
python ai/clean_runtime.py --task-id claude-20260709-120000

# 大仓库：只删除这一次 dispatch 的运行时产物
python ai/clean_runtime.py --task-id claude-20260709-120000 --apply
```

**检查上下文工具：**

```bash
# 检查哪些 LSP/代码检查工具可用（只读）
python ai/install_context_tools.py

# 显示某个 profile 的安装命令（干运行）
python ai/install_context_tools.py --apply python --manager npm

# 实际安装（需要 --apply、--manager 和 --yes）
python ai/install_context_tools.py --apply python --manager npm --yes
```

上下文工具助手检查常见的 LSP、代码检查和代码智能工具（pyright、ruff、mypy、typescript-language-server、gopls、rust-analyzer）。默认调用为只读。实际执行包管理器命令需要三个标志：`--apply PROFILE`、`--manager MANAGER` 和 `--yes`。

注意：安装上下文工具二进制文件不会自动将它们暴露为 Codex LSP/codegraph 工具。Codex 代理需要单独配置才能使用它们。

---

## 开发验证

修改安装器或工作流脚本前，运行本地 smoke tests：

```powershell
python -m unittest discover -s tests -v
```

测试只使用 Python 标准库，覆盖安装器幂等性、managed block 用户内容保留、`CLAUDE.md` import 位置，以及 Codex skill 复制时的运行时产物排除规则。

## 许可证

MIT 许可证 - 详见 [LICENSE](LICENSE)

## 链接

- GitHub 仓库: https://github.com/luozj1020/ai-coding-workflow
- 问题反馈: https://github.com/luozj1020/ai-coding-workflow/issues
