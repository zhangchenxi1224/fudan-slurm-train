#!/usr/bin/env python3
"""Plan, stage, and submit training code through configured SSH aliases (Python 3.10+)."""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
import posixpath
import re
import secrets
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config.local.json"
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
DEFAULT_EXCLUDES = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".cache", ".tox", ".nox", ".venv", "venv", "env",
    "node_modules", "logs", "output", "outputs", "wandb", "checkpoints",
    "build", "dist", ".ssh", ".aws", ".azure", ".kube", ".gnupg",
    ".config", "secrets", "credentials",
}
SECRET_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.ppk",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "credentials*",
    "secrets.*", "secret.*", "service-account*.json", "service_account*.json",
    ".netrc", ".npmrc", ".pypirc", ".git-credentials", "config.local.json",
)


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    archive_path: str
    size_bytes: int


def run(cmd: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {shlex.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def ssh(alias: str, command: str, *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return run(["ssh", *SSH_OPTIONS, alias, command], timeout=timeout)


def upload(alias: str, local: Path, remote: str) -> None:
    # Remote paths are validated to contain no shell metacharacters or whitespace.
    run(["scp", *SSH_OPTIONS, str(local), f"{alias}:{remote}"], timeout=300)


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip(".-")
    return value[:64] or "train"


def identifier(value: object, label: str, *, max_length: int = 80) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) or len(value) > max_length:
        raise ValueError(f"{label} must start with a letter or digit and contain only letters, digits, _, . or - (max {max_length}).")
    return value


def remote_root(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"(?:/|~/)[A-Za-z0-9_./-]*", value):
        raise ValueError("remote_root must be an absolute POSIX path or start with ~/; use letters, digits, /, _, . and - only.")
    if ".." in value.split("/"):
        raise ValueError("remote_root cannot contain '..' components.")
    return "~/" if value.rstrip("/") == "~" else (value.rstrip("/") or "/")


def load_config(path: str | None, namespace_override: str | None) -> tuple[dict, str, Path]:
    selected = Path(path or os.environ.get("FUDAN_SLURM_CONFIG") or DEFAULT_CONFIG).expanduser().resolve()
    if not selected.is_file():
        raise ValueError(f"Configuration not found: {selected}. Copy config.example.json to config.local.json, configure your SSH aliases and run_namespace, or pass --config.")
    with selected.open(encoding="utf-8-sig") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object.")
    namespace = namespace_override if namespace_override is not None else config.get("run_namespace")
    namespace = identifier(namespace, "run_namespace / --namespace", max_length=64)
    if namespace.lower() in {"your_run_namespace", "your_namespace", "change_me", "changeme", "example", "placeholder"}:
        raise ValueError("Replace the example run_namespace with your own unique namespace, or pass --namespace.")
    clusters = config.get("clusters")
    if not isinstance(clusters, dict) or not clusters:
        raise ValueError("Configuration must contain a nonempty 'clusters' object.")
    for key, cluster in clusters.items():
        identifier(key, "Cluster key")
        if key == "auto" or not isinstance(cluster, dict):
            raise ValueError("Each cluster must be an object with a key other than 'auto'.")
        identifier(cluster.get("ssh_alias"), f"clusters.{key}.ssh_alias")
        partitions = cluster.get("allowed_partitions")
        if not isinstance(partitions, list) or not partitions:
            raise ValueError(f"clusters.{key}.allowed_partitions must be a nonempty list.")
        for partition in partitions:
            identifier(partition, f"clusters.{key}.allowed_partitions")
            if partition == "auto":
                raise ValueError("'auto' is reserved and cannot be a partition name.")
        if cluster.get("default_partition") not in partitions:
            raise ValueError(f"clusters.{key}.default_partition must be in allowed_partitions.")
        cluster["remote_root"] = remote_root(cluster.get("remote_root"))
        for field in ("account", "qos"):
            value = cluster.get(field, "")
            if value != "":
                identifier(value, f"clusters.{key}.{field}")
    return config, namespace, selected


def decide(args: argparse.Namespace, config: dict) -> dict:
    clusters = config["clusters"]
    gpus = args.gpus if args.gpus is not None else {"auto": 1, "small": 1, "medium": 1, "large": 4}[args.task_size]
    if gpus < 0:
        raise ValueError("--gpus must be zero or greater.")
    if args.host != "auto" and args.host not in clusters:
        raise ValueError(f"Unknown --host {args.host!r}; configured hosts: {', '.join(clusters)}.")
    candidates = [args.host] if args.host != "auto" else list(clusters)
    if args.partition != "auto":
        candidates = [key for key in candidates if args.partition in clusters[key]["allowed_partitions"]]
        if not candidates:
            raise ValueError(f"Partition {args.partition!r} is not allowed for --host {args.host!r}.")
    prefer = "a800" if args.task_size in {"medium", "large"} or gpus >= 2 or (args.nodes or 1) > 1 else "x090"
    if prefer in candidates:
        key = prefer
    elif len(candidates) == 1:
        key = candidates[0]
    elif args.partition != "auto":
        raise ValueError("Partition matches multiple clusters; choose --host explicitly.")
    else:
        key = sorted(candidates)[0]
    cluster = clusters[key]
    partition = cluster["default_partition"] if args.partition == "auto" else args.partition
    cpus = args.cpus if args.cpus is not None else max(2, 12 * max(1, gpus))
    for label, value in (("--cpus", cpus), ("--nodes", args.nodes), ("--ntasks", args.ntasks), ("--ntasks-per-node", args.ntasks_per_node)):
        if value is not None and value < 1:
            raise ValueError(f"{label} must be a positive integer.")
    if not re.fullmatch(r"[1-9][0-9]*(?:\.[0-9]+)?[KMGTkmgt]?", args.mem_per_cpu):
        raise ValueError("--mem-per-cpu must be a positive Slurm memory value, such as 4G or 4096.")
    time_limit = args.time or {"auto": "04:00:00", "small": "04:00:00", "medium": "12:00:00", "large": "24:00:00"}[args.task_size]
    if not re.fullmatch(r"(?:[0-9]+-)?[0-9]+(?::[0-9]{1,2}){0,2}", time_limit):
        raise ValueError("--time must be a Slurm duration, such as 04:00:00 or 1-00:00:00.")
    account = args.account if args.account is not None else cluster.get("account", "")
    qos = args.qos if args.qos is not None else cluster.get("qos", "")
    for label, value in (("--account", account), ("--qos", qos)):
        if value:
            identifier(value, label)
    return {
        "cluster": key, "host": cluster["ssh_alias"], "partition": partition,
        "gpus": gpus, "cpus_per_task": cpus, "mem_per_cpu": args.mem_per_cpu,
        "time": time_limit, "nodes": args.nodes, "ntasks": args.ntasks,
        "ntasks_per_node": args.ntasks_per_node, "account": account, "qos": qos,
    }


def excluded(relative: str, extra: list[str]) -> bool:
    parts = relative.split("/")
    if any(part.casefold() in DEFAULT_EXCLUDES or any(fnmatch.fnmatchcase(part.casefold(), pattern) for pattern in SECRET_PATTERNS) for part in parts):
        return True
    for pattern in extra:
        pattern = pattern.replace("\\", "/").strip("/")
        if pattern and (fnmatch.fnmatchcase(relative, pattern) or relative.startswith(pattern + "/") or ("/" not in pattern and any(fnmatch.fnmatchcase(part, pattern) for part in parts))):
            return True
    return False


def linked_or_special(info: os.stat_result) -> bool:
    # On Windows also exclude junctions/reparse points; os.walk must not follow them.
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def build_manifest(source: Path, extra: list[str], max_upload_mb: int) -> tuple[str, list[ManifestEntry], int]:
    if max_upload_mb < 1:
        raise ValueError("--max-upload-mb must be a positive integer.")
    top = safe_name(source.stem if source.is_file() else source.name)
    manifest: list[ManifestEntry] = []
    total = 0
    limit = max_upload_mb * 1024 * 1024

    def include(path: Path, relative: str, archive_name: str) -> None:
        nonlocal total
        info = path.lstat()
        if linked_or_special(info) or not stat.S_ISREG(info.st_mode) or excluded(relative, extra):
            return
        total += info.st_size
        if total > limit:
            raise ValueError(f"Upload exceeds --max-upload-mb={max_upload_mb} ({total} uncompressed file bytes so far). Exclude data/checkpoints or increase the limit intentionally.")
        manifest.append(ManifestEntry(relative, archive_name, info.st_size))

    def walk(directory: Path, relative_parts: tuple[str, ...] = ()) -> None:
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
        for child in children:
            parts = (*relative_parts, child.name)
            relative = "/".join(parts)
            if excluded(relative, extra):
                continue
            info = child.stat(follow_symlinks=False)
            if linked_or_special(info):
                continue
            if stat.S_ISDIR(info.st_mode):
                walk(Path(child.path), parts)
            elif stat.S_ISREG(info.st_mode):
                include(Path(child.path), relative, f"{top}/{relative}")

    if source.is_file():
        include(source, source.name, source.name)
    else:
        walk(source)
    if not manifest:
        raise ValueError("No regular source files remain after exclusions.")
    return top, manifest, total


def make_archive(source: Path, archive: Path, manifest: list[ManifestEntry]) -> None:
    # Add each planned regular file exactly once. Directory recursion is never delegated to tarfile.
    with tarfile.open(archive, "w:gz", dereference=True) as output:
        for entry in manifest:
            path = source if source.is_file() else source.joinpath(*entry.path.split("/"))
            info = path.lstat()
            if linked_or_special(info) or not stat.S_ISREG(info.st_mode) or info.st_size != entry.size_bytes:
                raise ValueError(f"Source changed after manifest creation: {entry.path}. Run again to rebuild the plan.")
            with path.open("rb") as handle:
                current = os.fstat(handle.fileno())
                if current.st_size != entry.size_bytes or (info.st_ino, info.st_dev) != (current.st_ino, current.st_dev):
                    raise ValueError(f"Source changed while archiving: {entry.path}.")
                member = output.gettarinfo(fileobj=handle, arcname=entry.archive_path)
                if member.size != entry.size_bytes:
                    raise ValueError(f"Source changed while archiving: {entry.path}.")
                # Avoid embedding local user/group names and IDs in the archive.
                member.uid = member.gid = 0
                member.uname = member.gname = ""
                output.addfile(member, handle)


def infer_command(source: Path, explicit: str | None, manifest: list[ManifestEntry]) -> str:
    if explicit:
        return explicit
    if source.is_file() and source.suffix == ".py":
        return f"python3 {shlex.quote(source.name)}"
    included = {entry.path for entry in manifest}
    for candidate in ("train.py", "main.py", "run.py"):
        if candidate in included:
            return f"python3 {candidate}"
    raise ValueError("Cannot infer training command from included files. Pass --command.")


def sbatch_text(args: argparse.Namespace, resources: dict, command: str) -> str:
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --partition={resources['partition']}",
        f"#SBATCH --job-name={args.job_name}",
        f"#SBATCH --cpus-per-task={resources['cpus_per_task']}",
        f"#SBATCH --mem-per-cpu={resources['mem_per_cpu']}",
        f"#SBATCH --time={resources['time']}",
        "#SBATCH --output=logs/%x-%j.out", "#SBATCH --error=logs/%x-%j.err",
    ]
    if resources["gpus"]:
        lines.append(f"#SBATCH --gres=gpu:{resources['gpus']}")
    for field, option in (("nodes", "nodes"), ("ntasks", "ntasks"), ("ntasks_per_node", "ntasks-per-node"), ("account", "account"), ("qos", "qos")):
        if resources[field]:
            lines.append(f"#SBATCH --{option}={resources[field]}")
    lines += ["", "set -euo pipefail", "mkdir -p logs", "date", "hostname", "pwd", 'echo "SLURM_JOB_ID=${SLURM_JOB_ID:-}"', 'echo "CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-}"']
    if args.cuda_module:
        lines += [
            "if ! type module >/dev/null 2>&1 && [ -f /etc/profile.d/modules.sh ]; then",
            "  . /etc/profile.d/modules.sh", "fi",
            "type module >/dev/null 2>&1 || { echo 'Requested CUDA module but the module command is unavailable.' >&2; exit 1; }",
            f"module load {shlex.quote(args.cuda_module)} || {{ echo 'Requested CUDA module failed to load.' >&2; exit 1; }}",
        ]
    if args.conda_env:
        if args.conda_sh:
            if args.conda_sh.startswith("~/"):
                conda_path = '"$HOME"/' + shlex.quote(args.conda_sh[2:])
            else:
                conda_path = shlex.quote(args.conda_sh)
            lines += [f"conda_init={conda_path}", '[ -f "$conda_init" ] || { echo "Conda initialization script not found: $conda_init" >&2; exit 1; }', '. "$conda_init"']
        else:
            lines += [
                'if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then',
                '  . "$HOME/miniconda3/etc/profile.d/conda.sh"',
                'elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then',
                '  . "$HOME/anaconda3/etc/profile.d/conda.sh"',
                "elif command -v conda >/dev/null 2>&1; then",
                '  conda_base="$(conda info --base)"',
                '  [ -f "$conda_base/etc/profile.d/conda.sh" ] || { echo "Conda shell initialization is missing; pass --conda-sh." >&2; exit 1; }',
                '  . "$conda_base/etc/profile.d/conda.sh"',
                "else", "  echo 'Requested conda environment but conda was not found; pass --conda-sh.' >&2", "  exit 1", "fi",
            ]
        lines += ["type conda >/dev/null 2>&1 || { echo 'Conda shell initialization failed.' >&2; exit 1; }", f"conda activate {shlex.quote(args.conda_env)} || {{ echo 'Requested conda environment could not be activated.' >&2; exit 1; }}"]
    if args.pre_cmd:
        lines += ["", args.pre_cmd]
    lines += ["", "echo '== training command =='", f"echo {shlex.quote(command)}", command]
    return "\n".join(lines) + "\n"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--source", required=True, help="Local code file or directory.")
    result.add_argument("--config", help="JSON configuration; otherwise FUDAN_SLURM_CONFIG, then config.local.json.")
    result.add_argument("--namespace", help="Override run_namespace with your unique shared-root namespace.")
    result.add_argument("--command", help="Shell command in the staged project directory.")
    result.add_argument("--task-size", choices=["auto", "small", "medium", "large"], default="auto")
    result.add_argument("--host", default="auto", help="Configured cluster key, or auto.")
    result.add_argument("--partition", default="auto", help="Allowed partition in the selected cluster, or auto.")
    for name in ("gpus", "cpus", "nodes", "ntasks", "ntasks-per-node"):
        result.add_argument(f"--{name}", type=int)
    result.add_argument("--mem-per-cpu", default="4G")
    result.add_argument("--time", help="Slurm time limit, e.g. 04:00:00 or 1-00:00:00.")
    result.add_argument("--account", help="Override the configured Slurm account.")
    result.add_argument("--qos", help="Override the configured Slurm QoS.")
    result.add_argument("--conda-env")
    result.add_argument("--conda-sh", help="Remote conda.sh path; requires --conda-env.")
    result.add_argument("--cuda-module", default="")
    result.add_argument("--pre-cmd")
    result.add_argument("--job-name", default="")
    result.add_argument("--run-name", default="", help="Explicit run directory name; existing directories are rejected.")
    result.add_argument("--exclude", action="append", default=[], help="Repeatable relative path, basename, or glob to exclude at any depth.")
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="Local JSON manifest, resource plan, and sbatch preview; no archive, SSH, upload, or submission.")
    mode.add_argument("--dry-run", action="store_true", help="Stage files and write sbatch remotely without submitting.")
    result.add_argument("--max-upload-mb", type=int, default=200, help="Maximum total uncompressed included file bytes in MiB (default: 200).")
    return result


