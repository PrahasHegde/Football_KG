"""
Run every check in one go, the same steps CI runs:

    python scripts/check.py            # backend tests + frontend typecheck, unit tests and build
    python scripts/check.py --rebuild  # first rebuild the graph and the app database from the raw data

Stops at the first failing step and exits non-zero. Runs offline (FKG_OFFLINE=1): no Wikimedia or Groq calls.
Stop the app server before --rebuild (Windows locks the open database file).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
NPM = shutil.which("npm") or "npm"


def step(name: str, cmd: list[str], cwd: Path = ROOT) -> None:
    print(f"\n=== {name}: {' '.join(cmd)}", flush=True)
    t0 = time.time()
    env = {**os.environ, "FKG_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"}
    code = subprocess.call(cmd, cwd=cwd, env=env)
    if code != 0:
        print(f"\n✗ {name} failed (exit {code})")
        sys.exit(code)
    print(f"✓ {name} ({time.time() - t0:.1f}s)")


def main() -> None:
    if not (ROOT / "data" / "international_results" / "results.csv").exists():
        step("get data", [PY, "scripts/get_data.py"])
    if "--rebuild" in sys.argv or not (ROOT / "app_data" / "football.duckdb").exists():
        step("build graph", [PY, "src/build_kg.py"])
        step("build app database", [PY, "-m", "server.build_db"])
    step("backend tests", [PY, "-m", "pytest", "-q"])
    if not (ROOT / "web" / "node_modules").exists():
        step("install UI deps", [NPM, "ci"], ROOT / "web")
    step("UI typecheck + unit tests + build", [NPM, "run", "check"], ROOT / "web")
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
