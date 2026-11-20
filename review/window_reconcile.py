import sqlite3

from reports.window_plans import (
    build_window_plan, get_window_plan_record, insert_window_plan, validate_window_selection,
)
from review.window_admission import get_accepted_window
from review.window_feasibility import reconcile_windows


def review_windows(db: sqlite3.Connection, supply_id: str, receive_id: str) -> dict:
    """Reconcile two admitted V2 window packets and atomically publish one plan."""
    if not isinstance(supply_id, str) or not supply_id or not isinstance(receive_id, str) or not receive_id:
        raise ValueError('window admission ids must be nonblank strings')
    supply_accepted = get_accepted_window(db, supply_id)
    receive_accepted = get_accepted_window(db, receive_id)
    supply = supply_accepted.packet
    receive = receive_accepted.packet
    # Raises ValueError on wrong sides, window batches, totals or port sets.
    validate_window_selection(supply, receive)
    status, witness = reconcile_windows(supply, receive)
    plan = build_window_plan(supply_accepted, receive_accepted, status, witness)

    db.execute('BEGIN IMMEDIATE')
    with db:
        insert_window_plan(db, plan)
    return get_window_plan_record(db, plan['plan_id'])
