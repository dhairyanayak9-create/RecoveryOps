from flask import Blueprint, request, jsonify, render_template
from sqlalchemy import text
from werkzeug.security import generate_password_hash
from datetime import datetime, timezone, timedelta
import re, io
import pandas as pd
from . import db
from .models import User, AgencyCompany, Vehicle, Bank, Client, RecoveryCase, AppFile, Challan
from .auth import auth_required

super_admin = Blueprint("super_admin", __name__)

PLANS = {
    "STARTER": {"monthly_price": 999, "yearly_price": 9999, "user_limit": 5, "repo_agent_limit": 3, "vehicle_case_limit": 5000, "storage_limit_gb": 5},
    "PROFESSIONAL": {"monthly_price": 2499, "yearly_price": 24999, "user_limit": 15, "repo_agent_limit": 10, "vehicle_case_limit": 25000, "storage_limit_gb": 25},
    "ENTERPRISE": {"monthly_price": 5999, "yearly_price": 59999, "user_limit": 50, "repo_agent_limit": 30, "vehicle_case_limit": 100000, "storage_limit_gb": 1000},
}

TENANT_TABLES = [User, Bank, Vehicle, Client, RecoveryCase, AppFile, Challan]

def _require_super_admin(user):
    if user.role != "SUPER_ADMIN": return jsonify({"error":"Super Admin access required"}),403

def _normalize_email(value):
    value=str(value or "").strip().lower(); return value or None

def _normalize_phone(value):
    digits=re.sub(r"\D","",str(value or ""))
    if digits.startswith("91") and len(digits)==12: digits=digits[2:]
    return digits or None

def _make_username(email,slug):
    base=re.sub(r"[^a-zA-Z0-9]+","_",(email or slug).split("@")[0]).strip("_").lower() or "admin"
    username=base; n=2
    while User.query.filter_by(username=username).first(): username=f"{base}_{n}"; n+=1
    return username

def _ensure_subscription_table():
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS agency_subscriptions (
            agency_id INTEGER PRIMARY KEY,
            plan VARCHAR(30) NOT NULL DEFAULT 'STARTER', billing_cycle VARCHAR(20) NOT NULL DEFAULT 'monthly',
            status VARCHAR(20) NOT NULL DEFAULT 'TRIAL', monthly_price NUMERIC(12,2) NOT NULL DEFAULT 999,
            yearly_price NUMERIC(12,2) NOT NULL DEFAULT 9999, user_limit INTEGER NOT NULL DEFAULT 5,
            repo_agent_limit INTEGER NOT NULL DEFAULT 3, vehicle_case_limit INTEGER NOT NULL DEFAULT 5000,
            storage_limit_gb NUMERIC(12,2) NOT NULL DEFAULT 5, start_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expiry_date TIMESTAMP NOT NULL, trial_end_date TIMESTAMP, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""))
    db.session.commit()

def _sub(row):
    if not row:return None
    d=dict(row)
    for k in ("monthly_price","yearly_price","storage_limit_gb"):
        if d.get(k) is not None:d[k]=float(d[k])
    for k in ("start_date","expiry_date","trial_end_date","created_at","updated_at"):
        if d.get(k) is not None and hasattr(d[k],"isoformat"):d[k]=d[k].isoformat()
    return d

def _agency_storage(agency_id):
    # Logical row storage estimate. This measures PostgreSQL row payloads by agency, not external object storage.
    total=0
    for model in TENANT_TABLES:
        try:
            table=model.__tablename__
            result=db.session.execute(text(f'''SELECT COALESCE(SUM(pg_column_size(t)),0) FROM "{table}" t WHERE agency_id=:id'''),{"id":agency_id}).scalar()
            total += int(result or 0)
        except Exception:
            db.session.rollback()
    return total

def _storage_gb(n): return round(float(n)/(1024**3),4)

