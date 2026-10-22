"""Validated environment defaults and effective settings for collector runs."""
import os
import re


_SPECS = (
    ("room_gap_seconds", 0, 0, 3600),
    ("page_refresh_seconds", 300, 15, 3600),
    ("status_refresh_seconds", 5, 2, 60),
    ("checkpoint_seconds", 30, 5, 600),
    ("listen_timeout_seconds", 2, 1, 30),
    ("scan_timeout_seconds", 30, 5, 180),
    ("connect_timeout_seconds", 30, 5, 180),
    ("captcha_timeout_seconds", 180, 10, 1800),
    ("stall_timeout_seconds", 60, 10, 600),
    ("max_record_attempts", 3, 1, 10),
    ("failure_cooldown_minutes", 120, 1, 1440),
    ("max_room_failures", 3, 1, 20),
    ("max_collected", 800, 10, 2000),
)
RUNTIME_FIELDS = {
    name: {"default": default, "min": low, "max": high,
           "env": "LIVE_" + name.upper()}
    for name, default, low, high in _SPECS
}


def read_runtime_options(environ=None):
    environ = os.environ if environ is None else environ
    result = {}
    for name, spec in RUNTIME_FIELDS.items():
        raw = environ.get(spec["env"])
        if type(raw) is int:
            value = raw
        elif isinstance(raw, str) and re.fullmatch(r"[+-]?\d+", raw.strip()):
            value = int(raw.strip())
        else:
            value = spec["default"]
        result[name] = value if spec["min"] <= value <= spec["max"] else spec["default"]
    return result


def effective_settings(config, instance):
    """Merge new global runtime settings with explicit per-instance overrides."""
    result = dict(config)
    result.update({name: config.get(name, spec["default"])
                   for name, spec in RUNTIME_FIELDS.items()})
    for key in ("max_minutes", "cooldown_minutes", "room_gap_seconds", "page_refresh_seconds"):
        value = instance.get(key)
        if value is not None:
            result[key] = value
    result["cooldown_minutes"] = config.get("cooldown_minutes", 120)
    if instance.get("cooldown_minutes") is not None:
        result["cooldown_minutes"] = instance["cooldown_minutes"]
    if instance.get("max_minutes") is not None:
        result["max_minutes"] = instance["max_minutes"]
    if "segments" in instance:
        result["segments"] = instance["segments"]
    return result
