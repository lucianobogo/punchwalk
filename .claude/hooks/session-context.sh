#!/usr/bin/env bash
# SessionStart hook: prints the current build state into Claude's context
# on every new session, resume, /clear and compaction, so work picks up
# exactly where it left off.

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}" 2>/dev/null || exit 0

echo "===== PUNCHWALK BUILD STATE (injected by .claude/hooks/session-context.sh) ====="
echo "Now: $(TZ=America/New_York date '+%a %Y-%m-%d %H:%M %Z')"
echo

if [ -f STATE.md ]; then
  cat STATE.md
else
  echo "STATE.md is missing. Start from PLAN.md, Phase 0."
fi

echo
echo "----- Latest PROGRESS.md entries -----"
if [ -f PROGRESS.md ]; then
  tail -n 15 PROGRESS.md
fi

echo
echo "----- git -----"
git status --short 2>/dev/null | head -n 40 || true
git log --oneline -6 2>/dev/null || true

echo
echo "Orchestrator: do the 'Start of every session' steps in your instructions before anything else."
exit 0
