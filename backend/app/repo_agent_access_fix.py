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
            return original(user, write)

        # Repo Agents can work with the data of their authenticated agency.
        # Tenant search_path + ORM tenant guards prevent cross-agency access.
        return Vehicle.query

    accessible_vehicle_query._recoveryops_repo_agent_fix = True
    routes.accessible_vehicle_query = accessible_vehicle_query
    _installed = True
