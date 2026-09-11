#!/bin/bash
LOG=/tmp/opencode/hpm-exp01/evidence-raw/15-supervisor-heartbeat.log
for i in $(seq 1 6); do
  STATUS=$(herdr agent get exp01pi2 2>/dev/null | grep -o '"agent_status":"[a-z]*"' | head -1)
  printf '%s tick=%s pid=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$i" "$$" "$STATUS" >> "$LOG"
  sleep 5
done
printf '%s supervisor-exit pid=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$$" >> "$LOG"
