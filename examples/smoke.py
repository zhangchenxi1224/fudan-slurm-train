"""Minimal, dependency-free entrypoint for an explicitly requested Slurm smoke job."""

import os
import platform
import socket

print("Python:", platform.python_version())
print("Host:", socket.gethostname())
print("Slurm job:", os.environ.get("SLURM_JOB_ID", "local"))
print("Smoke test completed.")
