import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from sqlalchemy import event, inspect, text
from sqlalchemy.orm import with_loader_criteria
from flask import g

db = SQLAlchemy()

def _agency_id():
    try: return getattr(g, "agency_id", None)
    except RuntimeError: return None

def _install_tenant_guards():
    from .models import TENANT_MODELS
    @event.listens_for(db.session, "do_orm_execute")
    def _scope_tenant_queries(execute_state):
        agency_id=_agency_id()
        if not agency_id or not execute_state.is_select:return
        statement=execute_state.statement
        for model in TENANT_MODELS:
            statement=statement.options(with_loader_criteria(model,lambda cls,agency_id=agency_id:cls.agency_id==agency_id,include_aliases=True))
        execute_state.statement=statement
    @event.listens_for(db.session, "before_flush")
    def _protect_tenant_writes(session,flush_context,instances):
        agency_id=_agency_id()
        if not agency_id:return
        for obj in session.new:
            if isinstance(obj,TENANT_MODELS):
                if getattr(obj,"agency_id",None) is None:obj.agency_id=agency_id
                elif obj.agency_id!=agency_id:raise ValueError("Cross-agency write blocked")
        for obj in session.dirty:
            if isinstance(obj,TENANT_MODELS):
                obj_agency=getattr(obj,"agency_id",None)
                if obj_agency is not None and obj_agency!=agency_id:raise ValueError("Cross-agency write blocked")

def create_app():
    app=Flask(__name__,template_folder="../templates",static_folder="../static")
    app.config["SECRET_KEY"]=os.getenv("SECRET_KEY","dev-change-me");app.config["JWT_SECRET_KEY"]=os.getenv("JWT_SECRET_KEY","jwt-change-me")
    database_url=os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL") or os.getenv("MYSQL_URL") or os.getenv("MYSQL_PUBLIC_URL") or "sqlite:///recovery_ops_dev.db"
    if database_url.startswith("postgres://"):database_url=database_url.replace("postgres://","postgresql+psycopg://",1)
    elif database_url.startswith("postgresql://"):database_url=database_url.replace("postgresql://","postgresql+psycopg://",1)
    elif database_url.startswith("mysql://"):database_url=database_url.replace("mysql://","mysql+pymysql://",1)
    if database_url.startswith("postgresql+psycopg://") and "supabase.co" in database_url and "sslmode=" not in database_url:database_url += "&sslmode=require" if "?" in database_url else "?sslmode=require"
    app.config["SQLALCHEMY_DATABASE_URI"]=database_url;app.config["SQLALCHEMY_TRACK_MODIFICATIONS"]=False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"]={"pool_pre_ping":True,"pool_recycle":280}
    if database_url.startswith("postgresql+psycopg://"):app.config["SQLALCHEMY_ENGINE_OPTIONS"].update({"pool_size":int(os.getenv("DB_POOL_SIZE","5")),"max_overflow":int(os.getenv("DB_MAX_OVERFLOW","10"))})
    if database_url.startswith("sqlite"):app.config["SQLALCHEMY_ENGINE_OPTIONS"].update({"connect_args":{"timeout":60,"check_same_thread":False}})
    app.config["MAX_CONTENT_LENGTH"]=50*1024*1024;db.init_app(app);_install_tenant_guards()
    CORS(app,origins=os.getenv("CORS_ORIGINS","*").split(","))
    from .models import User,Bank,Vehicle,DataAssignment,StatusHistory,Notification,SyncMutation,Client,RecoveryCase,AppFile,AgencyCompany,Challan,ChatConversation,ChatMessage,Report,UserOTP
    from .routes import api,web
    from .super_admin import super_admin
    from . import super_admin_extra,super_admin_control_v4,super_admin_routes_v5
    app.register_blueprint(api,url_prefix="/api");app.register_blueprint(web);app.register_blueprint(super_admin)
    from .tenant_security import install as install_tenant_security
    install_tenant_security(app);super_admin_control_v4.install(app);super_admin_routes_v5.install(app)
    with app.app_context():
        if database_url.startswith("sqlite"):db.create_all()
        ensure_schema();seed()
    return app

