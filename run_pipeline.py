"""End-to-end pipeline orchestrator: bronze ingestion -> dbt (silver, gold).

This is the "manual" orchestrator for phase 1 of the project. It's what the
scheduled GitHub Actions workflow calls, and what a real orchestrator
(Airflow/Dagster) would eventually replace with a proper DAG of retries,
alerting, and dependency tracking -- see README "Roadmap" for that plan.

This runs the DE (forecast) models only -- it excludes anything tagged
`ds`, since those models source from the historical-actuals bronze table
that only exists after someone has run ds/backfill_historical.py. That
keeps this script (and the daily CI job that calls it) working standalone
on a repo where the one-time DS backfill hasn't happened yet. See
ds/run_ds_pipeline.py for the DS-side orchestrator.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DBT_PROJECT_DIR = REPO_ROOT / "dbt_project"
DB_PATH = REPO_ROOT / "data" / "weather.duckdb"


def step(name: str, fn) -> None:
    print(f"\n=== {name} ===")
    fn()
    print(f"=== {name}: done ===")


def run_ingestion() -> None:
    sys.path.insert(0, str(REPO_ROOT / "ingestion"))
    import fetch_weather

    count = fetch_weather.run()
    if not count:
        raise SystemExit("ingestion produced no rows; aborting before dbt run")


def run_dbt(cmd: list[str]) -> None:
    env = {**os.environ, "DBT_DUCKDB_PATH": str(DB_PATH)}
    result = subprocess.run(
        [
            "dbt", *cmd, "--exclude", "tag:ds",
            "--project-dir", str(DBT_PROJECT_DIR), "--profiles-dir", str(DBT_PROJECT_DIR),
        ],
        cwd=REPO_ROOT,
        env=env,
    )
    if result.returncode != 0:
        raise SystemExit(f"dbt {' '.join(cmd)} failed with exit code {result.returncode}")


def main() -> None:
    step("Bronze ingestion (Open-Meteo -> DuckDB)", run_ingestion)
    step("dbt run (silver + gold, DE models)", lambda: run_dbt(["run"]))
    step("dbt test (DE models)", lambda: run_dbt(["test"]))
    print("\nPipeline completed successfully.")


if __name__ == "__main__":
    main()
