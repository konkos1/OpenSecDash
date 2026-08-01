#!/usr/bin/env python3
"""Generate deterministic application third-party notices from release inputs."""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import difflib
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import sys
import tomllib
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPOSITORY_ROOT / "third_party" / "app-components.toml"
PYPROJECT_PATH = REPOSITORY_ROOT / "backend" / "pyproject.toml"
LOCK_PATH = REPOSITORY_ROOT / "backend" / "uv.lock"
MARKDOWN_OUTPUT = REPOSITORY_ROOT / "THIRD_PARTY_NOTICES.md"
JSON_OUTPUT = REPOSITORY_ROOT / "backend" / "app" / "legal" / "third-party-notices.json"

LICENSE_ALIASES = {
    "annotated-types": "MIT",
    "itsdangerous": "BSD-3-Clause",
    "jinja2": "BSD-3-Clause",
}
ALLOWED_LICENSES = {
    "0BSD",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "EDL-1.0",
    "MIT",
    "MPL-2.0",
    "PSF-2.0",
}
LICENSE_FILE_PATTERN = re.compile(
    r"^(license|licence|copying|copyright)([._-].*)?$",
    re.IGNORECASE,
)
NOTICE_FILE_PATTERN = re.compile(r"^notice([._-].*)?$", re.IGNORECASE)


@dataclass(frozen=True)
class LicenseDocument:
    name: str
    text: str
    kind: str


@dataclass(frozen=True)
class Component:
    name: str
    version: str
    license_expression: str
    homepage: str
    source: str
    source_archive: str
    distributed_files: tuple[str, ...]
    license_documents: tuple[LicenseDocument, ...]
    source_required: bool
    ecosystem: str


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _runtime_roots() -> list[str]:
    project = _read_toml(PYPROJECT_PATH)["project"]
    return [Requirement(value).name for value in project["dependencies"]]


def _runtime_distributions() -> list[metadata.Distribution]:
    pending = deque(_runtime_roots())
    found: dict[str, metadata.Distribution] = {}
    while pending:
        requested_name = pending.popleft()
        distribution = metadata.distribution(requested_name)
        normalized_name = canonicalize_name(distribution.metadata["Name"])
        if normalized_name in found:
            continue
        found[normalized_name] = distribution
        for raw_requirement in distribution.requires or []:
            requirement = Requirement(raw_requirement)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return sorted(found.values(), key=lambda item: item.metadata["Name"].casefold())


def _locked_sources() -> dict[tuple[str, str], str]:
    sources: dict[tuple[str, str], str] = {}
    for package in _read_toml(LOCK_PATH)["package"]:
        sdist = package.get("sdist")
        if sdist:
            key = (canonicalize_name(package["name"]), package["version"])
            sources[key] = sdist["url"]
    return sources


def _project_url(distribution: metadata.Distribution) -> str:
    priorities = ("Source", "Repository", "Homepage", "Home")
    urls: dict[str, str] = {}
    for value in distribution.metadata.get_all("Project-URL", []):
        label, separator, url = value.partition(",")
        if separator:
            urls[label.strip()] = url.strip()
    for label in priorities:
        if label in urls:
            return urls[label]
    return distribution.metadata.get("Home-page", "")


def _license_expression(
    distribution: metadata.Distribution,
    overrides: dict[str, Any],
) -> str:
    normalized_name = canonicalize_name(distribution.metadata["Name"])
    override = overrides.get(normalized_name, {})
    if override.get("license"):
        expression = override["license"]
    elif LICENSE_ALIASES.get(normalized_name):
        expression = LICENSE_ALIASES[normalized_name]
    else:
        expression = distribution.metadata.get("License-Expression", "").strip()
        if not expression:
            expression = distribution.metadata.get("License", "").strip()
    if not expression or expression.upper() == "UNKNOWN":
        raise ValueError(f"{distribution.metadata['Name']} {distribution.version} has no declared license")
    identifiers = set(re.findall(r"[A-Za-z0-9.-]+", expression)) - {"AND", "OR", "WITH"}
    unsupported = identifiers - ALLOWED_LICENSES
    if unsupported:
        raise ValueError(
            f"{distribution.metadata['Name']} {distribution.version} uses unreviewed license identifiers: "
            f"{', '.join(sorted(unsupported))}"
        )
    return expression


