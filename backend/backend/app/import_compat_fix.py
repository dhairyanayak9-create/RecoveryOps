"""Compatibility bridge for the tenant importer.

Keeps the existing streaming importer intact while normalizing the final
1:1 agency -> tenant routing and supporting both legacy integer and newer UUID
vehicle references during replacement cleanup.
"""
import re
from sqlalchemy import text


def install():
    from . import import_fix
    from . import db

    def resolve_schema(agency_id):
        if agency_id is None:
            raise RuntimeError("Import worker has no agency_id")
        schema = db.session.execute(
            text("SELECT schema_name FROM public.agency_tenants WHERE agency_id=:aid LIMIT 1"),
            {"aid": int(agency_id)},
        ).scalar()
        if not schema or not re.fullmatch(r"recovery_agency_[0-9]+", str(schema)):
            raise RuntimeError(f"Tenant schema not found for agency_id={agency_id}")
        return str(schema)

    original_mapping = import_fix._column_mapping

    def column_mapping(headers):
        mapping = original_mapping(headers)
        normalized = {
            re.sub(r"\s+", " ", str(h or "").strip().lower().replace("_", " ")): h
            for h in headers if h is not None
        }
        aliases = {
            "loan_no": ("loan no", "loan number", "loan", "loan id", "agreement no", "agreement number"),
            "account_no": ("account no", "account number", "account", "acct no", "acct number"),
            "registration_no": ("registration no", "registration number", "reg no", "vehicle no", "registration"),
            "chassis_no": ("chassis no", "chassis number", "chassis"),
            "engine_no": ("engine no", "engine number", "engine"),
        }
        for target, opts in aliases.items():
            if target in mapping:
                continue
            for opt in opts:
                if opt in normalized:
                    mapping[target] = normalized[opt]
                    break
        return mapping

    def delete_previous_bank_uploads(bank_id, current_file_id):
        old_ids = [
            row[0]
            for row in db.session.execute(
                text("SELECT id FROM app_file WHERE bank_id=:bank_id AND id<>:current_file_id"),
                {"bank_id": bank_id, "current_file_id": current_file_id},
            ).all()
        ]
        if not old_ids:
            return
        placeholders = ", ".join(f":fid{i}" for i in range(len(old_ids)))
        params = {f"fid{i}": value for i, value in enumerate(old_ids)}
        vehicle_subquery = f"SELECT id::text FROM vehicle WHERE source_file_id IN ({placeholders})"
        for table in ("data_assignment", "status_history_legacy", "sync_mutation", "recovery_case", "challan"):
            db.session.execute(
                text(f"DELETE FROM {table} WHERE vehicle_id::text IN ({vehicle_subquery})"),
                params,
            )
        db.session.execute(text(f"DELETE FROM vehicle WHERE source_file_id IN ({placeholders})"), params)
        db.session.execute(text(f"DELETE FROM app_file WHERE id IN ({placeholders})"), params)
        db.session.commit()

    import_fix._resolve_schema = resolve_schema
    import_fix._column_mapping = column_mapping
    import_fix._delete_previous_bank_uploads = delete_previous_bank_uploads
