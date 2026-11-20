import sqlite3

from issue.window_envelope import WindowPacket, decode_window_packet, encode_window_packet


def initialize_window_catalog(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS window_packets (
            packet_id TEXT PRIMARY KEY NOT NULL,
            envelope TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS window_permissions (
            packet_id TEXT NOT NULL REFERENCES window_packets(packet_id),
            window_index INTEGER NOT NULL CHECK (window_index IN (0, 1)),
            revision_id TEXT NOT NULL,
            pair_order INTEGER NOT NULL CHECK (pair_order > 0),
            PRIMARY KEY (packet_id, pair_order, window_index)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS window_pickups (
            pickup_id INTEGER PRIMARY KEY,
            packet_id TEXT NOT NULL REFERENCES window_packets(packet_id)
        )
    ''')


def insert_window_packet(db: sqlite3.Connection, packet: WindowPacket, pairs: tuple[tuple[str, str], ...]) -> None:
    db.execute(
        'INSERT INTO window_packets (packet_id, envelope) VALUES (?, ?)',
        (packet.packet_id, encode_window_packet(packet)),
    )
    db.executemany(
        '''INSERT INTO window_permissions (packet_id, window_index, revision_id, pair_order)
           VALUES (?, ?, ?, ?)''',
        ((packet.packet_id, window_index, revision_id, pair_order)
         for pair_order, pair in enumerate(pairs, start=1)
         for window_index, revision_id in enumerate(pair)),
    )


def find_window_packet(db: sqlite3.Connection, packet_id: str) -> WindowPacket | None:
    row = db.execute(
        'SELECT envelope FROM window_packets WHERE packet_id = ?', (packet_id,),
    ).fetchone()
    return decode_window_packet(row[0]) if row is not None else None


def record_window_pickup(db: sqlite3.Connection, packet_id: str) -> None:
    db.execute('INSERT INTO window_pickups (packet_id) VALUES (?)', (packet_id,))
