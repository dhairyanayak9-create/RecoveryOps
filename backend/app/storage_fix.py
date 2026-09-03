import os
import threading
import time
from flask import jsonify
from sqlalchemy import text
from . import db
from .auth import current_user

UPLOAD_SUFFIXES = ('.xlsx', '.xls', '.csv')
MAX_TEMP_AGE_SECONDS = int(os.getenv('RECOVERYOPS_TEMP_MAX_AGE', '1800'))
CLEANUP_INTERVAL_SECONDS = int(os.getenv('RECOVERYOPS_TEMP_CLEANUP_INTERVAL', '600'))


def _sa():
    u = current_user()
    return u if u and u.active and not getattr(u, 'is_deleted', False) and u.role == 'SUPER_ADMIN' else None


def _schema_map():
    return db.session.execute(text('''
        SELECT a.id AS modern_id, coalesce(c.id, a.id) AS legacy_id, t.schema_name
        FROM public.agencies a
        LEFT JOIN public.agency_company c ON c.name=a.name OR c.code=a.slug
        LEFT JOIN public.agency_tenants t ON t.agency_id=a.id
        WHERE t.schema_name ~ '^recovery_agency_[0-9]+$'
    ''')).mappings().all()


def _delete_file(schema, legacy_id, file_id):
    q = lambda sql, p: db.session.execute(text(sql), p)
    params = {'f': file_id, 'a': legacy_id}
    found = q(f'SELECT id, filename FROM "{schema}"."app_file" WHERE id=:f AND agency_id=:a', params).mappings().first()
    if not found:
        return None

    for table in ('data_assignment', 'status_history_legacy', 'sync_mutation', 'recovery_case', 'challan'):
        q(f'''DELETE FROM "{schema}"."{table}"
              WHERE vehicle_id IN (
                  SELECT id FROM "{schema}"."vehicle"
                  WHERE source_file_id=:f AND agency_id=:a
              )''', params)

    q(f'DELETE FROM "{schema}"."vehicle" WHERE source_file_id=:f AND agency_id=:a', params)
    q(f'DELETE FROM "{schema}"."app_file" WHERE id=:f AND agency_id=:a', params)
    db.session.commit()
    return found


def _file_delete(file_id, agency_id=None):
    if not _sa():
        return jsonify(error='Super Admin access required'), 403

    try:
        for m in _schema_map():
            if agency_id is not None and int(m['modern_id']) != int(agency_id):
                continue
            target = _delete_file(str(m['schema_name']), int(m['legacy_id']), file_id)
            if target:
                return jsonify(message='File permanently deleted', id=file_id, filename=target['filename'])
        return jsonify(error='File not found'), 404
    except Exception as exc:
        db.session.rollback()
        return jsonify(error=str(exc)), 400


def _cleanup_temp_files():
    now = time.time()
    removed = 0
    try:
        for name in os.listdir('/tmp'):
            path = os.path.join('/tmp', name)
            try:
                if not os.path.isfile(path) or not name.lower().endswith(UPLOAD_SUFFIXES):
                    continue
                if now - os.path.getmtime(path) < MAX_TEMP_AGE_SECONDS:
                    continue
                os.remove(path)
                removed += 1
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return removed


def _cleanup_loop(app):
    while True:
        time.sleep(CLEANUP_INTERVAL_SECONDS)
        try:
            removed = _cleanup_temp_files()
            if removed:
                app.logger.info('Removed %s stale upload temp files', removed)
        except Exception:
            app.logger.exception('Background upload temp cleanup failed')


def _install_indexes():
    for m in _schema_map():
        s = str(m['schema_name'])
        statements = (
            f'CREATE INDEX IF NOT EXISTS "{s}_vehicle_source_file_idx" ON "{s}"."vehicle" (source_file_id)',
            f'CREATE INDEX IF NOT EXISTS "{s}_vehicle_bank_idx" ON "{s}"."vehicle" (bank_id)',
            f'CREATE INDEX IF NOT EXISTS "{s}_vehicle_registration_suffix_idx" ON "{s}"."vehicle" (registration_suffix)',
            f'CREATE INDEX IF NOT EXISTS "{s}_vehicle_agency_idx" ON "{s}"."vehicle" (agency_id)',
            f'CREATE INDEX IF NOT EXISTS "{s}_app_file_bank_idx" ON "{s}"."app_file" (bank_id)',
            f'CREATE INDEX IF NOT EXISTS "{s}_app_file_agency_idx" ON "{s}"."app_file" (agency_id)',
            f'CREATE INDEX IF NOT EXISTS "{s}_data_assignment_vehicle_idx" ON "{s}"."data_assignment" (vehicle_id)',
            f'CREATE INDEX IF NOT EXISTS "{s}_status_history_legacy_vehicle_idx" ON "{s}"."status_history_legacy" (vehicle_id)',
            f'CREATE INDEX IF NOT EXISTS "{s}_sync_mutation_vehicle_idx" ON "{s}"."sync_mutation" (vehicle_id)',
            f'CREATE INDEX IF NOT EXISTS "{s}_recovery_case_vehicle_idx" ON "{s}"."recovery_case" (vehicle_id)',
            f'CREATE INDEX IF NOT EXISTS "{s}_challan_vehicle_idx" ON "{s}"."challan" (vehicle_id)',
        )
        for sql in statements:
            try:
                db.session.execute(text(sql))
            except Exception:
                db.session.rollback()
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()


def _fast_dashboard(user):
    if not user or not user.active or getattr(user, 'is_deleted', False):
        return jsonify(error='Authentication required'), 401
    if user.role not in {'ADMIN', 'STAFF', 'SUPER_ADMIN'}:
        return jsonify(total=0, accessible_total=0, by_status={'PENDING': 0, 'HOLD': 0, 'IN_YARD': 0, 'RELEASE': 0})

    row = db.session.execute(text('''
        SELECT count(*) AS total,
               count(*) FILTER (WHERE status='PENDING') AS pending,
               count(*) FILTER (WHERE status='HOLD') AS hold,
               count(*) FILTER (WHERE status='IN_YARD') AS in_yard,
               count(*) FILTER (WHERE status='RELEASE') AS release
        FROM vehicle
    ''')).mappings().one()
    total = int(row['total'] or 0)
    return jsonify(
        total=total,
        accessible_total=total,
        by_status={
            'PENDING': int(row['pending'] or 0),
            'HOLD': int(row['hold'] or 0),
            'IN_YARD': int(row['in_yard'] or 0),
            'RELEASE': int(row['release'] or 0),
        },
    )


def install(app):
    removed = _cleanup_temp_files()
    if removed:
        app.logger.info('Removed %s stale upload temp files', removed)

    with app.app_context():
        try:
            _install_indexes()
        except Exception:
            app.logger.exception('Runtime index setup failed')

    # Keep stale uploads from accumulating even when no new deployment occurs.
    t = threading.Thread(target=_cleanup_loop, args=(app,), daemon=True, name='recoveryops-temp-cleaner')
    t.start()

    for rule in list(app.url_map.iter_rules()):
        if rule.rule == '/api/super-admin/agencies/<int:agency_id>/files/<int:file_id>' and 'DELETE' in rule.methods:
            app.view_functions[rule.endpoint] = lambda agency_id, file_id: _file_delete(file_id, agency_id)
        if rule.rule == '/api/dashboard' and 'GET' in rule.methods:
            app.view_functions[rule.endpoint] = _fast_dashboard

    if 'storage_fix_file_delete' not in app.view_functions:
        app.add_url_rule('/api/super-admin/files/<int:file_id>', 'storage_fix_file_delete', _file_delete, methods=['DELETE'])
