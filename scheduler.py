"""
Long-running IST scheduler for Naukri profile automation.

Run:
    python scheduler.py

This process stays alive and triggers main.run_once() at:
    08:30, 12:30, 14:30, 18:00 India Standard Time
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, date, time as dt_time
from zoneinfo import ZoneInfo

from main import run_once

IST = ZoneInfo("Asia/Kolkata")
SCHEDULE_TIMES = [
    dt_time(8, 30),
    dt_time(12, 30),
    dt_time(14, 30),
    dt_time(18, 0),
]

log = logging.getLogger("naukri.scheduler")


def _ist_now() -> datetime:
    return datetime.now(tz=IST)


def _run_key(run_date: date, run_time: dt_time) -> str:
    return f"{run_date.isoformat()}_{run_time.strftime('%H:%M')}"


def run_scheduler() -> None:
    log.info("Naukri IST scheduler started: %s", ", ".join(t.strftime("%H:%M") for t in SCHEDULE_TIMES))

    executed: set[str] = set()
    last_date: date | None = None

    while True:
        now = _ist_now()
        today = now.date()
        current_minute = now.strftime("%H:%M")

        if last_date is None or today != last_date:
            executed.clear()
            last_date = today

        for run_time in SCHEDULE_TIMES:
            key = _run_key(today, run_time)
            if key in executed:
                continue
            if current_minute == run_time.strftime("%H:%M"):
                log.info("Triggering scheduled run for %s IST", run_time.strftime("%H:%M"))
                try:
                    run_once()
                except Exception as exc:
                    log.exception("Scheduled run failed at %s IST: %s", run_time.strftime("%H:%M"), exc)
                executed.add(key)

        time.sleep(20)


if __name__ == "__main__":
    run_scheduler()
