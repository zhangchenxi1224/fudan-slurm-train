"""Small submission check; optional driver/PyTorch checks run only in Slurm jobs."""

import argparse
import json
import os
import platform
import socket
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-gpu", action="store_true", help="Check nvidia-smi inside a GPU allocation.")
    parser.add_argument("--check-torch", action="store_true", help="Also run a tiny PyTorch CUDA tensor calculation; requires preinstalled PyTorch.")
    args = parser.parse_args(argv)
    result = {
        "python": platform.python_version(), "host": socket.gethostname(),
        "job_id": os.environ.get("SLURM_JOB_ID"),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "checks": {"python": "pass"},
    }
    try:
        if args.check_gpu or args.check_torch:
            if not result["job_id"]:
                raise RuntimeError("GPU checks require a Slurm GPU allocation; submit this script with --gpus 1.")
            driver = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                text=True, capture_output=True, timeout=15, check=True,
            )
            if not driver.stdout.strip():
                raise RuntimeError("nvidia-smi did not report a GPU.")
            result["gpu_driver"] = driver.stdout.strip().splitlines()
            result["checks"]["gpu_driver"] = "pass"
        if args.check_torch:
            import torch
            if not torch.cuda.is_available():
                raise RuntimeError("PyTorch CUDA is unavailable in this allocation/environment.")
            value = torch.arange(1024, dtype=torch.float32, device="cuda").reshape(32, 32)
            calculated = value @ torch.eye(32, device="cuda")
            torch.cuda.synchronize()
            if not torch.allclose(calculated, value):
                raise RuntimeError("CUDA tensor calculation returned an unexpected result.")
            result["torch"] = {"version": torch.__version__, "cuda": torch.version.cuda, "device": torch.cuda.get_device_name(0)}
            result["checks"]["torch_cuda"] = "pass"
        result["status"] = "passed"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        result.update(status="failed", error=str(error))
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
