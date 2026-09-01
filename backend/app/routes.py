from flask import Blueprint, request, jsonify, render_template, current_app
from sqlalchemy import text
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, timezone
import uuid, json, re, secrets, csv, io, os, tempfile, threading
import pandas as pd
from . import db
from .models import User, Bank, Vehicle, DataAssignment, StatusHistory, Notification, SyncMutation, Client, RecoveryCase, AppFile, AgencyCompany, Challan, ChatConversation, ChatMessage, Report, UserOTP
from .auth import make_token, auth_required, roles

api = Blueprint("api", __name__)
web = Blueprint("web", __name__)


@web.get("/")
def home():
    return render_template("index.html")


def dt(v):
    return v.isoformat() if v else None


def normalize_email(v):
    v = (v or "").strip().lower()
    return v or None


def normalize_phone(v):
    import re
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
        "updated_at": dt(v.updated_at)
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
        "created_at": dt(u.created_at)
    }


def accessible_vehicle_query(user, write=False):
    """Return the vehicles visible to the user using SQL-side filtering."""
    q = Vehicle.query

    # ADMIN and OFFICE_STAFF retain full operational access.
    # REPO_AGENT users can read/sync/search the full dataset so the Android
    # app remains useful after login; their status-write permission remains
    # assignment-based below.
    if user.role in {"SUPER_ADMIN", "ADMIN", "OFFICE_STAFF"} or (
        user.role == "REPO_AGENT" and not write
    ):
        return q

    vehicle_ids = db.session.query(
        DataAssignment.vehicle_id
    ).filter(
        DataAssignment.user_id == user.id,
        DataAssignment.active.is_(True),
        DataAssignment.vehicle_id.isnot(None),
        DataAssignment.permission == "UPDATE_STATUS"
        if write
        else DataAssignment.permission.in_(["READ", "UPDATE_STATUS"])
    )

    bank_ids = db.session.query(
        DataAssignment.bank_id
    ).filter(
        DataAssignment.user_id == user.id,
        DataAssignment.active.is_(True),
        DataAssignment.bank_id.isnot(None),
        DataAssignment.permission == "UPDATE_STATUS"
        if write
        else DataAssignment.permission.in_(["READ", "UPDATE_STATUS"])
    )

    return q.filter(
        db.or_(
            Vehicle.id.in_(vehicle_ids),
            Vehicle.bank_id.in_(bank_ids)
        )
    )


def can_access(user, vehicle, write=False):
    return (
        accessible_vehicle_query(user, write)
        .filter(Vehicle.id == vehicle.id)
        .first()
        is not None
    )


@api.get("/health")
def health():
    """Lightweight deployment health check; never exposes credentials."""
    from sqlalchemy import text as sql_text
    try:
        db.session.execute(sql_text("SELECT 1"))
        return jsonify({"status": "ok", "database": "connected"})
    except Exception:
        db.session.rollback()
        return jsonify({"status": "error", "database": "unavailable"}), 503


@api.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}

    identifier = (
        data.get("username")
        or data.get("identifier")
        or ""
    ).strip()

    password = data.get("password", "")

    email = normalize_email(identifier)
    phone = normalize_phone(identifier)

    try:
        user = User.query.filter(
            db.or_(
                User.username == identifier,
                User.email == email,
                User.phone == phone
            )
        ).first()

        if (
            not user
            or user.is_deleted
            or not user.active
            or not check_password_hash(user.password_hash, password)
        ):
            return jsonify({"error": "Invalid ID or password"}), 401

        # ADMIN and OFFICE_STAFF are allowed to stay signed in on both
        # the website and Android app at the same time. REPO_AGENT remains
        # device-bound so the existing field/device access control is kept.
        device_id = (data.get("device_id") or "").strip()

        if user.role == "REPO_AGENT":
            if not device_id:
                return jsonify({"error": "Device ID is required."}), 400

            if user.device_id and user.device_id != device_id:
                return jsonify({
                    "error": "This user is already linked to another device. "
                             "Logout from the existing device or ask an administrator "
                             "to remove device access."
                }), 409

            if not user.device_id:
                user.device_id = device_id
                db.session.commit()
        else:
            # Clear an old device binding left by the previous one-device
            # policy so staff/admin tokens are fully independent.
            if user.device_id:
                user.device_id = None
                db.session.commit()

        return jsonify({
            "token": make_token(user),
            "user": user_json(user)
        })

    except Exception:
        db.session.rollback()
        raise


@api.post("/auth/logout")
@auth_required
def logout(user):
    # Explicit logout releases the one-device binding so the user can sign in on another device.
    user.device_id = None
    db.session.commit()

    return jsonify({
        "message": "Logged out and device access released"
    })


@api.get("/dashboard")
@auth_required
def dashboard(user):
    base = accessible_vehicle_query(user)

    accessible_total = base.count()
    return jsonify({
        "total": Vehicle.query.count(),
        "accessible_total": accessible_total,
        "by_status": {
            s: base.filter(Vehicle.status == s).count()
            for s in ["PENDING", "HOLD", "IN_YARD", "RELEASE"]
        }
    })


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
            "created_at": dt(b.created_at)
        }
        for b in Bank.query.order_by(Bank.name).all()
    ])


@api.post("/banks")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def add_bank(user):
    d = request.get_json(silent=True) or {}

    name = (d.get("name") or "").strip()
    code = (d.get("code") or "").strip()

    if not name:
        return jsonify({"error": "Bank name required"}), 400

    if Bank.query.filter(
        db.or_(
            Bank.name == name,
            Bank.code == code if code else False
        )
    ).first():
        return jsonify({"error": "Bank already exists"}), 409

    b = Bank(
        name=name,
        code=code or None,
        phone=d.get("phone"),
        email=normalize_email(d.get("email")),
        active=True
    )

    db.session.add(b)
    db.session.commit()

    return jsonify({
        "id": b.id,
        "name": b.name,
        "code": b.code
    }), 201


