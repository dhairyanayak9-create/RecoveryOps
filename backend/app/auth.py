from functools import wraps
from flask import request, jsonify, current_app, g
import jwt
from datetime import datetime, timedelta, timezone
from .models import User
from . import db


def make_token(user):
    return jwt.encode({
        "sub": str(user.id),
        "agency_id": user.agency_id,
        "role": user.role,
        "device_id": user.device_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=12),
    }, current_app.config["JWT_SECRET_KEY"], algorithm="HS256")


def current_user():
    h = request.headers.get("Authorization", "")
    if not h.startswith("Bearer "):
        return None

    try:
        data = jwt.decode(
            h[7:],
            current_app.config["JWT_SECRET_KEY"],
            algorithms=["HS256"],
        )

        user = db.session.get(User, int(data["sub"]))
        if not user:
            return None

        # The agency comes from the database, not from a client-controlled
        # request parameter or token claim. The token claim is only a hint and
        # is deliberately not trusted for authorization.
        if not user.agency_id and user.role != "SUPER_ADMIN":
            return None

        token_agency = data.get("agency_id")
        if user.role != "SUPER_ADMIN" and token_agency is not None and int(token_agency) != int(user.agency_id):
            return None

        token_device = data.get("device_id")
        if (
            user.role == "REPO_AGENT"
            and token_device
            and user.device_id != token_device
        ):
            return None

        g.agency_id = None if user.role == "SUPER_ADMIN" else user.agency_id
        return user

    except Exception:
        return None


def auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if (
            not user
            or not user.active
            or getattr(user, "is_deleted", False)
            or (not getattr(user, "agency_id", None) and user.role != "SUPER_ADMIN")
        ):
            return jsonify({"error": "Authentication required"}), 401

        g.agency_id = None if user.role == "SUPER_ADMIN" else user.agency_id
        return fn(user, *args, **kwargs)

    return wrapper


def roles(*allowed):
    def deco(fn):
        @wraps(fn)
        def wrapper(user, *args, **kwargs):
            if user.role != "SUPER_ADMIN" and user.role not in allowed:
                return jsonify({"error": "Forbidden"}), 403
            g.agency_id = user.agency_id
            return fn(user, *args, **kwargs)

        return wrapper

    return deco
