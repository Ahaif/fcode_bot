#!/usr/bin/env bash
# Run this bot locally against the official starter.
#
# Usage:
#   ./scripts/run_vs_starter.sh
#   ./scripts/run_vs_starter.sh maps/duel.map26
#   ./scripts/run_vs_starter.sh maps/duel.map26 1
#   WATCH=1 ./scripts/run_vs_starter.sh maps/hive.map26 2
#
# Env overrides:
#   SEED=1 REPLAY=replay.replay26 WATCH=1 TLE=0 ./scripts/run_vs_starter.sh [map] [seed]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# fcode walks the entire bot directory. A nested .venv will fail validation
# (pip/site-packages use `finally`, custom exceptions, etc.).
if [[ -d "$ROOT/.venv" ]]; then
  echo "error: $ROOT/.venv must not live inside the bot directory." >&2
  echo "fcode validates every .py file under '.', including pip inside .venv." >&2
  echo "Move it out, e.g.:" >&2
  echo "  mv \"$ROOT/.venv\" \"$ROOT/../bot-venv\"" >&2
  echo "Then use that fcode, or flor's: ../flor/.venv/bin/fcode" >&2
  exit 1
fi

# Prefer an fcode install *outside* this bot tree (never $ROOT/.venv).
if [[ -x "$ROOT/../flor/.venv/bin/fcode" ]]; then
  FCODE="$ROOT/../flor/.venv/bin/fcode"
elif [[ -x "$ROOT/../bot-venv/bin/fcode" ]]; then
  FCODE="$ROOT/../bot-venv/bin/fcode"
elif command -v fcode >/dev/null 2>&1; then
  FCODE="$(command -v fcode)"
else
  echo "fcode not found. Create a venv *outside* this bot dir and: pip install fcode" >&2
  echo "  python3 -m venv \"$ROOT/../bot-venv\"" >&2
  echo "  \"$ROOT/../bot-venv/bin/pip\" install fcode" >&2
  exit 1
fi

MAP_PATH="${1:-maps/vase.map26}"
SEED="${2:-${SEED:-1}}"
REPLAY="${REPLAY:-replay.replay26}"
TLE="${TLE:-0}"
WATCH="${WATCH:-0}"

ARGS=(run . bots/starter "$MAP_PATH" --seed "$SEED" --replay "$REPLAY" --tle "$TLE")
if [[ "$WATCH" == "1" ]]; then
  ARGS+=(--watch)
fi

echo "[run_vs_starter] $FCODE ${ARGS[*]}"
exec "$FCODE" "${ARGS[@]}"
