"""
JWT Authentication Module

JWT (JSON Web Token) = stateless auth.
Token chứa: user_id, role, expiry → không cần check DB mỗi request.

Flow:
    POST /auth/token  → trả về JWT
    GET  /ask         → gửi JWT trong header Authorization: Bearer <token>
    Server verify signature → extract user info → process request
"""
import hmac
import json
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

security = HTTPBearer(auto_error=False)


def _jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="JWT authentication is not configured")
    return secret


def create_token(username: str, role: str) -> str:
    """Tạo JWT token với expiry."""
    payload = {
        "sub": username,           # subject (user identifier)
        "role": role,
        "iat": datetime.now(timezone.utc),  # issued at
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=ALGORITHM)


def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)) -> dict:
    """
    Dependency: verify JWT token từ Authorization header.
    Raise HTTPException nếu token invalid hoặc expired.
    """
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Include: Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(credentials.credentials, _jwt_secret(), algorithms=[ALGORITHM])
        if not isinstance(payload, dict):
            raise jwt.InvalidTokenError("Token payload must be an object")
        username = payload.get("sub")
        role = payload.get("role")
        if not isinstance(username, str) or not isinstance(role, str):
            raise jwt.InvalidTokenError("Required claims are missing")
        return {
            "username": username,
            "role": role,
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Token expired. Please login again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail="Invalid token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def authenticate_user(username: str, password: str) -> dict:
    """Authenticate against the externally configured AUTH_USERS JSON object."""
    configured_users = os.getenv("AUTH_USERS", "")
    if not configured_users:
        raise HTTPException(status_code=503, detail="User authentication is not configured")
    try:
        users = json.loads(configured_users)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=503, detail="AUTH_USERS configuration is invalid") from exc
    if not isinstance(users, dict):
        raise HTTPException(status_code=503, detail="AUTH_USERS configuration is invalid")

    user = users.get(username)
    configured_password = user.get("password") if isinstance(user, dict) else None
    if not isinstance(configured_password, str) or not hmac.compare_digest(configured_password, password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    role = user.get("role")
    if not isinstance(role, str) or not role:
        raise HTTPException(status_code=503, detail="AUTH_USERS configuration is invalid")
    return {"username": username, "role": role}
