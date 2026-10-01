"""Local unit tests. No test connects to a cluster or submits an actual job."""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "submit_training.py"
SPEC = importlib.util.spec_from_file_location("submit_training", SCRIPT)
submit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = submit
SPEC.loader.exec_module(submit)


def configuration(namespace="research-team"):
    return {
        "run_namespace": namespace,
        "clusters": {
            "a800": {"ssh_alias": "test-a800", "default_partition": "a800", "allowed_partitions": ["a800"], "remote_root": "~/codex_runs", "account": "", "qos": ""},
            "x090": {"ssh_alias": "test-x090", "default_partition": "fnlp-x090", "allowed_partitions": ["fnlp-x090", "fnlp-4090"], "remote_root": "~/codex_runs", "account": "", "qos": ""},
        },
    }


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "project"
        self.source.mkdir()
        (self.source / "train.py").write_text("print('training')\n", encoding="utf-8")
        self.config = self.root / "settings.json"
        self.config.write_text(json.dumps(configuration()), encoding="utf-8")

    def args(self, *extra):
        return submit.parser().parse_args(["--source", str(self.source), "--config", str(self.config), *extra])

    def write(self, name, data=b"not for upload"):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def transport(self, commands):
        def fake_run(command, *, timeout=120):
            commands.append(command)
            output = ""
            if command[0] == "ssh" and "id -un" in command[-1]:
                output = "/home/remote-user\nremote-user\n"
            elif command[0] == "ssh" and "sbatch --parsable" in command[-1]:
                output = "12345;test-cluster\n"
            return subprocess.CompletedProcess(command, 0, output, "")
        return fake_run

    def test_archive_filters_nested_secrets_and_has_unique_posix_members(self):
        for name in ("pkg/.env", "pkg/.env.production", "pkg/.ssh/id_custom", "pkg/.aws/credentials", "pkg/private.pem", "pkg/api.key", "pkg/credentials.json", "pkg/service-account-prod.json", "pkg/__pycache__/module.pyc", "pkg/outputs/model.bin", "pkg/.venv/bin/python", "pkg/config.local.json", "logs/log.txt", "custom/ignored.txt"):
            self.write(name)
        self.write("pkg/module.py", b"VALUE = 1\n")
        self.write("pkg/sub/helper.py", b"VALUE = 2\n")
        top, manifest, size = submit.build_manifest(self.source, ["custom"], 1)
        self.assertEqual({entry.path for entry in manifest}, {"train.py", "pkg/module.py", "pkg/sub/helper.py"})
        self.assertEqual(size, sum((self.source / entry.path).stat().st_size for entry in manifest))
        archive = self.root / "payload.tar.gz"
        submit.make_archive(self.source, archive, manifest)
        with tarfile.open(archive) as handle:
            names = handle.getnames()
            self.assertEqual(len(names), len(set(names)))
            self.assertEqual(set(names), {f"{top}/{entry.path}" for entry in manifest})
            self.assertTrue(all("\\" not in name for name in names))
            self.assertEqual(sum(member.size for member in handle.getmembers()), size)
            self.assertTrue(all(member.isfile() and member.uname == "" and member.gname == "" for member in handle.getmembers()))

    def test_size_limit_is_checked_before_archive_and_transport(self):
        self.write("too-large.bin", b"a" * (1024 * 1024))
        with mock.patch.object(submit, "make_archive") as archive, mock.patch.object(submit, "run") as network:
            with self.assertRaisesRegex(ValueError, "exceeds"):
                submit.execute(self.args("--max-upload-mb", "1"))
            archive.assert_not_called()
            network.assert_not_called()

    def test_exact_limit_single_file_is_allowed(self):
        file = self.write("exact.bin", b"a" * (1024 * 1024))
        _, manifest, total = submit.build_manifest(file, [], 1)
        self.assertEqual(total, 1024 * 1024)
        self.assertEqual(manifest[0].archive_path, "exact.bin")

    def test_symlinks_are_not_uploaded(self):
        target = self.root / "outside.txt"
        target.write_text("private", encoding="utf-8")
        try:
            (self.source / "linked.txt").symlink_to(target)
        except OSError:
            self.skipTest("Creating symlinks is unavailable on this platform/account.")
        _, manifest, _ = submit.build_manifest(self.source, [], 1)
        self.assertEqual([entry.path for entry in manifest], ["train.py"])

    def test_local_plan_has_manifest_script_and_no_network_or_archive(self):
        with mock.patch.object(submit, "run", side_effect=AssertionError("Network called")), mock.patch.object(submit, "make_archive", side_effect=AssertionError("Archive created")):
            result = submit.execute(self.args("--plan", "--task-size", "medium", "--run-name", "preview"))
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["cluster"], "a800")
        self.assertEqual(result["remote_dir"], "~/codex_runs/research-team/preview")
        self.assertEqual(result["manifest"][0]["path"], "train.py")
        self.assertIn("#SBATCH --partition=a800", result["sbatch_text"])

    def test_config_precedence_and_namespace_override(self):
        environment_config = self.root / "env.json"
        environment_config.write_text(json.dumps(configuration("environment")), encoding="utf-8")
        with mock.patch.dict(os.environ, {"FUDAN_SLURM_CONFIG": str(environment_config)}):
            _, namespace, path = submit.load_config(None, None)
            self.assertEqual(namespace, "environment")
            self.assertEqual(path, environment_config.resolve())
            _, namespace, path = submit.load_config(str(self.config), "override")
            self.assertEqual(namespace, "override")
            self.assertEqual(path, self.config.resolve())
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(submit, "DEFAULT_CONFIG", self.config):
            self.assertEqual(submit.load_config(None, None)[1], "research-team")

    def test_example_missing_namespace_and_missing_config_fail(self):
        for namespace in ("YOUR_RUN_NAMESPACE", "", None, "../other"):
            self.config.write_text(json.dumps(configuration(namespace)), encoding="utf-8")
            with self.assertRaises(ValueError):
                submit.load_config(str(self.config), None)
        self.assertEqual(submit.load_config(str(self.config), "override")[1], "override")
        with self.assertRaisesRegex(ValueError, "Configuration not found"):
            submit.load_config(str(self.root / "missing.json"), None)

    def test_namespace_isolates_shared_root_and_generated_runs_are_unique(self):
        one = submit.execute(self.args("--plan", "--namespace", "team-one", "--run-name", "same-name"))
        two = submit.execute(self.args("--plan", "--namespace", "team-two", "--run-name", "same-name"))
        self.assertNotEqual(one["remote_dir"], two["remote_dir"])
        first = submit.execute(self.args("--plan"))["run_name"]
        second = submit.execute(self.args("--plan"))["run_name"]
        self.assertNotEqual(first, second)

    def test_host_partition_validation_and_single_cluster_fallback(self):
        with self.assertRaisesRegex(ValueError, "not allowed"):
            submit.decide(self.args("--host", "a800", "--partition", "fnlp-x090"), configuration())
        only = configuration()
        del only["clusters"]["a800"]
        self.assertEqual(submit.decide(self.args("--task-size", "medium"), only)["cluster"], "x090")
        with self.assertRaisesRegex(ValueError, "Unknown --host"):
            submit.decide(self.args("--host", "a800"), only)
        self.assertEqual(submit.decide(self.args("--partition", "fnlp-4090"), configuration())["cluster"], "x090")

    def test_dynamic_cluster_key_and_account_qos(self):
        config = configuration()
        config["clusters"] = {"custom": config["clusters"]["a800"]}
        config["clusters"]["custom"].update({"account": "group", "qos": "normal"})
        self.config.write_text(json.dumps(config), encoding="utf-8")
        result = submit.execute(self.args("--plan", "--host", "custom", "--qos", "short"))
        self.assertEqual(result["account"], "group")
        self.assertIn("#SBATCH --qos=short", result["sbatch_text"])

    def test_home_root_expands_only_during_staging(self):
        config = configuration()
        config["clusters"]["x090"]["remote_root"] = "~/"
        self.config.write_text(json.dumps(config), encoding="utf-8")
        planned = submit.execute(self.args("--plan", "--run-name", "home-test"))
        self.assertEqual(planned["remote_dir"], "~/research-team/home-test")
        commands = []
        with mock.patch.object(submit, "run", side_effect=self.transport(commands)):
            staged = submit.execute(self.args("--dry-run", "--run-name", "home-test"))
        self.assertEqual(staged["remote_dir"], "/home/remote-user/research-team/home-test")

    def test_single_file_reserved_names_are_rejected_before_transport(self):
        for name in ("run.sbatch", "payload.tar.gz", "logs"):
            file = self.root / name
            file.write_bytes(b"source")
            args = self.args("--command", "true")
            args.source = str(file)
            with mock.patch.object(submit, "run") as network:
                with self.assertRaisesRegex(ValueError, "single-file source"):
                    submit.execute(args)
                network.assert_not_called()

    def test_option_like_alias_and_unsafe_remote_paths_are_rejected(self):
        for alias in ("-oProxyCommand=unsafe", "host;command", "user@host"):
            config = configuration()
            config["clusters"]["a800"]["ssh_alias"] = alias
            self.config.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ssh_alias"):
                submit.load_config(str(self.config), None)
        for value in ("relative/path", "/path with spaces", "~/../other", "/path;command"):
            with self.assertRaises(ValueError):
                submit.remote_root(value)

    def test_dry_run_stages_and_creates_logs_but_does_not_submit(self):
        commands = []
        with mock.patch.object(submit, "run", side_effect=self.transport(commands)):
            result = submit.execute(self.args("--dry-run", "--run-name", "stage-only"))
        self.assertEqual(result["status"], "staged")
        self.assertIsNone(result["job_id"])
        self.assertEqual(result["remote_dir"], "/home/remote-user/codex_runs/research-team/stage-only")
        self.assertEqual(sum(command[0] == "scp" for command in commands), 2)
        self.assertTrue(any("mkdir -p /home/remote-user/codex_runs/research-team/stage-only/project/logs" in command[-1] for command in commands))
        self.assertFalse(any("sbatch --parsable" in command[-1] for command in commands))
        for command in commands:
            self.assertIn("BatchMode=yes", command)
            self.assertIn("ConnectTimeout=15", command)
            self.assertFalse(any("StrictHostKeyChecking=no" in item for item in command))

    def test_submit_uses_parsable_after_logs_creation_and_reports_hints(self):
        commands = []
        with mock.patch.object(submit, "run", side_effect=self.transport(commands)):
            result = submit.execute(self.args("--run-name", "submit-test"))
        submit_indices = [index for index, command in enumerate(commands) if "sbatch --parsable" in command[-1]]
        self.assertEqual(len(submit_indices), 1)
        log_index = next(index for index, command in enumerate(commands) if "project/logs" in command[-1])
        self.assertLess(log_index, submit_indices[0])
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["job_id"], "12345")
        self.assertTrue(result["log_hint"].endswith("project-12345.out"))
        self.assertIn("squeue -u remote-user", result["queue_command"])
        self.assertIn("scancel 12345", result["cancel_command"])

    def test_existing_run_directory_failure_prevents_upload_and_submit(self):
        commands = []
        base = self.transport(commands)
        def collide(command, *, timeout=120):
            if command[0] == "ssh" and " && mkdir " in command[-1]:
                commands.append(command)
                raise RuntimeError("mkdir: File exists")
            return base(command, timeout=timeout)
        with mock.patch.object(submit, "run", side_effect=collide):
            with self.assertRaisesRegex(RuntimeError, "File exists"):
                submit.execute(self.args("--run-name", "existing"))
        self.assertFalse(any(command[0] == "scp" or "sbatch --parsable" in command[-1] for command in commands))

    def test_ambiguous_submission_is_never_retried(self):
        commands = []
        base = self.transport(commands)
        def ambiguous(command, *, timeout=120):
            result = base(command, timeout=timeout)
            if "sbatch --parsable" in command[-1]:
                raise subprocess.TimeoutExpired(command, timeout)
            return result
        with mock.patch.object(submit, "run", side_effect=ambiguous):
            with self.assertRaisesRegex(RuntimeError, "acceptance is unknown"):
                submit.execute(self.args())
        self.assertEqual(sum("sbatch --parsable" in command[-1] for command in commands), 1)

    def test_environment_requests_fail_explicitly_in_script(self):
        result = submit.execute(self.args("--plan", "--cuda-module", "cuda/test", "--conda-env", "train", "--conda-sh", "~/custom/etc/profile.d/conda.sh"))
        script = result["sbatch_text"]
        self.assertIn("Requested CUDA module failed to load", script)
        self.assertIn("Conda initialization script not found", script)
        self.assertIn('conda_init="$HOME"/custom/etc/profile.d/conda.sh', script)
        self.assertNotIn("|| true", script)

    def test_changed_source_is_rejected_before_archive_upload(self):
        _, manifest, _ = submit.build_manifest(self.source, [], 1)
        self.write("train.py", b"changed size")
        with self.assertRaisesRegex(ValueError, "Source changed"):
            submit.make_archive(self.source, self.root / "payload.tar.gz", manifest)

    def test_cli_plan_outputs_json_without_transport(self):
        stdout = io.StringIO()
        with mock.patch.object(submit, "run", side_effect=AssertionError("Network")), contextlib.redirect_stdout(stdout):
            status = submit.main(["--source", str(self.source), "--config", str(self.config), "--plan"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "planned")


if __name__ == "__main__":
    unittest.main()
