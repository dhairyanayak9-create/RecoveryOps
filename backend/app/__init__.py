import os
from flask import Flask, g
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from sqlalchemy import event, inspect, text
from sqlalchemy.orm import with_loader_criteria

db=SQLAlchemy()
_agency_provision_hook_installed=False

def _agency_id():
    try:return getattr(g,'agency_id',None)
    except RuntimeError:return None

def _install_tenant_guards():
    from .models import TENANT_MODELS
    @event.listens_for(db.session,'do_orm_execute')
    def _scope(execute_state):
        aid=_agency_id()
        if not aid or not execute_state.is_select:return
        st=execute_state.statement
        for model in TENANT_MODELS:st=st.options(with_loader_criteria(model,lambda cls,aid=aid:cls.agency_id==aid,include_aliases=True))
        execute_state.statement=st
    @event.listens_for(db.session,'before_flush')
    def _protect(session,flush_context,instances):
        aid=_agency_id()
        if not aid:return
        for obj in session.new:
            if isinstance(obj,TENANT_MODELS):
                if getattr(obj,'agency_id',None) is None:obj.agency_id=aid
                elif obj.agency_id!=aid:raise ValueError('Cross-agency write blocked')
        for obj in session.dirty:
            if isinstance(obj,TENANT_MODELS) and getattr(obj,'agency_id',None) not in (None,aid):raise ValueError('Cross-agency write blocked')

def create_app():
    app=Flask(__name__,template_folder='../templates',static_folder='../static')
    app.config['SECRET_KEY']=os.getenv('SECRET_KEY','dev-change-me');app.config['JWT_SECRET_KEY']=os.getenv('JWT_SECRET_KEY','jwt-change-me')
    database_url=os.getenv('SUPABASE_DB_URL') or os.getenv('DATABASE_URL') or os.getenv('MYSQL_URL') or os.getenv('MYSQL_PUBLIC_URL') or 'sqlite:///recovery_ops_dev.db'
    if database_url.startswith('postgres://'):database_url=database_url.replace('postgres://','postgresql+psycopg://',1)
    elif database_url.startswith('postgresql://'):database_url=database_url.replace('postgresql://','postgresql+psycopg://',1)
    elif database_url.startswith('mysql://'):database_url=database_url.replace('mysql://','mysql+pymysql://',1)
    if database_url.startswith('postgresql+psycopg://') and 'supabase.co' in database_url and 'sslmode=' not in database_url:database_url += '&sslmode=require' if '?' in database_url else '?sslmode=require'
    app.config['SQLALCHEMY_DATABASE_URI']=database_url;app.config['SQLALCHEMY_TRACK_MODIFICATIONS']=False;app.config['SQLALCHEMY_ENGINE_OPTIONS']={'pool_pre_ping':True,'pool_recycle':280}
    if database_url.startswith('postgresql+psycopg://'):app.config['SQLALCHEMY_ENGINE_OPTIONS'].update({'pool_size':int(os.getenv('DB_POOL_SIZE','5')),'max_overflow':int(os.getenv('DB_MAX_OVERFLOW','10'))})
    if database_url.startswith('sqlite'):app.config['SQLALCHEMY_ENGINE_OPTIONS'].update({'connect_args':{'timeout':60,'check_same_thread':False}})
    app.config['MAX_CONTENT_LENGTH']=50*1024*1024;db.init_app(app);_install_tenant_guards()
    @app.errorhandler(413)
    def too_large(error):return {'error':'File size exceeds the 50 MB upload limit.','max_bytes':50*1024*1024},413
    CORS(app,origins=os.getenv('CORS_ORIGINS','*').split(','))
    from .models import User,Bank,Vehicle,DataAssignment,StatusHistory,Notification,SyncMutation,Client,RecoveryCase,AppFile,AgencyCompany,Challan,ChatConversation,ChatMessage,Report,UserOTP
    _install_agency_provision_hook(AgencyCompany)
    from .routes import api,web
    from .super_admin import super_admin
    from . import super_admin_extra,super_admin_control_v4,super_admin_routes_v5,super_admin_route_compat
    app.register_blueprint(api,url_prefix='/api');app.register_blueprint(web);app.register_blueprint(super_admin)
    from .tenant_security import install as install_tenant_security
    install_tenant_security(app);super_admin_control_v4.install(app);super_admin_routes_v5.install(app);super_admin_route_compat.install(app)
    with app.app_context():
        if database_url.startswith('sqlite'):db.create_all()
        ensure_schema();seed()
    return app

