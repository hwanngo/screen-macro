import json
import random
import tempfile
import unittest

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

from macro import main
from macro_config import (
    ClickTemplateAction,
    KeyAction,
    MacroDefinition,
    RepeatAction,
    WaitAction,
    load_macro,
)


class RunCycleTests(unittest.TestCase):
    def test_readme_documents_json_only_usage(self):
        # The README must provide both supported example invocations and make
        # clear that the example is intended to be customized.
        readme = (Path(__file__).parent.parent / "README.md").read_text()

        self.assertIn("--macro macros/example/macro.json", readme)
        self.assertIn("--macro macros/example/macro.json --dry-run", readme)
        self.assertIn("copy it", readme)

    def test_readme_explains_local_macro_policy(self):
        # Removing the local-only policy makes it unclear which macros belong
        # in version control and which must remain private to one machine.
        readme = (Path(__file__).parent.parent / "README.md").read_text()

        self.assertIn("## Local macros", readme)
        self.assertIn("`macros/*`", readme)
        self.assertIn("ignored by Git", readme)
        self.assertIn("`macros/example/`", readme)

    def test_run_actions_executes_nested_repeat_and_random_key_in_order(self):
        # Removing recursive execution, using the lower random index, or skipping
        # the wait must each make this behavior fail.
        from macro import run_actions

        actions = (
            RepeatAction(2, (KeyAction(random_choice=("1", "2")),)),
            WaitAction(0.5, 0.8),
            KeyAction(key="right"),
        )
        sent, waits = [], []

        completed = run_actions(
            actions,
            sent.append,
            lambda seconds: waits.append(seconds) or True,
            lambda path: True,
            lambda low, high: high,
            lambda: True,
            lambda low, high: low,
        )

        self.assertTrue(completed)
        self.assertEqual(sent, ["2", "2", "right"])
        self.assertEqual(waits, [0.5])

    def test_run_actions_stops_before_a_click_after_an_interrupted_wait(self):
        # Continuing after a false wait would invoke the deliberately failing click.
        from macro import run_actions

        completed = run_actions(
            (WaitAction(0.5, 0.8), ClickTemplateAction(Path("button.png"))),
            lambda key: None,
            lambda seconds: False,
            lambda path: self.fail("click must not occur after an interrupted wait"),
            random.randint,
            lambda: True,
            random.uniform,
        )

        self.assertFalse(completed)

    def test_run_actions_stops_before_remaining_actions_after_a_failed_click(self):
        # Continuing after a failed template click would send the trailing key.
        from macro import run_actions

        sent = []
        completed = run_actions(
            (ClickTemplateAction(Path("button.png")), KeyAction(key="right")),
            sent.append,
            lambda seconds: True,
            lambda path: False,
            random.randint,
            lambda: True,
            random.uniform,
        )

        self.assertFalse(completed)
        self.assertEqual(sent, [])

    def test_runner_macro_mode_runs_startup_pause_then_first_loop_action(self):
        from macro import MacroRunner

        class ImmediateEvent:
            def __init__(self):
                self._event = Event()

            def is_set(self):
                return self._event.is_set()

            def set(self):
                self._event.set()

            def wait(self, seconds):
                return self._event.is_set()

        stopped = ImmediateEvent()
        events = []

        def send_key(key):
            events.append(("send", key))
            if key == "loop":
                stopped.set()

        runner = MacroRunner(
            send_key,
            event_factory=lambda: stopped,
            macro=MacroDefinition(
                startup=(KeyAction(key="startup"),),
                loop=(KeyAction(key="loop"),),
                after_round=(),
            ),
            uniform=lambda low, high: events.append(("pause", low, high)) or low,
        )

        self.assertTrue(runner.enable())
        runner._worker.join(1)
        self.assertFalse(runner.is_enabled() and runner._worker.is_alive())
        self.assertEqual(
            events,
            [
                ("pause", 0.5, 0.8),
                ("send", "startup"),
                ("pause", 0.5, 0.8),
                ("send", "loop"),
            ],
        )
        runner.disable()

    def test_runner_macro_mode_runs_after_round_after_configured_loop_completions(self):
        from macro import MacroRunner

        class ImmediateEvent:
            def __init__(self):
                self._event = Event()

            def is_set(self):
                return self._event.is_set()

            def set(self):
                self._event.set()

            def wait(self, seconds):
                return self._event.is_set()

        stopped = ImmediateEvent()
        sent = []

        def send_key(key):
            sent.append(key)
            if key == "after":
                stopped.set()

        runner = MacroRunner(
            send_key,
            event_factory=lambda: stopped,
            macro=MacroDefinition(
                startup=(),
                loop=(KeyAction(key="loop"),),
                after_round=(KeyAction(key="after"),),
                cycles_per_round=2,
            ),
            uniform=lambda low, high: low,
        )

        self.assertTrue(runner.enable())
        runner._worker.join(1)
        self.assertEqual(sent, ["loop", "loop", "after"])
        runner.disable()

    def test_runner_stops_when_a_macro_template_click_times_out(self):
        # Leaving the event unset would report a macro as active after a
        # template click has failed and its worker has returned.
        from macro import MacroRunner

        class ImmediateEvent:
            def __init__(self):
                self._event = Event()

            def is_set(self):
                return self._event.is_set()

            def set(self):
                self._event.set()

            def wait(self, seconds):
                return self._event.is_set()

        stopped = ImmediateEvent()
        runner = MacroRunner(
            lambda key: self.fail("the loop contains no key action"),
            event_factory=lambda: stopped,
            macro=MacroDefinition(
                startup=(),
                loop=(ClickTemplateAction(Path("/resolved/button.png")),),
                after_round=(),
            ),
            click_template=lambda path: False,
            uniform=lambda low, high: 0,
        )

        self.assertTrue(runner.enable())
        runner._worker.join(1)
        self.assertTrue(stopped.is_set())
        runner.disable()

    def test_click_and_move_away_removes_hover_after_clicking(self):
        from macro import click_and_move_away

        actions = []
        click_and_move_away(
            lambda x, y, **options: actions.append(("click", x, y, options)),
            lambda x, y: actions.append(("move", x, y)),
            967,
            586,
        )

        self.assertEqual(
            actions,
            [("click", 967, 586, {"button": "left"}), ("move", 5, 5)],
        )

    def test_locate_template_uses_confidence_matching(self):
        from macro import locate_template

        calls = []
        expected = (10, 20, 30, 40)

        found = locate_template(
            lambda template, **options: calls.append((template, options)) or expected,
            Path("button.png"),
        )

        self.assertEqual(found, expected)
        self.assertEqual(calls[0][1], {"grayscale": True, "confidence": 0.8})

    def test_key_for_name_uses_pynput_special_keys_for_right_and_enter(self):
        from macro import key_for_name

        class Keys:
            right = object()
            enter = object()

        self.assertIs(key_for_name(Keys, "right"), Keys.right)
        self.assertIs(key_for_name(Keys, "enter"), Keys.enter)
        self.assertEqual(key_for_name(Keys, "2"), "2")

    def test_template_matching_screenshot_backend_is_importable(self):
        import pyscreeze

        self.assertTrue(callable(pyscreeze.screenshot))

    def test_wait_until_stopped_reports_an_already_stopped_worker_as_interrupted(self):
        from macro import wait_until_stopped

        stopped = Event()
        stopped.set()

        self.assertFalse(wait_until_stopped(stopped, 60))

    def test_main_requires_macro_path_in_normal_and_dry_run_modes(self):
        for arguments in ([], ["--dry-run"]):
            with self.subTest(arguments=arguments):
                errors = StringIO()
                with redirect_stderr(errors), self.assertRaises(SystemExit):
                    main(arguments)
                self.assertIn("--macro", errors.getvalue())

    def test_main_dry_run_previews_valid_nested_template_macro_without_runtimes(self):
        # Removing the dry-run return, skipping recursive formatting, or
        # initializing either input runtime makes this user-visible preview fail.
        with tempfile.TemporaryDirectory() as raw:
            temporary_directory = Path(raw)
            template_path = temporary_directory / "template.png"
            template_path.touch()
            macro_path = temporary_directory / "macro.json"
            macro_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "startup": [
                            {
                                "type": "repeat",
                                "times": 2,
                                "actions": [
                                    {
                                        "type": "click_template",
                                        "path": template_path.name,
                                    }
                                ],
                            }
                        ],
                        "loop": [{"type": "key", "key": "right"}],
                    }
                )
            )
            output = StringIO()
            with patch(
                "macro.create_keyboard_runtime",
                side_effect=AssertionError("dry run must not initialize keyboard runtime"),
            ), patch(
                "macro.create_template_runtime",
                side_effect=AssertionError("dry run must not initialize template runtime"),
            ), redirect_stdout(output):
                main(["--macro", str(macro_path), "--dry-run"])

        self.assertEqual(
            output.getvalue(),
            "Dry run: validated macro.\n"
            "startup: repeat 2:\n"
            f"  click template {template_path.resolve()}\n"
            "loop: key right\n",
        )

    def test_main_dry_run_help_describes_validation_and_preview(self):
        # Reverting the dry-run help to describe keyboard controls rather than
        # its observable validation and preview behavior makes this fail.
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            main(["--help"])

        self.assertIn("validates and previews configured actions", output.getvalue())

    def test_main_rejects_invalid_macro_before_keyboard_setup(self):
        # Accepting malformed JSON or reaching keyboard setup would permit an
        # invalid macro to get as far as the input runtime.
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            macro_path = Path(raw) / "bad.json"
            macro_path.write_text("not json")
            errors = StringIO()
            with patch(
                "macro.create_keyboard_runtime",
                side_effect=AssertionError("keyboard setup must not run"),
            ), redirect_stderr(errors):
                with self.assertRaises(SystemExit):
                    main(["--macro", str(macro_path)])

        self.assertIn("macro:", errors.getvalue())


    def test_main_key_only_macro_does_not_create_template_runtime(self):
        # Initializing the screenshot backend for a key-only macro makes an
        # unrelated Screen Recording permission a requirement.
        import json
        import tempfile

        class KeyboardRuntime:
            class Key:
                ctrl_l = object()
                ctrl_r = object()
                alt_l = object()
                alt_r = object()
                esc = object()
                right = object()
                enter = object()

            class Controller:
                pass

            class Listener:
                def __init__(self, **callbacks):
                    self.callbacks = callbacks

                def __enter__(self):
                    return self

                def __exit__(self, *unused):
                    return False

                def join(self):
                    return None

        with tempfile.TemporaryDirectory() as raw:
            macro_path = Path(raw) / "key-only.json"
            macro_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "loop": [{"type": "key", "key": "right"}],
                    }
                )
            )
            with patch("macro.create_keyboard_runtime", return_value=KeyboardRuntime), patch(
                "macro.create_template_runtime",
                side_effect=AssertionError("key-only macro must not initialize templates"),
            ):
                main(["--macro", str(macro_path)])

    def test_macro_template_runtime_detection_handles_nested_repeat_actions(self):
        # Skipping recursive detection would leave nested click_template
        # actions without the screenshot-to-mouse adapter.
        from macro import macro_uses_template_runtime

        template = Path("/resolved/button.png")
        self.assertFalse(
            macro_uses_template_runtime(
                MacroDefinition(startup=(), loop=(KeyAction(key="right"),), after_round=())
            )
        )
        self.assertTrue(
            macro_uses_template_runtime(
                MacroDefinition(
                    startup=(),
                    loop=(RepeatAction(2, (ClickTemplateAction(template),)),),
                    after_round=(),
                )
            )
        )

    def test_example_loads_with_resolved_template_and_round_count(self):
        # Breaking the JSON-relative path or its round count changes the
        # documented example workflow.
        macro = load_macro(Path("macros/example/macro.json"))
        expected_template = (Path("macros/example") / "assets/example-button.png").resolve()

        self.assertEqual(macro.cycles_per_round, 185)
        self.assertEqual(macro.startup, (ClickTemplateAction(expected_template),))
        self.assertEqual(
            macro.after_round,
            (
                KeyAction(key="enter"),
                ClickTemplateAction(expected_template),
                WaitAction(0.5, 0.8),
            ),
        )

    def test_template_click_adapter_scales_click_moves_away_and_reports_timeout(self):
        # Using an unresolved path, unscaled center, or omitting the pointer
        # move would make the found-template portion fail; continuing after
        # polling timeout would make the timeout portion fail.
        from macro import make_template_click_adapter

        template = Path("/resolved/button.png")
        interactions = []
        adapter = make_template_click_adapter(
            locate=lambda path: interactions.append(("locate", path)) or (1848, 1140, 172, 62),
            click=lambda x, y: interactions.append(("click", x, y)),
            move=lambda x, y: interactions.append(("move", x, y)),
            wait=lambda seconds: self.fail("a found template should not poll"),
            keep_running=lambda: True,
            coordinate_scale=(0.5, 0.5),
        )

        self.assertTrue(adapter(template))
        self.assertEqual(
            interactions,
            [
                ("locate", str(template)),
                ("click", 967, 586),
                ("move", 5, 5),
            ],
        )

        messages = []
        timed_out = make_template_click_adapter(
            locate=lambda path: None,
            click=lambda x, y: self.fail("timeout must not click"),
            move=lambda x, y: self.fail("timeout must not move"),
            wait=lambda seconds: True,
            keep_running=lambda: True,
            report=messages.append,
            timeout=0.5,
        )

        self.assertFalse(timed_out(template))
        self.assertEqual(len(messages), 1)
        self.assertIn(str(template), messages[0])

    def test_runner_disable_joins_the_worker_before_reenabling(self):
        from macro import MacroRunner

        first_send_started = Event()
        allow_first_send_to_finish = Event()
        disabled = Event()
        sent = []

        def send_key(key):
            sent.append(key)
            first_send_started.set()
            allow_first_send_to_finish.wait()

        runner = MacroRunner(
            send_key,
            randint=lambda low, high: low,
            macro=MacroDefinition(
                startup=(), loop=(KeyAction(key="key"),), after_round=()
            ),
            uniform=lambda low, high: 0,
        )
        runner.enable()
        self.assertTrue(first_send_started.wait(1))

        Thread(target=lambda: (runner.disable(), disabled.set()), daemon=True).start()
        self.assertFalse(disabled.wait(0.05))
        allow_first_send_to_finish.set()
        self.assertTrue(disabled.wait(1))
        self.assertEqual(sent, ["key"])

        runner.enable()
        self.assertTrue(runner.is_enabled())
        runner.disable()

    def test_runner_does_not_stop_between_send_check_and_send(self):
        from macro import MacroRunner

        class PausingEvent:
            def __init__(self):
                self._event = Event()
                self._checks = 0
                self.send_check_started = Event()
                self.resume_send_check = Event()
                self.stop_set = Event()

            def is_set(self):
                self._checks += 1
                if self._checks == 3:
                    self.send_check_started.set()
                    self.resume_send_check.wait(1)
                return self._event.is_set()

            def set(self):
                self.stop_set.set()
                self._event.set()

            def wait(self, seconds):
                return self._event.wait(seconds)

        stopped = PausingEvent()
        disabled = Event()
        sent_after_stop = []
        runner = MacroRunner(
            lambda key: sent_after_stop.append(stopped.stop_set.is_set()),
            randint=lambda low, high: low,
            event_factory=lambda: stopped,
            macro=MacroDefinition(
                startup=(), loop=(KeyAction(key="key"),), after_round=()
            ),
            uniform=lambda low, high: 0,
        )
        runner.enable()
        self.assertTrue(stopped.send_check_started.wait(1))
        Thread(target=lambda: (runner.disable(), disabled.set()), daemon=True).start()
        try:
            self.assertFalse(stopped.stop_set.wait(0.05))
        finally:
            stopped.resume_send_check.set()

        self.assertTrue(disabled.wait(1))
        self.assertEqual(sent_after_stop, [False])
