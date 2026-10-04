import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "check_conventional.py"
spec = importlib.util.spec_from_file_location("check_conventional", SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


@pytest.mark.parametrize(
    "subject",
    [
        "feat: initial public release",
        "docs(readme): write the example brief in English",
        "feat(cli): uhura init creates .env with fresh secrets",
        "fix(service)!: bind answers to their call",
        "ci: give the workflow's token read access only",
    ],
)
def test_conventional_subjects_pass(subject):
    assert check.invalid([subject]) == []


@pytest.mark.parametrize(
    "subject",
    [
        # The three subjects that reached GitHub on 2026-10-04 before this check existed.
        "Uhura: place phone calls through an AI voice agent",
        "Add a security policy and restrict CI permissions",
        "Document what a tunnel exposes and list all agent tools",
        "feature: not a known type",
        "docs(README): scopes are lowercase",
        "docs:missing space",
        "fix: ",
    ],
)
def test_other_subjects_fail(subject):
    assert check.invalid([subject]) == [subject]


def test_script_exit_code_and_output():
    run = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="docs: fine\nNot fine\n\n",
        capture_output=True,
        text=True,
    )
    assert run.returncode == 1
    assert "not a Conventional Commit: Not fine" in run.stdout and "docs: fine" not in run.stdout
