"""Stage Breaker+FVG generated outputs with a daily research push policy.

The dashboard data is staged every run so GitHub Pages can update frequently.
The research CSVs are staged once per day after market close to avoid bloating
Git history with large hourly data commits.
"""

import argparse
import subprocess
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
DASHBOARD_DATA = "Newtest/Breaker_Based/breaker_fvg_dashboard_data.js"
RESEARCH_FILES = [
    "Newtest/Breaker_Based/breaker_fvg_research_log.csv",
    "Newtest/Breaker_Based/breaker_fvg_trade_timeseries.csv",
]
IST = ZoneInfo("Asia/Kolkata")
DAILY_RESEARCH_PUSH_TIME = time(15, 15)


def run_git_add(paths):
    existing = [path for path in paths if (REPO_ROOT / path).exists()]
    if not existing:
        return []
    subprocess.run(["git", "add", *existing], cwd=REPO_ROOT, check=True)
    return existing


def should_stage_research(now, force):
    return force or now.time() >= DAILY_RESEARCH_PUSH_TIME


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force-research",
        action="store_true",
        help="Stage research CSVs regardless of the current IST time.",
    )
    args = parser.parse_args()

    now = datetime.now(IST)
    staged = run_git_add([DASHBOARD_DATA])
    print(f"Staged dashboard output: {', '.join(staged) if staged else 'none'}")

    if should_stage_research(now, args.force_research):
        staged_research = run_git_add(RESEARCH_FILES)
        print(f"Staged research outputs: {', '.join(staged_research) if staged_research else 'none'}")
    else:
        print(
            "Skipped research outputs for this hourly run; "
            f"daily staging starts at {DAILY_RESEARCH_PUSH_TIME.strftime('%H:%M')} IST."
        )


if __name__ == "__main__":
    main()
