import os
import re
import pandas as pd
from flask import g, appcontext_pushed
from sqlalchemy import text
from contextvars import ContextVar

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


def _normalize_headers(temp_path):
    """Make common bank-export headers match the legacy importer aliases."""
    name = str(temp_path).lower()
    if name.endswith(".csv"):
        df = pd.read_csv(temp_path)
        writer = "csv"
    else:
        df = pd.read_excel(temp_path)
        writer = "excel"

    rename = {}
    aliases = {
        "loan no": "loan number",
        "loan number": "loan number",
        "customer name": "customer full name",
        "registration number": "registration number",
        "engine number": "engine number",
        "chassis number": "chassis number",
    }
    for col in df.columns:
        key = str(col).strip().lower().replace("_", " ")
        if key in aliases and str(col) != aliases[key]:
            rename[col] = aliases[key]
    if rename:
        df = df.rename(columns=rename)
        if writer == "csv":
            df.to_csv(temp_path, index=False)
        else:
            df.to_excel(temp_path, index=False)


def install(app):
    """Patch the legacy import worker without blocking the HTTP request."""
    from . import routes

    worker_agency = ContextVar("recoveryops_import_agency", default=None)

    @appcontext_pushed.connect_via(app)
    def _seed_import_context(sender, **extra):
        aid = worker_agency.get()
        if aid is None:
            return
        g.agency_id = aid
        try:
            schema = _resolve_schema(aid)
            g.tenant_schema = schema
            db.session.connection().exec_driver_sql(
                f'SET search_path TO "{schema}", public'
            )
        except Exception:
            # The worker will surface the actual import error.
            pass

    original_worker = routes._process_import_job
    if not getattr(original_worker, "_recoveryops_fixed", False):
        def fixed_worker(*args, **kwargs):
            agency_id = kwargs.get("agency_id")
            if agency_id is None and len(args) >= 8:
                agency_id = args[7]
            temp_path = kwargs.get("temp_path")
            if temp_path is None and len(args) >= 3:
                temp_path = args[2]

            token = worker_agency.set(agency_id)
            try:
                with app.app_context():
                    g.agency_id = agency_id
                    schema = _resolve_schema(agency_id)
                    g.tenant_schema = schema
                    db.session.connection().exec_driver_sql(
                        f'SET search_path TO "{schema}", public'
                    )
                    if temp_path:
                        _normalize_headers(temp_path)
                # original_worker creates its own app context; the signal above
                # restores the same tenant context inside that nested context.
                return original_worker(*args, **kwargs)
            finally:
                worker_agency.reset(token)
        fixed_worker._recoveryops_fixed = True
        routes._process_import_job = fixed_worker

    # IMPORTANT: keep the existing /api/files/import endpoint asynchronous.
    # The frontend already polls /api/files/<id>. Waiting inside the HTTP
    # request for a large Excel import can hit Railway's edge/proxy timeout
    # and surface as HTTP 502 even while the import worker is still healthy.
