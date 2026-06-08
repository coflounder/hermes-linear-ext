#!/usr/bin/env bash
# Prove the plugin loads + registers the `linear` platform inside a stock Hermes
# image — no patch, no live creds. Requires Docker + a local hermes-agent image.
#   IMAGE=nousresearch/hermes-agent:vYYYY.M.D bash scripts/validate_load.sh
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE="${IMAGE:-hermes-agent-coflounder:v2026.5.29.2-dev2}"
work="$(mktemp -d)"; cp -r linear "$work/linear"
cat > "$work/check.py" <<'PY'
import sys
from hermes_cli.plugins import discover_plugins
from gateway.platform_registry import platform_registry
try: discover_plugins(force=True)
except TypeError: discover_plugins()
names=[e.name for e in platform_registry.plugin_entries()]
print("platforms:", sorted(names))
sys.exit(0 if "linear" in names else 1)
PY
cat > "$work/Dockerfile" <<DOCKER
FROM ${IMAGE}
USER root
COPY linear /opt/data/plugins/linear
COPY check.py /tmp/check.py
RUN /opt/hermes/.venv/bin/hermes plugins enable linear-platform || true
RUN /opt/hermes/.venv/bin/python /tmp/check.py
DOCKER
docker build -f "$work/Dockerfile" -t hermes-linear-loadtest:check "$work"
echo "OK: linear platform loads + registers in ${IMAGE}"
