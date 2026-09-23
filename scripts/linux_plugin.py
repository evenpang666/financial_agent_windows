#!/usr/bin/env python3
"""Check or re-register the local DSH plugin on Linux."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "dsh-finance-agent"
PROFILE = Path.home() / ".dsh" / "profiles" / "web"
BIN = ROOT / ".local" / "node_modules" / ".bin"


def installed_spec() -> str:
    try:
        return str(json.loads((PROFILE / "package.json").read_text())["dependencies"].get("dsh-finance-agent", ""))
    except (OSError, ValueError, KeyError, AttributeError):
        return ""


def ready() -> bool:
    spec = installed_spec()
    try:
        return spec.startswith("link:") and (PROFILE / spec[5:]).resolve() == PLUGIN.resolve()
    except OSError:
        return False


def register():
    environment = os.environ.copy()
    environment["PATH"] = str(BIN) + os.pathsep + environment.get("PATH", "")
    dsh = shutil.which("dsh", path=environment["PATH"])
    if not dsh:
        raise RuntimeError("dsh was not found")
    if installed_spec():
        subprocess.run([dsh, "plugin", "--profile", "web", "remove", "dsh-finance-agent"],
                       cwd=ROOT, env=environment, check=True)
    subprocess.run([dsh, "plugin", "--profile", "web", "add", str(PLUGIN)], cwd=ROOT, env=environment, check=True)
    subprocess.run([dsh, "plugin", "--profile", "web", "install"], cwd=ROOT, env=environment, check=True)
    if not ready():
        raise RuntimeError("Plugin registration could not be verified")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "status":
        print("DSH plugin: registered" if ready() else "DSH plugin: missing or stale")
        sys.exit(0 if ready() else 1)
    if action == "register":
        register()
        print("DSH plugin re-registered")
    else:
        raise SystemExit("Usage: linux_plugin.py status|register")
