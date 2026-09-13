# Screen Macro

A local, JSON-driven screen-automation tool for repetitive desktop tasks. A
macro can send keys, wait for a fixed or random interval, and click a visible
image template. It is useful wherever the same on-screen sequence needs to be
repeated; it is not tied to a browser or a particular website.

## Quick start

Python 3.14+ and [uv](https://docs.astral.sh/uv/) are required. The bundled
example is a template: copy it, adjust its actions and assets for your task,
then run it with:

```sh
uv run --python 3.14 macro.py --macro macros/example/macro.json
```

Preview the validated execution plan without listening for or sending keyboard
input:

```sh
uv run --python 3.14 macro.py --macro macros/example/macro.json --dry-run
```

Press `Ctrl+Alt+M` to toggle the macro and `Esc` to exit. On macOS, grant
Accessibility permission to the terminal application that runs it:
**System Settings → Privacy & Security → Accessibility**. Macros containing a
`click_template` action also require Screen Recording permission.

## Write a macro

Place each macro in its own directory with a `macro.json` file and any image
assets it needs. Template paths are relative to the JSON file.

```json
{
  "version": 1,
  "loop": [
    { "type": "key", "key": "right" },
    { "type": "wait", "min_seconds": 0.5, "max_seconds": 0.8 },
    { "type": "click_template", "path": "assets/continue.png" }
  ]
}
```

Supported actions are:

- `key`: sends one character, `right`, or `enter`; use `random_choice` to
  select from a list of supported keys.
- `wait`: pauses for a fixed duration or a random duration between
  `min_seconds` and `max_seconds`.
- `click_template`: waits up to 15 seconds for an image template, then clicks
  its center.
- `repeat`: runs a non-empty action list a positive number of times; repeats
  can nest up to three levels.

An optional `startup` list runs once when the macro is enabled. Use `rounds`
with an `after_round` list to run actions after a configured number of loop
iterations. The full macro and its template paths are validated before any
keyboard or screen automation begins.

Run a macro directly, or use the included Justfile:

```sh
uv run --python 3.14 macro.py --macro path/to/macro.json
just run macros/example
```

Use a tightly cropped screenshot of the target control for every image
template. Keep the target application's display scale and appearance aligned
with the reference image for reliable matching.

## Local macros

Personal macro directories are local to the machine where you create them.
`macros/*` is ignored by Git by default, so their
configuration and image assets are not shared accidentally. `macros/example/`
is the shareable exception; copy it when you need a starting point.

## Development

```sh
just test
just format-check
just secrets
```

Install [Gitleaks](https://github.com/gitleaks/gitleaks) for `just secrets`,
which scans the working tree (including untracked files) and redacts detected
values. Run `just secrets-history` to scan committed history.
