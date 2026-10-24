#!/usr/bin/env bash
set -uo pipefail

REPO_DIR="/home/ervin/code/ak820pro-linux"
export PATH="/home/ervin/.local/bin:/home/ervin/.nvm/versions/node/v24.21.0/bin:/usr/local/bin:/usr/bin:/bin:${PATH}"
MAX_ITERATIONS="${1:-100}"
LOG_DIR="${REPO_DIR}/state/evolver_logs"
mkdir -p "$LOG_DIR"

cd "$REPO_DIR"

log() {
  local msg="[$(date -Iseconds)] $*"
  echo "$msg"
  echo "$msg" >> "${REPO_DIR}/state/evolver_main.log"
}

wait_for_cooldown() {
  for _ in $(seq 1 18); do
    local max_t=0
    for f in /sys/class/hwmon/hwmon*/temp*_input; do
      [[ -r "$f" ]] || continue
      local t=$(($(cat "$f" 2>/dev/null || echo 0) / 1000))
      (( t > max_t && t < 120 )) && max_t=$t
    done
    (( max_t <= 82 )) && break
    log "🌡️ Strix Halo APU at ${max_t}°C (>82°C guard), cooling down 10s..."
    sleep 10
  done
}

smoke_test() {
  local base_commit="$1"
  sleep 1

  # 1. Compile test: verify all Python files compile cleanly
  python3 -m compileall -q . || return 1

  # 2. Unit test suite: all tests must pass 100%
  python3 -m unittest discover -s tests || return 1

  # 3. Ensure at least one code file changed since base_commit
  git status --porcelain | grep -qE '\.(py|json|md|rules)$' || git diff --name-only "$base_commit" | grep -qE '\.(py|json|md|rules)$' || return 2

  return 0
}

log "Starting Autonomous Evolver for ak820pro-linux in ${REPO_DIR}"
log "Max iterations configured: ${MAX_ITERATIONS}"

for i in $(seq 1 "$MAX_ITERATIONS"); do
  wait_for_cooldown

  LAST_TAG=$(git tag -l "v0.*" --sort=-v:refname | head -n 1)
  LAST_TAG="${LAST_TAG:-v0.0.0}"
  MINOR=$(echo "$LAST_TAG" | awk -F. '{print $2}')
  NEXT_MINOR=$((MINOR + 1))
  NEXT_TAG="v0.${NEXT_MINOR}.0"
  SAFE_COMMIT=$(git rev-parse HEAD)
  ITER_LOG="${LOG_DIR}/iter_${NEXT_TAG}_$(date +%Y%m%d_%H%M%S).jsonl"
  PROMPT_FILE="${LOG_DIR}/prompt_${NEXT_TAG}.txt"

  # Check if a previous iteration's remote CI failed
  CI_FAILURE_LOG=$(ls -t "${LOG_DIR}"/ci_failure_*.log 2>/dev/null | head -n 1)
  if [[ -n "$CI_FAILURE_LOG" && -s "$CI_FAILURE_LOG" ]]; then
    MODE_INSTRUCTION="CRITICAL PRIORITY: Remote GitHub Actions CI failed! Diagnose the failure in ${CI_FAILURE_LOG}, fix the failing test or build error, ensure tests pass, and commit. Do not proceed to new features until remote CI is fixed."
  # Check if __NEXT_TAG__ has an explicit queued item in ITERATIONS.md
  elif [[ -n "$QUEUED_ITEM" ]]; then
    MODE_INSTRUCTION="PRIORITY QUEUE: Implement the queued specification for ${NEXT_TAG} from ITERATIONS.md. Follow all technical requirements and bugfix specifications defined there."
  else
    # Continuous Cadence rotation once priority queue completes:
    # 1. Modular Package Refactoring (<300 LOC per domain module)
    # 2. Protocol Reliability & FakeTransport Mocking
    # 3. Independent Deep Re-Audit & Optimization Discovery
    CADENCE_MOD=$(( NEXT_MINOR % 3 ))
    if [[ "$CADENCE_MOD" -eq 1 ]]; then
      MODE_INSTRUCTION="MODE: MODULAR PACKAGE REFACTORING. Continue splitting monolithic concerns out of ak820_ctrl.py into dedicated single-responsibility modules in ak820/ (<300 lines each) with clean regression tests. Keep backward compatibility shims intact."
    elif [[ "$CADENCE_MOD" -eq 2 ]]; then
      MODE_INSTRUCTION="MODE: RELIABILITY & ROBUSTNESS. Add protocol-level error handling, disconnect recovery, timeout guards, and FakeTransport simulation tests. Ensure full CI coverage with zero hardware attached."
    else
      MODE_INSTRUCTION="MODE: COMPREHENSIVE RE-AUDIT. Perform a complete deep architectural audit of the current codebase. Update REVIEW.md, record solved defects, and queue the next actionable improvements in ITERATIONS.md."
    fi
  fi

  log "=================================================================="
  log "Starting Autonomous Iteration ${i}/${MAX_ITERATIONS} (${LAST_TAG} -> ${NEXT_TAG})"
  log "Cadence Mode: ${MODE_INSTRUCTION}"
  log "Log: ${ITER_LOG}"
  log "=================================================================="

  cat > "$PROMPT_FILE" <<'EOF'
