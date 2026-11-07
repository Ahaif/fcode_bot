#!/usr/bin/env bash
# Package the bot's runtime files into bot_submission.zip and (optionally)
# upload it with `fcode submit`.
#
# Usage:
#   ./scripts/package_submission.sh              # build bot_submission.zip only
#   ./scripts/package_submission.sh --submit      # build, then fcode submit it
#   NAME="v0.3 dynamic defense" ./scripts/package_submission.sh --submit
#
# Only the files main.py actually needs at runtime go in the zip — never
# tests/, bots/ (reference opponents), docs/, maps/, or __pycache__/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -d "$ROOT/.venv" ]]; then
  echo "error: $ROOT/.venv must not live inside the bot directory." >&2
  exit 1
fi

if [[ -x "$ROOT/../flor/.venv/bin/fcode" ]]; then
  FCODE="$ROOT/../flor/.venv/bin/fcode"
elif [[ -x "$ROOT/../bot-venv/bin/fcode" ]]; then
  FCODE="$ROOT/../bot-venv/bin/fcode"
elif command -v fcode >/dev/null 2>&1; then
  FCODE="$(command -v fcode)"
else
  echo "fcode not found. Create a venv *outside* this bot dir and: pip install fcode" >&2
  exit 1
fi

# Runtime files only — keep this list in sync with main.py's own "Layout"
# docstring comment when adding/removing a top-level module.
TOP_LEVEL_FILES=(
  main.py
  constants.py
  team_store.py
  map_knowledge.py
  geometry.py
  navigator.py
  assignment.py
  claim_manager.py
  economy.py
  explore.py
  defense.py
  combat.py
  pathfind.py
  condition_state.py
  idle_investigate.py
)
# roles/ is auto-discovered (every *.py in it, minus __pycache__) instead of
# hardcoded — a hardcoded list here silently drops a newly-added role module
# from the zip without erroring (2026-08-21 outage: roles/sentinel_role.py
# existed on disk, passed local `fcode run .` directory-mode testing, but
# wasn't in this list, so the zip shipped without it and the platform's
# loader crashed on `from roles import sentinel_role` in main.py — "unknown
# location" ImportError, 100% game losses). roles/ only ever holds legitimate
# per-EntityType handlers, so auto-globbing it is safe; TOP_LEVEL_FILES stays
# an explicit list since the repo root also holds docs/scripts/maps/etc.
ROLES_FILES=()
while IFS= read -r -d '' f; do
  ROLES_FILES+=("${f#"$ROOT/"}")
done < <(find "$ROOT/roles" -maxdepth 1 -name '*.py' -print0 | sort -z)

for f in "${TOP_LEVEL_FILES[@]}"; do
  if [[ ! -f "$ROOT/$f" ]]; then
    echo "error: expected runtime file missing: $f" >&2
    exit 1
  fi
done
if [[ ${#ROLES_FILES[@]} -eq 0 ]]; then
  echo "error: no *.py files found under roles/ — something is very wrong" >&2
  exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/roles"
for f in "${TOP_LEVEL_FILES[@]}"; do
  cp "$ROOT/$f" "$STAGE/$f"
done
for f in "${ROLES_FILES[@]}"; do
  cp "$ROOT/$f" "$STAGE/$f"
done

# The platform's sandboxed loader does not set __file__ on bot modules —
# any module-level (import-time) reference to it crashes the whole bot load
# even if gated behind an unrelated env-var check (2026-08-20 outage: both
# idle_investigate.py and roles/builder_role.py did this for a debug-log
# default path). Catch it here before it ships again.
if grep -rn '__file__' "$STAGE" >/dev/null 2>&1; then
  echo "error: __file__ found in staged submission files — this crashes bot load on the platform:" >&2
  grep -rn '__file__' "$STAGE" >&2
  exit 1
fi

ZIP_PATH="$ROOT/bot_submission.zip"
rm -f "$ZIP_PATH"
( cd "$STAGE" && zip -q -r "$ZIP_PATH" . -x '*__pycache__*' -x '*.pyc' )

echo "[package_submission] built $ZIP_PATH"
unzip -l "$ZIP_PATH"

if [[ "${1:-}" == "--submit" ]]; then
  ARGS=(submit "$ZIP_PATH")
  if [[ -n "${NAME:-}" ]]; then
    ARGS+=(--name "$NAME")
  fi
  echo "[package_submission] $FCODE ${ARGS[*]}"
  exec "$FCODE" "${ARGS[@]}"
else
  echo
  echo "Not submitted. To upload:"
  echo "  $FCODE submit $ZIP_PATH"
  echo "or rerun this script with --submit."
fi
