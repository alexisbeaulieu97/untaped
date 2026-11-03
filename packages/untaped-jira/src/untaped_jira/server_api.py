"""jira's ``ServerApi``: which Jira it talks to, and the adapter that speaks to it.

Its own module, so the plugin class and every command import it without
importing each other. Commands reach it through ``JiraCommand.client()``.
"""

from __future__ import annotations

from untaped.sdk import key
from untaped.sdk.network import ServerApi
from untaped_jira.data_center import DataCenter
from untaped_jira.ports import JiraApi
from untaped_jira.server import JiraVersion, detect
from untaped_jira.settings import TOKEN, JiraSettings

SERVER_API = ServerApi[JiraApi, JiraSettings, JiraVersion](
    server=key(JiraSettings).server,
    credential=TOKEN,
    detect=detect,
    adapters=(DataCenter,),
    override=key(JiraSettings).server_version,
)

__all__ = ["SERVER_API"]
