# AI Coding Workflow Skill

一个可复用的 Codex / Claude Code 工作流技能，用于将多智能体编码工作流安装到软件仓库中。

[English](README.md) | 中文

## 功能说明

ai-coding-workflow 可以为仓库自动配置：
- `AGENTS.md` - 所有智能体的共享规则
- `CLAUDE.md` - Claude Code 执行规则
- 任务卡和证据包模板
- Codex + Claude Code 工作流的安全调度/审查脚本
- 幂等更新的托管块（managed blocks）

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
    operating-model.md  ← 智能体角色和交接模型
    review-policy.md    ← 代码审查分工
    mcp-policy.md       ← 信息检索顺序
  scripts/
    install_workflow.py ← 引导仓库
    install_for_codex.py← 安装技能供 Codex 发现
    dispatch-to-claude.sh← 向 Claude Code 分发任务卡
    review-with-codex.sh← 向 Codex/GPT 发送证据审查
```

## 安装为 Codex 技能

### Windows PowerShell

```powershell
git clone https://github.com/luozj1020/ai-coding-workflow.git

$dst = "$env:USERPROFILE\.codex\skills\ai-coding-workflow"
Remove-Item -Recurse -Force $dst -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\skills" | Out-Null
Copy-Item -Recurse -Force ".\ai-coding-workflow" $dst
```

或使用安装脚本：

```powershell
cd ai-coding-workflow
python .\scripts\install_for_codex.py
```

### macOS / Linux

```bash
git clone https://github.com/luozj1020/ai-coding-workflow.git
mkdir -p ~/.codex/skills
rm -rf ~/.codex/skills/ai-coding-workflow
cp -R ai-coding-workflow ~/.codex/skills/ai-coding-workflow
```

## 引导仓库

安装技能后，引导任意仓库：

### Windows PowerShell

```powershell
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py E:\path\to\repo
```

### macOS / Linux

```bash
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py /path/to/repo
```

## 更新现有仓库

再次运行相同命令。安装程序使用托管块来保留项目特定规则：

```powershell
# Windows
python $env:USERPROFILE\.codex\skills\ai-coding-workflow\scripts\install_workflow.py E:\path\to\repo
```

```bash
# macOS / Linux
python ~/.codex/skills/ai-coding-workflow/scripts/install_workflow.py /path/to/repo
```

## 典型工作流程

1. **生成任务卡** - 使用 `ai/task-card-template.md`
2. **收集证据** - 优先使用 LSP/codegraph/MCP
3. **分发给 Claude Code** - `bash ai/dispatch-to-claude.sh ai/task-cards/PROJ-123.md`
4. **Codex 审查** - `bash ai/review-with-codex.sh ai/task-cards/PROJ-123.md .worktrees/claude-<timestamp>/result.json .worktrees/claude-<timestamp>/diff.patch`
5. **人工审查** - 最终合并

## Windows 注意事项

在 Windows 上，PATH 中的 `bash` 可能解析为 WSL 而非 Git Bash。如果 WSL 没有默认发行版，直接调用 `bash -n` 会失败。这并不意味着脚本无效。

安装程序（`install_workflow.py`）会显式搜索 Git Bash，当 bash 不可用时报告 `WARN_SKIPPED`，不会将其视为硬性失败。

**解决方案：**
1. 安装 Git for Windows，确保 `C:\Program Files\Git\bin` 在 PATH 中位于 WSL 之前
2. 安装 WSL 发行版（`wsl --install -d Ubuntu`）
3. 通过安装程序验证，而不是直接运行 `bash -n`

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

## 验证安装

运行验证命令确认安装成功：

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
- .worktrees/.gitkeep 存在
- 第二次运行报告文件未变/已跳过

## 许可证

MIT 许可证 - 详见 [LICENSE](LICENSE)

## 链接

- GitHub 仓库: https://github.com/luozj1020/ai-coding-workflow
- 问题反馈: https://github.com/luozj1020/ai-coding-workflow/issues