You are running an autonomous, git-tagged evolution iteration (__LAST_TAG__ -> __NEXT_TAG__) on ak820pro-linux (`/home/ervin/code/ak820pro-linux`) following the `local-claude-evolver` skill (`/home/ervin/.gemini/config/skills/local-claude-evolver/SKILL.md`) and specifications in `REVIEW.md` and `ITERATIONS.md`.

### Cadence Mode for this Iteration:
__MODE_INSTRUCTION__

### Speed & Discipline Rules (CRITICAL — Keep This Iteration Under 8–10 Minutes / 25 Turns):
1. **Batch tool calls in parallel & NEVER spawn inner subagents**: Call multiple `Read` / `Edit` / `Grep` tools in a single assistant turn. Do NOT call the `Task` or `Agent` subagent tool — perform all work directly in your main process. Keep `<thinking>` brief and decisive.
2. **Deterministic Verification Gate**:
   - Always run `python3 -m unittest discover -s tests` to verify existing and new tests pass 100%.
   - Always write regression tests for every bugfix or refactored module in `tests/`.
3. **Modularity Limits**:
   - Keep any newly created modules in `ak820/` strictly `< 300` lines.
   - If refactoring `ak820_ctrl.py`, maintain backward compatibility for existing entry points.
4. **Self-Documenting Evolution**:
   - Update `ITERATIONS.md`: mark `[x] __NEXT_TAG__` in the Priority Queue and add a 1-line summary to the Summary Table.
   - Commit cleanly: `git add -A && git commit -m "feat(__NEXT_TAG__): <concise summary>"`.
   - Output a 3-line summary and **EXIT IMMEDIATELY** (never edit files after tagging).
5. **Continuous Improvement & Diminishing Returns**:
   - Continue improving the codebase across iterations as long as meaningful improvements exist (modularity, error handling, performance, test coverage, code quality).
   - Only if all possible improvements from REVIEW.md, architectural refactoring, and audits are fully exhausted and the project is in a complete state, output `<STATUS>PROJECT_COMPLETE</STATUS>`.
EOF

  sed -i "s/__LAST_TAG__/${LAST_TAG}/g; s/__NEXT_TAG__/${NEXT_TAG}/g; s|__MODE_INSTRUCTION__|${MODE_INSTRUCTION}|g" "$PROMPT_FILE"

  timeout --foreground -k 30s 1200s claude -p "$(cat "$PROMPT_FILE")" \
    --max-turns 45 \
    --dangerously-skip-permissions \
    --output-format stream-json \
    --verbose > "$ITER_LOG" 2>&1 || true

  log "Verifying Iteration ${NEXT_TAG} health..."
  if smoke_test "$SAFE_COMMIT"; then
    if [[ -n "$(git status --porcelain)" ]]; then
      git add -A
      git diff --cached --quiet || git commit -m "feat(${NEXT_TAG}): iteration complete"
    fi
    git tag -f "$NEXT_TAG"
    git push origin master --tags || true
    log "✅ Iteration ${NEXT_TAG} successfully verified, committed, and pushed!"

    # Proactive Remote CI Verification Gate
    CI_CONCLUSION="unknown"
    if [[ -d .github/workflows ]]; then
      log "🔍 Polling remote GitHub Actions CI status for ${NEXT_TAG}..."
      sleep 12
      for _ in $(seq 1 12); do
        CI_DATA=$(gh run list --limit 1 --json status,conclusion -q '.[0]' 2>/dev/null || true)
        CI_STATUS=$(echo "$CI_DATA" | jq -r '.status // "unknown"')
        CI_CONCLUSION=$(echo "$CI_DATA" | jq -r '.conclusion // "unknown"')
        if [[ "$CI_STATUS" == "completed" ]]; then
          break
        fi
        sleep 10
      done
      if [[ "$CI_CONCLUSION" == "success" ]]; then
        log "✅ Remote GitHub Actions CI verified GREEN for ${NEXT_TAG}!"
        rm -f "${LOG_DIR}"/ci_failure_*.log 2>/dev/null || true
      elif [[ "$CI_CONCLUSION" == "failure" ]]; then
        log "❌ Remote GitHub Actions CI FAILED for ${NEXT_TAG}! Capturing failed logs..."
        gh run view --log-failed > "${LOG_DIR}/ci_failure_${NEXT_TAG}.log" 2>/dev/null || true
      else
        log "⏳ Remote CI still running (${CI_STATUS}/${CI_CONCLUSION}); proceeding..."
      fi
    fi
  else
    log "❌ Smoke test failed for ${NEXT_TAG}! Rolling back to ${SAFE_COMMIT}..."
    git reset --hard "$SAFE_COMMIT"
    git clean -fd
  fi

  # Check if agent signaled complete (refuse exit if remote CI is failed)
  if grep -q "<STATUS>PROJECT_COMPLETE</STATUS>" "$ITER_LOG" 2>/dev/null; then
    if [[ -d .github/workflows && "${CI_CONCLUSION:-unknown}" == "failure" ]]; then
      log "⚠️ Agent signaled PROJECT_COMPLETE but remote CI is FAILED! Continuing evolution to remediate CI..."
    else
      log "🏁 Agent signaled project is complete and remote CI is healthy. Exiting cleanly."
      break
    fi
  fi

  sleep 5
done

log "Autonomous overnight evolution loop finished."
