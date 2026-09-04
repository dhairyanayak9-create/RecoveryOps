import os
import threading
import time
import re

from flask import jsonify
from sqlalchemy import text

from . import db
from .auth import current_user

UPLOAD_SUFFIXES = (".xlsx", ".xls", ".csv")
TEMP_MAX_AGE = int(os.getenv("RECOVERYOPS_TEMP_MAX_AGE", "1800"))
CLEANUP_INTERVAL = int(os.getenv("RECOVERYOPS_TEMP_CLEANUP_INTERVAL", "600"))


def _super_admin():
    user = current_user()
    return user if user and user.active and not getattr(user, "is_deleted", False) and user.role == "SUPER_ADMIN" else None


def _schema_map():
    return db.session.execute(text("""
        SELECT a.id AS modern_id,
               coalesce(c.id, a.id) AS legacy_id,
               t.schema_name
        FROM public.agencies a
        LEFT JOIN public.agency_company c ON c.name=a.name OR c.code=a.slug
        JOIN public.agency_tenants t ON t.agency_id=a.id
        WHERE t.schema_name ~ '^recovery_agency_[0-9]+$'
    """)).mappings().all()


def _delete_file(schema, legacy_id, file_id):
    p = {"f": file_id, "a": legacy_id}
    found = db.session.execute(
        text(f'SELECT id, filename FROM "{schema}"."app_file" WHERE id=:f AND agency_id=:a'), p
    ).mappings().first()
    if not found:
        return None

    # Super Admin force-delete: remove every known vehicle dependency first.
    for table in ("data_assignment", "status_history_legacy", "sync_mutation", "recovery_case", "challan"):
        db.session.execute(text(f'''
            DELETE FROM "{schema}"."{table}"
            WHERE vehicle_id IN (
                SELECT id FROM "{schema}"."vehicle"
                WHERE source_file_id=:f AND agency_id=:a
            )
        '''), p)

    db.session.execute(text(f'DELETE FROM "{schema}"."vehicle" WHERE source_file_id=:f AND agency_id=:a'), p)
    db.session.execute(text(f'DELETE FROM "{schema}"."app_file" WHERE id=:f AND agency_id=:a'), p)
    db.session.commit()
    return found


def force_file_delete(file_id, agency_id=None):
    if not _super_admin():
        return jsonify(error="Super Admin access required"), 403
    try:
        for item in _schema_map():
            if agency_id is not None and int(item["modern_id"]) != int(agency_id):
                continue
            result = _delete_file(str(item["schema_name"]), int(item["legacy_id"]), file_id)
            if result:
                return jsonify(message="File permanently deleted by Super Admin", id=file_id, filename=result["filename"])
        return jsonify(error="File not found"), 404
    except Exception as exc:
        db.session.rollback()
        return jsonify(error=str(exc)), 400


def _cleanup_temp_files():
    now = time.time()
    removed = 0
    try:
        for name in os.listdir("/tmp"):
            path = os.path.join("/tmp", name)
            try:
                if not os.path.isfile(path) or not name.lower().endswith(UPLOAD_SUFFIXES):
                    continue
                if now - os.path.getmtime(path) >= TEMP_MAX_AGE:
                    os.remove(path)
                    removed += 1
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    return removed


def _cleanup_loop(app):
    while True:
        time.sleep(CLEANUP_INTERVAL)
        try:
            removed = _cleanup_temp_files()
            if removed:
                app.logger.info("RecoveryOps removed %s stale upload temp files", removed)
        except Exception:
            app.logger.exception("RecoveryOps temp cleanup failed")


