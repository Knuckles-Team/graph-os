"""Check explicitly supplied, rendered Kubernetes YAML for digest-pinned images.

Run ``python scripts/check_deployment_image_digests.py rendered.yaml [...]``.
Exit 0 means every checked image passed; exit 1 means an input or policy error.
At least one container across the input files must be checked. Non-workload
resources are ignored; this is not a general Kubernetes schema validator.

Only Pod, Deployment, StatefulSet, DaemonSet, ReplicaSet, Job, CronJob and
recursive List resources are traversed, checking containers and initContainers.
Custom resources, typed lists and ephemeralContainers are outside this scope.
Images must use a lowercase repository (optionally a DNS registry and numeric
port) followed by @sha256:<64 lowercase hex>. Tags, including tag@digest, IPv6
registries, other digest algorithms and unrendered templates are unsupported.
YAML duplicate/non-string mapping keys and merge keys are rejected; ordinary
non-cyclic aliases are allowed. No images are resolved or pulled.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_HOST_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
_REGISTRY = rf"{_HOST_LABEL}(?:\.{_HOST_LABEL})*(?::[0-9]+)?"
_IMAGE = re.compile(
    rf"(?P<repository>(?:{_REGISTRY}/)?{_COMPONENT}(?:/{_COMPONENT})*)"
    r"@sha256:[0-9a-f]{64}"
)
_POD_PATHS = {
    "Pod": ("spec",),
    "Deployment": ("spec", "template", "spec"),
    "StatefulSet": ("spec", "template", "spec"),
    "DaemonSet": ("spec", "template", "spec"),
    "ReplicaSet": ("spec", "template", "spec"),
    "Job": ("spec", "template", "spec"),
    "CronJob": ("spec", "jobTemplate", "spec", "template", "spec"),
}


class StrictLoader(yaml.SafeLoader):
    """Preserve mapping ambiguity as an error instead of discarding evidence."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict:
        keys: set[str] = set()
        for key, _ in node.value:
            if key.tag != "tag:yaml.org,2002:str":
                raise ConstructorError(
                    "while reading a mapping",
                    node.start_mark,
                    "only string keys are supported (YAML merge keys are unsupported)",
                    key.start_mark,
                )
            if key.value in keys:
                raise ConstructorError(
                    "while reading a mapping",
                    node.start_mark,
                    f"duplicate key {key.value!r}",
                    key.start_mark,
                )
            keys.add(key.value)
        return super().construct_mapping(node, deep=deep)


def _mapping(value: object, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location}: expected a mapping")
    return value


def _valid_image(image: object) -> bool:
    match = _IMAGE.fullmatch(image) if isinstance(image, str) else None
    if match is None or len(match["repository"]) > 255:
        return False
    first, separator, _ = match["repository"].partition("/")
    if separator and ("." in first or ":" in first or first == "localhost"):
        return re.fullmatch(_REGISTRY, first) is not None
    return True


def _check_containers(pod: dict[str, Any], location: str) -> int:
    count = 0
    for field in ("containers", "initContainers"):
        entries = pod.get(field, [] if field == "initContainers" else None)
        field_location = f"{location}.{field}"
        if not isinstance(entries, list) or (field == "containers" and not entries):
            raise ValueError(
                f"{field_location}: expected a {'nonempty ' if field == 'containers' else ''}list"
            )
        for index, entry in enumerate(entries):
            entry_location = f"{field_location}[{index}]"
            container = _mapping(entry, entry_location)
            if not _valid_image(container.get("image")):
                raise ValueError(
                    f"{entry_location}.image: expected repository@sha256:"
                    "<64 lowercase hex> with no tag (repository max 255 characters)"
                )
            count += 1
    return count


def _check_resource(
    value: object, location: str, ancestors: frozenset[int] = frozenset()
) -> int:
    resource = _mapping(value, location)
    if id(resource) in ancestors:
        raise ValueError(f"{location}: cyclic List aliases are unsupported")
    kind = resource.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError(f"{location}.kind: expected a nonempty string")
    metadata = resource.get("metadata")
    name = (
        metadata.get("name", "<unnamed>") if isinstance(metadata, dict) else "<unnamed>"
    )
    location = f"{location} ({kind}/{name})"
    if kind == "List":
        items = resource.get("items")
        if not isinstance(items, list):
            raise ValueError(f"{location}.items: expected a list")
        lineage = ancestors | {id(resource)}
        return sum(
            _check_resource(item, f"{location}.items[{index}]", lineage)
            for index, item in enumerate(items)
        )
    if kind not in _POD_PATHS:
        return 0
    pod = resource
    for key in _POD_PATHS[kind]:
        location = f"{location}.{key}"
        pod = _mapping(pod.get(key), location)
    return _check_containers(pod, location)


def check_file(path: Path) -> tuple[int, list[str]]:
    """Return the checked count and diagnostics, including file/document context."""
    count = 0
    errors: list[str] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for index, resource in enumerate(
                yaml.load_all(stream, Loader=StrictLoader), 1
            ):
                location = f"{path}: document {index}"
                # Empty documents from rendered YAML separators carry no resources.
                if resource is None:
                    continue
                try:
                    count += _check_resource(resource, location)
                except ValueError as exc:
                    errors.append(str(exc))
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError) as exc:
        errors.append(f"{path}: {exc}")
    return count, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="rendered YAML files")
    args = parser.parse_args(argv)
    total = 0
    errors: list[str] = []
    for path in args.paths:
        count, file_errors = check_file(path)
        total += count
        errors.extend(file_errors)
    if not total and not errors:
        errors.append("no containers checked across the supplied rendered YAML files")
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Checked {total} container images: all digest-pinned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
