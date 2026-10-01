"""Smoke checks stay CPU-only unless explicitly requested within Slurm."""

import contextlib
import importlib.util
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location("smoke", Path(__file__).resolve().parents[1] / "examples" / "smoke.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class SmokeTests(unittest.TestCase):
    def test_default_check_needs_no_gpu_or_external_command(self):
        output = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(smoke.subprocess, "run", side_effect=AssertionError("external command")), contextlib.redirect_stdout(output):
            self.assertEqual(smoke.main([]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["checks"], {"python": "pass"})
        self.assertIsNone(result["job_id"])

    def test_gpu_and_torch_checks_require_allocation_before_running(self):
        for option in ("--check-gpu", "--check-torch"):
            with self.subTest(option=option), mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(smoke.subprocess, "run", side_effect=AssertionError("external command")), contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(smoke.main([option]), 1)
                self.assertEqual(json.loads(error.getvalue())["status"], "failed")


if __name__ == "__main__":
    unittest.main()
