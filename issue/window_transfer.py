import sqlite3

from issue.window_catalog import find_window_packet, record_window_pickup
from issue.window_envelope import encode_window_packet


def get_windows_packet(db: sqlite3.Connection, packet_id: str) -> str:
    """Return the fixed V2 JSON text for a sealed dual-window packet."""
    db.execute('BEGIN IMMEDIATE')
    with db:
        packet = find_window_packet(db, packet_id)
        if packet is None:
            raise KeyError(packet_id)
        record_window_pickup(db, packet_id)
    return encode_window_packet(packet)
