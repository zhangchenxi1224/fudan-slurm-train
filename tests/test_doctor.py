"""Doctor checks use mocked transport: these tests never authenticate to a cluster."""

import contextlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("doctor", SCRIPTS / "doctor.py")
doctor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(doctor)


def configuration():
    return {
        "run_namespace": "research-team",
        "clusters": {
            "a800": {"ssh_alias": "test-a800", "default_partition": "a800", "allowed_partitions": ["a800"], "remote_root": "~/codex_runs"},
            "x090": {"ssh_alias": "test-x090", "default_partition": "fnlp-x090", "allowed_partitions": ["fnlp-x090"], "remote_root": "~/codex_runs"},
        },
    }


def remote_output(partition="a800", failed=None, omitted=None):
    names = ["tool.bash", "home", "identity", "tool.tar", "tool.sbatch", "tool.squeue", "tool.sinfo", "tool.sacct", "remote_root", "partition." + partition, "python"]
    return "Login banner\n" + "\n".join(f"FUDAN_DOCTOR\t{name}\t{'fail' if name == failed else 'pass'}\tDiagnostic message" for name in names if name != omitted)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "settings.json"
        self.config.write_text(json.dumps(configuration()), encoding="utf-8")
        self.which = mock.patch.object(doctor.shutil, "which", side_effect=lambda name: name)
        self.which.start()
        self.addCleanup(self.which.stop)

    def args(self, *extra):
        return doctor.parser().parse_args(["--config", str(self.config), *extra])

    def transport(self, commands, remote=None):
        def fake_call(command, timeout):
            commands.append((command, timeout))
            if "-G" in command:
                output = "hostname cluster.example.invalid\nuser shared-user\nport 20000\nidentityfile ~/.ssh/test_key\n"
            else:
                if remote is None:
                    raise AssertionError("Unexpected authentication attempt")
                output = remote
            return subprocess.CompletedProcess(command, 0, output, "")
        return fake_call

    def test_default_only_expands_aliases_and_does_not_claim_remote_ready(self):
        commands = []
        with mock.patch.object(doctor, "call", side_effect=self.transport(commands)):
            result = doctor.execute(self.args())
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["scope"], "local")
        self.assertFalse(result["remote_checked"])
        self.assertIsNone(result["remote_ready"])
        self.assertIn("unverified", result["summary"])
        self.assertEqual(len(commands), 2)
        for command, timeout in commands:
            self.assertEqual(command[0:2], ["ssh", "-G"])
            self.assertIn("CanonicalizeHostname=no", command)
            self.assertIn("PermitLocalCommand=no", command)
            self.assertEqual(timeout, 20)

    def test_config_precedence_namespace_override_and_host_filter(self):
        commands = []
        args = doctor.parser().parse_args(["--namespace", "individual", "--host", "x090"])
        with mock.patch.dict(os.environ, {"FUDAN_SLURM_CONFIG": str(self.config)}), mock.patch.object(doctor, "call", side_effect=self.transport(commands)):
            result = doctor.execute(args)
        self.assertEqual(result["namespace"], "individual")
        self.assertEqual(result["config"], str(self.config.resolve()))
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0][-1], "test-x090")
        with mock.patch.dict(os.environ, {"FUDAN_SLURM_CONFIG": str(self.root / "missing.json")}), mock.patch.object(doctor, "call", side_effect=self.transport([])):
            self.assertEqual(doctor.execute(self.args())["status"], "ready")

    def test_missing_config_is_actionable_json_and_exits_nonzero(self):
        output = io.StringIO()
        with mock.patch.object(doctor, "call") as transport, contextlib.redirect_stdout(output):
            code = doctor.main(["--config", str(self.root / "missing.json")])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("Copy config.example.json", result["checks"][-1]["message"])
        transport.assert_not_called()

    def test_invalid_or_placeholder_config_never_starts_transport(self):
        config = configuration()
        config["run_namespace"] = "YOUR_RUN_NAMESPACE"
        self.config.write_text(json.dumps(config), encoding="utf-8")
        with mock.patch.object(doctor, "call") as transport:
            self.assertEqual(doctor.execute(self.args("--remote"))["status"], "incomplete")
        transport.assert_not_called()

    def test_missing_local_ssh_prevents_remote_checks(self):
        with mock.patch.object(doctor.shutil, "which", return_value=None), mock.patch.object(doctor, "call") as transport:
            result = doctor.execute(self.args("--remote"))
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["remote_checked"])
        transport.assert_not_called()

    def test_example_ssh_alias_values_fail_before_remote_authentication(self):
        response = subprocess.CompletedProcess([], 0, "hostname A800_LOGIN_HOST\nuser SHARED_CLUSTER_USER\nport 22\n", "")
        with mock.patch.object(doctor, "call", return_value=response) as transport:
            result = doctor.execute(self.args("--host", "a800", "--remote"))
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["remote_checked"])
        self.assertEqual(transport.call_count, 1)

    def test_remote_success_is_bounded_read_only_and_verifies_host_keys(self):
        commands = []
        with mock.patch.object(doctor, "call", side_effect=self.transport(commands, remote_output())):
            result = doctor.execute(self.args("--host", "a800", "--remote", "--timeout", "12"))
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["remote_checked"])
        self.assertTrue(result["remote_ready"])
        self.assertEqual(len(commands), 2)
        command, timeout = commands[-1]
        self.assertEqual(timeout, 12)
        for option in ("BatchMode=yes", "ConnectTimeout=12", "StrictHostKeyChecking=yes", "UpdateHostKeys=no", "PermitLocalCommand=no", "ClearAllForwardings=yes", "RemoteCommand=none"):
            self.assertIn(option, command)
        shell = shlex.split(command[-1])
        self.assertEqual(shell[:2], ["bash", "-lc"])
        self.assertIn("sinfo --noheader --format=%P", shell[2])
        self.assertNotIn("mkdir", shell[2])
        self.assertNotIn("sbatch --", shell[2])
        self.assertNotIn("nvidia-smi", shell[2])
        self.assertTrue(any("compute-node CUDA" in item for item in result["limitations"]))

    def test_remote_partition_or_root_failure_never_claims_ready(self):
        for failed in ("partition.a800", "remote_root"):
            with self.subTest(failed=failed), mock.patch.object(doctor, "call", side_effect=self.transport([], remote_output(failed=failed))):
                result = doctor.execute(self.args("--host", "a800", "--remote"))
            self.assertEqual(result["status"], "incomplete")
            self.assertFalse(result["remote_ready"])
            self.assertTrue(any(item["name"] == "remote." + failed and item["status"] == "fail" for item in result["checks"]))

    def test_missing_duplicate_or_malformed_remote_output_is_not_success(self):
        outputs = ["ordinary login output", remote_output(omitted="tool.tar"), remote_output() + "\nFUDAN_DOCTOR\thome\tpass\tDuplicate", remote_output() + "\nFUDAN_DOCTOR\tbad"]
        for output in outputs:
            with self.subTest(output=output[:30]), mock.patch.object(doctor, "call", side_effect=self.transport([], output)):
                result = doctor.execute(self.args("--host", "a800", "--remote"))
            self.assertEqual(result["status"], "incomplete")
            self.assertFalse(result["remote_ready"])

    def test_ssh_authentication_failure_and_timeout_are_actionable(self):
        local = self.transport([])
        for failure in (subprocess.CompletedProcess([], 255, "", "Permission denied (publickey)."), subprocess.TimeoutExpired(["ssh"], 20)):
            def fake(command, timeout):
                if "-G" in command:
                    return local(command, timeout)
                if isinstance(failure, Exception):
                    raise failure
                return failure
            with mock.patch.object(doctor, "call", side_effect=fake):
                result = doctor.execute(self.args("--host", "a800", "--remote"))
            self.assertEqual(result["status"], "incomplete")
            self.assertFalse(result["remote_ready"])
            self.assertTrue(any(item["name"] == "remote_connection" and item["status"] == "fail" for item in result["checks"]))

    def test_untrusted_interpolated_values_and_invalid_timeout_are_rejected(self):
        invalid_options = [
            ["--timeout", "0"], ["--timeout", "61"], ["--host", "unknown"],
            ["--conda-env", "training"], ["--remote", "--conda-sh", "~/conda.sh"],
            ["--remote", "--conda-env", "name;touch-file"],
            ["--remote", "--conda-env", "train", "--conda-sh", "~/../other.sh"],
            ["--remote", "--cuda-module", "cuda/12;bad"],
            ["--remote", "--cuda-module", "cuda/../bad"],
        ]
        for options in invalid_options:
            with self.subTest(options=options), mock.patch.object(doctor, "call") as transport:
                result = doctor.execute(self.args(*options))
            self.assertEqual(result["status"], "incomplete")
            transport.assert_not_called()

    def test_optional_environment_checks_only_report_python_and_preserve_paths(self):
        args = self.args("--remote", "--conda-env", "train", "--conda-sh", "~/custom/etc/profile.d/conda.sh", "--cuda-module", "cuda/12.4")
        script, expected = doctor.remote_script(configuration()["clusters"]["a800"], "individual", args)
        self.assertIn('conda_init="$HOME"/custom/etc/profile.d/conda.sh', script)
        self.assertIn("module load cuda/12.4", script)
        self.assertIn("conda activate train", script)
        self.assertIn("python --version", script)
        self.assertTrue({"conda_env", "python", "cuda_module"}.issubset(expected))
        for unwanted in ("pip install", "conda install", "nvidia-smi", "import torch", "sbatch --", "mkdir"):
            self.assertNotIn(unwanted, script)


if __name__ == "__main__":
    unittest.main()
