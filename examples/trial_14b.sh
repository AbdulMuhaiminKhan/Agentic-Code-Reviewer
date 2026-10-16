#!/usr/bin/env bash
# 2-case speed/memory trial of qwen2.5-coder:14b on the frozen test split (Git Bash on Windows).
# Logs `ollama ps` (GPU/CPU split) and free RAM every 30 s while the trial runs.
# The judge stays on the 7B model (from .env) so scores are comparable with the 7B runs.
#   bash examples/trial_14b.sh results/trial14b.log
set -u
LOG=${1:-results/trial14b.log}
export ACR_MODEL=qwen2.5-coder:14b ACR_NUM_CTX=${ACR_NUM_CTX:-8192} PYTHONUNBUFFERED=1
{
  echo "start $(date +%T) ACR_NUM_CTX=$ACR_NUM_CTX"
  python -m eval.backtest --cases data/cases.jsonl --clone data/django --split test --n 2 --out results/trial14b &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    echo "--- $(date +%T)"
    ollama ps | tail -n +2
    powershell -NoProfile -Command "\$o = Get-CimInstance Win32_OperatingSystem; 'RAM free {0:N1} GB of {1:N1} GB; pagefile in use {2:N1} GB' -f (\$o.FreePhysicalMemory/1MB), (\$o.TotalVisibleMemorySize/1MB), ((Get-CimInstance Win32_PageFileUsage | Measure-Object CurrentUsage -Sum).Sum/1KB)"
    sleep 30
  done
  wait "$pid"
  echo "end $(date +%T) exit=$?"
} > "$LOG" 2>&1
