# Repair or Verification Brief

Create a ticket from this read-only template in the target repository and supply its absolute path via `--material`. Fill only applicable inputs; supply linked materials explicitly because snapshots do not collect links recursively. The injected worker contract owns execution and reporting rules; [Integration repair](repair-and-closeout.md) owns coordinator decisions.

## Goal and pinned inputs

- Ticket ID and type: text-conflict repair / behavior repair / investigation / verification.
- Authorized goal, original tickets and requirements; behavior that must survive from **each side**:
- Target branch and full base SHA; incoming SHA or controlled patch with provenance; previous repair SHA when relevant:
- Accessible source materials and durable failure evidence:
- Exact reproduction command, observed exit code, expected versus actual behavior:
- Changes since the previous baseline and compatibility questions, if any:

## Required evidence

- Task-specific checks and expected outcomes covering original requirements and the combined behavior:
- Required reproduction evidence and regression coverage; for a text conflict, reproduce by merging the pinned incoming SHA before resolving both intents:
- Findings artifact and questions to answer for investigation or verification-only work:
