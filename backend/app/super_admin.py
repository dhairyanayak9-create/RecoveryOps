from flask import Blueprint, request, jsonify, render_template
from sqlalchemy import text
from werkzeug.security import generate_password_hash
from datetime import datetime, timezone, timedelta
import re
from . import db
from .models import User, AgencyCompany, Vehicle
from .auth import auth_required

super_admin = Blueprint("super_admin", __name__)

PLANS = {
    "STARTER": {"monthly_price": 999, "yearly_price": 9999, "user_limit": 5, "repo_agent_limit": 3, "vehicle_case_limit": 5000, "storage_limit_gb": 5},
    "PROFESSIONAL": {"monthly_price": 2499, "yearly_price": 24999, "user_limit": 15, "repo_agent_limit": 10, "vehicle_case_limit": 25000, "storage_limit_gb": 25},
    "ENTERPRISE": {"monthly_price": 5999, "yearly_price": 59999, "user_limit": 50, "repo_agent_limit": 30, "vehicle_case_limit": 100000, "storage_limit_gb": 1000},
}

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
            plan VARCHAR(30) NOT NULL DEFAULT 'STARTER',
            billing_cycle VARCHAR(20) NOT NULL DEFAULT 'monthly',
            status VARCHAR(20) NOT NULL DEFAULT 'TRIAL',
            monthly_price NUMERIC(12,2) NOT NULL DEFAULT 999,
            yearly_price NUMERIC(12,2) NOT NULL DEFAULT 9999,
            user_limit INTEGER NOT NULL DEFAULT 5,
            repo_agent_limit INTEGER NOT NULL DEFAULT 3,
            vehicle_case_limit INTEGER NOT NULL DEFAULT 5000,
            storage_limit_gb NUMERIC(12,2) NOT NULL DEFAULT 5,
            start_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expiry_date TIMESTAMP NOT NULL,
            trial_end_date TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    db.session.commit()

def _subscription_json(row):
    if not row: return None
    d=dict(row)
    for k in ("monthly_price","yearly_price","storage_limit_gb"):
        if d.get(k) is not None: d[k]=float(d[k])
    for k in ("start_date","expiry_date","trial_end_date","created_at","updated_at"):
        if d.get(k) is not None and hasattr(d[k],"isoformat"): d[k]=d[k].isoformat()
    return d

@super_admin.get("/super-admin")
def super_admin_page(): return render_template("super_admin.html")

@super_admin.get("/api/super-admin/plans")
@auth_required
def list_plans(user):
    denied=_require_super_admin(user)
    if denied:return denied
    return jsonify(PLANS)

@super_admin.get("/api/super-admin/agencies")
@auth_required
def list_agencies(user):
    denied=_require_super_admin(user)
    if denied:return denied
    _ensure_subscription_table()
    rows=db.session.execute(text("""
        SELECT a.id,a.name,a.slug,a.phone,a.email,a.address,a.is_active,a.created_at,
        COUNT(DISTINCT am.user_id) FILTER(WHERE am.role='STAFF' AND am.is_active) staff_count,
        COUNT(DISTINCT am.user_id) FILTER(WHERE am.role='REPO_AGENT' AND am.is_active) repo_agent_count,
        COUNT(DISTINCT CASE WHEN u.role='ADMIN' AND u.is_deleted=false THEN u.id END) admin_count,
        s.plan,s.billing_cycle,s.status subscription_status,s.monthly_price,s.yearly_price,
        s.user_limit,s.repo_agent_limit,s.vehicle_case_limit,s.storage_limit_gb,
        s.start_date,s.expiry_date,s.trial_end_date
        FROM public.agencies a
        LEFT JOIN public.agency_members am ON am.agency_id=a.id
        LEFT JOIN public."user" u ON u.id=am.user_id
        LEFT JOIN agency_subscriptions s ON s.agency_id=a.id
        GROUP BY a.id,s.agency_id
        ORDER BY a.created_at DESC
    """)).mappings().all()
    return jsonify([dict(r) for r in rows])

