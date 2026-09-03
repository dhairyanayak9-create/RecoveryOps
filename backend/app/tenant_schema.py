from sqlalchemy import text
from . import db

TENANT_PREFIX = "recovery_agency_"


def schema_for_legacy_agency(legacy_agency_id):
    if legacy_agency_id is None:
        return None
    value = int(legacy_agency_id)
    row = db.session.execute(
        text("SELECT schema_name FROM public.agency_tenants WHERE legacy_agency_id=:id LIMIT 1"),
        {"id": value},
    ).scalar()
    if row and str(row).startswith(TENANT_PREFIX):
        return str(row)
    return None


def set_tenant_schema(legacy_agency_id):
    schema = schema_for_legacy_agency(legacy_agency_id)
    if not schema:
        db.session.execute(text("SET search_path TO public"))
        return None
    db.session.execute(text("SET search_path TO public"))
    db.session.execute(text("SET search_path TO %s, public" % schema))
    return schema


def reset_tenant_schema():
    try:
        db.session.execute(text("SET search_path TO public"))
    except Exception:
        db.session.rollback()
