#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
export PATH="$project_root/.local/node_modules/.bin:$PATH"
echo 'Stopping all managed services...'
.venv/bin/python scripts/linux_service.py DisableDshWeb
.venv/bin/python scripts/linux_service.py DisableAgent
command -v git >/dev/null || { echo 'Git is missing.'; exit 1; }
echo 'Pulling latest project code...'
git pull --ff-only
echo 'Updating Python dependencies in .venv...'
.venv/bin/python -m pip install -r requirements.txt
echo 'Re-registering the DSH plugin; dsh and pnpm are unchanged...'
.venv/bin/python scripts/linux_plugin.py register
echo 'Update completed. All services remain stopped; restart the control panel to resume.'
