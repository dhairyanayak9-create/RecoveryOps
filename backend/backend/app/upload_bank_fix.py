import inspect
from flask import g, jsonify
from sqlalchemy import text

from . import db
from .auth import current_user


def install(app):
    endpoint = "api.import_file"
    original = app.view_functions.get(endpoint)
    if not original or getattr(original, "_recoveryops_bank_fix", False):
        return

    core = inspect.unwrap(original)

    def fixed_import_file(*args, **kwargs):
        user = current_user()
        if not user or not user.active or getattr(user, "is_deleted", False):
            return jsonify({"error": "Authentication required"}), 401

        if user.role == "SUPER_ADMIN":
            return core(user, *args, **kwargs)

        if user.role not in {"ADMIN", "STAFF"}:
            return jsonify({"error": "Forbidden"}), 403

        try:
            row = db.session.execute(
                text(
                    "SELECT agency_id, schema_name, legacy_agency_id "
                    "FROM public.agency_tenants "
                    "WHERE legacy_agency_id=:aid OR agency_id=:aid LIMIT 1"
                ),
                {"aid": int(user.agency_id)},
            ).mappings().first()
            if not row:
                return jsonify({"error": "Agency data store is not provisioned"}), 503

            schema = str(row["schema_name"])
            legacy_id = int(row["legacy_agency_id"])
            modern_id = int(row["agency_id"])
            bank_id = request_bank_id = None
            try:
                request_bank_id = int(__import__("flask").request.form.get("bank_id"))
            except (TypeError, ValueError):
                request_bank_id = None

            if request_bank_id is None:
                return core(user, *args, **kwargs)

            bank = db.session.execute(
                text(
                    f'SELECT id, agency_id FROM "{schema}"."bank" '
                    "WHERE id=:bank_id AND agency_id IN (:legacy_id,:modern_id) LIMIT 1"
                ),
                {
                    "bank_id": request_bank_id,
                    "legacy_id": legacy_id,
                    "modern_id": modern_id,
                },
            ).mappings().first()

            if bank:
                # Legacy tenant tables use the legacy agency_company id.
                g.agency_id = int(bank["agency_id"])
                g.tenant_schema = schema

            return core(user, *args, **kwargs)
        except Exception:
            db.session.rollback()
            raise

    fixed_import_file._recoveryops_bank_fix = True
    app.view_functions[endpoint] = fixed_import_file
