from flask import jsonify, request, g
from sqlalchemy import text
from werkzeug.security import generate_password_hash
from datetime import datetime, timezone, timedelta
import csv, io, re

from . import db
from .models import User
from .auth import current_user
from .super_admin import super_admin

TENANT_TABLES = (
    "bank", "vehicle", "status_history_legacy", "notification", "sync_mutation",
    "client", "recovery_case", "app_file", "challan", "chat_conversation",
    "chat_message", "report", "data_assignment", "agency_subscriptions",
)


def _sa():
    u = current_user()
    return u if u and u.active and not getattr(u, "is_deleted", False) and u.role == "SUPER_ADMIN" else None


def _legacy(agency_id):
    row = db.session.execute(text("""
        SELECT a.id AS modern_id, COALESCE(c.id, a.id) AS legacy_id, a.name, a.slug
        FROM public.agencies a
        LEFT JOIN public.agency_company c ON c.name=a.name OR c.code=a.slug
        WHERE a.id=:id LIMIT 1
    """), {"id": agency_id}).mappings().first()
    return row


def _schema(agency_id):
    row = db.session.execute(text("""
        SELECT t.schema_name
        FROM public.agency_tenants t
        WHERE t.agency_id=:id
        LIMIT 1
    """), {"id": agency_id}).scalar()
    return str(row) if row and re.fullmatch(r"recovery_agency_[0-9]+", str(row)) else None


def _q(schema, table):
    if table not in TENANT_TABLES:
        raise ValueError("invalid tenant table")
    return f'"{schema}"."{table}"'


def _agency_counts(schema, legacy_id):
    out = {}
    for table in ("vehicle", "bank", "client", "recovery_case", "app_file", "challan"):
        out[table if table != "vehicle" else "vehicles"] = int(db.session.execute(text(f"SELECT count(*) FROM {_q(schema, table)} WHERE agency_id=:id"), {"id": legacy_id}).scalar() or 0)
    out["users"] = int(db.session.execute(text("SELECT count(*) FROM public.user WHERE agency_id=:id AND is_deleted=false"), {"id": legacy_id}).scalar() or 0)
    return out


def _storage(schema):
    total = 0
    for table in TENANT_TABLES:
        try:
            total += int(db.session.execute(text("SELECT COALESCE(pg_total_relation_size(to_regclass(:r)),0)"), {"r": f"{schema}.{table}"}).scalar() or 0)
        except Exception:
            db.session.rollback()
    return total


def _user_row(r):
    return {"id": r["id"], "agency_id": r["agency_id"], "username": r["username"], "full_name": r["full_name"], "email": r["email"], "phone": r["phone"], "role": r["role"], "active": bool(r["active"]), "is_deleted": bool(r["is_deleted"]), "device_id": r["device_id"], "created_at": r["created_at"].isoformat() if r["created_at"] else None}


