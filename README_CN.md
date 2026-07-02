# AI Coding Workflow Skill

一个可复用的 Codex / Claude Code 工作流技能，用于将多智能体编码工作流安装到软件仓库中。

[English](README.md) | 中文

## 功能说明

ai-coding-workflow 可以为仓库自动配置：
- `AGENTS.md` - 所有智能体的共享规则
- `CLAUDE.md` - Claude Code 执行规则
- 任务卡和证据包模板
- Codex + Claude Code 工作流的安全调度/审查/循环脚本
- 幂等更新的托管块（managed blocks）

## 两个动作

| 动作 | 时机 | 命令 |
|------|------|------|
| **安装 Skill** | 每台电脑一次 | `python scripts/install_for_codex.py` |
| **引导项目** | 每个仓库一次 | `python scripts/install_workflow.py .` |

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
  references/
    loop-model.md       ← 循环状态机和停止条件
    operating-model.md  ← 智能体角色和交接模型
    review-policy.md    ← 代码审查分工
    mcp-policy.md       ← 信息检索顺序
  scripts/
    install_workflow.py ← 引导仓库
    install_for_codex.py← 安装技能供 Codex 发现
    dispatch-to-claude.sh← 向 Claude Code 分发任务卡
    review-with-codex.sh← 向 Codex/GPT 发送证据审查
    run-loop.sh         ← 可选循环运行器（调度 + 审查）
    doctor_workflow.py  ← 调度/审查循环就绪检查（只读）
    clean_runtime.py    ← 预览/清理已忽略的运行时产物
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

**测试是否生效：**

```
Use ai-coding-workflow to explain how to install the workflow in this repo.
```

如果 Codex 能回答并引用此 Skill 的安装器，说明 Skill 已生效。

---

## 场景 B：引导新项目

Skill 安装完成后，引导任意仓库。每个项目只需执行一次。

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
ai/README.md
ai/dispatch-to-claude.sh
ai/review-with-codex.sh
ai/run-loop.sh
ai/doctor_workflow.py
ai/clean_runtime.py
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

---

## 日常工作流程

工作流是一个显式循环：**观察  ->  计划  ->  调度  ->  执行  ->  验证  ->  审查  ->  学习  ->  重复。**

**核心原则：** Codex 负责设计和审查。Claude 负责编辑。工具优先收集低 token 证据。

**步骤 1：初始化项目**（一次性）

```powershell
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py .
```

**步骤 2：创建任务卡**（在 Codex 中  -  观察 + 计划）

```
Use ai-coding-workflow to create a task card for implementing <功能>.
```

**步骤 3：Claude Code 执行**（调度 + 执行 + 验证）

```
Use the coding executor workflow. Execute this task card and return an evidence packet.
```

这会在 `.worktrees/` 下生成以下产物：

**代理行为：** `dispatch-to-claude.sh` 默认会在运行 Claude Code 前清理常见代理环境变量（`HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` 及其小写形式）。这样 Codex 可以继续使用当前 shell 的代理，而 Claude Code 默认直连。若 Claude Code 必须继承代理，请运行：

```bash
CLAUDE_CODE_PROXY_MODE=inherit bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md
```

| 产物 | 说明 |
|------|------|
| `*.result.json` | Claude 原始 JSON 输出 |
| `*.status.txt` | Claude 标准错误 / 执行日志 |
| `*.diffstat.txt` | 已跟踪文件的 `git diff --stat` |
| `*.diff` | 完整差异，包含未跟踪实现文件 |
| `*.source-status.txt` | 调度前源仓库状态 |
| `*.worktree-status.txt` | 执行后工作树状态 |
| `*.untracked.txt` | 未跟踪文件列表和 patch 证据 |
| `*.usage.txt` | Claude Token/费用使用摘要 |
| `*.report.md` | Claude 修改报告，供人工和 Codex 审查 |
| `*.review.txt` | 持久化的 Codex 审查输出 |
| `*.codex-events.jsonl` | 可用时记录的 Codex 原始 JSON 事件 |
| `*.codex-usage.txt` | 可用时记录的 Codex 审查 Token/费用摘要 |

**步骤 4：Codex 审查**（审查）

```
Use ai-coding-workflow to review this execution evidence packet and diff. Decide accept / revise / split / reject.
```

要将 token/费用和仓库状态证据纳入审查：

```bash
bash ai/review-with-codex.sh ai/task-cards/PROJ-123.md \
  .worktrees/claude-<id>.result.json \
  .worktrees/claude-<id>.diff \
  .worktrees/claude-<id>.usage.txt \
  .worktrees/claude-<id>.source-status.txt \
  .worktrees/claude-<id>.worktree-status.txt \
  .worktrees/claude-<id>.untracked.txt
```

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
```

`cleanup-worktree.sh` 会在记录的 Claude PID 仍存活时拒绝运行。仅当 `git worktree remove` 因损坏或 dirty worktree 需要时才使用 `--force`。

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

**清理运行时产物：**

```bash
# 预览将要删除的内容（干运行）
python ai/clean_runtime.py

# 实际删除产物
python ai/clean_runtime.py --apply
```

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
