from functools import wraps
from flask import request, jsonify, current_app, g
import jwt
from datetime import datetime, timedelta, timezone
from .models import User, AgencyCompany
from . import db
from sqlalchemy import text, event


_tenant_routing_installed = set()


def _resolve_legacy_agency(user):
    if not user:
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


def _tenant_schema_for(agency_id):
    """One agency ID always maps to the same tenant schema: agency N -> tenant N."""
    if agency_id is None:
        return None
    try:
        aid = int(agency_id)
        if aid <= 0:
            return None
        # Single-user deployment keeps operational tables in public.
        if aid == 1:
            return "public"
        # Tenant identity is deliberately the same as the application's agency ID.
        # Keep agency_tenants as a compatibility registry, but never use a separate
        # modern/legacy ID to choose the tenant schema.
        row = db.session.execute(
            text("SELECT schema_name FROM public.agency_tenants WHERE legacy_agency_id=:id OR agency_id=:id LIMIT 1"),
            {"id": aid},
        ).scalar()
        expected = f"recovery_agency_{aid}"
        if row and str(row) == expected:
            return expected
        # If the compatibility row is stale/missing, the canonical schema is still
        # deterministic and can be provisioned by the agency provisioning flow.
        return expected
    except Exception:
        db.session.rollback()
        return None


def _install_tenant_sql_routing():
    """Route authenticated tenant SQL to its isolated agency schema."""
    engine = db.engine
    key = id(engine)
    if key in _tenant_routing_installed:
        return

    @event.listens_for(engine, "before_cursor_execute")
    def _route_tenant_sql(connection, cursor, statement, parameters, context, executemany):
        try:
            schema = getattr(g, "tenant_schema", None)
        except RuntimeError:
            schema = None
        target = schema if schema and schema.startswith("recovery_agency_") else "public"
        if not statement.lstrip().upper().startswith(("SET ", "SHOW ")):
            cursor.execute("SET search_path TO %s, public" % target)
        return statement, parameters

    _tenant_routing_installed.add(key)


def _install_repo_agent_access():
    try:
        from .repo_agent_access_fix import install
        install()
    except Exception:
        current_app.logger.exception("Repo Agent access bridge installation failed")


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

        if user.role == "SUPER_ADMIN":
            g.agency_id = None
            g.tenant_schema = None
            _install_tenant_sql_routing()
            return user

        resolved = _resolve_legacy_agency(user)
        if not resolved:
            return None
        token_device = data.get("device_id")
        if user.role == "REPO_AGENT" and token_device and user.device_id != token_device:
            return None
        g.agency_id = resolved
        g.tenant_schema = _tenant_schema_for(resolved)
        if not g.tenant_schema:
            return None
        _install_tenant_sql_routing()
        _install_repo_agent_access()
        return user
    except Exception:
        return None


def auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or not user.active or getattr(user, "is_deleted", False):
            return jsonify({"error": "Authentication required"}), 401

        if user.role == "SUPER_ADMIN":
            g.agency_id = None
            g.tenant_schema = None
            _install_tenant_sql_routing()
            return fn(user, *args, **kwargs)

        if not getattr(user, "agency_id", None):
            return jsonify({"error": "Authentication required"}), 401
        g.agency_id = user.agency_id
        g.tenant_schema = _tenant_schema_for(user.agency_id)
        if not g.tenant_schema:
            return jsonify({"error": "Agency data store is not provisioned"}), 503
        _install_tenant_sql_routing()
        _install_repo_agent_access()
        return fn(user, *args, **kwargs)
    return wrapper


def roles(*allowed):
    def deco(fn):
        @wraps(fn)
        def wrapper(user, *args, **kwargs):
            effective_role = "STAFF" if user.role == "OFFICE_STAFF" else user.role
            if user.role != "SUPER_ADMIN" and effective_role not in allowed:
                return jsonify({"error": "Forbidden"}), 403
            if user.role == "SUPER_ADMIN":
                g.agency_id = None
                g.tenant_schema = None
                _install_tenant_sql_routing()
                return fn(user, *args, **kwargs)
            g.agency_id = user.agency_id
            g.tenant_schema = _tenant_schema_for(user.agency_id)
            if not g.tenant_schema:
                return jsonify({"error": "Agency data store is not provisioned"}), 503
            _install_tenant_sql_routing()
            _install_repo_agent_access()
            return fn(user, *args, **kwargs)
        return wrapper
    return deco