@api.put("/banks/<int:bank_id>")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def update_bank(user, bank_id):
    b = Bank.query.get_or_404(bank_id)
    d = request.get_json(silent=True) or {}

    name = (d.get("name") or b.name).strip()
    code = (d.get("code") or b.code or "").strip() or None

    if not name:
        return jsonify({"error": "Bank name required"}), 400

    clash = Bank.query.filter(
        Bank.id != b.id,
        db.or_(
            Bank.name == name,
            Bank.code == code if code else False
        )
    ).first()

    if clash:
        return jsonify({"error": "Bank name or code already exists"}), 409

    b.name = name
    b.code = code

    if "phone" in d:
        b.phone = d.get("phone")

    if "email" in d:
        b.email = normalize_email(d.get("email"))

    db.session.commit()

    return jsonify({
        "id": b.id,
        "name": b.name,
        "code": b.code,
        "active": b.active
    })


@api.delete("/banks/<int:bank_id>")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def delete_bank(user, bank_id):
    b = Bank.query.get_or_404(bank_id)

    if Vehicle.query.filter_by(bank_id=b.id).first():
        return jsonify({
            "error": "Bank has vehicle records. Delete/reassign those records first."
        }), 409

    db.session.delete(b)
    db.session.commit()

    return jsonify({"message": "Bank deleted"})


@api.get("/vehicles/search")
@auth_required
def search(user):
    q = (request.args.get("q") or "").strip()
    field = request.args.get("field", "registration_no")

    if not q:
        return jsonify([])

    if field != "registration_no":
        field = "registration_no"

    digits = re.sub(r"\D", "", q)

    if len(digits) != 4:
        return jsonify([])

    rows = (
        accessible_vehicle_query(user)
        .filter(Vehicle.registration_suffix == digits)
        .order_by(Vehicle.registration_no.asc())
        .limit(500)
        .all()
    )

    bank_ids = {v.bank_id for v in rows if v.bank_id}
    bank_map = {
        b.id: b.name
        for b in Bank.query.filter(Bank.id.in_(bank_ids)).all()
    } if bank_ids else {}

    return jsonify([
        vehicle_json(v, bank_map.get(v.bank_id, "N/A"))
        for v in rows
    ])


@api.get("/vehicles/<int:vehicle_id>")
@auth_required
def vehicle_detail(user, vehicle_id):
    v = Vehicle.query.get_or_404(vehicle_id)

    if not can_access(user, v):
        return jsonify({"error": "No access"}), 403

    return jsonify(vehicle_json(v))


@api.get("/vehicles/<int:vehicle_id>/history")
@auth_required
def vehicle_history(user, vehicle_id):
    v = Vehicle.query.get_or_404(vehicle_id)

    if not can_access(user, v):
        return jsonify({"error": "No access"}), 403

    rows = (
        StatusHistory.query
        .filter_by(vehicle_id=v.id)
        .order_by(StatusHistory.id.desc())
        .all()
    )

    return jsonify([
        {
            "id": x.id,
            "old_status": x.old_status,
            "new_status": x.new_status,
            "source": x.source,
            "user_id": x.user_id,
            "created_at": dt(x.created_at)
        }
        for x in rows
    ])


@api.delete("/vehicles/<int:vehicle_id>")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def delete_vehicle(user, vehicle_id):
    v = Vehicle.query.get_or_404(vehicle_id)

    DataAssignment.query.filter_by(
        vehicle_id=v.id
    ).delete(synchronize_session=False)

    StatusHistory.query.filter_by(
        vehicle_id=v.id
    ).delete(synchronize_session=False)

    SyncMutation.query.filter_by(
        vehicle_id=v.id
    ).delete(synchronize_session=False)

    RecoveryCase.query.filter_by(
        vehicle_id=v.id
    ).delete(synchronize_session=False)

    Challan.query.filter_by(
        vehicle_id=v.id
    ).delete(synchronize_session=False)

    db.session.delete(v)
    db.session.commit()

    return jsonify({"message": "Vehicle deleted"})


@api.post("/vehicles/bulk-delete")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def bulk_delete(user):
    ids = (request.get_json(silent=True) or {}).get("ids", [])

    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "ids required"}), 400

    count = 0

    for vid in ids:
        v = Vehicle.query.get(vid)

        if not v:
            continue

        DataAssignment.query.filter_by(
            vehicle_id=v.id
        ).delete(synchronize_session=False)

        StatusHistory.query.filter_by(
            vehicle_id=v.id
        ).delete(synchronize_session=False)

        SyncMutation.query.filter_by(
            vehicle_id=v.id
        ).delete(synchronize_session=False)

        db.session.delete(v)
        count += 1

    db.session.commit()

    return jsonify({"deleted": count})


@api.post("/vehicles/<int:vehicle_id>/status")
@auth_required
def update_status(user, vehicle_id):
    v = Vehicle.query.get_or_404(vehicle_id)

    if not can_access(user, v, write=True):
        return jsonify({"error": "No UPDATE_STATUS permission"}), 403

    new = (
        request.get_json(silent=True) or {}
    ).get("status")

    if new not in {"PENDING", "HOLD", "IN_YARD", "RELEASE"}:
        return jsonify({"error": "Invalid status"}), 400

    old = v.status
    v.status = new
    v.version = (v.version or 0) + 1

    db.session.add(
        StatusHistory(
            vehicle_id=v.id,
            user_id=user.id,
            old_status=old,
            new_status=new,
            source="WEB"
        )
    )

    db.session.commit()

    return jsonify(vehicle_json(v))


@api.get("/users")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def users(user):
    return jsonify([
        user_json(u)
        for u in User.query.order_by(User.id.desc()).all()
    ])


