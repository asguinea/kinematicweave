"""Run the repository's local quality checks."""

from __future__ import annotations

from pathlib import Path
import shlex
import subprocess
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMMANDS: tuple[tuple[str, ...], ...] = (
    ("uv", "lock", "--check"),
    ("uv", "run", "--frozen", "ruff", "format", "--check", "."),
    ("uv", "run", "--frozen", "ruff", "check", "."),
    ("uv", "run", "--frozen", "mypy", "src", "tests", "scripts"),
    ("uv", "run", "--frozen", "pytest"),
)


def run_command(command: tuple[str, ...]) -> int:
    """Run one quality command from the repository root."""
    print(f"$ {shlex.join(command)}", flush=True)
    try:
        completed = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            check=False,
            shell=False,
        )
    except FileNotFoundError:
        print(f"Command not found: {command[0]}", file=sys.stderr)
        return 127
    return completed.returncode


def main() -> int:
    """Run all quality commands, stopping at the first failure."""
    for command in COMMANDS:
        return_code = run_command(command)
        if return_code != 0:
            return return_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
