"""The reusable build workflow, and the CI that builds the examples through it."""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def load_workflow(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # YAML 1.1 reads the key `on` as the boolean true.
    data["on"] = data.pop(True, data.get("on"))
    return data


def test_build_workflow_defaults_to_this_release():
    """Callers pin build.yml to a tag and get the pipeline from that same tag."""
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    inputs = load_workflow("build.yml")["on"]["workflow_call"]["inputs"]
    assert inputs["linuxlive-ref"]["default"] == f"v{version}"
    assert inputs["linuxlive-repository"]["default"] == "Eric-Ness/linuxlive"
    assert inputs["spec"]["default"] == "linuxlive.yaml"


def test_spike_builds_examples_through_the_reusable_workflow_at_its_own_commit():
    build = load_workflow("spike.yml")["jobs"]["build"]
    assert build["uses"] == "./.github/workflows/build.yml"
    assert build["with"]["linuxlive-ref"] == "${{ github.sha }}"
    assert build["with"]["linuxlive-repository"] == "${{ github.repository }}"
    matrix = build["strategy"]["matrix"]["example"]
    assert matrix, "spike builds no examples"
    for name in matrix:
        assert (ROOT / "examples" / f"{name}.yaml").is_file(), f"matrix entry {name} has no spec in examples/"


def test_spike_publishes_every_example_only_after_all_builds_pass():
    spike = load_workflow("spike.yml")
    publish = spike["jobs"]["publish"]
    assert publish["needs"] == "build"
    assert publish["if"] == "inputs.publish == true || github.event_name == 'schedule'"
    assert publish["strategy"]["matrix"]["example"] == spike["jobs"]["build"]["strategy"]["matrix"]["example"]
    # Publishing needs the ISO artifacts.
    assert "inputs.publish == true" in spike["jobs"]["build"]["with"]["keep-iso"]
    assert "github.event_name == 'schedule'" in spike["jobs"]["build"]["with"]["keep-iso"]


def test_spike_rebuilds_and_publishes_the_examples_monthly():
    spike = load_workflow("spike.yml")
    crons = [c["cron"] for c in spike["on"].get("schedule", [])]
    assert len(crons) == 1 and crons[0].split()[2:] == ["3", "*", "*"], f"expected one monthly schedule, got {crons}"
    # A push must never cancel the monthly publish halfway through its uploads.
    assert "github.event_name" in spike["concurrency"]["group"]
    assert spike["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'push' }}"
