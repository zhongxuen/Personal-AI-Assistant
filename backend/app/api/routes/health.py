"""
Health check route.

This exists to prove basic API communication end-to-end (Phase 0). It
deliberately does not touch the LLM layer or any tool system.

`storage_mode` tells clients whether data written to this backend survives a restart:
`"local-durable"` on a desktop install (SQLite + local disk), `"ephemeral"` on the
Render deployment, whose free plan wipes the disk on every restart/redeploy. The web
app uses it to warn that conversations, memory and attachments made through the
deployed site are temporary and never reach the desktop install. Unauthenticated, like
the rest of this route -- it describes the deployment, not any user's data.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.database.database import get_db

router = APIRouter(tags=["health"])


# HEAD as well as GET: uptime/keep-warm pingers (cron-job.org, UptimeRobot, ...) often
# probe with HEAD, and a 405 there counts as a failed ping -- enough of those and
# cron-job.org auto-disables the job, letting Render's free instance spin down again.
@router.api_route("/health", methods=["GET", "HEAD"])
def health_check(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "service": "jarvis-backend",
        "storage_mode": get_settings().storage_mode,
    }
