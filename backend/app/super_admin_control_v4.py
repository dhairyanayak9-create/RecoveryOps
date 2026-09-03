from flask import request, jsonify
from sqlalchemy import text
from werkzeug.security import generate_password_hash
import csv, io, re
from . import db
from .auth import current_user

TENANT_TABLES=("bank","vehicle","status_history_legacy","notification","sync_mutation","client","recovery_case","app_file","challan","chat_conversation","chat_message","report","data_assignment","agency_subscriptions")

def sa():
    u=current_user()
    return u if u and u.active and not getattr(u,'is_deleted',False) and u.role=='SUPER_ADMIN' else None

def mapping(aid):
    return db.session.execute(text("""
      SELECT a.id modern_id, coalesce(c.id,a.id) legacy_id, t.schema_name
      FROM public.agencies a
      LEFT JOIN public.agency_company c ON c.name=a.name OR c.code=a.slug
      LEFT JOIN public.agency_tenants t ON t.agency_id=a.id
      WHERE a.id=:id LIMIT 1
    """),{"id":aid}).mappings().first()

def schema(m):
    s=str(m['schema_name'] or '') if m else ''
    return s if re.fullmatch(r'recovery_agency_[0-9]+',s) else None

def T(s,t):
    if t not in TENANT_TABLES: raise ValueError('invalid tenant table')
    return f'"{s}"."{t}"'

def row_user(r):
    return {"id":r["id"],"agency_id":r["agency_id"],"username":r["username"],"full_name":r["full_name"],"email":r["email"],"phone":r["phone"],"role":r["role"],"active":bool(r["active"]),"is_deleted":bool(r["is_deleted"]),"device_id":r["device_id"],"created_at":r["created_at"].isoformat() if r["created_at"] else None}

