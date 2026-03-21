#!/usr/bin/env python
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# -------- Python path so we can import web_app & vault_core --------

ROOT = Path(__file__).resolve().parents[1]  # /home/dom/vault-app
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from web_app import (  # type: ignore[import]
    ingest_url_web,
    _log_debug,
    JOBS_QUEUE_DIR,
)

# -------- Job directories (derived from web_app's queue dir) --------

JOBS_ROOT = JOBS_QUEUE_DIR.parent
QUEUE_DIR = JOBS_QUEUE_DIR
PROCESSING_DIR = JOBS_ROOT / "processing"
DONE_DIR = JOBS_ROOT / "done"
FAILED_DIR = JOBS_ROOT / "failed"

POLL_INTERVAL = float(os.getenv("STRAIGHTLINE_JOB_POLL_INTERVAL", "1.0"))


@dataclass
class Job:
    path: Path
    url: str
    case: Optional[str]


def load_job(path: Path) -> Optional[Job]:
    """Read JSON job file and return a Job, or None if invalid."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        url = data.get("url")
        case = data.get("case")

        if not isinstance(url, str) or not url:
            _log_debug(f"worker: invalid job file (no url): {path}")
            return None

        if case is not None and not isinstance(case, str):
            case = None

        return Job(path=path, url=url, case=case)

    except Exception as e:
        _log_debug(f"worker: failed to load job {path}: {e!r}")
        return None


def claim_next_job() -> Optional[Job]:
    """
    Atomically move the oldest file from queue/ -> processing/
    and return it as a Job, or None if queue is empty.
    """
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSING_DIR.mkdir(parents=True, exist_ok=True)
    FAILED_DIR.mkdir(parents=True, exist_ok=True)

    for entry in sorted(QUEUE_DIR.iterdir()):
        if not entry.is_file():
            continue

        if not os.access(entry, os.W_OK):
            _log_debug(
                f"worker: skipping job not writable by worker (ownership/perm issue): {entry}"
            )
            continue

        processing_path = PROCESSING_DIR / entry.name
        try:
            entry.replace(processing_path)
        except FileNotFoundError:
            continue
        except Exception as e:
            _log_debug(f"worker: error claiming job {entry}: {e!r}")
            continue

        job = load_job(processing_path)
        if job is None:
            try:
                processing_path.replace(FAILED_DIR / processing_path.name)
            except Exception as e:
                _log_debug(
                    f"worker: failed to move invalid job {processing_path} to failed/: {e!r}"
                )
            return None

        return job

    return None


def mark_done(job: Job, ok: bool) -> None:
    """Move job file from processing/ -> done/ or failed/."""
    target_dir = DONE_DIR if ok else FAILED_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        job.path.replace(target_dir / job.path.name)
    except Exception as e:
        _log_debug(f"worker: failed to move {job.path} -> {target_dir}: {e!r}")


def main() -> None:
    _log_debug("job_worker: starting")

    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSING_DIR.mkdir(parents=True, exist_ok=True)
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    FAILED_DIR.mkdir(parents=True, exist_ok=True)

    # ---- orphan reaper: reclaim stale processing jobs on startup ----
    for entry in sorted(PROCESSING_DIR.iterdir()):
        if not entry.is_file():
            continue
        try:
            entry.replace(QUEUE_DIR / entry.name)
            _log_debug(f"job_worker: re-queued orphaned processing job: {entry.name}")
        except Exception as e:
            _log_debug(f"job_worker: failed to re-queue {entry}: {e!r}")

    while True:
        job = claim_next_job()
        if job is None:
            time.sleep(POLL_INTERVAL)
            continue

        _log_debug(
            f"job_worker: processing url={job.url!r} case={job.case!r} (file={job.path})"
        )

        try:
            ingest_url_web(job.url, case=job.case)
        except Exception as e:
            _log_debug(f"job_worker: job FAILED url={job.url!r}: {e!r}")
            mark_done(job, ok=False)
        else:
            _log_debug(f"job_worker: job DONE url={job.url!r}")
            mark_done(job, ok=True)


if __name__ == "__main__":
    main()
