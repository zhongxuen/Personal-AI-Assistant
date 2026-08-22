"""
Health check route.

This exists to prove basic API communication end-to-end (Phase 0). It
deliberately does not touch the LLM layer or any tool system — those
don't exist yet.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.database import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "service": "jarvis-backend",
    }
