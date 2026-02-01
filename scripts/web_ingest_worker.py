#!/usr/bin/env python3

import json
import os
import time
from pathlib import Path

# IMPORTANT:
# When run as "python scripts/web_ingest_worker.py", sys.path[0] is the "scripts"
# directory, so a plain "import web_app" correctly loads scripts/web_app.py.
import web_app  # type: ignore[import]

# Reuse helpers from the Flask app so ingest behavior stays in sync
ingest_url_web = web_app.ingest_url_web
_log_debug = getattr(web_app, "_log_debug", print)

import os

# Define jobs root locally (do NOT depend on web_app.JOBS_ROOT existing)
JOBS_ROOT = Path(os.getenv("STRAIGHTLINE_JOBS_DIR", "/opt/straightline-vault/jobs"))

# Prefer web_app.JOBS_QUEUE_DIR if present; otherwise derive from JOBS_ROOT
JOBS_QUEUE_DIR = getattr(web_app, "JOBS_QUEUE_DIR", JOBS_ROOT / "queue")

# Worker-local directories, parallel to queue/
JOBS_PROCESSING_DIR = JOBS_ROOT / "processing"
JOBS_DONE_DIR = JOBS_ROOT / "done"
JOBS_FAILED_DIR = JOBS_ROOT / "failed"

# Make sure the directories exist
for d in (JOBS_QUEUE_DIR, JOBS_PROCESSING_DIR, JOBS_DONE_DIR, JOBS_FAILED_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _load_job(path: Path) -> dict:
    """Load a job JSON file into a dict."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        _log_debug(f"worker: ERROR reading job {path}: {e!r}")
        raise


def _process_one_job() -> bool:
    """
    Take the oldest job from queue/, move it to processing/, run ingest_url_web,
    then move the job file to done/ or failed/.

    Returns True if a job was processed, False if the queue was empty.
    """
    jobs = sorted(JOBS_QUEUE_DIR.glob("*.json"))
    if not jobs:
        return False

    job_path = jobs[0]
    processing_path = JOBS_PROCESSING_DIR / job_path.name

    # Move queue -> processing
    try:
        job_path.replace(processing_path)
    except Exception as e:
        _log_debug(f"worker: ERROR moving {job_path} to processing: {e!r}")
        return False

    # Load job payload
    try:
        job = _load_job(processing_path)
    except Exception:
        failed_path = JOBS_FAILED_DIR / processing_path.name
        processing_path.replace(failed_path)
        return True

    url = job.get("url")
    case = job.get("case")

    _log_debug(f"worker: ingesting url={url!r}, case={case!r}")

    try:
        # This calls the same ingest_url_web() used by the web UI:
        # - PDF -> ingest_source pipeline
        # - HTML / DOCX / CSV / text -> OCR_DIR + manifest + index_txt_document
        ingest_url_web(url, case=case)
        done_path = JOBS_DONE_DIR / processing_path.name
        processing_path.replace(done_path)
        _log_debug(f"worker: SUCCESS url={url!r}, case={case!r}")
    except Exception as e:
        _log_debug(f"worker: FAILED url={url!r}: {e!r}")
        failed_path = JOBS_FAILED_DIR / processing_path.name
        processing_path.replace(failed_path)

    return True


def main() -> None:
    _log_debug("worker: starting web ingest worker loop")
    while True:
        try:
            processed = _process_one_job()
            if not processed:
                time.sleep(2)
        except KeyboardInterrupt:
            _log_debug("worker: stopped by KeyboardInterrupt")
            break
        except Exception as e:
            _log_debug(f"worker: unexpected error: {e!r}")
            time.sleep(2)


if __name__ == "__main__":
    main()
