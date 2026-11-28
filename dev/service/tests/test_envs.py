"""Prune verified orphans without touching other checkouts on the Docker daemon."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from dev.service import envs, instance, stores


@pytest.mark.parametrize("option", ["--dry-run", "--yes", "--legacy"])
def test_prune_preserves_existing_and_locked_checkouts_and_other_projects(
    option: str,
    checkout_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = instance.ensure_instance(checkout_root)
    missing = tmp_path / "missing"
    missing.mkdir()
    stale = instance.ensure_instance(missing)
    shutil.rmtree(missing)
    offline = tmp_path / "offline"
    offline.mkdir()
    locked = instance.ensure_instance(offline)
    shutil.rmtree(offline)
    gone = str(tmp_path / "no-stores")
    instance.save_registry(
        {**instance.registered_instances(), gone: instance.Instance("123abcdef456", gone, stale.ports)}
    )

    unregistered = tmp_path / "worktree"
    unregistered.mkdir()
    external = tmp_path / "external checkout"
    external.mkdir()
    project = stores.PROJECT_PREFIX
    worktree_project = project + instance.identity(unregistered)
    stale_project = stores.project(stale)
    locked_project = stores.project(locked)
    orphan_project = project + "123456789abc"
    legacy_project = project + "987654321abc"
    external_project = project + "abcdefabcdef"
    removed: list[str] = []

    def docker(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        args = command[1:]
        if args[:1] == ["ps"]:
            stdout = "".join(
                f"{json.dumps(name)}\t{json.dumps(str(root / 'dev/service'))}\n"
                for name, root in (
                    (stores.project(live), checkout_root),
                    (external_project, external),
                    (orphan_project, tmp_path / "removed-external"),
                    ("compose", tmp_path / "unrelated"),
                )
            )
        elif args[:2] == ["volume", "ls"] and args[3] == f"label={stores.PROJECT_LABEL}":
            stdout = "\n".join((legacy_project, locked_project, worktree_project, stale_project, project + "other"))
        elif args[:2] == ["network", "ls"]:
            stdout = f"{stale_project}\nagent-foundation-local-langfuse-v2\n"
        else:
            pytest.fail(f"Unexpected Docker command: {command}")
        return subprocess.CompletedProcess(command, 0, stdout)

    monkeypatch.setattr(stores.subprocess, "run", docker)
    monkeypatch.setattr(stores, "delete_orphan", removed.append)
    monkeypatch.setattr(
        envs,
        "_worktrees",
        lambda: {str(unregistered): envs.Worktree("branch"), str(offline): envs.Worktree("offline", locked=True)},
    )
    arguments = ["--yes", "--project", legacy_project] if option == "--legacy" else [option]
    monkeypatch.setattr(sys, "argv", ["envs", "prune", *arguments])
    records_before = instance.registered_instances()
    envs.main()

    output = capsys.readouterr().out
    assert all(name in output for name in (stale_project, orphan_project, legacy_project, str(missing), gone))
    assert all(
        name not in output for name in (stores.project(live), worktree_project, external_project, locked_project)
    )
    if option == "--dry-run":
        assert not removed
        assert instance.registered_instances() == records_before
    else:
        expected = [stale_project, orphan_project]
        if option == "--legacy":
            expected.append(legacy_project)
        else:
            assert f"Skipped {legacy_project}" in output
        assert removed == sorted(expected)
        assert instance.registered_instances() == {str(checkout_root): live, str(offline): locked}
        assert instance.load_instance(checkout_root) == live


@pytest.mark.parametrize("options", [[], ["--dry-run", "--yes"], ["--dry-run", "--project", "dev"]])
def test_prune_requires_exactly_one_confirmation_option(options: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["envs", "prune", *options])
    monkeypatch.setattr(envs, "prune", lambda **_: pytest.fail("prune must not run"))
    with pytest.raises(SystemExit) as exc:
        envs.main()
    assert exc.value.code == 2


def test_explicit_selection_cannot_remove_a_project_whose_checkout_exists(
    checkout_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = stores.PROJECT_PREFIX + "123456789abc"
    monkeypatch.setattr(envs, "_worktrees", dict)
    monkeypatch.setattr(stores, "discover_projects", lambda: {name: {checkout_root}})
    monkeypatch.setattr(stores, "delete_orphan", lambda _: pytest.fail("existing checkout must be preserved"))
    with pytest.raises(ValueError, match="Not unattributed local projects"):
        envs.prune(dry_run=False, projects={name})


def test_worktree_list_preserves_locked_paths_and_parses_nul_delimiters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "offline\nworktree"

    def git(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert command[-2:] == ["--porcelain", "-z"]
        return subprocess.CompletedProcess(command, 0, f"worktree {path}\0HEAD 123\0locked Offline drive\0\0")

    monkeypatch.setattr(envs.subprocess, "run", git)
    assert envs._worktrees() == {str(path): envs.Worktree(None, locked=True)}


def test_delete_orphan_removes_only_labelled_volumes(monkeypatch: pytest.MonkeyPatch) -> None:
    name = stores.PROJECT_PREFIX + "123456789abc"
    calls: list[list[str]] = []

    def docker(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        stdout = "old_data\n" if command[1:3] == ["volume", "ls"] else ""
        return subprocess.CompletedProcess(command, 0, stdout)

    monkeypatch.setattr(stores.subprocess, "run", docker)
    stores.delete_orphan(name)

    assert calls[0][1:6] == ["compose", "--env-file", "/dev/null", "--project-name", name]
    assert calls[0][-2:] == ["down", "--remove-orphans"]
    assert calls[1] == [
        "docker",
        "volume",
        "ls",
        "--filter",
        f"label={stores.PROJECT_LABEL}={name}",
        "--format",
        "{{.Name}}",
    ]
    assert calls[2] == ["docker", "volume", "rm", "--", "old_data"]
