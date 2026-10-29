"""Prepare application versions without changing the resolved dependency graph."""

import argparse
import difflib
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

DEVELOPMENT_VERSION = "0.0.0-dev"
NUMBER = r"(?:0|[1-9][0-9]*)"
RELEASE_TAG = re.compile(
    rf"v(?P<version>{NUMBER}\.{NUMBER}\.{NUMBER}(?:-(?:alpha|beta|rc)\.{NUMBER})?)",
    re.ASCII,
)


def version_from_tag(tag):
    match = RELEASE_TAG.fullmatch(tag)
    if not match:
        raise ValueError(f"Invalid release tag: {tag!r}; expected vX.Y.Z[-alpha.N|-beta.N|-rc.N]")
    version = match["version"]
    if any(int(part) > 65535 for part in version.split("-")[0].split(".")):
        raise ValueError("Version components must fit the Windows version resource (0..65535)")
    return version


def replace_once(text, pattern, replacement):
    updated, count = re.subn(pattern, lambda _: replacement, text, count=0, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"Expected one version field matching {pattern!r}, found {count}")
    return updated


def version_updates(root, version):
    """Return only changes to this application's own metadata, preserving dependencies."""
    root = Path(root)
    updates = {}
    cargo_path = "src/native/Cargo.toml"
    cargo = (root / cargo_path).read_text(encoding="utf-8")
    parsed = tomllib.loads(cargo)
    if parsed["package"]["name"] != "nrclip-native":
        raise ValueError("Unexpected native package")
    updates[cargo_path] = replace_once(
        cargo, r'^version = "[^"\n]+"$', f'version = "{version}"'
    )
    lock = (root / "Cargo.lock").read_text(encoding="utf-8")
    updates["Cargo.lock"] = replace_once(
        lock, r'^name = "nrclip-native"\nversion = "[^"\n]+"$',
        f'name = "nrclip-native"\nversion = "{version}"',
    )
    for path in ("src/web/package.json", "src/web/package-lock.json"):
        data = json.loads((root / path).read_text(encoding="utf-8"))
        data["version"] = version
        if path.endswith("package-lock.json"):
            data["packages"][""]["version"] = version
        updates[path] = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return updates


def version_patch(root, version):
    root = Path(root)
    return "".join(
        "".join(difflib.unified_diff(
            (root / path).read_text(encoding="utf-8").splitlines(keepends=True),
            updated.splitlines(keepends=True), fromfile=f"a/{path}", tofile=f"b/{path}",
        ))
        for path, updated in version_updates(root, version).items()
    )


def prepare(root, version):
    """Show and apply one patch, rather than silently rewriting manifests."""
    root = Path(root)
    patch = version_patch(root, version)
    if patch:
        print(patch, end="", flush=True)
        subprocess.run(["git", "apply", "--ignore-space-change", "--whitespace=nowarn", "-"],
                       input=patch.encode("utf-8"), cwd=root, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Published release tag; omit for a development build")
    args = parser.parse_args()
    version = version_from_tag(args.tag) if args.tag is not None else DEVELOPMENT_VERSION
    prepare(Path(__file__).resolve().parents[1], version)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"version={version}\n")
    print(f"Application version: {version}")


if __name__ == "__main__":
    main()
