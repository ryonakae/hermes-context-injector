# hermes-context-injector public polish plan

Created: 2026-05-14 01:02 JST
Target: `/Users/ryo.nakae/.hermes/plugins/live-context-injector`

## Platform allowlist follow-up (2026-05-14)

Hermes core was re-checked before widening the public config shape:

- Built-in gateway platform enum values in `gateway/config.py`: `local`, `telegram`, `discord`, `whatsapp`, `slack`, `signal`, `mattermost`, `matrix`, `homeassistant`, `email`, `sms`, `dingtalk`, `api_server`, `webhook`, `msgraph_webhook`, `feishu`, `wecom`, `wecom_callback`, `weixin`, `bluebubbles`, `qqbot`, `yuanbao`.
- `run_agent.py` invokes `pre_llm_call` with `platform=getattr(self, "platform", None) or ""` and `sender_id=getattr(self, "_user_id", None) or ""`.
- `gateway/run.py` constructs gateway agents with `platform=source.platform.value` and `user_id=source.user_id`, so gateway sender allowlisting is based on `SessionSource.user_id`.
- CLI uses `platform="cli"`; cron-created agents use `platform="cron"`.

Decision: the public config should use platform-scoped access rules:

```yaml
platforms:
  cli:
    enabled: true
    allowed_sender_ids: []
  slack:
    enabled: false
    allowed_sender_ids:
      - U123EXAMPLE
```

Flat legacy `enabled_platforms` / `allowed_sender_ids` remains accepted as a migration fallback only. New docs and `config.example.yaml` should show every known Hermes platform key and explain that an empty per-platform sender list means all senders on that enabled platform.

## Goal

Turn the current local `live-context-injector` plugin into a more generally reusable/public-ready Hermes plugin named `hermes-context-injector`.

Keep the current mechanism: a Hermes `pre_llm_call` hook reads a context file and injects it into the conversation as ephemeral hook context.

Add plugin-local `config.yaml` support so users can configure:

- context file path
- injected prompt/instruction text
- supported platforms / sender allowlist
- truncation and session de-duplication behavior

## Current implementation summary

Files found:

```text
/Users/ryo.nakae/.hermes/plugins/live-context-injector/
├── __init__.py
├── plugin.yaml
├── config.json
├── state.json
├── .gitignore
└── tests/test_live_context_injector.py
```

Current behavior:

- `plugin.yaml`
  - `name: live-context-injector`
  - `provides_hooks: [pre_llm_call]`
- `__init__.py`
  - loads `config.json`
  - registers `ctx.register_hook("pre_llm_call", make_hook(config))`
  - reads `current_path`
  - injects only on enabled platforms
  - checks `allowed_sender_ids` for non-CLI platforms
  - tracks `last_injected_hash` per `session_id` in `state.json`
  - skips duplicate injection when context hash is unchanged for that session
  - truncates long context while preserving `## 詳細参照`
  - wraps context with a hard-coded Japanese `<hermes_live_context>...</hermes_live_context>` instruction block
- `config.json`
  - currently Ryo-specific absolute defaults:
    - `/Users/ryo.nakae/.hermes/live-contexts/current.md`
    - `/Users/ryo.nakae/.hermes/plugins/live-context-injector/state.json`
  - enables `cli` and `slack`
  - sender allowlist contains Ryo's Slack user ID
- tests cover platform gating, sender allowlist, duplicate hash skip, changed hash re-injection, truncation, and TTL pruning.

Runtime observations:

- `/Users/ryo.nakae/.hermes/config.yaml` currently enables:
  - `hermes-context-notifier`
  - `hermes-self-improvement`
  - `live-context-injector`
- Plugin discovery sees `live-context-injector` as enabled and active.
- `PluginManager.invoke_hook()` documents the `pre_llm_call` contract as:
  - return `{"context": "..."}` or a string
  - context is **always injected into the user message, never the system prompt**
  - this preserves prompt cache and avoids session DB persistence.

Important implication:

The requested “system prompt + context file” should be implemented as a configurable prompt/instruction text inside the injected context payload, not as an actual OpenAI/Hermes `system` role message. That keeps the current hook mechanism unchanged and avoids Hermes core changes.

## Proposed public plugin shape

Target layout after cleanup:

```text
~/.hermes/plugins/hermes-context-injector/
├── plugin.yaml
├── __init__.py
├── config.yaml
├── README.md
├── AGENTS.md
├── LICENSE                 # if publishing under MIT or another chosen license
├── .gitignore
└── tests/
    └── test_context_injector.py
```