def _fast_dashboard():
    user = _super_admin()
    if not user:
        return jsonify(error="Super Admin access required"), 403

    agencies = db.session.execute(text("""
        SELECT a.id, a.name, a.slug, a.phone, a.email, a.is_active, a.created_at,
               coalesce(c.id, a.id) AS legacy_id, t.schema_name
        FROM public.agencies a
        LEFT JOIN public.agency_company c ON c.name=a.name OR c.code=a.slug
        JOIN public.agency_tenants t ON t.agency_id=a.id
        WHERE t.schema_name ~ '^recovery_agency_[0-9]+$'
        ORDER BY a.created_at DESC
    """)).mappings().all()

    out = []
    total_storage = 0
    for a in agencies:
        s = str(a["schema_name"])
        aid = int(a["legacy_id"])
        counts = db.session.execute(text(f'''
            SELECT
              (SELECT count(*) FROM "{s}"."vehicle" WHERE agency_id=:a) AS vehicles,
              (SELECT count(*) FROM "{s}"."bank" WHERE agency_id=:a) AS banks,
              (SELECT count(*) FROM "{s}"."app_file" WHERE agency_id=:a) AS files,
              (SELECT count(*) FROM "{s}"."client" WHERE agency_id=:a) AS clients,
              (SELECT count(*) FROM "{s}"."recovery_case" WHERE agency_id=:a) AS recovery_cases,
              (SELECT count(*) FROM "{s}"."challan" WHERE agency_id=:a) AS challans
        '''), {"a": aid}).mappings().one()
        storage = db.session.execute(text(f'''
            SELECT coalesce(sum(pg_total_relation_size(format('%I.%I', :s, v.tab))), 0)
            FROM (VALUES
                ('vehicle'), ('bank'), ('client'), ('recovery_case'), ('app_file'),
                ('data_assignment'), ('status_history_legacy'), ('notification'),
                ('sync_mutation'), ('challan'), ('chat_conversation'), ('chat_message'),
                ('report'), ('agency_subscriptions')
            ) AS v(tab)
        '''), {"s": s}).scalar() or 0
        users = db.session.execute(text('SELECT count(*) FROM public."user" WHERE agency_id=:a AND is_deleted=false'), {"a": aid}).scalar() or 0
        sub = db.session.execute(text('SELECT * FROM public.agency_subscriptions WHERE agency_id=:a'), {"a": a["id"]}).mappings().first()
        total_storage += int(storage)
        out.append({
            "id": a["id"], "name": a["name"], "slug": a["slug"], "phone": a["phone"], "email": a["email"],
            "is_active": bool(a["is_active"]), "created_at": a["created_at"].isoformat() if a["created_at"] else None,
            "legacy_agency_id": aid, "schema_name": s,
            "counts": {k: int(v or 0) for k, v in counts.items()} | {"users": int(users)},
            "storage_bytes": int(storage), "storage_gb": round(int(storage) / 1024**3, 4),
            "subscription": dict(sub) if sub else None,
        })

    return jsonify(total_agencies=len(out), total_storage_bytes=total_storage,
                   total_storage_gb=round(total_storage / 1024**3, 4), agencies=out)


def install(app):
    # No DB DDL or index creation here. This function is deliberately startup-safe.
    removed = _cleanup_temp_files()
    if removed:
        app.logger.info("RecoveryOps removed %s stale upload temp files", removed)

    t = threading.Thread(target=_cleanup_loop, args=(app,), daemon=True, name="recoveryops-temp-cleaner")
    t.start()

    for rule in list(app.url_map.iter_rules()):
        if rule.rule == "/api/super-admin/agencies/<int:agency_id>/files/<int:file_id>" and "DELETE" in rule.methods:
            app.view_functions[rule.endpoint] = lambda agency_id, file_id: force_file_delete(file_id, agency_id)
        if rule.rule == "/api/super-admin/dashboard" and "GET" in rule.methods:
            app.view_functions[rule.endpoint] = _fast_dashboard

    if "recoveryops_super_admin_file_delete" not in app.view_functions:
        app.add_url_rule("/api/super-admin/files/<int:file_id>", "recoveryops_super_admin_file_delete", force_file_delete, methods=["DELETE"])
