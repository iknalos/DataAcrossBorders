"""Demo authentication: three hardcoded users (one per role) and HS256 JWTs.
Plaintext demo passwords are intentional for the hackathon; swap for a real
user store + hashing (passlib) before anything resembling production.
"""

import os
import time

import jwt
from fastapi import Header, HTTPException

# Externalized secret (set DAB_JWT_SECRET in production); demo default keeps it runnable.
JWT_SECRET = os.environ.get("DAB_JWT_SECRET", "dab-demo-jwt-secret")
TOKEN_TTL_S = 8 * 3600

USERS = {
    "dr.chen": {"password": "demo123", "role": "clinician", "display": "Dr. Emily Chen (BCH Neurology)"},
    "res.kim": {"password": "demo123", "role": "researcher", "display": "M. Kim (Research Fellow)"},
    "admin": {"password": "demo123", "role": "admin", "display": "Federation Admin"},
}


def issue_token(username: str, password: str) -> dict:
    user = USERS.get(username)
    if not user or user["password"] != password:
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": int(time.time()) + TOKEN_TTL_S},
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"access_token": token, "token_type": "bearer", "role": user["role"], "display": user["display"]}


def current_user(authorization: str = Header(default="")) -> dict:
    """FastAPI dependency: validates `Authorization: Bearer <jwt>`."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    try:
        claims = jwt.decode(authorization[7:], JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")
    return {"username": claims["sub"], "role": claims["role"]}
