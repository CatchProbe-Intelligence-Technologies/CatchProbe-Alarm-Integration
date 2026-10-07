"""Business workflow for processing CatchProbe alarms.

:class:`AlarmProcessor` ties the :class:`CatchProbeClient` and :class:`Settings`
together to implement the full pipeline:

    fetch alarm -> fetch detail -> POST to webhook -> mark passive

Because every processed alarm leaves the ``status`` filter (it becomes
``passive``), the processor repeatedly fetches the *first* page until the queue
drains. This keeps memory usage flat even for tens of thousands of alarms.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional

import requests

from alarm_integration.client import CatchProbeClient
from alarm_integration.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class ProcessResult:
    """Aggregate outcome of a processing run.

    Attributes:
        succeeded: Number of alarms processed successfully.
        failed: Number of alarms that raised an error while processing.
    """

    succeeded: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        """Total number of alarms attempted."""
        return self.succeeded + self.failed


class AlarmProcessor:
    """Orchestrates the alarm fetch/forward/deactivate workflow."""

    def __init__(self, settings: Settings, client: Optional[CatchProbeClient] = None) -> None:
        """Initialise the processor.

        Args:
            settings: Validated runtime configuration.
            client: Optional pre-built client (useful for testing). A new one is
                created from ``settings`` when omitted.
        """
        self._settings = settings
        self._client = client or CatchProbeClient(settings)

    # ------------------------------------------------------------------ #
    # Single alarm
    # ------------------------------------------------------------------ #
    def process_alarm(self, alarm: dict, result: ProcessResult, dry_run: bool = False) -> None:
        """Process one alarm and update ``result`` in place.

        Args:
            alarm: Alarm object from the list endpoint.
            result: Running :class:`ProcessResult` to update.
            dry_run: When ``True`` the detail is fetched but the webhook POST and
                the passive PATCH are skipped.
        """
        alarm_id = self._client.extract_alarm_id(alarm)
        if not alarm_id:
            logger.warning("Alarm id not found; skipping entry.")
            result.failed += 1
            return

        try:
            detail = self._client.get_alarm_detail(alarm_id)
            logger.info("Fetched detail for alarm %s.", alarm_id)

            if dry_run:
                logger.info("[dry-run] alarm %s not forwarded, not deactivated.", alarm_id)
                result.succeeded += 1
                return

            self._client.post_alarm_detail_to_webhook(alarm_id, detail)
            logger.info("Forwarded alarm %s to webhook.", alarm_id)

            self._client.set_alarm_passive(alarm_id)
            logger.info("Alarm %s marked passive.", alarm_id)

            result.succeeded += 1
        except requests.RequestException as exc:
            result.failed += 1
            logger.error("Failed to process alarm %s: %s", alarm_id, exc)

    # ------------------------------------------------------------------ #
    # Full run
    # ------------------------------------------------------------------ #
    def run(
        self,
        rule_ids_override: Optional[List[str]] = None,
        status: Optional[str] = None,
        dry_run: bool = False,
    ) -> ProcessResult:
        """Run the workflow once until the alarm queue is drained.

        Args:
            rule_ids_override: Optional rule ids overriding configuration.
            status: Alarm status to process; defaults to ``Settings.alarm_status``.
            dry_run: When ``True`` only the first page is inspected and no
                mutating calls are made.

        Returns:
            A :class:`ProcessResult` summarising the run.
        """
        status = status or self._settings.alarm_status
        core_rule_id = self._settings.resolve_core_rule_id(rule_ids_override)
        result = ProcessResult()

        if core_rule_id is None:
            logger.info("Rule filter: all rules (core_rule_id omitted).")
        else:
            logger.info("Rule filter: core_rule_id=%s", core_rule_id)

        if dry_run:
            logger.info("DRY-RUN: webhook POST and passive PATCH are disabled.")
            self._run_dry(status, core_rule_id, result)
        else:
            self._run_live(status, core_rule_id, result)

        logger.info(
            "Run complete. processed=%d succeeded=%d failed=%d",
            result.total,
            result.succeeded,
            result.failed,
        )
        return result

    def _run_dry(self, status: str, core_rule_id: Optional[str], result: ProcessResult) -> None:
        """Inspect only the first page (no state changes) to avoid an infinite loop.

        In dry-run the alarms are never deactivated, so re-fetching page 1 would
        return the same records forever. We therefore process a single page.
        """
        alarms, pagination = self._client.get_alarms_page(
            status=status, page=1, core_rule_id=core_rule_id
        )
        total = pagination.get("total_count")
        logger.info(
            "status=%s total=%s (dry-run: inspecting first %d).", status, total, len(alarms)
        )
        for alarm in alarms:
            self.process_alarm(alarm, result, dry_run=True)

    def _run_live(self, status: str, core_rule_id: Optional[str], result: ProcessResult) -> None:
        """Drain the queue by repeatedly processing the first page.

        Each processed alarm becomes ``passive`` and leaves the ``status``
        filter, so fetching page 1 again surfaces the next batch. The loop stops
        when the page is empty or when a whole batch fails (which would otherwise
        repeat forever).
        """
        delay = self._settings.delay_between_alarms

        while True:
            alarms, pagination = self._client.get_alarms_page(
                status=status, page=1, core_rule_id=core_rule_id
            )
            total = pagination.get("total_count")

            if not alarms:
                logger.info("No remaining '%s' alarms.", status)
                break

            logger.info("status=%s remaining=%s. Processing %d in this batch.",
                        status, total, len(alarms))

            succeeded_before = result.succeeded
            for index, alarm in enumerate(alarms):
                self.process_alarm(alarm, result, dry_run=False)
                if index < len(alarms) - 1 and delay > 0:
                    time.sleep(delay)

            if result.succeeded == succeeded_before:
                logger.error(
                    "No alarm in this batch could be processed; the same records "
                    "would repeat. Stopping to avoid an infinite loop."
                )
                break

    def run_forever(
        self,
        rule_ids_override: Optional[List[str]] = None,
        status: Optional[str] = None,
        dry_run: bool = False,
        interval: Optional[int] = None,
    ) -> None:
        """Run :meth:`run` on a fixed interval until interrupted.

        Args:
            rule_ids_override: Optional rule ids overriding configuration.
            status: Alarm status to process.
            dry_run: Forwarded to :meth:`run`.
            interval: Seconds between cycles; defaults to
                ``Settings.poll_interval_seconds``.
        """
        interval = interval if interval is not None else self._settings.poll_interval_seconds
        cycle = 0
        logger.info("Polling mode started: every %ds. Press Ctrl+C to stop.", interval)

        try:
            while True:
                cycle += 1
                logger.info("===== Cycle #%d =====", cycle)
                try:
                    self.run(rule_ids_override=rule_ids_override, status=status, dry_run=dry_run)
                except requests.RequestException as exc:
                    logger.error("Request failed this cycle: %s", exc)
                logger.info("Sleeping %ds...", interval)
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Stopped (Ctrl+C).")

    # ------------------------------------------------------------------ #
    # Rules listing
    # ------------------------------------------------------------------ #
    def list_rules(self) -> List[dict]:
        """Fetch and return the filter rules as a list of objects.

        Returns:
            A list of rule dicts, each typically carrying ``_id`` and ``title``.
        """
        payload = self._client.get_filter_rules()
        return self._client.extract_rules(payload)