@super_admin.post("/api/super-admin/agencies")
@auth_required
def create_agency(user):
    denied=_require_super_admin(user)
    if denied:return denied
    _ensure_subscription_table()
    d=request.get_json(silent=True) or {}
    name=str(d.get("agency_name") or "").strip(); slug=str(d.get("slug") or "").strip().upper()
    phone=_normalize_phone(d.get("phone")); email=_normalize_email(d.get("email")); address=str(d.get("address") or "").strip() or None
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
        now=datetime.now(timezone.utc); trial_end=now+timedelta(days=14); p=PLANS["STARTER"]
        db.session.execute(text("INSERT INTO agency_subscriptions(agency_id,plan,billing_cycle,status,monthly_price,yearly_price,user_limit,repo_agent_limit,vehicle_case_limit,storage_limit_gb,start_date,expiry_date,trial_end_date) VALUES(:id,'STARTER','monthly','TRIAL',:mp,:yp,:ul,:rl,:vl,:sg,:st,:ex,:te)"),{"id":c.id,"mp":p["monthly_price"],"yp":p["yearly_price"],"ul":p["user_limit"],"rl":p["repo_agent_limit"],"vl":p["vehicle_case_limit"],"sg":p["storage_limit_gb"],"st":now,"ex":trial_end,"te":trial_end})
        db.session.commit()
        return jsonify({"message":"Agency and admin created successfully","agency":dict(a),"admin":{"id":admin.id,"username":admin.username,"full_name":admin.full_name,"email":admin.email,"phone":admin.phone,"role":admin.role},"subscription":{"plan":"STARTER","status":"TRIAL","billing_cycle":"monthly","trial_days":14,**p}}),201
    except Exception: db.session.rollback(); raise

@super_admin.put("/api/super-admin/agencies/<int:agency_id>")
@auth_required
def update_agency(user,agency_id):
    denied=_require_super_admin(user)
    if denied:return denied
    _ensure_subscription_table()
    d=request.get_json(silent=True) or {}; name=str(d.get("name") or "").strip(); slug=str(d.get("slug") or "").strip().upper(); phone=_normalize_phone(d.get("phone")); email=_normalize_email(d.get("email")); address=str(d.get("address") or "").strip() or None; active=bool(d.get("is_active",True))
    if not name or not slug:return jsonify({"error":"Agency name and slug are required"}),400
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*",slug):return jsonify({"error":"Invalid agency slug"}),400
    try:
        if db.session.execute(text("SELECT id FROM public.agencies WHERE (lower(name)=lower(:n) OR upper(slug)=upper(:s)) AND id<>:id LIMIT 1"),{"n":name,"s":slug,"id":agency_id}).first():return jsonify({"error":"Agency name or slug already exists"}),409
        a=db.session.execute(text("UPDATE public.agencies SET name=:n,slug=:s,phone=:p,email=:e,address=:a,is_active=:active,updated_at=now() WHERE id=:id RETURNING id,name,slug,phone,email,address,is_active,created_at"),{"id":agency_id,"n":name,"s":slug,"p":phone,"e":email,"a":address,"active":active}).mappings().first()
        if not a:return jsonify({"error":"Agency not found"}),404
        db.session.execute(text("UPDATE public.agency_members SET is_active=:active,updated_at=now() WHERE agency_id=:id"),{"id":agency_id,"active":active})
        c=AgencyCompany.query.filter_by(id=agency_id).first()
        if c:c.name=name;c.code=slug;c.phone=phone;c.email=email;c.active=active
        db.session.commit(); return jsonify({"message":"Agency updated successfully","agency":dict(a)})
    except Exception:db.session.rollback();raise

