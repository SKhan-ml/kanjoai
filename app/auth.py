"""
Minimal API-key auth. Not enterprise SSO, but every endpoint that touches
financial data requires a key, and nothing sensitive is logged in
cleartext beyond what the audit trail itself needs.
"""
from typing import Optional

from fastapi import Header, HTTPException, status

from app.config import settings


def require_api_key(x_api_key: Optional[str] = Header(None)):
    if x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header.",
        )
