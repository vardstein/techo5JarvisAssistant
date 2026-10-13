#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
STATIC_ONLY=
[ "${1:-}" = "--static-only" ] && STATIC_ONLY=1
cd "$ROOT"

echo '=== SECURITY REGRESSION GATE ==='
if [ -n "$STATIC_ONLY" ]; then
  tools/security-regression.sh --static-only
else
  tools/security-regression.sh
fi

echo '=== PYTHON INSTALLER / OTA TESTS ==='
python3 -m unittest discover -s tests -p 'test_*.py'

echo '=== PYTHON SYNTAX ==='
python3 -m compileall -q tools tests

echo '=== BOARD CATALOGUE ==='
python3 tools/jarvis_crown/catalogue.py check

echo '=== SHELL SYNTAX ==='
bash -n tools/release-jarvis-show.sh
bash -n tools/linux/deploy-rootfs.sh
bash -n tools/linux/build-image.sh
bash -n tools/linux/build-kernel.sh
sh -n tools/linux/mkrootfs.sh
sh -n tools/linux/slotctl

echo '=== WORKFLOW YAML ==='
python3 - <<'PY'
from pathlib import Path
try:
    import yaml
except ImportError:
    print('WARN: PyYAML unavailable; workflow YAML parse deferred to CI/GitHub')
else:
    for path in sorted(Path('.github/workflows').glob('*.yml')):
        yaml.safe_load(path.read_text(encoding='utf-8'))
        print('PASS:', path)
PY

echo '=== DIFF HYGIENE ==='
git diff --check

if [ -n "$STATIC_ONLY" ]; then
    echo 'PASS: static-only validation complete'
    exit 0
fi

echo '=== GO TOOLCHAIN ==='
version=$(go version 2>/dev/null | awk '{print $3}' || true)
case "$version" in
  go1.2[6-9]*|go1.[3-9][0-9]*|go[2-9].*) ;;
  *) echo "FAIL: Go >=1.26 required for full validation; found ${version:-unknown}" >&2; exit 2;;
esac

echo '=== GO TESTS ==='
go test ./...
(cd echod && go test ./...)
(cd dashcast && go test ./...)

echo 'PASS: full Jarvis Show validation complete'
