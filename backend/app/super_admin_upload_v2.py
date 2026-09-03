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


TENANT_TABLES = [
    "bank", "vehicle", "status_history_legacy", "notification", "sync_mutation",
    "client", "recovery_case", "app_file", "challan", "chat_conversation",
    "chat_message", "report",
]
SEQUENCED_TABLES = [
    "bank", "vehicle", "status_history_legacy", "notification", "client",
    "recovery_case", "app_file", "challan", "chat_conversation", "chat_message", "report",
]


def _public_legacy_agency_id(agency_id):
    name = db.session.execute(text("SELECT name FROM public.agencies WHERE id=:id"), {"id": agency_id}).scalar()
    if not name:
        return None
    return db.session.execute(
        text("SELECT id FROM public.agency_company WHERE name=:name LIMIT 1"), {"name": name}
    ).scalar() or agency_id


def _tenant_schema(legacy_id):
    return db.session.execute(
        text("SELECT schema_name FROM public.agency_tenants WHERE legacy_agency_id=:id LIMIT 1"),
        {"id": int(legacy_id)},
    ).scalar()


def _safe_schema(value):
    value = str(value or "")
    return value if re.fullmatch(r"recovery_agency_[0-9]+", value) else None


def _repair_tenant_sequences(schema):
    for table in SEQUENCED_TABLES:
        if db.session.execute(text("SELECT to_regclass(:n)"), {"n": f"{schema}.{table}"}).scalar() is None:
            continue
        seq = f'{schema}.{table}_id_seq'
        db.session.execute(text(f'CREATE SEQUENCE IF NOT EXISTS "{schema}"."{table}_id_seq"'))
        db.session.execute(text(f'ALTER SEQUENCE "{schema}"."{table}_id_seq" OWNED BY "{schema}"."{table}".id'))
        db.session.execute(text(f'ALTER TABLE "{schema}"."{table}" ALTER COLUMN id SET DEFAULT nextval(:seq::regclass)'), {"seq": seq})
        max_id = db.session.execute(text(f'SELECT max(id) FROM "{schema}"."{table}"')).scalar()
        if max_id is None:
            db.session.execute(text(f'SELECT setval(:seq::regclass, 1, false)'), {"seq": seq})
        else:
            db.session.execute(text(f'SELECT setval(:seq::regclass, :v, true)'), {"seq": seq, "v": int(max_id)})

    if db.session.execute(text("SELECT to_regclass(:n)"), {"n": f"{schema}.data_assignment"}).scalar() is not None:
        seq = f'{schema}.data_assignment_id_seq'
        db.session.execute(text(f'CREATE SEQUENCE IF NOT EXISTS "{schema}"."data_assignment_id_seq"'))
        db.session.execute(text(f'ALTER SEQUENCE "{schema}"."data_assignment_id_seq" OWNED BY "{schema}".data_assignment.id'))
        db.session.execute(text(f'ALTER TABLE "{schema}".data_assignment ALTER COLUMN id SET DEFAULT nextval(:seq::regclass)'), {"seq": seq})
        max_id = db.session.execute(text(f'SELECT max(id) FROM "{schema}".data_assignment')).scalar()
        if max_id is None:
            db.session.execute(text(f'SELECT setval(:seq::regclass, 1, false)'), {"seq": seq})
        else:
            db.session.execute(text(f'SELECT setval(:seq::regclass, :v, true)'), {"seq": seq, "v": int(max_id)})


