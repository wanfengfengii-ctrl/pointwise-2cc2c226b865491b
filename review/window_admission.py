import sqlite3
from dataclasses import dataclass
from uuid import uuid4

from issue.window_envelope import WindowPacket, decode_window_packet, encode_window_packet


@dataclass(frozen=True)
class AcceptedWindow:
    accepted_id: str
    packet: WindowPacket


def initialize_window_admissions(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS accepted_window_packets (
            accepted_id TEXT PRIMARY KEY NOT NULL,
            envelope TEXT NOT NULL
        )
    ''')


def admit_windows(db: sqlite3.Connection, material) -> str:
    """Admit a trusted fixed V2 dual-window packet and return its opaque admission id."""
    if isinstance(material, WindowPacket):
        packet = material
    elif isinstance(material, (str, bytes)):
        packet = decode_window_packet(material)
    else:
        raise ValueError('window admission requires JSON text or UTF-8 bytes')
    encoded = encode_window_packet(packet)
    db.execute('BEGIN IMMEDIATE')
    with db:
        accepted_id = 'win-admit-' + uuid4().hex
        db.execute(
            'INSERT INTO accepted_window_packets (accepted_id, envelope) VALUES (?, ?)',
            (accepted_id, encoded),
        )
    return accepted_id


def get_accepted_window(db: sqlite3.Connection, accepted_id: str) -> AcceptedWindow:
    if not isinstance(accepted_id, str) or not accepted_id:
        raise ValueError('window admission id must be a nonblank string')
    row = db.execute(
        'SELECT accepted_id, envelope FROM accepted_window_packets WHERE accepted_id = ?',
        (accepted_id,),
    ).fetchone()
    if row is None:
        raise KeyError(accepted_id)
    return AcceptedWindow(row[0], decode_window_packet(row[1]))
