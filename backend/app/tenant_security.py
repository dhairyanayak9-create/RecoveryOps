from flask import jsonify, g
from sqlalchemy import text
from . import db
from .models import User, Bank, Vehicle, AppFile, StatusHistory, SyncMutation, RecoveryCase, Challan, Client
from .auth import current_user


def _user():
    u = current_user()
    if not u or not u.active or getattr(u, 'is_deleted', False):
        return None
    return u


def _unauthorized():
    return jsonify({'error': 'Authentication required'}), 401


def _forbidden():
    return jsonify({'error': 'Forbidden'}), 403


def _vehicle_query(user):
    q = Vehicle.query
    if user.role == 'SUPER_ADMIN':
        return q
    if user.role in {'ADMIN', 'STAFF'} and user.agency_id is not None:
        return q.filter(Vehicle.agency_id == user.agency_id)
    return q.filter(db.false())


def _bank_query(user):
    q = Bank.query
    if user.role == 'SUPER_ADMIN':
        return q
    return q.filter(Bank.agency_id == user.agency_id)


def _file_query(user):
    q = AppFile.query
    if user.role == 'SUPER_ADMIN':
        return q
    return q.filter(AppFile.agency_id == user.agency_id)


def _user_query(user):
    q = User.query
    if user.role == 'SUPER_ADMIN':
        return q
    return q.filter(User.agency_id == user.agency_id)


def _vehicle_json(v):
    bank = Bank.query.filter(Bank.id == v.bank_id, Bank.agency_id == v.agency_id).first() if v.bank_id else None
    return {
        'id': v.id, 'account_no': v.account_no, 'loan_no': v.loan_no,
        'registration_no': v.registration_no, 'chassis_no': v.chassis_no,
        'engine_no': v.engine_no, 'customer_name': v.customer_name,
        'mobile': v.mobile, 'city': v.city, 'state': v.state,
        'pincode': v.pincode, 'vehicle_type': v.vehicle_type,
        'bank_id': v.bank_id, 'bank_name': bank.name if bank else 'N/A',
        'status': v.status, 'version': v.version,
        'source_file_id': v.source_file_id,
        'latitude': v.latitude, 'longitude': v.longitude,
        'updated_at': v.updated_at.isoformat() if v.updated_at else None,
    }


def _replace_view(app, endpoint, fn):
    if endpoint in app.view_functions:
        app.view_functions[endpoint] = fn


