"""Security exceptions must stop suppressing findings when their review expires."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def quality(tmp_path):
    source = Path(__file__).resolve().parents[1] / "scripts/quality.py"
    spec = importlib.util.spec_from_file_location("quality", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    (tmp_path / ".betterleaksignore").write_text("")
    workflows = tmp_path / ".github/workflows"
    workflows.mkdir(parents=True)
    (workflows / "dependabot-automerge.yml").write_text("on: {}\n")
    return module


@pytest.mark.parametrize("kind", ["secret", "workflow"])
@pytest.mark.parametrize("review", ["2000-01-01", "2027-02-31", "not-a-date", ""])
def test_expired_or_invalid_exception_fails(quality, kind, review):
    reason = f"# Artificial fixture for testing the review deadline (review {review})\n"
    if kind == "secret":
        (quality.ROOT / ".betterleaksignore").write_text(
            reason + "a" * 40 + ":tests/fixture.py:generic-password:1\n"
        )
    else:
        (quality.ROOT / ".github/workflows/dependabot-automerge.yml").write_text(
            reason + "# jactionlint ignore=dangerous-triggers\nworkflow_run:\n"
        )
    with pytest.raises(ValueError):
        quality.exceptions()


def test_current_exact_secret_exception_is_accepted(quality):
    (quality.ROOT / ".betterleaksignore").write_text(
        "# Artificial fixture for testing the review deadline (review 9999-01-01)\n"
        + "a" * 40 + ":tests/fixture.py:generic-password:1\n"
    )
    quality.exceptions()


def test_secret_exception_cannot_ignore_a_whole_file(quality):
    (quality.ROOT / ".betterleaksignore").write_text(
        "# Artificial fixture for testing the review deadline (review 9999-01-01)\n"
        "tests/fixture.py\n"
    )
    with pytest.raises(ValueError, match="exact fingerprint"):
        quality.exceptions()


def test_new_workflow_suppression_needs_approval(quality):
    (quality.ROOT / ".github/workflows/dependabot-automerge.yml").write_text(
        "# Artificial fixture for testing the review deadline (review 9999-01-01)\n"
        "# jactionlint ignore=unpinned-uses\n"
    )
    with pytest.raises(ValueError, match="unapproved suppression"):
        quality.exceptions()
