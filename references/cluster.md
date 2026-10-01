# 运行与集群参考

适用于资源选择、源码上传、训练环境、作业监控和常见故障。首次接入和共享账号授权见 [access.md](access.md)。

## 执行模式

在技能目录运行 `python scripts/submit_training.py --help` 查看完整参数。脚本接受单个源码文件或项目目录。

| 模式 | 行为 |
| --- | --- |
| `--plan` | 完全本地：读取配置、检查上传范围和大小、输出文件清单、资源与 sbatch 预览；不调用 SSH 或 SCP |
| `--dry-run` | 连接集群、上传源码并准备远端目录与 sbatch 文件；不提交作业 |
| 普通运行 | 完成远端暂存，然后调用 `sbatch` |

`--plan` 与 `--dry-run` 用途不同；按用户要求选择。两种预览模式均不能证明实际训练可运行。

```powershell
python scripts/submit_training.py --source 'C:\path\to\train.py' --host a800 --gpus 1 --plan
python scripts/submit_training.py --source 'C:\path\to\project' --command 'python3 train.py --epochs 1' --task-size medium --dry-run
```

对于 `.py` 文件，省略 `--command` 时使用 `python3 <文件名>`。对于目录，依次寻找项目根目录的 `train.py`、`main.py`、`run.py`；其他布局需要提供命令。训练命令在上传后的项目目录中执行。

## 资源选择

显式 `--host` 和 `--partition` 优先于自动选择，主机与分区必须符合本地配置。

| `--task-size` | 自动选择集群的倾向 | 默认 GPU 数量 | 默认时长 |
| --- | --- | --- | --- |
| `small` | x090 | 1 | 4 小时 |
| `medium` | A800 | 1 | 12 小时 |
| `large` | A800 | 4 | 24 小时 |
| `auto` | 1 GPU 用 x090；2+ GPUs 用 A800 | 1 | 4 小时 |

显式请求 2+ GPUs 且未指定主机或分区时优先 A800。默认 CPU 数量为每张 GPU 12 个，至少按 1 张 GPU 计算；内存为每 CPU `4G`。可通过 `--gpus`、`--cpus`、`--mem-per-cpu`、`--time` 覆盖。CPU 作业可显式指定 `--gpus 0` 和需要的 CPU 数量。

示例配置使用 A800 的 `a800` 分区，以及 x090 集群的 `fnlp-x090`、`fnlp-4090`、`fnlp-3090`、`fnlp-a800` 分区。它们是可编辑的配置值，不代表每个账号都获授权访问所有分区。

| 参数 | 用途 |
| --- | --- |
| `--config PATH` | 选择配置文件 |
| `--namespace NAME` | 覆盖本次运行的个人命名空间 |
| `--host auto\|NAME` | 选择配置中的集群名，例如 `a800`、`x090` |
| `--partition auto\|NAME` | 使用默认分区或该集群允许的分区 |
| `--account NAME` / `--qos NAME` | 覆盖配置中的 Slurm account / QOS |
| `--nodes N` | 节点数量 |
| `--ntasks N` / `--ntasks-per-node N` | Slurm task 数量 |
| `--job-name NAME` / `--run-name NAME` | 自定义作业名称或本次运行名称 |
| `--conda-env NAME` | 激活远端已有 conda 环境 |
| `--conda-sh PATH` | 指定远端 conda 初始化脚本 |
| `--cuda-module NAME` | 加载指定 CUDA module |
| `--pre-cmd COMMAND` | 在训练命令前运行远端 shell 命令 |
| `--exclude PATTERN` | 添加上传排除规则；可多次使用 |
| `--max-upload-mb N` | 排除后文件的未压缩总大小上限，默认 200 MiB |

Slurm 资源申请参数不会自动把 Python 脚本改为分布式训练。多进程或多节点训练需要正确的 `srun`、`torchrun` 或项目自己的 launcher 命令，并与申请资源保持一致。

## 上传和环境

先用 `--plan` 查看实际上传清单。脚本默认排除常见版本库、缓存、虚拟环境、训练输出与凭据文件；额外数据目录应通过 `--exclude` 排除。自定义文件名中的敏感内容无法仅靠默认规则识别，检查清单后再上传。

上传清单只包含普通文件，符号链接、Windows junction 和其他特殊文件会跳过。额外排除项支持相对路径、任意层级的文件或目录名、glob 模式，例如 `--exclude data --exclude '*.pt'`。

数据集、模型和 checkpoint 通常引用已有远端路径。若确实需要上传较大文件，显式调整范围与 `--max-upload-mb`。本地绝对路径不能直接用作远端训练的数据路径。

训练环境需预先就绪。不要假设远端已安装 conda 或存在 `python` 命令；默认命令使用 `python3`。非标准 conda 安装可提供 `--conda-sh`：

```powershell
python scripts/submit_training.py --source 'C:\path\to\project' --command 'python train.py --data /path/to/remote/data' --host a800 --conda-sh '/path/to/conda/etc/profile.d/conda.sh' --conda-env training --plan
```

`--command` 和 `--pre-cmd` 按远端 shell 命令执行。使用 shell 变量时，注意本地 shell 的引号规则；PowerShell 中单引号可保留命令里的 `$` 字符供远端解释。

## 提交后检查

保存脚本输出中的远端运行目录和作业 ID，并使用输出给出的具体命令与日志路径。下列 `JOB_ID`、`RUN_DIR`、`LOG_FILE` 均为需要替换的占位符。

```powershell
ssh fudan-a800 'squeue -j JOB_ID'
ssh fudan-a800 'scontrol show job JOB_ID'
ssh fudan-a800 'tail -n 60 /path/to/LOG_FILE'
```

需要集群概况时：

```powershell
ssh fudan-a800 'sinfo -o "%P %a %l %D %t %N"'
ssh fudan-a800 'squeue -u "$(id -un)"'
```

共享账号的队列包含其他人的作业。只取消已确认属于用户当前任务的具体 ID：

```powershell
ssh fudan-a800 'scancel JOB_ID'
```

实际使用 x090 时，将别名换为配置中的 x090 别名。不要按共享用户名批量取消作业。

## 常见问题

- **SSH 超时或拒绝连接**：核对网络是否已连通、主机与端口是否正确。无法建立连接时先联系维护者确认入口。
- **Permission denied (publickey)**：核对 SSH config 的用户、密钥路径、agent 与该入口的公钥授权。不要发送私钥排查。
- **invalid partition / account / QOS**：确认维护者提供的有效值，并同步 `config.local.json`。
- **远端目录不可写**：确认 `remote_root` 属于获授权的可写位置；共享账号下使用个人命名空间。
- **作业 Pending**：查看 `squeue -j JOB_ID` 的等待原因。已提交的作业不需要因为排队而反复提交。
- **提交连接中断，是否成功未知**：先检查本次目录与队列中的作业名称/时间，再决定是否重试。
- **作业立即退出**：查看 stderr 与 `scontrol show job JOB_ID`，核对环境初始化、训练入口、数据权限和资源需求。
- **CUDA/conda 错误**：在对应集群确认环境路径与模块名；不要把缺少环境当作需要增加 GPU 的信号。

调试计算仍应使用 Slurm 分配的资源。脚本的本地测试无法替代目标集群上的授权与运行验证。
