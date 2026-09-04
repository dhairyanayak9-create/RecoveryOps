from flask import Blueprint, request, jsonify, render_template, current_app
from sqlalchemy import text
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, timezone
import uuid, json, re, secrets, csv, io, os, tempfile, threading, hmac
import pandas as pd

from . import db
from .models import (
    User,
    Bank,
    Vehicle,
    DataAssignment,
    StatusHistory,
    Notification,
    SyncMutation,
    Client,
    RecoveryCase,
    AppFile,
    AgencyCompany,
    Challan,
    ChatConversation,
    ChatMessage,
    Report,
    UserOTP,
)
from .auth import make_token, auth_required, roles


api = Blueprint("api", __name__)
web = Blueprint("web", __name__)


def _import_job_directory(app=None):
    """Return the import-job directory, preferring a Railway Volume when mounted."""
    configured = os.getenv("RECOVERYOPS_IMPORT_DIR", "").strip()
    if configured:
        return os.path.abspath(configured)

    # Railway Volume convention: mount the volume at /app/data.
    # Fall back to the existing instance directory when no volume is mounted.
    volume_root = "/app/data"
    if os.path.isdir(volume_root) or os.path.exists(volume_root):
        return os.path.join(volume_root, "import_jobs")

    target_app = app or current_app
    return os.path.join(target_app.instance_path, "import_jobs")


def dt(v):
    return v.isoformat() if v else None


def normalize_email(v):
    v = (v or "").strip().lower()
    return v or None


def normalize_phone(v):
    digits = re.sub(r"\D", "", (v or "").strip())

    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]

    return digits or None


def get_registration_suffix(v):
    digits = re.sub(r"\D", "", (v or ""))
    return digits[-4:] if len(digits) >= 4 else None


def vehicle_json(v, bank_name=None):
    return {
        "id": v.id,
        "account_no": v.account_no,
        "loan_no": v.loan_no,
        "registration_no": v.registration_no,
        "chassis_no": v.chassis_no,
        "engine_no": v.engine_no,
        "customer_name": v.customer_name,
        "mobile": v.mobile,
        "city": v.city,
        "state": v.state,
        "pincode": v.pincode,
        "vehicle_type": v.vehicle_type,
        "bank_id": v.bank_id,
        "bank_name": (
            bank_name
            if bank_name is not None
            else (
                Bank.query.get(v.bank_id).name
                if v.bank_id and Bank.query.get(v.bank_id)
                else "N/A"
            )
        ),
        "status": v.status,
        "version": v.version,
        "source_file_id": v.source_file_id,
        "latitude": v.latitude,
        "longitude": v.longitude,
        "updated_at": dt(v.updated_at),
    }


def user_json(u):
    return {
        "id": u.id,
        "agency_id": u.agency_id,
        "username": u.username,
        "full_name": u.full_name,
        "email": u.email,
        "phone": u.phone,
        "role": u.role,
        "active": u.active,
        "is_deleted": u.is_deleted,
        "device_id": u.device_id,
        "created_at": dt(u.created_at),
    }


def accessible_vehicle_query(user, write=False):
    """Return vehicles visible to the authenticated user."""

    q = Vehicle.query

    # ADMIN, STAFF and SUPER_ADMIN have full operational access.
    if user.role in {"ADMIN", "STAFF", "SUPER_ADMIN"}:
        return q

    # Legacy DataAssignment schema is incompatible with the
    # production UUID-based public.data_assignment table.
    # Do not query it here until the assignment bridge is migrated.
    return q.filter(db.false())


def can_access(user, vehicle, write=False):
    return (
        accessible_vehicle_query(user, write)
        .filter(Vehicle.id == vehicle.id)
        .first()
        is not None
    )


@api.get("/health")
def health():
    """Lightweight deployment health check."""

    try:
        db.session.execute(text("SELECT 1"))

        return jsonify({
            "status": "ok",
            "database": "connected",
        })

    except Exception:
        db.session.rollback()

        return jsonify({
            "status": "error",
            "database": "unavailable",
        }), 503


