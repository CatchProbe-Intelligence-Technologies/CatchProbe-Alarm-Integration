"""HTTP client for the CatchProbe backoffice alarm API.

The client is intentionally thin: it knows about endpoints, authentication,
pagination and retry-on-429, but contains no business logic. Workflow decisions
live in :mod:`alarm_integration.service`.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from alarm_integration.config import Settings

logger = logging.getLogger(__name__)

# Keys under which different APIs may nest a list payload.
_LIST_KEYS = ("data", "items", "results", "alarms")
# Keys under which an alarm/rule object may carry its identifier.
_ID_KEYS = ("_id", "id", "alarm_id", "core_rule_id", "rule_id")


class CatchProbeClient:
    """Thin wrapper around the CatchProbe backoffice REST API.

    A single :class:`requests.Session` is reused for connection pooling. All
    requests carry the authentication headers from :class:`Settings` and honour
    HTTP 429 ``Retry-After`` responses with exponential backoff.
    """

    def __init__(self, settings: Settings, session: Optional[requests.Session] = None) -> None:
        """Initialise the client.

        Args:
            settings: Validated runtime configuration.
            session: Optional pre-built session (useful for testing). A new one
                is created when omitted.
        """
        self._settings = settings
        self._session = session or requests.Session()
        self._session.headers.update(settings.auth_headers)

    # ------------------------------------------------------------------ #
    # Low-level request handling
    # ------------------------------------------------------------------ #
    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Send a request, retrying on HTTP 429.

        Args:
            method: HTTP verb, e.g. ``"GET"`` or ``"PATCH"``.
            url: Fully-qualified request URL.
            **kwargs: Extra arguments forwarded to ``requests``.

        Returns:
            The successful :class:`requests.Response`.

        Raises:
            requests.HTTPError: For non-429 error responses, or when retries for
                a 429 response are exhausted.
        """
        settings = self._settings
        timeout = kwargs.pop("timeout", settings.request_timeout)

        for attempt in range(1, settings.max_retries + 1):
            response = self._session.request(method, url, timeout=timeout, **kwargs)

            if response.status_code != 429:
                response.raise_for_status()
                return response

            wait = self._retry_delay(response, attempt)
            if attempt < settings.max_retries:
                logger.warning(
                    "Rate limited (429) on %s %s. Waiting %.0fs (attempt %d/%d).",
                    method,
                    url,
                    wait,
                    attempt,
                    settings.max_retries,
                )
                time.sleep(wait)
            else:
                logger.error("Rate limited (429) and retries exhausted for %s %s.", method, url)
                response.raise_for_status()

        return response  # pragma: no cover - loop always returns or raises.

    def _retry_delay(self, response: requests.Response, attempt: int) -> float:
        """Return how long to wait before retrying a 429 response."""
        retry_after = response.headers.get("Retry-After")
        if retry_after and str(retry_after).strip().isdigit():
            return float(retry_after)
        return self._settings.retry_backoff_seconds * (2 ** (attempt - 1))

    def _url(self, path: str) -> str:
        """Join the configured base URL with an endpoint ``path``."""
        return f"{self._settings.base_url.rstrip('/')}/{path.lstrip('/')}"

    # ------------------------------------------------------------------ #
    # Alarm endpoints
    # ------------------------------------------------------------------ #
    def get_alarms_page(
        self,
        status: str,
        page: int = 1,
        per_page: Optional[int] = None,
        core_rule_id: Optional[str] = None,
        **extra_params: Any,
    ) -> Tuple[List[dict], Dict[str, Any]]:
        """Fetch a single page of the alarm list.

        Args:
            status: Alarm status filter (e.g. ``"new"``).
            page: 1-based page number.
            per_page: Page size; defaults to ``Settings.per_page``.
            core_rule_id: Optional ``core_rule_id`` filter. When ``None`` the
                parameter is omitted so every alarm is returned.
            **extra_params: Additional query parameters (e.g. ``field_id``).

        Returns:
            Tuple of ``(alarms, pagination)`` where ``alarms`` is the list of
            alarm objects and ``pagination`` is the response pagination block.
        """
        params: Dict[str, Any] = {
            "status": status,
            "page": page,
            "per_page": per_page if per_page is not None else self._settings.per_page,
        }
        if core_rule_id is not None:
            params["core_rule_id"] = core_rule_id
        params.update({key: value for key, value in extra_params.items() if value is not None})

        response = self._request("GET", self._url("/backoffice/alarms/list"), params=params)
        data = response.json()
        alarms = self._extract_list(data)
        pagination = data.get("pagination", {}) if isinstance(data, dict) else {}
        return alarms, pagination

    def get_alarm_detail(self, alarm_id: str) -> dict:
        """Fetch the detail of a single alarm.

        Args:
            alarm_id: Identifier of the alarm.

        Returns:
            The parsed JSON detail payload.
        """
        response = self._request("GET", self._url(f"/backoffice/alarms/{alarm_id}"))
        return response.json()

    def set_alarm_passive(self, alarm_id: str) -> dict:
        """Mark an alarm as ``passive``.

        Sends ``PATCH /backoffice/alarms/:id`` with
        ``{"field": "status", "value": "passive"}``.

        Args:
            alarm_id: Identifier of the alarm to update.

        Returns:
            The parsed JSON response, or ``{"ok": True}`` for an empty body.
        """
        headers = {"Content-Type": "application/json"}
        body = {"field": "status", "value": "passive"}
        response = self._request(
            "PATCH", self._url(f"/backoffice/alarms/{alarm_id}"), headers=headers, json=body
        )
        return response.json() if response.content else {"ok": True}

    def get_filter_rules(self) -> Any:
        """Fetch the alarm filter rules.

        Returns:
            The parsed JSON payload of ``GET /backoffice/alarms/filter/rules``.
        """
        response = self._request("GET", self._url("/backoffice/alarms/filter/rules"))
        return response.json()

    # ------------------------------------------------------------------ #
    # Webhook
    # ------------------------------------------------------------------ #
    def post_alarm_detail_to_webhook(self, alarm_id: str, alarm_detail: dict) -> bool:
        """POST a single alarm's detail to the configured webhook.

        Args:
            alarm_id: Identifier of the alarm.
            alarm_detail: Detail payload previously fetched for the alarm.

        Returns:
            ``True`` when the webhook responds successfully.
        """
        payload = {
            "organization_id": self._settings.organization_id,
            "alarm_id": alarm_id,
            "detail": alarm_detail,
        }
        response = self._request("POST", self._settings.webhook_url, json=payload)
        return response.ok

    # ------------------------------------------------------------------ #
    # Response normalisation helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_list(data: Any) -> List[dict]:
        """Normalise an API payload to a list of objects.

        Handles bare lists as well as common wrappers such as ``{"data": [...]}``
        and one level of nesting like ``{"data": {"items": [...]}}``.
        """
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in _LIST_KEYS:
                value = data.get(key)
                if isinstance(value, list):
                    return value
                if isinstance(value, dict):
                    for inner_key in _LIST_KEYS:
                        inner = value.get(inner_key)
                        if isinstance(inner, list):
                            return inner
        return []

    @staticmethod
    def extract_alarm_id(alarm: dict) -> Optional[str]:
        """Return an alarm's identifier, trying several common key names."""
        if not isinstance(alarm, dict):
            return None
        for key in _ID_KEYS:
            if alarm.get(key):
                return alarm[key]
        return None

    def extract_rules(self, rules_payload: Any) -> List[dict]:
        """Normalise the filter-rules payload to a list of rule objects."""
        return self._extract_list(rules_payload)

    def extract_rule_ids(self, rules_payload: Any) -> List[str]:
        """Extract rule ids from the filter-rules payload, in order."""
        ids: List[str] = []
        for item in self._extract_list(rules_payload):
            rule_id = self.extract_alarm_id(item)
            if rule_id:
                ids.append(rule_id)
        return ids
