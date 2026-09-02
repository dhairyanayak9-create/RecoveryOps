from flask import Blueprint, request, jsonify, render_template
from sqlalchemy import text
from werkzeug.security import generate_password_hash
import re

from . import db
from .models import User, AgencyCompany, Vehicle
from .auth import auth_required


super_admin = Blueprint("super_admin", __name__)


def _require_super_admin(user):
    if user.role != "SUPER_ADMIN":
        return jsonify({"error": "Super Admin access required"}), 403
    return None


def _normalize_email(value):
    value = str(value or "").strip().lower()
    return value or None


def _normalize_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    return digits or None


def _make_username(email, slug):
    base = re.sub(r"[^a-zA-Z0-9]+", "_", (email or slug).split("@")[0]).strip("_").lower() or "admin"
    username = base
    n = 2
    while User.query.filter_by(username=username).first():
        username = f"{base}_{n}"
        n += 1
    return username


@super_admin.get("/super-admin")
def super_admin_page():
    return render_template("super_admin.html")


@super_admin.get("/api/super-admin/agencies")
@auth_required
def list_agencies(user):
    denied = _require_super_admin(user)
    if denied:
        return denied

    rows = db.session.execute(text("""
        SELECT
            a.id,
            a.name,
            a.slug,
            a.phone,
            a.email,
            a.address,
            a.is_active,
            a.created_at,
            COUNT(DISTINCT am.user_id) FILTER (WHERE am.role = 'STAFF' AND am.is_active) AS staff_count,
            COUNT(DISTINCT am.user_id) FILTER (WHERE am.role = 'REPO_AGENT' AND am.is_active) AS repo_agent_count,
            COUNT(DISTINCT CASE WHEN u.role = 'ADMIN' AND u.is_deleted = false THEN u.id END) AS admin_count
        FROM public.agencies a
        LEFT JOIN public.agency_members am ON am.agency_id = a.id
        LEFT JOIN public."user" u ON u.id = am.user_id
        GROUP BY a.id
        ORDER BY a.created_at DESC
    """)).mappings().all()

    return jsonify([dict(r) for r in rows])


@super_admin.post("/api/super-admin/agencies")
@auth_required
def create_agency(user):
    denied = _require_super_admin(user)
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    agency_name = str(data.get("agency_name") or "").strip()
    slug = str(data.get("slug") or "").strip().upper()
    phone = _normalize_phone(data.get("phone"))
    email = _normalize_email(data.get("email"))
    address = str(data.get("address") or "").strip() or None
    admin_name = str(data.get("admin_name") or "").strip()
    admin_email = _normalize_email(data.get("admin_email"))
    admin_phone = _normalize_phone(data.get("admin_phone"))
    password = str(data.get("password") or "")

    if not agency_name or not slug or not admin_name or not password:
        return jsonify({"error": "Agency name, slug, admin name and password are required"}), 400
    if len(slug) > 80 or not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", slug):
        return jsonify({"error": "Slug may contain only A-Z, 0-9, underscore and hyphen"}), 400
    if len(password) < 8:
        return jsonify({"error": "Admin password must be at least 8 characters"}), 400
    if not admin_email and not admin_phone:
        return jsonify({"error": "Admin email or phone is required"}), 400

    try:
        existing_agency = db.session.execute(
            text("SELECT id FROM public.agencies WHERE lower(name) = lower(:name) OR upper(slug) = upper(:slug) LIMIT 1"),
            {"name": agency_name, "slug": slug},
        ).mappings().first()
        if existing_agency:
            return jsonify({"error": "Agency name or slug already exists"}), 409

        if admin_email and User.query.filter(db.func.lower(User.email) == admin_email).first():
            return jsonify({"error": "Admin email is already registered"}), 409
        if admin_phone and User.query.filter(User.phone == admin_phone).first():
            return jsonify({"error": "Admin phone is already registered"}), 409

        modern = db.session.execute(text("""
            INSERT INTO public.agencies (name, slug, phone, email, address, is_active)
            VALUES (:name, :slug, :phone, :email, :address, true)
            RETURNING id, name, slug, phone, email, address, is_active, created_at
        """), {
            "name": agency_name,
            "slug": slug,
            "phone": phone,
            "email": email,
            "address": address,
        }).mappings().one()

        compatibility = AgencyCompany(
            name=agency_name,
            code=slug,
            phone=phone,
            email=email,
            active=True,
        )
        db.session.add(compatibility)
        db.session.flush()

        username = _make_username(admin_email, slug)
        admin = User(
            agency_id=compatibility.id,
            username=username,
            password_hash=generate_password_hash(password, method="scrypt:32768:8:1"),
            full_name=admin_name,
            role="ADMIN",
            phone=admin_phone,
            email=admin_email,
            active=True,
            is_deleted=False,
        )
        db.session.add(admin)
        db.session.flush()

        db.session.execute(text("""
            INSERT INTO public.agency_members (agency_id, user_id, role, is_active)
            VALUES (:agency_id, :user_id, 'ADMIN', true)
        """), {"agency_id": modern["id"], "user_id": admin.id})

        db.session.commit()

        return jsonify({
            "message": "Agency and admin created successfully",
            "agency": dict(modern),
            "admin": {
                "id": admin.id,
                "username": admin.username,
                "full_name": admin.full_name,
                "email": admin.email,
                "phone": admin.phone,
                "role": admin.role,
            },
        }), 201

    except Exception:
        db.session.rollback()
        raise


# ------------------------------------------------------------------
# Agency-isolated vehicle access.
# The legacy routes module previously returned every vehicle to ADMIN/
# STAFF, which caused every agency to see the same 70k+ records.
# Keep SUPER_ADMIN global; tenant users are restricted to their agency.
# ------------------------------------------------------------------

def _agency_scoped_vehicle_query(user, write=False):
    q = Vehicle.query
    if user.role == "SUPER_ADMIN":
        return q
    if user.role in {"ADMIN", "STAFF"} and user.agency_id is not None:
        return q.filter(Vehicle.agency_id == user.agency_id)
    return q.filter(db.false())


# routes.py looks up accessible_vehicle_query from its module globals at
# request time, so replacing the function here applies to all existing
# vehicle/search/sync access paths without rewriting the large routes file.
try:
    from . import routes as _routes
    _routes.accessible_vehicle_query = _agency_scoped_vehicle_query
except Exception:
    pass
