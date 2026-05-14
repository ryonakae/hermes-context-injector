import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load_plugin():
    path = Path(__file__).resolve().parents[1] / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "hermes_context_injector_under_test",
        path,
        submodule_search_locations=[str(path.parent)],
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_config(plugin_dir: Path, text: str) -> None:
    (plugin_dir / "config.yaml").write_text(text, encoding="utf-8")


def write_json_config(plugin_dir: Path, data: dict) -> None:
    (plugin_dir / "config.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def now_at(value: datetime):
    def _clock():
        return value

    return _clock


def current_text():
    return "# Hermes context\n\nbody\n\n## 詳細参照\n- weather.md\n"


def make_config(plugin, plugin_dir: Path, context_path: Path, **overrides):
    base = {
        "context_path": str(context_path),
        "platforms": {"cli": {"enabled": True}},
        "session_state_ttl_hours": 168,
        "max_context_chars": 12000,
        "system_prompt": "Use this context carefully.",
        "wrapper": {"tag": "hermes_context"},
        "injection": {
            "inject_on_first_turn": True,
            "reinject_after_turns": 6,
            "reinject_after_minutes": 60,
        },
    }
    base.update(overrides)
    return plugin.normalize_config(base, plugin_dir=plugin_dir)


def test_loads_config_yaml_and_wraps_configured_prompt(tmp_path):
    plugin = load_plugin()
    current = tmp_path / "current.md"
    current.write_text(current_text(), encoding="utf-8")
    write_config(
        tmp_path,
        f"""
context_path: {current}
enabled_platforms: [cli]
system_prompt: |
  You are reading a context file.
  Use it only when relevant.
wrapper:
  tag: custom_context
injection:
  reinject_after_turns: 3
  reinject_after_minutes: 30
""",
    )

    cfg = plugin.load_config(plugin_dir=tmp_path)
    hook = plugin.make_hook(cfg, plugin_dir=tmp_path, clock=now_at(datetime(2026, 1, 1, tzinfo=timezone.utc)))
    result = hook(platform="cli", session_id="s1", sender_id="")

    assert result
    context = result["context"]
    assert context.startswith("<custom_context>\nYou are reading a context file.")
    assert context.index("Use it only when relevant.") < context.index("# Hermes context")
    assert context.endswith("</custom_context>")
    assert (tmp_path / "state.json").exists()


def test_config_yaml_wins_over_legacy_json_and_legacy_state_path_is_ignored(tmp_path):
    plugin = load_plugin()
    json_current = tmp_path / "json-current.md"
    yaml_current = tmp_path / "yaml-current.md"
    forbidden_state = tmp_path / "forbidden-state.json"
    json_current.write_text("json body", encoding="utf-8")
    yaml_current.write_text("yaml body", encoding="utf-8")
    write_json_config(
        tmp_path,
        {
            "current_path": str(json_current),
            "state_path": str(forbidden_state),
            "enabled_platforms": ["cli"],
        },
    )
    write_config(
        tmp_path,
        f"""
context_path: {yaml_current}
enabled_platforms: [cli]
system_prompt: YAML prompt
""",
    )

    cfg = plugin.load_config(plugin_dir=tmp_path)
    hook = plugin.make_hook(cfg, plugin_dir=tmp_path, clock=now_at(datetime(2026, 1, 1, tzinfo=timezone.utc)))
    result = hook(platform="cli", session_id="s1", sender_id="")

    assert result
    assert "yaml body" in result["context"]
    assert "json body" not in result["context"]
    assert (tmp_path / "state.json").exists()
    assert not forbidden_state.exists()


def test_legacy_config_json_loads_but_state_path_is_ignored(tmp_path):
    plugin = load_plugin()
    current = tmp_path / "current.md"
    forbidden_state = tmp_path / "legacy-state.json"
    current.write_text(current_text(), encoding="utf-8")
    write_json_config(
        tmp_path,
        {
            "current_path": str(current),
            "state_path": str(forbidden_state),
            "enabled_platforms": ["cli"],
            "max_context_chars": 5000,
        },
    )

    cfg = plugin.load_config(plugin_dir=tmp_path)
    hook = plugin.make_hook(cfg, plugin_dir=tmp_path, clock=now_at(datetime(2026, 1, 1, tzinfo=timezone.utc)))
    result = hook(platform="cli", session_id="s1", sender_id="")

    assert result
    assert "# Hermes context" in result["context"]
    assert (tmp_path / "state.json").exists()
    assert not forbidden_state.exists()


def test_platform_disabled_and_sender_mismatch_do_not_advance_cadence(tmp_path):
    plugin = load_plugin()
    current = tmp_path / "current.md"
    current.write_text(current_text(), encoding="utf-8")
    cfg = make_config(plugin, tmp_path, current, platforms={"slack": {"enabled": True, "allowed_sender_ids": ["U123EXAMPLE"]}})
    hook = plugin.make_hook(cfg, plugin_dir=tmp_path, clock=now_at(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    assert hook(platform="cli", session_id="s1", sender_id="") is None
    assert hook(platform="slack", session_id="s1", sender_id="OTHER") is None

    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state.get("sessions", {}) == {}


def test_normalizes_platform_access_for_all_builtin_platform_names(tmp_path):
    plugin = load_plugin()

    cfg = plugin.normalize_config(
        {
            "platforms": {
                "CLI": True,
                "slack": {"enabled": True, "allowed_sender_ids": ["<@U123EXAMPLE>"]},
                "api-server": {"enabled": False},
                "msgraph-webhook": {"enabled": True, "allowed_sender_ids": ["user@example.com"]},
            }
        },
        plugin_dir=tmp_path,
    )

    assert set(plugin.SUPPORTED_PLATFORM_KEYS) >= {
        "cli",
        "cron",
        "local",
        "telegram",
        "discord",
        "whatsapp",
        "slack",
        "signal",
        "mattermost",
        "matrix",
        "homeassistant",
        "email",
        "sms",
        "dingtalk",
        "api_server",
        "webhook",
        "msgraph_webhook",
        "feishu",
        "wecom",
        "wecom_callback",
        "weixin",
        "bluebubbles",
        "qqbot",
        "yuanbao",
    }
    assert cfg["platforms"]["cli"]["enabled"] is True
    assert cfg["platforms"]["slack"]["allowed_sender_ids"] == ["U123EXAMPLE"]
    assert cfg["platforms"]["api_server"]["enabled"] is False
    assert cfg["platforms"]["msgraph_webhook"]["allowed_sender_ids"] == ["user@example.com"]


def test_sender_allowlist_is_scoped_per_platform(tmp_path):
    plugin = load_plugin()
    current = tmp_path / "current.md"
    current.write_text(current_text(), encoding="utf-8")
    cfg = make_config(
        plugin,
        tmp_path,
        current,
        platforms={
            "slack": {"enabled": True, "allowed_sender_ids": ["U123EXAMPLE"]},
            "discord": {"enabled": True, "allowed_sender_ids": ["D123EXAMPLE"]},
        },
    )
    hook = plugin.make_hook(cfg, plugin_dir=tmp_path, clock=now_at(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    assert hook(platform="slack", session_id="s1", sender_id="<@U123EXAMPLE>")
    assert hook(platform="discord", session_id="s2", sender_id="U123EXAMPLE") is None
    assert hook(platform="discord", session_id="s3", sender_id="D123EXAMPLE")


def test_legacy_enabled_platforms_and_flat_sender_allowlist_still_work(tmp_path):
    plugin = load_plugin()
    current = tmp_path / "current.md"
    current.write_text(current_text(), encoding="utf-8")
    cfg = plugin.normalize_config(
        {
            "context_path": str(current),
            "enabled_platforms": ["slack", "api-server"],
            "allowed_sender_ids": ["U123EXAMPLE"],
        },
        plugin_dir=tmp_path,
    )

    assert cfg["platforms"]["slack"] == {"enabled": True, "allowed_sender_ids": ["U123EXAMPLE"]}
    assert cfg["platforms"]["api_server"] == {"enabled": True, "allowed_sender_ids": ["U123EXAMPLE"]}


def test_cadence_first_turn_skip_turn_threshold_time_threshold_and_changed_hash(tmp_path):
    plugin = load_plugin()
    current = tmp_path / "current.md"
    current.write_text("first body", encoding="utf-8")
    start = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    cfg = make_config(
        plugin,
        tmp_path,
        current,
        injection={"inject_on_first_turn": True, "reinject_after_turns": 3, "reinject_after_minutes": 60},
    )
    current_time = {"value": start}
    hook = plugin.make_hook(cfg, plugin_dir=tmp_path, clock=lambda: current_time["value"])

    first = hook(platform="cli", session_id="s1", sender_id="")
    assert first and "first body" in first["context"]

    assert hook(platform="cli", session_id="s1", sender_id="") is None
    current.write_text("changed body", encoding="utf-8")
    assert hook(platform="cli", session_id="s1", sender_id="") is None

    by_turn = hook(platform="cli", session_id="s1", sender_id="")
    assert by_turn and "changed body" in by_turn["context"]

    assert hook(platform="cli", session_id="s1", sender_id="") is None
    current_time["value"] = start + timedelta(minutes=61)
    by_time = hook(platform="cli", session_id="s1", sender_id="")
    assert by_time and "changed body" in by_time["context"]

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["sessions"]["s1"]["eligible_turns_since_injection"] == 0
    assert state["sessions"]["s1"]["last_injected_at"] == current_time["value"].isoformat()


def test_inject_on_first_turn_false_accumulates_eligible_turns_until_threshold(tmp_path):
    plugin = load_plugin()
    current = tmp_path / "current.md"
    current.write_text("body", encoding="utf-8")
    cfg = make_config(
        plugin,
        tmp_path,
        current,
        injection={"inject_on_first_turn": False, "reinject_after_turns": 3, "reinject_after_minutes": 60},
    )
    hook = plugin.make_hook(cfg, plugin_dir=tmp_path, clock=now_at(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    assert hook(platform="cli", session_id="s1", sender_id="") is None
    assert hook(platform="cli", session_id="s1", sender_id="") is None
    third = hook(platform="cli", session_id="s1", sender_id="")

    assert third and "body" in third["context"]


def test_wrapper_tag_validation_falls_back_to_safe_default(tmp_path):
    plugin = load_plugin()
    current = tmp_path / "current.md"
    current.write_text("body", encoding="utf-8")
    cfg = make_config(plugin, tmp_path, current, wrapper={"tag": "bad tag><script"})
    hook = plugin.make_hook(cfg, plugin_dir=tmp_path, clock=now_at(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    result = hook(platform="cli", session_id="s1", sender_id="")

    assert result
    assert result["context"].startswith("<hermes_context>")
    assert result["context"].endswith("</hermes_context>")


def test_hash_uses_truncated_injected_payload_not_raw_tail(tmp_path):
    plugin = load_plugin()
    current = tmp_path / "current.md"
    current.write_text("visible\n" + "x" * 500, encoding="utf-8")
    cfg = make_config(plugin, tmp_path, current, max_context_chars=80, injection={"inject_on_first_turn": True, "reinject_after_turns": 1, "reinject_after_minutes": 60})
    hook = plugin.make_hook(cfg, plugin_dir=tmp_path, clock=now_at(datetime(2026, 1, 1, tzinfo=timezone.utc)))

    assert hook(platform="cli", session_id="s1", sender_id="") is not None
    first_hash = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["sessions"]["s1"]["last_injected_hash"]
    current.write_text("visible\n" + "x" * 499 + "y", encoding="utf-8")
    assert hook(platform="cli", session_id="s1", sender_id="") is not None
    second_hash = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["sessions"]["s1"]["last_injected_hash"]

    assert second_hash == first_hash


def test_truncates_but_preserves_references(tmp_path):
    plugin = load_plugin()
    current = tmp_path / "current.md"
    current.write_text("# Hermes\n" + "x" * 500 + "\n\n## 詳細参照\n- weather.md\n", encoding="utf-8")
    cfg = make_config(plugin, tmp_path, current, max_context_chars=120)
    hook = plugin.make_hook(cfg, plugin_dir=tmp_path, clock=now_at(datetime(2026, 1, 1, tzinfo=timezone.utc)))
    result = hook(platform="cli", session_id="s1", sender_id="")
    assert "## 詳細参照" in result["context"]
    assert "weather.md" in result["context"]
    assert "省略" in result["context"]


def test_prunes_old_sessions_with_new_state_shape(tmp_path):
    plugin = load_plugin()
    current = tmp_path / "current.md"
    current.write_text(current_text(), encoding="utf-8")
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"sessions": {"old": {"last_injected_at": "2000-01-01T00:00:00+00:00"}}}),
        encoding="utf-8",
    )
    cfg = make_config(plugin, tmp_path, current)
    hook = plugin.make_hook(cfg, plugin_dir=tmp_path, clock=now_at(datetime(2026, 1, 1, tzinfo=timezone.utc)))
    hook(platform="cli", session_id="new", sender_id="")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "old" not in state["sessions"]
    assert "new" in state["sessions"]


def test_malformed_yaml_and_invalid_numbers_fail_safe(tmp_path):
    plugin = load_plugin()
    (tmp_path / "config.yaml").write_text("max_context_chars: [not, an, int", encoding="utf-8")

    cfg = plugin.load_config(plugin_dir=tmp_path)

    assert cfg["platforms"]["cli"]["enabled"] is True
    assert cfg["max_context_chars"] == 12000
    assert cfg["injection"]["reinject_after_turns"] == 6
