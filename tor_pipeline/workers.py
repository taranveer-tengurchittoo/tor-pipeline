"""
Parallel worker pool: distributes work items across multiple OS processes,
each with its own browser instance and proxy connection.

Workers are launched as subprocesses so they get independent memory spaces
and can each maintain their own Selenium session. Logs are routed to
per-worker files for easy debugging.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class WorkerInfo:
    worker_id: int
    pid: int
    log_path: Path


@dataclass
class PoolConfig:
    num_workers: int = 10
    log_dir: str = "logs"
    start_delay: float = 2.0


class WorkerPool:
    """Manages a pool of subprocess workers for parallel scraping.

    Each worker runs the same Python script with a ``--worker N`` argument.
    The pool handles staggered startup (to avoid thundering-herd on the
    target), per-worker log files, and a summary of running processes.

    Usage::

        pool = WorkerPool(
            script="scraper.py",
            command="stage2",
            config=PoolConfig(num_workers=10),
        )
        pool.launch()
        # workers are now running as background processes
        pool.status()
    """

    def __init__(
        self,
        script: str,
        command: str,
        config: PoolConfig | None = None,
        python: str | None = None,
    ) -> None:
        self.script = script
        self.command = command
        self.config = config or PoolConfig()
        self.python = python or sys.executable
        self._workers: list[WorkerInfo] = []

    def launch(self) -> list[WorkerInfo]:
        """Launch all workers with staggered startup.

        Returns a list of WorkerInfo with PIDs and log paths.
        """
        log_dir = Path(self.config.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        for i in range(1, self.config.num_workers + 1):
            log_path = log_dir / f"{self.command}_worker_{i}.log"
            cmd = [self.python, "-u", self.script, self.command, "--worker", str(i)]

            with open(log_path, "w") as log_file:
                proc = subprocess.Popen(
                    cmd,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )

            info = WorkerInfo(worker_id=i, pid=proc.pid, log_path=log_path)
            self._workers.append(info)
            log.info("worker %d: PID %d -> %s", i, proc.pid, log_path)

            if i < self.config.num_workers:
                time.sleep(self.config.start_delay)

        return list(self._workers)

    @property
    def workers(self) -> list[WorkerInfo]:
        return list(self._workers)


def distribute_work(
    items: Sequence[Any],
    num_workers: int,
) -> list[list[Any]]:
    """Split a sequence of work items into roughly equal batches.

    The first ``len(items) % num_workers`` batches get one extra item
    so that no work is dropped.

    Returns:
        A list of ``num_workers`` lists.
    """
    if num_workers <= 0:
        raise ValueError("num_workers must be positive")

    items = list(items)
    base_size = len(items) // num_workers
    remainder = len(items) % num_workers

    batches: list[list[Any]] = []
    offset = 0
    for i in range(num_workers):
        size = base_size + (1 if i < remainder else 0)
        batches.append(items[offset : offset + size])
        offset += size

    return batches


def generate_search_space(
    letter_ranges: dict[int, tuple[str, str]],
    worker_id: int,
    depth: int = 3,
) -> list[str]:
    """Generate alphabetic search terms for a worker's assigned letter range.

    Given a mapping of ``{worker_id: (start_letter, end_letter)}`` and a
    depth (default 3), produces all combinations from ``start``AA...A to
    ``end``ZZ...Z.

    This is how large search-based APIs can be exhaustively enumerated:
    split the alphabet across workers so each covers a non-overlapping
    prefix range.
    """
    import string

    start, end = letter_ranges[worker_id]

    terms = []
    alpha = string.ascii_uppercase
    if depth == 3:
        for a in alpha:
            for b in alpha:
                for c in alpha:
                    term = a + b + c
                    if start <= term[0] <= end:
                        terms.append(term)
    elif depth == 2:
        for a in alpha:
            for b in alpha:
                term = a + b
                if start <= term[0] <= end:
                    terms.append(term)
    else:
        for a in alpha:
            if start <= a <= end:
                terms.append(a)

    return terms