def ensure_schema():
    from .models import User,AgencyCompany
    inspector=inspect(db.engine)
    if inspector.has_table("vehicle"):
        cols={c["name"] for c in inspector.get_columns("vehicle")}
        if "registration_suffix" not in cols:db.session.execute(text("ALTER TABLE vehicle ADD COLUMN registration_suffix VARCHAR(4)"));db.session.commit()
        try:db.session.execute(text("CREATE INDEX ix_vehicle_registration_suffix ON vehicle (registration_suffix)"));db.session.commit()
        except Exception:db.session.rollback()
    tenant_tables=["user","user_otp","bank","vehicle","data_assignment","status_history_legacy","notification","sync_mutation","client","recovery_case","app_file","challan","chat_conversation","chat_message","report"]
    for table in tenant_tables:
        if not inspect(db.engine).has_table(table):continue
        cols={c["name"] for c in inspect(db.engine).get_columns(table)}
        if "agency_id" not in cols:db.session.execute(text(f'ALTER TABLE "{table}" ADD COLUMN agency_id INTEGER'));db.session.commit()
        try:db.session.execute(text(f"CREATE INDEX ix_{table}_agency_id ON {table} (agency_id)"));db.session.commit()
        except Exception:db.session.rollback()
    if inspect(db.engine).has_table("agency_company"):
        cols={c["name"] for c in inspect(db.engine).get_columns("agency_company")}
        if "supabase_agency_id" not in cols:db.session.execute(text("ALTER TABLE agency_company ADD COLUMN supabase_agency_id VARCHAR(36)"));db.session.commit()
        try:db.session.execute(text("CREATE UNIQUE INDEX ix_agency_company_supabase_agency_id ON agency_company (supabase_agency_id)"));db.session.commit()
        except Exception:db.session.rollback()
    try:modern_agencies=db.session.execute(text("SELECT id,name,slug,phone,email,is_active FROM public.agencies ORDER BY created_at")).mappings().all()
    except Exception:modern_agencies=[]
    for ma in modern_agencies:
        agency=AgencyCompany.query.filter(db.or_(AgencyCompany.name==ma["name"],AgencyCompany.code==ma["slug"])).first()
        if not agency:db.session.add(AgencyCompany(name=ma["name"],code=ma["slug"],phone=ma["phone"],email=ma["email"],active=bool(ma["is_active"])))
        else:agency.phone=ma["phone"];agency.email=ma["email"];agency.active=bool(ma["is_active"])
    db.session.commit()
    if inspect(db.engine).has_table("user"):
        cols={c["name"] for c in inspect(db.engine).get_columns("user")}
        if "supabase_user_id" not in cols:db.session.execute(text('ALTER TABLE "user" ADD COLUMN supabase_user_id VARCHAR(36)'));db.session.commit()
        try:db.session.execute(text('CREATE UNIQUE INDEX ix_user_supabase_user_id ON "user" (supabase_user_id)'));db.session.commit()
        except Exception:db.session.rollback()
    try:mappings=db.session.execute(text("""SELECT u.id legacy_user_id,u.supabase_user_id::text auth_user_id,am.role member_role,am.agency_id modern_agency_id,ag.name agency_name,ag.slug agency_slug FROM public."user" u JOIN public.agency_members am ON am.user_id=u.id JOIN public.agencies ag ON ag.id=am.agency_id AND ag.is_active=true WHERE u.supabase_user_id IS NOT NULL AND u.active=true AND u.is_deleted=false AND am.is_active=true""")).mappings().all()
    except Exception:mappings=[]
    for m in mappings:
        u=User.query.get(m["legacy_user_id"]);agency=AgencyCompany.query.filter(db.or_(AgencyCompany.name==m["agency_name"],AgencyCompany.code==m["agency_slug"])).first()
        if u and agency:
            u.supabase_user_id=m["auth_user_id"];u.agency_id=agency.id;role=(m["member_role"] or "").lower();u.role="ADMIN" if role=="admin" else ("STAFF" if role=="staff" else ("REPO_AGENT" if role in {"agent","repo_agent","recovery_agent"} else u.role))
    db.session.commit()

def seed():
    from .models import User,AgencyCompany
    from werkzeug.security import generate_password_hash
    agency=AgencyCompany.query.order_by(AgencyCompany.id.asc()).first()
    if not agency:agency=AgencyCompany(name="Demo Recovery Agency",code="DEMO");db.session.add(agency);db.session.flush()
    admin=User.query.filter_by(username="admin").first()
    if not admin:db.session.add(User(agency_id=agency.id,username="admin",password_hash=generate_password_hash("admin123"),full_name="System Administrator",role="ADMIN",active=True))
    elif admin.agency_id is None:admin.agency_id=agency.id
    su=(os.getenv("SUPER_ADMIN_USERNAME") or "").strip();se=(os.getenv("SUPER_ADMIN_EMAIL") or "").strip().lower() or None;sp=os.getenv("SUPER_ADMIN_PASSWORD") or ""
    if su and sp:
        sa=User.query.filter(db.or_(User.username==su,User.email==se)).first()
        if not sa:db.session.add(User(agency_id=None,username=su,email=se,password_hash=generate_password_hash(sp,method="scrypt:32768:8:1"),full_name="RecoveryOps Super Admin",role="SUPER_ADMIN",active=True,is_deleted=False))
        else:sa.role="SUPER_ADMIN";sa.active=True;sa.is_deleted=False;sa.email=se or sa.email;sa.password_hash=generate_password_hash(sp,method="scrypt:32768:8:1")
    db.session.commit()
