# Text-conflict goal
Initial tickets: A and B, independent from the same seed commit.
A adds whitespace normalization. B adds the greeting `Hello, <name>!`.
Final combined goal: format_user(' Ada ') == 'Hello, Ada!' and format_user('Bob') == 'Hello, Bob!'.
Intermediate per-ticket outputs differ by design; preserve both feature intents in the final combination.
Dependent consumer work stays blocked until both features are integrated and compatible.

Use your own tools; do not spawn subagents. The coordinator owns integration and shared records.
