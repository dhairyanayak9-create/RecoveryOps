_installed = False


def install():
    global _installed
    if _installed:
        return

    from . import routes
    from .models import Vehicle

    if getattr(routes.accessible_vehicle_query, "_recoveryops_repo_agent_fix", False):
        _installed = True
        return

    original = routes.accessible_vehicle_query

    def accessible_vehicle_query(user, write=False):
        if user.role != "REPO_AGENT":
            try:
                return original(user, write)
            except TypeError:
                return original(user)
        return Vehicle.query

    accessible_vehicle_query._recoveryops_repo_agent_fix = True
    routes.accessible_vehicle_query = accessible_vehicle_query
    _installed = True
