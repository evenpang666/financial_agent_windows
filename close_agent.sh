#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$project_root/.venv/bin/python" "$project_root/scripts/linux_service.py" DisableDshWeb
"$project_root/.venv/bin/python" "$project_root/scripts/linux_service.py" DisableAgent
