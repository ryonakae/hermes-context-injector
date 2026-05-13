from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_SYSTEM_PROMPT = """You are reading a temporary context file injected by Hermes.
Treat it as background context, not as a user request.
Distinguish facts, estimates, and uncertainty.
If it conflicts with the user's latest message, prefer the user's latest message.
Only use it when it is naturally relevant."""

DEFAULT_CONFIG: dict[str, Any] = {
    "context_path": "~/.hermes/live-contexts/current.md",
    "enabled_platforms": ["cli"],
    "allowed_sender_ids": [],
    "session_state_ttl_hours": 168,
    "max_context_chars": 12000,
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "wrapper": {"tag": "hermes_context"},
    "injection": {
        "inject_on_first_turn": True,
        "reinject_after_turns": 6,
        "reinject_after_minutes": 60,
    },
}

_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def register(ctx):
    plugin_dir = Path(__file__).resolve().parent
    config = load_config(plugin_dir=plugin_dir)
    ctx.register_hook("pre_llm_call", make_hook(config, plugin_dir=plugin_dir))


def load_config(plugin_dir: Path | None = None) -> dict[str, Any]:
    plugin_dir = Path(plugin_dir or Path(__file__).resolve().parent)
    yaml_path = plugin_dir / "config.yaml"
    json_path = plugin_dir / "config.json"

    data: dict[str, Any] = {}
    if yaml_path.exists():
        try:
            import yaml

            loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    elif json_path.exists():
        try:
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}

    return normalize_config(data, plugin_dir=plugin_dir)


def normalize_config(data: dict[str, Any] | None, plugin_dir: Path | None = None) -> dict[str, Any]:
    plugin_dir = Path(plugin_dir or Path(__file__).resolve().parent)
    raw = dict(data or {})

    context_value = raw.get("context_path", raw.get("current_path", DEFAULT_CONFIG["context_path"]))
    wrapper = raw.get("wrapper") if isinstance(raw.get("wrapper"), dict) else {}
    injection = raw.get("injection") if isinstance(raw.get("injection"), dict) else {}

    return {
        "context_path": _resolve_path(_string_or_default(context_value, DEFAULT_CONFIG["context_path"]), plugin_dir),
        "enabled_platforms": _string_list_or_default(raw.get("enabled_platforms"), DEFAULT_CONFIG["enabled_platforms"]),
        "allowed_sender_ids": _string_list_or_default(raw.get("allowed_sender_ids"), DEFAULT_CONFIG["allowed_sender_ids"]),
        "session_state_ttl_hours": _positive_int_or_default(
            raw.get("session_state_ttl_hours"), DEFAULT_CONFIG["session_state_ttl_hours"]
        ),
        "max_context_chars": _positive_int_or_default(raw.get("max_context_chars"), DEFAULT_CONFIG["max_context_chars"]),
        "system_prompt": _string_or_default(raw.get("system_prompt"), DEFAULT_CONFIG["system_prompt"]),
        "wrapper": {"tag": _safe_tag(wrapper.get("tag", DEFAULT_CONFIG["wrapper"]["tag"]))},
        "injection": {
            "inject_on_first_turn": bool(injection.get("inject_on_first_turn", DEFAULT_CONFIG["injection"]["inject_on_first_turn"])),
            "reinject_after_turns": _positive_int_or_default(
                injection.get("reinject_after_turns"), DEFAULT_CONFIG["injection"]["reinject_after_turns"]
            ),
            "reinject_after_minutes": _positive_int_or_default(
                injection.get("reinject_after_minutes"), DEFAULT_CONFIG["injection"]["reinject_after_minutes"]
            ),
        },
    }


