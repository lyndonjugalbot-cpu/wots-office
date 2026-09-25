"""Employee type catalogue (spec v2 §5): YAML definitions + one implementation class per type.

Types are platform-level. `sync_catalogue()` loads them (plus workflows and office templates)
into their tables on startup. `validate_config()` checks a hire's config against the type's
config_schema; `model` and `effort` are platform-wide overrides every employee accepts.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

TYPES_DIR = Path(__file__).parent / "types"
EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]
PLATFORM_CONFIG = {"model", "effort"}


class CatalogueError(ValueError):
    pass


@dataclass(frozen=True)
class EmployeeTypeDef:
    key: str
    version: int
    display_name: str
    category: str
    description: str
    impl: str
    task_kinds: list[str]
    default_model: str
    tools: list[str]
    config_schema: dict[str, dict]
    risk_level: str
    plans: list[str]
    est_credits_per_task: int
    status: str
    extra: dict = field(default_factory=dict)

    @property
    def uses_llm(self) -> bool:
        return bool(self.extra.get("uses_llm", True))

    @property
    def hireable(self) -> bool:
        return self.status in {"available", "beta"}

    def load_impl(self):
        """The implementation class, or None if it isn't built yet (a later phase)."""
        module_name, _, class_name = self.impl.partition(":")
        try:
            return getattr(importlib.import_module(module_name), class_name)
        except (ImportError, AttributeError):
            return None

    def validate_config(self, config: dict[str, Any] | None) -> dict[str, Any]:
        """Defaults filled in; unknown keys, wrong types and out-of-range values rejected."""
        config = dict(config or {})
        unknown = set(config) - set(self.config_schema) - PLATFORM_CONFIG
        if unknown:
            raise CatalogueError(f"{self.display_name} has no setting(s): {', '.join(sorted(unknown))}")
        out: dict[str, Any] = {}
        for name, spec in self.config_schema.items():
            value = config.get(name, spec.get("default"))
            kind = spec.get("type")
            if kind == "enum" and value not in spec["values"]:
                raise CatalogueError(f"{name} must be one of {', '.join(spec['values'])}")
            if kind == "int":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise CatalogueError(f"{name} must be a whole number") from None
                if not spec.get("min", value) <= value <= spec.get("max", value):
                    raise CatalogueError(f"{name} must be between {spec.get('min')} and {spec.get('max')}")
            if kind == "list" and not isinstance(value, list):
                value = [v.strip() for v in str(value).split(",") if v.strip()]
            out[name] = value
        if config.get("effort") is not None and config["effort"] not in EFFORT_LEVELS:
            raise CatalogueError(f"effort must be one of {', '.join(EFFORT_LEVELS)}")
        for key in PLATFORM_CONFIG:
            if config.get(key) not in (None, ""):
                out[key] = config[key]
        return out


def load_types(directory: Path = TYPES_DIR) -> dict[str, EmployeeTypeDef]:
    types = {}
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text())
        known = {f for f in EmployeeTypeDef.__dataclass_fields__ if f != "extra"}
        data = {k: raw[k] for k in known if k in raw}
        data.setdefault("tools", [])
        data.setdefault("config_schema", {})
        data["config_schema"] = data["config_schema"] or {}
        data["extra"] = {k: v for k, v in raw.items() if k not in known}
        try:
            t = EmployeeTypeDef(**data)
        except TypeError as e:
            raise CatalogueError(f"{path.name}: {e}") from e
        if t.risk_level not in {"low", "medium", "high"}:
            raise CatalogueError(f"{path.name}: risk_level must be low, medium or high")
        types[t.key] = t
    return types
