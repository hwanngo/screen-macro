import json
import math
import tempfile
import unittest
from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path

from macro_config import (
    ClickTemplateAction,
    KeyAction,
    MacroConfigError,
    MacroDefinition,
    RepeatAction,
    WaitAction,
    load_macro,
)


class MacroConfigTests(unittest.TestCase):
    def write_config(self, directory: Path, document, name="macro.json") -> Path:
        path = directory / name
        if isinstance(document, str):
            path.write_text(document, encoding="utf-8")
        else:
            path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def assert_invalid(self, directory: Path, document, location: str):
        with self.assertRaisesRegex(MacroConfigError, location):
            load_macro(self.write_config(directory, document))

    def test_load_macro_resolves_template_relative_to_json_and_returns_frozen_types(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            image = directory / "images" / "button.png"
            image.parent.mkdir()
            image.write_bytes(b"not actually decoded")
            path = self.write_config(directory, {
                "version": 1,
                "startup": [{"type": "key", "key": "enter"}],
                "rounds": {"cycles": 2},
                "loop": [
                    {"type": "click_template", "path": "images/button.png"},
                    {"type": "wait", "min_seconds": 0, "max_seconds": 1.5},
                    {"type": "repeat", "times": 2, "actions": [{"type": "key", "random_choice": ["1", "right"]}]},
                ],
                "after_round": [{"type": "key", "key": "right"}],
            })
            macro = load_macro(path)

        self.assertEqual(macro.startup, (KeyAction(key="enter"),))
        self.assertEqual(macro.loop[0], ClickTemplateAction(image.resolve()))
        self.assertEqual(macro.loop[1], WaitAction(0, 1.5))
        self.assertEqual(macro.loop[2], RepeatAction(2, (KeyAction(random_choice=("1", "right")),)))
        self.assertEqual(macro.after_round, (KeyAction(key="right"),))
        self.assertEqual(macro.cycles_per_round, 2)
        self.assertTrue(all(is_dataclass(value) for value in (macro, macro.loop[0])))
        with self.assertRaises(FrozenInstanceError):
            macro.cycles_per_round = 3

    def test_rejects_document_missing_required_loop(self):
        with tempfile.TemporaryDirectory() as raw:
            self.assert_invalid(Path(raw), {"version": 1}, "loop")

    def test_rejects_unreadable_and_malformed_json(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            with self.assertRaisesRegex(MacroConfigError, "read|No such file"):
                load_macro(directory / "missing.json")
            self.assert_invalid(directory, "{", "JSON|line|column")

    def test_rejects_wrong_version_and_top_level_fields(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self.assert_invalid(directory, {"version": 2, "loop": []}, "version")
            self.assert_invalid(directory, {"version": True, "loop": []}, "version")
            self.assert_invalid(directory, {"version": 1.0, "loop": []}, "version")
            self.assert_invalid(directory, {"version": 1, "loop": [], "extra": 1}, "extra")

    def test_rejects_malformed_phase_lists_and_rounds(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self.assert_invalid(directory, {"version": 1, "loop": {}}, "loop")
            self.assert_invalid(directory, {"version": 1, "loop": [], "startup": [1]}, "startup")
            self.assert_invalid(directory, {"version": 1, "loop": [], "rounds": []}, "rounds")
            self.assert_invalid(directory, {"version": 1, "loop": [], "rounds": {"cycles": 0}}, "cycles")
            self.assert_invalid(directory, {"version": 1, "loop": [], "rounds": {"cycles": 1, "x": 2}}, "rounds")
            self.assert_invalid(directory, {"version": 1, "loop": [], "after_round": []}, "after_round|cycles")

    def test_rejects_unknown_action_fields_and_types(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self.assert_invalid(directory, {"version": 1, "loop": [{"type": "shell"}]}, r"loop\[0\]|action")
            self.assert_invalid(directory, {"version": 1, "loop": [{"type": []}]}, r"loop\[0\]|action")
            self.assert_invalid(directory, {"version": 1, "loop": [{"type": "key", "key": "a", "extra": 1}]}, r"loop\[0\]")
            self.assert_invalid(directory, {"version": 1, "loop": [{"key": "a"}]}, "type")
            self.assert_invalid(directory, {"version": 1, "loop": ["key"]}, r"loop\[0\]")

    def test_rejects_invalid_keys_and_random_choices(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            for key in ("", "ab", "space", 1, None):
                self.assert_invalid(directory, {"version": 1, "loop": [{"type": "key", "key": key}]}, "key")
            self.assert_invalid(directory, {"version": 1, "loop": [{"type": "key", "key": "a", "random_choice": ["b"]}]}, "key")
            for choices in ([], ["ab"], ["left"], [1], ["right", "bad"]):
                self.assert_invalid(directory, {"version": 1, "loop": [{"type": "key", "random_choice": choices}]}, "random_choice")

    def test_rejects_invalid_waits(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            invalid = [
                {"type": "wait", "min_seconds": -1, "max_seconds": 1},
                {"type": "wait", "min_seconds": 2, "max_seconds": 1},
                {"type": "wait", "min_seconds": math.inf, "max_seconds": 2},
                {"type": "wait", "min_seconds": 0, "max_seconds": "1"},
                {"type": "wait", "min_seconds": 0},
            ]
            for action in invalid:
                # JSON cannot encode infinity, so write this case directly below.
                if math.isinf(action.get("min_seconds", 0)):
                    path = directory / "macro.json"
                    path.write_text('{"version": 1, "loop": [{"type":"wait", "min_seconds": 1e999, "max_seconds": 2}]}')
                    with self.assertRaisesRegex(MacroConfigError, "wait"):
                        load_macro(path)
                else:
                    self.assert_invalid(directory, {"version": 1, "loop": [action]}, "loop")

    def test_rejects_waits_above_maximum_with_wait_location(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self.assert_invalid(
                directory,
                {"version": 1, "loop": [{"type": "wait", "min_seconds": 0, "max_seconds": 3600.001}]},
                r"loop\[0\].max_seconds|loop\[0\]",
            )

    def test_accepts_wait_bounds_at_maximum(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            macro = load_macro(self.write_config(directory, {
                "version": 1,
                "loop": [{"type": "wait", "min_seconds": 0, "max_seconds": 3600}],
            }))
            self.assertEqual(macro.loop, (WaitAction(0, 3600),))

    def test_rejects_huge_integer_wait_as_macro_config_error(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            path = directory / "macro.json"
            path.write_text(
                '{"version": 1, "loop": [{"type":"wait", "min_seconds": 0, "max_seconds": '
                + str(10**400)
                + '}]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MacroConfigError, r"loop\[0\]"):
                load_macro(path)

    def test_rejects_invalid_template_paths(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            for path_value in ("", ".", "missing.png", "../outside.png", 1):
                self.assert_invalid(directory, {"version": 1, "loop": [{"type": "click_template", "path": path_value}]}, "path|template")
            subdir = directory / "folder"
            subdir.mkdir()
            self.assert_invalid(directory, {"version": 1, "loop": [{"type": "click_template", "path": "folder"}]}, "path|template")

    def test_rejects_invalid_repeats_and_accepts_nested_supported_actions(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            base = {"version": 1, "loop": []}
            for repeat in (
                {"type": "repeat", "times": 0, "actions": [{"type": "key", "key": "a"}]},
                {"type": "repeat", "times": -1, "actions": [{"type": "key", "key": "a"}]},
                {"type": "repeat", "times": 1.5, "actions": [{"type": "key", "key": "a"}]},
                {"type": "repeat", "times": 1, "actions": []},
                {"type": "repeat", "times": 1, "actions": {}},
                {"type": "repeat", "times": 1, "actions": [{"type": "repeat", "times": 1, "actions": [{"type": "repeat", "times": 1, "actions": [{"type": "repeat", "times": 1, "actions": [{"type": "key", "key": "a"}]}]}]}]},
            ):
                self.assert_invalid(directory, {**base, "loop": [repeat]}, "loop")

            oversized = {"type": "repeat", "times": 1001, "actions": [{"type": "key", "key": "a"}]}
            self.assert_invalid(directory, {**base, "loop": [oversized]}, "loop")
            nested = {"type": "repeat", "times": 2, "actions": [{"type": "wait", "min_seconds": 0, "max_seconds": 0}, {"type": "repeat", "times": 2, "actions": [{"type": "key", "key": "enter"}]}]}
            macro = load_macro(self.write_config(directory, {**base, "loop": [nested]}))
            self.assertEqual(macro.loop[0], RepeatAction(2, (WaitAction(0, 0), RepeatAction(2, (KeyAction(key="enter"),)))))


if __name__ == "__main__":
    unittest.main()
