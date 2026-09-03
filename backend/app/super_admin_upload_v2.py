from flask import request, jsonify
from sqlalchemy import text
from werkzeug.security import generate_password_hash
from datetime import datetime, timezone, timedelta
import re
import pandas as pd

from . import db
from .models import AgencyCompany, User, Bank, Vehicle, AppFile
from .auth import auth_required
from .super_admin import super_admin, _require_super_admin


def _public_legacy_agency_id(agency_id):
    name = db.session.execute(
        text("SELECT name FROM public.agencies WHERE id=:id"),
        {"id": agency_id},
    ).scalar()
    if not name:
        return None
    return db.session.execute(
        text("SELECT id FROM public.agency_company WHERE name=:name LIMIT 1"),
        {"name": name},
    ).scalar() or agency_id


def _tenant_schema(legacy_id):
    return db.session.execute(
        text("SELECT schema_name FROM public.agency_tenants WHERE legacy_agency_id=:id LIMIT 1"),
        {"id": int(legacy_id)},
    ).scalar()


def _safe_schema(value):
    value = str(value or "")
    return value if re.fullmatch(r"recovery_agency_[0-9]+", value) else None


def _provision_schema(modern_id, legacy_id, copy_public_rows=False):
    schema = _safe_schema(f"recovery_agency_{int(legacy_id)}")
    if not schema:
        raise ValueError("Invalid tenant schema")

    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS public.agency_tenants (
            agency_id BIGINT PRIMARY KEY REFERENCES public.agencies(id) ON DELETE RESTRICT,
            legacy_agency_id INTEGER NOT NULL UNIQUE REFERENCES public.agency_company(id) ON DELETE RESTRICT,
            schema_name TEXT NOT NULL UNIQUE,
            provisioned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            schema_version INTEGER NOT NULL DEFAULT 1
        )
    """))
    db.session.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    tables = [
        "user", "user_otp", "bank", "vehicle", "status_history_legacy",
        "notification", "sync_mutation", "client", "recovery_case",
        "app_file", "challan", "chat_conversation", "chat_message", "report"
    ]
    for table in tables:
        exists = db.session.execute(
            text("SELECT to_regclass(:name)"),
            {"name": f"public.{table}"},
        ).scalar()
        if exists:
            db.session.execute(text(
                f'CREATE TABLE IF NOT EXISTS "{schema}"."{table}" (LIKE public."{table}" INCLUDING ALL)'
            ))
            if copy_public_rows:
                db.session.execute(text(
                    f'INSERT INTO "{schema}"."{table}" SELECT * FROM public."{table}" WHERE agency_id=:agency_id ON CONFLICT DO NOTHING'
                ), {"agency_id": int(legacy_id)})

    db.session.execute(text(f'''
        CREATE TABLE IF NOT EXISTS "{schema}"."data_assignment" (
            id INTEGER PRIMARY KEY,
            agency_id INTEGER,
            user_id INTEGER NOT NULL,
            bank_id INTEGER,
            vehicle_id INTEGER,
            permission VARCHAR(30) DEFAULT 'READ',
            active BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT now()
        )
    '''))
    db.session.execute(text(f'CREATE INDEX IF NOT EXISTS "ix_data_assignment_agency_id" ON "{schema}"."data_assignment" (agency_id)'))

    db.session.execute(text("""
        INSERT INTO public.agency_tenants(agency_id, legacy_agency_id, schema_name)
        VALUES(:agency_id,:legacy_id,:schema_name)
        ON CONFLICT(agency_id) DO UPDATE SET
          legacy_agency_id=EXCLUDED.legacy_agency_id,
          schema_name=EXCLUDED.schema_name,
          schema_version=GREATEST(public.agency_tenants.schema_version,1)
    """), {"agency_id": int(modern_id), "legacy_id": int(legacy_id), "schema_name": schema})
    return schema


def _copy_user_to_tenant(user_id, legacy_id, schema):
    # Keep the public control-plane user and tenant operational user synchronized at creation time.
    db.session.execute(text(f'''
        INSERT INTO "{schema}"."user"
        SELECT * FROM public."user" WHERE id=:uid AND agency_id=:aid
        ON CONFLICT (id) DO UPDATE SET
            agency_id=EXCLUDED.agency_id,
            username=EXCLUDED.username,
            password_hash=EXCLUDED.password_hash,
            full_name=EXCLUDED.full_name,
            role=EXCLUDED.role,
            phone=EXCLUDED.phone,
            email=EXCLUDED.email,
            active=EXCLUDED.active,
            is_deleted=EXCLUDED.is_deleted,
            device_id=EXCLUDED.device_id
    '''), {"uid": int(user_id), "aid": int(legacy_id)})


def _suffix(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-4:] if len(digits) >= 4 else None


@super_admin.post("/api/super-admin/agencies/provisioned")
@auth_required
def create_agency_provisioned(user):
    denied = _require_super_admin(user)
    if denied:
        return denied
    d = request.get_json(silent=True) or {}
    name = str(d.get("agency_name") or "").strip()
    slug = str(d.get("slug") or "").strip().upper()
    phone = re.sub(r"\D", "", str(d.get("phone") or "")) or None
    email = str(d.get("email") or "").strip().lower() or None
    address = str(d.get("address") or "").strip() or None
    admin_name = str(d.get("admin_name") or "").strip()
    admin_email = str(d.get("admin_email") or "").strip().lower() or None
    admin_phone = re.sub(r"\D", "", str(d.get("admin_phone") or "")) or None
    password = str(d.get("password") or "")
    if admin_phone and admin_phone.startswith("91") and len(admin_phone) == 12:
        admin_phone = admin_phone[2:]
    if phone and phone.startswith("91") and len(phone) == 12:
        phone = phone[2:]

    if not name or not slug or not admin_name or len(password) < 8:
        return jsonify({"error": "Agency name, slug, admin name and password (8+ chars) are required"}), 400
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,79}", slug):
        return jsonify({"error": "Invalid agency slug"}), 400
    if not admin_email and not admin_phone:
        return jsonify({"error": "Admin email or phone is required"}), 400

    try:
        if db.session.execute(text("SELECT id FROM public.agencies WHERE lower(name)=lower(:n) OR upper(slug)=upper(:s) LIMIT 1"), {"n": name, "s": slug}).first():
            return jsonify({"error": "Agency name or slug already exists"}), 409
        if admin_email and db.session.execute(text('SELECT id FROM public."user" WHERE lower(email)=:e LIMIT 1'), {"e": admin_email}).first():
            return jsonify({"error": "Admin email is already registered"}), 409
        if admin_phone and db.session.execute(text('SELECT id FROM public."user" WHERE phone=:p LIMIT 1'), {"p": admin_phone}).first():
            return jsonify({"error": "Admin phone is already registered"}), 409

        agency = db.session.execute(text("""
            INSERT INTO public.agencies(name,slug,phone,email,address,is_active)
            VALUES(:n,:s,:p,:e,:a,true)
            RETURNING id,name,slug,phone,email,address,is_active,created_at
        """), {"n": name, "s": slug, "p": phone, "e": email, "a": address}).mappings().one()

        company = AgencyCompany(name=name, code=slug, phone=phone, email=email, active=True)
        db.session.add(company)
        db.session.flush()

        username_base = re.sub(r"[^a-zA-Z0-9]+", "_", (admin_email or slug).split("@")[0]).strip("_").lower() or "admin"
        username = username_base
        n = 2
        while db.session.execute(text('SELECT id FROM public."user" WHERE username=:u LIMIT 1'), {"u": username}).first():
            username = f"{username_base}_{n}"
            n += 1

        admin = User(
            agency_id=company.id,
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
        db.session.execute(text("INSERT INTO public.agency_members(agency_id,user_id,role,is_active) VALUES(:a,:u,'ADMIN',true)"), {"a": agency["id"], "u": admin.id})

        now = datetime.now(timezone.utc)
        trial = now + timedelta(days=14)
        db.session.execute(text("""
            INSERT INTO public.agency_subscriptions
              (agency_id,plan,billing_cycle,status,monthly_price,yearly_price,user_limit,repo_agent_limit,vehicle_case_limit,storage_limit_gb,start_date,expiry_date,trial_end_date)
            VALUES(:id,'STARTER','monthly','TRIAL',999,9999,5,3,5000,5,:st,:ex,:te)
            ON CONFLICT (agency_id) DO NOTHING
        """), {"id": agency["id"], "st": now, "ex": trial, "te": trial})

        schema = _provision_schema(agency["id"], company.id, copy_public_rows=False)
        _copy_user_to_tenant(admin.id, company.id, schema)
        db.session.commit()
        return jsonify({"message": "Agency, admin and isolated data store created successfully", "agency": dict(agency), "schema": schema, "admin_id": admin.id}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400


@super_admin.post("/api/super-admin/agencies/<int:agency_id>/ensure-store")
@auth_required
def ensure_agency_store(user, agency_id):
    denied = _require_super_admin(user)
    if denied:
        return denied
    row = db.session.execute(text("""
        SELECT a.id AS agency_id, c.id AS legacy_id
        FROM public.agencies a
        LEFT JOIN public.agency_company c ON c.name=a.name OR c.code=a.slug
        WHERE a.id=:id
        LIMIT 1
    """), {"id": agency_id}).mappings().first()
    if not row:
        return jsonify({"error": "Agency not found"}), 404
    try:
        schema = _provision_schema(row["agency_id"], row["legacy_id"] or row["agency_id"], copy_public_rows=True)
        db.session.commit()
        return jsonify({"message": "Agency data store ready", "schema": schema})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400


@super_admin.delete("/api/super-admin/agencies/<int:agency_id>/hard-delete")
@auth_required
def hard_delete_agency(user, agency_id):
    denied = _require_super_admin(user)
    if denied:
        return denied

    try:
        row = db.session.execute(text("""
            SELECT a.id AS agency_id, a.name, c.id AS legacy_id, t.schema_name
            FROM public.agencies a
            LEFT JOIN public.agency_company c ON c.name=a.name OR c.code=a.slug
            LEFT JOIN public.agency_tenants t ON t.agency_id=a.id
            WHERE a.id=:id
            LIMIT 1
        """), {"id": agency_id}).mappings().first()
        if not row:
            return jsonify({"error": "Agency not found"}), 404
        legacy_id = int(row["legacy_id"] or row["agency_id"])
        schema = _safe_schema(row["schema_name"])

        # Remove modern UUID tenant records first.
        modern_tables = [
            ("status_history", "agency_id", "uuid"),
            ("data_assignment", "agency_id", "uuid"),
            ("recovery_cases", "agency_id", "uuid"),
            ("clients", "agency_id", "uuid"),
            ("banks", "agency_id", "uuid"),
            ("app_files", "agency_id", "uuid"),
        ]
        for table, col, _ in modern_tables:
            if db.session.execute(text("SELECT to_regclass(:n)"), {"n": f"public.{table}"}).scalar():
                try:
                    db.session.execute(text(f'DELETE FROM public."{table}" WHERE "{col}"=:id'), {"id": agency_id})
                except Exception:
                    db.session.rollback()

        # Remove legacy public rows so nothing remains in the old shared tables.
        legacy_tables = [
            "status_history_legacy", "notification", "sync_mutation", "data_assignment",
            "chat_message", "chat_conversation", "recovery_case", "challan", "vehicle",
            "client", "app_file", "user_otp", "report", "bank"
        ]
        for table in legacy_tables:
            if db.session.execute(text("SELECT to_regclass(:n)"), {"n": f"public.{table}"}).scalar():
                try:
                    db.session.execute(text(f'DELETE FROM public."{table}" WHERE agency_id=:id'), {"id": legacy_id})
                except Exception:
                    db.session.rollback()

        member_users = [r[0] for r in db.session.execute(text("SELECT user_id FROM public.agency_members WHERE agency_id=:id"), {"id": agency_id}).all()]
        db.session.execute(text("DELETE FROM public.agency_members WHERE agency_id=:id"), {"id": agency_id})
        db.session.execute(text("DELETE FROM public.agency_subscriptions WHERE agency_id=:id"), {"id": agency_id})
        db.session.execute(text('DELETE FROM public."user" WHERE agency_id=:id AND role <> \'SUPER_ADMIN\''), {"id": legacy_id})
        if legacy_id != agency_id:
            db.session.execute(text('DELETE FROM public."user" WHERE agency_id=:id AND role <> \'SUPER_ADMIN\''), {"id": agency_id})
        db.session.execute(text("DELETE FROM public.agency_tenants WHERE agency_id=:id"), {"id": agency_id})
        db.session.execute(text("DELETE FROM public.agency_company WHERE id=:id"), {"id": legacy_id})
        db.session.execute(text("DELETE FROM public.agencies WHERE id=:id"), {"id": agency_id})

        if schema:
            db.session.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))

        db.session.commit()
        return jsonify({"message": "Agency and all agency data permanently deleted", "agency_id": agency_id})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400


@super_admin.post("/api/super-admin/agencies/<int:agency_id>/upload-v2")
@auth_required
def upload_agency_data_v2(user, agency_id):
    denied = _require_super_admin(user)
    if denied:
        return denied

    legacy_id = _public_legacy_agency_id(agency_id)
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
            "registration": "registration_no", "registration_number": "registration_no", "reg_no": "registration_no",
            "vehicle_no": "registration_no", "customer": "customer_name", "mobile_no": "mobile", "phone": "mobile",
            "chassis": "chassis_no", "engine": "engine_no", "account": "account_no", "account_number": "account_no",
            "loan": "loan_no", "loan_number": "loan_no", "vehicle_type": "vehicle_type",
        }
        df = df.rename(columns={c: aliases.get(c, c) for c in df.columns})
        supported = ["account_no", "loan_no", "registration_no", "chassis_no", "engine_no", "customer_name", "mobile", "city", "state", "pincode", "latitude", "longitude"]
        if not any(c in df.columns for c in supported):
            return jsonify({"error": "No supported vehicle columns found"}), 400

        rows=[]; skipped=0
        for _, r in df.iterrows():
            def val(col):
                if col not in df.columns:return None
                x=r.get(col)
                if pd.isna(x):return None
                s=str(x).strip();return s or None
            reg=val("registration_no"); chassis=val("chassis_no"); account=val("account_no")
            if not any((reg,chassis,account)):
                skipped+=1;continue
            def num(col):
                x=val(col)
                try:return float(x) if x is not None else None
                except:return None
            rows.append({"agency_id":legacy_id,"account_no":account,"loan_no":val("loan_no"),"registration_no":reg,"registration_suffix":_suffix(reg),"chassis_no":(chassis or "")[:150],"engine_no":(val("engine_no") or "")[:150],"customer_name":val("customer_name"),"mobile":val("mobile"),"city":val("city"),"state":val("state"),"pincode":val("pincode"),"vehicle_type":vehicle_type,"latitude":num("latitude"),"longitude":num("longitude"),"bank_id":bank.id})

        af=AppFile(agency_id=legacy_id,filename=filename,vehicle_type=vehicle_type,bank_id=bank.id,record_count=0,status="IMPORTING",uploaded_by=user.id)
        db.session.add(af);db.session.flush()
        for i in range(0,len(rows),5000):
            db.session.bulk_insert_mappings(Vehicle,[dict(x,source_file_id=af.id) for x in rows[i:i+5000]])
        af.record_count=len(rows);af.status="IMPORTED";db.session.commit()
        return jsonify({"message":"Data uploaded successfully","rows_created":len(rows),"rows_skipped":skipped,"agency_id":agency_id,"bank_id":bank.id,"bank_name":bank.name,"vehicle_type":vehicle_type}),201
    except Exception as e:
        db.session.rollback();return jsonify({"error":str(e)}),400