def ensure_schema():
    if db.engine.dialect.name!='postgresql':return
    try:
        agencies=db.session.execute(text('SELECT id,name,slug,phone,email,is_active FROM public.agencies ORDER BY id')).mappings().all()
        for a in agencies:
            c=db.session.execute(text('SELECT id FROM public.agency_company WHERE name=:n OR code=:s LIMIT 1'),{'n':a['name'],'s':a['slug']}).scalar()
            if not c:db.session.execute(text('INSERT INTO public.agency_company(name,code,phone,email,active,supabase_agency_id,created_at) VALUES(:n,:s,:p,:e,:x,:sid,now())'),{'n':a['name'],'s':a['slug'],'p':a['phone'],'e':a['email'],'x':bool(a['is_active']),'sid':str(a['id'])})
            else:db.session.execute(text('UPDATE public.agency_company SET phone=:p,email=:e,active=:x WHERE id=:id'),{'id':c,'p':a['phone'],'e':a['email'],'x':bool(a['is_active'])})
        db.session.execute(text('''UPDATE public."user" u SET agency_id=amap.legacy_id FROM (SELECT am.user_id,coalesce(c.id,am.agency_id) legacy_id FROM public.agency_members am LEFT JOIN public.agencies a ON a.id=am.agency_id LEFT JOIN public.agency_company c ON c.name=a.name OR c.code=a.slug WHERE am.is_active=true) amap WHERE u.id=amap.user_id AND u.agency_id IS DISTINCT FROM amap.legacy_id'''))
        db.session.commit()
    except Exception:db.session.rollback()

def _provision_tenant_schema(modern_agency_id, legacy_agency_id, conn=None):
    """Create one isolated business schema and copy the legacy table shapes into it."""
    if conn is None:
        conn=db.session.connection()
    if conn.dialect.name != 'postgresql':
        return None
    schema=f'recovery_agency_{int(modern_agency_id)}'
    tables=('bank','client','vehicle','recovery_case','app_file','data_assignment','status_history_legacy','notification','sync_mutation','challan','chat_conversation','chat_message','report')
    conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    for table in tables:
        conn.execute(text(f'CREATE TABLE IF NOT EXISTS "{schema}"."{table}" (LIKE public."{table}" INCLUDING ALL)'))
        fks=conn.execute(text('''SELECT conname FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE c.contype='f' AND n.nspname=:schema AND r.relname=:table'''),{'schema':schema,'table':table}).scalars().all()
        for conname in fks:
            safe=str(conname).replace('"','""')
            conn.execute(text(f'ALTER TABLE "{schema}"."{table}" DROP CONSTRAINT "{safe}"'))
    conn.execute(text('''INSERT INTO public.agency_tenants(agency_id,legacy_agency_id,schema_name,provisioned_at,schema_version) VALUES(:modern,:legacy,:schema,now(),1) ON CONFLICT (agency_id) DO UPDATE SET legacy_agency_id=EXCLUDED.legacy_agency_id,schema_name=EXCLUDED.schema_name,schema_version=1'''),{'modern':int(modern_agency_id),'legacy':int(legacy_agency_id),'schema':schema})
    return schema

def _install_agency_provision_hook(AgencyCompany):
    global _agency_provision_hook_installed
    if _agency_provision_hook_installed:
        return
    @event.listens_for(AgencyCompany,'after_insert')
    def _after_agency_company_insert(mapper,connection,target):
        if connection.dialect.name!='postgresql':
            return
        row=connection.execute(text('SELECT id FROM public.agencies WHERE name=:n OR slug=:s LIMIT 1'),{'n':target.name,'s':target.code}).scalar()
        if row is not None:
            _provision_tenant_schema(int(row),int(target.id),conn=connection)
    _agency_provision_hook_installed=True

def seed():
    # Production must remain control-plane-only until SUPER_ADMIN provisions an agency.
    # Never recreate the old Demo Recovery Agency or a default admin automatically.
    from .models import User
    from werkzeug.security import generate_password_hash
    su=(os.getenv('SUPER_ADMIN_USERNAME') or '').strip();se=(os.getenv('SUPER_ADMIN_EMAIL') or '').strip().lower() or None;sp=os.getenv('SUPER_ADMIN_PASSWORD') or ''
    if not su or not sp:
        return
    try:
        sa=User.query.filter(db.or_(User.username==su,User.email==se)).first()
        if not sa:
            db.session.add(User(agency_id=None,username=su,email=se,password_hash=generate_password_hash(sp,method='scrypt:32768:8:1'),full_name='RecoveryOps Super Admin',role='SUPER_ADMIN',active=True,is_deleted=False))
        else:
            sa.role='SUPER_ADMIN';sa.active=True;sa.is_deleted=False;sa.email=se or sa.email;sa.password_hash=generate_password_hash(sp,method='scrypt:32768:8:1');sa.agency_id=None
        db.session.commit()
    except Exception:
        db.session.rollback()