@api.post("/users/request-otp")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def request_user_otp(user):
    d = request.get_json(silent=True) or {}

    email = normalize_email(d.get("email"))
    phone = normalize_phone(d.get("phone"))

    if not email and not phone:
        return jsonify({
            "error": "Email or mobile is required"
        }), 400

    if email and User.query.filter_by(email=email).first():
        return jsonify({
            "error": "Email already registered"
        }), 409

    if phone and User.query.filter_by(phone=phone).first():
        return jsonify({
            "error": "Mobile number already registered"
        }), 409

    otp = f"{secrets.randbelow(1000000):06d}"

    UserOTP.query.filter_by(
        used=False
    ).update(
        {"used": True},
        synchronize_session=False
    )

    rec = UserOTP(
        email=email,
        phone=phone,
        otp_hash=generate_password_hash(otp),
        expires_at=datetime.now(timezone.utc).replace(microsecond=0)
    )

    # Five minute expiry; stored as UTC-aware value.
    from datetime import timedelta

    rec.expires_at = datetime.utcnow() + timedelta(minutes=5)

    db.session.add(rec)
    db.session.commit()

    # SMS/third-party OTP delivery is intentionally disabled. Keep the existing
    # OTP verification flow and development OTP response for user creation.
    return jsonify({
        "message": "OTP generated for verification.",
        "otp_dev": otp
        if __import__("os").getenv(
            "OTP_DEV_MODE",
            "true"
        ).lower() == "true"
        else None
    })


@api.post("/users")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def create_user(user):
    d = request.get_json(silent=True) or {}

    username = (d.get("username") or "").strip()
    email = normalize_email(d.get("email"))
    phone = normalize_phone(d.get("phone"))
    password = d.get("password", "")
    full_name = (d.get("full_name") or username).strip()
    role = (d.get("role") or "REPO_AGENT").strip().upper()
    otp = (d.get("otp") or "").strip()

    if not username or not password:
        return jsonify({
            "error": "Username and password are required"
        }), 400

    if not email and not phone:
        return jsonify({
            "error": "Email or mobile number is required"
        }), 400

    if len(password) < 8:
        return jsonify({
            "error": "Password must contain at least 8 characters"
        }), 400

    if role not in {"ADMIN", "REPO_AGENT", "OFFICE_STAFF"}:
        return jsonify({"error": "Invalid role"}), 400

    if not otp:
        return jsonify({
            "error": "OTP verification is required"
        }), 400

    rec = (
        UserOTP.query
        .filter_by(used=False)
        .order_by(UserOTP.id.desc())
        .first()
    )

    if (
        not rec
        or rec.expires_at < datetime.utcnow()
        or (email and rec.email != email)
        or (phone and rec.phone != phone)
        or not check_password_hash(rec.otp_hash, otp)
    ):
        return jsonify({
            "error": "Invalid or expired OTP"
        }), 400

    if User.query.filter_by(username=username).first():
        return jsonify({
            "error": "Username already registered"
        }), 409

    if email and User.query.filter_by(email=email).first():
        return jsonify({
            "error": "Email already registered"
        }), 409

    if phone and User.query.filter_by(phone=phone).first():
        return jsonify({
            "error": "Mobile number already registered"
        }), 409

    requested_agency_id = d.get("agency_id")
    if user.role == "SUPER_ADMIN":
        if requested_agency_id is None and role != "SUPER_ADMIN":
            return jsonify({"error": "agency_id is required for tenant users"}), 400
        target_agency_id = int(requested_agency_id) if requested_agency_id is not None else None
    else:
        target_agency_id = user.agency_id

    if role != "SUPER_ADMIN" and not target_agency_id:
        return jsonify({"error": "User must belong to an agency"}), 400

    if target_agency_id is not None and not AgencyCompany.query.get(target_agency_id):
        return jsonify({"error": "Agency not found"}), 404

    u = User(
        agency_id=target_agency_id,
        username=username,
        full_name=full_name,
        email=email,
        phone=phone,
        role=role,
        password_hash=generate_password_hash(password),
        active=bool(d.get("active", True)),
        is_deleted=False
    )

    db.session.add(u)
    rec.used = True

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({
            "error": "Username, email, or mobile is already registered"
        }), 409

    return jsonify(user_json(u)), 201


@api.patch("/users/<int:user_id>")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def update_user(user, user_id):
    u = User.query.get_or_404(user_id)
    d = request.get_json(silent=True) or {}

    if "username" in d:
        username = (d.get("username") or "").strip()
        if not username:
            return jsonify({"error": "Username is required"}), 400
        clash = User.query.filter(
            User.id != u.id,
            User.username == username
        ).first()
        if clash:
            return jsonify({"error": "Username already registered"}), 409
        u.username = username

    if "full_name" in d:
        u.full_name = (
            d["full_name"] or ""
        ).strip()

    if "role" in d:
        role = (d["role"] or "REPO_AGENT").upper()

        if role not in {
            "ADMIN",
            "REPO_AGENT",
            "OFFICE_STAFF"
        }:
            return jsonify({"error": "Invalid role"}), 400

        u.role = role

    if "email" in d:
        email = normalize_email(d.get("email"))

        if email and User.query.filter(
            User.id != u.id,
            User.email == email
        ).first():
            return jsonify({
                "error": "Email already registered"
            }), 409

        u.email = email

    if "phone" in d:
        phone = normalize_phone(d.get("phone"))

        if phone and User.query.filter(
            User.id != u.id,
            User.phone == phone
        ).first():
            return jsonify({
                "error": "Mobile number already registered"
            }), 409

        u.phone = phone

    if "active" in d:
        u.active = bool(d["active"])

    if "password" in d and d["password"]:
        u.password_hash = generate_password_hash(
            d["password"]
        )

    db.session.commit()

    return jsonify(user_json(u))


@api.delete("/users/<int:user_id>")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def delete_user(user, user_id):
    u = User.query.get_or_404(user_id)

    if u.id == user.id:
        return jsonify({
            "error": "You cannot delete your own account"
        }), 400

    if u.role == "ADMIN":
        return jsonify({
            "error": "Admin account cannot be deleted from this screen"
        }), 400

    u.active = False
    u.is_deleted = True

    db.session.commit()

    return jsonify({
        "message": "User deleted and login disabled"
    })


@api.get("/users/me")
@auth_required
def get_me(user):
    return jsonify(user_json(user))


