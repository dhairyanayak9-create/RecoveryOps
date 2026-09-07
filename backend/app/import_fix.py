import csv
import io
import os
import re

from flask import g, current_app
from sqlalchemy import text

from . import db
from .models import AppFile, Vehicle, Notification, User
from .routes import get_registration_suffix


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


def _delete_previous_bank_uploads(bank_id, current_file_id):
    """Delete previous bank data inside the caller transaction; never commit here."""
    old_ids = [
        row[0]
        for row in db.session.execute(
            text("SELECT id FROM app_file WHERE bank_id = :bank_id AND id <> :current_file_id"),
            {"bank_id": bank_id, "current_file_id": current_file_id},
        ).all()
    ]
    if not old_ids:
        return

    placeholders = ", ".join(f":fid{i}" for i in range(len(old_ids)))
    params = {f"fid{i}": value for i, value in enumerate(old_ids)}
    vehicle_subquery = f"SELECT id FROM vehicle WHERE source_file_id IN ({placeholders})"

    for table in (
        "data_assignment",
        "status_history_legacy",
        "sync_mutation",
        "recovery_case",
        "challan",
    ):
        db.session.execute(
            text(f"DELETE FROM {table} WHERE vehicle_id IN ({vehicle_subquery})"),
            params,
        )

    db.session.execute(text(f"DELETE FROM vehicle WHERE source_file_id IN ({placeholders})"), params)
    db.session.execute(text(f"DELETE FROM app_file WHERE id IN ({placeholders})"), params)


def _load_existing_identifiers():
    """Load all identifiers with one DB round trip instead of four queries."""
    rows = db.session.execute(
        text(
            "SELECT registration_no, chassis_no, account_no, loan_no "
            "FROM vehicle "
            "WHERE registration_no IS NOT NULL OR chassis_no IS NOT NULL "
            "OR account_no IS NOT NULL OR loan_no IS NOT NULL"
        )
    ).all()
    regs, chassis, accounts, loans = set(), set(), set(), set()
    for reg, ch, account, loan in rows:
        if reg:
            regs.add(reg)
        if ch:
            chassis.add(ch)
        if account:
            accounts.add(account)
        if loan:
            loans.add(loan)
    return regs, chassis, accounts, loans


def _copy_insert_vehicle_rows(rows):
    """Insert prepared rows through PostgreSQL COPY using psycopg2/psycopg3."""
    if not rows:
        return

    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer, lineterminator="\n")
    columns = (
        "agency_id", "account_no", "loan_no", "registration_no", "registration_suffix",
        "chassis_no", "engine_no", "customer_name", "mobile", "city", "state", "pincode",
        "vehicle_type", "latitude", "longitude", "bank_id", "source_file_id",
    )
    for row in rows:
        writer.writerow([row.get(column) for column in columns])
    csv_text = csv_buffer.getvalue()

    connection = db.session.connection()
    raw = connection.connection
    try:
        if hasattr(raw, "cursor"):
            cursor = raw.cursor()
            try:
                if hasattr(cursor, "copy_expert"):
                    cursor.copy_expert(
                        "COPY vehicle (" + ", ".join(columns) + ") FROM STDIN WITH (FORMAT CSV)",
                        io.StringIO(csv_text),
                    )
                elif hasattr(cursor, "copy"):
                    with cursor.copy(
                        "COPY vehicle (" + ", ".join(columns) + ") FROM STDIN WITH (FORMAT CSV)"
                    ) as copy:
                        copy.write(csv_text)
                else:
                    raise RuntimeError("PostgreSQL driver does not expose COPY support")
            finally:
                cursor.close()
        else:
            raise RuntimeError("PostgreSQL connection does not expose a cursor")
    except Exception:
        db.session.rollback()
        raise


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
    """Memory-safe streaming importer with atomic bank replacement."""
    with app.app_context():
        g.agency_id = agency_id
        af = None
        try:
            schema = _resolve_schema(agency_id)
            expected_schema = f"recovery_agency_{int(agency_id)}"
            if schema != expected_schema:
                raise RuntimeError(
                    f"Agency tenant mismatch: agency_id={agency_id} schema={schema}"
                )
            g.tenant_schema = schema
            db.session.connection().exec_driver_sql(
                f'SET search_path TO "{schema}", public'
            )

            bank_row = db.session.execute(
                text(
                    "SELECT id, agency_id FROM bank "
                    "WHERE id=:bank_id AND agency_id=:agency_id "
                    "AND COALESCE(active,true)=true LIMIT 1"
                ),
                {"bank_id": int(bank_id), "agency_id": int(agency_id)},
            ).mappings().first()
            if not bank_row:
                raise RuntimeError(
                    f"Tenant bank validation failed: agency_id={agency_id}, bank_id={bank_id}"
                )

            af = db.session.get(AppFile, file_id)
            if not af:
                raise RuntimeError("Upload file record not found.")

            if int(getattr(af, "agency_id", agency_id) or agency_id) != int(agency_id):
                raise RuntimeError(
                    f"Upload file belongs to a different agency: file_id={file_id}"
                )
            if int(getattr(af, "bank_id", bank_id) or bank_id) != int(bank_id):
                raise RuntimeError(
                    f"Upload file bank mismatch: file_id={file_id}, bank_id={bank_id}"
                )

            # Everything below stays in one transaction. If parsing, COPY, or
            # validation fails, the old bank data is restored by rollback.
            _delete_previous_bank_uploads(bank_id, af.id)

            requested_vehicle_type = _string(vehicle_type)
            mapping = None
            created = 0
            dup = 0
            invalid = 0
            batch = []
            BATCH_SIZE = 10000

            existing_regs, existing_chassis, existing_accounts, existing_loans = _load_existing_identifiers()

            for row_mapping, row in _iter_rows(temp_path, filename):
                if mapping is None:
                    mapping = row_mapping
                    if not any(
                        k in mapping
                        for k in ("registration_no", "chassis_no", "account_no", "loan_no")
                    ):
                        raise ValueError(
                            "File must contain Registration Number, Chassis Number, Account Number, or Loan Number column."
                        )

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
                    _copy_insert_vehicle_rows(batch)
                    batch = []

            if batch:
                _copy_insert_vehicle_rows(batch)

            af = db.session.get(AppFile, file_id)
            if not af:
                raise RuntimeError("Current upload file disappeared during replacement.")
            af.record_count = created
            af.status = "IMPORTED"
            db.session.commit()

            recipients_q = User.query.filter_by(active=True, is_deleted=False)
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

            current_app.logger.info(
                "File import completed: bank=%s file=%s created=%s duplicates=%s invalid=%s replaced_previous=true method=COPY atomic=true",
                bank_id,
                file_id,
                created,
                dup,
                invalid,
            )

        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception("File import failed for agency=%s bank=%s file=%s", agency_id, bank_id, file_id)
            try:
                af = db.session.get(AppFile, file_id)
                if af:
                    af.status = "FAILED"
                    db.session.commit()
            except Exception:
                db.session.rollback()
            current_app.logger.error("IMPORT_ERROR agency=%s bank=%s file=%s error=%s", agency_id, bank_id, file_id, exc)
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            db.session.remove()


def install(app):
    """Install the high-throughput streaming import worker."""
    from . import routes

    if not getattr(routes._process_import_job, "_recoveryops_streaming", False):
        _process_import_job_streaming._recoveryops_streaming = True
        routes._process_import_job = _process_import_job_streaming
