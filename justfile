# Common development and macro commands. Run `just --list` to see these recipes.

default:
    @just --list

test:
    uv run --python 3.14 python -m unittest discover -v

dry-run:
    uv run --python 3.14 macro.py --macro macros/example/macro.json --dry-run

example:
    uv run --python 3.14 macro.py --macro macros/example/macro.json

run macro:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -d "{{macro}}" ]]; then
      macro_path="{{macro}}/macro.json"
    elif [[ -d "macros/{{macro}}" ]]; then
      macro_path="macros/{{macro}}/macro.json"
    else
      macro_path="{{macro}}"
    fi
    uv run --python 3.14 macro.py --macro "$macro_path"

format-check:
    uv run --python 3.14 python -m compileall -q macro.py macro_config.py tests

# Scan the working tree, including untracked files, before committing or sharing.
secrets:
    gitleaks dir . --no-banner --redact --log-level=warn

# Scan every committed revision after the repository has its first commit.
secrets-history:
    gitleaks git . --no-banner --redact --log-level=warn