@api.put("/users/me")
@auth_required
def update_me(user):
    d = request.get_json(silent=True) or {}

    if "username" in d:
        username = (d.get("username") or "").strip()
        if not username:
            return jsonify({"error": "Username is required"}), 400
        clash = User.query.filter(
            User.id != user.id,
            User.username == username
        ).first()
        if clash:
            return jsonify({"error": "Username already registered"}), 409
        user.username = username

    if "full_name" in d:
        user.full_name = (
            d.get("full_name") or ""
        ).strip() or user.full_name

    if "email" in d:
        email = normalize_email(d.get("email"))

        clash = (
            User.query.filter(
                User.id != user.id,
                User.email == email
            ).first()
            if email
            else None
        )

        if clash:
            return jsonify({
                "error": "Email already registered"
            }), 409

        user.email = email

    if "phone" in d:
        phone = normalize_phone(d.get("phone"))

        clash = (
            User.query.filter(
                User.id != user.id,
                User.phone == phone
            ).first()
            if phone
            else None
        )

        if clash:
            return jsonify({
                "error": "Mobile number already registered"
            }), 409

        user.phone = phone

    if "password" in d and (d.get("password") or "").strip():
        user.password_hash = generate_password_hash(
            d.get("password").strip()
        )

    db.session.commit()

    return jsonify({
        "user": user_json(user)
    })


@api.delete("/users/me/device")
@auth_required
def remove_my_device(user):
    user.device_id = None
    db.session.commit()

    return jsonify({
        "message": "Device access removed"
    })


@api.delete("/users/<int:user_id>/device")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def remove_user_device(user, user_id):
    u = User.query.get_or_404(user_id)

    u.device_id = None
    db.session.commit()

    return jsonify({
        "message": "Device access removed"
    })


@api.get("/assignments")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def assignments(user):
    return jsonify([
        {
            "id": a.id,
            "user_id": a.user_id,
            "bank_id": a.bank_id,
            "vehicle_id": a.vehicle_id,
            "permission": a.permission,
            "active": a.active
        }
        for a in DataAssignment.query.order_by(
            DataAssignment.id.desc()
        ).all()
    ])


@api.post("/assignments")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def create_assignment(user):
    d = request.get_json(silent=True) or {}

    if not d.get("user_id"):
        return jsonify({
            "error": "user_id required"
        }), 400

    target_user = User.query.get(d["user_id"])
    if not target_user:
        return jsonify({"error": "User not found in this agency"}), 404

    bank_id = d.get("bank_id")
    if bank_id is not None:
        bank = Bank.query.get(bank_id)
        if not bank:
            return jsonify({"error": "Bank not found in this agency"}), 404

    vehicle_id = d.get("vehicle_id")
    if vehicle_id is not None:
        vehicle = Vehicle.query.get(vehicle_id)
        if not vehicle:
            return jsonify({"error": "Vehicle not found in this agency"}), 404

    permission = (d.get("permission") or "READ").strip().upper()
    if permission not in {"READ", "UPDATE_STATUS"}:
        return jsonify({"error": "Invalid permission"}), 400

    a = DataAssignment(
        user_id=target_user.id,
        bank_id=bank_id,
        vehicle_id=vehicle_id,
        permission=permission,
        active=True
    )

    db.session.add(a)
    db.session.commit()

    return jsonify({
        "id": a.id
    }), 201


@api.get("/notifications")
@auth_required
def notifications(user):
    rows = (
        Notification.query
        .filter_by(user_id=user.id)
        .order_by(Notification.id.desc())
        .limit(100)
        .all()
    )

    return jsonify([
        {
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "type": n.type,
            "read": bool(n.read_at),
            "created_at": dt(n.created_at)
        }
        for n in rows
    ])


@api.get("/sync/download")
@auth_required
def sync_download(user):
    try:
        after_version = int(
            request.args.get("after_version", 0)
        )

        after_id = int(
            request.args.get("after_id", 0)
        )

        limit = min(
            max(
                int(request.args.get("limit", 5000)),
                1
            ),
            5000
        )

    except ValueError:
        return jsonify({
            "error": "Invalid sync cursor"
        }), 400

    access_q = accessible_vehicle_query(user)
    total = access_q.count()

    cursor_filter = db.or_(
        Vehicle.version > after_version,
        db.and_(
            Vehicle.version == after_version,
            Vehicle.id > after_id
        )
    )

    rows = (
        access_q
        .filter(cursor_filter)
        .order_by(
            Vehicle.version.asc(),
            Vehicle.id.asc()
        )
        .limit(limit)
        .all()
    )

    if rows:
        last = rows[-1]
        next_version = last.version or 0
        next_id = last.id
    else:
        next_version = after_version
        next_id = after_id

    more = (
        access_q
        .filter(
            db.or_(
                Vehicle.version > next_version,
                db.and_(
                    Vehicle.version == next_version,
                    Vehicle.id > next_id
                )
            )
        )
        .first()
        is not None
    )

    bank_ids = {v.bank_id for v in rows if v.bank_id}
    bank_map = {
        b.id: b.name
        for b in Bank.query.filter(Bank.id.in_(bank_ids)).all()
    } if bank_ids else {}

    return jsonify({
        "records": [
            vehicle_json(v, bank_map.get(v.bank_id, "N/A"))
            for v in rows
        ],
        "next_version": next_version,
        "next_id": next_id,
        "has_more": more,
        "total": total
    })


@api.post("/sync/mutations")
@auth_required
def sync_mutations(user):
    items = (
        request.get_json(silent=True) or {}
    ).get("mutations", [])

    results = []

    for item in items:
        mid = str(
            item.get("id")
            or uuid.uuid4()
        )

        if SyncMutation.query.get(mid):
            results.append({
                "id": mid,
                "status": "already_applied"
            })
            continue

        v = Vehicle.query.get(
            item.get("vehicle_id")
        )

        if not v or not can_access(
            user,
            v,
            write=True
        ):
            results.append({
                "id": mid,
                "status": "forbidden"
            })
            continue

        new = item.get("new_status")

        if new not in {
            "PENDING",
            "HOLD",
            "IN_YARD",
            "RELEASE"
        }:
            results.append({
                "id": mid,
                "status": "invalid"
            })
            continue

        old = v.status
        v.status = new
        v.version = (v.version or 0) + 1

        db.session.add(
            SyncMutation(
                id=mid,
                user_id=user.id,
                vehicle_id=v.id,
                new_status=new,
                applied_at=datetime.now(timezone.utc)
            )
        )

        db.session.add(
            StatusHistory(
                vehicle_id=v.id,
                user_id=user.id,
                old_status=old,
                new_status=new,
                source="OFFLINE"
            )
        )

        results.append({
            "id": mid,
            "status": "applied",
            "vehicle": vehicle_json(v)
        })

    db.session.commit()

    return jsonify({
        "results": results
    })


