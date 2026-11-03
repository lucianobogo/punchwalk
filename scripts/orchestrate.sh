#!/usr/bin/env bash
# Start or resume the Punchwalk orchestrator: Claude Fable 5.1 as the main
# session, delegating to Opus 5.5 workers (see .claude/agents/).
#
#   ./scripts/orchestrate.sh              new session; then paste the /goal line from HUMAN_TODO.md
#   ./scripts/orchestrate.sh --continue   resume the latest session; an active /goal comes back by itself
#
# Optional overrides:
#   PUNCHWALK_MODEL=claude-opus-5-5              run the orchestrator on Opus instead of Fable
#   PUNCHWALK_PERMISSION_MODE=bypassPermissions  only on a machine or user account with nothing else important on it

set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${PUNCHWALK_MODEL:-claude-fable-5-1}"
MODE="${PUNCHWALK_PERMISSION_MODE:-auto}"

if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code isn't installed or isn't on your PATH." >&2
  exit 1
fi

if command -v caffeinate >/dev/null 2>&1; then
  # macOS: keep the machine awake for as long as the run is going.
  exec caffeinate -dims claude --agent orchestrator --model "$MODEL" --permission-mode "$MODE" "$@"
else
  exec claude --agent orchestrator --model "$MODEL" --permission-mode "$MODE" "$@"
fi
