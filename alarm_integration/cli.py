"""Command-line interface for the alarm integration.

Exposes two subcommands:

* ``rules`` -- list the available filter rules (id + title).
* ``run``   -- execute the fetch/forward/deactivate workflow, optionally on a
  polling interval.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import List, Optional

import requests

from alarm_integration import __version__
from alarm_integration.config import Settings
from alarm_integration.logging_config import configure_logging
from alarm_integration.service import AlarmProcessor

logger = logging.getLogger(__name__)

_EPILOG = """\
Examples:
  alarm-integration rules                  # list filter rules
  alarm-integration run                    # process ALARM_STATUS alarms, polling
  alarm-integration run --rules '*'        # follow every rule
  alarm-integration run --rules id1 id2    # follow only the given rules
  alarm-integration run --dry-run          # inspect without changing anything
  alarm-integration run --status active    # process the 'active' status
  alarm-integration run --interval 60      # poll every 60 seconds
  alarm-integration run --once             # run a single cycle and exit
"""


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="alarm-integration",
        description=(
            "CatchProbe alarm integration: fetch alarms by rule, forward each "
            "alarm's detail to a webhook and mark processed alarms as passive."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("rules", help="List the available alarm filter rules.")

    run_parser = sub.add_parser("run", help="Run the alarm processing workflow.")
    run_parser.add_argument(
        "--rules",
        nargs="+",
        metavar="ID",
        help="Rule ids to follow. '*' means every rule. Defaults to FOLLOW_RULE_IDS.",
    )
    run_parser.add_argument(
        "--status",
        default=None,
        help="Status of alarms to process (defaults to ALARM_STATUS).",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch details but skip the webhook POST and passive PATCH.",
    )
    run_parser.add_argument(
        "--interval",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Polling interval in seconds (defaults to POLL_INTERVAL_SECONDS).",
    )
    run_parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle instead of polling forever.",
    )
    return parser


def _print_rules(processor: AlarmProcessor) -> None:
    """Fetch and pretty-print the filter rules."""
    rules = processor.list_rules()
    if not rules:
        logger.warning("No rules found or unexpected response shape.")
        return

    print(f"Found {len(rules)} filter rule(s):\n")
    for index, rule in enumerate(rules, start=1):
        rule_id = (
            rule.get("_id") or rule.get("id") or rule.get("core_rule_id") or "?"
            if isinstance(rule, dict)
            else "?"
        )
        title = ""
        if isinstance(rule, dict):
            title = rule.get("title") or rule.get("name") or rule.get("label") or ""
        print(f"  {index:>3}. id={rule_id}  title={title}")

    print("\nUse these ids with --rules, e.g.:")
    print("  alarm-integration run --rules <id1> <id2>")


def _run_command(processor: AlarmProcessor, args: argparse.Namespace) -> None:
    """Dispatch the ``run`` subcommand (single cycle or polling)."""
    rule_ids: Optional[List[str]] = getattr(args, "rules", None)
    status: Optional[str] = getattr(args, "status", None)
    dry_run: bool = getattr(args, "dry_run", False)
    interval: Optional[int] = getattr(args, "interval", None)
    once: bool = getattr(args, "once", False)

    if once:
        processor.run(rule_ids_override=rule_ids, status=status, dry_run=dry_run)
    else:
        processor.run_forever(
            rule_ids_override=rule_ids, status=status, dry_run=dry_run, interval=interval
        )


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv``).

    Returns:
        Process exit code (0 on success, 1 on failure).
    """
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = Settings()
    try:
        settings.validate()
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    processor = AlarmProcessor(settings)
    command = args.command or "run"

    try:
        if command == "rules":
            _print_rules(processor)
        else:
            _run_command(processor, args)
    except requests.RequestException as exc:
        logger.error("Request failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