def _read_distribution_documents(
    distribution: metadata.Distribution,
    override: dict[str, Any],
) -> list[LicenseDocument]:
    selected_names = set(override.get("license_file_names", []))
    documents: list[LicenseDocument] = []
    seen: set[tuple[str, str]] = set()
    for relative_path in distribution.files or []:
        basename = Path(str(relative_path)).name
        is_notice = bool(NOTICE_FILE_PATTERN.match(basename))
        is_license = bool(LICENSE_FILE_PATTERN.match(basename))
        if not is_license and not is_notice:
            continue
        if selected_names and is_license and basename not in selected_names:
            continue
        path = Path(distribution.locate_file(relative_path))
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as error:
            raise ValueError(f"Cannot read license evidence from {path}: {error}") from error
        if not text:
            continue
        key = (basename, text)
        if key in seen:
            continue
        seen.add(key)
        documents.append(
            LicenseDocument(
                name=basename,
                text=text,
                kind="notice" if is_notice else "license",
            )
        )
    for relative_path in override.get("extra_license_files", []):
        path = REPOSITORY_ROOT / relative_path
        text = path.read_text(encoding="utf-8").strip()
        documents.append(LicenseDocument(name=path.name, text=text, kind="license"))
    if not any(document.kind == "license" for document in documents):
        raise ValueError(
            f"{distribution.metadata['Name']} {distribution.version} has no readable license file"
        )
    return documents


def _python_components(manifest: dict[str, Any]) -> list[Component]:
    overrides = {
        canonicalize_name(name): value
        for name, value in manifest.get("python_overrides", {}).items()
    }
    locked_sources = _locked_sources()
    components: list[Component] = []
    for distribution in _runtime_distributions():
        normalized_name = canonicalize_name(distribution.metadata["Name"])
        override = overrides.get(normalized_name, {})
        expression = _license_expression(distribution, overrides)
        source_archive = locked_sources.get((normalized_name, distribution.version), "")
        if not source_archive:
            raise ValueError(
                f"{distribution.metadata['Name']} {distribution.version} has no locked source archive"
            )
        components.append(
            Component(
                name=distribution.metadata["Name"],
                version=distribution.version,
                license_expression=expression,
                homepage=_project_url(distribution),
                source=_project_url(distribution),
                source_archive=source_archive,
                distributed_files=(),
                license_documents=tuple(
                    _read_distribution_documents(distribution, override)
                ),
                source_required="MPL-2.0" in expression or "EPL-2.0" in expression,
                ecosystem="Python",
            )
        )
    return components


def _validate_first_party_assets(manifest: dict[str, Any]) -> None:
    for pattern in manifest["first_party"]["assets"]:
        if not list(REPOSITORY_ROOT.glob(pattern)):
            raise ValueError(f"First-party asset declaration does not match any files: {pattern}")


def _browser_components(manifest: dict[str, Any]) -> list[Component]:
    components: list[Component] = []
    for entry in manifest["components"]:
        distributed_files = tuple(entry["distributed_files"])
        expected_hashes = entry.get("sha256", [])
        if expected_hashes and len(expected_hashes) != len(distributed_files):
            raise ValueError(f"{entry['name']} has a mismatched sha256 declaration")
        for index, relative_path in enumerate(distributed_files):
            path = REPOSITORY_ROOT / relative_path
            if not path.is_file():
                raise ValueError(f"Missing distributed file for {entry['name']}: {relative_path}")
            if expected_hashes:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != expected_hashes[index]:
                    raise ValueError(
                        f"Unexpected content for {relative_path}: {digest}; update only with the upstream version"
                    )
        documents = []
        for relative_path in entry["license_files"]:
            path = REPOSITORY_ROOT / relative_path
            documents.append(
                LicenseDocument(
                    name=path.name,
                    text=path.read_text(encoding="utf-8").strip(),
                    kind="license",
                )
            )
        components.append(
            Component(
                name=entry["name"],
                version=entry["version"],
                license_expression=entry["license"],
                homepage=entry["homepage"],
                source=entry["source"],
                source_archive=entry["source_archive"],
                distributed_files=distributed_files,
                license_documents=tuple(documents),
                source_required=False,
                ecosystem="Browser",
            )
        )
    return components


def _component_json(component: Component) -> dict[str, Any]:
    return {
        "name": component.name,
        "version": component.version,
        "license": component.license_expression,
        "ecosystem": component.ecosystem,
        "homepage": component.homepage,
        "source": component.source,
        "source_archive": component.source_archive,
        "source_required": component.source_required,
        "distributed_files": list(component.distributed_files),
        "documents": [
            {"name": document.name, "kind": document.kind, "text": document.text}
            for document in component.license_documents
        ],
    }


