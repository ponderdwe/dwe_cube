#!/usr/bin/env python3
# Copyright 2026 Ponder
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
extract_dbt_metadata.py

Extracts dbt manifest.json (and catalog.json) for the dbt-cube-sync pipeline.

Resolution order:
  1. dbt Cloud API  — if DBT_CLOUD_TOKEN + DBT_CLOUD_ACCOUNTID + DBT_CLOUD_JOB_ID are set
  2. Pre-compiled manifest — dbt/target/manifest.json or /workspace/dbt/target/manifest.json
  3. dbt compile  — if --dbt-project-dir is supplied or a dbt/ directory exists

Usage:
    python extract_dbt_metadata.py [--dbt-project-dir /path/to/dbt]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = Path("dbt_metadata")
MANIFEST_OUT = OUTPUT_DIR / "manifest.json"
CATALOG_OUT = OUTPUT_DIR / "catalog.json"

MANIFEST_CANDIDATES = [
    Path("dbt/target/manifest.json"),
    Path("target/manifest.json"),
    Path("/workspace/dbt/target/manifest.json"),
]


# ── dbt Cloud ────────────────────────────────────────────────────────────────

def _dbt_cloud_headers(token: str) -> dict:
    return {"Authorization": f"Token {token}", "Content-Type": "application/json"}


def _latest_run_id(token: str, account_id: str, job_id: str) -> int:
    url = f"https://cloud.getdbt.com/api/v2/accounts/{account_id}/runs/"
    resp = requests.get(
        url,
        headers=_dbt_cloud_headers(token),
        params={"job_definition_id": job_id, "order_by": "-created_at", "limit": 1},
        timeout=30,
    )
    resp.raise_for_status()
    runs = resp.json()["data"]
    if not runs:
        raise ValueError(f"No runs found for dbt Cloud job {job_id}")
    return runs[0]["id"]


def _download_artifact(token: str, account_id: str, run_id: int, artifact: str, dest: Path) -> None:
    url = f"https://cloud.getdbt.com/api/v2/accounts/{account_id}/runs/{run_id}/artifacts/{artifact}.json"
    resp = requests.get(url, headers=_dbt_cloud_headers(token), timeout=60)
    resp.raise_for_status()
    dest.write_text(json.dumps(resp.json(), indent=2))
    print(f"Downloaded {artifact}.json → {dest}")


def fetch_from_dbt_cloud() -> bool:
    token = os.getenv("DBT_CLOUD_TOKEN")
    account_id = os.getenv("DBT_CLOUD_ACCOUNTID")
    job_id = os.getenv("DBT_CLOUD_JOB_ID")
    if not all([token, account_id, job_id]):
        return False

    print(f"dbt Cloud credentials found — fetching artifacts for job {job_id} ...")
    run_id = _latest_run_id(token, account_id, job_id)
    print(f"Latest run: {run_id}")
    _download_artifact(token, account_id, run_id, "manifest", MANIFEST_OUT)
    try:
        _download_artifact(token, account_id, run_id, "catalog", CATALOG_OUT)
    except Exception as exc:
        print(f"Warning: could not download catalog.json ({exc})")
    return True


# ── Local / compile ───────────────────────────────────────────────────────────

def copy_existing_manifest() -> bool:
    for p in MANIFEST_CANDIDATES:
        if p.exists():
            print(f"Found existing manifest at {p}, copying → {MANIFEST_OUT}")
            shutil.copy2(p, MANIFEST_OUT)
            return True
    return False


def compile_dbt(dbt_project_dir: Path) -> None:
    print(f"Running dbt compile in {dbt_project_dir} ...")
    result = subprocess.run(
        ["dbt", "compile", "--project-dir", str(dbt_project_dir)],
        capture_output=False,
    )
    if result.returncode != 0:
        print("ERROR: dbt compile failed.", file=sys.stderr)
        sys.exit(result.returncode)
    shutil.copy2(dbt_project_dir / "target" / "manifest.json", MANIFEST_OUT)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract dbt manifest for dbt-cube-sync")
    parser.add_argument("--dbt-project-dir", default=None)
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if fetch_from_dbt_cloud():
        pass
    elif copy_existing_manifest():
        pass
    elif args.dbt_project_dir:
        compile_dbt(Path(args.dbt_project_dir))
    else:
        for candidate in [Path("dbt"), Path("/workspace/dbt")]:
            if candidate.exists():
                compile_dbt(candidate)
                break
        else:
            print(
                "ERROR: No dbt project found and no --dbt-project-dir supplied.\n"
                "Either set DBT_CLOUD_TOKEN + DBT_CLOUD_ACCOUNTID + DBT_CLOUD_JOB_ID,\n"
                "run `dbt compile` and place manifest.json at dbt/target/manifest.json,\n"
                "or pass --dbt-project-dir.",
                file=sys.stderr,
            )
            sys.exit(1)

    with open(MANIFEST_OUT) as f:
        manifest = json.load(f)
    node_count = len(manifest.get("nodes", {}))
    print(f"Manifest ready: {node_count} nodes → {MANIFEST_OUT}")


if __name__ == "__main__":
    main()
