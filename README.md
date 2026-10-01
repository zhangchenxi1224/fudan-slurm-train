# fudan-slurm-train Skill

将本地训练代码上传到已获授权的 Fudan 实验室 Slurm 集群，生成并提交 `sbatch` 作业的 Codex 技能。支持 A800 与 x090/fnlp 集群配置、本地预览、远端暂存以及作业队列和日志排查。

本仓库采用 [MIT 许可证](LICENSE)。公开文件仅包含可复用的技能、脚本和配置模板；实际连接地址、账号与本地路径由使用者自行配置。

## 开始前需要什么

- 本地 Python **3.10 或更新版本**，以及可从终端调用的 `ssh`、`scp`。脚本仅使用 Python 标准库。
- 使用下方克隆安装命令需要 Git。
- 已经能通过学校或实验室允许的网络访问集群。
- 获授权的集群账号；支持实验室授权多人使用的共享账号。每人使用自己的 SSH 密钥。
- 远端可用的 Bash、`tar`、Slurm 命令，以及训练需要的 Python/conda 环境、CUDA 和数据。

首次使用请阅读 [接入与共享账号配置](references/access.md)，向维护者取得连接信息并完成公钥授权。无需提供密码或私钥。

只运行 `--plan` 时，准备好 Python、源码和有效的本地 JSON 配置即可；不需要已连接集群，也不会调用 SSH/SCP。可以先预览，再办理远端接入。

## 安装到 Codex

### 直接交给 Codex 安装

复制下面这段话到 Codex 即可；其中 `$skill-installer` 是技能名称，无需替换成本机文件链接：

```text
使用 $skill-installer，从 https://github.com/zhangchenxi1224/fudan-slurm-train 安装技能。技能位于仓库根目录（--path .），名称为 fudan-slurm-train（--name fudan-slurm-train）。先检查是否已经安装同名技能；如已安装，保留现有本地配置并比较更新，避免创建重复副本。安装后阅读 SKILL.md 和 references/access.md，检查缺少哪些本地配置，并告诉我需要补充哪些连接信息。
```

安装技能只添加说明、脚本和模板，实际登录信息需要在下一节完成本地配置。

### 手动安装