def _agency_detail(agency_id):
    a=db.session.execute(text("SELECT id,name,slug,phone,email,address,is_active,created_at FROM public.agencies WHERE id=:id"),{"id":agency_id}).mappings().first()
    if not a:return None
    company=AgencyCompany.query.filter_by(name=a["name"]).first()
    legacy_id=company.id if company else agency_id
    users=User.query.filter_by(agency_id=legacy_id).order_by(User.id.desc()).all()
    detail=dict(a)
    detail["users"]=[_user_json(x) for x in users]
    detail["counts"]={"admins":sum(x.role=="ADMIN" and not x.is_deleted for x in users),"staff":sum(x.role=="STAFF" and not x.is_deleted for x in users),"repo_agents":sum(x.role=="REPO_AGENT" and not x.is_deleted for x in users),"vehicles":Vehicle.query.filter_by(agency_id=legacy_id).count(),"banks":Bank.query.filter_by(agency_id=legacy_id).count(),"clients":Client.query.filter_by(agency_id=legacy_id).count(),"recovery_cases":RecoveryCase.query.filter_by(agency_id=legacy_id).count(),"files":AppFile.query.filter_by(agency_id=legacy_id).count(),"challans":Challan.query.filter_by(agency_id=legacy_id).count()}
    detail["legacy_agency_id"]=legacy_id
    detail["storage_bytes"]=_agency_storage(legacy_id); detail["storage_gb"]=_storage_gb(detail["storage_bytes"])
    return detail

def _user_json(u):
    return {"id":u.id,"agency_id":u.agency_id,"username":u.username,"full_name":u.full_name,"email":u.email,"phone":u.phone,"role":u.role,"active":bool(u.active),"is_deleted":bool(u.is_deleted),"device_id":u.device_id,"created_at":u.created_at.isoformat() if u.created_at else None}

@super_admin.get("/super-admin")
def super_admin_page(): return render_template("super_admin.html")

@super_admin.get("/api/super-admin/plans")
@auth_required
def list_plans(user):
    denied=_require_super_admin(user)
    if denied:return denied
    return jsonify(PLANS)

@super_admin.get("/api/super-admin/dashboard")
@auth_required
def dashboard(user):
    denied=_require_super_admin(user)
    if denied:return denied
    _ensure_subscription_table()
    agencies=db.session.execute(text("SELECT id,name,slug,is_active FROM public.agencies ORDER BY created_at DESC")).mappings().all()
    items=[]; total_bytes=0
    for a in agencies:
        d=_agency_detail(a["id"])
        if not d:continue
        total_bytes += d["storage_bytes"]
        s=db.session.execute(text("SELECT * FROM agency_subscriptions WHERE agency_id=:id"),{"id":a["id"]}).mappings().first()
        item={k:d[k] for k in ("id","name","slug","phone","email","address","is_active","created_at","counts","storage_bytes","storage_gb","legacy_agency_id")}
        item["subscription"]=_sub(s)
        items.append(item)
    return jsonify({"total_agencies":len(items),"total_storage_bytes":total_bytes,"total_storage_gb":_storage_gb(total_bytes),"agencies":items})

@super_admin.get("/api/super-admin/agencies")
@auth_required
def list_agencies(user):
    denied=_require_super_admin(user)
    if denied:return denied
    return dashboard(user)

@super_admin.get("/api/super-admin/agencies/<int:agency_id>")
@auth_required
def agency_detail(user,agency_id):
    denied=_require_super_admin(user)
    if denied:return denied
    _ensure_subscription_table(); d=_agency_detail(agency_id)
    if not d:return jsonify({"error":"Agency not found"}),404
    d["subscription"]=_sub(db.session.execute(text("SELECT * FROM agency_subscriptions WHERE agency_id=:id"),{"id":agency_id}).mappings().first())
    return jsonify(d)

