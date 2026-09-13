"""Validated, filesystem-only JSON macro configuration loading."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MacroConfigError(ValueError):
    """Raised when a macro file cannot be read or fails validation."""


@dataclass(frozen=True)
class KeyAction:
    key: str | None = None
    random_choice: tuple[str, ...] = ()


@dataclass(frozen=True)
class WaitAction:
    min_seconds: int | float
    max_seconds: int | float


@dataclass(frozen=True)
class ClickTemplateAction:
    path: Path


@dataclass(frozen=True)
class RepeatAction:
    times: int
    actions: tuple[KeyAction | WaitAction | ClickTemplateAction | "RepeatAction", ...]


Action = KeyAction | WaitAction | ClickTemplateAction | RepeatAction


@dataclass(frozen=True)
class MacroDefinition:
    startup: tuple[Action, ...]
    loop: tuple[Action, ...]
    after_round: tuple[Action, ...]
    cycles_per_round: int | None = None


_TOP_LEVEL_FIELDS = {"version", "rounds", "startup", "loop", "after_round"}
_KEY_FIELDS = {"type", "key", "random_choice"}
_WAIT_FIELDS = {"type", "min_seconds", "max_seconds"}
_CLICK_FIELDS = {"type", "path"}
_REPEAT_FIELDS = {"type", "times", "actions"}
_SUPPORTED_SPECIAL_KEYS = {"right", "enter"}
_MAX_ACTIONS = 1000
_MAX_REPEAT_DEPTH = 3
_MAX_WAIT_SECONDS = 3600


def _fail(location: str, message: str) -> None:
    raise MacroConfigError(f"macro.{location}: {message}")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _supported_key(value: Any) -> bool:
    return isinstance(value, str) and (len(value) == 1 or value in _SUPPORTED_SPECIAL_KEYS)


def _parse_action(raw: Any, parent: Path, location: str, depth: int = 0) -> tuple[Action, int]:
    if not isinstance(raw, dict):
        _fail(location, "action must be an object")
    action_type = raw.get("type")
    if not isinstance(action_type, str) or action_type not in {"key", "wait", "click_template", "repeat"}:
        _fail(location, "unknown or missing action type")

    if action_type == "key":
        unknown = set(raw) - _KEY_FIELDS
        if unknown:
            _fail(location, f"unknown field(s): {', '.join(sorted(unknown))}")
        has_key = "key" in raw
        has_choices = "random_choice" in raw
        if has_key == has_choices:
            _fail(location, "key requires exactly one of key or random_choice")
        if has_key:
            if not _supported_key(raw["key"]):
                _fail(f"{location}.key", "must be one character, right, or enter")
            return KeyAction(key=raw["key"]), 1
        choices = raw["random_choice"]
        if not isinstance(choices, list) or not choices:
            _fail(f"{location}.random_choice", "must be a non-empty list")
        for index, choice in enumerate(choices):
            if not _supported_key(choice):
                _fail(f"{location}.random_choice[{index}]", "unsupported key")
        return KeyAction(random_choice=tuple(choices)), 1

    if action_type == "wait":
        unknown = set(raw) - _WAIT_FIELDS
        if unknown:
            _fail(location, f"unknown field(s): {', '.join(sorted(unknown))}")
        if "min_seconds" not in raw or "max_seconds" not in raw:
            _fail(location, "wait requires min_seconds and max_seconds")
        minimum, maximum = raw["min_seconds"], raw["max_seconds"]
        if not _is_number(minimum) or not _is_number(maximum):
            _fail(location, "wait bounds must be numeric")
        minimum_finite = isinstance(minimum, int) or math.isfinite(minimum)
        maximum_finite = isinstance(maximum, int) or math.isfinite(maximum)
        if not minimum_finite or not maximum_finite or minimum < 0 or maximum < 0:
            _fail(location, "wait bounds must be finite and non-negative")
        if minimum > _MAX_WAIT_SECONDS:
            _fail(f"{location}.min_seconds", "must not exceed 3600 seconds")
        if maximum > _MAX_WAIT_SECONDS:
            _fail(f"{location}.max_seconds", "must not exceed 3600 seconds")
        if minimum > maximum:
            _fail(location, "min_seconds must not exceed max_seconds")
        return WaitAction(minimum, maximum), 1

    if action_type == "click_template":
        unknown = set(raw) - _CLICK_FIELDS
        if unknown:
            _fail(location, f"unknown field(s): {', '.join(sorted(unknown))}")
        value = raw.get("path")
        if not isinstance(value, str) or not value or Path(value).is_absolute():
            _fail(f"{location}.path", "must be a non-empty relative path")
        resolved = (parent / value).resolve()
        if not resolved.is_file():
            _fail(f"{location}.path", f"template is not an existing regular file: {resolved}")
        return ClickTemplateAction(resolved), 1

    unknown = set(raw) - _REPEAT_FIELDS
    if unknown:
        _fail(location, f"unknown field(s): {', '.join(sorted(unknown))}")
    times = raw.get("times")
    if not isinstance(times, int) or isinstance(times, bool) or times <= 0:
        _fail(f"{location}.times", "must be a positive integer")
    if depth >= _MAX_REPEAT_DEPTH:
        _fail(location, f"repeat nesting exceeds maximum depth {_MAX_REPEAT_DEPTH}")
    children = raw.get("actions")
    if not isinstance(children, list) or not children:
        _fail(f"{location}.actions", "must be a non-empty list")
    parsed: list[Action] = []
    expanded = 0
    for index, child in enumerate(children):
        action, count = _parse_action(child, parent, f"{location}.actions[{index}]", depth + 1)
        parsed.append(action)
        expanded += count
    expanded *= times
    if expanded > _MAX_ACTIONS:
        _fail(location, f"repeat expansion exceeds {_MAX_ACTIONS} executed actions")
    return RepeatAction(times, tuple(parsed)), expanded


def _parse_phase(raw: Any, parent: Path, location: str) -> tuple[Action, ...]:
    if not isinstance(raw, list):
        _fail(location, "phase must be an action list")
    result: list[Action] = []
    expanded = 0
    for index, item in enumerate(raw):
        action, count = _parse_action(item, parent, f"{location}[{index}]")
        expanded += count
        if expanded > _MAX_ACTIONS:
            _fail(location, f"phase expansion exceeds {_MAX_ACTIONS} executed actions")
        result.append(action)
    return tuple(result)


def _parse_document(document: Any, parent: Path) -> MacroDefinition:
    if not isinstance(document, dict):
        _fail("document", "must be an object")
    unknown = set(document) - _TOP_LEVEL_FIELDS
    if unknown:
        _fail("document", f"unknown field(s): {', '.join(sorted(unknown))}")
    version = document.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        _fail("version", "must be integer 1")
    if "loop" not in document:
        _fail("loop", "required phase is missing")

    cycles: int | None = None
    if "rounds" in document:
        rounds = document["rounds"]
        if not isinstance(rounds, dict) or set(rounds) != {"cycles"}:
            _fail("rounds", "must contain only cycles")
        cycles = rounds["cycles"]
        if not isinstance(cycles, int) or isinstance(cycles, bool) or cycles <= 0:
            _fail("rounds.cycles", "must be a positive integer")
    if "after_round" in document and cycles is None:
        _fail("after_round", "requires rounds.cycles")
    startup = _parse_phase(document.get("startup", []), parent, "startup")
    loop = _parse_phase(document["loop"], parent, "loop")
    after_round = _parse_phase(document.get("after_round", []), parent, "after_round")
    return MacroDefinition(startup, loop, after_round, cycles)


def load_macro(path: str | Path) -> MacroDefinition:
    source = Path(path).resolve()
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MacroConfigError(f"macro: unable to read or decode {source}: {error}") from error
    try:
        return _parse_document(document, source.parent)
    except MacroConfigError:
        raise
    except (TypeError, ValueError) as error:
        raise MacroConfigError(f"macro: invalid document: {error}") from error
