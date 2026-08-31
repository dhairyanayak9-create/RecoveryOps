import os
from sqlalchemy import event
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS

db = SQLAlchemy()

def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-change-me")
    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "jwt-change-me")
    database_url = os.getenv("DATABASE_URL") or os.getenv("MYSQL_URL") or os.getenv("MYSQL_PUBLIC_URL") or "sqlite:///recovery_ops_dev.db"
    if database_url.startswith("mysql://"):
        database_url = database_url.replace("mysql://", "mysql+pymysql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 280}
    if database_url.startswith("sqlite"):
        app.config["SQLALCHEMY_ENGINE_OPTIONS"].update({
            "connect_args": {"timeout": 60, "check_same_thread": False}
        })
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
    db.init_app(app)

    # Register SQLite connection pragmas only after Flask-SQLAlchemy has been
    # initialized and an application context is active. Accessing db.engine
    # before that context exists causes: "Working outside of application context."
    if database_url.startswith("sqlite"):
        with app.app_context():
            engine = db.engine

            def _sqlite_pragmas(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                    cursor.execute("PRAGMA busy_timeout=60000")
                finally:
                    cursor.close()

            event.listen(engine, "connect", _sqlite_pragmas)
    @app.errorhandler(413)
    def request_entity_too_large(error):
        return {"error": "File size exceeds the 50 MB upload limit.", "max_bytes": 50 * 1024 * 1024}, 413
    CORS(app, origins=os.getenv("CORS_ORIGINS", "*").split(","))

    from .models import User, Bank, Vehicle, DataAssignment, StatusHistory, Notification, SyncMutation, Client, RecoveryCase, AppFile, AgencyCompany, Challan, ChatConversation, ChatMessage, Report, UserOTP
    from .routes import api, web
    app.register_blueprint(api, url_prefix="/api")
    app.register_blueprint(web)

    with app.app_context():
        db.create_all()
        ensure_schema()
        seed()

    return app

def ensure_schema():
    """Small forward-only migration for existing SQLite/MySQL databases."""
    from sqlalchemy import inspect, text
    inspector=inspect(db.engine)
    cols={c["name"] for c in inspector.get_columns("vehicle")} if inspector.has_table("vehicle") else set()
    if "registration_suffix" not in cols:
        db.session.execute(text("ALTER TABLE vehicle ADD COLUMN registration_suffix VARCHAR(4)"))
        db.session.commit()
    try:
        db.session.execute(text("CREATE INDEX ix_vehicle_registration_suffix ON vehicle (registration_suffix)"))
        db.session.commit()
    except Exception:
        db.session.rollback()
    # Backfill only rows that are missing the indexed suffix.
    rows=db.session.execute(text("SELECT id, registration_no FROM vehicle WHERE registration_suffix IS NULL OR registration_suffix='' LIMIT 5000")).fetchall()
    if rows:
        from .models import Vehicle
        import re
        for row in rows:
            digits=re.sub(r"\D", "", row.registration_no or "")
            Vehicle.query.filter_by(id=row.id).update({"registration_suffix": digits[-4:] if len(digits)>=4 else None}, synchronize_session=False)
        db.session.commit()


def seed():
    from .models import User, Bank
    from werkzeug.security import generate_password_hash
    if not User.query.filter_by(username="admin").first():
        db.session.add(User(
            username="admin",
            password_hash=generate_password_hash("admin123"),
            full_name="System Administrator",
            role="ADMIN",
            active=True
        ))
    if not Bank.query.first():
        db.session.add_all([
            Bank(name="Demo Bank", code="DEMO", active=True),
            Bank(name="Sample Finance", code="SAMPLE", active=True)
        ])
    db.session.commit()
