#!/usr/bin/env python3
"""
extract_dbt_metadata.py

Extracts dbt manifest.json from the project and writes it to dbt_metadata/.
The dbt-cube-sync pipeline uses this manifest to generate Cube.js schema files.

Usage:
    python extract_dbt_metadata.py [--dbt-project-dir /path/to/dbt]

By default looks for the dbt project in /workspace/dbt (Docker mount) or ./dbt.
If a compiled manifest already exists at target/manifest.json it is copied directly.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


OUTPUT_DIR = Path("dbt_metadata")
OUTPUT_FILE = OUTPUT_DIR / "manifest.json"

# Candidate locations for an already-compiled manifest
MANIFEST_CANDIDATES = [
    Path("dbt/target/manifest.json"),
    Path("target/manifest.json"),
    Path("/workspace/dbt/target/manifest.json"),
]


def find_existing_manifest() -> Path | None:
    for p in MANIFEST_CANDIDATES:
        if p.exists():
            return p
    return None


def compile_dbt(dbt_project_dir: Path) -> Path:
    """Run `dbt compile` and return the path to the resulting manifest."""
    manifest_path = dbt_project_dir / "target" / "manifest.json"
    print(f"Running dbt compile in {dbt_project_dir} ...")
    result = subprocess.run(
        ["dbt", "compile", "--project-dir", str(dbt_project_dir)],
        capture_output=False,
    )
    if result.returncode != 0:
        print("ERROR: dbt compile failed.", file=sys.stderr)
        sys.exit(result.returncode)
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract dbt manifest for dbt-cube-sync")
    parser.add_argument(
        "--dbt-project-dir",
        default=None,
        help="Path to dbt project directory (default: auto-detect)",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Try to use an already-compiled manifest
    existing = find_existing_manifest()
    if existing:
        print(f"Found existing manifest at {existing}, copying to {OUTPUT_FILE}")
        shutil.copy2(existing, OUTPUT_FILE)

    elif args.dbt_project_dir:
        dbt_dir = Path(args.dbt_project_dir)
        manifest_path = compile_dbt(dbt_dir)
        shutil.copy2(manifest_path, OUTPUT_FILE)

    else:
        # Try common default locations
        for candidate_dir in [Path("dbt"), Path("/workspace/dbt")]:
            if candidate_dir.exists():
                manifest_path = compile_dbt(candidate_dir)
                shutil.copy2(manifest_path, OUTPUT_FILE)
                break
        else:
            print(
                "ERROR: No dbt project found and no --dbt-project-dir supplied.\n"
                "Either run `dbt compile` manually and place manifest.json at "
                "dbt/target/manifest.json, or pass --dbt-project-dir.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Validate the manifest
    with open(OUTPUT_FILE) as f:
        manifest = json.load(f)

    node_count = len(manifest.get("nodes", {}))
    print(f"Manifest extracted successfully: {node_count} nodes → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
