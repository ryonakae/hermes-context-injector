# AGENTS.md

This repository is a Hermes Agent standalone plugin. Start with these files:

- `plugin.yaml` — plugin metadata and hook declaration
- `__init__.py` — thin plugin loader/export surface
- `context_injector.py` — hook implementation, config loading, cadence, state handling
- `config.example.yaml` — tracked example configuration; copy it to ignored `config.yaml` for local runtime settings
- `tests/test_context_injector.py` — behavior tests
- `.hermes/plans/` — implementation plans and review notes

## Commands

Run from the plugin root:

```bash
python -m pytest -q
python -m py_compile __init__.py context_injector.py tests/*.py
```

Plugin discovery smoke from the Hermes Agent checkout:

```bash
cd ~/.hermes/hermes-agent
python - <<'PY'
from hermes_cli.plugins import PluginManager
pm = PluginManager()
pm.discover_and_load(force=True)
loaded = pm._plugins.get('hermes-context-injector')
print('found=', bool(loaded))
print('enabled=', getattr(loaded, 'enabled', None))
print('error=', getattr(loaded, 'error', None))
print('pre_llm_callbacks=', pm._hooks.get('pre_llm_call'))
PY
```

## Development rules

- Do not change Hermes core for ordinary plugin behavior.
- Keep the mechanism as a `pre_llm_call` hook returning `{"context": ...}`.
- Hook context is injected into the current user turn at API-call time only; it is not a real system-role prompt and is not persisted to the session DB.
- `state.json` is fixed to the plugin directory and must remain ignored by git.
- Do not expose `state_path` as public config unless a real read-only/package-install requirement appears later.
- Use TDD for behavior changes. Verify RED before implementing and run the full plugin test command after GREEN.
- Keep `README.md` user-facing and this file agent-facing; do not duplicate long explanations unnecessarily.

## Config and runtime notes

- Public example config file: `config.example.yaml`; local runtime config file: ignored `config.yaml`.
- `context_path` may point at any local markdown/text file. Relative paths are resolved from the plugin directory.
- Legacy `config.json` may be read as a migration fallback, but local `config.yaml` wins.
- Legacy `state_path` must be ignored.
- `system_prompt` is plugin terminology for the instruction prepended inside the fixed `<hermes_context>` block, not a Hermes/model system message.
- `wrapper.tag` is intentionally not configurable; keep `<hermes_context>` fixed.
- Platform access lives under `platforms.<platform>.enabled` and `platforms.<platform>.allowed_sender_ids`.
- Keep `config.example.yaml` aligned with user-facing Hermes platform keys: `cli`, `cron`, `telegram`, `discord`, `whatsapp`, `slack`, `signal`, `mattermost`, `matrix`, `homeassistant`, `email`, `sms`, `dingtalk`, `api_server`, `webhook`, `msgraph_webhook`, `feishu`, `wecom`, `wecom_callback`, `weixin`, `bluebubbles`, `qqbot`, `yuanbao`.
- `cli` is ordinary interactive Hermes CLI usage; `cron` is Hermes scheduled-job execution. Do not include Hermes' internal `local` session source in public examples unless a concrete user-facing need appears.
- `allowed_sender_ids` is platform-scoped. Empty means all senders on that enabled platform; non-empty means the hook `sender_id` must match one of the configured IDs. CLI currently has no Hermes sender ID, so `platforms.cli.allowed_sender_ids` should stay empty.
- Legacy `enabled_platforms` and flat `allowed_sender_ids` are migration fallback only; do not document them as the preferred config shape.
- Cadence defaults should avoid every-turn injection: first eligible turn, then every configured number of eligible turns or minutes.
