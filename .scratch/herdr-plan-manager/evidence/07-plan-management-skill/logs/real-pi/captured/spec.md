# Dynamic integration smoke
Complete A, B and C with independent workers. A and B have no code dependency; C depends only on A integrated into main.
A creates api.txt containing exactly `HPM07_API_V1
` after an automatic session handoff.
B produces independent b.txt containing exactly `HPM07_B_OK
` after the observer releases its acceptance checkpoint.
C reads the integrated api.txt and writes consumer.txt containing exactly `consumer:HPM07_API_V1
`.
Each worker commits only its own business files, runs exact content assertions and records evidence.
Whole goal: all three outputs integrated into main with exact contents, automatic handoff and dynamic ordering proven.
Do not spawn subagents. This is a tiny fixture; use your own tools. Do not implement a scheduler.
