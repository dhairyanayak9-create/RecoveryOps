from functools import wraps
import inspect
from flask import redirect

def install(app):
    # Older Super Admin handlers use `aid`; Flask routes use `agency_id`.
    # Adapt every matching existing handler without duplicating URL rules.
    for rule in list(app.url_map.iter_rules()):
        if '/api/super-admin/agencies/<int:agency_id>' not in rule.rule:
            continue
        endpoint=rule.endpoint
        fn=app.view_functions.get(endpoint)
        if not fn or getattr(fn,'_sa_compat',False):
            continue
        try: params=inspect.signature(fn).parameters
        except Exception: continue
        if 'agency_id' in params or 'aid' not in params:
            continue
        @wraps(fn)
        def wrapped(*args,_fn=fn,**kwargs):
            if 'agency_id' in kwargs and 'aid' not in kwargs:kwargs['aid']=kwargs.pop('agency_id')
            return _fn(*args,**kwargs)
        wrapped._sa_compat=True
        app.view_functions[endpoint]=wrapped

    # The old standalone /super-admin page uses the legacy /upload endpoint,
    # which writes to the old public store. The main app contains the unified
    # Super Admin Control Center and its tenant-safe upload-v2 flow. Always use
    # that single UI so agency uploads go to the selected isolated store.
    if 'super_admin.super_admin_page' in app.view_functions:
        def unified_super_admin_page():
            return redirect('/')
        app.view_functions['super_admin.super_admin_page'] = unified_super_admin_page

    # Runtime storage/file maintenance. This is installed here because this
    # compatibility module is already loaded for every production app start.
    from .storage_fix import install as install_storage_fix
    install_storage_fix(app)
