"""Promote a validated Phase 1 staging dataset into harmonized_data."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
STAGING = (BASE / ".data-review" / "harmonized_phase1_staging").resolve()
CANONICAL = (BASE / "RAG Project Dataset" / "harmonized_data").resolve()
BACKUP = (BASE / ".data-review" / "phase1_pre_ground_truth_backup_20260821").resolve()


def require_contained(path: Path, parent: Path) -> None:
    if parent != path and parent not in path.parents:
        raise RuntimeError(f"Path {path} is outside required parent {parent}")


require_contained(STAGING, BASE)
require_contained(CANONICAL, BASE)
require_contained(BACKUP, BASE)
if not BACKUP.exists():
    raise RuntimeError("Recovery backup is missing; refusing to promote")

report_path = STAGING / "governance" / "phase1_validation_report.json"
report = json.loads(report_path.read_text(encoding="utf-8"))
if report.get("passed") is not True:
    raise RuntimeError("Staging validation did not pass")

email_target = CANONICAL / "EmailJson2500"
require_contained(email_target.resolve(), CANONICAL)
for old_email in email_target.glob("*.json"):
    old_email.unlink()

promoted = 0
for source in sorted(STAGING.rglob("*")):
    if not source.is_file() or source.name.endswith(".inspect.ndjson"):
        continue
    rel = source.relative_to(STAGING)
    target = CANONICAL / rel
    require_contained(target.resolve(), CANONICAL)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    promoted += 1

print(f"Promoted {promoted} Phase 1 files into {CANONICAL}")
print("Phase 2 document directories were not modified")
