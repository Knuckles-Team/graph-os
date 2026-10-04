"""The scanner entry point delegates authentic jscpd provisioning to its owner.

Command doubles exercise wiring and failure propagation without builds or
network access. Actual provenance acceptance belongs to the hosted provider.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
INSTALLER = ROOT / "scripts/install_scanners.sh"

# One recorder implements each external command; it creates no binary/receipt.
RECORDER = r"""
import json
import os
import sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
phase = name
if name == "git":
    phase = {"rev-parse": "revision"}.get(args[2], args[2])
    if any(key.startswith("GIT_") for key in os.environ):
        raise SystemExit("repository selectors leaked to provider Git")
elif name == "python3":
    phase = "toolchain" if args[0] == "-" else "provider"
with open(os.environ["RECORDER_LOG"], "a") as stream:
    stream.write(json.dumps({"name": name, "args": args, "phase": phase}) + "\n")
if os.environ.get("FAIL_PHASE") == phase:
    raise SystemExit(23)
if phase == "revision":
    print(os.environ["PROVIDER_REVISION"])
elif phase == "toolchain":
    code = sys.stdin.read()
    if "from pipelines_hooks.core.jscpd_build import RUST_TOOLCHAIN" not in code:
        raise SystemExit("toolchain must come from provider contract")
    print("9.8.7-test-provider")
elif phase == "provider":
    if args[1] != "--root":
        raise SystemExit("missing explicit provider root")
    print(str(Path(args[2]) / "verified-build" / "bin"))
"""


def _provider_commit() -> str:
    match = re.search(
        r'^provider_commit="([0-9a-f]{40})"$', INSTALLER.read_text(), re.MULTILINE
    )
    assert match is not None, "source provider must have a full immutable commit"
    return match[1]


@pytest.fixture
def commands(tmp_path: Path):
    binaries = tmp_path / "commands"
    binaries.mkdir()
    for name in ("cargo", "git", "rustup", "python3", "npm"):
        command = binaries / name
        command.write_text(f"#!{sys.executable}\n" + RECORDER)
        command.chmod(0o755)
    log = tmp_path / "commands.jsonl"
    env = {
        **os.environ,
        "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
        "RECORDER_LOG": str(log),
        "PROVIDER_REVISION": _provider_commit(),
        # The wrapper must strip these before every provider Git command.
        "GIT_DIR": str(tmp_path / "foreign.git"),
        "GIT_WORK_TREE": str(tmp_path / "foreign-tree"),
        "GIT_CONFIG_COUNT": "0",
    }
    return tmp_path / "scanner root", log, env


def _run(commands, failure: str = ""):
    root, log, env = commands
    result = subprocess.run(
        ["bash", str(INSTALLER), str(root)],
        env={**env, "FAIL_PHASE": failure},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    return result, calls


def test_installer_delegates_to_provider_and_preserves_scanner_paths(commands):
    root, _, _ = commands
    result, calls = _run(commands)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        str(root / "cccc/bin"),
        str(root / "kiss/bin"),
        str(root / "dupehound/bin"),
        str(root / "jscpd/verified-build/bin"),
    ]
    assert [call["phase"] for call in calls] == [
        "cargo",
        "cargo",
        "cargo",
        "init",
        "fetch",
        "checkout",
        "revision",
        "toolchain",
        "rustup",
        "provider",
    ]
    provider = Path(calls[3]["args"][1])
    assert provider.parent == root
    assert not provider.exists()  # private source checkout is cleaned on exit
    assert calls[3]["args"] == ["-C", str(provider), "init", "--quiet"]
    assert calls[4]["args"] == [
        "-C",
        str(provider),
        "fetch",
        "--depth",
        "1",
        "https://github.com/Knuckles-Team/pipelines.git",
        _provider_commit(),
    ]
    assert calls[5]["args"] == [
        "-C",
        str(provider),
        "checkout",
        "--quiet",
        "--detach",
        "FETCH_HEAD",
    ]
    assert calls[6]["args"] == ["-C", str(provider), "rev-parse", "HEAD"]
    assert calls[7]["args"] == ["-", str(provider)]
    assert calls[8]["args"] == [
        "toolchain",
        "install",
        "9.8.7-test-provider",
        "--profile",
        "minimal",
    ]
    assert calls[9]["args"] == [
        str(provider / "scripts/install_jscpd.py"),
        "--root",
        str(root / "jscpd"),
    ]
    assert "Scanner provider revision: " + _provider_commit() in result.stderr
    assert not list(root.rglob("*.provenance.json"))


def test_installer_preserves_other_scanner_pins(commands):
    root, _, _ = commands
    result, calls = _run(commands)
    assert result.returncode == 0, result.stderr
    expected = [
        (
            "cccc",
            "https://github.com/moznion/cccc",
            "d728759323be5d9977b7390a27133e8eaf481f26",
            "cccc-cli",
        ),
        (
            "kiss",
            "https://github.com/Knucklessg1/kiss",
            "7f1c6785697d3fe9a41ceb8b8e5d0f615fb1f3d9",
            "kiss-ai",
        ),
    ]
    for call, (name, url, revision, package) in zip(calls[:2], expected, strict=True):
        assert call["args"] == [
            "install",
            "--quiet",
            "--locked",
            "--git",
            url,
            "--rev",
            revision,
            "--root",
            str(root / name),
            package,
        ]
    assert calls[2]["args"] == [
        "install",
        "--quiet",
        "--locked",
        "--version",
        "0.1.2",
        "--root",
        str(root / "dupehound"),
        "dupehound",
    ]


@pytest.mark.parametrize(
    "phase",
    [
        "cargo",
        "init",
        "fetch",
        "checkout",
        "revision",
        "toolchain",
        "rustup",
        "provider",
    ],
)
def test_installer_fails_closed_before_emitting_paths(commands, phase):
    root, _, _ = commands
    result, calls = _run(commands, phase)
    assert result.returncode == 23
    assert result.stdout == ""
    assert calls[-1]["phase"] == phase
    assert not list(root.glob("pipelines-provider.*"))
    assert not list(root.rglob("*.provenance.json"))


def test_repeat_install_still_delegates_cache_verification(commands):
    for _ in range(2):
        result, _ = _run(commands)
        assert result.returncode == 0, result.stderr
    _, log, _ = commands
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    providers = [call for call in calls if call["phase"] == "provider"]
    assert len(providers) == 2
    assert providers[0]["args"][1:] == providers[1]["args"][1:]


def test_installer_rejects_unexpected_provider_revision(commands):
    root, log, env = commands
    result, calls = _run((root, log, {**env, "PROVIDER_REVISION": "0" * 40}))
    assert result.returncode == 1
    assert "Scanner provider revision mismatch" in result.stderr
    assert result.stdout == ""
    assert calls[-1]["phase"] == "revision"
    assert not list(root.glob("pipelines-provider.*"))
