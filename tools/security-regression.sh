#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
STATIC_ONLY=
[ "${1:-}" = "--static-only" ] && STATIC_ONLY=1
cd "$ROOT"

echo '=== SECURITY CONTRACT / SOURCE TESTS ==='
python3 -m unittest tests.test_security_contract

echo '=== SECURITY-SENSITIVE INSTALLER / OTA TESTS ==='
python3 -m unittest \
  tests.test_jarvis_crown_device_gate \
  tests.test_jarvis_crown_unlock \
  tests.test_jarvis_crown_recovery \
  tests.test_jarvis_show_flow \
  tests.test_release_contract \
  tests.test_secret_scan \
  tests.test_supply_chain \
  tests.test_slotctl_ab

if [ -n "$STATIC_ONLY" ]; then
  echo 'PASS: static security regression suite complete'
  exit 0
fi

version=$(go version 2>/dev/null | awk '{print $3}' || true)
case "$version" in
  go1.2[6-9]*|go1.[3-9][0-9]*|go[2-9].*) ;;
  *) echo "FAIL: Go >=1.26 required for runtime security regression tests; found ${version:-unknown}" >&2; exit 2;;
esac

echo '=== DEVICE RUNTIME SECURITY TESTS ==='
(
  cd echod
  go test \
    ./internal/config \
    ./internal/feature/api \
    ./internal/feature/announce \
    ./internal/feature/mute \
    ./internal/feature/phone \
    ./internal/feature/security \
    ./internal/feature/sendspin \
    ./internal/feature/web \
    ./internal/update
)

echo '=== DASHCAST AUTHENTICATION TESTS ==='
(
  cd dashcast
  go test .
)

echo 'PASS: full security regression suite complete'
