"""FastAPI route modules grouped by user-facing capability."""

from . import admin, chat, documents, reports, system

ROUTERS = (
    system.router,
    chat.router,
    documents.router,
    reports.router,
    admin.router,
)

__all__ = ["ROUTERS"]
