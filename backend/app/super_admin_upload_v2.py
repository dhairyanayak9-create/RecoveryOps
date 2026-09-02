from flask import request, jsonify
from sqlalchemy import text
import re
import pandas as pd

from . import db
from .models import AgencyCompany, Bank, Vehicle, AppFile
from .auth import auth_required
from .super_admin import super_admin, _require_super_admin, _agency_detail


def _legacy_agency_id(agency_id):
    detail = _agency_detail(agency_id)
    return detail.get("legacy_agency_id") if detail else None


def _suffix(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-4:] if len(digits) >= 4 else None


@super_admin.post("/api/super-admin/agencies/<int:agency_id>/upload-v2")
@auth_required
def upload_agency_data_v2(user, agency_id):
    denied = _require_super_admin(user)
    if denied:
        return denied

    legacy_id = _legacy_agency_id(agency_id)
    if legacy_id is None:
        return jsonify({"error": "Agency not found"}), 404

    f = request.files.get("file")
    if not f or not (f.filename or "").strip():
        return jsonify({"error": "File is required"}), 400

    try:
        bank_id = int(request.form.get("bank_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Select a valid bank from this agency"}), 400

    bank = Bank.query.filter_by(id=bank_id, agency_id=legacy_id).first()
    if not bank:
        return jsonify({"error": "Selected bank does not belong to this agency"}), 404

    vehicle_type = str(request.form.get("vehicle_type") or "").strip().upper()
    if vehicle_type not in {"TWO_WHEELER", "FOUR_WHEELER", "CV"}:
        return jsonify({"error": "Vehicle type is required: 2 Wheeler, 4 Wheeler or CV"}), 400

    filename = (f.filename or "").strip()
    lower = filename.lower()
    if not lower.endswith((".csv", ".xlsx", ".xls")):
        return jsonify({"error": "Only CSV, XLS and XLSX files are supported"}), 400

    try:
        df = pd.read_csv(f) if lower.endswith(".csv") else pd.read_excel(f)
        df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
        aliases = {
            "registration": "registration_no",
            "registration_number": "registration_no",
            "reg_no": "registration_no",
            "vehicle_no": "registration_no",
            "customer": "customer_name",
            "mobile_no": "mobile",
            "phone": "mobile",
            "chassis": "chassis_no",
            "engine": "engine_no",
            "account": "account_no",
            "account_number": "account_no",
            "loan": "loan_no",
            "loan_number": "loan_no",
            "vehicle_type": "vehicle_type",
        }
        df = df.rename(columns={c: aliases.get(c, c) for c in df.columns})

        supported = [
            "account_no", "loan_no", "registration_no", "chassis_no", "engine_no",
            "customer_name", "mobile", "city", "state", "pincode", "latitude", "longitude"
        ]
        if not any(c in df.columns for c in supported):
            return jsonify({"error": "No supported vehicle columns found"}), 400

        created = 0
        skipped = 0
        rows = []
        for _, r in df.iterrows():
            def val(col):
                if col not in df.columns:
                    return None
                x = r.get(col)
                if pd.isna(x):
                    return None
                s = str(x).strip()
                return s or None

            reg = val("registration_no")
            chassis = val("chassis_no")
            account = val("account_no")
            if not any((reg, chassis, account)):
                skipped += 1
                continue

            def num(col):
                x = val(col)
                try:
                    return float(x) if x is not None else None
                except (TypeError, ValueError):
                    return None

            rows.append({
                "agency_id": legacy_id,
                "account_no": account,
                "loan_no": val("loan_no"),
                "registration_no": reg,
                "registration_suffix": _suffix(reg),
                "chassis_no": (chassis or "")[:150],
                "engine_no": (val("engine_no") or "")[:150],
                "customer_name": val("customer_name"),
                "mobile": val("mobile"),
                "city": val("city"),
                "state": val("state"),
                "pincode": val("pincode"),
                "vehicle_type": vehicle_type,
                "latitude": num("latitude"),
                "longitude": num("longitude"),
                "bank_id": bank.id,
            })

        af = AppFile(
            agency_id=legacy_id,
            filename=filename,
            vehicle_type=vehicle_type,
            bank_id=bank.id,
            record_count=0,
            status="IMPORTING",
            uploaded_by=user.id,
        )
        db.session.add(af)
        db.session.flush()

        if rows:
            for i in range(0, len(rows), 5000):
                db.session.bulk_insert_mappings(Vehicle, [dict(x, source_file_id=af.id) for x in rows[i:i + 5000]])
                db.session.flush()

        created = len(rows)
        af.record_count = created
        af.status = "IMPORTED"
        db.session.commit()

        return jsonify({
            "message": "Data uploaded successfully",
            "rows_created": created,
            "rows_skipped": skipped,
            "agency_id": agency_id,
            "bank_id": bank.id,
            "bank_name": bank.name,
            "vehicle_type": vehicle_type,
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
