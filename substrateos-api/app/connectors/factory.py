from __future__ import annotations

from app.connectors.models import Connection
from app.connectors.outlook_calendar import OutlookCalendarConnector
from app.connectors.outlook_mail import OutlookMailConnector
from app.connectors.sharepoint import SharePointConnector
from app.connectors.teams import TeamsConnector


def connector_for(conn: Connection):
    """Return the right connector instance for the given Connection."""
    if conn.type == "teams":
        return TeamsConnector(tenant_id=conn.connected_tenant_id)
    if conn.type == "outlook_mail":
        return OutlookMailConnector(tenant_id=conn.connected_tenant_id)
    if conn.type == "outlook_calendar":
        return OutlookCalendarConnector(tenant_id=conn.connected_tenant_id)
    return SharePointConnector(tenant_id=conn.connected_tenant_id)