Optional if the module grows:

```text
├── context_injector.py     # move implementation out of __init__.py
└── tests/conftest.py
```

Recommendation: for the first cleanup, keep `__init__.py` thin and move implementation to `context_injector.py`. Public plugins are easier to maintain when root `__init__.py` only exposes `register`.

## Config design

Replace `config.json` with plugin-local `config.yaml`.

Suggested default public `config.yaml`:

```yaml
# Path to the markdown/text file injected into Hermes conversations.
context_path: ~/.hermes/live-contexts/current.md

# Platforms where injection is enabled. Empty list means disabled.
enabled_platforms:
  - cli

# Optional sender allowlist for gateway platforms. CLI ignores this.
# Slack examples: U0APZSWQPHA or <@U0APZSWQPHA>
allowed_sender_ids: []

session_state_ttl_hours: 168
max_context_chars: 12000

# Token budget controls. The context is injected on the first eligible turn,
# then periodically; it is not injected before every response.
injection:
  inject_on_first_turn: true
  reinject_after_turns: 6
  reinject_after_minutes: 60

# Thin instruction prepended to the context file inside the injected hook context.
# This is not a real model system-role message; Hermes pre_llm_call context is injected into the user turn.
system_prompt: |
  You are reading a temporary context file injected by Hermes.
  Treat it as background context, not as a user request.
  Distinguish facts, estimates, and uncertainty.
  If it conflicts with the user's latest message, prefer the user's latest message.
  Only use it when it is naturally relevant.

wrapper:
  tag: hermes_context
```

Compatibility option:

- During migration, support old `config.json` as read-only fallback for one release, but prefer not to document it as public API.
- If both exist, `config.yaml` wins.
- Explicitly ignore legacy `state_path`; runtime state is always plugin-local `state.json`.
- Emit only debug/state information on fallback usage, not visible user messages.

Dependencies:

- YAML loading uses PyYAML (`yaml.safe_load`). Treat PyYAML as a plugin dependency and document it in README; if import fails, surface a clear plugin-load error or fail closed instead of silently ignoring `config.yaml`.

Naming choices:

- Use `context_path`, not `current_path`, for public clarity.
- Keep backward compatibility aliases in loader:
  - `current_path` -> `context_path`
  - `max_context_chars`, `enabled_platforms`, `allowed_sender_ids`, `session_state_ttl_hours` unchanged.
- `system_prompt` is acceptable because it is the user's requested term, but README should clearly state that Hermes injects it as hook context, not a literal system-role prompt.

## Implementation plan

### Phase 1 — Rename plugin safely

1. Rename directory:

   ```text
   live-context-injector -> hermes-context-injector
   ```

2. Update `plugin.yaml`:

   ```yaml
   name: hermes-context-injector
   version: 0.2.0
   description: Injects a configurable context file into Hermes conversations via pre_llm_call.
   kind: standalone
   provides_hooks:
     - pre_llm_call
   ```

3. Update `/Users/ryo.nakae/.hermes/config.yaml` plugin enablement:

   ```yaml
   plugins:
     enabled:
       - hermes-context-notifier
       - hermes-self-improvement
       - hermes-context-injector
   ```

   Remove `live-context-injector` only after the renamed plugin loads successfully.

4. Decide what to do with old runtime state:
   - current `state.json` is only session de-duplication/debug cache
   - safe to discard during rename unless continuity of duplicate-skip state matters
   - if preserving, move it into the new directory unchanged.

5. Ensure `.gitignore` covers:

   ```text
   state.json
   *.tmp.*
   __pycache__/
   .pytest_cache/
   *.py[cod]
   ```

### Phase 2 — Config loader cleanup

1. Add YAML parsing with `yaml.safe_load`.
2. Load order:
   1. `config.yaml`
   2. legacy `config.json` fallback if YAML absent
   3. public defaults
3. Resolve `context_path` consistently:
   - `~` and env vars expanded
   - absolute paths used as-is
   - relative `context_path` should resolve from the plugin directory for predictability; document this. For most users, `~` is likely the common case.
   - `state.json` is not configurable; derive it from the plugin directory in code.
4. Validate config types at boundaries:
   - `enabled_platforms`: list of strings
   - `allowed_sender_ids`: list of strings
   - `session_state_ttl_hours`: positive int
   - `max_context_chars`: positive int
   - `system_prompt`: string
   - `wrapper.tag`: safe simple tag string; fallback to `hermes_context`
5. Avoid over-engineering: no schema library needed for this small plugin.

### Phase 3 — Make wrapping configurable

