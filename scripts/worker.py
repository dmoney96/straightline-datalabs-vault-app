import json
import time
from pathlib import Path

JOBS_DIR = Path(
    Path(__file__).resolve().parents[2]
    / "opt"
    / "straightline-vault"
    / "jobs"
    / "queue"
)

MANIFEST = Path(__file__).resolve().parents[1] / "data" / "manifest.jsonl"

print(f"[worker] watching {JOBS_DIR}")

while True:
    jobs = sorted(JOBS_DIR.glob("*.json"))
    if not jobs:
        time.sleep(1)
        continue

    job_path = jobs[0]
    print(f"[worker] processing {job_path.name}")

    try:
        job = json.loads(job_path.read_text())
    except Exception as e:
        print(f"[worker] failed to read {job_path.name}: {e}")
        job_path.unlink(missing_ok=True)
        continue

    manifest_entry = {
        "job_id": job_path.name,
        "source": job.get("source"),
        "ingest_type": job.get("type"),
        "status": "processed_stub",
        "ts": time.time(),
    }

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a") as f:
        f.write(json.dumps(manifest_entry) + "\n")

    job_path.unlink(missing_ok=True)
    print(f"[worker] completed {job_path.name}")