def install(app):
    from . import routes

    routes.accessible_vehicle_query = _vehicle_query

    def bank_list():
        u = _user()
        if not u: return _unauthorized()

        # Bank is a tenant-owned table. Resolve the schema explicitly instead of
        # relying only on the connection search_path, which can be stale across
        # pooled connections during nested authentication queries.
        schema = getattr(g, 'tenant_schema', None)
        if u.role != 'SUPER_ADMIN':
            if not schema or not str(schema).startswith('recovery_agency_'):
                return jsonify({'error': 'Agency data store is not provisioned'}), 503
            rows = db.session.execute(text(f'''SELECT id, name, code, phone, email, active, created_at
                                               FROM "{schema}"."bank"
                                               WHERE agency_id = :agency_id
                                               ORDER BY name'''),
                                      {'agency_id': u.agency_id}).mappings().all()
        else:
            if schema and str(schema).startswith('recovery_agency_'):
                rows = db.session.execute(text(f'''SELECT id, name, code, phone, email, active, created_at
                                                   FROM "{schema}"."bank"
                                                   ORDER BY name''')).mappings().all()
            else:
                rows = db.session.execute(text('''SELECT id, name, code, phone, email, active, created_at
                                                   FROM public."bank"
                                                   ORDER BY name''')).mappings().all()

        return jsonify([{
            'id': b['id'], 'name': b['name'], 'code': b['code'],
            'phone': b['phone'], 'email': b['email'],
            'active': bool(b['active']),
            'created_at': b['created_at'].isoformat() if b['created_at'] else None,
        } for b in rows])

    def bank_create():
        u = _user()
        if not u: return _unauthorized()
        if u.role not in {'ADMIN', 'STAFF', 'SUPER_ADMIN'}: return _forbidden()
        from flask import request
        d = request.get_json(silent=True) or {}
        name = str(d.get('name') or '').strip()
        code = str(d.get('code') or '').strip() or None
        if not name: return jsonify({'error': 'Bank name required'}), 400
        if _bank_query(u).filter(db.or_(Bank.name == name, Bank.code == code) if code else Bank.name == name).first():
            return jsonify({'error': 'Bank already exists'}), 409
        b = Bank(agency_id=None if u.role == 'SUPER_ADMIN' else u.agency_id,
                 name=name, code=code, phone=d.get('phone'), email=str(d.get('email') or '').strip().lower() or None, active=True)
        db.session.add(b); db.session.commit()
        return jsonify({'id': b.id, 'name': b.name, 'code': b.code}), 201

    def bank_update(bank_id):
        u = _user()
        if not u: return _unauthorized()
        if u.role not in {'ADMIN', 'STAFF', 'SUPER_ADMIN'}: return _forbidden()
        b = _bank_query(u).filter(Bank.id == bank_id).first()
        if not b: return jsonify({'error': 'Bank not found in your agency'}), 404
        from flask import request
        d = request.get_json(silent=True) or {}
        name = str(d.get('name') or b.name).strip()
        code = str(d.get('code') or b.code or '').strip() or None
        clash = _bank_query(u).filter(Bank.id != b.id, db.or_(Bank.name == name, Bank.code == code) if code else Bank.name == name).first()
        if clash: return jsonify({'error': 'Bank name or code already exists'}), 409
        b.name, b.code = name, code
        if 'phone' in d: b.phone = d.get('phone')
        if 'email' in d: b.email = str(d.get('email') or '').strip().lower() or None
        db.session.commit(); return jsonify({'id': b.id, 'name': b.name, 'code': b.code, 'active': bool(b.active)})

    def bank_delete(bank_id):
        u = _user()
        if not u: return _unauthorized()
        if u.role not in {'ADMIN', 'STAFF', 'SUPER_ADMIN'}: return _forbidden()
        b = _bank_query(u).filter(Bank.id == bank_id).first()
        if not b: return jsonify({'error': 'Bank not found in your agency'}), 404
        if Vehicle.query.filter(Vehicle.bank_id == b.id, Vehicle.agency_id == b.agency_id).first():
            return jsonify({'error': 'Bank has vehicle records. Delete or reassign those records first.'}), 409
        db.session.delete(b); db.session.commit(); return jsonify({'message': 'Bank deleted'})

    def users_list():
        u = _user()
        if not u: return _unauthorized()
        if u.role not in {'ADMIN', 'STAFF', 'SUPER_ADMIN'}: return _forbidden()
        return jsonify([routes.user_json(x) for x in _user_query(u).order_by(User.id.desc()).all()])

    def files_list():
        u = _user()
        if not u: return _unauthorized()
        rows = _file_query(u).order_by(AppFile.id.desc()).all()
        out=[]
        for f in rows:
            b=Bank.query.filter(Bank.id == f.bank_id, Bank.agency_id == f.agency_id).first() if f.bank_id else None
            out.append({
                'id': f.id, 'filename': f.filename, 'vehicle_type': f.vehicle_type,
                'bank_id': f.bank_id, 'bank_name': b.name if b else '—',
                'record_count': f.record_count or 0, 'status': f.status,
                'uploaded_by': f.uploaded_by, 'agency_id': f.agency_id,
                'created_at': f.created_at.isoformat() if f.created_at else None,
            })
        return jsonify(out)

    def file_status(file_id):
        u = _user()
        if not u: return _unauthorized()
        f = _file_query(u).filter(AppFile.id == file_id).first()
        if not f: return jsonify({'error': 'File not found in your agency'}), 404
        return jsonify({'id': f.id, 'filename': f.filename, 'status': f.status,
                        'record_count': f.record_count or 0, 'bank_id': f.bank_id,
                        'agency_id': f.agency_id, 'uploaded_by': f.uploaded_by})

    def file_delete(file_id):
        u = _user()
        if not u: return _unauthorized()
        if u.role not in {'ADMIN', 'STAFF', 'SUPER_ADMIN'}: return _forbidden()
        f = _file_query(u).filter(AppFile.id == file_id).first()
        if not f: return jsonify({'error': 'File not found in your agency'}), 404
        qv = Vehicle.query.filter(Vehicle.source_file_id == f.id)
        if u.role != 'SUPER_ADMIN': qv = qv.filter(Vehicle.agency_id == u.agency_id)
        vehicles = qv.all()
        ids = [v.id for v in vehicles]
        if ids:
            for model in (StatusHistory, SyncMutation, RecoveryCase, Challan):
                q = model.query.filter(model.vehicle_id.in_(ids))
                if u.role != 'SUPER_ADMIN': q = q.filter(model.agency_id == u.agency_id)
                q.delete(synchronize_session=False)
            Vehicle.query.filter(Vehicle.id.in_(ids)).delete(synchronize_session=False)
        filename = f.filename
        records = len(vehicles)
        db.session.delete(f); db.session.commit()
        return jsonify({'message': 'File and imported records deleted', 'filename': filename, 'vehicles_deleted': records})

    def clients_list():
        u = _user()
        if not u: return _unauthorized()
        q = Client.query if u.role == 'SUPER_ADMIN' else Client.query.filter(Client.agency_id == u.agency_id)
        return jsonify([routes.obj_json(x, ['id','name','code','phone','email','active','created_at']) for x in q.order_by(Client.id.desc()).all()])

    def recovery_list():
        u = _user()
        if not u: return _unauthorized()
        q = RecoveryCase.query if u.role == 'SUPER_ADMIN' else RecoveryCase.query.filter(RecoveryCase.agency_id == u.agency_id)
        return jsonify([routes.obj_json(x, ['id','vehicle_id','client_id','assigned_user_id','stage','notes','next_action_at','created_at','updated_at']) for x in q.order_by(RecoveryCase.id.desc()).limit(500).all()])

    _replace_view(app, 'api.banks', bank_list)
    _replace_view(app, 'api.add_bank', bank_create)
    _replace_view(app, 'api.update_bank', bank_update)
    _replace_view(app, 'api.delete_bank', bank_delete)
    _replace_view(app, 'api.users', users_list)
    _replace_view(app, 'api.files_list', files_list)
    _replace_view(app, 'api.file_status', file_status)
    _replace_view(app, 'api.delete_file', file_delete)
    _replace_view(app, 'api.get_clients', clients_list)
    _replace_view(app, 'api.get_recovery', recovery_list)