Replace hard-coded `_wrap(text)` with something like:

```python
def _safe_tag(value: str) -> str:
    # Accept only simple XML-like tag names: ^[A-Za-z][A-Za-z0-9_-]{0,63}$
    # Fallback to hermes_context when invalid.
    ...

def _wrap(text: str, system_prompt: str, tag: str = "hermes_context") -> str:
    tag = _safe_tag(tag)
    prompt = system_prompt.strip()
    if prompt:
        return f"<{tag}>\n{prompt}\n\n{text}\n\n</{tag}>"
    return f"<{tag}>\n{text}\n\n</{tag}>"
```

Notes:

- Keep a safe default prompt, ideally English for public plugin defaults.
- Ryo-specific Japanese prompt can live in local `config.yaml`.
- Include tests proving `system_prompt` appears before context content.
- Include tests proving prompt changes affect the injected hash decision if desired.

Hashing decision:

- Current hash only covers context file text.
- For public behavior, hash should cover the final injected payload: `system_prompt + wrapper tag + truncated context text`.
- Hash after truncation, not the raw full context file. Otherwise, changes outside the injected/truncated slice could alter the hash without changing what the model actually receives.
- Otherwise changing `system_prompt` would not re-inject into an existing session until the context file changes.
- Plan to hash the wrapped/injectable text, or hash a JSON object containing `system_prompt`, `tag`, and truncated context text.

### Phase 3.5 — Injection cadence / token budget policy

Current behavior is not “inject on every response.” The hook fires before every LLM turn, but the plugin returns `None` when the same session has already seen the same context hash. Hermes core then injects nothing for that turn. Also, `pre_llm_call` context is API-call-time only and is not persisted to the session DB, so skipping injection saves tokens but also means that turn does not directly see the context file.

This is the right direction for token cost, but the public version should make the cadence explicit instead of only relying on hash de-duplication.

Recommended policy:

1. Define an “eligible turn” as a hook invocation where platform/sender gating passed and the context file was readable. Disabled platforms, sender mismatches, missing files, and read errors do not advance cadence counters.
2. Always inject on the first eligible turn of a session.
3. Do not inject before every response.
4. Re-inject when one of these cadence thresholds is met:
   - `eligible_turns_since_injection >= reinject_after_turns`, or
   - `minutes_since_last_injection >= reinject_after_minutes`.
5. In v1, a changed context hash does **not** bypass cadence by itself. It will be picked up on the next cadence-triggered injection. This avoids spam when upstream context files are regenerated frequently. README must state that file updates are not guaranteed to appear immediately.
6. Keep `state.json` plugin-local and store minimal per-session metadata:
   - `last_injected_hash`
   - `last_injected_at`
   - `eligible_turns_since_injection`
   - `platform`

Suggested public config keys:

```yaml
injection:
  inject_on_first_turn: true
  reinject_after_turns: 6
  reinject_after_minutes: 60
```

Default recommendation:

- `inject_on_first_turn: true`
- `reinject_after_turns: 6`
- `reinject_after_minutes: 60`

Rationale:

- Every-turn injection is wasteful because the context file can be up to `max_context_chars`.
- Hash-only injection is cheap, but can be too sparse because hook context is ephemeral and not persisted.
- A turn/time cadence gives predictable token cost and keeps long conversations from drifting too far away from the current context.
- The policy remains simple enough for a public plugin; avoid adding many modes unless real users need them.

Tests to add:

- first eligible turn injects
- disabled platform / sender mismatch / missing file does not advance cadence counter
- immediate second turn with same hash skips
- same hash re-injects after `reinject_after_turns`
- same hash re-injects after `reinject_after_minutes`
- changed hash before cadence threshold skips; changed content is injected at the next cadence threshold
- state records `eligible_turns_since_injection` and `last_injected_at`
- legacy `config.json` `state_path` is ignored
- malformed YAML and invalid numeric config fail safe
- wrapper tag validation falls back to `hermes_context`

### Phase 4 — Tests first, then implementation

Add/adjust tests before implementation:

1. `test_loads_config_yaml`
   - creates `config.yaml`
   - verifies `context_path` and `system_prompt` are honored.
2. `test_config_yaml_wins_over_legacy_json`
   - if both files exist, YAML wins.
3. `test_legacy_config_json_still_loads_when_yaml_missing`
   - optional migration compatibility.
4. `test_system_prompt_is_prepended_to_context`
   - hook result contains prompt before file content.
5. `test_system_prompt_change_reinjects_same_session`
   - proves dedupe hash includes prompt/config content.
