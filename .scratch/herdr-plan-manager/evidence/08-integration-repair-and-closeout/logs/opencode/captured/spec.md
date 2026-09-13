# Behavior-conflict goal
Initial tickets: A and B, independent from the same seed commit.
A changes producer output to cents with explicit unit metadata. B adds currency formatting to the legacy dollar consumer.
Final combined goal: render(produce(12)) == '$12.00', render(produce(0)) == '$0.00', and the legacy dollar packet remains supported.
Both original tickets may pass individually while combined behavior fails; that must trigger a new repair ticket and keep the goal open.

Use your own tools; do not spawn subagents. The coordinator owns integration and shared records.