def install(app):
    def dashboard():
        if not _sa(): return jsonify({"error":"Super Admin access required"}),403
        agencies = db.session.execute(text("SELECT id,name,slug,phone,email,address,is_active,created_at FROM public.agencies ORDER BY created_at DESC")).mappings().all()
        items=[]; total=0
        for a in agencies:
            m=_legacy(a["id"]); schema=_schema(a["id"])
            if not m or not schema: continue
            counts=_agency_counts(schema,m["legacy_id"])
            used=_storage(schema); total += used
            sub=db.session.execute(text("SELECT * FROM public.agency_subscriptions WHERE agency_id=:id"),{"id":a["id"]}).mappings().first()
            items.append({**dict(a),"legacy_agency_id":m["legacy_id"],"schema_name":schema,"counts":counts,"storage_bytes":used,"storage_gb":round(used/(1024**3),4),"subscription":dict(sub) if sub else None})
        return jsonify({"total_agencies":len(items),"total_storage_bytes":total,"total_storage_gb":round(total/(1024**3),4),"agencies":items})

    def agency_detail(agency_id):
        if not _sa(): return jsonify({"error":"Super Admin access required"}),403
        a=db.session.execute(text("SELECT * FROM public.agencies WHERE id=:id"),{"id":agency_id}).mappings().first()
        m=_legacy(agency_id); schema=_schema(agency_id)
        if not a or not m or not schema: return jsonify({"error":"Agency or data store not found"}),404
        users=db.session.execute(text("SELECT id,agency_id,username,full_name,email,phone,role,active,is_deleted,device_id,created_at FROM public.user WHERE agency_id=:id AND is_deleted=false ORDER BY id DESC"),{"id":m["legacy_id"]}).mappings().all()
        banks=db.session.execute(text(f"SELECT id,name,code,phone,email,active,created_at FROM {_q(schema,'bank')} WHERE agency_id=:id ORDER BY name"),{"id":m["legacy_id"]}).mappings().all()
        files=db.session.execute(text(f"SELECT id,filename,vehicle_type,bank_id,record_count,status,uploaded_by,created_at FROM {_q(schema,'app_file')} WHERE agency_id=:id ORDER BY id DESC"),{"id":m["legacy_id"]}).mappings().all()
        sub=db.session.execute(text("SELECT * FROM public.agency_subscriptions WHERE agency_id=:id"),{"id":agency_id}).mappings().first()
        return jsonify({"agency":dict(a),"legacy_agency_id":m["legacy_id"],"schema_name":schema,"users":[_user_row(x) for x in users],"banks":[dict(x) for x in banks],"files":[dict(x) for x in files],"counts":_agency_counts(schema,m["legacy_id"]),"storage_bytes":_storage(schema),"subscription":dict(sub) if sub else None})

    def users(agency_id):
        if not _sa(): return jsonify({"error":"Super Admin access required"}),403
        m=_legacy(agency_id)
        if not m:return jsonify({"error":"Agency not found"}),404
        rows=db.session.execute(text("SELECT id,agency_id,username,full_name,email,phone,role,active,is_deleted,device_id,created_at FROM public.user WHERE agency_id=:id AND is_deleted=false ORDER BY id DESC"),{"id":m["legacy_id"]}).mappings().all()
        return jsonify([_user_row(x) for x in rows])

    def create_user(agency_id):
        if not _sa(): return jsonify({"error":"Super Admin access required"}),403
        m=_legacy(agency_id)
        if not m:return jsonify({"error":"Agency not found"}),404
        d=request.get_json(silent=True) or {}; name=str(d.get("full_name") or "").strip(); email=str(d.get("email") or "").strip().lower() or None; phone=re.sub(r"\D","",str(d.get("phone") or "")) or None; role=str(d.get("role") or "STAFF").upper(); password=str(d.get("password") or "")
        if not name or role not in {"ADMIN","STAFF","REPO_AGENT"} or len(password)<8 or (not email and not phone): return jsonify({"error":"Name, role, password(8+) and email or phone are required"}),400
        if email and db.session.execute(text('SELECT 1 FROM public."user" WHERE lower(email)=:v LIMIT 1'),{"v":email}).first():return jsonify({"error":"Email already registered"}),409
        if phone and db.session.execute(text('SELECT 1 FROM public."user" WHERE phone=:v LIMIT 1'),{"v":phone}).first():return jsonify({"error":"Phone already registered"}),409
        base=re.sub(r"[^a-zA-Z0-9]+","_",(email or name)).strip("_").lower() or "user"; username=base; n=2
        while db.session.execute(text('SELECT 1 FROM public."user" WHERE username=:u'),{"u":username}).first():username=f"{base}_{n}";n+=1
        r=db.session.execute(text('''INSERT INTO public."user"(agency_id,username,password_hash,full_name,role,phone,email,active,is_deleted) VALUES(:a,:u,:p,:n,:r,:ph,:e,true,false) RETURNING id,agency_id,username,full_name,email,phone,role,active,is_deleted,device_id,created_at'''),{"a":m["legacy_id"],"u":username,"p":generate_password_hash(password,method="scrypt:32768:8:1"),"n":name,"r":role,"ph":phone,"e":email}).mappings().one()
        db.session.execute(text("INSERT INTO public.agency_members(agency_id,user_id,role,is_active) VALUES(:a,:u,:r,true)"),{"a":agency_id,"u":r["id"],"r":role})
        db.session.commit();return jsonify(_user_row(r)),201

    def update_user(uid):
        if not _sa(): return jsonify({"error":"Super Admin access required"}),403
        d=request.get_json(silent=True) or {}; sets=[]; params={"id":uid}
        for field in ("full_name","email","phone","role","active"):
            if field in d:
                v=d[field]
                if field=="email":v=str(v or "").strip().lower() or None
                if field=="phone":v=re.sub(r"\D","",str(v or "")) or None
                if field=="role":v=str(v).upper()
                if field=="role" and v not in {"ADMIN","STAFF","REPO_AGENT","SUPER_ADMIN"}:return jsonify({"error":"Invalid role"}),400
                sets.append(f"{field}=:{field}");params[field]=v
        if d.get("password"):
            if len(str(d["password"]))<8:return jsonify({"error":"Password must be 8+ characters"}),400
            sets.append("password_hash=:password_hash");params["password_hash"]=generate_password_hash(str(d["password"]),method="scrypt:32768:8:1")
        if not sets:return jsonify({"error":"No changes supplied"}),400
        r=db.session.execute(text(f'SELECT id,agency_id FROM public."user" WHERE id=:id AND is_deleted=false'),{"id":uid}).mappings().first()
        if not r:return jsonify({"error":"User not found"}),404
        row=db.session.execute(text(f'UPDATE public."user" SET {", ".join(sets)} WHERE id=:id RETURNING id,agency_id,username,full_name,email,phone,role,active,is_deleted,device_id,created_at'),params).mappings().one()
        if "role" in d:db.session.execute(text("UPDATE public.agency_members SET role=:r WHERE user_id=:u"),{"r":str(d["role"]).upper(),"u":uid})
        db.session.commit();return jsonify(_user_row(row))

    def delete_user(uid):
        if not _sa(): return jsonify({"error":"Super Admin access required"}),403
        r=db.session.execute(text('SELECT id,agency_id,role FROM public."user" WHERE id=:id AND is_deleted=false'),{"id":uid}).mappings().first()
        if not r:return jsonify({"error":"User not found"}),404
        if r["role"]=="SUPER_ADMIN":return jsonify({"error":"Super Admin cannot be deleted"}),409
        db.session.execute(text("DELETE FROM public.agency_members WHERE user_id=:u"),{"u":uid})
        db.session.execute(text('DELETE FROM public."user" WHERE id=:id'),{"id":uid})
        db.session.commit();return jsonify({"message":"User deleted","id":uid})

    def banks(agency_id):
        if not _sa(): return jsonify({"error":"Super Admin access required"}),403
        m=_legacy(agency_id);s=_schema(agency_id)
        if not m or not s:return jsonify({"error":"Agency data store not found"}),404
        rows=db.session.execute(text(f"SELECT id,name,code,phone,email,active,created_at FROM {_q(s,'bank')} WHERE agency_id=:a ORDER BY name"),{"a":m["legacy_id"]}).mappings().all();return jsonify([dict(x) for x in rows])

    def create_bank(agency_id):
        if not _sa():return jsonify({"error":"Super Admin access required"}),403
        m=_legacy(agency_id);s=_schema(agency_id);d=request.get_json(silent=True) or {};name=str(d.get("name") or "").strip();code=str(d.get("code") or "").strip() or None
        if not m or not s:return jsonify({"error":"Agency data store not found"}),404
        if not name:return jsonify({"error":"Bank name required"}),400
        if db.session.execute(text(f"SELECT 1 FROM {_q(s,'bank')} WHERE agency_id=:a AND (lower(name)=lower(:n) OR (:c IS NOT NULL AND lower(code)=lower(:c))) LIMIT 1"),{"a":m["legacy_id"],"n":name,"c":code}).first():return jsonify({"error":"Bank already exists"}),409
        r=db.session.execute(text(f"INSERT INTO {_q(s,'bank')}(agency_id,name,code,phone,email,active) VALUES(:a,:n,:c,:p,:e,true) RETURNING id,name,code,phone,email,active,created_at"),{"a":m["legacy_id"],"n":name,"c":code,"p":d.get("phone"),"e":str(d.get("email") or "").strip().lower() or None}).mappings().one();db.session.commit();return jsonify(dict(r)),201

    def update_bank(agency_id,bid):
        if not _sa():return jsonify({"error":"Super Admin access required"}),403
        m=_legacy(agency_id);s=_schema(agency_id);d=request.get_json(silent=True) or {}
        if not m or not s:return jsonify({"error":"Agency data store not found"}),404
        r=db.session.execute(text(f"SELECT id FROM {_q(s,'bank')} WHERE id=:b AND agency_id=:a"),{"b":bid,"a":m["legacy_id"]}).first()
        if not r:return jsonify({"error":"Bank not found"}),404
        sets=[];p={"b":bid,"a":m["legacy_id"]}
        for f in ("name","code","phone","email","active"):
            if f in d:sets.append(f"{f}=:{f}");p[f]=d[f]
        if not sets:return jsonify({"error":"No changes supplied"}),400
        row=db.session.execute(text(f"UPDATE {_q(s,'bank')} SET {', '.join(sets)} WHERE id=:b AND agency_id=:a RETURNING id,name,code,phone,email,active,created_at"),p).mappings().one();db.session.commit();return jsonify(dict(row))

    def delete_bank(agency_id,bid):
        if not _sa():return jsonify({"error":"Super Admin access required"}),403
        m=_legacy(agency_id);s=_schema(agency_id)
        if not m or not s:return jsonify({"error":"Agency data store not found"}),404
        if not db.session.execute(text(f"SELECT 1 FROM {_q(s,'bank')} WHERE id=:b AND agency_id=:a"),{"b":bid,"a":m["legacy_id"]}).first():return jsonify({"error":"Bank not found"}),404
        db.session.execute(text(f"DELETE FROM {_q(s,'vehicle')} WHERE bank_id=:b AND agency_id=:a"),{"b":bid,"a":m["legacy_id"]})
        db.session.execute(text(f"DELETE FROM {_q(s,'app_file')} WHERE bank_id=:b AND agency_id=:a"),{"b":bid,"a":m["legacy_id"]})
        db.session.execute(text(f"DELETE FROM {_q(s,'bank')} WHERE id=:b AND agency_id=:a"),{"b":bid,"a":m["legacy_id"]});db.session.commit();return jsonify({"message":"Bank and dependent data deleted","id":bid})

    def files_list(agency_id):
        if not _sa():return jsonify({"error":"Super Admin access required"}),403
        m=_legacy(agency_id);s=_schema(agency_id)
        if not m or not s:return jsonify({"error":"Agency data store not found"}),404
        rows=db.session.execute(text(f"SELECT f.id,f.filename,f.vehicle_type,f.bank_id,b.name AS bank_name,f.record_count,f.status,f.uploaded_by,f.created_at FROM {_q(s,'app_file')} f LEFT JOIN {_q(s,'bank')} b ON b.id=f.bank_id WHERE f.agency_id=:a ORDER BY f.id DESC"),{"a":m["legacy_id"]}).mappings().all();return jsonify([dict(x) for x in rows])

    def file_delete(agency_id,fid):
        if not _sa():return jsonify({"error":"Super Admin access required"}),403
        m=_legacy(agency_id);s=_schema(agency_id)
        if not m or not s:return jsonify({"error":"Agency data store not found"}),404
        r=db.session.execute(text(f"SELECT id,filename FROM {_q(s,'app_file')} WHERE id=:f AND agency_id=:a"),{"f":fid,"a":m["legacy_id"]}).mappings().first()
        if not r:return jsonify({"error":"File not found"}),404
        for t in ("status_history_legacy","sync_mutation","recovery_case","challan"):
            db.session.execute(text(f"DELETE FROM {_q(s,t)} WHERE vehicle_id IN (SELECT id FROM {_q(s,'vehicle')} WHERE source_file_id=:f AND agency_id=:a)"),{"f":fid,"a":m["legacy_id"]})
        db.session.execute(text(f"DELETE FROM {_q(s,'vehicle')} WHERE source_file_id=:f AND agency_id=:a"),{"f":fid,"a":m["legacy_id"]})
        db.session.execute(text(f"DELETE FROM {_q(s,'app_file')} WHERE id=:f AND agency_id=:a"),{"f":fid,"a":m["legacy_id"]});db.session.commit();return jsonify({"message":"File and all imported records deleted","id":fid,"filename":r["filename"]})

    def vehicle_update(agency_id,vid):
        if not _sa():return jsonify({"error":"Super Admin access required"}),403
        m=_legacy(agency_id);s=_schema(agency_id);d=request.get_json(silent=True) or {}
        if not m or not s:return jsonify({"error":"Agency data store not found"}),404
        r=db.session.execute(text(f"SELECT id FROM {_q(s,'vehicle')} WHERE id=:v AND agency_id=:a"),{"v":vid,"a":m["legacy_id"]}).first()
        if not r:return jsonify({"error":"Vehicle not found"}),404
        allowed=("account_no","loan_no","registration_no","chassis_no","engine_no","customer_name","mobile","city","state","pincode","vehicle_type","bank_id","status")
        sets=[];p={"v":vid,"a":m["legacy_id"]}
        for f in allowed:
            if f in d:sets.append(f"{f}=:{f}");p[f]=d[f]
        if "registration_no" in d:p["registration_suffix"]=re.sub(r"\D","",str(d["registration_no"] or ""))[-4:] or None;sets.append("registration_suffix=:registration_suffix")
        if not sets:return jsonify({"error":"No changes supplied"}),400
        row=db.session.execute(text(f"UPDATE {_q(s,'vehicle')} SET {', '.join(sets)},updated_at=now() WHERE id=:v AND agency_id=:a RETURNING id,registration_no,customer_name,vehicle_type,bank_id,status,updated_at"),p).mappings().one();db.session.commit();return jsonify(dict(row))

    def vehicle_delete(agency_id,vid):
        if not _sa():return jsonify({"error":"Super Admin access required"}),403
        m=_legacy(agency_id);s=_schema(agency_id)
        if not m or not s:return jsonify({"error":"Agency data store not found"}),404
        if not db.session.execute(text(f"SELECT 1 FROM {_q(s,'vehicle')} WHERE id=:v AND agency_id=:a"),{"v":vid,"a":m["legacy_id"]}).first():return jsonify({"error":"Vehicle not found"}),404
        for t in ("status_history_legacy","sync_mutation","recovery_case","challan"):
            db.session.execute(text(f"DELETE FROM {_q(s,t)} WHERE vehicle_id=:v AND agency_id=:a"),{"v":vid,"a":m["legacy_id"]})
        db.session.execute(text(f"DELETE FROM {_q(s,'vehicle')} WHERE id=:v AND agency_id=:a"),{"v":vid,"a":m["legacy_id"]});db.session.commit();return jsonify({"message":"Vehicle deleted","id":vid})

    def vehicle_list(agency_id):
        if not _sa():return jsonify({"error":"Super Admin access required"}),403
        m=_legacy(agency_id);s=_schema(agency_id)
        if not m or not s:return jsonify({"error":"Agency data store not found"}),404
        limit=min(max(int(request.args.get("limit",1000)),1),5000)
        rows=db.session.execute(text(f"SELECT id,account_no,loan_no,registration_no,chassis_no,engine_no,customer_name,mobile,city,state,pincode,vehicle_type,bank_id,status,updated_at FROM {_q(s,'vehicle')} WHERE agency_id=:a ORDER BY id DESC LIMIT :lim"),{"a":m["legacy_id"],"lim":limit}).mappings().all();return jsonify({"items":[dict(x) for x in rows],"count":len(rows),"agency_id":agency_id})

    def agency_update(agency_id):
        if not _sa():return jsonify({"error":"Super Admin access required"}),403
        d=request.get_json(silent=True) or {};name=str(d.get("name") or "").strip();slug=str(d.get("slug") or "").strip().upper()
        if not name or not slug:return jsonify({"error":"Agency name and slug are required"}),400
        row=db.session.execute(text("UPDATE public.agencies SET name=:n,slug=:s,phone=:p,email=:e,address=:a,is_active=:active,updated_at=now() WHERE id=:id RETURNING *"),{"id":agency_id,"n":name,"s":slug,"p":d.get("phone"),"e":str(d.get("email") or "").strip().lower() or None,"a":d.get("address"),"active":bool(d.get("is_active",True))}).mappings().first()
        if not row:return jsonify({"error":"Agency not found"}),404
        m=_legacy(agency_id)
        if m:db.session.execute(text("UPDATE public.agency_company SET name=:n,code=:s,phone=:p,email=:e,active=:active WHERE id=:id"),{"id":m["legacy_id"],"n":name,"s":slug,"p":d.get("phone"),"e":d.get("email"),"active":bool(d.get("is_active",True))})
        db.session.commit();return jsonify({"message":"Agency updated","agency":dict(row)})

    def agency_delete(agency_id):
        if not _sa():return jsonify({"error":"Super Admin access required"}),403
        m=_legacy(agency_id);s=_schema(agency_id)
        if not m:return jsonify({"error":"Agency not found"}),404
        try:
            if s:
                for t in ("status_history_legacy","sync_mutation","recovery_case","challan","chat_message","chat_conversation","notification","report","data_assignment","vehicle","app_file","client","bank","agency_subscriptions"):
                    db.session.execute(text(f"DELETE FROM {_q(s,t)} WHERE agency_id=:a"),{"a":m["legacy_id"]})
                db.session.execute(text(f'DROP SCHEMA "{s}" CASCADE'))
            db.session.execute(text("DELETE FROM public.agency_members WHERE agency_id=:a"),{"a":agency_id})
            db.session.execute(text('DELETE FROM public."user" WHERE agency_id=:a'),{"a":m["legacy_id"]})
            db.session.execute(text("DELETE FROM public.agency_subscriptions WHERE agency_id=:a"),{"a":agency_id})
            db.session.execute(text("DELETE FROM public.agency_tenants WHERE agency_id=:a"),{"a":agency_id})
            db.session.execute(text("DELETE FROM public.agency_company WHERE id=:a"),{"a":m["legacy_id"]})
            db.session.execute(text("DELETE FROM public.agencies WHERE id=:a"),{"a":agency_id})
            db.session.commit();return jsonify({"message":"Agency permanently deleted","id":agency_id})
        except Exception as e:
            db.session.rollback();return jsonify({"error":str(e)}),400

    app.view_functions['super_admin.dashboard']=dashboard
    app.view_functions['super_admin.agency_detail']=agency_detail
    app.view_functions['super_admin.agency_users']=users
    app.view_functions['super_admin.create_agency_user']=create_user
    app.view_functions['super_admin.update_super_user']=update_user
    app.view_functions['super_admin.delete_super_user']=delete_user
    app.view_functions['super_admin.agency_banks']=banks
    app.view_functions['super_admin.create_agency_bank']=create_bank
    app.view_functions['super_admin.update_agency_bank']=update_bank
    app.view_functions['super_admin.delete_agency_bank']=delete_bank
    app.view_functions['super_admin.agency_files']=files_list
    app.view_functions['super_admin.delete_agency_file']=file_delete
    app.view_functions['super_admin.agency_vehicles']=vehicle_list
    app.view_functions['super_admin.update_agency_vehicle']=vehicle_update
    app.view_functions['super_admin.delete_agency_vehicle']=vehicle_delete
    app.view_functions['super_admin.update_agency']=agency_update
    app.view_functions['super_admin.hard_delete_agency']=agency_delete
