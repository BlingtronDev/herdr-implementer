# Ticket B (no dependency)
Create b.txt with HPM07_B_OK plus newline and test the exact contents; commit it.
Acceptance includes observing the master checkpoint at absolute path `/tmp/hpm07-real-l6liisu5/opencode/.git/release-b` before declaring delivered.
This is a controlled concurrency experiment, not a user permission prompt. Remain working while that file is absent.
Use a foreground Python loop that only reads gate existence and sleeps 1 second, with no total task deadline.
If your tool window expires, continue checking; only the actual file releases this checkpoint.
When present, verify b.txt again and write your contract result. Never create or change the gate yourself.
