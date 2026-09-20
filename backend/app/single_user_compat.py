from functools import wraps
from flask import request
from werkzeug.security import generate_password_hash
from sqlalchemy import func

def install(app):
    from .models import User
    from . import db

    # Runtime compatibility for the single-user schema.
    User.role = property(
        lambda self: getattr(self, "_runtime_role", "ADMIN"),
        lambda self, value: setattr(self, "_runtime_role", value),
    )
    User.device_id = property(
        lambda self: getattr(self, "_runtime_device_id", None),
        lambda self, value: setattr(self, "_runtime_device_id", value),
    )
    User.agency_id = property(
        lambda self: getattr(self, "_runtime_agency_id", 1),
        lambda self, value: setattr(self, "_runtime_agency_id", value),
    )

    original = app.view_functions.get("api.login")
    if not original or getattr(original, "_single_user_bootstrap", False):
        return

    @wraps(original)
    def login_with_bootstrap(*args, **kwargs):
        # Only runs when the users table is completely empty.
        # The submitted password is hashed in-process and never logged.
        try:
            if User.query.with_entities(func.count(User.id)).scalar() == 0:
                data = request.get_json(silent=True) or {}
                identifier = str(
                    data.get("username")
                    or data.get("identifier")
                    or data.get("email")
                    or ""
                ).strip().lower()

                if identifier in {"dhairya", "dhairyanayak9@gmail.com"}:
                    password = str(data.get("password") or "")
                    if password:
                        user = User(
                            username="Dhairya",
                            password_hash=generate_password_hash(password),
                            full_name="Dhairya",
                            email="dhairyanayak9@gmail.com",
                            active=True,
                        )
                        user.role = "ADMIN"
                        user.agency_id = 1
                        db.session.add(user)
                        db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Single-user bootstrap failed")

        return original(*args, **kwargs)

    login_with_bootstrap._single_user_bootstrap = True
    app.view_functions["api.login"] = login_with_bootstrap
