"""Check server.json before a tag turns a typo into a failed release.

The MCP Registry validates on publish, which is the worst moment to find out:
the image has already been built and pushed, the tag already exists, and fixing
it costs another tag. These are the constraints that actually rejected a real
release, plus the agreements between fields that no schema can express.

    python -m scripts.validate_server_json
    python -m scripts.validate_server_json --version 0.1.2
"""

import argparse
import json
import pathlib
import re
import sys

# From the published schema at
# https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json
MAX_LENGTHS = {"name": 200, "title": 100, "description": 100, "version": 255}
MIN_LENGTHS = {"name": 3, "title": 1, "description": 1}
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$")
REQUIRED = ("name", "description", "version")

# The registry only accepts images from registries it can verify ownership against.
OCI_REGISTRIES = ("docker.io/", "ghcr.io/", "quay.io/", "mcr.microsoft.com/")
OCI_SUFFIXES = (".pkg.dev", ".azurecr.io")


def problems(server: dict, *, expected_version: str | None, dockerfile: str) -> list[str]:
    found: list[str] = []

    found.extend(f"{field} is required" for field in REQUIRED if not server.get(field))

    for field, limit in MAX_LENGTHS.items():
        value = server.get(field)
        if isinstance(value, str) and len(value) > limit:
            found.append(
                f"{field} is {len(value)} characters, limit is {limit}: {value[:60]}..."
            )
    for field, limit in MIN_LENGTHS.items():
        value = server.get(field)
        if isinstance(value, str) and len(value) < limit:
            found.append(f"{field} is shorter than {limit} characters")

    name = server.get("name", "")
    if name and not NAME_PATTERN.match(name):
        found.append(f"name must be namespace/server in reverse-DNS form, got {name!r}")

    # The registry proves you own the image by reading this label out of it. A
    # mismatch is only discovered at publish time, after the image is pushed.
    if name and f'io.modelcontextprotocol.server.name="{name}"' not in dockerfile:
        found.append(
            f"the Dockerfile must carry LABEL io.modelcontextprotocol.server.name=\"{name}\" "
            "or the registry cannot verify the image"
        )

    version = server.get("version", "")
    if expected_version and version != expected_version:
        found.append(f"version is {version!r} but the tag says {expected_version!r}")

    for index, package in enumerate(server.get("packages", [])):
        where = f"packages[{index}]"
        identifier = package.get("identifier", "")
        if package.get("registryType") == "oci":
            if not identifier.startswith(OCI_REGISTRIES) and not any(
                identifier.split("/")[0].endswith(suffix) for suffix in OCI_SUFFIXES
            ):
                found.append(f"{where}: {identifier!r} is not a registry the MCP Registry accepts")
            _, _, tag = identifier.rpartition(":")
            if not tag or "/" in tag:
                found.append(f"{where}: identifier needs an explicit tag, got {identifier!r}")
            elif version and tag != version:
                found.append(f"{where}: image tag {tag!r} does not match version {version!r}")
        if not package.get("transport", {}).get("type"):
            found.append(f"{where}: transport.type is required")

    if not server.get("packages") and not server.get("remotes"):
        found.append("one of packages or remotes must be present, or nobody can run it")

    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="version the release is for, e.g. 0.1.2")
    parser.add_argument("--path", default="server.json")
    arguments = parser.parse_args()

    path = pathlib.Path(arguments.path)
    try:
        server = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        print(f"{path}: {exc}")
        return 1

    dockerfile = pathlib.Path("Dockerfile")
    found = problems(
        server,
        expected_version=arguments.version,
        dockerfile=dockerfile.read_text() if dockerfile.exists() else "",
    )
    if found:
        print(f"{path} would be rejected by the MCP Registry:")
        for problem in found:
            print(f"  - {problem}")
        return 1
    print(f"{path} looks publishable: {server['name']} {server['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
