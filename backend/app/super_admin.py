from flask import Blueprint, request, jsonify, render_template
from sqlalchemy import text
from werkzeug.security import generate_password_hash
import re
from . import db
from .models import User, AgencyCompany, Vehicle
from .auth import auth_required
super_admin = Blueprint("super_admin", __name__)
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
@super_admin.get("/super-admin")
def super_admin_page(): return render_template("super_admin.html")
@super_admin.get("/api/super-admin/agencies")
@auth_required
def list_agencies(user):
    denied=_require_super_admin(user)
    if denied:return denied
    rows=db.session.execute(text("SELECT a.id,a.name,a.slug,a.phone,a.email,a.address,a.is_active,a.created_at,COUNT(DISTINCT am.user_id) FILTER(WHERE am.role='STAFF' AND am.is_active) staff_count,COUNT(DISTINCT am.user_id) FILTER(WHERE am.role='REPO_AGENT' AND am.is_active) repo_agent_count,COUNT(DISTINCT CASE WHEN u.role='ADMIN' AND u.is_deleted=false THEN u.id END) admin_count FROM public.agencies a LEFT JOIN public.agency_members am ON am.agency_id=a.id LEFT JOIN public.\"user\" u ON u.id=am.user_id GROUP BY a.id ORDER BY a.created_at DESC")).mappings().all()
    return jsonify([dict(r) for r in rows])
@super_admin.post("/api/super-admin/agencies")
@auth_required
def create_agency(user):
    denied=_require_super_admin(user)
    if denied:return denied
    d=request.get_json(silent=True) or {}; name=str(d.get("agency_name") or "").strip(); slug=str(d.get("slug") or "").strip().upper(); phone=_normalize_phone(d.get("phone")); email=_normalize_email(d.get("email")); address=str(d.get("address") or "").strip() or None; admin_name=str(d.get("admin_name") or "").strip(); admin_email=_normalize_email(d.get("admin_email")); admin_phone=_normalize_phone(d.get("admin_phone")); password=str(d.get("password") or "")
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
        db.session.execute(text("INSERT INTO public.agency_members(agency_id,user_id,role,is_active) VALUES(:a,:u,'ADMIN',true)"),{"a":a["id"],"u":admin.id}); db.session.commit()
        return jsonify({"message":"Agency and admin created successfully","agency":dict(a),"admin":{"id":admin.id,"username":admin.username,"full_name":admin.full_name,"email":admin.email,"phone":admin.phone,"role":admin.role}}),201
    except Exception: db.session.rollback(); raise
@super_admin.put("/api/super-admin/agencies/<int:agency_id>")
@auth_required
def update_agency(user,agency_id):
    denied=_require_super_admin(user)
    if denied:return denied
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
@super_admin.delete("/api/super-admin/agencies/<int:agency_id>")
@auth_required
def delete_agency(user,agency_id):
    denied=_require_super_admin(user)
    if denied:return denied
    try:
        a=db.session.execute(text("SELECT id FROM public.agencies WHERE id=:id"),{"id":agency_id}).first()
        if not a:return jsonify({"error":"Agency not found"}),404
        db.session.execute(text("UPDATE public.agencies SET is_active=false,updated_at=now() WHERE id=:id"),{"id":agency_id}); db.session.execute(text("UPDATE public.agency_members SET is_active=false,updated_at=now() WHERE agency_id=:id"),{"id":agency_id})
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