def _process_import_job(app, file_id, temp_path, bank_id, filename, vehicle_type, uploaded_by, agency_id):
    """Process a bank file outside the HTTP request so reverse proxies do not return 502."""
    with app.app_context():
        from flask import g
        g.agency_id = agency_id
        af = None
        try:
            af = AppFile.query.get(file_id)
            if not af:
                return

            name = filename.lower()
            df = pd.read_csv(temp_path) if name.endswith(".csv") else pd.read_excel(temp_path)

            aliases = {
                "account_no": ["account_no", "account number", "account", "acct_no"],
                "loan_no": ["loan_no", "loan number", "loan"],
                "registration_no": ["registration_no", "registration number", "reg_no", "vehicle_no"],
                "chassis_no": ["chassis_no", "chassis number", "chassis"],
                "engine_no": ["engine_no", "engine number", "engine"],
                "customer_name": ["customer_name", "customer full name", "name"],
                "mobile": ["mobile", "mobile_no", "phone"],
                "city": ["city"],
                "state": ["state"],
                "pincode": ["pincode", "pin", "pin_code"],
                "vehicle_type": ["vehicle_type", "vehicle type", "type"],
                "latitude": ["latitude", "lat"],
                "longitude": ["longitude", "lng", "lon"]
            }
            normalized = {str(c).strip().lower(): c for c in df.columns}
            mapping = {}
            for target, opts in aliases.items():
                for opt in opts:
                    if opt in normalized:
                        mapping[target] = normalized[opt]
                        break

            if not any(k in mapping for k in ("registration_no", "chassis_no", "account_no")):
                raise ValueError("File must contain Registration Number, Chassis Number, or Account Number column.")

            # Replace the selected bank's previous imported file(s) using set-based deletes.
            old_files = AppFile.query.filter_by(bank_id=bank_id).all()
            old_vehicle_count = sum(
                Vehicle.query.filter_by(source_file_id=old_file.id).count()
                for old_file in old_files
            )
            for old_file in old_files:
                params = {"file_id": old_file.id}
                for table in ("data_assignment", "status_history", "sync_mutation", "recovery_case", "challan"):
                    db.session.execute(text(
                        f"DELETE FROM {table} WHERE vehicle_id IN "
                        "(SELECT id FROM vehicle WHERE source_file_id = :file_id)"
                    ), params)
                db.session.execute(text("DELETE FROM vehicle WHERE source_file_id = :file_id"), params)
                if old_file.id != af.id:
                    db.session.delete(old_file)
            db.session.commit()

            created = dup = invalid = 0
            existing_regs = {x[0] for x in Vehicle.query.with_entities(Vehicle.registration_no).filter(Vehicle.registration_no.isnot(None)).all()}
            existing_chassis = {x[0] for x in Vehicle.query.with_entities(Vehicle.chassis_no).filter(Vehicle.chassis_no.isnot(None)).all()}
            existing_accounts = {x[0] for x in Vehicle.query.with_entities(Vehicle.account_no).filter(Vehicle.account_no.isnot(None)).all()}

            def value(row, key):
                col = mapping.get(key)
                if not col or col not in row.index:
                    return None
                x = row[col]
                if pd.isna(x):
                    return None
                return str(x).strip() or None

            def number(row, key):
                x = value(row, key)
                try:
                    return float(x) if x is not None else None
                except (TypeError, ValueError):
                    return None

            batch = []
            BATCH_SIZE = 5000
            requested_vehicle_type = (vehicle_type or "").strip() or None

            for _, row in df.iterrows():
                reg = value(row, "registration_no")
                chassis = value(row, "chassis_no")
                account = value(row, "account_no")
                if not any((reg, chassis, account)):
                    invalid += 1
                    continue
                if (reg and reg in existing_regs) or (chassis and chassis in existing_chassis) or (account and account in existing_accounts):
                    dup += 1
                    continue

                batch.append({
                    "agency_id": agency_id,
                    "account_no": account,
                    "loan_no": value(row, "loan_no"),
                    "registration_no": reg,
                    "registration_suffix": get_registration_suffix(reg),
                    "chassis_no": chassis,
                    "engine_no": value(row, "engine_no"),
                    "customer_name": value(row, "customer_name"),
                    "mobile": value(row, "mobile"),
                    "city": value(row, "city"),
                    "state": value(row, "state"),
                    "pincode": value(row, "pincode"),
                    "vehicle_type": value(row, "vehicle_type") or requested_vehicle_type,
                    "latitude": number(row, "latitude"),
                    "longitude": number(row, "longitude"),
                    "bank_id": bank_id,
                    "source_file_id": af.id
                })
                if reg: existing_regs.add(reg)
                if chassis: existing_chassis.add(chassis)
                if account: existing_accounts.add(account)
                created += 1

                if len(batch) >= BATCH_SIZE:
                    db.session.bulk_insert_mappings(Vehicle, batch)
                    db.session.commit()
                    batch = []
                    af = AppFile.query.get(file_id)
                    if af:
                        af.record_count = created
                        db.session.commit()

            if batch:
                db.session.bulk_insert_mappings(Vehicle, batch)
                db.session.commit()

            af = AppFile.query.get(file_id)
            if af:
                af.record_count = created
                af.status = "IMPORTED"
                db.session.commit()

                recipients = User.query.filter_by(active=True, is_deleted=False).with_entities(User.id).all()
                if recipients:
                    db.session.bulk_insert_mappings(
                        Notification,
                        [{
                            "agency_id": agency_id,
                            "user_id": rid,
                            "title": "New file uploaded",
                            "message": f"{filename} uploaded successfully with {created:,} records. Existing bank file replaced.",
                            "type": "UPLOAD"
                        } for (rid,) in recipients]
                    )
                    db.session.commit()

        except Exception:
            db.session.rollback()
            current_app.logger.exception("File import failed for bank %s", bank_id)
            try:
                af = AppFile.query.get(file_id)
                if af:
                    af.status = "FAILED"
                    db.session.commit()
            except Exception:
                db.session.rollback()
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            db.session.remove()


