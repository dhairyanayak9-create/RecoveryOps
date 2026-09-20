from functools import wraps
from flask import request
from werkzeug.security import generate_password_hash
from sqlalchemy import func

def install(app):
    from .models import User

    # Current single-user deployment keeps legacy model compatibility.
    # These properties prevent old agency/device fields from breaking auth.
    if not hasattr(User, "role"):
        User.role = property(
            lambda self: getattr(self, "_runtime_role", "ADMIN"),
            lambda self, value: setattr(self, "_runtime_role", value),
        )
    if not hasattr(User, "device_id"):
        User.device_id = property(
            lambda self: getattr(self, "_runtime_device_id", None),
            lambda self, value: setattr(self, "_runtime_device_id", value),
        )

    original = app.view_functions.get("api.login")
    if not original or getattr(original, "_single_user_bootstrap", False):
        return

    @wraps(original)
    def login_with_bootstrap(*args, **kwargs):
        # Bootstrap is available only while the users table is empty.
        # The submitted password is hashed inside the application and is
        # never stored in source code or logs.
        try:
            if User.query.with_entities(func.count(User.id)).scalar() == 0:
                data = request.get_json(silent=True) or {}
                identifier = str(
                    data.get("username")
                    or data.get("identifier")
                    or data.get("email")
                    or ""
                ).strip().lower()
                email = (str(data.get("email") or "").strip().lower()
                         or "dhairyanayak9@gmail.com")
                allowed_username = "dhairya"
                allowed_email = "dhairyanayak9@gmail.com"

                if identifier in {allowed_username, allowed_email}:
                    password = str(data.get("password") or "")
                    if password:
                        user = User(
                            username="Dhairya",
                            password_hash=generate_password_hash(password),
                            full_name="Dhairya",
                            email=email,
                            active=True,
                        )
                        db = User.query.session if hasattr(User.query, "session") else None
                        if db is None:
                            from . import db
                            db = db.session
                        db.add(user)
                        db.flush()
                        user.role = "ADMIN"
                        db.commit()
        except Exception:
            from . import db
            db.session.rollback()
            app.logger.exception("Single-user bootstrap failed")
        return original(*args, **kwargs)

    login_with_bootstrap._single_user_bootstrap = True
    app.view_functions["api.login"] = login_with_bootstrap
