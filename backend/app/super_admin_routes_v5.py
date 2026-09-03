from flask import jsonify
from sqlalchemy import text
from . import db
from .auth import current_user
import re

def install(app):
    def delete_agency(agency_id):
        u=current_user()
        if not u or u.role!='SUPER_ADMIN' or not u.active or getattr(u,'is_deleted',False):return jsonify(error='Super Admin access required'),403
        m=db.session.execute(text('''SELECT a.id modern_id, coalesce(c.id,a.id) legacy_id, t.schema_name FROM public.agencies a LEFT JOIN public.agency_company c ON c.name=a.name OR c.code=a.slug LEFT JOIN public.agency_tenants t ON t.agency_id=a.id WHERE a.id=:id LIMIT 1'''),{'id':agency_id}).mappings().first()
        if not m:return jsonify(error='Agency not found'),404
        s=str(m['schema_name'] or '')
        try:
            if re.fullmatch(r'recovery_agency_[0-9]+',s):db.session.execute(text(f'DROP SCHEMA "{s}" CASCADE'))
            # Remove modern compatibility rows first because they reference each other.
            for t in ('status_history','data_assignments','reports','recovery_cases','app_files','challans','chat_message','chat_conversation','notification','vehicles','clients','banks'):
                if db.session.execute(text('SELECT to_regclass(:r)'),{'r':f'public.{t}'}).scalar():db.session.execute(text(f'DELETE FROM public."{t}" WHERE agency_id=:a'),{'a':agency_id})
            # Remove legacy compatibility rows too so old endpoints cannot resurrect deleted data.
            for t in ('status_history_legacy','sync_mutation','recovery_case','challan','chat_message','chat_conversation','notification','report','data_assignment','vehicle','app_file','client','bank'):
                if db.session.execute(text('SELECT to_regclass(:r)'),{'r':f'public.{t}'}).scalar():db.session.execute(text(f'DELETE FROM public."{t}" WHERE agency_id=:a'),{'a':m['legacy_id']})
            db.session.execute(text('DELETE FROM public.agency_members WHERE agency_id=:a'),{'a':agency_id})
            db.session.execute(text('DELETE FROM public."user" WHERE agency_id=:a'),{'a':m['legacy_id']})
            db.session.execute(text('DELETE FROM public.agency_subscriptions WHERE agency_id=:a'),{'a':agency_id})
            db.session.execute(text('DELETE FROM public.agency_tenants WHERE agency_id=:a'),{'a':agency_id})
            db.session.execute(text('DELETE FROM public.agency_company WHERE id=:a'),{'a':m['legacy_id']})
            db.session.execute(text('DELETE FROM public.agencies WHERE id=:a'),{'a':agency_id})
            db.session.commit();return jsonify(message='Agency permanently deleted',id=agency_id)
        except Exception as e:db.session.rollback();return jsonify(error=str(e)),400
    app.add_url_rule('/api/super-admin/agencies/<int:agency_id>','super_admin_permanent_agency_delete',delete_agency,methods=['DELETE'])
