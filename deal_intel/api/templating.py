"""Jinja2 templates and static files for the server-rendered UI."""

import re
from pathlib import Path

import jinja2
from fastapi import Request
from fastapi.templating import Jinja2Templates

from deal_intel.api.request_context import current_request_id
from deal_intel.contracts.api import DECISION_NOTE_MAX_CHARS
from deal_intel.contracts.reference import USER_ID_PATTERN
from deal_intel.contracts.runs import WAIT_STATES
from deal_intel.rendering.labels import label_text

UI_ROOT = Path(__file__).resolve().parents[1] / "ui"
TEMPLATES_DIR = UI_ROOT / "templates"
STATIC_DIR = UI_ROOT / "static"
STATIC_MOUNT_PATH = "/static"
STATIC_MOUNT_NAME = "static"
UI_PATH_PREFIX = "/ui"
# The simulated identity travels as this query parameter, exactly as the API receives it.
VIEWER_PARAM = "user_id"

NOT_FOUND_TEMPLATE = "not_found.html"
ERROR_TEMPLATE = "error.html"
NEW_RUN_TEMPLATE = "new_run.html"
BRIEF_TEMPLATE = "brief.html"
APPROVALS_TEMPLATE = "approvals.html"
TRACE_TEMPLATE = "trace.html"
APPROVAL_ROW_TEMPLATE = "partials/approval_row.html"
RUN_STATUS_TEMPLATE = "partials/run_status.html"
HTMX_REQUEST_HEADER = "hx-request"
HTMX_ENABLED = "true"
STATUS_POLL_SECONDS = 2
FRESH_FIELD = "fresh"

WELL_FORMED_USER_ID = re.compile(USER_ID_PATTERN)
_TERMINAL_STATUS_VALUES = frozenset(state.value for state in WAIT_STATES)


def polls_run_status(state: object) -> bool:
    """True while the run is still working, so HTMX does not reload a finished page."""
    value = getattr(state, "value", state)
    return value not in _TERMINAL_STATUS_VALUES


def is_ui_path(path: str) -> bool:
    return path == UI_PATH_PREFIX or path.startswith(f"{UI_PATH_PREFIX}/")


def viewer_id_of(request: Request) -> str | None:
    """The simulated identity, or None when absent or malformed, so pages never echo raw input."""
    candidate = request.query_params.get(VIEWER_PARAM)
    return candidate if candidate and WELL_FORMED_USER_ID.match(candidate) else None


def page_context(request: Request) -> dict[str, object]:
    return {
        "viewer_id": viewer_id_of(request),
        "viewer_param": VIEWER_PARAM,
        "request_id": current_request_id(),
        "static_prefix": STATIC_MOUNT_PATH,
        "ui_prefix": UI_PATH_PREFIX,
        "status_poll_seconds": STATUS_POLL_SECONDS,
        "decision_note_max_chars": DECISION_NOTE_MAX_CHARS,
    }


def is_htmx_request(request: Request) -> bool:
    return request.headers.get(HTMX_REQUEST_HEADER) == HTMX_ENABLED


def with_viewer(path: str, viewer_id: str | None) -> str:
    if not viewer_id:
        return path
    return f"{path}?{VIEWER_PARAM}={viewer_id}"


def build_environment() -> jinja2.Environment:
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES_DIR),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["approval_label"] = label_text
    environment.globals["polls_run_status"] = polls_run_status
    return environment


templates = Jinja2Templates(env=build_environment(), context_processors=[page_context])
