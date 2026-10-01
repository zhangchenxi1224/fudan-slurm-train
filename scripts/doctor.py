#!/usr/bin/env python3
"""Check local setup, or explicitly inspect a remote cluster without staging or submitting."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from submit_training import identifier, load_config, remote_root


MARKER = "FUDAN_DOCTOR"
LIMITATIONS = [
    "No job is submitted: account/QoS authorization, resource quotas, scheduler acceptance, and compute-node CUDA are not verified.",
    "Write access is inspected with shell permission tests; filesystem quotas and actual file creation are not tested.",
    "SSH configuration is trusted local code: ssh -G can evaluate Match exec directives. Canonical DNS lookup and PermitLocalCommand are disabled.",
]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", help="JSON configuration; otherwise FUDAN_SLURM_CONFIG, then config.local.json.")
    result.add_argument("--namespace", help="Override the configured run_namespace.")
    result.add_argument("--host", help="One configured cluster key; default: all configured clusters.")
    result.add_argument("--remote", action="store_true", help="Explicitly authenticate and run read-only login-node checks.")
    result.add_argument("--timeout", type=int, default=20, help="Timeout for each SSH process, from 1 to 60 seconds (default: 20).")
    result.add_argument("--conda-env", help="With --remote, activate this environment and report Python only.")
    result.add_argument("--conda-sh", help="Remote absolute or ~/ conda.sh path; requires --conda-env.")
    result.add_argument("--cuda-module", help="With --remote, load this module; no GPU computation is performed.")
    return result


def check(report: dict, name: str, status: str, message: str, host: str | None = None) -> None:
    entry = {"name": name, "status": status, "message": message}
    if host is not None:
        entry["host"] = host
    report["checks"].append(entry)


def finish(report: dict) -> dict:
    failed = any(item["status"] == "fail" for item in report["checks"])
    report["status"] = "incomplete" if failed else "ready"
    report["remote_ready"] = (not failed) if report["remote_checked"] else None
    report["summary"] = (
        "One or more required checks failed; review the failed checks before continuing."
        if failed else (
            "Read-only remote prerequisites passed; submission and compute-node execution remain unverified."
            if report["remote_checked"] else
            "Local prerequisites passed only; remote access, authorization, and submission remain unverified."
        )
    )
    return report


def call(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def short_error(result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or "SSH returned no diagnostic text.").strip()
    return f"Exit {result.returncode}: {detail[:800]}"


def local_alias(report: dict, key: str, alias: str, timeout: int) -> bool:
    command = ["ssh", "-G", "-o", "CanonicalizeHostname=no", "-o", "PermitLocalCommand=no", alias]
    try:
        result = call(command, timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        check(report, "ssh_config", "fail", f"Cannot inspect SSH alias {alias}: {type(error).__name__}. Check OpenSSH and its config; timeout={timeout}s.", key)
        return False
    if result.returncode:
        check(report, "ssh_config", "fail", short_error(result), key)
        return False
    fields: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        name, separator, value = line.partition(" ")
        if separator:
            fields.setdefault(name.lower(), []).append(value.strip())
    host = fields.get("hostname", [""])[0]
    user = fields.get("user", [""])[0]
    port = fields.get("port", [""])[0]
    placeholders = {"a800_login_host", "x090_login_host", "shared_cluster_user", "ssh_port", "your_user", "your_host"}
    if not host or not user or not port.isdigit() or not 1 <= int(port) <= 65535 or host.lower() in placeholders or user.lower() in placeholders:
        check(report, "ssh_config", "fail", f"Alias {alias} has missing or example HostName/User/Port values. Fill your local SSH config from ssh_config.example.", key)
        return False
    check(report, "ssh_config", "pass", f"Alias {alias} expands to a hostname, username, and valid port; no authentication was attempted by this check.", key)
    if host == alias:
        check(report, "ssh_alias_resolution", "warn", "HostName is unchanged from the alias. Confirm this is a resolvable login hostname, or add the alias to your SSH config.", key)
    check(report, "ssh_key_authorization", "warn", "Local inspection cannot establish key authorization. This script never reads or displays key contents; --remote lets OpenSSH authenticate using your normal SSH configuration.", key)
    return True


def shell_path(value: str) -> str:
    return '"$HOME"/' + shlex.quote(value[2:]) if value.startswith("~/") else shlex.quote(value)


def remote_script(cluster: dict, namespace: str, args: argparse.Namespace) -> tuple[str, list[str]]:
    """Generate only read-only checks. All substituted arguments were validated first."""
    expected = ["tool.bash", "home", "identity", "tool.tar", "tool.sbatch", "tool.squeue", "tool.sinfo", "tool.sacct", "remote_root"]
    lines = [
        "set -u",
        'emit() { printf "FUDAN_DOCTOR\\t%s\\t%s\\t%s\\n" "$1" "$2" "$3"; }',
        "emit tool.bash pass 'Bash is available.'",
        'if [[ "${HOME:-}" =~ ^/[A-Za-z0-9_./-]*$ ]] && [[ "/$HOME/" != *"/../"* ]]; then',
        "  emit home pass 'HOME has a supported absolute POSIX path.'",
        "else emit home fail 'HOME is missing or uses a path unsupported by the submission script.'; fi",
        'remote_user="$(id -un 2>/dev/null)"',
        'if [[ "$remote_user" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] && [ "${#remote_user}" -le 80 ]; then',
        "  emit identity pass 'Remote username is available and supported.'",
        "else emit identity fail 'Cannot determine a supported remote username.'; fi",
        "for tool in tar sbatch squeue sinfo; do",
        '  if command -v "$tool" >/dev/null 2>&1; then emit "tool.$tool" pass "Available: $tool";',
        '  else emit "tool.$tool" fail "Missing on the login node: $tool"; fi',
        "done",
        "if command -v sacct >/dev/null 2>&1; then emit tool.sacct pass 'sacct is available.';",
        "else emit tool.sacct warn 'sacct is missing; final accounting status may require administrator assistance.'; fi",
        'partitions="$(sinfo --noheader --format=%P 2>/dev/null)"; sinfo_status=$?',
    ]
    for partition in cluster["allowed_partitions"]:
        name = "partition." + partition
        expected.append(name)
        lines += [
            f"wanted={shlex.quote(partition)}; found=0",
            'while IFS= read -r entry; do entry="${entry%\\*}"; [ "$entry" != "$wanted" ] || found=1; done <<< "$partitions"',
            f"if [ \"$sinfo_status\" -ne 0 ]; then emit {shlex.quote(name)} fail 'sinfo could not query the scheduler.';",
            f"elif [ \"$found\" -eq 1 ]; then emit {shlex.quote(name)} pass 'Configured partition is visible; submit authorization is unverified.';",
            f"else emit {shlex.quote(name)} fail 'Configured partition is not visible. Confirm its name and cluster.'; fi",
        ]
    lines += [
        f"root={shell_path(cluster['remote_root'])}",
        f'probe="${{root%/}}"/{shlex.quote(namespace)}',
        'while [ ! -e "$probe" ] && [ ! -L "$probe" ] && [ "$probe" != / ]; do probe="${probe%/*}"; [ -n "$probe" ] || probe=/; done',
        'if [ -d "$probe" ] && [ -w "$probe" ] && [ -x "$probe" ]; then',
        "  emit remote_root pass 'Run namespace or nearest existing ancestor is writable/searchable; no directory was created.'",
        "else emit remote_root fail 'Run namespace or nearest existing ancestor is not a writable/searchable directory.'; fi",
    ]
    if args.cuda_module:
        expected.append("cuda_module")
        lines += [
            "if ! type module >/dev/null 2>&1 && [ -f /etc/profile.d/modules.sh ]; then . /etc/profile.d/modules.sh >/dev/null 2>&1; fi",
            f"if type module >/dev/null 2>&1 && module load {shlex.quote(args.cuda_module)} >/dev/null 2>&1; then",
            "  emit cuda_module pass 'Requested module loaded in this check shell; compute-node CUDA is unverified.'",
            "else emit cuda_module fail 'Requested module could not be loaded in the login shell.'; fi",
        ]
    if args.conda_env:
        expected.extend(["conda_env", "python"])
        lines += ["conda_ready=0"]
        if args.conda_sh:
            lines += [f"conda_init={shell_path(args.conda_sh)}"]
        else:
            lines += [
                'conda_init="$HOME/miniconda3/etc/profile.d/conda.sh"',
                'if [ ! -f "$conda_init" ]; then conda_init="$HOME/anaconda3/etc/profile.d/conda.sh"; fi',
                'if [ ! -f "$conda_init" ] && command -v conda >/dev/null 2>&1; then conda_base="$(conda info --base 2>/dev/null)"; conda_init="$conda_base/etc/profile.d/conda.sh"; fi',
            ]
        lines += [
            # Environment activation hooks are user-managed code, explicitly requested by these flags.
            "set +u",
            f'if [ -f "$conda_init" ] && . "$conda_init" >/dev/null 2>&1 && type conda >/dev/null 2>&1 && conda activate {shlex.quote(args.conda_env)} >/dev/null 2>&1; then conda_ready=1; fi',
            "set -u",
            'if [ "$conda_ready" -eq 1 ]; then',
            "  emit conda_env pass 'Requested environment activated in this check shell.'",
            '  if python_version="$(python --version 2>&1)"; then emit python pass "$python_version";',
            "  else emit python fail 'Python is unavailable in the activated environment.'; fi",
            "else",
            "  emit conda_env fail 'Conda initialization or activation failed. Check --conda-env and --conda-sh.'",
            "  emit python fail 'Python was not checked because environment activation failed.'",
            "fi",
        ]
    else:
        expected.append("python")
        lines += [
            'if python_version="$(python3 --version 2>&1)"; then emit python pass "$python_version";',
            "else emit python warn 'python3 is unavailable in the login shell; supply --conda-env if needed.'; fi",
        ]
    lines.append("exit 0")
    return "\n".join(lines), expected


def inspect_remote(report: dict, key: str, cluster: dict, namespace: str, args: argparse.Namespace) -> None:
    script, expected = remote_script(cluster, namespace, args)
    options = [
        "BatchMode=yes", f"ConnectTimeout={min(args.timeout, 15)}", "ServerAliveInterval=5", "ServerAliveCountMax=1",
        "StrictHostKeyChecking=yes", "UpdateHostKeys=no", "PermitLocalCommand=no", "ClearAllForwardings=yes",
        "ForwardAgent=no", "ForwardX11=no", "ControlMaster=no", "ControlPath=none", "RemoteCommand=none",
    ]
    command = ["ssh", "-T", *[part for option in options for part in ("-o", option)], cluster["ssh_alias"], shlex.join(["bash", "-lc", script])]
    report["remote_checked"] = True
    try:
        result = call(command, args.timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        check(report, "remote_connection", "fail", f"Remote check did not complete: {type(error).__name__}; timeout={args.timeout}s. Check network reachability, SSH authorization, and host-key verification.", key)
        return
    if result.returncode:
        check(report, "remote_connection", "fail", short_error(result) + " Verify the server fingerprint with your administrator during an initial interactive SSH login; this check never accepts unknown host keys.", key)
        return
    records: dict[str, tuple[str, str]] = {}
    malformed = False
    for line in result.stdout.splitlines():
        if not line.startswith(MARKER + "\t"):
            continue  # Login banners are allowed; only the diagnostic protocol is interpreted.
        fields = line.split("\t", 3)
        if len(fields) != 4 or fields[1] not in expected or fields[2] not in {"pass", "warn", "fail"} or fields[1] in records:
            malformed = True
            continue
        records[fields[1]] = (fields[2], fields[3])
    missing = sorted(set(expected) - records.keys())
    if malformed or missing:
        check(report, "remote_protocol", "fail", "Remote diagnostic output was incomplete or invalid. Missing checks: " + (", ".join(missing) or "none") + ". Inspect login-shell initialization; no readiness conclusion can be made.", key)
    else:
        check(report, "remote_connection", "pass", "Noninteractive SSH completed with existing host-key verification.", key)
    for name in expected:
        if name in records:
            status, message = records[name]
            check(report, "remote." + name, status, message, key)


def execute(args: argparse.Namespace) -> dict:
    report = {"schema_version": 1, "scope": "remote" if args.remote else "local", "remote_checked": False, "remote_ready": None, "config": None, "namespace": None, "checks": [], "limitations": list(LIMITATIONS)}
    check(report, "python", "pass" if sys.version_info >= (3, 10) else "fail", f"Python {sys.version_info.major}.{sys.version_info.minor}; requires Python 3.10+.")
    try:
        if not 1 <= args.timeout <= 60:
            raise ValueError("--timeout must be between 1 and 60 seconds.")
        if (args.conda_env or args.conda_sh or args.cuda_module) and not args.remote:
            raise ValueError("Environment checks require --remote; the default mode only inspects local setup.")
        if args.conda_sh and not args.conda_env:
            raise ValueError("--conda-sh requires --conda-env.")
        if args.conda_env:
            identifier(args.conda_env, "--conda-env")
        if args.conda_sh:
            remote_root(args.conda_sh)
        if args.cuda_module and (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./+-]{0,127}", args.cuda_module) or ".." in args.cuda_module.split("/")):
            raise ValueError("--cuda-module must be a simple module name/version (letters, digits, _, ., /, + or -).")
        config, namespace, path = load_config(args.config, args.namespace)
        if args.host is not None and args.host not in config["clusters"]:
            raise ValueError(f"Unknown --host {args.host!r}; choose one of: {', '.join(config['clusters'])}.")
        report.update({"config": str(path), "namespace": namespace})
        check(report, "config", "pass", "Configuration and namespace are valid.")
    except (ValueError, OSError) as error:
        check(report, "config", "fail", str(error))
        return finish(report)
    available = {}
    for tool in ("ssh", "scp"):
        available[tool] = shutil.which(tool) is not None
        check(report, "local." + tool, "pass" if available[tool] else "fail", f"{tool} is available on PATH." if available[tool] else f"Install an OpenSSH client and ensure {tool} is available on PATH.")
    if available["ssh"]:
        for key, cluster in config["clusters"].items():
            if args.host is not None and args.host != key:
                continue
            if local_alias(report, key, cluster["ssh_alias"], args.timeout) and args.remote:
                inspect_remote(report, key, cluster, namespace, args)
    if args.remote and not report["remote_checked"]:
        check(report, "remote_connection", "fail", "Remote checks were requested but could not start because local prerequisites failed.")
    return finish(report)


def main(argv: list[str] | None = None) -> int:
    report = execute(parser().parse_args(argv))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
