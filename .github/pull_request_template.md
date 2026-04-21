# <PR title>

<!-- Branch must be prefixed: feature/ · fix/ · docs/ · chore/ -->

## Summary
<1–3 sentences: what & why>

## Changes
- <bullet>
- <bullet>

## Why
<business / user / tech motivation; link to handoff/BACKLOG.md or SPRINT_PLAN.md entry>

## Architecture notes
<only if non-trivial: data flow, new modules, batch pipeline changes, calc.js ↔ app.py parity impact>

## Risks & mitigations
- **Risk:** <what could break>
  **Mitigation:** <how this PR guards against it>

## Test plan
- [ ] `make test-py` passes
- [ ] `make test-js` passes
- [ ] `make test-parity` passes (hard gate for any math/threshold change)
- [ ] Manual smoke on `python app.py` at :8000 (golden path + one edge case)
- [ ] Touched UI? Keyboard + screen-reader sanity check

## Review checklist
- [ ] `/review` ran; zero BLOCKER/HIGH findings remain
- [ ] MEDIUM/LOW findings logged in `handoff/BACKLOG.md`
- [ ] No secrets / env values committed
- [ ] No changes to `spec/constants.json` or `handoff/USER_PROFILE.example.md` without an ADR

## Docs
- [ ] `handoff/CHANGELOG.md` updated (or will be via `/ship-done` post-merge)
- [ ] New ADR drafted if architectural decision made
- [ ] `README.md` / `RUN_ME.md` updated if setup or run steps changed

## Related
<!-- Linear / issue links, related PRs -->

## Screenshots / recordings
<!-- UI changes: before / after -->
