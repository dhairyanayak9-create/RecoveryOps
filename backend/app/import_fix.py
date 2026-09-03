import csv
import os
import re
from datetime import datetime, timezone

from flask import g
from sqlalchemy import text

from . import db


def _resolve_schema(agency_id):
    if agency_id is None:
        raise RuntimeError("Import worker has no agency_id")
    schema = db.session.execute(
        text(
            "SELECT schema_name FROM public.agency_tenants "
            "WHERE legacy_agency_id=:aid OR agency_id=:aid LIMIT 1"
        ),
        {"aid": int(agency_id)},
    ).scalar()
    if not schema or not re.fullmatch(r"recovery_agency_[0-9]+", str(schema)):
        raise RuntimeError(f"Tenant schema not found for agency_id={agency_id}")
    return str(schema)


def _clean_header(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower().replace("_", " "))


def _string(value):
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    value = str(value).strip()
    return value or None


def _number(value):
    value = _string(value)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _column_mapping(headers):
    aliases = {
        "account_no": ["account no", "account number", "account", "acct no", "acct_no"],
        "loan_no": ["loan no", "loan number", "loan"],
        "registration_no": ["registration no", "registration number", "reg no", "vehicle no", "registration"],
        "chassis_no": ["chassis no", "chassis number", "chassis"],
        "engine_no": ["engine no", "engine number", "engine"],
        "customer_name": ["customer name", "customer full name", "name"],
        "mobile": ["mobile", "mobile no", "phone"],
        "city": ["city"],
        "state": ["state", "state name"],
        "pincode": ["pincode", "pin", "pin code"],
        "vehicle_type": ["vehicle type", "vehicle_type", "type"],
        "latitude": ["latitude", "lat"],
        "longitude": ["longitude", "lng", "lon"],
    }
    normalized = {_clean_header(h): h for h in headers}
    mapping = {}
    for target, opts in aliases.items():
        for opt in opts:
            key = _clean_header(opt)
            if key in normalized:
                mapping[target] = normalized[key]
                break
    return mapping


