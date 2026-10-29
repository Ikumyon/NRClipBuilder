"""Require both nonempty distribution archives on an existing GitHub Release."""

import json
import os
from pathlib import Path
import subprocess

ASSET_NAMES = ("NRClipBuilder-windows-x64.zip", "NRClipBuilder-linux-x64.tar.gz")


def verify_assets(assets, directory):
    by_name = {asset["name"]: asset for asset in assets}
    for name in ASSET_NAMES:
        asset = by_name.get(name)
        local_size = (Path(directory) / name).stat().st_size
        if not asset or asset.get("state") != "uploaded" or not local_size or asset["size"] != local_size:
            raise ValueError(f"Missing, incomplete or empty release asset: {name}")


if __name__ == "__main__":
    response = subprocess.run(
        ["gh", "api", f"repos/{os.environ['GITHUB_REPOSITORY']}/releases/{os.environ['RELEASE_ID']}"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    verify_assets(json.loads(response.stdout)["assets"], "artifacts")
    print("Verified both release assets and their sizes")
