"""JSON-driven desktop automation macro."""

import argparse
import random
import threading
from pathlib import Path

from macro_config import (
    ClickTemplateAction,
    KeyAction,
    MacroConfigError,
    RepeatAction,
    WaitAction,
    load_macro,
)

def locate_template(locate_on_screen, template_path):
    """Find the reference label while tolerating minor font-rendering changes."""
    return locate_on_screen(str(template_path), grayscale=True, confidence=0.8)


def click_and_move_away(click, move, x, y):
    """Click the button and remove the pointer so its hover style clears."""
    click(x, y, button="left")
    move(5, 5)


def key_for_name(keys, name):
    """Convert macro key names to pynput's special-key objects."""
    if name == "right":
        return keys.right
    if name == "enter":
        return keys.enter
    return name


def make_template_click_adapter(
    locate,
    click,
    move,
    wait=None,
    keep_running=None,
    coordinate_scale=(1.0, 1.0),
    report=print,
    timeout=15,
):
    """Return an interruptible template finder for JSON macro click actions."""

    def click_template(path, *, runtime_wait=None, runtime_keep_running=None, click_action=None):
        action_wait = runtime_wait or wait
        action_keep_running = runtime_keep_running or keep_running
        if action_wait is None or action_keep_running is None:
            raise RuntimeError("template click adapter requires runner wait controls")

        elapsed = 0.0
        while action_keep_running() and elapsed < timeout:
            box = locate(str(path))
            if box is not None:
                left, top, width, height = box
                scale_x, scale_y = coordinate_scale

                def click_and_clear_hover():
                    click(
                        round((left + width / 2) * scale_x),
                        round((top + height / 2) * scale_y),
                    )
                    move(5, 5)

                if click_action is not None:
                    return click_action(click_and_clear_hover)
                click_and_clear_hover()
                return True
            delay = min(0.5, timeout - elapsed)
            if not action_wait(delay):
                return False
            elapsed += delay

        if action_keep_running():
            report(f"Template not found within {timeout:g} seconds: {path}. Macro stopped.")
        return False

    click_template.uses_runtime_controls = True
    return click_template


def wait_until_stopped(stopped, seconds):
    """Wait for a duration, returning False when the worker is stopped."""
    return not stopped.wait(seconds)


def random_pause(wait, uniform):
    """Wait a randomized human-like delay, stopping when requested."""
    return wait(uniform(0.5, 0.8))


def run_actions(actions, send_key, wait, click_template, randint, keep_running, uniform):
    """Execute configured actions, stopping at the first interruption or failure."""
    for action in actions:
        if not keep_running():
            return False
        if isinstance(action, KeyAction):
            if action.key is not None:
                send_key(action.key)
            else:
                send_key(
                    action.random_choice[
                        randint(0, len(action.random_choice) - 1)
                    ]
                )
        elif isinstance(action, WaitAction):
            if not wait(uniform(action.min_seconds, action.max_seconds)):
                return False
        elif isinstance(action, ClickTemplateAction):
            if not click_template(action.path):
                return False
        elif isinstance(action, RepeatAction):
            for _ in range(action.times):
                if not run_actions(
                    action.actions,
                    send_key,
                    wait,
                    click_template,
                    randint,
                    keep_running,
                    uniform,
                ):
                    return False
    return True


def macro_uses_template_runtime(macro):
    """Return whether any macro phase contains a template click action."""

    def actions_use_templates(actions):
        return any(
            isinstance(action, ClickTemplateAction)
            or (
                isinstance(action, RepeatAction)
                and actions_use_templates(action.actions)
            )
            for action in actions
        )

    return any(
        actions_use_templates(actions)
        for actions in (macro.startup, macro.loop, macro.after_round)
    )


def describe_macro(macro):
    """Return human-readable lines describing a validated macro."""

    def describe_actions(actions, indent=""):
        lines = []
        for action in actions:
            if isinstance(action, KeyAction):
                if action.key is not None:
                    lines.append(f"{indent}key {action.key}")
                else:
                    lines.append(
                        f"{indent}random choice {','.join(action.random_choice)}"
                    )
            elif isinstance(action, WaitAction):
                lines.append(
                    f"{indent}wait {action.min_seconds} to {action.max_seconds} seconds"
                )
            elif isinstance(action, ClickTemplateAction):
                lines.append(f"{indent}click template {action.path}")
            elif isinstance(action, RepeatAction):
                lines.append(f"{indent}repeat {action.times}:")
                lines.extend(describe_actions(action.actions, f"{indent}  "))
        return lines

    lines = []
    for phase, actions in (
        ("startup", macro.startup),
        ("loop", macro.loop),
        ("after_round", macro.after_round),
    ):
        action_lines = describe_actions(actions)
        if action_lines:
            lines.append(f"{phase}: {action_lines[0]}")
            lines.extend(action_lines[1:])
    return tuple(lines)


