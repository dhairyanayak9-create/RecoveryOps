import inspect
from flask import g, jsonify, request
from sqlalchemy import text

from . import db
from .auth import current_user


def install(app):
    endpoint = "api.import_file"
    original = app.view_functions.get(endpoint)
    if not original or getattr(original, "_recoveryops_bank_fix", False):
        return

    # app.view_functions contains the auth/role decorator wrapper. Calling that
    # wrapper directly with an already-resolved user passes the user twice and
    # causes a TypeError -> HTTP 500. Unwrap to the actual import_file function.
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
            aid = int(user.agency_id)
            row = db.session.execute(
                text(
                    "SELECT agency_id, schema_name "
                    "FROM public.agency_tenants "
                    "WHERE agency_id=:aid LIMIT 1"
                ),
                {"aid": aid},
            ).mappings().first()
            schema = str(row["schema_name"]) if row else f"recovery_agency_{aid}"
            expected = f"recovery_agency_{aid}"
            if schema != expected:
                return jsonify({"error": "Agency data store is not provisioned"}), 503

            raw_bank_id = request.form.get("bank_id")
            try:
                requested_bank_id = int(raw_bank_id)
            except (TypeError, ValueError):
                requested_bank_id = None

            if requested_bank_id is None:
                return jsonify({"error": "Invalid bank_id"}), 400

            # Resolve the bank strictly inside the logged-in agency tenant.
            # Accept both the real PK and the legacy bank code used by older clients.
            bank = db.session.execute(
                text(
                    f'''SELECT id, agency_id, name, code FROM "{schema}"."bank"
                        WHERE agency_id=:aid
                          AND (id=:bank_id OR code=:bank_code)
                          AND COALESCE(active,true)=true
                        ORDER BY CASE WHEN id=:bank_id THEN 0 ELSE 1 END
                        LIMIT 1'''
                ),
                {
                    "aid": aid,
                    "bank_id": requested_bank_id,
                    "bank_code": str(requested_bank_id),
                },
            ).mappings().first()

            # If an older/cached browser submitted a stale bank PK, do not cross
            # agencies. Only fall back when this agency has exactly one active bank.
            # This preserves tenant isolation while keeping existing single-bank
            # agency clients compatible.
            if not bank:
                active_banks = db.session.execute(
                    text(
                        f'''SELECT id, agency_id, name, code FROM "{schema}"."bank"
                            WHERE agency_id=:aid AND COALESCE(active,true)=true
                            ORDER BY id'''
                    ),
                    {"aid": aid},
                ).mappings().all()
                if len(active_banks) == 1:
                    bank = active_banks[0]

            if not bank or int(bank["agency_id"]) != aid:
                app.logger.warning(
                    "Bank resolution failed: agency=%s requested_bank_id=%s schema=%s",
                    aid, requested_bank_id, schema,
                )
                return jsonify({
                    "error": "Selected bank does not exist in your agency."
                }), 404

            canonical_bank_id = int(bank["id"])

            # The core importer reads request.form again. Replace only bank_id with
            # the tenant-local canonical primary key resolved above.
            form = request.form.copy()
            form["bank_id"] = str(canonical_bank_id)
            request.form = form

            g.agency_id = aid
            g.tenant_schema = schema
            db.session.connection().exec_driver_sql(
                f'SET search_path TO "{schema}", public'
            )

            return core(user, *args, **kwargs)
        except Exception:
            db.session.rollback()
            raise

    fixed_import_file._recoveryops_bank_fix = True
    app.view_functions[endpoint] = fixed_import_file