@api.get("/auth/diagnostic/user")
def diagnostic_user():
    """Temporary authentication diagnostic."""

    diagnostic_key = os.getenv(
        "AUTH_DIAGNOSTIC_KEY",
        ""
    ).strip()

    supplied_key = request.headers.get(
        "X-Diagnostic-Key",
        ""
    ).strip()

    if (
        not diagnostic_key
        or not supplied_key
        or not hmac.compare_digest(
            supplied_key,
            diagnostic_key
        )
    ):
        return jsonify({
            "error": "Diagnostic access denied"
        }), 403

    email = normalize_email(
        request.args.get("email")
    )

    password = str(
        request.args.get("password") or ""
    )

    if not email:
        return jsonify({
            "error": "email query parameter is required"
        }), 400

    try:
        user = (
            User.query
            .filter(
                db.func.lower(User.email) == email
            )
            .first()
        )

        if not user:
            return jsonify({
                "found": False,
                "email": email,
                "message": (
                    "User not found in Railway "
                    "application database"
                ),
            }), 404

        password_match = False

        if user.password_hash and password:
            try:
                password_match = check_password_hash(
                    user.password_hash,
                    password,
                )
            except Exception:
                current_app.logger.exception(
                    "Diagnostic password verification "
                    "failed for user id=%s",
                    user.id,
                )

        return jsonify({
            "found": True,
            "database_user": {
                "id": user.id,
                "email": user.email,
                "username": user.username,
                "full_name": user.full_name,
                "phone": user.phone,
                "role": user.role,
                "active": bool(user.active),
                "is_deleted": bool(user.is_deleted),
                "agency_id": user.agency_id,
                "device_id_set": bool(user.device_id),
            },
            "password": {
                "supplied": bool(password),
                "hash_exists": bool(
                    user.password_hash
                ),
                "hash_algorithm": (
                    user.password_hash.split(":", 1)[0]
                    if user.password_hash
                    else None
                ),
                "matches": bool(password_match),
            },
            "diagnostic": {
                "login_should_pass_basic_credentials_check": bool(
                    user
                    and not user.is_deleted
                    and user.active
                    and password_match
                ),
            },
        })

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Authentication diagnostic failed "
            "for email=%s",
            email,
        )

        return jsonify({
            "error": "Diagnostic query failed"
        }), 500


# ============================================================
# AUTHENTICATION
# ============================================================

@api.post("/auth/login")
def login():
    data = request.get_json(
        silent=True
    ) or {}

    identifier = (
        data.get("username")
        or data.get("identifier")
        or data.get("email")
        or data.get("phone")
        or ""
    ).strip()

    password = str(
        data.get("password") or ""
    )

    if not identifier or not password:
        return jsonify({
            "error": "ID and password are required"
        }), 400

    email = normalize_email(identifier)
    phone = normalize_phone(identifier)

    try:
        conditions = [
            db.func.lower(User.username)
            == identifier.lower()
        ]

        if email:
            conditions.append(
                db.func.lower(User.email)
                == email
            )

        if phone:
            conditions.append(
                User.phone == phone
            )

        user = (
            User.query
            .filter(db.or_(*conditions))
            .first()
        )

        if (
            not user
            or user.is_deleted
            or not user.active
        ):
            current_app.logger.warning(
                "Login failed: invalid/inactive "
                "user identifier=%s",
                identifier,
            )

            return jsonify({
                "error": "Invalid ID or password"
            }), 401

        try:
            password_ok = check_password_hash(
                user.password_hash or "",
                password,
            )

        except Exception:
            current_app.logger.exception(
                "Password verification failed "
                "for user id=%s",
                user.id,
            )

            password_ok = False

        if not password_ok:
            current_app.logger.warning(
                "Login failed: password mismatch "
                "user_id=%s email=%s",
                user.id,
                user.email,
            )

            return jsonify({
                "error": "Invalid ID or password"
            }), 401

        # ----------------------------------------------------
        # LOGIN ROLE POLICY
        #
        # ADMIN       -> ALLOWED
        # STAFF       -> ALLOWED
        # SUPER_ADMIN -> ALLOWED
        # REPO_AGENT  -> APP ONLY
        # ----------------------------------------------------
        device_id = str(data.get("device_id") or "").strip()

        if user.role == "REPO_AGENT" and not device_id:
            current_app.logger.warning("Web login blocked for REPO_AGENT user_id=%s", user.id)
            return jsonify({"error": "REPO_AGENT login is available only from the application"}), 403

        if user.role not in {
            "ADMIN",
            "STAFF",
            "SUPER_ADMIN",
            "REPO_AGENT",
        }:
            current_app.logger.warning(
                "Login blocked: role=%s user_id=%s",
                user.role,
            )

            return jsonify({
                "error": "Login not allowed for this role"
            }), 403

        # Web login does not use device binding.
        # Clear any legacy device binding.
        if user.role != "REPO_AGENT" and user.device_id:
            user.device_id = None

        db.session.commit()

        return jsonify({
            "token": make_token(user),
            "user": user_json(user),
        })

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Unexpected login error"
        )

        raise


@api.post("/auth/logout")
@auth_required
def logout(user):
    user.device_id = None

    db.session.commit()

    return jsonify({
        "message": (
            "Logged out and device access released"
        )
    })


# ============================================================
# DASHBOARD
# ============================================================

@api.get("/dashboard")
@auth_required
def dashboard(user):
    base = accessible_vehicle_query(user)

    accessible_total = base.count()

    return jsonify({
        "total": Vehicle.query.count(),
        "accessible_total": accessible_total,
        "by_status": {
            s: base.filter(
                Vehicle.status == s
            ).count()
            for s in [
                "PENDING",
                "HOLD",
                "IN_YARD",
                "RELEASE",
            ]
        },
    })