def _iter_rows(temp_path, filename):
    name = str(filename or temp_path).lower()
    if name.endswith(".csv"):
        with open(temp_path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            headers = reader.fieldnames or []
            mapping = _column_mapping(headers)
            for row in reader:
                yield mapping, row
        return

    from openpyxl import load_workbook

    wb = load_workbook(temp_path, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        try:
            headers = list(next(rows))
        except StopIteration:
            return
        mapping = _column_mapping(headers)
        for values in rows:
            yield mapping, dict(zip(headers, values))
    finally:
        wb.close()


def _row_value(row, mapping, key):
    col = mapping.get(key)
    return _string(row.get(col)) if col is not None else None


def _row_number(row, mapping, key):
    col = mapping.get(key)
    return _number(row.get(col)) if col is not None else None


def _process_import_job_streaming(
    app,
    file_id,
    temp_path,
    bank_id,
    filename,
    vehicle_type,
    uploaded_by,
    agency_id,
):
    """Memory-safe replacement for the legacy pandas import worker."""
    with app.app_context():
        g.agency_id = agency_id
        af = None
        try:
            schema = _resolve_schema(agency_id)
            g.tenant_schema = schema
            db.session.connection().exec_driver_sql(
                f'SET search_path TO "{schema}", public'
            )

            af = db.session.get(__import__(".models", fromlist=["AppFile"]).AppFile, file_id)
            if not af:
                return

            first_mapping = None
            try:
                first_mapping, _ = next(_iter_rows(temp_path, filename))
            except StopIteration:
                raise ValueError("File is empty or contains no data rows.")

            if not any(k in first_mapping for k in ("registration_no", "chassis_no", "account_no", "loan_no")):
                raise ValueError(
                    "File must contain Registration Number, Chassis Number, Account Number, or Loan Number column."
                )

            from .models import AppFile, Vehicle, DataAssignment, StatusHistory, SyncMutation, RecoveryCase, Challan, Notification, User
            from .routes import get_registration_suffix

            old_files = AppFile.query.filter(
                AppFile.bank_id == bank_id,
                AppFile.agency_id == agency_id,
            ).all()

            for old_file in old_files:
                params = {"file_id": old_file.id}
                for table in ("status_history_legacy", "sync_mutation", "recovery_case", "challan"):
                    db.session.execute(
                        text(
                            f"DELETE FROM {table} WHERE vehicle_id IN ("
                            "SELECT id FROM vehicle WHERE source_file_id = :file_id)"
                        ),
                        params,
                    )
                db.session.execute(
                    text("DELETE FROM vehicle WHERE source_file_id = :file_id"),
                    params,
                )
                if old_file.id != af.id:
                    db.session.delete(old_file)
            db.session.commit()

            existing_regs = {
                x[0] for x in Vehicle.query.with_entities(Vehicle.registration_no).filter(Vehicle.registration_no.isnot(None)).all()
            }
            existing_chassis = {
                x[0] for x in Vehicle.query.with_entities(Vehicle.chassis_no).filter(Vehicle.chassis_no.isnot(None)).all()
            }
            existing_accounts = {
                x[0] for x in Vehicle.query.with_entities(Vehicle.account_no).filter(Vehicle.account_no.isnot(None)).all()
            }
            existing_loans = {
                x[0] for x in Vehicle.query.with_entities(Vehicle.loan_no).filter(Vehicle.loan_no.isnot(None)).all()
            }

            created = 0
            dup = 0
            invalid = 0
            batch = []
            requested_vehicle_type = _string(vehicle_type)
            BATCH_SIZE = 1000

            for mapping, row in _iter_rows(temp_path, filename):
                reg = _row_value(row, mapping, "registration_no")
                chassis = _row_value(row, mapping, "chassis_no")
                account = _row_value(row, mapping, "account_no")
                loan = _row_value(row, mapping, "loan_no")

                if not any((reg, chassis, account, loan)):
                    invalid += 1
                    continue

                if (
                    (reg and reg in existing_regs)
                    or (chassis and chassis in existing_chassis)
                    or (account and account in existing_accounts)
                    or (loan and loan in existing_loans)
                ):
                    dup += 1
                    continue

                batch.append({
                    "agency_id": agency_id,
                    "account_no": account,
                    "loan_no": loan,
                    "registration_no": reg,
                    "registration_suffix": get_registration_suffix(reg),
                    "chassis_no": (chassis or "")[:150],
                    "engine_no": (_row_value(row, mapping, "engine_no") or "")[:150],
                    "customer_name": _row_value(row, mapping, "customer_name"),
                    "mobile": _row_value(row, mapping, "mobile"),
                    "city": _row_value(row, mapping, "city"),
                    "state": _row_value(row, mapping, "state"),
                    "pincode": _row_value(row, mapping, "pincode"),
                    "vehicle_type": _row_value(row, mapping, "vehicle_type") or requested_vehicle_type,
                    "latitude": _row_number(row, mapping, "latitude"),
                    "longitude": _row_number(row, mapping, "longitude"),
                    "bank_id": bank_id,
                    "source_file_id": af.id,
                })

                if reg:
                    existing_regs.add(reg)
                if chassis:
                    existing_chassis.add(chassis)
                if account:
                    existing_accounts.add(account)
                if loan:
                    existing_loans.add(loan)
                created += 1

                if len(batch) >= BATCH_SIZE:
                    db.session.bulk_insert_mappings(Vehicle, batch)
                    db.session.commit()
                    batch = []
                    af = db.session.get(AppFile, file_id)
                    if af:
                        af.record_count = created
                        db.session.commit()

            if batch:
                db.session.bulk_insert_mappings(Vehicle, batch)
                db.session.commit()

            af = db.session.get(AppFile, file_id)
            if af:
                af.record_count = created
                af.status = "IMPORTED"
                db.session.commit()

                recipients_q = User.query.filter_by(active=True, is_deleted=False)
                if agency_id is not None:
                    recipients_q = recipients_q.filter(User.agency_id == agency_id)
                recipients = recipients_q.with_entities(User.id).all()
                if recipients:
                    db.session.bulk_insert_mappings(
                        Notification,
                        [
                            {
                                "agency_id": agency_id,
                                "user_id": rid,
                                "title": "New file uploaded",
                                "message": f"{filename} uploaded successfully with {created:,} records. Existing bank file replaced.",
                                "type": "UPLOAD",
                            }
                            for (rid,) in recipients
                        ],
                    )
                    db.session.commit()

        except Exception:
            db.session.rollback()
            current_app = __import__("flask", fromlist=["current_app"]).current_app
            current_app.logger.exception("File import failed for bank %s", bank_id)
            try:
                from .models import AppFile
                af = db.session.get(AppFile, file_id)
                if af:
                    af.status = "FAILED"
                    db.session.commit()
            except Exception:
                db.session.rollback()
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            db.session.remove()


def install(app):
    """Install a streaming import worker; keep the HTTP endpoint asynchronous."""
    from . import routes

    if not getattr(routes._process_import_job, "_recoveryops_streaming", False):
        _process_import_job_streaming._recoveryops_streaming = True
        routes._process_import_job = _process_import_job_streaming
