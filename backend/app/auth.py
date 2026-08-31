from functools import wraps
from flask import request, jsonify, current_app
import jwt
from datetime import datetime, timedelta, timezone
from .models import User
from . import db

def make_token(user):
    return jwt.encode({
        "sub": str(user.id),
        "role": user.role,
        "device_id": user.device_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=12)
    }, current_app.config["JWT_SECRET_KEY"], algorithm="HS256")

def current_user():
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        return None
    try:
        data = jwt.decode(h[7:], current_app.config["JWT_SECRET_KEY"], algorithms=["HS256"])
        user = db.session.get(User, int(data["sub"]))
        if not user:
            return None
        token_device = data.get("device_id")
        if (
            user.role == "REPO_AGENT"
            and token_device
            and user.device_id != token_device
        ):
            return None
        return user
    except Exception:
        return None

def auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or not user.active or getattr(user, "is_deleted", False):
            return jsonify({"error": "Authentication required"}), 401
        return fn(user, *args, **kwargs)
    return wrapper

def roles(*allowed):
    def deco(fn):
        @wraps(fn)
        def wrapper(user, *args, **kwargs):
            if user.role not in allowed:
                return jsonify({"error": "Forbidden"}), 403
            return fn(user, *args, **kwargs)
        return wrapper
    return deco
