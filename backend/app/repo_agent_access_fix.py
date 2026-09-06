from sqlalchemy import or_

from . import db
from .models import Vehicle, DataAssignment

_installed = False


def install():
    global _installed
    if _installed:
        return

    from . import routes

    if getattr(routes.accessible_vehicle_query, "_recoveryops_repo_agent_fix", False):
        _installed = True
        return

    original = routes.accessible_vehicle_query

    def accessible_vehicle_query(user, write=False):
        if user.role != "REPO_AGENT":
            return original(user, write)

        # Repo Agents can sync only records assigned to them. Assignments may
        # be made at bank level or for individual vehicles. These queries are
        # intentionally executed against the authenticated tenant schema.
        bank_ids = (
            db.session.query(DataAssignment.bank_id)
            .filter(
                DataAssignment.user_id == user.id,
                DataAssignment.active.is_(True),
                DataAssignment.bank_id.isnot(None),
            )
        )

        vehicle_ids = (
            db.session.query(DataAssignment.vehicle_id)
            .filter(
                DataAssignment.user_id == user.id,
                DataAssignment.active.is_(True),
                DataAssignment.vehicle_id.isnot(None),
            )
        )

        return (
            Vehicle.query
            .filter(Vehicle.agency_id == user.agency_id)
            .filter(
                or_(
                    Vehicle.bank_id.in_(bank_ids),
                    Vehicle.id.in_(vehicle_ids),
                )
            )
        )

    accessible_vehicle_query._recoveryops_repo_agent_fix = True
    routes.accessible_vehicle_query = accessible_vehicle_query
    _installed = True
