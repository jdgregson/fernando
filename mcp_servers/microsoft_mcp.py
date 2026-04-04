#!/usr/bin/env python3
"""
MCP Server for Microsoft 365 integration via Microsoft Graph API.
Provides tools for mail, calendar, and authentication.
"""

import os
import sys

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_venv_site = os.path.join(
    _project_root,
    "venv",
    "lib",
    f"python{sys.version_info.major}.{sys.version_info.minor}",
    "site-packages",
)
if os.path.isdir(_venv_site) and _venv_site not in sys.path:
    sys.path.insert(0, _venv_site)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import asyncio
import json
import msal
import requests
from datetime import datetime, timezone
from mcp.server import Server
from mcp.types import Tool, TextContent

app = Server("microsoft")

CONFIG_DIR = os.path.join(_project_root, "data", "microsoft")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
TOKEN_FILE = os.path.join(CONFIG_DIR, "tokens.json")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

SCOPES = [
    "User.Read",
    "Mail.Read",
    "Mail.ReadWrite",
    "Mail.Send",
    "Mail.ReadWrite.Shared",
    "Mail.Send.Shared",
    "Calendars.ReadWrite",
    "Calendars.ReadWrite.Shared",
    "Contacts.Read",
    "Contacts.ReadWrite",
    "Tasks.ReadWrite",
    "Notes.ReadWrite",
    "Files.ReadWrite",
]


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(config):
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    os.chmod(CONFIG_FILE, 0o600)


def load_tokens():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return json.load(f)
    return {}


def save_tokens(tokens):
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)


def get_msal_app():
    config = load_config()
    if not config.get("client_id") or not config.get("tenant_id"):
        return None, "Not configured. Use microsoft_configure first."
    return msal.ConfidentialClientApplication(
        config["client_id"],
        authority=f"https://login.microsoftonline.com/{config['tenant_id']}",
        client_credential=config.get("client_secret"),
    ), None


def get_access_token():
    """Get a valid access token using the stored refresh token."""
    msal_app, err = get_msal_app()
    if err:
        return None, err

    tokens = load_tokens()
    if not tokens.get("refresh_token"):
        return None, "Not authenticated. Use microsoft_login first."

    result = msal_app.acquire_token_by_refresh_token(
        tokens["refresh_token"], scopes=SCOPES
    )

    if "access_token" in result:
        tokens["access_token"] = result["access_token"]
        if "refresh_token" in result:
            tokens["refresh_token"] = result["refresh_token"]
        save_tokens(tokens)
        return result["access_token"], None
    else:
        return (
            None,
            f"Token refresh failed: {result.get('error_description', result.get('error', 'unknown'))}",
        )