@super_admin.post("/api/super-admin/agencies")
@auth_required
def create_agency(user):
    denied=_require_super_admin(user)
    if denied:return denied
    _ensure_subscription_table(); d=request.get_json(silent=True) or {}
    name=str(d.get("agency_name") or "").strip(); slug=str(d.get("slug") or "").strip().upper(); phone=_normalize_phone(d.get("phone")); email=_normalize_email(d.get("email")); address=str(d.get("address") or "").strip() or None
    admin_name=str(d.get("admin_name") or "").strip(); admin_email=_normalize_email(d.get("admin_email")); admin_phone=_normalize_phone(d.get("admin_phone")); password=str(d.get("password") or "")
    if not name or not slug or not admin_name or not password:return jsonify({"error":"Agency name, slug, admin name and password are required"}),400
    if len(slug)>80 or not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*",slug):return jsonify({"error":"Invalid agency slug"}),400
    if len(password)<8:return jsonify({"error":"Admin password must be at least 8 characters"}),400
    if not admin_email and not admin_phone:return jsonify({"error":"Admin email or phone is required"}),400
    try:
        if db.session.execute(text("SELECT id FROM public.agencies WHERE lower(name)=lower(:n) OR upper(slug)=upper(:s) LIMIT 1"),{"n":name,"s":slug}).first():return jsonify({"error":"Agency name or slug already exists"}),409
        if admin_email and User.query.filter(db.func.lower(User.email)==admin_email).first():return jsonify({"error":"Admin email is already registered"}),409
        if admin_phone and User.query.filter(User.phone==admin_phone).first():return jsonify({"error":"Admin phone is already registered"}),409
        a=db.session.execute(text("INSERT INTO public.agencies(name,slug,phone,email,address,is_active) VALUES(:n,:s,:p,:e,:a,true) RETURNING id,name,slug,phone,email,address,is_active,created_at"),{"n":name,"s":slug,"p":phone,"e":email,"a":address}).mappings().one()
        c=AgencyCompany(name=name,code=slug,phone=phone,email=email,active=True); db.session.add(c); db.session.flush()
        admin=User(agency_id=c.id,username=_make_username(admin_email,slug),password_hash=generate_password_hash(password,method="scrypt:32768:8:1"),full_name=admin_name,role="ADMIN",phone=admin_phone,email=admin_email,active=True,is_deleted=False); db.session.add(admin); db.session.flush()
        db.session.execute(text("INSERT INTO public.agency_members(agency_id,user_id,role,is_active) VALUES(:a,:u,'ADMIN',true)"),{"a":a["id"],"u":admin.id})
        now=datetime.now(timezone.utc); trial=now+timedelta(days=14); p=PLANS["STARTER"]
        db.session.execute(text("INSERT INTO agency_subscriptions(agency_id,plan,billing_cycle,status,monthly_price,yearly_price,user_limit,repo_agent_limit,vehicle_case_limit,storage_limit_gb,start_date,expiry_date,trial_end_date) VALUES(:id,'STARTER','monthly','TRIAL',:mp,:yp,:ul,:rl,:vl,:sg,:st,:ex,:te)"),{"id":a["id"],"mp":p["monthly_price"],"yp":p["yearly_price"],"ul":p["user_limit"],"rl":p["repo_agent_limit"],"vl":p["vehicle_case_limit"],"sg":p["storage_limit_gb"],"st":now,"ex":trial,"te":trial})
        db.session.commit(); return jsonify({"message":"Agency and admin created successfully","agency":dict(a),"admin":_user_json(admin),"subscription":{"plan":"STARTER","status":"TRIAL","billing_cycle":"monthly","trial_days":14,**p}}),201
    except Exception:db.session.rollback();raise

@super_admin.put("/api/super-admin/agencies/<int:agency_id>")
@auth_required
def update_agency(user,agency_id):
    denied=_require_super_admin(user)
    if denied:return denied
    d=request.get_json(silent=True) or {}; name=str(d.get("name") or "").strip(); slug=str(d.get("slug") or "").strip().upper(); phone=_normalize_phone(d.get("phone")); email=_normalize_email(d.get("email")); address=str(d.get("address") or "").strip() or None; active=bool(d.get("is_active",True))
    if not name or not slug:return jsonify({"error":"Agency name and slug are required"}),400
    try:
        a=db.session.execute(text("UPDATE public.agencies SET name=:n,slug=:s,phone=:p,email=:e,address=:a,is_active=:active,updated_at=now() WHERE id=:id RETURNING *"),{"id":agency_id,"n":name,"s":slug,"p":phone,"e":email,"a":address,"active":active}).mappings().first()
        if not a:return jsonify({"error":"Agency not found"}),404
        c=AgencyCompany.query.filter_by(id=agency_id).first()
        if c:c.name=name;c.code=slug;c.phone=phone;c.email=email;c.active=active
        db.session.execute(text("UPDATE public.agency_members SET is_active=:active,updated_at=now() WHERE agency_id=:id"),{"id":agency_id,"active":active}); db.session.commit(); return jsonify({"message":"Agency updated successfully","agency":dict(a)})
    except Exception:db.session.rollback();raise