def install(app):
    def dash():
        if not sa(): return jsonify(error='Super Admin access required'),403
        out=[]; total=0
        agencies=db.session.execute(text("SELECT * FROM public.agencies ORDER BY created_at DESC")).mappings().all()
        for a in agencies:
            m=mapping(a['id']); s=schema(m)
            if not s: continue
            aid=m['legacy_id']
            counts={k:int(db.session.execute(text(f'SELECT count(*) FROM {T(s,t)} WHERE agency_id=:a'),{'a':aid}).scalar() or 0) for k,t in [('vehicles','vehicle'),('banks','bank'),('files','app_file'),('clients','client'),('recovery_cases','recovery_case'),('challans','challan')]}
            counts['users']=int(db.session.execute(text('SELECT count(*) FROM public."user" WHERE agency_id=:a AND is_deleted=false'),{'a':aid}).scalar() or 0)
            used=sum(int(db.session.execute(text('SELECT coalesce(pg_total_relation_size(to_regclass(:r)),0)'),{'r':f'{s}.{t}'}).scalar() or 0) for t in TENANT_TABLES)
            total+=used
            sub=db.session.execute(text('SELECT * FROM public.agency_subscriptions WHERE agency_id=:a'),{'a':a['id']}).mappings().first()
            out.append({**dict(a),'legacy_agency_id':aid,'schema_name':s,'counts':counts,'storage_bytes':used,'storage_gb':round(used/1024**3,4),'subscription':dict(sub) if sub else None})
        return jsonify(total_agencies=len(out),total_storage_bytes=total,total_storage_gb=round(total/1024**3,4),agencies=out)

    def detail(aid):
        if not sa():return jsonify(error='Super Admin access required'),403
        a=db.session.execute(text('SELECT * FROM public.agencies WHERE id=:id'),{'id':aid}).mappings().first();m=mapping(aid);s=schema(m)
        if not a or not m or not s:return jsonify(error='Agency or data store not found'),404
        users=db.session.execute(text('SELECT id,agency_id,username,full_name,email,phone,role,active,is_deleted,device_id,created_at FROM public."user" WHERE agency_id=:a AND is_deleted=false ORDER BY id DESC'),{'a':m['legacy_id']}).mappings().all()
        banks=db.session.execute(text(f'SELECT id,name,code,phone,email,active,created_at FROM {T(s,"bank")} WHERE agency_id=:a ORDER BY name'),{'a':m['legacy_id']}).mappings().all()
        files=db.session.execute(text(f'SELECT f.id,f.filename,f.vehicle_type,f.bank_id,b.name bank_name,f.record_count,f.status,f.uploaded_by,f.created_at FROM {T(s,"app_file")} f LEFT JOIN {T(s,"bank")} b ON b.id=f.bank_id WHERE f.agency_id=:a ORDER BY f.id DESC'),{'a':m['legacy_id']}).mappings().all()
        return jsonify(agency=dict(a),legacy_agency_id=m['legacy_id'],schema_name=s,users=[row_user(x) for x in users],banks=[dict(x) for x in banks],files=[dict(x) for x in files])

    def users(aid):
        if not sa():return jsonify(error='Super Admin access required'),403
        m=mapping(aid)
        if not m:return jsonify(error='Agency not found'),404
        rs=db.session.execute(text('SELECT id,agency_id,username,full_name,email,phone,role,active,is_deleted,device_id,created_at FROM public."user" WHERE agency_id=:a AND is_deleted=false ORDER BY id DESC'),{'a':m['legacy_id']}).mappings().all();return jsonify([row_user(x) for x in rs])

    def create_user(aid):
        if not sa():return jsonify(error='Super Admin access required'),403
        m=mapping(aid);d=request.get_json(silent=True) or {};name=str(d.get('full_name') or '').strip();email=str(d.get('email') or '').strip().lower() or None;phone=re.sub(r'\D','',str(d.get('phone') or '')) or None;role=str(d.get('role') or 'STAFF').upper();pw=str(d.get('password') or '')
        if not m:return jsonify(error='Agency not found'),404
        if not name or role not in {'ADMIN','STAFF','REPO_AGENT'} or len(pw)<8 or not(email or phone):return jsonify(error='Name, role, password(8+) and email or phone are required'),400
        if email and db.session.execute(text('SELECT 1 FROM public."user" WHERE lower(email)=:v'),{'v':email}).first():return jsonify(error='Email already registered'),409
        if phone and db.session.execute(text('SELECT 1 FROM public."user" WHERE phone=:v'),{'v':phone}).first():return jsonify(error='Phone already registered'),409
        base=re.sub(r'[^A-Za-z0-9]+','_',email or name).strip('_').lower() or 'user';u=base;n=2
        while db.session.execute(text('SELECT 1 FROM public."user" WHERE username=:u'),{'u':u}).first():u=f'{base}_{n}';n+=1
        r=db.session.execute(text('''INSERT INTO public."user"(agency_id,username,password_hash,full_name,role,phone,email,active,is_deleted) VALUES(:a,:u,:p,:n,:r,:ph,:e,true,false) RETURNING id,agency_id,username,full_name,email,phone,role,active,is_deleted,device_id,created_at'''),{'a':m['legacy_id'],'u':u,'p':generate_password_hash(pw,method='scrypt:32768:8:1'),'n':name,'r':role,'ph':phone,'e':email}).mappings().one()
        db.session.execute(text('INSERT INTO public.agency_members(agency_id,user_id,role,is_active) VALUES(:a,:u,:r,true)'),{'a':aid,'u':r['id'],'r':role});db.session.commit();return jsonify(row_user(r)),201

    def update_user(uid):
        if not sa():return jsonify(error='Super Admin access required'),403
        d=request.get_json(silent=True) or {};sets=[];p={'id':uid}
        for f in ('full_name','email','phone','role','active'):
            if f in d:
                v=d[f]
                if f=='email':v=str(v or '').strip().lower() or None
                if f=='phone':v=re.sub(r'\D','',str(v or '')) or None
                if f=='role':v=str(v).upper()
                if f=='role' and v not in {'ADMIN','STAFF','REPO_AGENT','SUPER_ADMIN'}:return jsonify(error='Invalid role'),400
                sets.append(f'{f}=:{f}');p[f]=v
        if d.get('password'):
            if len(str(d['password']))<8:return jsonify(error='Password must be 8+ characters'),400
            sets.append('password_hash=:password_hash');p['password_hash']=generate_password_hash(str(d['password']),method='scrypt:32768:8:1')
        if not sets:return jsonify(error='No changes supplied'),400
        if not db.session.execute(text('SELECT 1 FROM public."user" WHERE id=:id AND is_deleted=false'),{'id':uid}).first():return jsonify(error='User not found'),404
        r=db.session.execute(text(f'UPDATE public."user" SET {", ".join(sets)} WHERE id=:id RETURNING id,agency_id,username,full_name,email,phone,role,active,is_deleted,device_id,created_at'),p).mappings().one();db.session.execute(text('UPDATE public.agency_members SET role=:r,is_active=:a WHERE user_id=:u'),{'r':r['role'],'a':bool(r['active']),'u':uid});db.session.commit();return jsonify(row_user(r))

    def delete_user(uid):
        if not sa():return jsonify(error='Super Admin access required'),403
        r=db.session.execute(text('SELECT id,role FROM public."user" WHERE id=:id AND is_deleted=false'),{'id':uid}).mappings().first()
        if not r:return jsonify(error='User not found'),404
        if r['role']=='SUPER_ADMIN':return jsonify(error='Super Admin cannot be deleted'),409
        db.session.execute(text('DELETE FROM public.agency_members WHERE user_id=:u'),{'u':uid});db.session.execute(text('DELETE FROM public."user" WHERE id=:id'),{'id':uid});db.session.commit();return jsonify(message='User deleted',id=uid)

    def banks(aid):
        if not sa():return jsonify(error='Super Admin access required'),403
        m=mapping(aid);s=schema(m)
        if not m or not s:return jsonify(error='Agency data store not found'),404
        rs=db.session.execute(text(f'SELECT id,name,code,phone,email,active,created_at FROM {T(s,"bank")} WHERE agency_id=:a ORDER BY name'),{'a':m['legacy_id']}).mappings().all();return jsonify([dict(x) for x in rs])

    def bank_create(aid):
        if not sa():return jsonify(error='Super Admin access required'),403
        m=mapping(aid);s=schema(m);d=request.get_json(silent=True) or {};name=str(d.get('name') or '').strip();code=str(d.get('code') or '').strip() or None
        if not m or not s:return jsonify(error='Agency data store not found'),404
        if not name:return jsonify(error='Bank name required'),400
        if db.session.execute(text(f'SELECT 1 FROM {T(s,"bank")} WHERE agency_id=:a AND (lower(name)=lower(:n) OR (:c IS NOT NULL AND lower(code)=lower(:c)))'),{'a':m['legacy_id'],'n':name,'c':code}).first():return jsonify(error='Bank already exists'),409
        r=db.session.execute(text(f'INSERT INTO {T(s,"bank")}(agency_id,name,code,phone,email,active) VALUES(:a,:n,:c,:p,:e,true) RETURNING id,name,code,phone,email,active,created_at'),{'a':m['legacy_id'],'n':name,'c':code,'p':d.get('phone'),'e':str(d.get('email') or '').strip().lower() or None}).mappings().one();db.session.commit();return jsonify(dict(r)),201

    def bank_update(aid,bid):
        if not sa():return jsonify(error='Super Admin access required'),403
        m=mapping(aid);s=schema(m);d=request.get_json(silent=True) or {};allowed=('name','code','phone','email','active')
        if not m or not s:return jsonify(error='Agency data store not found'),404
        if not db.session.execute(text(f'SELECT 1 FROM {T(s,"bank")} WHERE id=:b AND agency_id=:a'),{'b':bid,'a':m['legacy_id']}).first():return jsonify(error='Bank not found'),404
        sets=[f'{f}=:{f}' for f in allowed if f in d];p={'a':m['legacy_id'],'b':bid,**{f:d[f] for f in allowed if f in d}}
        if not sets:return jsonify(error='No changes supplied'),400
        r=db.session.execute(text(f'UPDATE {T(s,"bank")} SET {", ".join(sets)} WHERE id=:b AND agency_id=:a RETURNING id,name,code,phone,email,active,created_at'),p).mappings().one();db.session.commit();return jsonify(dict(r))

    def bank_delete(aid,bid):
        if not sa():return jsonify(error='Super Admin access required'),403
        m=mapping(aid);s=schema(m)
        if not m or not s:return jsonify(error='Agency data store not found'),404
        if not db.session.execute(text(f'SELECT 1 FROM {T(s,"bank")} WHERE id=:b AND agency_id=:a'),{'b':bid,'a':m['legacy_id']}).first():return jsonify(error='Bank not found'),404
        for t in ('recovery_case','challan','status_history_legacy','sync_mutation'):db.session.execute(text(f'DELETE FROM {T(s,t)} WHERE vehicle_id IN (SELECT id FROM {T(s,"vehicle")} WHERE bank_id=:b AND agency_id=:a)'),{'b':bid,'a':m['legacy_id']})
        db.session.execute(text(f'DELETE FROM {T(s,"vehicle")} WHERE bank_id=:b AND agency_id=:a'),{'b':bid,'a':m['legacy_id']});db.session.execute(text(f'DELETE FROM {T(s,"app_file")} WHERE bank_id=:b AND agency_id=:a'),{'b':bid,'a':m['legacy_id']});db.session.execute(text(f'DELETE FROM {T(s,"bank")} WHERE id=:b AND agency_id=:a'),{'b':bid,'a':m['legacy_id']});db.session.commit();return jsonify(message='Bank permanently deleted',id=bid)

    def files(aid):
        if not sa():return jsonify(error='Super Admin access required'),403
        m=mapping(aid);s=schema(m)
        if not m or not s:return jsonify(error='Agency data store not found'),404
        rs=db.session.execute(text(f'SELECT f.id,f.filename,f.vehicle_type,f.bank_id,b.name bank_name,f.record_count,f.status,f.uploaded_by,f.created_at FROM {T(s,"app_file")} f LEFT JOIN {T(s,"bank")} b ON b.id=f.bank_id WHERE f.agency_id=:a ORDER BY f.id DESC'),{'a':m['legacy_id']}).mappings().all();return jsonify([dict(x) for x in rs])

    def file_delete(aid,fid):
        if not sa():return jsonify(error='Super Admin access required'),403
        m=mapping(aid);s=schema(m)
        if not m or not s:return jsonify(error='Agency data store not found'),404
        r=db.session.execute(text(f'SELECT id,filename FROM {T(s,"app_file")} WHERE id=:f AND agency_id=:a'),{'f':fid,'a':m['legacy_id']}).mappings().first()
        if not r:return jsonify(error='File not found'),404
        for t in ('status_history_legacy','sync_mutation','recovery_case','challan'):db.session.execute(text(f'DELETE FROM {T(s,t)} WHERE vehicle_id IN (SELECT id FROM {T(s,"vehicle")} WHERE source_file_id=:f AND agency_id=:a)'),{'f':fid,'a':m['legacy_id']})
        db.session.execute(text(f'DELETE FROM {T(s,"vehicle")} WHERE source_file_id=:f AND agency_id=:a'),{'f':fid,'a':m['legacy_id']});db.session.execute(text(f'DELETE FROM {T(s,"app_file")} WHERE id=:f AND agency_id=:a'),{'f':fid,'a':m['legacy_id']});db.session.commit();return jsonify(message='File permanently deleted',id=fid,filename=r['filename'])

    def vehicle_list(aid):
        if not sa():return jsonify(error='Super Admin access required'),403
        m=mapping(aid);s=schema(m);limit=min(max(int(request.args.get('limit',1000)),1),5000)
        if not m or not s:return jsonify(error='Agency data store not found'),404
        rs=db.session.execute(text(f'SELECT id,account_no,loan_no,registration_no,chassis_no,engine_no,customer_name,mobile,city,state,pincode,vehicle_type,bank_id,status,updated_at FROM {T(s,"vehicle")} WHERE agency_id=:a ORDER BY id DESC LIMIT :l'),{'a':m['legacy_id'],'l':limit}).mappings().all();return jsonify(items=[dict(x) for x in rs],count=len(rs))

    def vehicle_update(aid,vid):
        if not sa():return jsonify(error='Super Admin access required'),403
        m=mapping(aid);s=schema(m);d=request.get_json(silent=True) or {}
        if not m or not s:return jsonify(error='Agency data store not found'),404
        allowed=('account_no','loan_no','registration_no','chassis_no','engine_no','customer_name','mobile','city','state','pincode','vehicle_type','bank_id','status');sets=[];p={'a':m['legacy_id'],'v':vid}
        for f in allowed:
            if f in d:sets.append(f'{f}=:{f}');p[f]=d[f]
        if 'registration_no' in d:sets.append('registration_suffix=:rs');p['rs']=re.sub(r'\D','',str(d['registration_no'] or ''))[-4:] or None
        if not sets:return jsonify(error='No changes supplied'),400
        r=db.session.execute(text(f'UPDATE {T(s,"vehicle")} SET {", ".join(sets)},updated_at=now() WHERE id=:v AND agency_id=:a RETURNING id,registration_no,customer_name,vehicle_type,bank_id,status,updated_at'),p).mappings().one();db.session.commit();return jsonify(dict(r))

    def vehicle_delete(aid,vid):
        if not sa():return jsonify(error='Super Admin access required'),403
        m=mapping(aid);s=schema(m)
        if not m or not s:return jsonify(error='Agency data store not found'),404
        if not db.session.execute(text(f'SELECT 1 FROM {T(s,"vehicle")} WHERE id=:v AND agency_id=:a'),{'v':vid,'a':m['legacy_id']}).first():return jsonify(error='Vehicle not found'),404
        for t in ('status_history_legacy','sync_mutation','recovery_case','challan'):db.session.execute(text(f'DELETE FROM {T(s,t)} WHERE vehicle_id=:v AND agency_id=:a'),{'v':vid,'a':m['legacy_id']})
        db.session.execute(text(f'DELETE FROM {T(s,"vehicle")} WHERE id=:v AND agency_id=:a'),{'v':vid,'a':m['legacy_id']});db.session.commit();return jsonify(message='Vehicle permanently deleted',id=vid)

    def agency_update(aid):
        if not sa():return jsonify(error='Super Admin access required'),403
        d=request.get_json(silent=True) or {};name=str(d.get('name') or '').strip();slug=str(d.get('slug') or '').strip().upper()
        if not name or not slug:return jsonify(error='Agency name and slug are required'),400
        r=db.session.execute(text('UPDATE public.agencies SET name=:n,slug=:s,phone=:p,email=:e,address=:ad,is_active=:x,updated_at=now() WHERE id=:id RETURNING *'),{'id':aid,'n':name,'s':slug,'p':d.get('phone'),'e':str(d.get('email') or '').strip().lower() or None,'ad':d.get('address'),'x':bool(d.get('is_active',True))}).mappings().first()
        if not r:return jsonify(error='Agency not found'),404
        m=mapping(aid);db.session.execute(text('UPDATE public.agency_company SET name=:n,code=:s,phone=:p,email=:e,active=:x WHERE id=:id'),{'id':m['legacy_id'],'n':name,'s':slug,'p':d.get('phone'),'e':d.get('email'),'x':bool(d.get('is_active',True))});db.session.commit();return jsonify(message='Agency updated',agency=dict(r))

    def agency_delete(aid):
        if not sa():return jsonify(error='Super Admin access required'),403
        m=mapping(aid);s=schema(m)
        if not m:return jsonify(error='Agency not found'),404
        try:
            if s:db.session.execute(text(f'DROP SCHEMA "{s}" CASCADE'))
            db.session.execute(text('DELETE FROM public.agency_members WHERE agency_id=:a'),{'a':aid});db.session.execute(text('DELETE FROM public."user" WHERE agency_id=:a'),{'a':m['legacy_id']});db.session.execute(text('DELETE FROM public.agency_subscriptions WHERE agency_id=:a'),{'a':aid});db.session.execute(text('DELETE FROM public.agency_tenants WHERE agency_id=:a'),{'a':aid});db.session.execute(text('DELETE FROM public.agency_company WHERE id=:a'),{'a':m['legacy_id']});db.session.execute(text('DELETE FROM public.agencies WHERE id=:a'),{'a':aid});db.session.commit();return jsonify(message='Agency permanently deleted',id=aid)
        except Exception as e:db.session.rollback();return jsonify(error=str(e)),400

    def fast_upload(aid):
        if not sa():return jsonify(error='Super Admin access required'),403
        m=mapping(aid);s=schema(m)
        if not m or not s:return jsonify(error='Agency data store not found'),404
        f=request.files.get('file');filename=(f.filename or '').strip() if f else ''
        if not f or not filename:return jsonify(error='File is required'),400
        try:bid=int(request.form.get('bank_id'))
        except:return jsonify(error='Select a valid bank'),400
        b=db.session.execute(text(f'SELECT id,name FROM {T(s,"bank")} WHERE id=:b AND agency_id=:a'),{'b':bid,'a':m['legacy_id']}).mappings().first()
        if not b:return jsonify(error='Selected bank does not belong to this agency'),404
        vt=str(request.form.get('vehicle_type') or '').strip().upper()
        if vt not in {'TWO_WHEELER','FOUR_WHEELER','CV'}:return jsonify(error='Vehicle type is required'),400
        lower=filename.lower()
        if not lower.endswith(('.csv','.xlsx','.xls')):return jsonify(error='Only CSV, XLS and XLSX files are supported'),400
        aliases={'registration':'registration_no','registration_number':'registration_no','reg_no':'registration_no','vehicle_no':'registration_no','customer':'customer_name','mobile_no':'mobile','phone':'mobile','chassis':'chassis_no','engine':'engine_no','account':'account_no','account_number':'account_no','loan':'loan_no','loan_number':'loan_no'}
        try:
            if lower.endswith('.csv'):
                raw=io.TextIOWrapper(f.stream,encoding='utf-8-sig',newline='');reader=csv.DictReader(raw);headers=[str(x or '').strip().lower().replace(' ','_') for x in (reader.fieldnames or [])]
                reader.fieldnames=headers; rows_iter=reader
            else:
                import pandas as pd
                df=pd.read_excel(f);df.columns=[str(c).strip().lower().replace(' ','_') for c in df.columns];df=df.rename(columns={c:aliases.get(c,c) for c in df.columns});rows_iter=df.to_dict('records')
            batch=[];created=0;skipped=0
            def norm(r):
                rr={aliases.get(str(k).strip().lower().replace(' ','_'),str(k).strip().lower().replace(' ','_')):v for k,v in r.items()}
                def v(k):
                    x=rr.get(k);return None if x is None or str(x).strip().lower() in {'','nan','none'} else str(x).strip()
                reg,ch,acc=v('registration_no'),v('chassis_no'),v('account_no')
                if not(reg or ch or acc):return None
                def num(k):
                    x=v(k)
                    try:return float(x) if x else None
                    except:return None
                return {'agency_id':m['legacy_id'],'account_no':acc,'loan_no':v('loan_no'),'registration_no':reg,'registration_suffix':re.sub(r'\D','',reg or '')[-4:] or None,'chassis_no':(ch or '')[:150],'engine_no':(v('engine_no') or '')[:150],'customer_name':v('customer_name'),'mobile':v('mobile'),'city':v('city'),'state':v('state'),'pincode':v('pincode'),'vehicle_type':vt,'latitude':num('latitude'),'longitude':num('longitude'),'bank_id':bid}
            for r in rows_iter:
                x=norm(r)
                if x:batch.append(x)
                else:skipped+=1
                if len(batch)>=1000:
                    db.session.execute(text(f'''INSERT INTO {T(s,"vehicle")}(agency_id,account_no,loan_no,registration_no,registration_suffix,chassis_no,engine_no,customer_name,mobile,city,state,pincode,vehicle_type,latitude,longitude,bank_id,status,version,created_at,updated_at) VALUES(:agency_id,:account_no,:loan_no,:registration_no,:registration_suffix,:chassis_no,:engine_no,:customer_name,:mobile,:city,:state,:pincode,:vehicle_type,:latitude,:longitude,:bank_id,'PENDING',1,now(),now())'''),batch);created+=len(batch);batch=[]
            if batch:db.session.execute(text(f'''INSERT INTO {T(s,"vehicle")}(agency_id,account_no,loan_no,registration_no,registration_suffix,chassis_no,engine_no,customer_name,mobile,city,state,pincode,vehicle_type,latitude,longitude,bank_id,status,version,created_at,updated_at) VALUES(:agency_id,:account_no,:loan_no,:registration_no,:registration_suffix,:chassis_no,:engine_no,:customer_name,:mobile,:city,:state,:pincode,:vehicle_type,:latitude,:longitude,:bank_id,'PENDING',1,now(),now())'''),batch);created+=len(batch)
            af=db.session.execute(text(f'INSERT INTO {T(s,"app_file")}(agency_id,filename,vehicle_type,bank_id,record_count,status,uploaded_by,created_at) VALUES(:a,:f,:v,:b,:c,\'COMPLETED\',NULL,now()) RETURNING id'),{'a':m['legacy_id'],'f':filename,'v':vt,'b':bid,'c':created}).scalar();db.session.commit();return jsonify(rows_created=created,rows_skipped=skipped,file_id=af,bank_name=b['name'],vehicle_type=vt)
        except Exception as e:db.session.rollback();return jsonify(error=str(e)),400

    # Replace by URL so endpoint names from older modules do not matter.
    handlers={
      '/api/super-admin/dashboard':dash,
      '/api/super-admin/agencies/<int:agency_id>':detail,
      '/api/super-admin/agencies/<int:agency_id>/users':users,
      '/api/super-admin/agencies/<int:agency_id>/banks':banks,
      '/api/super-admin/agencies/<int:agency_id>/files':files,
      '/api/super-admin/agencies/<int:agency_id>/vehicles':vehicle_list,
      '/api/super-admin/agencies/<int:agency_id>':agency_update,
      '/api/super-admin/agencies/<int:agency_id>/upload-v2':fast_upload,
      '/api/super-admin/agencies/<int:agency_id>/users':users,
      '/api/super-admin/agencies/<int:agency_id>/banks':banks,
    }
    for rule in list(app.url_map.iter_rules()):
        if rule.rule in handlers:
            fn=handlers[rule.rule]
            # detail/update share a URL; preserve GET vs PUT via endpoint mapping below.
            if rule.rule.endswith('/users') and rule.methods=={'POST','OPTIONS'}:fn=create_user
            elif rule.rule.endswith('/users') and 'POST' not in rule.methods:fn=users
            elif rule.rule.endswith('/banks') and rule.methods=={'POST','OPTIONS'}:fn=bank_create
            elif rule.rule.endswith('/banks') and 'POST' not in rule.methods:fn=banks
            elif rule.rule.endswith('/files') and 'GET' in rule.methods:fn=files
            elif rule.rule.endswith('/vehicles') and 'GET' in rule.methods:fn=vehicle_list
            elif rule.rule.endswith('/upload-v2'):fn=fast_upload
            elif rule.rule.endswith('/agencies/<int:agency_id>') and 'GET' in rule.methods:fn=detail
            elif rule.rule.endswith('/agencies/<int:agency_id>') and 'PUT' in rule.methods:fn=agency_update
            app.view_functions[rule.endpoint]=fn
        if re.fullmatch(r'/api/super-admin/users/<int:user_id>',rule.rule):
            app.view_functions[rule.endpoint]=update_user if 'PUT' in rule.methods else delete_user
        if re.fullmatch(r'/api/super-admin/agencies/<int:agency_id>/banks/<int:bank_id>',rule.rule):
            app.view_functions[rule.endpoint]=bank_update if 'PUT' in rule.methods else bank_delete
        if re.fullmatch(r'/api/super-admin/agencies/<int:agency_id>/files/<int:file_id>',rule.rule):app.view_functions[rule.endpoint]=file_delete
        if re.fullmatch(r'/api/super-admin/agencies/<int:agency_id>/vehicles/<int:vehicle_id>',rule.rule):app.view_functions[rule.endpoint]=vehicle_update if 'PUT' in rule.methods else vehicle_delete
    app.view_functions.update({})