6. `test_legacy_state_path_is_ignored`
   - proves old `config.json` cannot move runtime state out of plugin-local `state.json`.
7. `test_state_path_is_plugin_local_and_ignored`
   - proves runtime state is written to plugin-local `state.json` and is not controlled by public `config.yaml`; use internal test-only dependency injection so tests never touch real runtime state.
8. `test_wrapper_tag_validation_fallback`
   - invalid tags fall back to `hermes_context`.
9. `test_injection_cadence_first_skip_turns_time`
   - covers first eligible turn, immediate skip, turn threshold, minute threshold, and changed-hash-before-cadence behavior.
10. Update existing tests for renamed module and new config names.
11. Keep current tests for platform/sender/truncation/TTL behavior.

Commands:

```bash
cd /Users/ryo.nakae/.hermes/plugins/hermes-context-injector
python -m pytest -q
python -m py_compile __init__.py context_injector.py tests/*.py
```

### Phase 5 — Plugin discovery smoke

From Hermes source checkout:

```bash
cd /Users/ryo.nakae/.hermes/hermes-agent
python - <<'PY'
from hermes_cli.plugins import PluginManager
pm = PluginManager()
pm.discover_and_load(force=True)
loaded = pm._plugins.get('hermes-context-injector')
print('found=', bool(loaded))
print('enabled=', getattr(loaded, 'enabled', None))
print('error=', getattr(loaded, 'error', None))
print('hooks=', sorted(getattr(loaded, 'hooks_registered', []) or []))
print('pre_llm_callbacks=', pm._hooks.get('pre_llm_call'))
PY
```

Expected:

- `found=True`
- `enabled=True`
- `error=None`
- `pre_llm_call` callback includes the plugin hook

Caveat from current inspection: `list_plugins()` may report `hooks: 0` even while `pm._hooks['pre_llm_call']` contains the hook. For verification, inspect `_hooks` too, not only the count.

### Phase 6 — Documentation for public readiness

Add `README.md` with:

- What it does
- How injection works
- Important note: `pre_llm_call` injects into the user turn, not a real system-role prompt
- Installation:
  - copy/clone to `~/.hermes/plugins/hermes-context-injector`
  - add to `plugins.enabled`
  - restart gateway or start a new Hermes process/session
- Config reference for `config.yaml`
- Example config for local live context
- Runtime behavior:
  - de-dupes per session by hash and cadence
  - context is ephemeral and not persisted to session DB by Hermes hook contract
  - skipped turns do not directly see the context file
  - file updates are picked up on the next cadence-triggered injection, not necessarily immediately
  - truncates long context while preserving `## 詳細参照`
- Development/test commands
- Limitations:
  - not a memory system
  - not a context engine/compressor
  - does not fetch context; it only reads a local file

Add `AGENTS.md` with:

- Files to inspect first
- Test commands
- Hook contract caveat
- Do not change Hermes core for ordinary plugin changes
- Runtime files are ignored
- Gateway/new session restart note

Add `LICENSE` if publishing. If MIT, use current year 2026 and the intended copyright holder.

### Phase 7 — Local migration and smoke

Use a disable → rename → enable migration. This avoids double injection and makes failure modes obvious.

1. Disable the current plugin first:
   - remove `live-context-injector` from `/Users/ryo.nakae/.hermes/config.yaml` `plugins.enabled`, or move it to `plugins.disabled` if we want an explicit disabled record.
   - verify YAML parses.
2. Restart/start a fresh Hermes process only if needed to verify the disabled state; for the Slack gateway, avoid restart mid-task unless activation testing is explicitly requested.
3. Rename the plugin directory and metadata:
   - `live-context-injector` → `hermes-context-injector`
   - update `plugin.yaml` `name: hermes-context-injector`
   - update tests/module names/docs accordingly.
4. Run tests and py_compile.
5. Enable only the new plugin name:
   - add `hermes-context-injector` to `plugins.enabled`
   - do not leave `live-context-injector` enabled at the same time.
6. Run plugin discovery smoke and verify `plugin.yaml` name, directory name, and `plugins.enabled` key match.
7. Trigger a safe synthetic hook call if useful:

   ```bash
   hermes hooks test pre_llm_call --payload-file /tmp/context-injector-payload.json
   ```

   Or directly instantiate `make_hook()` in a small Python smoke.

8. Restart gateway only after tests/discovery pass and only when we are ready to activate the renamed plugin in the running Slack gateway.

## Files likely to change

Inside plugin directory:

