"""The narrated view reports actual experiment data, without replacing JSON."""
import copy
import json
import subprocess
import sys

import pytest

from codeintel.lab.presentation import render_demo, render_evaluation
from codeintel.lab.scenarios import evaluate, run_demo


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    return run_demo(tmp_path_factory.mktemp("narration") / "demo")


@pytest.fixture(scope="module")
def evaluation():
    return evaluate()


def test_demo_narrates_observed_source_and_limits_without_mutating(demo):
    original = copy.deepcopy(demo)
    text = render_demo(demo)
    assert demo == original
    assert "booking.py:4-10" in text
    assert "INDEX_REQUIRED" in text and "STALE_SOURCE" in text
    assert "exact_short_name" in text and "Identical fragments omitted: 1" in text
    assert "return available + requested" in text and "Now: return available - requested" in text
    for i in (2, 5):
        assert f"{demo['steps'][i]['telemetry']['packet_bytes']} / 4096" in text
    assert "did not diagnose this bug" in text
    assert "not better agent answers or token/cost savings" in text
    assert text.endswith("\n")


def test_evaluation_shows_both_arms_and_scope(evaluation):
    original = copy.deepcopy(evaluation)
    text = render_evaluation(evaluation)
    assert evaluation == original
    assert "ranked: 12 / 12" in text and "literal_scan: 12 / 12" in text
    assert "Contracts: 10 / 10 passed" in text
    assert "BUDGET_EXHAUSTED" in text and "model calls: 0" in text
    assert "no blind holdout" in text


def test_evaluation_cannot_hide_failed_checks(evaluation):
    report = copy.deepcopy(evaluation)
    report["rows"][0]["correct"] = False
    report["checks"][0]["pass"] = False
    report["status"] = "FAIL"
    text = render_evaluation(report)
    assert "ranked: 11 / 12" in text
    assert "Contracts: 9 / 10 passed" in text
    assert "FAIL duplicates" in text and "Result: FAIL" in text


@pytest.mark.parametrize("command", ["demo", "evaluate"])
@pytest.mark.parametrize("format", [None, "json", "text"])
def test_cli_formats(command, format, tmp_path):
    argv = [sys.executable, "-m", "codeintel.lab.cli", command]
    if command == "demo":
        argv += ["--workspace", str(tmp_path / "demo")]
    if format:
        argv += ["--format", format]
    run = subprocess.run(argv, capture_output=True, timeout=20)
    assert run.returncode == 0, run.stderr
    if format == "text":
        assert b"Result: PASS" in run.stdout
    else:
        report = json.loads(run.stdout)
        assert report["status"] == "PASS"


def test_text_view_preserves_workspace_collision_error(tmp_path):
    run = subprocess.run([sys.executable, "-m", "codeintel.lab.cli", "demo",
                          "--workspace", str(tmp_path), "--format", "text"],
                         capture_output=True, timeout=20)
    assert run.returncode == 2 and run.stdout == b""
    assert json.loads(run.stderr)["code"] == "WORKSPACE_EXISTS"


def test_demo_text_preserves_alias_and_coverage_information(tmp_path):
    from codeintel.lab.scenarios import run_demo
    text = render_demo(run_demo(tmp_path / 'aliases'))
    assert 'locations retained as aliases' in text
    assert 'Aliases: duplicate.py; known symbol covered=true' in text
