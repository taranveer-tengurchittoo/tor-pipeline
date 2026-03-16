"""
Checkpoint manager: persistent progress tracking with resume support.

Tracks two sets per checkpoint file:
  - **processed**: items that completed successfully (skipped on resume)
  - **failed**: items that errored, stored with a reason string so you can
    triage or re-queue them later

Writes are atomic (write-to-temp then rename) to avoid corruption if the
process is killed mid-write.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


class Checkpoint:
    """Tracks scraping progress with crash-safe persistence.

    Usage::

        cp = Checkpoint("progress/worker_1.json")
        cp.load()

        for item in work_items:
            if cp.is_done(item["id"]):
                continue
            try:
                scrape(item)
                cp.mark_done(item["id"])
            except Exception as e:
                cp.mark_failed(item["id"], str(e))
            cp.save()

        print(cp.summary())
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._processed: set[str] = set()
        self._failed: dict[str, str] = {}

    @property
    def processed_count(self) -> int:
        return len(self._processed)

    @property
    def failed_count(self) -> int:
        return len(self._failed)

    def load(self) -> None:
        """Load state from disk. No-op if the file doesn't exist yet."""
        if not self.path.exists():
            return

        try:
            with open(self.path) as f:
                data = json.load(f)
            self._processed = set(data.get("processed", []))
            self._failed = dict(data.get("failed", {}))
            log.info(
                "checkpoint loaded: %d processed, %d failed",
                len(self._processed),
                len(self._failed),
            )
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("checkpoint file corrupt, starting fresh: %s", exc)

    def save(self) -> None:
        """Persist current state to disk (atomic write)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "processed": sorted(self._processed),
            "failed": self._failed,
        }

        # Write to temp file then rename for crash safety
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.path.parent),
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self.path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    def is_done(self, item_id: str) -> bool:
        """True if the item was already processed or failed."""
        return item_id in self._processed or item_id in self._failed

    def is_processed(self, item_id: str) -> bool:
        return item_id in self._processed

    def is_failed(self, item_id: str) -> bool:
        return item_id in self._failed

    def mark_done(self, item_id: str) -> None:
        self._processed.add(item_id)

    def mark_failed(self, item_id: str, reason: str) -> None:
        self._failed[item_id] = reason

    def merge_from_disk(self, directory: str | Path, extension: str = ".json") -> int:
        """Scan a directory for existing output files and add them to processed.

        Useful when the checkpoint file is stale but completed work exists
        on disk. Returns the number of newly discovered items.
        """
        directory = Path(directory)
        if not directory.exists():
            return 0

        discovered = 0
        for f in directory.iterdir():
            if f.suffix == extension:
                item_id = f.stem
                if item_id not in self._processed:
                    self._processed.add(item_id)
                    discovered += 1

        if discovered:
            log.info("discovered %d completed items on disk", discovered)
        return discovered

    def failed_by_reason(self) -> dict[str, int]:
        """Group failed items by reason, returning counts."""
        counts: dict[str, int] = {}
        for reason in self._failed.values():
            counts[reason] = counts.get(reason, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def summary(self) -> str:
        lines = [
            f"processed: {len(self._processed)}",
            f"failed:    {len(self._failed)}",
        ]
        by_reason = self.failed_by_reason()
        if by_reason:
            for reason, count in by_reason.items():
                lines.append(f"  - {reason}: {count}")
        return "\n".join(lines)

    def get_remaining(self, all_ids: list[str]) -> list[str]:
        """Return IDs from ``all_ids`` that haven't been processed or failed."""
        return [i for i in all_ids if not self.is_done(i)]