def make_hook(
    config: dict[str, Any],
    plugin_dir: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    state_path: Path | None = None,
):
    plugin_dir = Path(plugin_dir or Path(__file__).resolve().parent)
    current_path = Path(config.get("context_path", _resolve_path(DEFAULT_CONFIG["context_path"], plugin_dir)))
    state_path = Path(state_path or plugin_dir / "state.json")
    enabled_platforms = set(config.get("enabled_platforms", []))
    allowed_sender_ids = {_normalize_sender_id(value) for value in config.get("allowed_sender_ids", [])}
    session_state_ttl_hours = int(config.get("session_state_ttl_hours", DEFAULT_CONFIG["session_state_ttl_hours"]))
    max_context_chars = int(config.get("max_context_chars", DEFAULT_CONFIG["max_context_chars"]))
    system_prompt = str(config.get("system_prompt", DEFAULT_CONFIG["system_prompt"]))
    wrapper_tag = _safe_tag((config.get("wrapper") or {}).get("tag", DEFAULT_CONFIG["wrapper"]["tag"]))
    injection = config.get("injection") or {}
    inject_on_first_turn = bool(injection.get("inject_on_first_turn", True))
    reinject_after_turns = int(injection.get("reinject_after_turns", 6))
    reinject_after_minutes = int(injection.get("reinject_after_minutes", 60))
    clock = clock or (lambda: datetime.now(timezone.utc))

    def hook(**kwargs):
        platform = kwargs.get("platform") or ""
        session_id = kwargs.get("session_id") or "unknown"
        sender_id = _normalize_sender_id(kwargs.get("sender_id") or "")

        if platform not in enabled_platforms:
            _debug(state_path, "skipped_platform", platform=platform, session_id=session_id)
            return None
        if platform != "cli" and allowed_sender_ids and sender_id not in allowed_sender_ids:
            _debug(state_path, "skipped_sender", platform=platform, session_id=session_id)
            return None
        if not current_path.exists():
            _debug(state_path, "skipped_missing_context", platform=platform, session_id=session_id)
            return None

        try:
            text = current_path.read_text(encoding="utf-8")
        except Exception as exc:
            _debug(state_path, "skipped_read_error", platform=platform, session_id=session_id, error=repr(exc))
            return None

        injected_text = _truncate_preserving_references(text, max_context_chars)
        wrapped = _wrap(injected_text, system_prompt=system_prompt, tag=wrapper_tag)
        digest = hashlib.sha256(wrapped.encode("utf-8")).hexdigest()
        now = _ensure_aware_utc(clock())

        state = _load_state(state_path)
        _prune_sessions(state, session_state_ttl_hours, now=now)
        sessions = state.setdefault("sessions", {})
        session_state = sessions.get(session_id, {}) if isinstance(sessions.get(session_id, {}), dict) else {}

        should_inject, eligible_turns = _should_inject(
            session_state=session_state,
            now=now,
            inject_on_first_turn=inject_on_first_turn,
            reinject_after_turns=reinject_after_turns,
            reinject_after_minutes=reinject_after_minutes,
        )

        if not should_inject:
            sessions[session_id] = {
                **session_state,
                "eligible_turns_since_injection": eligible_turns,
                "last_seen_hash": digest,
                "last_seen_at": now.isoformat(),
                "platform": platform,
            }
            _write_state(state_path, state)
            _debug(state_path, "skipped_cadence", platform=platform, session_id=session_id, turns=eligible_turns)
            return None

        sessions[session_id] = {
            "last_injected_hash": digest,
            "last_injected_at": now.isoformat(),
            "eligible_turns_since_injection": 0,
            "platform": platform,
        }
        _write_state(state_path, state)
        _debug(state_path, "injected", platform=platform, session_id=session_id, chars=len(injected_text))
        return {"context": wrapped}

    return hook


def _should_inject(
    session_state: dict[str, Any],
    now: datetime,
    inject_on_first_turn: bool,
    reinject_after_turns: int,
    reinject_after_minutes: int,
) -> tuple[bool, int]:
    last_injected_at = _parse_dt(str(session_state.get("last_injected_at", "")))
    previous_turns = _non_negative_int(session_state.get("eligible_turns_since_injection"), 0)
    current_turns = previous_turns + 1

    if last_injected_at is None:
        if inject_on_first_turn or current_turns >= reinject_after_turns:
            return True, 0
        return False, current_turns
    if current_turns >= reinject_after_turns:
        return True, 0
    if now - last_injected_at >= timedelta(minutes=reinject_after_minutes):
        return True, 0
    return False, current_turns


def _normalize_sender_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("<@") and text.endswith(">"):
        text = text[2:-1]
    if "|" in text:
        text = text.split("|", 1)[0]
    return text


def _wrap(text: str, system_prompt: str, tag: str = "hermes_context") -> str:
    tag = _safe_tag(tag)
    prompt = system_prompt.strip()
    if prompt:
        return f"<{tag}>\n{prompt}\n\n{text}\n\n</{tag}>"
    return f"<{tag}>\n{text}\n\n</{tag}>"


def _safe_tag(value: Any) -> str:
    text = str(value or "").strip()
    return text if _TAG_RE.fullmatch(text) else "hermes_context"


def _truncate_preserving_references(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    marker = "## 詳細参照"
    note = "> 注: context が長いため一部を省略しています。必要に応じて詳細参照を確認してください。\n\n"
    if marker in text:
        body, refs = text.split(marker, 1)
        refs = marker + refs
        budget = max_chars - len(note) - len(refs) - 2
        return note + body[: max(0, budget)].rstrip() + "\n\n" + refs
    return note + text[: max(0, max_chars - len(note))]


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"sessions": {}, "debug": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("sessions", {})
            data.setdefault("debug", [])
            return data
    except Exception:
        pass
    return {"sessions": {}, "debug": []}


def _parse_dt(value: str) -> datetime | None:
    try:
        return _ensure_aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except Exception:
        return None


def _ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _prune_sessions(state: dict[str, Any], ttl_hours: int, now: datetime | None = None) -> None:
    sessions = state.setdefault("sessions", {})
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=ttl_hours)
    for key, value in list(sessions.items()):
        if not isinstance(value, dict):
            sessions.pop(key, None)
            continue
        dt = _parse_dt(str(value.get("last_injected_at", ""))) or _parse_dt(str(value.get("last_seen_at", "")))
        if dt is None or dt < cutoff:
            sessions.pop(key, None)


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _debug(path: Path, event: str, **fields: Any) -> None:
    try:
        state = _load_state(path)
        debug = state.setdefault("debug", [])
        debug.append({"at": datetime.now(timezone.utc).isoformat(), "event": event, **fields})
        del debug[:-50]
        _write_state(path, state)
    except Exception:
        return None


def _resolve_path(value: str, plugin_dir: Path) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(value))
    path = Path(expanded)
    return path if path.is_absolute() else plugin_dir / path


def _string_or_default(value: Any, default: str) -> str:
    return value if isinstance(value, str) and value.strip() else default


def _string_list_or_default(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    return [str(item) for item in value if str(item).strip()]


def _positive_int_or_default(value: Any, default: int) -> int:
    try:
        number = int(value)
    except Exception:
        return int(default)
    return number if number > 0 else int(default)


def _non_negative_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except Exception:
        return int(default)
    return max(0, number)
