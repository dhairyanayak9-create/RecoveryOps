from functools import wraps
from flask import request, jsonify, current_app, g
import jwt
from datetime import datetime, timedelta, timezone
from .models import User, AgencyCompany
from . import db
from sqlalchemy import text, event


_tenant_routing_installed = set()


def _resolve_legacy_agency(user):
    if not user or user.role == "SUPER_ADMIN":
        return None
    try:
        row = db.session.execute(text("""
            SELECT ag.name AS agency_name, ag.slug AS agency_slug
            FROM public.agency_members am
            JOIN public.agencies ag ON ag.id = am.agency_id
            WHERE am.user_id = :user_id
              AND am.is_active = true
              AND ag.is_active = true
            ORDER BY am.id DESC
            LIMIT 1
        """), {"user_id": user.id}).mappings().first()
        if not row:
            return user.agency_id
        company = AgencyCompany.query.filter(
            db.or_(AgencyCompany.name == row["agency_name"], AgencyCompany.code == row["agency_slug"])
        ).first()
        if not company:
            return user.agency_id
        if user.agency_id != company.id:
            user.agency_id = company.id
            db.session.flush()
        return company.id
    except Exception:
        db.session.rollback()
        return user.agency_id


def _install_tenant_sql_routing():
    """Route every PostgreSQL statement to the current authenticated agency schema."""
    engine = db.engine
    key = id(engine)
    if key in _tenant_routing_installed:
        return

    @event.listens_for(engine, "before_cursor_execute")
    def _route_tenant_sql(connection, cursor, statement, parameters, context, executemany):
        try:
            agency_id = getattr(g, "agency_id", None)
        except RuntimeError:
            agency_id = None

        schema = None
        if agency_id is not None:
            cache_key = f"recoveryops_tenant_schema:{int(agency_id)}"
            schema = connection.info.get(cache_key)
            if schema is None:
                row = connection.exec_driver_sql(
                    "SELECT schema_name FROM public.agency_tenants WHERE legacy_agency_id = %s LIMIT 1",
                    (int(agency_id),),
                ).fetchone()
                schema = str(row[0]) if row and str(row[0]).startswith("recovery_agency_") else ""
                connection.info[cache_key] = schema

        target = schema or "public"
        if not statement.lstrip().upper().startswith(("SET ", "SHOW ")):
            cursor.execute("SET search_path TO %s, public" % target)
        return statement, parameters

    _tenant_routing_installed.add(key)


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
        data = jwt.decode(h[7:], current_app.config["JWT_SECRET_KEY"], algorithms=["HS256"])
        user = db.session.get(User, int(data["sub"]))
        if not user:
            return None
        resolved = _resolve_legacy_agency(user)
        if user.role != "SUPER_ADMIN" and not resolved:
            return None
        token_device = data.get("device_id")
        if user.role == "REPO_AGENT" and token_device and user.device_id != token_device:
            return None
        g.agency_id = None if user.role == "SUPER_ADMIN" else resolved
        _install_tenant_sql_routing()
        return user
    except Exception:
        return None


def auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or not user.active or getattr(user, "is_deleted", False) or (not getattr(user, "agency_id", None) and user.role != "SUPER_ADMIN"):
            return jsonify({"error": "Authentication required"}), 401
        g.agency_id = None if user.role == "SUPER_ADMIN" else user.agency_id
        _install_tenant_sql_routing()
        return fn(user, *args, **kwargs)
    return wrapper


def roles(*allowed):
    def deco(fn):
        @wraps(fn)
        def wrapper(user, *args, **kwargs):
            if user.role != "SUPER_ADMIN" and user.role not in allowed:
                return jsonify({"error": "Forbidden"}), 403
            g.agency_id = None if user.role == "SUPER_ADMIN" else user.agency_id
            _install_tenant_sql_routing()
            return fn(user, *args, **kwargs)
        return wrapper
    return deco
