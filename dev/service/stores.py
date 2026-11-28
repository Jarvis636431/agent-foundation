"""This checkout's PostgreSQL and Redis: one Compose project whose volumes only this checkout uses. Stdlib only."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from dev.service.checkout import ROOT
from dev.service.instance import Instance, require_free

COMPOSE_FILE = ROOT / "dev/service/compose.yaml"
PROJECT_PREFIX = "a13n-service-dev-"
PROJECT_LABEL = "com.docker.compose.project"
WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"
PROJECT_NAME = re.compile(rf"{PROJECT_PREFIX}[0-9a-f]{{12}}")


def project(instance: Instance) -> str:
    return PROJECT_PREFIX + instance.id


def compose(instance: Instance, *arguments: str, capture: bool = False) -> str:
    ports = {"A13N_DEV_POSTGRES_PORT": str(instance.ports.postgres), "A13N_DEV_REDIS_PORT": str(instance.ports.redis)}
    return _compose(project(instance), *arguments, capture=capture, env={**os.environ, **ports})


def _compose(name: str, *arguments: str, capture: bool = False, env: dict[str, str] | None = None) -> str:
    command = ["docker", "compose", "--env-file", os.devnull, "--project-name", name]
    try:
        result = subprocess.run(
            [*command, "--file", str(COMPOSE_FILE), *arguments],
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
        )
    except FileNotFoundError:
        raise RuntimeError("Docker is required for local development; install it and retry") from None
    return result.stdout or ""


def running(instance: Instance) -> set[str]:
    return set(compose(instance, "ps", "--services", "--status", "running", capture=True).split())


def start(instance: Instance) -> None:
    """Start the stores; an assigned port another process holds fails instead of moving."""
    stopped = {"postgres", "redis"} - running(instance)
    require_free({name: port for name, port in instance.ports.named().items() if name in stopped})
    compose(instance, "up", "--detach", "--wait")


def stop(instance: Instance) -> None:
    compose(instance, "stop")


def delete(instance: Instance) -> None:
    """Remove this checkout's containers and volumes; nothing else is touched."""
    compose(instance, "down", "--volumes", "--remove-orphans")


def discover_projects() -> dict[str, set[Path | None]]:
    """List store projects by Compose label, with checkout paths when still recorded on containers."""
    projects: dict[str, set[Path | None]] = {}
    container_format = f'{{{{json (.Label "{PROJECT_LABEL}")}}}}\t{{{{json (.Label "{WORKING_DIR_LABEL}")}}}}'
    for line in _docker_list("ps", "--all", label=PROJECT_LABEL, format_=container_format):
        project_label, directory_label = line.split("\t", 1)
        name = json.loads(project_label)
        if PROJECT_NAME.fullmatch(name):
            roots = projects.setdefault(name, set())
            directory = Path(json.loads(directory_label))
            # An unrecognised container is not evidence that an environment has been abandoned.
            roots.add(
                directory.parents[1] if directory.is_absolute() and directory.parts[-2:] == ("dev", "service") else None
            )
    for command in (("volume", "ls"), ("network", "ls")):
        for name in _docker_list(*command, label=PROJECT_LABEL, format_=f'{{{{.Label "{PROJECT_LABEL}"}}}}'):
            if PROJECT_NAME.fullmatch(name):
                projects.setdefault(name, set())
    return projects


def _docker_list(*arguments: str, label: str, format_: str) -> list[str]:
    return subprocess.run(
        ["docker", *arguments, "--filter", f"label={label}", "--format", format_],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.splitlines()


def delete_orphan(name: str) -> None:
    """Remove an orphan's Compose services and all its labelled volumes, including obsolete ones."""
    if not PROJECT_NAME.fullmatch(name):
        raise ValueError(f"Not a local Service Compose project: {name}")
    _compose(name, "down", "--remove-orphans")
    volumes = _docker_list("volume", "ls", label=f"{PROJECT_LABEL}={name}", format_="{{.Name}}")
    if volumes:
        # Docker refuses to remove a volume still attached to another container.
        subprocess.run(["docker", "volume", "rm", "--", *volumes], check=True)
