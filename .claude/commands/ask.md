# /ask

**Pause and present a structured question list before an ambiguous decision.**

## When to use

- A user instruction has 2+ plausible meanings
- A proposed change touches more than one feature and the boundary is unclear
- An agent suggestion contradicts another agent's suggestion in the same review iteration
- A refactor would rename public contracts consumed by 3+ features
- An irreversible operation is about to happen (data migration, schema rewrite, force-push, destructive file deletes)
- A math/threshold change would affect USER_PROFILE-driven numbers (see `handoff/USER_PROFILE.example.md`)

## Format

Ask 2–5 questions. Each follows the pattern:

```
**Question N:** <one-sentence prompt>

Default (if you skip): <strong default>
Alternatives: <1–2 alternatives with brief rationale>
```

Conclude with `Quick answers (even "all defaults" works):` and a numbered summary.

Pause and wait for response. Do not continue the current task until answered.
