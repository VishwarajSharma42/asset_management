#!/usr/bin/env python3
"""Run the three test suites. They run as separate pytest sessions on purpose: the backend and
the Pi each have their own conftest.py and must never see each other's modules."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITES = [("backend", ROOT / "backend", []), ("pi", ROOT / "pi", []),
          ("end-to-end", ROOT, ["tests", "-p", "no:cacheprovider"])]

failed = []
for name, cwd, extra in SUITES:
    print(f"\n=== {name} ===", flush=True)
    code = subprocess.call([sys.executable, "-m", "pytest", "-q", *extra], cwd=cwd)
    if code:
        failed.append(name)
print("\nFAILED: " + ", ".join(failed) if failed else "\nall suites passed")
sys.exit(1 if failed else 0)
