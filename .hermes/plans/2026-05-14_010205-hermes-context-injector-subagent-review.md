# Subagent review: hermes-context-injector public polish plan

Reviewed: 2026-05-14
Plan: `.hermes/plans/2026-05-14_010205-hermes-context-injector-public-polish.md`

## Verdict

APPROVE_WITH_CHANGES

## Summary

The plan is directionally correct:

- Keep `pre_llm_call` hook injection.
- Treat `system_prompt` as injected user-message-side instruction text, not a real system role.
- Keep `state.json` plugin-local and non-configurable.
- Add explicit injection cadence instead of injecting every turn or relying only on hash de-duplication.

Before implementation, make cadence rules and config/state boundaries more explicit.

## Required fixes

1. Define cadence as an implementable decision rule:
   - inject if first eligible turn
   - else inject if `eligible_turns_since_injection >= reinject_after_turns`
   - else inject if `minutes_since_last_injection >= reinject_after_minutes`
   - else skip
   - decide clearly whether changed context hash bypasses cadence. Recommendation: changed hash does not bypass cadence in v1; README must say file changes are not guaranteed to apply immediately.

2. Ignore legacy `state_path` completely:
   - `state_path` is not public config.
   - If legacy `config.json` contains `state_path`, loader must ignore it.
   - Add a test for legacy `state_path` ignored.

3. Keep tests away from real plugin-local `state.json`:
   - Public config must not expose `state_path`.
   - Tests still need dependency injection or an internal-only helper to point state at tmp dirs.
   - Example: `make_hook(config, plugin_dir=None, clock=None)`.

4. Clarify PyYAML dependency:
   - `yaml.safe_load` requires PyYAML.
   - README or error handling should make this clear.

5. Validate wrapper tag:
   - Restrict to a simple safe pattern such as `^[A-Za-z][A-Za-z0-9_-]{0,63}$`.
   - Fallback to `hermes_context` if invalid.

## Recommended improvements

- Keep initial cadence state minimal:
  - `last_injected_hash`
  - `last_injected_at`
  - `eligible_turns_since_injection`
  - `platform`
- Avoid `pending_hash` / `last_seen_hash` in first slice unless needed.
- Hash the final injected payload after truncation, not the raw full context file.
- Define “eligible turn” as platform/sender/context-file-readable passing.
- Add malformed config and invalid numeric fallback tests.
- Emphasize in README that skipped turns do not directly see injected context because hook context is API-call-time only.
- Avoid dual enablement during rename: do not enable both old and new plugin names at once.

## Suggested first slice simplification

1. YAML loader + config rename + plugin-local fixed state.
2. Configurable wrapper/system_prompt.
3. Hash final injected payload.
4. Minimal cadence implementation.
5. Tests.
6. Docs.
7. Rename and Hermes config migration after behavior tests pass.

## File changes by reviewer

None.
