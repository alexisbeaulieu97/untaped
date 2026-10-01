#!/usr/bin/env bash
# One-shot rename for 10.0 step 1; re-run after rebasing onto a main that
# gained new `capability_api` imports. Idempotent.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
[ -f src/untaped/capability_api.py ] && git mv src/untaped/capability_api.py src/untaped/sdk.py
[ -f tests/unit/test_capabilities/test_capability_api.py ] && \
  git mv tests/unit/test_capabilities/test_capability_api.py tests/unit/test_capabilities/test_sdk.py
git grep -lz 'capability_api' -- src tests docs scripts AGENTS.md CLAUDE.md README.md \
  ':!scripts/rename_sdk.sh' \
  | xargs -0 -r sed -i \
      -e 's/untaped\.capability_api/untaped.sdk/g' \
      -e 's/`capability_api`/`untaped.sdk`/g' \
      -e 's/capability_api\.py/sdk.py/g'  || true
git grep -n 'capability_api' -- src tests docs scripts AGENTS.md CLAUDE.md README.md \
  ':!scripts/rename_sdk.sh' || echo "rename complete"