def _json_output(components: list[Component], manifest: dict[str, Any]) -> str:
    data = {
        "schema_version": 1,
        "scope": "OpenSecDash application and application layer of the container image",
        "generated_from": [
            "backend/pyproject.toml",
            "backend/uv.lock",
            "third_party/app-components.toml",
            "installed runtime package metadata",
        ],
        "first_party_assets": manifest["first_party"]["assets"],
        "container_base": {
            "description": "Python slim image and its Debian packages",
            "license_location": "/usr/share/doc/<package>/copyright",
            "inventory": "container-os-packages.json and opensecdash.spdx.json attached to each release",
            "source_information": "THIRD_PARTY_SOURCE.md and the release source report",
        },
        "components": [_component_json(component) for component in components],
    }
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _markdown_output(components: list[Component], manifest: dict[str, Any]) -> str:
    python_components = [item for item in components if item.ecosystem == "Python"]
    browser_components = [item for item in components if item.ecosystem == "Browser"]
    lines = [
        "# OpenSecDash third-party notices",
        "",
        "This generated file covers the application and the application layer of the",
        "OpenSecDash container image. It is derived from the locked runtime environment",
        "and `third_party/app-components.toml`; do not edit it by hand.",
        "",
        "The OpenSecDash source itself and the explicitly declared first-party SVG assets",
        "are licensed under GNU AGPL-3.0 as described in `LICENSE`.",
        "",
        "## Python runtime packages",
        "",
        "| Package | Version | License | Exact source archive |",
        "| --- | --- | --- | --- |",
    ]
    for component in python_components:
        lines.append(
            f"| {component.name} | {component.version} | {component.license_expression} | "
            f"[source]({component.source_archive}) |"
        )
    lines.extend(
        [
            "",
            "## Browser components shipped with the application",
            "",
            "| Component | Version | License | Distributed files | Exact source archive |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for component in browser_components:
        files = "<br>".join(f"`{value}`" for value in component.distributed_files)
        lines.append(
            f"| {component.name} | {component.version} | {component.license_expression} | "
            f"{files} | [source]({component.source_archive}) |"
        )
    lines.extend(
        [
            "",
            "## Container base",
            "",
            "The Python slim base and its Debian packages are inventoried from the final",
            "release image. Their verbatim copyright and license files remain available at",
            "`/usr/share/doc/<package>/copyright`. Every release publishes",
            "`container-os-packages.json` and `opensecdash.spdx.json`; matching source",
            "information is described in `THIRD_PARTY_SOURCE.md`.",
            "",
            "## First-party asset declaration",
            "",
        ]
    )
    lines.extend(f"- `{pattern}`" for pattern in manifest["first_party"]["assets"])
    lines.extend(["", "## License and notice texts", ""])
    for component in components:
        lines.extend(
            [
                f"### {component.name} {component.version}",
                "",
                f"License: `{component.license_expression}`",
                "",
            ]
        )
        if component.homepage:
            lines.extend([f"Project: {component.homepage}", ""])
        if component.source_required:
            lines.extend(
                [
                    "The covered source code is available from the exact source archive",
                    f"listed above: {component.source_archive}",
                    "",
                ]
            )
        for document in component.license_documents:
            lines.extend([f"#### {document.name}", ""])
            lines.extend(f"    {line}" if line else "" for line in document.text.splitlines())
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _check_or_write(path: Path, expected: str, check: bool) -> bool:
    actual = path.read_text(encoding="utf-8") if path.exists() else ""
    if actual == expected:
        return True
    if check:
        difference = difflib.unified_diff(
            actual.splitlines(),
            expected.splitlines(),
            fromfile=str(path),
            tofile=f"generated {path}",
            lineterm="",
        )
        print("\n".join(list(difference)[:200]), file=sys.stderr)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(expected, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when committed notices differ from generated output",
    )
    args = parser.parse_args()

    manifest = _read_toml(MANIFEST_PATH)
    _validate_first_party_assets(manifest)
    components = sorted(
        _python_components(manifest) + _browser_components(manifest),
        key=lambda item: (item.ecosystem, item.name.casefold(), item.version),
    )
    results = [
        _check_or_write(MARKDOWN_OUTPUT, _markdown_output(components, manifest), args.check),
        _check_or_write(JSON_OUTPUT, _json_output(components, manifest), args.check),
    ]
    if not all(results):
        print(
            "Third-party notices are stale; run backend/.venv/bin/python "
            "scripts/generate_third_party_notices.py",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
