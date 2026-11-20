import json
import sqlite3
from uuid import uuid4

from issue.window_envelope import WindowPacket
from station.ports import canonical_port_ids
from station.volumes import encode_liters, parse_liters


def initialize_window_plans(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS window_plans (
            plan_id TEXT PRIMARY KEY NOT NULL,
            record TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS window_plan_receipts (
            plan_id TEXT PRIMARY KEY REFERENCES window_plans(plan_id),
            receipt_id TEXT NOT NULL UNIQUE
        )
    ''')


def validate_window_selection(supply: WindowPacket, receive: WindowPacket) -> tuple[str, ...]:
    if (supply.side, receive.side) != ('supply', 'receive'):
        raise ValueError('select a supply window packet followed by a receive window packet')
    if supply.window_ids != receive.window_ids:
        raise ValueError('window packets disagree on the ordered window batches')
    if supply.totals != receive.totals:
        raise ValueError('window packets disagree on the full batch volumes')
    supply_ports = canonical_port_ids(supply.ports)
    if canonical_port_ids(receive.ports) != supply_ports:
        raise ValueError('window packets disagree on the public port identities')
    return supply_ports


def build_window_plan(supply_accepted, receive_accepted, status: str, witness) -> dict:
    supply = supply_accepted.packet
    receive = receive_accepted.packet
    ports = validate_window_selection(supply, receive)
    plan_id = 'window-plan-' + uuid4().hex
    return {
        'plan_id': plan_id,
        'receipt_id': 'window-receipt-' + uuid4().hex,
        'supply_id': supply_accepted.accepted_id,
        'receive_id': receive_accepted.accepted_id,
        'supply_packet_id': supply.packet_id,
        'receive_packet_id': receive.packet_id,
        'window_ids': tuple(supply.window_ids),
        'totals': tuple(supply.totals),
        'ports': ports,
        'status': status,
        'witness': None if witness is None else tuple(tuple(vector) for vector in witness),
    }


def encode_window_plan(plan: dict) -> str:
    record = {
        'plan_id': plan['plan_id'],
        'receipt_id': plan['receipt_id'],
        'supply_id': plan['supply_id'],
        'receive_id': plan['receive_id'],
        'supply_packet_id': plan['supply_packet_id'],
        'receive_packet_id': plan['receive_packet_id'],
        'window_ids': list(plan['window_ids']),
        'totals': [encode_liters(value) for value in plan['totals']],
        'ports': list(plan['ports']),
        'status': plan['status'],
        'witness': None if plan['witness'] is None
        else [[encode_liters(amount) for amount in vector] for vector in plan['witness']],
    }
    return json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def decode_window_plan(encoded: str) -> dict:
    record = json.loads(encoded)
    if record['status'] not in ('feasible', 'infeasible'):
        raise ValueError('invalid stored window plan status')
    witness = None
    if record['status'] == 'feasible':
        witness = tuple(tuple(parse_liters(amount) for amount in vector) for vector in record['witness'])
    elif record['witness'] is not None:
        raise ValueError('infeasible window plans carry no witness')
    return {
        'plan_id': record['plan_id'],
        'receipt_id': record['receipt_id'],
        'supply_id': record['supply_id'],
        'receive_id': record['receive_id'],
        'supply_packet_id': record['supply_packet_id'],
        'receive_packet_id': record['receive_packet_id'],
        'window_ids': tuple(record['window_ids']),
        'totals': tuple(parse_liters(amount, positive=True) for amount in record['totals']),
        'ports': tuple(record['ports']),
        'status': record['status'],
        'witness': witness,
    }


def insert_window_plan(db: sqlite3.Connection, plan: dict) -> None:
    db.execute('INSERT INTO window_plans (plan_id, record) VALUES (?, ?)',
               (plan['plan_id'], encode_window_plan(plan)))
    db.execute('INSERT INTO window_plan_receipts (plan_id, receipt_id) VALUES (?, ?)',
               (plan['plan_id'], plan['receipt_id']))


def get_window_plan_record(db: sqlite3.Connection, plan_id: str):
    row = db.execute('SELECT record FROM window_plans WHERE plan_id = ?', (plan_id,)).fetchone()
    if row is None:
        raise KeyError(plan_id)
    return decode_window_plan(row[0])
