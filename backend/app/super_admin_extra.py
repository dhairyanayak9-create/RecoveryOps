from flask import request, jsonify
from sqlalchemy import text
from . import db
from .models import User, AgencyCompany, Vehicle, Bank, Client, RecoveryCase, AppFile, Challan
from .super_admin import super_admin, _require_super_admin, _agency_detail


def _legacy_agency_id(agency_id):
    name = db.session.execute(
        text("SELECT name FROM public.agencies WHERE id=:id"),
        {"id": agency_id},
    ).scalar()
    if not name:
        return None
    company = AgencyCompany.query.filter_by(name=name).first()
    return company.id if company else agency_id


def _vehicle_json(v):
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
        "status": v.status,
        "updated_at": v.updated_at.isoformat() if v.updated_at else None,
    }


@super_admin.get("/api/super-admin/agencies/<int:agency_id>/vehicles")
def agency_vehicles(user, agency_id):
    denied = _require_super_admin(user)
    if denied:
        return denied
    legacy_id = _legacy_agency_id(agency_id)
    if legacy_id is None:
        return jsonify({"error": "Agency not found"}), 404
    q = Vehicle.query.filter_by(agency_id=legacy_id).order_by(Vehicle.id.desc())
    limit = min(max(int(request.args.get("limit", 200)), 1), 1000)
    rows = q.limit(limit).all()
    return jsonify({"items": [_vehicle_json(v) for v in rows], "count": len(rows), "agency_id": agency_id})


@super_admin.put("/api/super-admin/agencies/<int:agency_id>/vehicles/<int:vehicle_id>")
def update_agency_vehicle(user, agency_id, vehicle_id):
    denied = _require_super_admin(user)
    if denied:
        return denied
    legacy_id = _legacy_agency_id(agency_id)
    if legacy_id is None:
        return jsonify({"error": "Agency not found"}), 404
    v = Vehicle.query.filter_by(id=vehicle_id, agency_id=legacy_id).first()
    if not v:
        return jsonify({"error": "Vehicle not found in this agency"}), 404
    d = request.get_json(silent=True) or {}
    fields = ["account_no", "loan_no", "registration_no", "chassis_no", "engine_no", "customer_name", "mobile", "city", "state", "pincode", "vehicle_type", "bank_id", "status"]
    for field in fields:
        if field in d:
            value = d.get(field)
            if field == "bank_id" and value in (None, ""):
                value = None
            setattr(v, field, value)
    db.session.commit()
    return jsonify(_vehicle_json(v))


@super_admin.delete("/api/super-admin/agencies/<int:agency_id>/vehicles/<int:vehicle_id>")
def delete_agency_vehicle(user, agency_id, vehicle_id):
    denied = _require_super_admin(user)
    if denied:
        return denied
    legacy_id = _legacy_agency_id(agency_id)
    v = Vehicle.query.filter_by(id=vehicle_id, agency_id=legacy_id).first() if legacy_id is not None else None
    if not v:
        return jsonify({"error": "Vehicle not found in this agency"}), 404
    db.session.delete(v)
    db.session.commit()
    return jsonify({"message": "Vehicle deleted"})


@super_admin.get("/api/super-admin/agencies/<int:agency_id>/banks")
def agency_banks(user, agency_id):
    denied = _require_super_admin(user)
    if denied:
        return denied
    legacy_id = _legacy_agency_id(agency_id)
    if legacy_id is None:
        return jsonify({"error": "Agency not found"}), 404
    rows = Bank.query.filter_by(agency_id=legacy_id).order_by(Bank.name).all()
    return jsonify([{"id": b.id, "name": b.name, "code": b.code, "phone": b.phone, "email": b.email, "active": bool(b.active)} for b in rows])


@super_admin.post("/api/super-admin/agencies/<int:agency_id>/banks")
def create_agency_bank(user, agency_id):
    denied = _require_super_admin(user)
    if denied:
        return denied
    legacy_id = _legacy_agency_id(agency_id)
    if legacy_id is None:
        return jsonify({"error": "Agency not found"}), 404
    d = request.get_json(silent=True) or {}
    name = str(d.get("name") or "").strip()
    code = str(d.get("code") or "").strip() or None
    if not name:
        return jsonify({"error": "Bank name required"}), 400
    if Bank.query.filter(Bank.agency_id == legacy_id, db.or_(Bank.name == name, Bank.code == code if code else False)).first():
        return jsonify({"error": "Bank already exists"}), 409
    b = Bank(agency_id=legacy_id, name=name, code=code, phone=d.get("phone"), email=d.get("email"), active=True)
    db.session.add(b)
    db.session.commit()
    return jsonify({"id": b.id, "name": b.name, "code": b.code}), 201


@super_admin.put("/api/super-admin/agencies/<int:agency_id>/banks/<int:bank_id>")
def update_agency_bank(user, agency_id, bank_id):
    denied = _require_super_admin(user)
    if denied:
        return denied
    legacy_id = _legacy_agency_id(agency_id)
    b = Bank.query.filter_by(id=bank_id, agency_id=legacy_id).first() if legacy_id is not None else None
    if not b:
        return jsonify({"error": "Bank not found in this agency"}), 404
    d = request.get_json(silent=True) or {}
    if "name" in d: b.name = str(d.get("name") or b.name).strip()
    if "code" in d: b.code = str(d.get("code") or "").strip() or None
    if "phone" in d: b.phone = d.get("phone")
    if "email" in d: b.email = d.get("email")
    if "active" in d: b.active = bool(d.get("active"))
    db.session.commit()
    return jsonify({"id": b.id, "name": b.name, "code": b.code, "phone": b.phone, "email": b.email, "active": bool(b.active)})