def execute(args: argparse.Namespace) -> dict:
    config, namespace, config_path = load_config(args.config, args.namespace)
    source = Path(args.source).expanduser().absolute()
    info = source.lstat()
    if linked_or_special(info) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError("--source must be a regular file or directory, not a symlink, junction, or special file.")
    source = source.resolve()
    if source.is_file() and source.name in {"run.sbatch", "payload.tar.gz", "logs"}:
        raise ValueError("A single-file source cannot be named run.sbatch, payload.tar.gz, or logs; place it inside a project directory.")
    if args.conda_sh and not args.conda_env:
        raise ValueError("--conda-sh requires --conda-env.")
    resources = decide(args, config)
    top, manifest, total = build_manifest(source, args.exclude, args.max_upload_mb)
    base = safe_name(source.stem if source.is_file() else source.name)
    args.job_name = identifier(args.job_name, "--job-name", max_length=64) if args.job_name else base
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_name = identifier(args.run_name, "--run-name", max_length=128) if args.run_name else f"{stamp}-{base}-{secrets.token_hex(4)}"
    command = infer_command(source, args.command, manifest)
    script = sbatch_text(args, resources, command)
    alias = resources["host"]
    root = config["clusters"][resources["cluster"]]["remote_root"]

    def paths(root_path: str) -> tuple[str, str]:
        directory = posixpath.join(root_path, namespace, run_name)
        project = posixpath.join(directory, top) if source.is_dir() else directory
        return directory, project

    directory, project = paths(root)
    result = {
        "status": "planned", "config": str(config_path), "namespace": namespace,
        "run_name": run_name, **resources, "resources": resources,
        "remote_dir": directory, "remote_project_dir": project,
        "sbatch": posixpath.join(directory, "run.sbatch"), "sbatch_text": script,
        "job_name": args.job_name, "job_id": None, "submit_stdout": "",
        "dry_run": args.dry_run, "plan": args.plan, "command": command,
        "total_size_bytes": total, "manifest": [asdict(entry) for entry in manifest],
        "exclusion_note": "Filename exclusions are best effort; review the manifest and source contents before staging.",
        "remote_user": None,
        "queue_command": shlex.join(["ssh", *SSH_OPTIONS, alias, 'squeue -u "$(id -un)"']),
        "accounting_command": None,
        "log_hint": posixpath.join(project, "logs", f"{args.job_name}-<JOBID>.out"),
        "error_log_hint": posixpath.join(project, "logs", f"{args.job_name}-<JOBID>.err"),
        "cancel_command": shlex.join(["ssh", *SSH_OPTIONS, alias, "scancel <JOBID>"]),
    }
    if args.plan:
        return result

    with tempfile.TemporaryDirectory(prefix="slurm-stage-") as temporary:
        local = Path(temporary)
        archive = local / "payload.tar.gz"
        make_archive(source, archive, manifest)
        sbatch = local / "run.sbatch"
        sbatch.write_text(script, encoding="utf-8", newline="\n")
        identity = ssh(alias, 'printf "%s\\n" "$HOME"; id -un', timeout=60).stdout.strip().splitlines()
        if len(identity) != 2:
            raise RuntimeError("Expected remote HOME and username; SSH login output was unexpected.")
        remote_home = remote_root(identity[0])
        if not remote_home.startswith("/"):
            raise RuntimeError("Remote HOME must be absolute.")
        remote_user = identifier(identity[1], "Remote username")
        if root.startswith("~/"):
            root = posixpath.join(remote_home, root[2:])
        directory, project = paths(root)
        parent = posixpath.dirname(directory)
        ssh(alias, f"mkdir -p {shlex.quote(parent)} && mkdir {shlex.quote(directory)}", timeout=60)
        upload(alias, archive, posixpath.join(directory, "payload.tar.gz"))
        upload(alias, sbatch, posixpath.join(directory, "run.sbatch"))
        # Slurm opens output files before the job body: create logs BEFORE sbatch.
        ssh(alias, f"cd {shlex.quote(directory)} && tar -xzf payload.tar.gz && chmod +x run.sbatch && mkdir -p {shlex.quote(posixpath.join(project, 'logs'))}")

    result.update({
        "status": "staged", "remote_user": remote_user,
        "remote_dir": directory, "remote_project_dir": project,
        "sbatch": posixpath.join(directory, "run.sbatch"),
        "queue_command": shlex.join(["ssh", *SSH_OPTIONS, alias, f"squeue -u {shlex.quote(remote_user)}"]),
        "log_hint": posixpath.join(project, "logs", f"{args.job_name}-<JOBID>.out"),
        "error_log_hint": posixpath.join(project, "logs", f"{args.job_name}-<JOBID>.err"),
    })
    if not args.dry_run:
        sbatch_relative = "../run.sbatch" if source.is_dir() else "run.sbatch"
        submit = f"cd {shlex.quote(project)} && sbatch --parsable {shlex.quote(sbatch_relative)}"
        try:
            output = ssh(alias, submit, timeout=60).stdout.strip()
        except Exception as error:
            raise RuntimeError(f"Submission was attempted once; acceptance is unknown. Check the queue before retrying. Staged run: {directory}. Queue: {result['queue_command']}. Details: {error}") from error
        if not re.fullmatch(r"[0-9]+(?:;[A-Za-z0-9_.-]+)?", output):
            raise RuntimeError(f"Unexpected sbatch --parsable response {output!r}. Do not resubmit until checking the queue. Staged run: {directory}. Queue: {result['queue_command']}")
        job_id, _, job_cluster = output.partition(";")
        cluster_options = ["-M", job_cluster] if job_cluster else []
        result.update({"status": "submitted", "job_id": job_id, "job_cluster": job_cluster or None, "submit_stdout": output})
        result["queue_command"] = shlex.join(["ssh", *SSH_OPTIONS, alias, shlex.join(["squeue", *cluster_options, "-j", job_id])])
        result["accounting_command"] = shlex.join(["ssh", *SSH_OPTIONS, alias, shlex.join([
            "sacct", *cluster_options, "-j", job_id, "--parsable2",
            "--format=JobIDRaw,State%30,ExitCode,DerivedExitCode,Elapsed",
        ])])
        result["log_hint"] = result["log_hint"].replace("<JOBID>", job_id)
        result["error_log_hint"] = result["error_log_hint"].replace("<JOBID>", job_id)
        result["cancel_command"] = shlex.join(["ssh", *SSH_OPTIONS, alias, shlex.join(["scancel", *cluster_options, job_id])])
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        result = execute(parser().parse_args(argv))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