@super_admin.put("/api/super-admin/agencies/<int:agency_id>/subscription")
@auth_required
def update_subscription(user,agency_id):
    denied=_require_super_admin(user)
    if denied:return denied
    _ensure_subscription_table()
    d=request.get_json(silent=True) or {}; plan=str(d.get("plan") or "STARTER").upper(); cycle=str(d.get("billing_cycle") or "monthly").lower(); status=str(d.get("status") or "ACTIVE").upper()
    if plan not in PLANS:return jsonify({"error":"Invalid plan"}),400
    if cycle not in {"monthly","yearly"}:return jsonify({"error":"Invalid billing cycle"}),400
    if status not in {"TRIAL","ACTIVE","PAST_DUE","EXPIRED","CANCELLED","SUSPENDED"}:return jsonify({"error":"Invalid subscription status"}),400
    agency=db.session.execute(text("SELECT id FROM public.agencies WHERE id=:id"),{"id":agency_id}).first()
    if not agency:return jsonify({"error":"Agency not found"}),404
    p=PLANS[plan]; now=datetime.now(timezone.utc)
    months=12 if cycle=="yearly" else 1
    current=db.session.execute(text("SELECT expiry_date FROM agency_subscriptions WHERE agency_id=:id"),{"id":agency_id}).scalar()
    start=now; expiry=now+timedelta(days=365 if cycle=="yearly" else 30)
    if status=="TRIAL": expiry=now+timedelta(days=14)
    db.session.execute(text("""
        INSERT INTO agency_subscriptions(agency_id,plan,billing_cycle,status,monthly_price,yearly_price,user_limit,repo_agent_limit,vehicle_case_limit,storage_limit_gb,start_date,expiry_date,trial_end_date,updated_at)
        VALUES(:id,:plan,:cycle,:status,:mp,:yp,:ul,:rl,:vl,:sg,:st,:ex,:te,now())
        ON CONFLICT(agency_id) DO UPDATE SET plan=EXCLUDED.plan,billing_cycle=EXCLUDED.billing_cycle,status=EXCLUDED.status,monthly_price=EXCLUDED.monthly_price,yearly_price=EXCLUDED.yearly_price,user_limit=EXCLUDED.user_limit,repo_agent_limit=EXCLUDED.repo_agent_limit,vehicle_case_limit=EXCLUDED.vehicle_case_limit,storage_limit_gb=EXCLUDED.storage_limit_gb,start_date=EXCLUDED.start_date,expiry_date=EXCLUDED.expiry_date,trial_end_date=EXCLUDED.trial_end_date,updated_at=now()
    """),{"id":agency_id,"plan":plan,"cycle":cycle,"status":status,"mp":p["monthly_price"],"yp":p["yearly_price"],"ul":p["user_limit"],"rl":p["repo_agent_limit"],"vl":p["vehicle_case_limit"],"sg":p["storage_limit_gb"],"st":start,"ex":expiry,"te":expiry if status=="TRIAL" else None})
    db.session.commit()
    row=db.session.execute(text("SELECT * FROM agency_subscriptions WHERE agency_id=:id"),{"id":agency_id}).mappings().first()
    return jsonify({"message":"Subscription updated successfully","subscription":_subscription_json(row)})

@super_admin.get("/api/super-admin/agencies/<int:agency_id>/subscription")
@auth_required
def get_subscription(user,agency_id):
    denied=_require_super_admin(user)
    if denied:return denied
    _ensure_subscription_table()
    row=db.session.execute(text("SELECT * FROM agency_subscriptions WHERE agency_id=:id"),{"id":agency_id}).mappings().first()
    return jsonify(_subscription_json(row) or {"error":"Subscription not found"})

@super_admin.delete("/api/super-admin/agencies/<int:agency_id>")
@auth_required
def delete_agency(user,agency_id):
    denied=_require_super_admin(user)
    if denied:return denied
    _ensure_subscription_table()
    try:
        a=db.session.execute(text("SELECT id FROM public.agencies WHERE id=:id"),{"id":agency_id}).first()
        if not a:return jsonify({"error":"Agency not found"}),404
        db.session.execute(text("UPDATE public.agencies SET is_active=false,updated_at=now() WHERE id=:id"),{"id":agency_id}); db.session.execute(text("UPDATE public.agency_members SET is_active=false,updated_at=now() WHERE agency_id=:id"),{"id":agency_id}); db.session.execute(text("UPDATE agency_subscriptions SET status='SUSPENDED',updated_at=now() WHERE agency_id=:id"),{"id":agency_id})
        c=AgencyCompany.query.filter_by(id=agency_id).first()
        if c:c.active=False
        db.session.commit(); return jsonify({"message":"Agency deleted successfully"})
    except Exception:db.session.rollback();raise

def _agency_scoped_vehicle_query(user,write=False):
    q=Vehicle.query
    if user.role=="SUPER_ADMIN":return q
    if user.role in {"ADMIN","STAFF"} and user.agency_id is not None:return q.filter(Vehicle.agency_id==user.agency_id)
    return q.filter(db.false())

try:
    from . import routes as _routes
    _routes.accessible_vehicle_query=_agency_scoped_vehicle_query
except Exception:pass
