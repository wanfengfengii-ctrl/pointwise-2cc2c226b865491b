import sqlite3

from issue.envelope import PacketEnvelope, read_limits
from reports.publish import publish_report
from reports.readback import Report
from reports.snapshots import build_snapshot
from review.ranges import PortRange, compute_port_range
from review.selection import ReviewSelection, select_pair


def _common_rows(packet: PacketEnvelope, coordinates: tuple[int, ...]):
    for mask, upper in read_limits(packet.evidence, packet.total):
        coefficients = [0] * len(coordinates)
        for local, common in enumerate(coordinates):
            if mask & (1 << local):
                coefficients[common] = 1
        yield tuple(coefficients), upper


def reconcile_evidence(selection: ReviewSelection) -> PortRange:
    alignment = selection.alignment
    supply = selection.supply.packet
    receive = selection.receive.packet
    rows = [*_common_rows(supply, alignment.supply_to_common),
            *_common_rows(receive, alignment.receive_to_common)]
    return compute_port_range(supply.total, rows, alignment.target_index)


def review(db: sqlite3.Connection, supply_id: int, receive_id: int, port: str) -> Report:
    selection = select_pair(db, supply_id, receive_id, port)
    result = reconcile_evidence(selection)
    return publish_report(db, build_snapshot(selection, result))
