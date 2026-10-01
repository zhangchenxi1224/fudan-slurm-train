---
name: fudan-slurm-train
description: Stage local training code and create or submit Slurm jobs on configured Fudan laboratory clusters. Use for A800 or x090/fnlp training requests, local sbatch previews, remote staging, and related SSH, queue, log, CUDA or conda troubleshooting.
---

# fudan-slurm-train Skill

Use `scripts/submit_training.py` to turn a local file or project into a Slurm job. Resolve script and reference paths relative to the directory containing this `SKILL.md`.

## Configuration

- First-time access or missing SSH configuration: read [references/access.md](references/access.md). Existing authorized access may be reused.
- Help fill only missing local configuration from confirmed user/maintainer information. Use `scripts/doctor.py` for local checks; add `--remote --host <configured-key>` for bounded read-only remote checks when connectivity should be available. A local pass does not verify SSH access, and a remote pass does not verify GPU execution or account/QoS permissions.
- Resource choices, CLI details, environment setup or queue/log checks: read [references/cluster.md](references/cluster.md).
- Local dependencies: Python >= 3.10 and OpenSSH `ssh`/`scp`; remote dependencies: Bash, `tar` and Slurm. Network access is assumed.
- For `--plan` alone, only Python, the source and valid local JSON configuration are needed; SSH tools and remote connectivity are not required.
- Configuration precedence: `--config` > `FUDAN_SLURM_CONFIG` > skill-root `config.local.json`. Actual addresses, SSH usernames and private-key locations stay in the user's SSH config; never put credentials in this skill or Git.
- An authorized shared cluster account is supported. Each person uses their own key and `run_namespace`. Namespaces reduce collisions but do not isolate permissions.

## Workflow

1. Inspect the user's source and training command. Save pasted code locally first. The helper can infer `python3 <file.py>` or a project-root `train.py`, `main.py`, then `run.py`; supply `--command` for other entrypoints.
2. Review upload scope and remote environment/data paths. Prefer existing cluster datasets and checkpoints. Use `--plan` to inspect the upload manifest, selected resources and sbatch text entirely locally.
3. Honor explicit host, partition and resource choices within configured cluster permissions. Small jobs default to x090; medium, large or requests for 2+ GPUs prefer A800 when host and partition are automatic. Confirm large allocations only when the user's authorization leaves their scope unresolved.
4. Choose the mode matching the user's request: `--plan` is local only; `--dry-run` uploads and writes remote files but does not call `sbatch`; an ordinary run submits a job. Do not interpret a preview request as submission authorization.
5. Submit using the bundled helper when authorized. Use the returned host, run directory and job ID to check the specific job and logs. On an uncertain submit result, inspect the queue and run directory before retrying to avoid duplicate allocations.
6. Report the selected resources, remote directory, job ID when available, current observed state, and commands for this job's logs and cancellation. Use the returned `accounting_command` to inspect `sacct` state and exit codes after execution; read relevant step records and logs as described in [references/cluster.md](references/cluster.md). A successful submission or empty queue is not evidence that training completed. If accounting is unavailable, report the result as unconfirmed unless another authoritative final-state record is available.

For a requested first-use acceptance check, use `examples/smoke.py` with small, time-limited Slurm resources. Its default tests Python execution only; `--check-gpu` tests driver access, and `--check-torch` tests a tiny CUDA calculation in an existing PyTorch environment. Neither GPU option is for direct execution on a login node. Follow the user's authorized test scope; do not install dependencies or allocate extra resources merely to make a check pass.

## Operational constraints

- Run training through Slurm allocations; keep login-node actions limited to staging, inspection and setup.
- Do not start a nested GPU allocation from an already allocated GPU shell.
- Do not assume conda, CUDA modules, datasets or model files exist. Use configured paths and diagnose missing prerequisites before submitting.
- Training commands and `--pre-cmd` are executed as shell commands on the remote cluster. Preserve the user's intended command and scope.
- Never copy or publish private keys. Each person shares only their public key for authorization.
- On shared accounts, inspect and cancel only job IDs confirmed to belong to the requested task. Never mass-cancel the shared user's jobs.
