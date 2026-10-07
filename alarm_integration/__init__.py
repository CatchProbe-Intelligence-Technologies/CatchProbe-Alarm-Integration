"""CatchProbe Alarm Integration package.

Pulls alarms from the CatchProbe backoffice API, forwards each alarm's detail to
a configured webhook and then marks the alarm as ``passive``.

Public API:
    Settings           -- runtime configuration loaded from the environment.
    CatchProbeClient   -- thin HTTP client for the CatchProbe backoffice API.
    AlarmProcessor     -- orchestrates the fetch -> webhook -> passive workflow.
"""

from alarm_integration.client import CatchProbeClient
from alarm_integration.config import Settings
from alarm_integration.service import AlarmProcessor

__all__ = ["Settings", "CatchProbeClient", "AlarmProcessor", "__version__"]

__version__ = "1.0.0"
