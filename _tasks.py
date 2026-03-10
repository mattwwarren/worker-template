#!/usr/bin/env python3
"""Post-generation tasks for worker template.

This script runs automatically after copier generates a new project.
It handles initial setup tasks that would otherwise be manual.
"""

import shutil
import subprocess
import sys
from pathlib import Path


def log_step(message: str) -> None:
    """Print a step message with formatting."""
    print(f"\n{'=' * 60}")
    print(f"  {message}")
    print(f"{'=' * 60}\n")


def log_success(message: str) -> None:
    """Print a success message."""
    print(f"  [ok] {message}")


def log_error(message: str) -> None:
    """Print an error message."""
    print(f"  [error] {message}", file=sys.stderr)


def log_warning(message: str) -> None:
    """Print a warning message."""
    print(f"  [warn] {message}")


ERROR_DOTENV_NOT_FOUND = "dotenv.example not found - this shouldn't happen"
ERROR_COPY_DOTENV_FAILED = "Failed to copy dotenv.example to .env"


def make_scripts_executable() -> None:
    """Make shell scripts in scripts/ executable."""
    log_step("Step 1/4: Make Scripts Executable")

    scripts_dir = Path("scripts")
    if not scripts_dir.is_dir():
        log_warning("scripts/ directory not found - skipping")
        return

    count = 0
    for script in sorted(scripts_dir.iterdir()):
        if script.suffix == ".sh":
            script.chmod(0o755)
            log_success(f"Made executable: {script}")
            count += 1

    if count == 0:
        log_warning("No .sh scripts found in scripts/")
    else:
        log_success(f"Made {count} scripts executable")


def copy_env_file() -> None:
    """Copy dotenv.example to .env if .env doesn't exist."""
    log_step("Step 2/4: Environment Configuration")

    env_example = Path("dotenv.example")
    env_file = Path(".env")

    if not env_example.exists():
        log_error(ERROR_DOTENV_NOT_FOUND)
        return

    if env_file.exists():
        log_warning(".env already exists - skipping copy")
        log_warning("If you want fresh defaults, run: cp dotenv.example .env")
        return

    try:
        shutil.copy2(env_example, env_file)
        log_success("Created .env from dotenv.example")
        log_warning("IMPORTANT: Edit .env and set DATABASE_URL, RABBITMQ_URL, REDIS_URL")
    except Exception as exc:
        log_error(f"{ERROR_COPY_DOTENV_FAILED}: {exc}")


def run_uv_sync() -> None:
    """Install dependencies using uv sync --dev."""
    log_step("Step 3/4: Install Dependencies")

    uv_path = shutil.which("uv")
    if not uv_path:
        log_warning("uv not found - skipping dependency installation")
        log_warning("Install uv: https://docs.astral.sh/uv/")
        log_warning("Then run: uv sync --dev")
        return

    try:
        subprocess.run(  # noqa: S603 - uv_path from shutil.which(), trusted
            [uv_path, "sync", "--dev"],
            check=True,
            capture_output=True,
            text=True,
        )
        log_success("Dependencies installed successfully")
    except subprocess.CalledProcessError as exc:
        log_error(f"Failed to install dependencies: {exc}")
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        log_warning("You can manually install later with: uv sync --dev")
    except Exception as exc:
        log_error(f"Unexpected error during uv sync: {exc}")


def main() -> int:
    """Run all post-generation tasks.

    Returns:
        Always returns 0 - failures are informational, not critical
    """
    print("\n" + "=" * 60)
    print("  Worker Template - Post-Generation Setup")
    print("=" * 60)

    # Step 1: Make scripts executable
    make_scripts_executable()

    # Step 2: Copy .env file
    copy_env_file()

    # Step 3: Install dependencies
    run_uv_sync()

    # Final summary (Step 4/4)
    log_step("Step 4/4: Setup Complete")
    print("Your worker project is ready for development!")

    # Always return 0 - failures are expected (no database, etc.)
    # and should not prevent copier from completing successfully
    return 0


if __name__ == "__main__":
    sys.exit(main())
