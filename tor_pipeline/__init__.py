"""
tor-pipeline: Privacy-first distributed scraping toolkit.

Provides composable building blocks for anonymous, resilient web scraping:
Tor circuit management, proxy rotation, browser anti-detection, Cloudflare
challenge handling, network interception, parallel worker orchestration,
and checkpoint-based resume.
"""

__version__ = "0.1.0"

from tor_pipeline.browser import StealthBrowser
from tor_pipeline.challenge import ChallengeHandler
from tor_pipeline.checkpoint import Checkpoint
from tor_pipeline.interceptor import NetworkInterceptor
from tor_pipeline.proxy import ProxyChain
from tor_pipeline.tor import TorManager
from tor_pipeline.workers import WorkerPool

__all__ = [
    "TorManager",
    "ProxyChain",
    "StealthBrowser",
    "ChallengeHandler",
    "NetworkInterceptor",
    "WorkerPool",
    "Checkpoint",
]
