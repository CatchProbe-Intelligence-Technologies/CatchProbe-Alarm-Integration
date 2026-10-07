"""Runtime configuration for the alarm integration.

All settings are read from environment variables so the service can be
configured without code changes (12-factor style). A ``.env`` file placed next
to the project root is loaded automatically when ``python-dotenv`` is available.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

try:  # Optional dependency: load variables from a .env file if present.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is optional at runtime.
    pass


def _env_str(name: str, default: str) -> str:
    """Return a trimmed environment string, falling back to ``default``."""
    value = os.getenv(name)
    return value.strip() if value is not None and value.strip() else default


def _env_int(name: str, default: int) -> int:
    """Return an environment variable parsed as ``int``."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"Environment variable {name!r} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    """Return an environment variable parsed as ``float``."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ValueError(f"Environment variable {name!r} must be a number, got {raw!r}") from exc


def _env_list(name: str, default: List[str]) -> List[str]:
    """Return a comma-separated environment variable as a list of strings."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    """Immutable configuration object built from environment variables.

    Attributes:
        base_url: Base URL of the CatchProbe core API.
        api_key: Value for the ``x-api-key`` authentication header.
        organization_id: Value for the ``organizationid`` header.
        webhook_url: Destination URL that receives alarm detail payloads.
        follow_rule_ids: Rules to follow. ``["*"]`` means every rule (the
            ``core_rule_id`` query parameter is omitted). Otherwise the ids are
            joined with commas and sent as a single ``core_rule_id`` value.
        alarm_status: Status filter for alarms to process (e.g. ``"new"``).
        poll_interval_seconds: Delay between polling cycles in loop mode.
        per_page: Page size requested from the paginated alarm list endpoint.
        request_timeout: Per-request timeout in seconds.
        delay_between_alarms: Pause between processing two alarms, used to stay
            under webhook/API rate limits.
        max_retries: Attempts for a request that returns HTTP 429.
        retry_backoff_seconds: Base backoff for the exponential retry schedule.
    """

    base_url: str = field(
        default_factory=lambda: _env_str("CATCHPROBE_BASE_URL", "https://core-api.catchprobe.io")
    )
    api_key: str = field(default_factory=lambda: _env_str("CATCHPROBE_API_KEY", ""))
    organization_id: str = field(
        default_factory=lambda: _env_str("CATCHPROBE_ORGANIZATION_ID", "")
    )
    webhook_url: str = field(default_factory=lambda: _env_str("WEBHOOK_URL", ""))

    follow_rule_ids: List[str] = field(
        default_factory=lambda: _env_list("FOLLOW_RULE_IDS", ["*"])
    )
    alarm_status: str = field(default_factory=lambda: _env_str("ALARM_STATUS", "new"))

    poll_interval_seconds: int = field(
        default_factory=lambda: _env_int("POLL_INTERVAL_SECONDS", 30)
    )
    per_page: int = field(default_factory=lambda: _env_int("PER_PAGE", 20))
    request_timeout: int = field(default_factory=lambda: _env_int("REQUEST_TIMEOUT", 30))
    delay_between_alarms: float = field(
        default_factory=lambda: _env_float("DELAY_BETWEEN_ALARMS", 1.0)
    )
    max_retries: int = field(default_factory=lambda: _env_int("MAX_RETRIES", 5))
    retry_backoff_seconds: float = field(
        default_factory=lambda: _env_float("RETRY_BACKOFF_SECONDS", 2.0)
    )

    def validate(self) -> None:
        """Raise ``ValueError`` if any mandatory setting is missing.

        Call this before starting the workflow so misconfiguration fails fast
        with a clear message instead of a confusing HTTP 401/404 later.
        """
        missing = [
            name
            for name, value in (
                ("CATCHPROBE_API_KEY", self.api_key),
                ("CATCHPROBE_ORGANIZATION_ID", self.organization_id),
                ("WEBHOOK_URL", self.webhook_url),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Missing required configuration: "
                + ", ".join(missing)
                + ". Set them via environment variables or a .env file."
            )

    @property
    def auth_headers(self) -> dict:
        """Headers attached to every authenticated CatchProbe request."""
        return {"x-api-key": self.api_key, "organizationid": self.organization_id}

    def resolve_core_rule_id(self, rule_ids_override: Optional[List[str]] = None) -> Optional[str]:
        """Compute the ``core_rule_id`` query value.

        Args:
            rule_ids_override: Optional rule ids (e.g. from the CLI). When
                ``None`` the configured ``follow_rule_ids`` are used.

        Returns:
            ``None`` when ``"*"`` is selected (the caller must omit the
            parameter so every alarm is returned), otherwise the ids joined by
            commas, e.g. ``"id1,id2"``.
        """
        source = rule_ids_override if rule_ids_override is not None else self.follow_rule_ids
        configured = [str(rule).strip() for rule in source if str(rule).strip()]
        if any(rule == "*" for rule in configured):
            return None
        return ",".join(configured)