# ============================================================
# BANKS
# ============================================================

@api.get("/banks")
@auth_required
def banks(user):
    return jsonify([
        {
            "id": b.id,
            "name": b.name,
            "code": b.code,
            "phone": b.phone,
            "email": b.email,
            "active": b.active,
            "created_at": dt(b.created_at),
        }
        for b in Bank.query.order_by(
            Bank.name
        ).all()
    ])


@api.post("/banks")
@auth_required
@roles("ADMIN", "STAFF")
def add_bank(user):
    d = request.get_json(
        silent=True
    ) or {}

    name = (
        d.get("name") or ""
    ).strip()

    code = (
        d.get("code") or ""
    ).strip()

    if not name:
        return jsonify({
            "error": "Bank name required"
        }), 400

    if Bank.query.filter(
        db.or_(
            Bank.name == name,
            Bank.code == code
            if code
            else False,
        )
    ).first():
        return jsonify({
            "error": "Bank already exists"
        }), 409

    b = Bank(
        name=name,
        code=code or None,
        phone=d.get("phone"),
        email=normalize_email(
            d.get("email")
        ),
        active=True,
    )

    db.session.add(b)
    db.session.commit()

    return jsonify({
        "id": b.id,
        "name": b.name,
        "code": b.code,
    }), 201


@api.put("/banks/<int:bank_id>")
@auth_required
@roles("ADMIN", "STAFF")
def update_bank(user, bank_id):
    b = Bank.query.get_or_404(
        bank_id
    )

    d = request.get_json(
        silent=True
    ) or {}

    name = (
        d.get("name") or b.name
    ).strip()

    code = (
        d.get("code")
        or b.code
        or ""
    ).strip() or None

    if not name:
        return jsonify({
            "error": "Bank name required"
        }), 400

    clash = Bank.query.filter(
        Bank.id != b.id,
        db.or_(
            Bank.name == name,
            Bank.code == code
            if code
            else False,
        )
    ).first()

    if clash:
        return jsonify({
            "error": (
                "Bank name or code already exists"
            )
        }), 409

    b.name = name
    b.code = code

    if "phone" in d:
        b.phone = d.get("phone")

    if "email" in d:
        b.email = normalize_email(
            d.get("email")
        )

    db.session.commit()

    return jsonify({
        "id": b.id,
        "name": b.name,
        "code": b.code,
        "active": b.active,
    })


@api.delete("/banks/<int:bank_id>")
@auth_required
@roles("ADMIN", "STAFF")
def delete_bank(user, bank_id):
    b = Bank.query.get_or_404(
        bank_id
    )

    if Vehicle.query.filter_by(
        bank_id=b.id
    ).first():
        return jsonify({
            "error": (
                "Bank has vehicle records. "
                "Delete/reassign those records first."
            )
        }), 409

    db.session.delete(b)
    db.session.commit()

    return jsonify({
        "message": "Bank deleted"
    })


# ============================================================
# VEHICLES
# ============================================================

@api.get("/vehicles/search")
@auth_required
def search(user):
    q = (
        request.args.get("q")
        or ""
    ).strip()

    field = request.args.get(
        "field",
        "registration_no"
    )

    if not q:
        return jsonify([])

    if field != "registration_no":
        field = "registration_no"

    digits = re.sub(
        r"\D",
        "",
        q
    )

    if len(digits) != 4:
        return jsonify([])

    rows = (
        accessible_vehicle_query(user)
        .filter(
            Vehicle.registration_suffix
            == digits
        )
        .order_by(
            Vehicle.registration_no.asc()
        )
        .limit(500)
        .all()
    )

    bank_ids = {
        v.bank_id
        for v in rows
        if v.bank_id
    }

    bank_map = {
        b.id: b.name
        for b in Bank.query.filter(
            Bank.id.in_(bank_ids)
        ).all()
    } if bank_ids else {}

    return jsonify([
        vehicle_json(v, bank_map.get(v.bank_id, "N/A"))
        for v in rows
    ])


@api.get("/vehicles")
@auth_required
def vehicles(user):
    try:
        limit = min(max(int(request.args.get("limit", 100)), 1), 500)
    except (TypeError, ValueError):
        limit = 100

    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0

    access_q = accessible_vehicle_query(user)
    total = access_q.count()
    rows = access_q.order_by(Vehicle.id.asc()).offset(offset).limit(limit).all()

    bank_ids = {v.bank_id for v in rows if v.bank_id}
    bank_map = {b.id: b.name for b in Bank.query.filter(Bank.id.in_(bank_ids)).all()} if bank_ids else {}

    return jsonify({
        "records": [vehicle_json(v, bank_map.get(v.bank_id, "N/A")) for v in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@api.get("/vehicles/export")
@auth_required
def export_vehicles(user):
    # Keep existing export implementation below.
    return _export_vehicles_impl(user)


# ============================================================
# USERS / REMAINDER
# ============================================================

# The existing routes.py continues below unchanged.
