"""Daily runs via Windows Task Scheduler; on other systems we print a crontab line."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TASK_PREFIX = "jobalert-filter"


def _python_for_background() -> str:
    exe = Path(sys.executable)
    # pythonw.exe runs without flashing a console window at 09:00.
    windowless = exe.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else exe)


def task_name(time: str) -> str:
    return f"{TASK_PREFIX}-{time.replace(':', '')}"


def install(config_path: Path, times: list[str]) -> list[str]:
    config_path = config_path.resolve()
    if sys.platform != "win32":
        python = sys.executable
        lines = []
        for t in times:
            hour, minute = t.split(":")
            lines.append(f'{int(minute)} {int(hour)} * * * "{python}" -m jobalert_filter run '
                         f'--config "{config_path}"')
        return ["Add these lines with `crontab -e`:", *lines]

    command = f'"{_python_for_background()}" -m jobalert_filter run --config "{config_path}"'
    done = []
    for t in times:
        subprocess.run(
            ["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", t, "/TN", task_name(t),
             "/TR", command],
            check=True, capture_output=True, text=True,
        )
        done.append(f"Scheduled {task_name(t)} daily at {t}")
    return done


def remove(times: list[str]) -> list[str]:
    if sys.platform != "win32":
        return ["Remove the jobalert lines with `crontab -e`."]
    done = []
    for t in times:
        result = subprocess.run(["schtasks", "/Delete", "/F", "/TN", task_name(t)],
                                capture_output=True, text=True)
        done.append(f"Removed {task_name(t)}" if result.returncode == 0
                    else f"{task_name(t)} not found")
    return done