@super_admin.put("/api/super-admin/agencies/<int:agency_id>/subscription")
@auth_required
def update_subscription(user,agency_id):
    denied=_require_super_admin(user)
    if denied:return denied
    _ensure_subscription_table(); d=request.get_json(silent=True) or {}; plan=str(d.get("plan") or "STARTER").upper(); cycle=str(d.get("billing_cycle") or "monthly").lower(); status=str(d.get("status") or "ACTIVE").upper()
    if plan not in PLANS or cycle not in {"monthly","yearly"} or status not in {"TRIAL","ACTIVE","PAST_DUE","EXPIRED","CANCELLED","SUSPENDED"}:return jsonify({"error":"Invalid subscription details"}),400
    if not db.session.execute(text("SELECT id FROM public.agencies WHERE id=:id"),{"id":agency_id}).first():return jsonify({"error":"Agency not found"}),404
    p=PLANS[plan]; now=datetime.now(timezone.utc); expiry=now+timedelta(days=14 if status=="TRIAL" else (365 if cycle=="yearly" else 30))
    db.session.execute(text("""INSERT INTO agency_subscriptions(agency_id,plan,billing_cycle,status,monthly_price,yearly_price,user_limit,repo_agent_limit,vehicle_case_limit,storage_limit_gb,start_date,expiry_date,trial_end_date,updated_at) VALUES(:id,:plan,:cycle,:status,:mp,:yp,:ul,:rl,:vl,:sg,:st,:ex,:te,now()) ON CONFLICT(agency_id) DO UPDATE SET plan=EXCLUDED.plan,billing_cycle=EXCLUDED.billing_cycle,status=EXCLUDED.status,monthly_price=EXCLUDED.monthly_price,yearly_price=EXCLUDED.yearly_price,user_limit=EXCLUDED.user_limit,repo_agent_limit=EXCLUDED.repo_agent_limit,vehicle_case_limit=EXCLUDED.vehicle_case_limit,storage_limit_gb=EXCLUDED.storage_limit_gb,start_date=EXCLUDED.start_date,expiry_date=EXCLUDED.expiry_date,trial_end_date=EXCLUDED.trial_end_date,updated_at=now()"""),{"id":agency_id,"plan":plan,"cycle":cycle,"status":status,"mp":p["monthly_price"],"yp":p["yearly_price"],"ul":p["user_limit"],"rl":p["repo_agent_limit"],"vl":p["vehicle_case_limit"],"sg":p["storage_limit_gb"],"st":now,"ex":expiry,"te":expiry if status=="TRIAL" else None}); db.session.commit(); return jsonify({"message":"Subscription updated successfully","subscription":_sub(db.session.execute(text("SELECT * FROM agency_subscriptions WHERE agency_id=:id"),{"id":agency_id}).mappings().first())})

@super_admin.get("/api/super-admin/agencies/<int:agency_id>/users")
@auth_required
def agency_users(user,agency_id):
    denied=_require_super_admin(user)
    if denied:return denied
    d=_agency_detail(agency_id)
    if not d:return jsonify({"error":"Agency not found"}),404
    return jsonify(d["users"])

@super_admin.post("/api/super-admin/agencies/<int:agency_id>/users")
@auth_required
def create_agency_user(user,agency_id):
    denied=_require_super_admin(user)
    if denied:return denied
    d=request.get_json(silent=True) or {}; name=str(d.get("full_name") or "").strip(); role=str(d.get("role") or "STAFF").upper(); email=_normalize_email(d.get("email")); phone=_normalize_phone(d.get("phone")); password=str(d.get("password") or "")
    if role not in {"ADMIN","STAFF","REPO_AGENT"} or not name or not password or (not email and not phone):return jsonify({"error":"Name, valid role, password and email or phone are required"}),400
    c=AgencyCompany.query.filter_by(name=db.session.execute(text("SELECT name FROM public.agencies WHERE id=:id"),{"id":agency_id}).scalar()).first()
    if not c:return jsonify({"error":"Agency mapping not found"}),404
    if email and User.query.filter(db.func.lower(User.email)==email).first():return jsonify({"error":"Email is already registered"}),409
    if phone and User.query.filter(User.phone==phone).first():return jsonify({"error":"Phone is already registered"}),409
    try:
        u=User(agency_id=c.id,username=_make_username(email,c.code),password_hash=generate_password_hash(password,method="scrypt:32768:8:1"),full_name=name,role=role,phone=phone,email=email,active=True,is_deleted=False); db.session.add(u); db.session.flush()
        db.session.execute(text("INSERT INTO public.agency_members(agency_id,user_id,role,is_active) VALUES(:a,:u,:r,true)"),{"a":agency_id,"u":u.id,"r":role}); db.session.commit(); return jsonify(_user_json(u)),201
    except Exception:db.session.rollback();raise

@super_admin.put("/api/super-admin/users/<int:user_id>")
@auth_required
def update_user(user,user_id):
    denied=_require_super_admin(user)
    if denied:return denied
    u=User.query.get_or_404(user_id); d=request.get_json(silent=True) or {}
    if "full_name" in d:u.full_name=str(d.get("full_name") or u.full_name).strip()
    if "email" in d:u.email=_normalize_email(d.get("email"))
    if "phone" in d:u.phone=_normalize_phone(d.get("phone"))
    if "role" in d and str(d["role"]).upper() in {"ADMIN","STAFF","REPO_AGENT"}:u.role=str(d["role"]).upper()
    if "active" in d:u.active=bool(d["active"])
    if "password" in d and str(d["password"]):u.password_hash=generate_password_hash(str(d["password"]),method="scrypt:32768:8:1")
    db.session.commit(); return jsonify(_user_json(u))

