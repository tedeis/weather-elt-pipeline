"""DS-side orchestrator: historical backfill -> dbt (ds-tagged models) ->
train models -> update the forecast-accuracy-by-lead-time table.

Run this once to set up the DS project, then re-run `train.py` and
`eval_forecast_accuracy.py` periodically as more history accumulates (the
daily GitHub Actions workflow does this automatically once
data/weather.duckdb contains the historical bronze table -- see
.github/workflows/pipeline.yml). The backfill itself only needs to run
once; re-running it just refreshes the archive with the same date range.

    python3 ds/run_ds_pipeline.py --years 2
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DBT_PROJECT_DIR = REPO_ROOT / "dbt_project"
DB_PATH = REPO_ROOT / "data" / "weather.duckdb"


def step(name: str, fn) -> None:
    print(f"\n=== {name} ===")
    fn()
    print(f"=== {name}: done ===")


def run_backfill(years: int) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import backfill_historical

    count = backfill_historical.run(years=years)
    if not count:
        raise SystemExit("backfill produced no rows; aborting")


def run_dbt(cmd: list[str]) -> None:
    env = {**os.environ, "DBT_DUCKDB_PATH": str(DB_PATH)}
    result = subprocess.run(
        [
            "dbt", *cmd, "--select", "tag:ds",
            "--project-dir", str(DBT_PROJECT_DIR), "--profiles-dir", str(DBT_PROJECT_DIR),
        ],
        cwd=REPO_ROOT,
        env=env,
    )
    if result.returncode != 0:
        raise SystemExit(f"dbt {' '.join(cmd)} failed with exit code {result.returncode}")


def run_script(script: str) -> None:
    result = subprocess.run([sys.executable, script], cwd=REPO_ROOT)
    if result.returncode != 0:
        raise SystemExit(f"{script} failed with exit code {result.returncode}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=2)
    args = parser.parse_args()

    step("Historical backfill (Open-Meteo Archive API -> DuckDB)", lambda: run_backfill(args.years))
    step("dbt run (ds-tagged silver model)", lambda: run_dbt(["run"]))
    step("dbt test (ds-tagged model)", lambda: run_dbt(["test"]))
    step("Train + evaluate forecasting models", lambda: run_script("ds/train.py"))
    step("Update forecast-accuracy-by-lead-time table", lambda: run_script("ds/eval_forecast_accuracy.py"))
    print("\nDS pipeline completed successfully.")


if __name__ == "__main__":
    main()
