"""Small adapters for checks that need temporary files or Git's file inventory."""

import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def exceptions():
    """Every ignored secret fingerprint requires a reason and an unexpired date."""
    reason = ""
    for number, line in enumerate((ROOT / ".betterleaksignore").read_text().splitlines(), 1):
        if line.startswith("#"):
            reason = line[1:].strip()
        elif line.strip():
            match = re.fullmatch(r"(.{20,}) \(review (\d{4}-\d{2}-\d{2})\)", reason)
            if not match:
                raise ValueError(f".betterleaksignore:{number}: missing reason/review date")
            review = datetime.strptime(match[2], "%Y-%m-%d").date()
            if review <= datetime.now(UTC).date():
                raise ValueError(f".betterleaksignore:{number}: review expired on {review}")
            if not re.fullmatch(r"[0-9a-f]{40}:[^:]+:[^:]+:\d+", line):
                raise ValueError(f".betterleaksignore:{number}: expected an exact fingerprint")
            reason = ""

    workflow = ROOT / ".github/workflows/dependabot-automerge.yml"
    lines = workflow.read_text().splitlines()
    for number, line in enumerate(lines):
        if "jactionlint ignore=" not in line:
            continue
        if line.strip() != "# jactionlint ignore=dangerous-triggers":
            raise ValueError(f"{workflow}:{number + 1}: unapproved suppression")
        reason = lines[number - 1].strip() if number else ""
        match = re.fullmatch(r"# (.{20,}) \(review (\d{4}-\d{2}-\d{2})\)", reason)
        if not match:
            raise ValueError(f"{workflow}:{number + 1}: missing reason/review date")
        review = datetime.strptime(match[2], "%Y-%m-%d").date()
        if review <= datetime.now(UTC).date():
            raise ValueError(f"{workflow}:{number + 1}: review expired on {review}")


def secrets():
    exceptions()
    run("betterleaks", "git", ".", "--redact", "--no-banner", "--ignore-gitleaks-allow")
    # Include current edits and new, non-ignored files as well as committed history.
    paths = run(
        "git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
        capture_output=True,
    ).stdout.decode().split("\0")
    with tempfile.TemporaryDirectory(prefix="tradingagents-secrets-") as directory:
        for name in set(paths) - {""}:
            source = ROOT / name
            if source.is_file() and not source.is_symlink():
                target = Path(directory) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        run(
            "betterleaks", "dir", directory, "--redact", "--no-banner",
            "--ignore-gitleaks-allow",
        )


def wheel_install(directory):
    """Check distribution contents, not imports accidentally served by the checkout."""
    destination = Path(directory)
    run("uv", "build", "--out-dir", str(destination / "dist"))
    wheels = list((destination / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("expected exactly one wheel")
    env = destination / "venv"
    run("uv", "venv", "--python", sys.executable, str(env))
    python = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    run("uv", "pip", "install", "--python", str(python), str(wheels[0]))
    run(
        str(python), "-c",
        "import importlib.resources as r; import tradingagents, cli.main, tradernet_global; "
        "assert (r.files('cli') / 'static' / 'welcome.txt').is_file(); "
        "print('wheel install and imports OK')",
        cwd=directory,
    )
    return python


def build():
    with tempfile.TemporaryDirectory(prefix="tradingagents-build-") as directory:
        wheel_install(directory)


def audit_install():
    # pip-based consumers may resolve versions different from uv.lock.
    with tempfile.TemporaryDirectory(prefix="tradingagents-audit-") as directory:
        python = wheel_install(directory)
        frozen = run(
            "uv", "pip", "freeze", "--python", str(python), capture_output=True, text=True,
        ).stdout
        requirements = Path(directory) / "requirements.txt"
        requirements.write_text("\n".join(
            line for line in frozen.splitlines() if not line.startswith("tradingagents ")
        ) + "\n")
        run("osv-scanner", "scan", "source", "--lockfile", str(requirements))


if __name__ == "__main__":
    commands = {"exceptions": exceptions, "secrets": secrets, "build": build,
                "audit-install": audit_install}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit(f"usage: {sys.argv[0]} {{{','.join(commands)}}}")
    try:
        commands[sys.argv[1]]()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from None
