# 接入与共享账号配置

适用于首次接入、换电脑、添加集群入口或排查 SSH 认证。网络连接应已由学校或实验室允许的方式准备好。

技能安装和集群授权是分别完成的步骤。安装方法及可直接交给 Codex 的首次配置提示词见 [README](../README.md#配置与第一次运行)。代理可以读取模板、协助填写本机配置并运行检查；主机地址、账号授权、主机指纹和允许使用的资源仍需由使用者或集群维护者提供。

## 向维护者确认的信息

对每个需要使用的集群入口分别确认以下内容：

| 信息 | 由谁提供 | 填写位置或用途 |
| --- | --- | --- |
| 登录主机地址 | 集群维护者 | SSH config 的 `HostName`；A800 与 x090 分别填写 |
| SSH 端口 | 集群维护者 | SSH config 的 `Port` |
| 获授权的登录用户名 | 集群维护者 | SSH config 的 `User`；可为维护者允许多人使用的共享账号 |
| 本机私钥文件路径 | 使用者在本机确定 | SSH config 的 `IdentityFile`；私钥由本人保存 |
| 待授权的公钥 | 使用者提供 `.pub` 文件 | 交给维护者；无需提供私钥 |
| 公钥授权状态 | 集群维护者确认 | 确认此人的公钥在每个所需入口或其共用认证系统上已授权 |
| 可信的主机密钥指纹 | 集群维护者通过可信渠道提供 | 首次连接时核对服务器身份 |
| 可选的跳板机别名 | 维护者提供入口，使用者配置别名 | 仅在维护者要求时配置 `ProxyJump`，并单独配置跳板机认证 |

不需要向技能维护者、Codex 对话或 GitHub 提供账号密码或私钥。实际主机地址、账号与本地路径保存在个人机器的配置中。

Slurm 和训练还需要以下信息：

| 信息 | 由谁提供 | 填写位置或用途 |
| --- | --- | --- |
| 允许使用的分区 | 集群维护者 | `clusters.<name>.allowed_partitions` 与 `default_partition` |
| Slurm account / QOS | 集群维护者 | 集群要求时填 `account` / `qos`；否则留空 |
| 可写的远端暂存根目录 | 维护者确认，使用者选择 | `remote_root`；可用 `~/codex_runs` 或获授权的绝对路径 |
| 个人运行命名空间 | 使用者选择并避免重名 | `run_namespace`，例如 `student-a` |
| Python/conda 环境与初始化脚本路径 | 使用者或环境维护者 | 对应 `--conda-env`、必要时 `--conda-sh` |
| 所需 CUDA module | 环境维护者 | 环境使用 Modules 时对应 `--cuda-module` |
| 数据与模型的远端路径 | 使用者或数据维护者 | 放入训练命令；确认账号具有所需访问权限 |

## 每人生成自己的 SSH 密钥

共享账号可以接受多人的公钥，每个人仍应使用自己生成的密钥。选一个尚不存在的本机密钥文件名；如果提示文件已存在，停止生成并使用另一个名字，避免覆盖已有密钥。

PowerShell 示例：

```powershell
New-Item -ItemType Directory -Path (Join-Path $HOME '.ssh') -Force | Out-Null
ssh-keygen -t ed25519 -f "$HOME/.ssh/fudan_slurm_personal" -C 'fudan-slurm-personal'
```

Linux/macOS 示例：

```bash
mkdir -p ~/.ssh
ssh-keygen -t ed25519 -f ~/.ssh/fudan_slurm_personal -C 'fudan-slurm-personal'
```

按提示设置密钥口令；需要非交互使用时，可在自己的 SSH agent 中加载密钥。将生成的 `fudan_slurm_personal.pub` 发给集群维护者，由维护者安装到已授权账号的 SSH 公钥列表。无 `.pub` 后缀的文件是私钥，留在本机，不放进仓库、训练源码或聊天。

A800 与 x090 的登录入口可能使用独立认证；一个入口成功不代表另一个也已授权。人员退出时，维护者可移除对应公钥。

## 配置 SSH 别名

打开仓库根目录的 [ssh_config.example](../ssh_config.example)，替换所有占位值，将所需条目合并进本机 `~/.ssh/config`。Windows 默认位置为 `$HOME/.ssh/config`。保留已有条目，避免重复定义相同别名。

默认别名为 `fudan-a800` 与 `fudan-x090`。需要其他别名时，同时更新 `config.local.json` 的 `ssh_alias`。模板中的 `IdentityFile` 指向本人的私钥路径；私钥本身不进入任何 JSON 配置。

首次连接前，取得维护者提供的主机密钥指纹，再分别连接并核对：

```powershell
ssh fudan-a800 'id -un; hostname'
ssh fudan-x090 'id -un; hostname'
```

确认主机身份并完成认证后，检查脚本需要的非交互连接：

```powershell
ssh -o BatchMode=yes fudan-a800 'command -v bash; command -v tar; command -v sbatch; command -v squeue'
ssh -o BatchMode=yes fudan-x090 'command -v bash; command -v tar; command -v sbatch; command -v squeue'
```

只配置和测试实际获授权使用的入口。出现主机密钥变化时，向维护者确认；不要通过禁用主机身份检查解决。若 `BatchMode` 认证失败，检查别名、用户、`IdentityFile`、SSH agent 以及公钥是否在该入口获授权。

## 配置个人运行目录

将 [config.example.json](../config.example.json) 复制为技能根目录下的 `config.local.json`；已有本地配置时直接编辑并保留现有值。

配置结构：

```json
{
  "run_namespace": "YOUR_RUN_NAMESPACE",
  "clusters": {
    "a800": {
      "ssh_alias": "fudan-a800",
      "default_partition": "a800",
      "allowed_partitions": ["a800"],
      "remote_root": "~/codex_runs",
      "account": "",
      "qos": ""
    },
    "x090": {
      "ssh_alias": "fudan-x090",
      "default_partition": "fnlp-x090",
      "allowed_partitions": ["fnlp-x090", "fnlp-4090", "fnlp-3090", "fnlp-a800"],
      "remote_root": "~/codex_runs",
      "account": "",
      "qos": ""
    }
  }
}
```

将 `YOUR_RUN_NAMESPACE` 改成维护者可识别、与其他使用者不同的简短名称，例如 `student-a`。命名空间也可以通过单次命令的 `--namespace` 覆盖。运行目录位于 `<remote_root>/<namespace>/<run>`。

`remote_root` 中的 `~` 表示远端登录账号的 home；不要填本机用户目录。示例分区名是可调整的初始模板，实际允许的分区、account 与 QOS 以维护者提供的信息为准。只保留可用集群配置；仅配置一个集群时建议显式传入 `--host`。

SSH 别名和个人命名空间使用英文字母、数字、下划线、点或连字符，首字符为字母或数字。此版本的远端暂存根目录及远端 home 仅支持由英文字母、数字、`/`、`_`、`.`、`-` 构成的 POSIX 路径；路径中不能有空格、中文或 `..`。本地源码路径可以包含空格或中文。

配置查找顺序：

1. 命令行的 `--config PATH`。
2. 环境变量 `FUDAN_SLURM_CONFIG` 指向的文件。
3. 安装技能根目录下的 `config.local.json`。

共享账号下，所有人的 SSH 会话与作业具有同一个 Unix 用户身份。独立密钥便于按人授权和撤销；独立命名空间减少目录冲突。二者都不会建立文件或作业权限隔离。需要权限隔离时，由集群维护者提供独立账号或其他隔离机制。

`config.local.json` 应留在本机；发布修改时保留占位配置模板即可。

## 接入检查与首次验收

以下命令在技能安装目录执行。每个需要使用的集群入口都应单独检查；可只保留并检查自己获授权的入口。

### 1. 本地检查与预览

```powershell
python scripts/doctor.py --config config.local.json
python scripts/submit_training.py --config config.local.json --source examples/smoke.py --task-size small --plan
```

`doctor.py` 默认检查配置是否完整、Python 版本、SSH/SCP 是否可用，以及 `ssh -G` 展开的别名设置。它不会建立 SSH 连接，也不会读取私钥内容、上传文件或提交作业。`--plan` 检查源码清单、资源参数和生成的 sbatch。配置中仍有占位值时，先按检查结果补齐。

### 2. 首次 SSH 认证与远端只读检查

先按本页的 [SSH 别名配置](#配置-ssh-别名) 核对主机指纹，并确认个人公钥已授权、带口令的密钥已在 SSH agent 中可用。随后运行：

```powershell
python scripts/doctor.py --config config.local.json --remote --host a800
```

`--host` 使用本地 JSON 中的集群键名，例如 `a800` 或 `x090`。远端检查有超时限制，检查 SSH 登录、所需命令、Slurm 分区与远端根目录条件，不创建运行目录、不上传代码、不调用 `sbatch`。

可选环境检查示例；占位路径和名称应替换为维护者确认的实际值：

```powershell
python scripts/doctor.py --config config.local.json --remote --host a800 --conda-env ENV_NAME --conda-sh /remote/path/to/conda.sh --cuda-module MODULE_NAME
```

不使用 conda 或 Modules 时，省略相应选项。输出是带通过、警告或失败结果的 JSON；根据具体检查项修复后再运行。`status=ready` 表示没有必需检查失败，仍可能含警告；代理应同时读取 `scope`、`remote_ready`、`checks` 和 `limitations`。本地模式的 `remote_ready` 为 `null`，失败时状态为 `incomplete`、进程退出码为 1。远端登录节点上的环境检查不会分配计算节点或 GPU，无法证明训练的 CUDA 兼容性、数据完整性、Slurm account/QOS 权限或实际资源可用性。

常见定位顺序：

| 检查结果或现象 | 下一步 |
| --- | --- |
| 本地命令缺失 | 安装 Python 3.10+ 或可从终端调用的 OpenSSH；重新打开终端 |
| 别名没有展开为实际主机、用户或端口 | 检查 `~/.ssh/config`、重复 `Host` 条目及配置中的 `ssh_alias` |
| SSH 超时或无法访问 | 确认获允许的网络连接、主机地址、端口与跳板机 |
| 主机指纹未知或变化 | 通过可信渠道向维护者核对，完成首次交互连接 |
| `Permission denied` 或非交互认证失败 | 检查公钥授权、账号、私钥路径及 SSH agent |
| Slurm 命令或分区不可见 | 向维护者确认登录入口、初始化方式和所用分区 |
| 远端目录或环境检查失败 | 检查账号权限、远端 home、根目录或初始化脚本与环境名称 |

检查输出可能包含本机路径、实际 SSH 地址或远端用户名。可以留在本地供代理排查；如需发布到公开 issue，先删除这些信息。

### 3. 小作业与完成状态核验

当使用者已授权本次提交并确认资源和环境后，让代理以 `examples/smoke.py` 为源码生成 `--plan`，选择获授权的集群、分区和最小合适资源；核对后执行提交。示例默认只检查基础 Python 运行。需要进一步验证时，可将训练命令设为 `python3 smoke.py --check-gpu` 检查 GPU 驱动，或 `python3 smoke.py --check-torch` 在已准备好的 PyTorch 环境中运行最小 CUDA 运算。这两个选项需在 Slurm 分配的作业内执行；不要在登录节点直接运行 GPU 测试。即使最小测试通过，实际模型依赖和数据仍需按训练任务核验。

保存这次返回的作业 ID、远端目录及日志路径。使用 `squeue` 查看等待或运行状态，结束后使用 `sacct` 核对该作业的最终 `State` 和 `ExitCode`，并阅读输出与错误日志。`sacct` 记录可能稍有延迟；记录不可用时应明确报告尚未确认，不能将队列为空当作成功。具体命令与边界见 [运行与集群参考](cluster.md)。

共享账号可能同时有其他人的作业。查询、跟踪与取消操作都针对已确认属于本次任务的作业 ID，避免按共享用户名批量操作。