def graph_request(method, path, **kwargs):
    """Make an authenticated request to Microsoft Graph."""
    token, err = get_access_token()
    if err:
        return {"error": err}

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{GRAPH_BASE}{path}" if path.startswith("/") else path
    resp = requests.request(method, url, headers=headers, **kwargs)

    if resp.status_code in (202, 204):
        return {"status": "success"}
    try:
        return resp.json()
    except Exception:
        return {"error": f"HTTP {resp.status_code}: {resp.text[:500]}"}


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="microsoft_configure",
            description="Configure Microsoft 365 app credentials. Required before login. Get these from Azure Portal > App registrations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "client_id": {
                        "type": "string",
                        "description": "Application (client) ID",
                    },
                    "tenant_id": {
                        "type": "string",
                        "description": "Directory (tenant) ID",
                    },
                    "client_secret": {
                        "type": "string",
                        "description": "Client secret value",
                    },
                },
                "required": ["client_id", "tenant_id", "client_secret"],
            },
        ),
        Tool(
            name="microsoft_login",
            description="Start the OAuth2 login flow. Returns a URL the user must open in their browser to sign in. After sign-in, Microsoft redirects to the callback URL where the token is captured.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="microsoft_status",
            description="Check if Microsoft 365 authentication is configured and working.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="microsoft_mail_list",
            description="List recent emails from inbox.",
            inputSchema={
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of emails to fetch (default 10, max 50)",
                    },
                    "folder": {
                        "type": "string",
                        "description": "Mail folder (default: Inbox)",
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Only show unread emails",
                    },
                },
            },
        ),
        Tool(
            name="microsoft_mail_read",
            description="Read a specific email by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "The email message ID",
                    },
                },
                "required": ["message_id"],
            },
        ),
        Tool(
            name="microsoft_mail_send",
            description="Send an email. Optionally attach a file by providing its local path.",
            inputSchema={
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject"},
                    "body": {
                        "type": "string",
                        "description": "Email body (plain text)",
                    },
                    "attachment_path": {
                        "type": "string",
                        "description": "Local file path to attach (optional)",
                    },
                },
                "required": ["to", "subject", "body"],
            },
        ),
        Tool(
            name="microsoft_calendar_list",
            description="List upcoming calendar events.",
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of days ahead to look (default 7)",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Max events to return (default 20)",
                    },
                },
            },
        ),
        Tool(
            name="microsoft_calendar_create",
            description="Create a calendar event.",
            inputSchema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "Event subject"},
                    "start": {
                        "type": "string",
                        "description": "Start time in ISO 8601 format (e.g. 2026-03-25T09:00:00)",
                    },
                    "end": {
                        "type": "string",
                        "description": "End time in ISO 8601 format",
                    },
                    "body": {
                        "type": "string",
                        "description": "Event body/description (optional)",
                    },
                    "attendees": {
                        "type": "string",
                        "description": "Comma-separated email addresses of attendees (optional)",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "Timezone (default: America/Los_Angeles)",
                    },
                    "recurrence": {
                        "type": "string",
                        "description": "Recurrence pattern: 'daily', 'weekly', 'monthly', 'yearly', or 'weekdays' (Mon-Fri). Optionally add interval e.g. 'weekly:2' for every 2 weeks.",
                    },
                    "recurrence_end": {
                        "type": "string",
                        "description": "Recurrence end date in YYYY-MM-DD format. Required if recurrence is set.",
                    },
                },
                "required": ["subject", "start", "end"],
            },
        ),
        Tool(
            name="microsoft_calendar_update",
            description="Update an existing calendar event. Only provide fields you want to change.",
            inputSchema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The event ID to update",
                    },
                    "subject": {"type": "string", "description": "New event subject"},
                    "start": {
                        "type": "string",
                        "description": "New start time in ISO 8601 format",
                    },
                    "end": {
                        "type": "string",
                        "description": "New end time in ISO 8601 format",
                    },
                    "body": {
                        "type": "string",
                        "description": "New event body/description",
                    },
                    "attendees": {
                        "type": "string",
                        "description": "Comma-separated email addresses of attendees",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "Timezone (default: America/Los_Angeles)",
                    },
                },
                "required": ["event_id"],
            },
        ),
        Tool(
            name="microsoft_calendar_delete",
            description="Delete a calendar event.",
            inputSchema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The event ID to delete",
                    },
                },
                "required": ["event_id"],
            },
        ),
        Tool(
            name="microsoft_calendar_get",
            description="Get full details of a specific calendar event by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The event ID to retrieve",
                    },
                },
                "required": ["event_id"],
            },
        ),
        Tool(
            name="microsoft_mail_reply",
            description="Reply to an email in the same conversation thread.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "The message ID to reply to",
                    },
                    "body": {
                        "type": "string",
                        "description": "Reply body (plain text)",
                    },
                },
                "required": ["message_id", "body"],
            },
        ),
        Tool(
            name="microsoft_mail_search",
            description="Search emails by keyword across subject, body, and sender.",
            inputSchema={
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Search query string"},
                    "count": {
                        "type": "integer",
                        "description": "Max results to return (default 10, max 50)",
                    },
                },
                "required": ["search"],
            },
        ),
        # --- Shared calendars ---
        Tool(
            name="microsoft_calendars_list",
            description="List all calendars available to the user, including shared and delegated calendars.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="microsoft_calendar_shared_events",
            description="List upcoming events from a specific calendar (shared or own) by calendar ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "calendar_id": {"type": "string", "description": "Calendar ID (get from microsoft_calendars_list)"},
                    "days": {"type": "integer", "description": "Days ahead to look (default 7)"},
                    "count": {"type": "integer", "description": "Max events (default 20)"},
                },
                "required": ["calendar_id"],
            },
        ),
        # --- Shared mailbox ---
        Tool(
            name="microsoft_shared_mailbox_list",
            description="List recent emails from a shared mailbox.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mailbox": {"type": "string", "description": "Shared mailbox email address"},
                    "count": {"type": "integer", "description": "Number of emails (default 10, max 50)"},
                    "folder": {"type": "string", "description": "Mail folder (default: Inbox)"},
                },
                "required": ["mailbox"],
            },
        ),
        Tool(
            name="microsoft_shared_mailbox_send",
            description="Send an email from a shared mailbox.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mailbox": {"type": "string", "description": "Shared mailbox email address (send as)"},
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject"},
                    "body": {"type": "string", "description": "Email body (plain text)"},
                },
                "required": ["mailbox", "to", "subject", "body"],
            },
        ),
        # --- Contacts ---
        Tool(
            name="microsoft_contacts_list",
            description="List contacts from the user's address book.",
            inputSchema={
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "Max contacts to return (default 20)"},
                    "search": {"type": "string", "description": "Search by name or email (optional)"},
                },
            },
        ),
        Tool(
            name="microsoft_contact_create",
            description="Create a new contact.",
            inputSchema={
                "type": "object",
                "properties": {
                    "given_name": {"type": "string", "description": "First name"},
                    "surname": {"type": "string", "description": "Last name"},
                    "email": {"type": "string", "description": "Email address"},
                    "phone": {"type": "string", "description": "Phone number (optional)"},
                    "company": {"type": "string", "description": "Company name (optional)"},
                },
                "required": ["given_name", "email"],
            },
        ),
        Tool(
            name="microsoft_contact_delete",
            description="Delete a contact by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "contact_id": {"type": "string", "description": "Contact ID to delete"},
                },
                "required": ["contact_id"],
            },
        ),
        # --- Tasks (To Do) ---
        Tool(
            name="microsoft_task_lists",
            description="List all Microsoft To Do task lists.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="microsoft_tasks_list",
            description="List tasks in a specific To Do task list.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_id": {"type": "string", "description": "Task list ID"},
                    "count": {"type": "integer", "description": "Max tasks (default 20)"},
                    "include_completed": {"type": "boolean", "description": "Include completed tasks (default false)"},
                },
                "required": ["list_id"],
            },
        ),
        Tool(
            name="microsoft_task_create",
            description="Create a new task in a To Do list.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_id": {"type": "string", "description": "Task list ID"},
                    "title": {"type": "string", "description": "Task title"},
                    "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD format (optional)"},
                    "body": {"type": "string", "description": "Task notes/body (optional)"},
                },
                "required": ["list_id", "title"],
            },
        ),
        Tool(
            name="microsoft_task_update",
            description="Update a task (title, status, due date). To complete a task, set status to 'completed'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_id": {"type": "string", "description": "Task list ID"},
                    "task_id": {"type": "string", "description": "Task ID"},
                    "title": {"type": "string", "description": "New title (optional)"},
                    "status": {"type": "string", "description": "Status: 'notStarted', 'inProgress', 'completed'"},
                    "due_date": {"type": "string", "description": "Due date YYYY-MM-DD (optional)"},
                    "body": {"type": "string", "description": "Task notes/body (optional)"},
                },
                "required": ["list_id", "task_id"],
            },
        ),
        Tool(
            name="microsoft_task_delete",
            description="Delete a task from a To Do list.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_id": {"type": "string", "description": "Task list ID"},
                    "task_id": {"type": "string", "description": "Task ID"},
                },
                "required": ["list_id", "task_id"],
            },
        ),
        # --- OneNote ---
        Tool(
            name="microsoft_onenote_notebooks",
            description="List OneNote notebooks.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="microsoft_onenote_notebook_create",
            description="Create a new OneNote notebook.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Notebook display name"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="microsoft_onenote_sections",
            description="List sections in a OneNote notebook.",
            inputSchema={
                "type": "object",
                "properties": {
                    "notebook_id": {"type": "string", "description": "Notebook ID"},
                },
                "required": ["notebook_id"],
            },
        ),
        Tool(
            name="microsoft_onenote_section_create",
            description="Create a new section in a OneNote notebook.",
            inputSchema={
                "type": "object",
                "properties": {
                    "notebook_id": {"type": "string", "description": "Notebook ID"},
                    "name": {"type": "string", "description": "Section name"},
                },
                "required": ["notebook_id", "name"],
            },
        ),
        Tool(
            name="microsoft_onenote_pages",
            description="List pages in a OneNote section.",
            inputSchema={
                "type": "object",
                "properties": {
                    "section_id": {"type": "string", "description": "Section ID"},
                    "count": {"type": "integer", "description": "Max pages (default 20)"},
                },
                "required": ["section_id"],
            },
        ),
        Tool(
            name="microsoft_onenote_page_read",
            description="Read the content of a OneNote page.",
            inputSchema={
                "type": "object",
                "properties": {
                    "page_id": {"type": "string", "description": "Page ID"},
                },
                "required": ["page_id"],
            },
        ),
        Tool(
            name="microsoft_onenote_page_create",
            description="Create a new page in a OneNote section.",
            inputSchema={
                "type": "object",
                "properties": {
                    "section_id": {"type": "string", "description": "Section ID"},
                    "title": {"type": "string", "description": "Page title"},
                    "content": {"type": "string", "description": "Page content (plain text, will be wrapped in HTML)"},
                },
                "required": ["section_id", "title", "content"],
            },
        ),
        Tool(
            name="microsoft_onenote_notebook_delete",
            description="Delete a OneNote notebook.",
            inputSchema={
                "type": "object",
                "properties": {
                    "notebook_id": {"type": "string", "description": "Notebook ID"},
                },
                "required": ["notebook_id"],
            },
        ),
        Tool(
            name="microsoft_onenote_section_delete",
            description="Delete a section from a OneNote notebook.",
            inputSchema={
                "type": "object",
                "properties": {
                    "section_id": {"type": "string", "description": "Section ID"},
                },
                "required": ["section_id"],
            },
        ),
        Tool(
            name="microsoft_onenote_page_delete",
            description="Delete a page from a OneNote section.",
            inputSchema={
                "type": "object",
                "properties": {
                    "page_id": {"type": "string", "description": "Page ID"},
                },
                "required": ["page_id"],
            },
        ),
        # --- OneDrive ---
        Tool(
            name="microsoft_files_list",
            description="List files and folders in OneDrive. Omit path for root.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Folder path (e.g. '/Documents'). Omit for root."},
                    "count": {"type": "integer", "description": "Max items (default 20)"},
                },
            },
        ),
        Tool(
            name="microsoft_file_read",
            description="Read a text file from OneDrive (returns content for text files, download URL for others).",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path in OneDrive (e.g. '/Documents/notes.txt')"},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="microsoft_file_upload",
            description="Upload/create a text file in OneDrive (max 4MB).",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Destination path (e.g. '/Documents/notes.txt')"},
                    "content": {"type": "string", "description": "File content"},
                },
                "required": ["path", "content"],
            },
        ),
        Tool(
            name="microsoft_file_delete",
            description="Delete a file or folder from OneDrive.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to delete (e.g. '/Documents/old.txt')"},
                },
                "required": ["path"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    result = {}

    if name == "microsoft_configure":
        config = {
            "client_id": arguments["client_id"],
            "tenant_id": arguments["tenant_id"],
            "client_secret": arguments["client_secret"],
            "redirect_uri": "http://localhost:8080/auth/callback",
        }
        save_config(config)
        result = {
            "status": "configured",
            "message": "Credentials saved. Now use microsoft_login to authenticate.",
        }

    elif name == "microsoft_login":
        config = load_config()
        if not config.get("client_id"):
            result = {"error": "Not configured. Use microsoft_configure first."}
        else:
            msal_app, err = get_msal_app()
            if err:
                result = {"error": err}
            else:
                auth_url = msal_app.get_authorization_request_url(
                    SCOPES,
                    redirect_uri=config.get(
                        "redirect_uri", "http://localhost:8080/auth/callback"
                    ),
                )
                result = {
                    "status": "login_required",
                    "url": auth_url,
                    "message": "Open this URL in the browser to sign in. After sign-in, the token will be captured automatically.",
                }

    elif name == "microsoft_status":
        config = load_config()
        if not config.get("client_id"):
            result = {
                "status": "not_configured",
                "message": "Use microsoft_configure to set up credentials.",
            }
        else:
            tokens = load_tokens()
            if not tokens.get("refresh_token"):
                result = {
                    "status": "not_authenticated",
                    "message": "Configured but not logged in. Use microsoft_login.",
                }
            else:
                token, err = get_access_token()
                if err:
                    result = {"status": "auth_error", "message": err}
                else:
                    me = graph_request("GET", "/me")
                    if "error" in me:
                        result = {"status": "error", "message": str(me["error"])}
                    else:
                        result = {
                            "status": "authenticated",
                            "user": me.get("displayName"),
                            "email": me.get("userPrincipalName"),
                        }

    elif name == "microsoft_mail_list":
        count = min(arguments.get("count", 10), 50)
        folder = arguments.get("folder", "Inbox")
        params = f"$top={count}&$select=id,subject,from,receivedDateTime,isRead,bodyPreview&$orderby=receivedDateTime desc"
        if arguments.get("unread_only"):
            params += "&$filter=isRead eq false"
        data = graph_request("GET", f"/me/mailFolders/{folder}/messages?{params}")
        if "error" in data:
            result = data
        else:
            emails = []
            for msg in data.get("value", []):
                emails.append(
                    {
                        "id": msg["id"],
                        "subject": msg.get("subject", "(no subject)"),
                        "from": msg.get("from", {})
                        .get("emailAddress", {})
                        .get("address", "unknown"),
                        "date": msg.get("receivedDateTime", ""),
                        "read": msg.get("isRead", False),
                        "preview": msg.get("bodyPreview", "")[:200],
                    }
                )
            result = {"count": len(emails), "emails": emails}

    elif name == "microsoft_mail_read":
        data = graph_request(
            "GET",
            f"/me/messages/{arguments['message_id']}?$select=subject,from,toRecipients,receivedDateTime,body,isRead",
        )
        if "error" in data:
            result = data
        else:
            result = {
                "subject": data.get("subject"),
                "from": data.get("from", {}).get("emailAddress", {}).get("address"),
                "to": [
                    r.get("emailAddress", {}).get("address")
                    for r in data.get("toRecipients", [])
                ],
                "date": data.get("receivedDateTime"),
                "body": data.get("body", {}).get("content", ""),
            }

    elif name == "microsoft_mail_send":
        import base64, os, mimetypes
        payload = {
            "message": {
                "subject": arguments["subject"],
                "body": {"contentType": "Text", "content": arguments["body"]},
                "toRecipients": [{"emailAddress": {"address": arguments["to"]}}],
            }
        }
        attachment_path = arguments.get("attachment_path")
        if attachment_path and os.path.isfile(attachment_path):
            with open(attachment_path, "rb") as f:
                content_bytes = base64.b64encode(f.read()).decode("utf-8")
            content_type = mimetypes.guess_type(attachment_path)[0] or "application/octet-stream"
            payload["message"]["attachments"] = [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": os.path.basename(attachment_path),
                "contentType": content_type,
                "contentBytes": content_bytes,
            }]
        data = graph_request("POST", "/me/sendMail", json=payload)
        if "error" in data:
            result = data
        else:
            result = {
                "status": "sent",
                "to": arguments["to"],
                "subject": arguments["subject"],
            }

    elif name == "microsoft_calendar_list":
        from datetime import timedelta

        days = arguments.get("days", 7)
        count = arguments.get("count", 20)
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days)
        params = (
            f"startDateTime={now.strftime('%Y-%m-%dT%H:%M:%SZ')}&endDateTime={end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            f"&$top={count}&$select=id,subject,start,end,location,organizer,attendees,bodyPreview"
            f"&$orderby=start/dateTime"
        )
        data = graph_request("GET", f"/me/calendarView?{params}")
        if "error" in data:
            result = data
        else:
            events = []
            for ev in data.get("value", []):
                events.append(
                    {
                        "id": ev.get("id"),
                        "subject": ev.get("subject"),
                        "start": ev.get("start", {}).get("dateTime"),
                        "end": ev.get("end", {}).get("dateTime"),
                        "location": ev.get("location", {}).get("displayName", ""),
                        "preview": ev.get("bodyPreview", "")[:200],
                    }
                )
            result = {"count": len(events), "events": events}

    elif name == "microsoft_calendar_create":
        tz = arguments.get("timezone", "America/Los_Angeles")
        payload = {
            "subject": arguments["subject"],
            "start": {"dateTime": arguments["start"], "timeZone": tz},
            "end": {"dateTime": arguments["end"], "timeZone": tz},
        }
        if arguments.get("body"):
            payload["body"] = {"contentType": "Text", "content": arguments["body"]}
        if arguments.get("attendees"):
            payload["attendees"] = [
                {"emailAddress": {"address": a.strip()}, "type": "required"}
                for a in arguments["attendees"].split(",")
            ]
        if arguments.get("recurrence"):
            rec = arguments["recurrence"]
            parts = rec.split(":")
            pattern_type = parts[0].lower()
            interval = int(parts[1]) if len(parts) > 1 else 1
            rec_pattern = {"interval": interval}
            if pattern_type == "daily":
                rec_pattern["type"] = "daily"
            elif pattern_type == "weekly":
                rec_pattern["type"] = "weekly"
                # Default to the day of the start date
                from datetime import datetime as dt
                day_names = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
                start_day = dt.fromisoformat(arguments["start"].replace("Z","")).weekday()
                rec_pattern["daysOfWeek"] = [day_names[start_day]]
            elif pattern_type == "weekdays":
                rec_pattern["type"] = "weekly"
                rec_pattern["interval"] = 1
                rec_pattern["daysOfWeek"] = ["monday","tuesday","wednesday","thursday","friday"]
            elif pattern_type == "monthly":
                rec_pattern["type"] = "absoluteMonthly"
                rec_pattern["dayOfMonth"] = int(arguments["start"].split("T")[0].split("-")[2])
            elif pattern_type == "yearly":
                rec_pattern["type"] = "absoluteYearly"
                date_parts = arguments["start"].split("T")[0].split("-")
                rec_pattern["dayOfMonth"] = int(date_parts[2])
                rec_pattern["month"] = int(date_parts[1])
            start_date = arguments["start"].split("T")[0]
            end_date = arguments.get("recurrence_end", "")
            rec_range = {"type": "endDate", "startDate": start_date, "endDate": end_date} if end_date else {"type": "noEnd", "startDate": start_date}
            payload["recurrence"] = {"pattern": rec_pattern, "range": rec_range}
        data = graph_request("POST", "/me/events", json=payload)
        if "error" in data:
            result = data
        else:
            result = {
                "status": "created",
                "id": data.get("id"),
                "subject": data.get("subject"),
                "start": data.get("start", {}).get("dateTime"),
                "end": data.get("end", {}).get("dateTime"),
                "recurrence": data.get("recurrence"),
            }

    elif name == "microsoft_calendar_update":
        event_id = arguments["event_id"]
        payload = {}
        if arguments.get("subject"):
            payload["subject"] = arguments["subject"]
        if arguments.get("start") or arguments.get("end"):
            tz = arguments.get("timezone", "America/Los_Angeles")
            if arguments.get("start"):
                payload["start"] = {"dateTime": arguments["start"], "timeZone": tz}
            if arguments.get("end"):
                payload["end"] = {"dateTime": arguments["end"], "timeZone": tz}
        if arguments.get("body"):
            payload["body"] = {"contentType": "Text", "content": arguments["body"]}
        if arguments.get("attendees"):
            payload["attendees"] = [
                {"emailAddress": {"address": a.strip()}, "type": "required"}
                for a in arguments["attendees"].split(",")
            ]
        data = graph_request("PATCH", f"/me/events/{event_id}", json=payload)
        if "error" in data:
            result = data
        else:
            result = {
                "status": "updated",
                "subject": data.get("subject"),
                "start": data.get("start", {}).get("dateTime"),
                "end": data.get("end", {}).get("dateTime"),
            }

    elif name == "microsoft_calendar_delete":
        data = graph_request("DELETE", f"/me/events/{arguments['event_id']}")
        if "error" in data:
            result = data
        else:
            result = {"status": "deleted", "event_id": arguments["event_id"]}

    elif name == "microsoft_calendar_get":
        data = graph_request(
            "GET",
            f"/me/events/{arguments['event_id']}?$select=subject,start,end,location,organizer,attendees,body",
        )
        if "error" in data:
            result = data
        else:
            result = {
                "subject": data.get("subject"),
                "start": data.get("start", {}).get("dateTime"),
                "end": data.get("end", {}).get("dateTime"),
                "timezone": data.get("start", {}).get("timeZone"),
                "location": data.get("location", {}).get("displayName", ""),
                "organizer": data.get("organizer", {})
                .get("emailAddress", {})
                .get("address"),
                "attendees": [
                    a.get("emailAddress", {}).get("address")
                    for a in data.get("attendees", [])
                ],
                "body": data.get("body", {}).get("content", ""),
            }

    elif name == "microsoft_mail_reply":
        message_id = arguments["message_id"]
        payload = {"comment": arguments["body"]}
        data = graph_request("POST", f"/me/messages/{message_id}/reply", json=payload)
        if "error" in data:
            result = data
        else:
            result = {"status": "replied", "message_id": message_id}

    elif name == "microsoft_mail_search":
        query = arguments["search"]
        count = min(arguments.get("count", 10), 50)
        data = graph_request(
            "GET",
            f'/me/messages?$search="{query}"&$top={count}&$select=id,subject,from,receivedDateTime,isRead,bodyPreview',
        )
        if "error" in data:
            result = data
        else:
            emails = []
            for msg in data.get("value", []):
                emails.append(
                    {
                        "id": msg["id"],
                        "subject": msg.get("subject", "(no subject)"),
                        "from": msg.get("from", {})
                        .get("emailAddress", {})
                        .get("address", "unknown"),
                        "date": msg.get("receivedDateTime", ""),
                        "read": msg.get("isRead", False),
                        "preview": msg.get("bodyPreview", "")[:200],
                    }
                )
            result = {"count": len(emails), "emails": emails}

    # --- Shared calendars ---
    elif name == "microsoft_calendars_list":
        data = graph_request("GET", "/me/calendars?$select=id,name,owner,canEdit,isDefaultCalendar")
        if "error" in data:
            result = data
        else:
            result = {"calendars": [
                {
                    "id": c["id"],
                    "name": c.get("name"),
                    "owner": c.get("owner", {}).get("address"),
                    "canEdit": c.get("canEdit", False),
                    "isDefault": c.get("isDefaultCalendar", False),
                }
                for c in data.get("value", [])
            ]}

    elif name == "microsoft_calendar_shared_events":
        from datetime import timedelta
        cal_id = arguments["calendar_id"]
        days = arguments.get("days", 7)
        count = arguments.get("count", 20)
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days)
        params = (
            f"startDateTime={now.strftime('%Y-%m-%dT%H:%M:%SZ')}&endDateTime={end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            f"&$top={count}&$select=id,subject,start,end,location,organizer,bodyPreview"
            f"&$orderby=start/dateTime"
        )
        data = graph_request("GET", f"/me/calendars/{cal_id}/calendarView?{params}")
        if "error" in data:
            result = data
        else:
            result = {"events": [
                {
                    "id": ev.get("id"),
                    "subject": ev.get("subject"),
                    "start": ev.get("start", {}).get("dateTime"),
                    "end": ev.get("end", {}).get("dateTime"),
                    "location": ev.get("location", {}).get("displayName", ""),
                    "organizer": ev.get("organizer", {}).get("emailAddress", {}).get("address"),
                    "preview": ev.get("bodyPreview", "")[:200],
                }
                for ev in data.get("value", [])
            ]}

    # --- Shared mailbox ---
    elif name == "microsoft_shared_mailbox_list":
        mailbox = arguments["mailbox"]
        count = min(arguments.get("count", 10), 50)
        folder = arguments.get("folder", "Inbox")
        params = f"$top={count}&$select=id,subject,from,receivedDateTime,isRead,bodyPreview&$orderby=receivedDateTime desc"
        data = graph_request("GET", f"/users/{mailbox}/mailFolders/{folder}/messages?{params}")
        if "error" in data:
            result = data
        else:
            result = {"emails": [
                {
                    "id": msg["id"],
                    "subject": msg.get("subject", "(no subject)"),
                    "from": msg.get("from", {}).get("emailAddress", {}).get("address", "unknown"),
                    "date": msg.get("receivedDateTime", ""),
                    "read": msg.get("isRead", False),
                    "preview": msg.get("bodyPreview", "")[:200],
                }
                for msg in data.get("value", [])
            ]}

    elif name == "microsoft_shared_mailbox_send":
        mailbox = arguments["mailbox"]
        payload = {
            "message": {
                "subject": arguments["subject"],
                "body": {"contentType": "Text", "content": arguments["body"]},
                "toRecipients": [{"emailAddress": {"address": arguments["to"]}}],
                "from": {"emailAddress": {"address": mailbox}},
            }
        }
        data = graph_request("POST", f"/users/{mailbox}/sendMail", json=payload)
        if "error" in data:
            result = data
        else:
            result = {"status": "sent", "from": mailbox, "to": arguments["to"], "subject": arguments["subject"]}

    # --- Contacts ---
    elif name == "microsoft_contacts_list":
        count = arguments.get("count", 20)
        search = arguments.get("search")
        if search:
            params = f"$top={count}&$filter=startswith(displayName,'{search}') or startswith(emailAddresses/any(e:e/address),'{search}')&$select=id,displayName,emailAddresses,businessPhones,companyName"
            # Filter on emailAddresses can be tricky, fall back to search
            data = graph_request("GET", f'/me/contacts?$top={count}&$search="{search}"&$select=id,displayName,emailAddresses,businessPhones,companyName')
        else:
            data = graph_request("GET", f"/me/contacts?$top={count}&$select=id,displayName,emailAddresses,businessPhones,companyName&$orderby=displayName")
        if "error" in data:
            result = data
        else:
            result = {"contacts": [
                {
                    "id": c["id"],
                    "name": c.get("displayName"),
                    "emails": [e.get("address") for e in c.get("emailAddresses", [])],
                    "phones": c.get("businessPhones", []),
                    "company": c.get("companyName"),
                }
                for c in data.get("value", [])
            ]}

    elif name == "microsoft_contact_create":
        payload = {
            "givenName": arguments["given_name"],
            "emailAddresses": [{"address": arguments["email"]}],
        }
        if arguments.get("surname"):
            payload["surname"] = arguments["surname"]
        if arguments.get("phone"):
            payload["businessPhones"] = [arguments["phone"]]
        if arguments.get("company"):
            payload["companyName"] = arguments["company"]
        data = graph_request("POST", "/me/contacts", json=payload)
        if "error" in data:
            result = data
        else:
            result = {"status": "created", "id": data.get("id"), "name": data.get("displayName")}

    elif name == "microsoft_contact_delete":
        data = graph_request("DELETE", f"/me/contacts/{arguments['contact_id']}")
        if "error" in data:
            result = data
        else:
            result = {"status": "deleted", "contact_id": arguments["contact_id"]}

    # --- Tasks (To Do) ---
    elif name == "microsoft_task_lists":
        data = graph_request("GET", "/me/todo/lists")
        if "error" in data:
            result = data
        else:
            result = {"lists": [
                {"id": l["id"], "name": l.get("displayName"), "isOwner": l.get("isOwner", True)}
                for l in data.get("value", [])
            ]}

    elif name == "microsoft_tasks_list":
        list_id = arguments["list_id"]
        count = arguments.get("count", 20)
        filter_str = "" if arguments.get("include_completed") else "&$filter=status ne 'completed'"
        data = graph_request("GET", f"/me/todo/lists/{list_id}/tasks?$top={count}{filter_str}")
        if "error" in data:
            result = data
        else:
            result = {"tasks": [
                {
                    "id": t["id"],
                    "title": t.get("title"),
                    "status": t.get("status"),
                    "due": t.get("dueDateTime", {}).get("dateTime") if t.get("dueDateTime") else None,
                    "importance": t.get("importance"),
                    "body": (t.get("body", {}).get("content", "") or "")[:200],
                }
                for t in data.get("value", [])
            ]}

    elif name == "microsoft_task_create":
        list_id = arguments["list_id"]
        payload = {"title": arguments["title"]}
        if arguments.get("due_date"):
            payload["dueDateTime"] = {"dateTime": arguments["due_date"] + "T00:00:00", "timeZone": "UTC"}
        if arguments.get("body"):
            payload["body"] = {"contentType": "text", "content": arguments["body"]}
        data = graph_request("POST", f"/me/todo/lists/{list_id}/tasks", json=payload)
        if "error" in data:
            result = data
        else:
            result = {"status": "created", "id": data.get("id"), "title": data.get("title")}

    elif name == "microsoft_task_update":
        list_id = arguments["list_id"]
        task_id = arguments["task_id"]
        payload = {}
        if arguments.get("title"):
            payload["title"] = arguments["title"]
        if arguments.get("status"):
            payload["status"] = arguments["status"]
        if arguments.get("due_date"):
            payload["dueDateTime"] = {"dateTime": arguments["due_date"] + "T00:00:00", "timeZone": "UTC"}
        if arguments.get("body"):
            payload["body"] = {"contentType": "text", "content": arguments["body"]}
        data = graph_request("PATCH", f"/me/todo/lists/{list_id}/tasks/{task_id}", json=payload)
        if "error" in data:
            result = data
        else:
            result = {"status": "updated", "id": data.get("id"), "title": data.get("title")}

    elif name == "microsoft_task_delete":
        list_id = arguments["list_id"]
        task_id = arguments["task_id"]
        data = graph_request("DELETE", f"/me/todo/lists/{list_id}/tasks/{task_id}")
        if "error" in data:
            result = data
        else:
            result = {"status": "deleted", "task_id": task_id}

    # --- OneNote ---
    elif name == "microsoft_onenote_notebooks":
        data = graph_request("GET", "/me/onenote/notebooks?$select=id,displayName,lastModifiedDateTime")
        if "error" in data:
            result = data
        else:
            result = {"notebooks": [
                {"id": n["id"], "name": n.get("displayName"), "modified": n.get("lastModifiedDateTime")}
                for n in data.get("value", [])
            ]}

    elif name == "microsoft_onenote_notebook_create":
        nb_name = arguments["name"]
        data = graph_request("POST", "/me/onenote/notebooks", json={"displayName": nb_name})
        if "error" in data:
            result = data
        else:
            result = {"id": data.get("id"), "name": data.get("displayName")}

    elif name == "microsoft_onenote_sections":
        nb_id = arguments["notebook_id"]
        data = graph_request("GET", f"/me/onenote/notebooks/{nb_id}/sections?$select=id,displayName")
        if "error" in data:
            result = data
        else:
            result = {"sections": [
                {"id": s["id"], "name": s.get("displayName")}
                for s in data.get("value", [])
            ]}

    elif name == "microsoft_onenote_section_create":
        nb_id = arguments["notebook_id"]
        section_name = arguments["name"]
        data = graph_request("POST", f"/me/onenote/notebooks/{nb_id}/sections", json={"displayName": section_name})
        if "error" in data:
            result = data
        else:
            result = {"id": data.get("id"), "name": data.get("displayName")}

    elif name == "microsoft_onenote_pages":
        section_id = arguments["section_id"]
        count = arguments.get("count", 20)
        data = graph_request("GET", f"/me/onenote/sections/{section_id}/pages?$top={count}&$select=id,title,createdDateTime,lastModifiedDateTime")
        if "error" in data:
            result = data
        else:
            result = {"pages": [
                {"id": p["id"], "title": p.get("title"), "modified": p.get("lastModifiedDateTime")}
                for p in data.get("value", [])
            ]}

    elif name == "microsoft_onenote_page_read":
        page_id = arguments["page_id"]
        token, err = get_access_token()
        if err:
            result = {"error": err}
        else:
            headers = {"Authorization": f"Bearer {token}"}
            resp = requests.get(f"{GRAPH_BASE}/me/onenote/pages/{page_id}/content", headers=headers)
            if resp.status_code == 200:
                result = {"content": resp.text[:10000]}
            else:
                result = {"error": f"HTTP {resp.status_code}: {resp.text[:500]}"}

    elif name == "microsoft_onenote_page_create":
        section_id = arguments["section_id"]
        html = f"<!DOCTYPE html><html><head><title>{arguments['title']}</title></head><body><p>{arguments['content']}</p></body></html>"
        token, err = get_access_token()
        if err:
            result = {"error": err}
        else:
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "text/html"}
            resp = requests.post(f"{GRAPH_BASE}/me/onenote/sections/{section_id}/pages", headers=headers, data=html)
            if resp.status_code in (200, 201):
                data = resp.json()
                result = {"status": "created", "id": data.get("id"), "title": data.get("title")}
            else:
                result = {"error": f"HTTP {resp.status_code}: {resp.text[:500]}"}

    elif name == "microsoft_onenote_notebook_delete":
        # OneNote API doesn't support direct delete; remove the OneDrive folder instead
        nb_id = arguments["notebook_id"]
        nb = graph_request("GET", f"/me/onenote/notebooks/{nb_id}?$select=displayName")
        if isinstance(nb, dict) and "error" in nb:
            result = nb
        else:
            nb_name = nb.get("displayName", "")
            data = graph_request("DELETE", f"/me/drive/items/root:/Notebooks/{nb_name}:")
            if isinstance(data, dict) and "error" in data:
                result = {"error": f"Could not delete notebook via OneDrive. Name: {nb_name}"}
            else:
                result = {"status": "deleted", "notebook_id": nb_id}

    elif name == "microsoft_onenote_section_delete":
        # OneNote API doesn't support direct section delete; delete the underlying OneDrive item
        section_id = arguments["section_id"]
        section = graph_request("GET", f"/me/onenote/sections/{section_id}?$expand=parentNotebook($select=id,displayName)")
        if isinstance(section, dict) and "error" in section:
            result = section
        else:
            section_name = section.get("displayName", "")
            nb = section.get("parentNotebook", {}) or {}
            nb_name = nb.get("displayName", "")
            if nb_name and section_name:
                data = graph_request("DELETE", f"/me/drive/items/root:/Notebooks/{nb_name}/{section_name}.one:")
                if isinstance(data, dict) and "error" in data:
                    result = {"error": f"Could not delete section via OneDrive. Section: {section_name}, Notebook: {nb_name}"}
                else:
                    result = {"status": "deleted", "section_id": section_id}
            else:
                result = {"error": f"Could not resolve names. section={section_name!r}, notebook={nb_name!r}"}

    elif name == "microsoft_onenote_page_delete":
        page_id = arguments["page_id"]
        data = graph_request("DELETE", f"/me/onenote/pages/{page_id}")
        if isinstance(data, dict) and "error" in data:
            result = data
        else:
            result = {"status": "deleted", "page_id": page_id}

    # --- OneDrive ---
    elif name == "microsoft_files_list":
        path = arguments.get("path", "")
        count = arguments.get("count", 20)
        if path and path != "/":
            path = path.strip("/")
            endpoint = f"/me/drive/root:/{path}:/children?$top={count}&$select=id,name,size,lastModifiedDateTime,folder,file"
        else:
            endpoint = f"/me/drive/root/children?$top={count}&$select=id,name,size,lastModifiedDateTime,folder,file"
        data = graph_request("GET", endpoint)
        if "error" in data:
            result = data
        else:
            result = {"items": [
                {
                    "id": i["id"],
                    "name": i.get("name"),
                    "type": "folder" if "folder" in i else "file",
                    "size": i.get("size"),
                    "modified": i.get("lastModifiedDateTime"),
                }
                for i in data.get("value", [])
            ]}

    elif name == "microsoft_file_read":
        path = arguments["path"].strip("/")
        # Get file metadata + download URL
        data = graph_request("GET", f"/me/drive/root:/{path}")
        if "error" in data:
            result = data
        elif "folder" in data:
            result = {"error": "Path is a folder, not a file. Use microsoft_files_list instead."}
        else:
            download_url = data.get("@microsoft.graph.downloadUrl")
            mime = data.get("file", {}).get("mimeType", "")
            if download_url and ("text" in mime or mime in ("application/json", "application/xml", "application/javascript")):
                resp = requests.get(download_url)
                result = {"name": data.get("name"), "content": resp.text[:20000]}
            elif download_url:
                result = {"name": data.get("name"), "mimeType": mime, "size": data.get("size"), "downloadUrl": download_url}
            else:
                result = {"error": "Could not get download URL"}

    elif name == "microsoft_file_upload":
        path = arguments["path"].strip("/")
        content = arguments["content"]
        token, err = get_access_token()
        if err:
            result = {"error": err}
        else:
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "text/plain"}
            resp = requests.put(f"{GRAPH_BASE}/me/drive/root:/{path}:/content", headers=headers, data=content.encode("utf-8"))
            if resp.status_code in (200, 201):
                data = resp.json()
                result = {"status": "uploaded", "name": data.get("name"), "size": data.get("size")}
            else:
                result = {"error": f"HTTP {resp.status_code}: {resp.text[:500]}"}

    elif name == "microsoft_file_delete":
        path = arguments["path"].strip("/")
        data = graph_request("DELETE", f"/me/drive/root:/{path}")
        if "error" in data:
            result = data
        else:
            result = {"status": "deleted", "path": arguments["path"]}

    else:
        result = {"error": f"Unknown tool: {name}"}

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
