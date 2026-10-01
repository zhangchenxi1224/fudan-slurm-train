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

将仓库克隆到技能目录中的 `fudan-slurm-train` 文件夹。通常是 `~/.codex/skills/fudan-slurm-train`；如果已设置 `CODEX_HOME`，则使用其下的 `skills/fudan-slurm-train`。

PowerShell 示例：

```powershell
$skillBase = if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME 'skills' } else { Join-Path $HOME '.codex/skills' }
$skillTarget = Join-Path $skillBase 'fudan-slurm-train'
if (Test-Path -LiteralPath $skillTarget) { throw '目标技能目录已存在，请先合并现有技能。' }
New-Item -ItemType Directory -Path $skillBase -Force | Out-Null
git clone https://github.com/zhangchenxi1224/fudan-slurm-train.git $skillTarget
Set-Location -LiteralPath $skillTarget
```

Linux/macOS 可在确认目标目录不存在后运行：

```bash
git clone https://github.com/zhangchenxi1224/fudan-slurm-train.git "${CODEX_HOME:-$HOME/.codex}/skills/fudan-slurm-train"
cd "${CODEX_HOME:-$HOME/.codex}/skills/fudan-slurm-train"
```

若已安装同名技能，先将发布版克隆到另一个临时目录，比较后合并需要的文件，保留现有 `config.local.json`、SSH 配置和本地定制。后续以安装目录中的 `SKILL.md` 为技能入口，不要覆盖整个现有技能目录。

## 配置与第一次运行

1. 按 [接入指南](references/access.md) 为自己生成 SSH 密钥，将公钥交给维护者，并将填好的 [ssh_config.example](ssh_config.example) 条目合并进本机 SSH config。
2. 将 [config.example.json](config.example.json) 复制为同目录下的 `config.local.json`，填好个人 `run_namespace`，确认各集群的别名、分区与可写远端根目录。`config.local.json` 被 Git 忽略。
3. 先对仓库自带的最小示例做本地预览，检查上传清单、资源与生成的 sbatch 脚本；之后换成自己的源码路径。

以下命令在技能仓库目录中运行：

```powershell
python scripts/submit_training.py --config config.local.json --source examples/smoke.py --task-size small --plan
```

配置文件优先级为：`--config PATH` → `FUDAN_SLURM_CONFIG` 环境变量 → 技能根目录的 `config.local.json`。实际 SSH 地址、端口、账号、私钥路径放在本机 SSH config 中。

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

也可以在 Codex 中使用：

> 使用 $fudan-slurm-train，为本地训练项目生成本地预览：命令是 `python3 train.py --epochs 1`，使用 A800、1 张 GPU。先查看上传文件、资源与 sbatch 内容。

## 共享账号下的使用约定

每个人生成并保管自己的私钥，只将 `.pub` 公钥交给维护者，分别确认所需集群入口已授权。各人使用独立 `run_namespace`，让运行目录分开；命名空间只能减少冲突，**不能隔离同一 Unix 账号下的文件权限或作业权限**。

查看队列时，共享账号可能显示其他人的作业。仅操作已确认属于本次任务的作业 ID；不要使用按共享用户名批量取消的命令。

## 能力与验证边界

脚本处理源码暂存、Slurm 参数与作业提交；训练依赖、数据和模型路径需要预先准备。多节点参数用于申请资源，分布式训练的 launcher 仍由训练命令指定。

本仓库的自动测试使用本地文件和模拟 SSH/Slurm 响应；它们不能确认某个集群的实际连通性、账号权限、可用 GPU 或训练环境。首次接入时需在获授权的集群上完成自己的连接和小作业验证。

在仓库目录运行本地测试：

```powershell
python -m unittest discover -s tests -v
```