def _provision_schema(modern_id, legacy_id, copy_public_rows=False):
    schema = _safe_schema(f"recovery_agency_{int(modern_id)}")
    if not schema:
        raise ValueError("Invalid tenant schema")

    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS public.agency_tenants (
            agency_id BIGINT PRIMARY KEY REFERENCES public.agencies(id) ON DELETE RESTRICT,
            legacy_agency_id INTEGER UNIQUE REFERENCES public.agency_company(id) ON DELETE RESTRICT,
            schema_name TEXT NOT NULL UNIQUE,
            provisioned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            schema_version INTEGER NOT NULL DEFAULT 1
        )
    """))
    db.session.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    for table in TENANT_TABLES:
        exists = db.session.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{table}"}).scalar()
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
    _repair_tenant_sequences(schema)

    db.session.execute(text("""
        INSERT INTO public.agency_tenants(agency_id, legacy_agency_id, schema_name)
        VALUES(:agency_id,:legacy_id,:schema_name)
        ON CONFLICT(agency_id) DO UPDATE SET
          legacy_agency_id=EXCLUDED.legacy_agency_id,
          schema_name=EXCLUDED.schema_name,
          schema_version=GREATEST(public.agency_tenants.schema_version,1)
    """), {"agency_id": int(modern_id), "legacy_id": int(legacy_id), "schema_name": schema})
    return schema


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
    if admin_phone and admin_phone.startswith("91") and len(admin_phone) == 12: admin_phone = admin_phone[2:]
    if phone and phone.startswith("91") and len(phone) == 12: phone = phone[2:]
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
        db.session.add(company); db.session.flush()
        username_base = re.sub(r"[^a-zA-Z0-9]+", "_", (admin_email or slug).split("@")[0]).strip("_").lower() or "admin"
        username = username_base; n = 2
        while db.session.execute(text('SELECT id FROM public."user" WHERE username=:u LIMIT 1'), {"u": username}).first():
            username = f"{username_base}_{n}"; n += 1
        admin = User(agency_id=company.id, username=username, password_hash=generate_password_hash(password, method="scrypt:32768:8:1"), full_name=admin_name, role="ADMIN", phone=admin_phone, email=admin_email, active=True, is_deleted=False)
        db.session.add(admin); db.session.flush()
        db.session.execute(text("INSERT INTO public.agency_members(agency_id,user_id,role,is_active) VALUES(:a,:u,'ADMIN',true)"), {"a": agency["id"], "u": admin.id})
        now = datetime.now(timezone.utc); trial = now + timedelta(days=14)
        db.session.execute(text("""
            INSERT INTO public.agency_subscriptions
              (agency_id,plan,billing_cycle,status,monthly_price,yearly_price,user_limit,repo_agent_limit,vehicle_case_limit,storage_limit_gb,start_date,expiry_date,trial_end_date)
            VALUES(:id,'STARTER','monthly','TRIAL',999,9999,5,3,5000,5,:st,:ex,:te)
            ON CONFLICT (agency_id) DO NOTHING
        """), {"id": agency["id"], "st": now, "ex": trial, "te": trial})
        schema = _provision_schema(agency["id"], company.id, copy_public_rows=False)
        db.session.commit()
        return jsonify({"message": "Agency, admin and isolated data store created successfully", "agency": dict(agency), "schema": schema, "admin_id": admin.id}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400


@super_admin.post("/api/super-admin/agencies/<int:agency_id>/ensure-store")
@auth_required
def ensure_agency_store(user, agency_id):
    denied = _require_super_admin(user)
    if denied: return denied
    row = db.session.execute(text("""
        SELECT a.id AS agency_id, c.id AS legacy_id
        FROM public.agencies a LEFT JOIN public.agency_company c ON c.name=a.name OR c.code=a.slug
        WHERE a.id=:id LIMIT 1
    """), {"id": agency_id}).mappings().first()
    if not row: return jsonify({"error": "Agency not found"}), 404
    try:
        schema = _provision_schema(row["agency_id"], row["legacy_id"] or row["agency_id"], copy_public_rows=True)
        db.session.commit()
        return jsonify({"message": "Agency data store ready", "schema": schema})
    except Exception as e:
        db.session.rollback(); return jsonify({"error": str(e)}), 400


@super_admin.post("/api/super-admin/agencies/<int:agency_id>/upload-v2")
@auth_required
def upload_agency_data_v2(user, agency_id):
    denied = _require_super_admin(user)
    if denied: return denied
    legacy_id = _public_legacy_agency_id(agency_id)
    if legacy_id is None: return jsonify({"error": "Agency not found"}), 404
    schema = _safe_schema(_tenant_schema(legacy_id))
    if not schema: return jsonify({"error": "Agency data store is not provisioned"}), 503
    f = request.files.get("file")
    if not f or not (f.filename or "").strip(): return jsonify({"error": "File is required"}), 400
    try: bank_id = int(request.form.get("bank_id"))
    except (TypeError, ValueError): return jsonify({"error": "Select a valid bank from this agency"}), 400
    bank = db.session.execute(text(f'SELECT id,name FROM "{schema}".bank WHERE id=:id AND agency_id=:aid LIMIT 1'), {"id": bank_id, "aid": legacy_id}).mappings().first()
    if not bank: return jsonify({"error": "Selected bank does not belong to this agency"}), 404
    vehicle_type = str(request.form.get("vehicle_type") or "").strip().upper()
    if vehicle_type not in {"TWO_WHEELER", "FOUR_WHEELER", "CV"}: return jsonify({"error": "Vehicle type is required: 2 Wheeler, 4 Wheeler or CV"}), 400
    filename = (f.filename or "").strip(); lower = filename.lower()
    if not lower.endswith((".csv", ".xlsx", ".xls")): return jsonify({"error": "Only CSV, XLS and XLSX files are supported"}), 400
    try:
        df = pd.read_csv(f) if lower.endswith(".csv") else pd.read_excel(f)
        df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
        aliases = {"registration":"registration_no","registration_number":"registration_no","reg_no":"registration_no","vehicle_no":"registration_no","customer":"customer_name","mobile_no":"mobile","phone":"mobile","chassis":"chassis_no","engine":"engine_no","account":"account_no","account_number":"account_no","loan":"loan_no","loan_number":"loan_no"}
        df = df.rename(columns={c: aliases.get(c,c) for c in df.columns})
        supported = ["account_no","loan_no","registration_no","chassis_no","engine_no","customer_name","mobile","city","state","pincode","latitude","longitude"]
        if not any(c in df.columns for c in supported): return jsonify({"error": "No supported vehicle columns found"}), 400
        rows=[]; skipped=0
        for _, r in df.iterrows():
            def val(col):
                if col not in df.columns: return None
                x=r.get(col)
                if pd.isna(x): return None
                s=str(x).strip(); return s or None
            reg=val("registration_no"); chassis=val("chassis_no"); account=val("account_no")
            if not any((reg,chassis,account)): skipped+=1; continue
            def num(col):
                x=val(col)
                try: return float(x) if x is not None else None
                except: return None
            rows.append({"agency_id":legacy_id,"account_no":account,"loan_no":val("loan_no"),"registration_no":reg,"registration_suffix":re.sub(r"\D","",reg or "")[-4:] or None,"chassis_no":(chassis or "")[:150],"engine_no":(val("engine_no") or "")[:150],"customer_name":val("customer_name"),"mobile":val("mobile"),"city":val("city"),"state":val("state"),"pincode":val("pincode"),"vehicle_type":vehicle_type,"latitude":num("latitude"),"longitude":num("longitude"),"bank_id":bank["id"]})
        # Use explicit SQL against the selected tenant schema so this path cannot accidentally write to public.
        af = db.session.execute(text(f'''INSERT INTO "{schema}".app_file(agency_id,filename,vehicle_type,bank_id,record_count,status,uploaded_by) VALUES(:aid,:fn,:vt,:bid,0,'IMPORTING',:uid) RETURNING id'''), {"aid":legacy_id,"fn":filename,"vt":vehicle_type,"bid":bank["id"],"uid":user.id}).scalar()
        if rows:
            for i in range(0,len(rows),5000):
                batch=rows[i:i+5000]
                params=[]
                for x in batch:
                    x["source_file_id"]=af
                    params.append(x)
                db.session.execute(text(f'''INSERT INTO "{schema}".vehicle(agency_id,account_no,loan_no,registration_no,registration_suffix,chassis_no,engine_no,customer_name,mobile,city,state,pincode,vehicle_type,bank_id,source_file_id,latitude,longitude,status,version,updated_at,created_at) VALUES(:agency_id,:account_no,:loan_no,:registration_no,:registration_suffix,:chassis_no,:engine_no,:customer_name,:mobile,:city,:state,:pincode,:vehicle_type,:bank_id,:source_file_id,:latitude,:longitude,'PENDING',1,now(),now())'''), batch)
        db.session.execute(text(f'''UPDATE "{schema}".app_file SET record_count=:n,status='IMPORTED' WHERE id=:id'''), {"n":len(rows),"id":af})
        db.session.commit()
        return jsonify({"message":"Data uploaded successfully","rows_created":len(rows),"rows_skipped":skipped,"agency_id":agency_id,"bank_id":bank["id"],"bank_name":bank["name"],"vehicle_type":vehicle_type,"file_id":af}),201
    except Exception as e:
        db.session.rollback(); return jsonify({"error":str(e)}),400
