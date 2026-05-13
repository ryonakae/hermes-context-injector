from __future__ import annotations

try:
    from .context_injector import SUPPORTED_PLATFORM_KEYS, load_config, make_hook, normalize_config, register
except ImportError:
    from context_injector import SUPPORTED_PLATFORM_KEYS, load_config, make_hook, normalize_config, register

__all__ = ["SUPPORTED_PLATFORM_KEYS", "load_config", "make_hook", "normalize_config", "register"]
