from flask import request, jsonify
from sqlalchemy import text
from . import db
from .models import AgencyCompany, User, Bank, Vehicle, AppFile
from .auth import auth_required
from .super_admin import super_admin, _require_super_admin, _agency_detail, _user_json, _legacy_agency_id


def _agency_ok(agency_id):
    return _agency_detail(agency_id)


@super_admin.get('/api/super-admin/agencies/<int:agency_id>/banks')
@auth_required
def section_banks(user, agency_id):
    denied = _require_super_admin(user)
    if denied: return denied
    detail = _agency_ok(agency_id)
    if not detail: return jsonify({'error':'Agency not found'}), 404
    legacy = detail['legacy_agency_id']
    rows = Bank.query.filter_by(agency_id=legacy).order_by(Bank.name.asc()).all()
    return jsonify([{'id':b.id,'name':b.name,'code':b.code,'active':bool(b.active)} for b in rows])


@super_admin.get('/api/super-admin/agencies/<int:agency_id>/files')
@auth_required
def section_files(user, agency_id):
    denied = _require_super_admin(user)
    if denied: return denied
    detail = _agency_ok(agency_id)
    if not detail: return jsonify({'error':'Agency not found'}), 404
    legacy = detail['legacy_agency_id']
    rows = AppFile.query.filter_by(agency_id=legacy).order_by(AppFile.created_at.desc()).all()
    out=[]
    for f in rows:
        bank=Bank.query.filter_by(id=f.bank_id, agency_id=legacy).first() if f.bank_id else None
        out.append({'id':f.id,'filename':f.filename,'vehicle_type':f.vehicle_type,'bank_name':bank.name if bank else '—','record_count':int(f.record_count or 0),'status':f.status,'uploaded_by':f.uploaded_by,'created_at':f.created_at.isoformat() if f.created_at else None})
    return jsonify(out)


@super_admin.delete('/api/super-admin/agencies/<int:agency_id>/files/<int:file_id>')
@auth_required
def section_delete_file(user, agency_id, file_id):
    denied = _require_super_admin(user)
    if denied: return denied
    detail = _agency_ok(agency_id)
    if not detail: return jsonify({'error':'Agency not found'}), 404
    legacy = detail['legacy_agency_id']
    f=AppFile.query.filter_by(id=file_id, agency_id=legacy).first()
    if not f: return jsonify({'error':'File not found in this agency'}),404
    filename=f.filename
    deleted_records=Vehicle.query.filter_by(source_file_id=f.id, agency_id=legacy).count()
    Vehicle.query.filter_by(source_file_id=f.id, agency_id=legacy).delete(synchronize_session=False)
    db.session.delete(f)
    db.session.commit()
    return jsonify({'message':'File deleted','filename':filename,'records_deleted':deleted_records})


@super_admin.get('/api/super-admin/agencies/<int:agency_id>/section-summary')
@auth_required
def section_summary(user, agency_id):
    denied=_require_super_admin(user)
    if denied:return denied
    detail=_agency_ok(agency_id)
    if not detail:return jsonify({'error':'Agency not found'}),404
    legacy=detail['legacy_agency_id']
    return jsonify({
        'agency': {'id':agency_id,'name':detail['name'],'slug':detail['slug']},
        'counts': detail['counts'],
        'storage_gb': detail['storage_gb'],
        'storage_bytes': detail['storage_bytes'],
        'users': detail['users'],
    })