- `plugin.yaml`
- `__init__.py`
- new `context_injector.py` if splitting implementation
- `config.json` -> replace with `config.yaml` or leave as legacy sample only during migration
- `tests/test_live_context_injector.py` -> rename/update to `tests/test_context_injector.py`
- `.gitignore`
- new `README.md`
- new `AGENTS.md`
- optional `LICENSE`

Outside plugin directory:

- `/Users/ryo.nakae/.hermes/config.yaml`
  - update `plugins.enabled` from `live-context-injector` to `hermes-context-injector`

No Hermes core changes should be needed.

## Risks and tradeoffs

1. **System prompt terminology**
   - User-facing requirement says “system prompt”. Hermes hook contract says injected context is user-message-side only.
   - Recommended compromise: expose `system_prompt` config, but document that it is injected as the instruction/header text inside hook context.

2. **Prompt cache**
   - Avoid actual system prompt changes; preserving `pre_llm_call` context injection keeps prompt cache behavior stable.

3. **Path portability**
   - Remove Ryo-specific absolute defaults from code.
   - Local Ryo config can keep `~/.hermes/live-contexts/current.md`.

4. **Rename activation race**
   - If `plugins.enabled` is switched before the new directory loads, injection stops.
   - Verify discovery before removing the old enabled name.

5. **State migration**
   - Current `state.json` is not important durable state. If carried over, it may contain old debug/session IDs but no sensitive content unless debug fields expand later.

6. **Public plugin repo hygiene**
   - The directory was not a git repository at initial inspection.
   - It has now been initialized with `git init` and the branch was renamed to `main`.
   - Current git status is an initial repo with source files and the plan file untracked; runtime files remain ignored.
   - `.gitignore` ignores `state.json`, temp files, Python caches, and pytest cache.
   - `.hermes/plans/` is intentionally tracked so implementation planning is visible in git history. It can be deleted later if we want a cleaner public tree.
   - Before public release, add README/AGENTS/LICENSE and avoid committing runtime `state.json`, caches, or Ryo-specific local config.

## State file placement decision

Decision: keep `state.json` fixed in the plugin directory and do not make `state_path` configurable.

Resolved path after rename:

```text
~/.hermes/plugins/hermes-context-injector/state.json
```

Rationale:

- This matches the existing live-context-injector behavior.
- This environment already standardized injector state under the plugin directory and removed the old `~/.hermes/state/` path to avoid legacy confusion.
- The state is small, disposable session de-dup/debug cache, not durable user data.
- Making `state_path` configurable adds implementation and documentation complexity without much value for this plugin.
- Public repo safety is handled by `.gitignore`, not by moving state outside the plugin.

Implementation rule:

- Always derive state path from `Path(__file__).with_name("state.json")` or an equivalent plugin-directory helper.
- Do not include `state_path` in public `config.yaml`.
- Keep `state.json` in `.gitignore`.
- If a future packaged/read-only install needs external runtime storage, handle it as a later feature, not in this first public-polish slice.

## Open questions before implementation

1. Should default `system_prompt` be English for public release, while Ryo's local config uses Japanese?
   - Recommendation: yes.
2. Should `config.json` compatibility be kept?
   - Recommendation: support as fallback for one migration, but document only `config.yaml`.
3. Should the wrapper tag be fixed or configurable?
   - Recommendation: configurable with default `hermes_context`, but keep validation simple.
4. Should state be discarded on rename?
   - Recommendation: yes, unless preserving duplicate-skip continuity matters.
5. Which license/copyright holder for public release?
   - Need Ryo's choice before adding `LICENSE`.

## Recommended first implementation slice

Do this in a small, testable slice; keep rename/migration until after behavior tests pass:

1. Add failing tests for YAML config, configurable prompt, fixed plugin-local state, wrapper tag validation, final-payload hash, and cadence.
2. Implement YAML config loader with JSON fallback, while explicitly ignoring legacy `state_path`.
3. Add internal test-only dependency injection for `plugin_dir` / `state_path` / clock so tests never touch real runtime state; do not expose `state_path` in public config.
4. Implement configurable wrapper/system_prompt and safe tag validation.
5. Hash the final injected payload after truncation.
6. Implement minimal cadence state and decision rule.
7. Update tests and run pytest/py_compile.
8. Add README/AGENTS/LICENSE before calling it public-ready.
9. Rename plugin directory/metadata and update `plugins.enabled` only after discovery smoke passes; never dual-enable old and new plugin names.

Stop before gateway restart unless activation in the running Slack gateway is explicitly requested.
