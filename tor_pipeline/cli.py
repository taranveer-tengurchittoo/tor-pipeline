"""Command-line interface for tor-pipeline."""

from __future__ import annotations

import argparse
import logging
import sys


def cmd_check(args: argparse.Namespace) -> int:
    """Verify Tor connectivity and print the current exit IP."""
    from tor_pipeline.tor import TorConfig, TorManager

    config_kwargs: dict[str, int] = {}
    if args.port:
        config_kwargs["socks_port"] = args.port

    tor = TorManager(config=TorConfig(**config_kwargs))

    if tor.verify():
        ip = tor.get_ip()
        print(f"Tor connected. Exit IP: {ip}")
        return 0

    print("Tor is not reachable. Start it with: sudo systemctl start tor", file=sys.stderr)
    return 1


def main() -> int:
    """Entry point for the ``tor-pipeline`` CLI."""
    parser = argparse.ArgumentParser(
        prog="tor-pipeline",
        description="Privacy-first distributed scraping toolkit over Tor.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable debug logging",
    )

    sub = parser.add_subparsers(dest="command")

    check_parser = sub.add_parser("check", help="verify Tor connectivity")
    check_parser.add_argument("--port", type=int, help="Tor SOCKS5 port (default: 9050)")

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "check":
        return cmd_check(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
