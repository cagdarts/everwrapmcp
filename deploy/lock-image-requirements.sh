#!/usr/bin/env bash
# Regenerate deploy/requirements-image.txt: every package the remote image installs,
# pinned with hashes for linux/x86_64. Run from the repository root after changing
# uv.lock or requirements-live.txt, in the development venv whose versions passed
# the tests (they constrain packages the lock leaves unpinned).
# CPU-only torch is installed separately from the Dockerfile's pinned URL and hash.
set -euo pipefail
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
extras=""
for e in ${EVERWRAP_EXTRAS:-en tr}; do extras="$extras --extra $e"; done
# shellcheck disable=SC2086
uv export --frozen --no-dev $extras --no-hashes --no-emit-project \
  | grep -v '^\s*#' | grep -v -E '^(torch|triton|nvidia-|cuda-|cryptography==)' > "$work/pins.txt"
# requirements-live.txt deliberately raises cryptography above presidio's cap
# (see docs/DEPENDENCY_SECURITY.md); keep that override explicit.
grep -E '^cryptography==' requirements-live.txt > "$work/override.txt"
uv pip freeze --python .venv/bin/python | grep -E '^[A-Za-z0-9_.-]+==' \
  | grep -v -i -E '^(torch|triton|nvidia|cuda|cryptography|pyobjc|appnope|pywin32)' > "$work/tested.txt"
uv pip compile "$work/pins.txt" requirements-live.txt \
  --override "$work/override.txt" --constraint "$work/tested.txt" \
  --generate-hashes --python-version 3.12 --python-platform x86_64-manylinux_2_28 \
  --no-header --no-annotate -o deploy/requirements-image.txt
echo "Wrote deploy/requirements-image.txt"