@api.post("/files/import")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def import_file(user):
    """Queue one bank-file replacement and return immediately.

    The actual pandas import runs in a background application-context thread,
    preventing a large replacement from being cut off by a hosting proxy.
    """
    if "file" not in request.files:
        return jsonify({"error": "file is required"}), 400

    bank_id = request.form.get("bank_id")
    try:
        bank_id = int(bank_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid bank_id"}), 400

    bank = Bank.query.get(bank_id)
    if not bank:
        return jsonify({"error": "Selected bank does not exist."}), 404

    f = request.files["file"]
    filename = (f.filename or "").strip()
    name = filename.lower()
    if not filename:
        return jsonify({"error": "File name is required."}), 400
    if not name.endswith((".csv", ".xlsx", ".xls")):
        return jsonify({"error": "Only CSV, XLSX and XLS files are supported."}), 400

    max_bytes = 50 * 1024 * 1024
    content_length = request.content_length or 0
    if content_length > max_bytes:
        return jsonify({"error": "File size exceeds the 50 MB upload limit.", "max_bytes": max_bytes}), 413

    job_dir = os.path.join(current_app.instance_path, "import_jobs")
    os.makedirs(job_dir, exist_ok=True)
    temp_path = os.path.join(job_dir, f"{uuid.uuid4().hex}_{filename}")

    try:
        f.save(temp_path)
        af = AppFile(
            filename=filename,
            vehicle_type=(request.form.get("vehicle_type") or "").strip() or None,
            bank_id=bank_id,
            record_count=0,
            status="IMPORTING",
            uploaded_by=user.id
        )
        db.session.add(af)
        db.session.commit()
        file_id = af.id
        app_obj = current_app._get_current_object()
        threading.Thread(
            target=_process_import_job,
            args=(app_obj, file_id, temp_path, bank_id, filename, request.form.get("vehicle_type"), user.id, user.agency_id),
            daemon=True
        ).start()
        return jsonify({
            "file_id": file_id,
            "status": "IMPORTING",
            "message": "Upload received. Import is running in the background."
        }), 202
    except Exception as e:
        db.session.rollback()
        try:
            os.remove(temp_path)
        except OSError:
            pass
        current_app.logger.exception("Could not queue file import")
        return jsonify({"error": f"Could not start file import: {e}"}), 500


# ---- Expanded application modules ----

def obj_json(o, fields):
    return {
        k: getattr(o, k)
        for k in fields
    }


def admin_or(user, *roles_allowed):
    return (
        user.role == "ADMIN"
        or user.role in roles_allowed
    )


@api.get("/clients")
@auth_required
def get_clients(user):
    return jsonify([
        obj_json(
            x,
            [
                "id",
                "name",
                "code",
                "phone",
                "email",
                "active",
                "created_at"
            ]
        )
        for x in Client.query.order_by(
            Client.id.desc()
        ).all()
    ])


@api.post("/clients")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def add_client(user):
    d = request.get_json(silent=True) or {}

    name = (d.get("name") or "").strip()

    if not name:
        return jsonify({
            "error": "Client name required"
        }), 400

    c = Client(
        name=name,
        code=(
            d.get("code") or ""
        ).strip() or None,
        phone=normalize_phone(
            d.get("phone")
        ),
        email=normalize_email(
            d.get("email")
        )
    )

    db.session.add(c)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

        return jsonify({
            "error": "Client code already exists"
        }), 409

    return jsonify(
        obj_json(
            c,
            [
                "id",
                "name",
                "code",
                "phone",
                "email",
                "active"
            ]
        )
    ), 201


@api.delete("/clients/<int:client_id>")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def delete_client(user, client_id):
    c = Client.query.get_or_404(client_id)

    db.session.delete(c)
    db.session.commit()

    return jsonify({
        "message": "Client deleted"
    })


@api.get("/recovery")
@auth_required
def get_recovery(user):
    q = RecoveryCase.query.order_by(
        RecoveryCase.id.desc()
    )

    rows = q.limit(500).all()

    return jsonify([
        obj_json(
            x,
            [
                "id",
                "vehicle_id",
                "client_id",
                "assigned_user_id",
                "stage",
                "notes",
                "next_action_at",
                "created_at",
                "updated_at"
            ]
        )
        for x in rows
    ])


@api.post("/recovery")
@auth_required
def add_recovery(user):
    d = request.get_json(silent=True) or {}

    if not d.get("vehicle_id"):
        return jsonify({
            "error": "vehicle_id required"
        }), 400

    v = Vehicle.query.get_or_404(
        d["vehicle_id"]
    )

    if not can_access(
        user,
        v,
        write=True
    ):
        return jsonify({
            "error": "No access"
        }), 403

    client_id = d.get("client_id")
    if client_id is not None and not Client.query.get(client_id):
        return jsonify({"error": "Client not found in this agency"}), 404

    assigned_user_id = d.get("assigned_user_id") or user.id
    assigned_user = User.query.get(assigned_user_id)
    if not assigned_user:
        return jsonify({"error": "Assigned user not found in this agency"}), 404

    r = RecoveryCase(
        vehicle_id=v.id,
        client_id=client_id,
        assigned_user_id=assigned_user.id,
        stage=d.get(
            "stage",
            "OPEN"
        ),
        notes=d.get("notes")
    )

    db.session.add(r)
    db.session.commit()

    return jsonify(
        obj_json(
            r,
            [
                "id",
                "vehicle_id",
                "client_id",
                "assigned_user_id",
                "stage",
                "notes"
            ]
        )
    ), 201


@api.delete("/recovery/<int:case_id>")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def delete_recovery(user, case_id):
    r = RecoveryCase.query.get_or_404(
        case_id
    )

    db.session.delete(r)
    db.session.commit()

    return jsonify({
        "message": "Recovery case deleted"
    })


@api.get("/files")
@auth_required
def files_list(user):
    q = AppFile.query.order_by(
        AppFile.id.desc()
    )

    if user.role != "ADMIN":
        bank_ids = db.session.query(
            DataAssignment.bank_id
        ).filter(
            DataAssignment.user_id == user.id,
            DataAssignment.active.is_(True),
            DataAssignment.bank_id.isnot(None)
        )

        q = q.filter(
            AppFile.bank_id.in_(bank_ids)
        )

    return jsonify([
        obj_json(
            x,
            [
                "id",
                "filename",
                "vehicle_type",
                "bank_id",
                "record_count",
                "status",
                "uploaded_by",
                "created_at"
            ]
        )
        for x in q.all()
    ])


@api.get("/files/<int:file_id>")
@auth_required
def file_status(user, file_id):
    f = AppFile.query.get_or_404(file_id)
    if user.role not in {"ADMIN", "OFFICE_STAFF"}:
        bank_ids = db.session.query(DataAssignment.bank_id).filter(
            DataAssignment.user_id == user.id,
            DataAssignment.active.is_(True),
            DataAssignment.bank_id.isnot(None)
        )
        if f.bank_id not in [x[0] for x in bank_ids.all()]:
            return jsonify({"error": "No access"}), 403
    return jsonify({
        "id": f.id,
        "filename": f.filename,
        "status": f.status,
        "record_count": f.record_count or 0,
        "bank_id": f.bank_id
    })


@api.delete("/files/<int:file_id>")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def delete_file(user, file_id):
    f = AppFile.query.get_or_404(file_id)

    vehicles = Vehicle.query.filter_by(
        source_file_id=f.id
    ).all()

    for v in vehicles:
        DataAssignment.query.filter_by(
            vehicle_id=v.id
        ).delete(
            synchronize_session=False
        )

        StatusHistory.query.filter_by(
            vehicle_id=v.id
        ).delete(
            synchronize_session=False
        )

        SyncMutation.query.filter_by(
            vehicle_id=v.id
        ).delete(
            synchronize_session=False
        )

        db.session.delete(v)

    db.session.delete(f)
    db.session.commit()

    return jsonify({
        "message": (
            "File and its imported vehicle "
            "records deleted"
        ),
        "vehicles_deleted": len(vehicles)
    })


@api.get("/agencies")
@auth_required
def agencies(user):
    fields = ["id", "name", "code", "phone", "email", "active", "created_at"]
    if user.role == "SUPER_ADMIN":
        return jsonify([obj_json(a, fields) for a in AgencyCompany.query.order_by(AgencyCompany.id.asc()).all()])
    a = AgencyCompany.query.get_or_404(user.agency_id)
    return jsonify([obj_json(a, fields)])


@api.post("/agencies")
@auth_required
@roles("ADMIN")
def add_agency(user):
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    code = (d.get("code") or "").strip() or None
    if not name:
        return jsonify({"error": "Agency name is required"}), 400
    if AgencyCompany.query.filter(AgencyCompany.name == name).first():
        return jsonify({"error": "Agency already exists"}), 409
    if code and AgencyCompany.query.filter(AgencyCompany.code == code).first():
        return jsonify({"error": "Agency code already exists"}), 409
    a = AgencyCompany(name=name, code=code, phone=d.get("phone"), email=normalize_email(d.get("email")))
    db.session.add(a)
    db.session.commit()
    return jsonify(obj_json(a, ["id", "name", "code", "phone", "email", "active", "created_at"])), 201


@api.delete("/agencies/<int:agency_id>")
@auth_required
@roles("ADMIN")
def delete_agency(user, agency_id):
    if agency_id != user.agency_id:
        return jsonify({"error": "No access"}), 403
    return jsonify({
        "error": "Agency deletion is disabled for tenant administrators"
    }), 403


@api.get("/challans")
@auth_required
def challans(user):
    return jsonify([
        obj_json(
            x,
            [
                "id",
                "vehicle_id",
                "challan_no",
                "amount",
                "status",
                "due_date",
                "payment_ref",
                "created_at"
            ]
        )
        for x in (
            Challan.query
            .order_by(Challan.id.desc())
            .limit(500)
            .all()
        )
    ])


@api.post("/challans")
@auth_required
def add_challan(user):
    d = request.get_json(silent=True) or {}

    v = Vehicle.query.get_or_404(
        d.get("vehicle_id")
    )

    if not can_access(user, v):
        return jsonify({
            "error": "No access"
        }), 403

    c = Challan(
        vehicle_id=v.id,
        challan_no=d.get("challan_no"),
        amount=d.get("amount", 0),
        status=d.get(
            "status",
            "PENDING"
        ),
        payment_ref=d.get(
            "payment_ref"
        )
    )

    db.session.add(c)
    db.session.commit()

    return jsonify(
        obj_json(
            c,
            [
                "id",
                "vehicle_id",
                "challan_no",
                "amount",
                "status",
                "payment_ref"
            ]
        )
    ), 201


@api.delete("/challans/<int:challan_id>")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def delete_challan(user, challan_id):
    c = Challan.query.get_or_404(
        challan_id
    )

    db.session.delete(c)
    db.session.commit()

    return jsonify({
        "message": "Challan deleted"
    })


@api.get("/chat")
@auth_required
def chat_list(user):
    cs = (
        ChatConversation.query
        .order_by(ChatConversation.id.desc())
        .limit(100)
        .all()
    )

    out = []

    for c in cs:
        msgs = (
            ChatMessage.query
            .filter_by(
                conversation_id=c.id
            )
            .order_by(
                ChatMessage.id.asc()
            )
            .all()
        )

        out.append({
            "id": c.id,
            "subject": c.subject,
            "created_by": c.created_by,
            "created_at": dt(c.created_at),
            "messages": [
                {
                    "id": m.id,
                    "sender_id": m.sender_id,
                    "message": m.message,
                    "created_at": dt(
                        m.created_at
                    )
                }
                for m in msgs
            ]
        })

    return jsonify(out)


@api.post("/chat")
@auth_required
def chat_create(user):
    d = request.get_json(silent=True) or {}

    c = ChatConversation(
        subject=(
            d.get("subject")
            or "RecoveryOps chat"
        ).strip(),
        created_by=user.id
    )

    db.session.add(c)
    db.session.flush()

    m = ChatMessage(
        conversation_id=c.id,
        sender_id=user.id,
        message=(
            d.get("message")
            or ""
        ).strip()
    )

    if not m.message:
        return jsonify({
            "error": "Message required"
        }), 400

    db.session.add(m)
    db.session.commit()

    return jsonify({
        "conversation_id": c.id,
        "message_id": m.id
    }), 201


@api.delete("/chat/<int:conversation_id>")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def delete_chat(user, conversation_id):
    c = ChatConversation.query.get_or_404(
        conversation_id
    )

    ChatMessage.query.filter_by(
        conversation_id=c.id
    ).delete(
        synchronize_session=False
    )

    db.session.delete(c)
    db.session.commit()

    return jsonify({
        "message": "Conversation deleted"
    })


@api.get("/reports")
@auth_required
def reports(user):
    return jsonify([
        obj_json(
            x,
            [
                "id",
                "name",
                "report_type",
                "status",
                "created_by",
                "created_at"
            ]
        )
        for x in (
            Report.query
            .order_by(Report.id.desc())
            .all()
        )
    ])


@api.post("/reports")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def add_report(user):
    d = request.get_json(silent=True) or {}

    r = Report(
        name=(
            d.get("name")
            or "Recovery Report"
        ).strip(),
        report_type=d.get(
            "report_type",
            "SUMMARY"
        ),
        created_by=user.id,
        payload=json.dumps(
            d.get("payload", {})
        )
    )

    db.session.add(r)
    db.session.commit()

    return jsonify(
        obj_json(
            r,
            [
                "id",
                "name",
                "report_type",
                "status",
                "created_by",
                "created_at"
            ]
        )
    ), 201


@api.delete("/reports/<int:report_id>")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def delete_report(user, report_id):
    r = Report.query.get_or_404(
        report_id
    )

    db.session.delete(r)
    db.session.commit()

    return jsonify({
        "message": "Report deleted"
    })


@api.get("/search/registration-suffix")
@auth_required
def registration_suffix(user):
    suffix = re.sub(
        r"\D",
        "",
        (
            request.args.get("suffix")
            or ""
        ).strip()
    )

    if len(suffix) != 4:
        return jsonify({
            "error": "suffix must contain exactly 4 digits"
        }), 400

    rows = (
        accessible_vehicle_query(user)
        .filter(
            Vehicle.registration_suffix == suffix
        )
        .order_by(
            Vehicle.registration_no.asc()
        )
        .limit(500)
        .all()
    )

    return jsonify([
        vehicle_json(v)
        for v in rows
    ])


@api.get("/stats")
@auth_required
def stats(user):
    d = (
        dashboard.__wrapped__(user)
        if hasattr(
            dashboard,
            "__wrapped__"
        )
        else None
    )

    # keep a simple stable payload for mobile/web
    q = accessible_vehicle_query(user)

    return jsonify({
        "total": Vehicle.query.count(),
        "accessible_total": q.count(),
        "two_wheeler": q.filter(
            Vehicle.vehicle_type.ilike("%two%")
        ).count(),
        "four_wheeler": q.filter(
            Vehicle.vehicle_type.ilike("%four%")
        ).count(),
        "cv": q.filter(
            db.or_(
                Vehicle.vehicle_type.ilike("%cv%"),
                Vehicle.vehicle_type.ilike("%commercial%")
            )
        ).count(),
        "holds": q.filter_by(
            status="HOLD"
        ).count(),
        "in_yard": q.filter_by(
            status="IN_YARD"
        ).count()
    })


@api.get("/reports/export")
@auth_required
@roles("ADMIN", "OFFICE_STAFF")
def export_report(user):
    fmt = (
        request.args.get("format")
        or "csv"
    ).lower()

    bank_id = request.args.get(
        "bank_id"
    )

    q = Vehicle.query.order_by(
        Vehicle.id.asc()
    )

    if bank_id:
        try:
            q = q.filter_by(
                bank_id=int(bank_id)
            )
        except ValueError:
            return jsonify({
                "error": "Invalid bank_id"
            }), 400

    rows = q.all()

    fields = [
        "id",
        "account_no",
        "loan_no",
        "registration_no",
        "chassis_no",
        "engine_no",
        "customer_name",
        "mobile",
        "city",
        "state",
        "pincode",
        "vehicle_type",
        "bank_id",
        "source_file_id",
        "latitude",
        "longitude",
        "status",
        "version",
        "created_at",
        "updated_at"
    ]

    if fmt not in {"csv", "xlsx"}:
        return jsonify({
            "error": "format must be csv or xlsx"
        }), 400

    data = [
        [
            getattr(v, f)
            if f not in {
                "created_at",
                "updated_at"
            }
            else dt(getattr(v, f))
            for f in fields
        ]
        for v in rows
    ]

    if fmt == "xlsx":
        out = io.BytesIO()

        pd.DataFrame(
            data,
            columns=fields
        ).to_excel(
            out,
            index=False
        )

        out.seek(0)

        from flask import send_file

        return send_file(
            out,
            mimetype=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            as_attachment=True,
            download_name=(
                f"RecoveryOps_"
                f"{'bank_' + str(bank_id) if bank_id else 'all'}"
                f"_data.xlsx"
            )
        )

    out = io.StringIO()

    w = csv.writer(out)
    w.writerow(fields)
    w.writerows(data)

    from flask import Response

    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": (
                "attachment; filename="
                f"RecoveryOps_"
                f"{'bank_' + str(bank_id) if bank_id else 'all'}"
                "_data.csv"
            )
        }
    )