@super_admin.delete("/api/super-admin/users/<int:user_id>")
@auth_required
def delete_user(user,user_id):
    denied=_require_super_admin(user)
    if denied:return denied
    u=User.query.get_or_404(user_id)
    if u.role=="SUPER_ADMIN":return jsonify({"error":"Cannot delete Super Admin"}),403
    u.active=False;u.is_deleted=True
    try:db.session.execute(text("UPDATE public.agency_members SET is_active=false,updated_at=now() WHERE user_id=:id"),{"id":u.id})
    except Exception:db.session.rollback()
    db.session.commit();return jsonify({"message":"User deactivated"})

@super_admin.post("/api/super-admin/agencies/<int:agency_id>/upload")
@auth_required
def upload_agency_data(user,agency_id):
    denied=_require_super_admin(user)
    if denied:return denied
    d=_agency_detail(agency_id)
    if not d:return jsonify({"error":"Agency not found"}),404
    f=request.files.get("file")
    if not f:return jsonify({"error":"File is required"}),400
    try:
        name=f.filename or "upload"
        if name.lower().endswith(".csv"):df=pd.read_csv(f)
        elif name.lower().endswith((".xlsx",".xls")):df=pd.read_excel(f)
        else:return jsonify({"error":"Only CSV/XLS/XLSX files are supported"}),400
        df.columns=[str(c).strip().lower().replace(" ","_") for c in df.columns]
        aliases={"registration":"registration_no","registration_number":"registration_no","reg_no":"registration_no","customer":"customer_name","customer_name":"customer_name","mobile_no":"mobile","chassis":"chassis_no","engine":"engine_no"}
        df=df.rename(columns={c:aliases.get(c,c) for c in df.columns})
        allowed=[c for c in ["account_no","loan_no","registration_no","chassis_no","engine_no","customer_name","mobile","city","state","pincode","vehicle_type"] if c in df.columns]
        if not allowed:return jsonify({"error":"No supported vehicle columns found"}),400
        bank=Bank.query.filter_by(agency_id=d["legacy_agency_id"]).first(); bank_id=bank.id if bank else None; created=0
        for _,r in df.iterrows():
            vals={c:(None if pd.isna(r.get(c)) else str(r.get(c)).strip()) for c in allowed}
            if not any(vals.values()):continue
            vals["agency_id"]=d["legacy_agency_id"];vals["bank_id"]=bank_id;v=Vehicle(**vals);db.session.add(v);created+=1
        db.session.commit();return jsonify({"message":"Data uploaded successfully","rows_created":created,"agency_id":agency_id})
    except Exception as e:db.session.rollback();return jsonify({"error":str(e)}),400

@super_admin.delete("/api/super-admin/agencies/<int:agency_id>")
@auth_required
def delete_agency(user,agency_id):
    denied=_require_super_admin(user)
    if denied:return denied
    _ensure_subscription_table()
    if not db.session.execute(text("SELECT id FROM public.agencies WHERE id=:id"),{"id":agency_id}).first():return jsonify({"error":"Agency not found"}),404
    db.session.execute(text("UPDATE public.agencies SET is_active=false,updated_at=now() WHERE id=:id"),{"id":agency_id});db.session.execute(text("UPDATE public.agency_members SET is_active=false,updated_at=now() WHERE agency_id=:id"),{"id":agency_id});db.session.execute(text("UPDATE agency_subscriptions SET status='SUSPENDED',updated_at=now() WHERE agency_id=:id"),{"id":agency_id})
    c=AgencyCompany.query.filter_by(id=agency_id).first()
    if c:c.active=False
    db.session.commit();return jsonify({"message":"Agency deactivated; data preserved"})

def _agency_scoped_vehicle_query(user,write=False):
    q=Vehicle.query
    if user.role=="SUPER_ADMIN":return q
    if user.role in {"ADMIN","STAFF"} and user.agency_id is not None:return q.filter(Vehicle.agency_id==user.agency_id)
    return q.filter(db.false())
try:
    from . import routes as _routes
    _routes.accessible_vehicle_query=_agency_scoped_vehicle_query
except Exception:pass
