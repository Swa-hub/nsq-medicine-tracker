"""Monthly run, orchestrated with Prefect (optional).

    pip install prefect
    python flows/monthly_flow.py            # one run now
    prefect server start                    # (optional) UI at http://127.0.0.1:4200

To run it every month without Prefect, schedule `python -m nsq all --strict`:
* Windows: Task Scheduler -> Create Basic Task -> Monthly, day 25 ->
  Program: python, Arguments: -m nsq all --strict, Start in: <repo folder>
* Mac/Linux: crontab -e ->  0 9 25 * *  cd /path/to/nsq-tracker && python -m nsq all --strict
CDSCO usually publishes a month's alerts around the 20th of the next month.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nsq import config  # noqa: E402
from nsq.__main__ import main as nsq_main  # noqa: E402

try:
    from prefect import flow, task
except ImportError:  # run the same steps without Prefect
    def task(fn=None, **_):
        return fn if fn else (lambda f: f)

    def flow(fn=None, **_):
        return fn if fn else (lambda f: f)


STEPS = ["index", "download", "parse", "clean", "link", "warehouse", "quality", "model", "export", "report"]


@task(retries=2, retry_delay_seconds=300)
def run_step(step: str, data_dir: str | None) -> None:
    args = [step, "--strict"] if step == "quality" else [step]
    if data_dir:
        args += ["--data-dir", data_dir]
    nsq_main(args)


@flow(name="nsq-monthly")
def monthly(data_dir: str | None = None) -> None:
    for step in STEPS:
        run_step(step, data_dir)


if __name__ == "__main__":
    monthly(str(config.get_paths().data))