class MacroRunner:
    """Run at most one interruptible macro worker at a time."""

    def __init__(
        self,
        send_key,
        randint=random.randint,
        event_factory=threading.Event,
        uniform=random.uniform,
        *,
        macro,
        click_template=None,
    ):
        self._send_key = send_key
        self._randint = randint
        self._event_factory = event_factory
        self._uniform = uniform
        self._macro = macro
        self._click_template = click_template
        self._state_lock = threading.Lock()
        self._injection_lock = threading.Lock()
        self._stopped = None
        self._worker = None

    def _send_if_running(self, stopped, key):
        with self._injection_lock:
            if stopped.is_set():
                return False
            self._send_key(key)
            return True

    def _run_action_if_running(self, stopped, action):
        with self._injection_lock:
            if stopped.is_set():
                return False
            action()
            return True

    def _click_template_if_running(self, stopped, path, wait, keep_running):
        if stopped.is_set() or self._click_template is None:
            return False
        if getattr(self._click_template, "uses_runtime_controls", False):
            return self._click_template(
                path,
                runtime_wait=wait,
                runtime_keep_running=keep_running,
                click_action=lambda action: self._run_action_if_running(stopped, action),
            )
        with self._injection_lock:
            if stopped.is_set():
                return False
            return self._click_template(path)

    def _work_macro(self, stopped):
        wait = lambda seconds: wait_until_stopped(stopped, seconds)
        keep_running = lambda: not stopped.is_set()
        send_key = lambda key: self._send_if_running(stopped, key)
        click_template = lambda path: self._click_template_if_running(
            stopped, path, wait, keep_running
        )

        if not random_pause(wait, self._uniform):
            return
        if not run_actions(
            self._macro.startup,
            send_key,
            wait,
            click_template,
            self._randint,
            keep_running,
            self._uniform,
        ):
            stopped.set()
            return
        if not random_pause(wait, self._uniform):
            return

        completed_loops = 0
        while keep_running():
            if not run_actions(
                self._macro.loop,
                send_key,
                wait,
                click_template,
                self._randint,
                keep_running,
                self._uniform,
            ):
                stopped.set()
                return
            completed_loops += 1
            if (
                self._macro.cycles_per_round is not None
                and completed_loops == self._macro.cycles_per_round
            ):
                if not run_actions(
                    self._macro.after_round,
                    send_key,
                    wait,
                    click_template,
                    self._randint,
                    keep_running,
                    self._uniform,
                ):
                    stopped.set()
                    return
                completed_loops = 0

    def enable(self):
        with self._state_lock:
            if self._worker is not None:
                return False
            stopped = self._event_factory()
            worker = threading.Thread(
                target=self._work_macro,
                args=(stopped,),
                daemon=True,
                name="browser-macro-worker",
            )
            self._stopped = stopped
            self._worker = worker
            worker.start()
            return True

    def disable(self):
        with self._state_lock:
            if self._worker is None:
                return False
            with self._injection_lock:
                self._stopped.set()
            self._worker.join()
            self._stopped = None
            self._worker = None
            return True

    def is_enabled(self):
        with self._state_lock:
            return self._worker is not None

    def toggle(self):
        if self.is_enabled():
            self.disable()
            return False
        self.enable()
        return True


def create_keyboard_runtime():
    """Import the keyboard runtime only after CLI macro validation succeeds."""
    from pynput import keyboard

    return keyboard


def create_template_runtime():
    """Initialize pyautogui only for modes that need screen templates."""
    import pyautogui

    screenshot_width, screenshot_height = pyautogui.screenshot().size
    mouse_width, mouse_height = pyautogui.size()
    coordinate_scale = (
        mouse_width / screenshot_width,
        mouse_height / screenshot_height,
    )

    def locate_screen_template(template):
        try:
            return locate_template(pyautogui.locateOnScreen, template)
        except pyautogui.ImageNotFoundException:
            return None

    return pyautogui, coordinate_scale, locate_screen_template


def main(argv=None, randint=random.randint, uniform=random.uniform):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validates and previews configured actions",
    )
    parser.add_argument(
        "--macro",
        type=Path,
        metavar="PATH",
        required=True,
        help="run a validated JSON macro from PATH",
    )
    args = parser.parse_args(argv)

    try:
        macro = load_macro(args.macro)
    except MacroConfigError as error:
        parser.error(str(error))

    if args.dry_run:
        print("Dry run: validated macro.")
        for line in describe_macro(macro):
            print(line)
        return

    keyboard = create_keyboard_runtime()

    needs_template_runtime = macro_uses_template_runtime(macro)
    template_clicker = None
    if needs_template_runtime:
        pyautogui, coordinate_scale, locate_screen_template = create_template_runtime()
        template_clicker = make_template_click_adapter(
            locate_screen_template,
            pyautogui.click,
            pyautogui.moveTo,
            coordinate_scale=coordinate_scale,
        )

    controller = keyboard.Controller()
    runner = MacroRunner(
        lambda name: (
            controller.press(key_for_name(keyboard.Key, name)),
            controller.release(key_for_name(keyboard.Key, name)),
        ),
        randint,
        uniform=uniform,
        macro=macro,
        click_template=template_clicker,
    )
    pressed = set()
    m_down = False

    modifiers = {
        keyboard.Key.ctrl_l,
        keyboard.Key.ctrl_r,
        keyboard.Key.alt_l,
        keyboard.Key.alt_r,
    }

    def on_press(key):
        nonlocal m_down
        if key == keyboard.Key.esc:
            runner.disable()
            return False
        if key in modifiers:
            pressed.add(key)
            return
        if getattr(key, "char", None) == "m" and not m_down:
            m_down = True
            if pressed & {keyboard.Key.ctrl_l, keyboard.Key.ctrl_r} and pressed & {
                keyboard.Key.alt_l,
                keyboard.Key.alt_r,
            }:
                print("Macro enabled." if runner.toggle() else "Macro disabled.")

    def on_release(key):
        nonlocal m_down
        pressed.discard(key)
        if getattr(key, "char", None) == "m":
            m_down = False

    print("Controls: Ctrl+Alt+M toggles the macro; Esc exits.")
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


if __name__ == "__main__":
    main()