Codex 当前的标准技能目录是用户级 `~/.agents/skills` 或项目级 `.agents/skills`。下面按用户级安装，供本机各项目使用；只供某个项目使用时，将目标目录改为该项目的 `.agents/skills/fudan-slurm-train`。[Codex 官方技能说明](https://learn.chatgpt.com/docs/build-skills)

部分 Codex 安装环境中的 Skill Installer 仍使用 `~/.codex/skills` 或 `CODEX_HOME` 下的 `skills`。使用安装器时以其报告的实际安装目录为准；手动安装前也检查这些目录。**同名技能只保留一个生效副本**，避免代理加载到旧版本。

PowerShell 示例：

```powershell
$skillBase = Join-Path $HOME '.agents/skills'
$skillTarget = Join-Path $skillBase 'fudan-slurm-train'
if (Test-Path -LiteralPath $skillTarget) { throw '目标技能目录已存在，请先合并现有技能。' }
New-Item -ItemType Directory -Path $skillBase -Force | Out-Null
git clone https://github.com/zhangchenxi1224/fudan-slurm-train.git $skillTarget
Set-Location -LiteralPath $skillTarget
```

Linux/macOS 可在确认目标目录不存在后运行：

```bash
mkdir -p ~/.agents/skills
git clone https://github.com/zhangchenxi1224/fudan-slurm-train.git ~/.agents/skills/fudan-slurm-train
cd ~/.agents/skills/fudan-slurm-train
```

若已安装同名技能，先将发布版克隆到另一个临时目录，比较后合并需要的文件，保留现有 `config.local.json`、SSH 配置和本地定制。后续以安装目录中的 `SKILL.md` 为技能入口，不要覆盖整个现有技能目录。

### 确认代理已经发现技能

在 Codex 中输入 `$fudan-slurm-train`，检查技能是否可选，并让代理报告读取的 `SKILL.md` 路径。Codex 通常会自动发现技能变更；未出现时先重启 Codex，再检查安装目录、`SKILL.md` 和是否存在同名副本。目录下载成功与当前会话已加载该技能是两个需要分别确认的步骤。

其他代理需支持 `SKILL.md` 技能格式，并能执行本地 Python、SSH/SCP 和访问集群；按其自身的技能安装规则配置。本仓库未逐一验证其他代理，只有聊天或网页访问能力的代理不能直接运行这些脚本。

## 配置与第一次运行

1. 按 [接入指南](references/access.md) 为自己生成 SSH 密钥，将公钥交给维护者，并将填好的 [ssh_config.example](ssh_config.example) 条目合并进本机 SSH config。
2. 将 [config.example.json](config.example.json) 复制为同目录下的 `config.local.json`，填好个人 `run_namespace`，确认各集群的别名、分区与可写远端根目录。`config.local.json` 被 Git 忽略。
3. 执行下方本地检查，完成首次主机指纹核对和 SSH 认证后再做远端只读检查。
4. 对仓库自带的最小示例做本地预览，检查上传清单、资源与生成的 sbatch 脚本；之后换成自己的源码路径。

也可以复制下面这段话，让 Codex 协助首次配置：

```text
使用 $fudan-slurm-train，阅读实际安装目录中的 SKILL.md、README.md 和 references/access.md，检查我现有的 SSH 别名及 config.local.json。保留已有配置，只列出缺少的信息：所需集群入口的主机地址、端口、获授权用户名、个人私钥的本地路径及公钥授权状态、可信主机指纹、必要的跳板机，以及允许的分区、account/QOS、可写远端根目录和个人 run_namespace。不要读取或索取私钥内容、密码。信息齐全后协助完成本地配置，先运行 doctor 本地检查和 smoke.py 的 --plan；我确认网络和 SSH 授权就绪后，再运行所选集群的远端只读检查。首次真实小作业的资源、环境和提交时机由我确认。
```

以下命令在技能仓库目录中运行：

```powershell
python scripts/doctor.py --config config.local.json
python scripts/submit_training.py --config config.local.json --source examples/smoke.py --task-size small --plan
```

配置文件优先级为：`--config PATH` → `FUDAN_SLURM_CONFIG` 环境变量 → 技能根目录的 `config.local.json`。实际 SSH 地址、端口、账号、私钥路径放在本机 SSH config 中。

`doctor.py` 默认检查本地配置、Python、SSH/SCP 和 `ssh -G` 展开的 SSH 设置；不连接集群、不上传文件、不提交作业。网络与认证就绪后，显式选择一个已获授权的集群做有超时限制的远端只读检查：

```powershell
python scripts/doctor.py --config config.local.json --remote --host a800
```

使用 x090 时将 `a800` 换为 `x090`。需要检查指定环境时，可附加 `--conda-env ENV_NAME`、`--conda-sh /remote/path/to/conda.sh` 或 `--cuda-module MODULE_NAME`。结果以 JSON 区分通过、警告和失败。远端检查覆盖 SSH、Slurm 命令与分区、远端暂存目录条件，并按选项检查环境；它不申请 GPU，也不能保证队列空闲、GPU 可用或训练能够成功。详情见 [接入检查](references/access.md#接入检查与首次验收)。

| 模式 | SSH / 上传 / 远端写入 | 提交 Slurm 作业 |
| --- | --- | --- |
| `--plan` | 无；仅本地生成上传清单、资源和 sbatch 预览 | 否 |
| `--dry-run` | 有；上传源码并准备远端运行目录与脚本 | 否 |
| 不带上述选项 | 有 | 是 |

完成预览和 SSH 接入后，可先暂存：

```powershell
python scripts/submit_training.py --source 'C:\path\to\project' --command 'python3 train.py --epochs 1' --task-size medium --dry-run
```

需要真正提交时，去掉 `--dry-run`。保存输出中的作业 ID 和远端目录，以便查看日志与管理本次作业。资源、环境选项和监控方法见 [运行与集群参考](references/cluster.md)。

首次接入的验收顺序为：技能可发现 → 本地检查与 `--plan` → SSH 和远端只读检查 → 获授权的小作业提交 → `sacct` 最终状态、退出码和日志核验。`squeue` 中消失只代表不再出现在队列，不能单凭这一点判定作业成功。

也可以在 Codex 中使用：

> 使用 $fudan-slurm-train，为本地训练项目生成本地预览：命令是 `python3 train.py --epochs 1`，使用 A800、1 张 GPU。先查看上传文件、资源与 sbatch 内容。

## 共享账号下的使用约定

每个人生成并保管自己的私钥，只将 `.pub` 公钥交给维护者，分别确认所需集群入口已授权。各人使用独立 `run_namespace`，让运行目录分开；命名空间只能减少冲突，**不能隔离同一 Unix 账号下的文件权限或作业权限**。

查看队列时，共享账号可能显示其他人的作业。仅操作已确认属于本次任务的作业 ID；不要使用按共享用户名批量取消的命令。

## 能力与验证边界

脚本处理源码暂存、Slurm 参数与作业提交；训练依赖、数据和模型路径需要预先准备。多节点参数用于申请资源，分布式训练的 launcher 仍由训练命令指定。

本仓库的自动测试使用本地文件和模拟 SSH/Slurm 响应；它们不能确认某个集群的实际连通性、账号权限、可用 GPU 或训练环境。

2026-10-01 的验证范围：

| 项目 | 结果 |
| --- | --- |
| 技能格式、从 GitHub 独立下载安装、本地 `--plan` | 通过 |
| `doctor.py` 本地检查及 A800、x090 远端只读检查 | 通过 |
| x090 的单 GPU 小作业：上传 → `sbatch` → Python / GPU 驱动查询 → 记账和日志 | 通过；约 1 秒，作业与 batch 步骤均为 `COMPLETED`、`ExitCode=0:0`，stderr 为空 |
| A800 上的新版本实际提交、PyTorch CUDA 运算、真实模型训练 | 本轮未验证 |

这次实测使用维护者本机已获授权的 SSH 配置；首次使用的新账号或新机器仍应完成自己的连接和小作业验证。仓库仅发布脱敏配置模板，不包含这次测试的实际登录信息与原始远端日志。

在仓库目录运行本地测试：

```powershell
python -m unittest discover -s tests -v
```
