"""Fixed dual-window V2 packets.

A V2 packet binds two ordered windows of one station side to the exact set
of directed shift permissions granted at sealing time.  Private valve
identifiers, station nodes, flow paths and raw measurements stay in the
station database; only full-batch boundary capability leaves the station.
"""
import json
from dataclasses import dataclass
from fractions import Fraction

from issue.envelope import _wire_liters, read_limits
from station.ports import PortRoster, canonical_port_ids, validate_identity, validate_roster
from station.volumes import encode_liters, parse_liters


@dataclass(frozen=True)
class WindowPacket:
    packet_id: str
    side: str
    window_ids: tuple[str, str]
    totals: tuple[Fraction, Fraction]
    ports: PortRoster
    evidence: dict
    version: int = 2


_FIELDS = {'version', 'packet_id', 'side', 'window_ids', 'totals', 'ports', 'evidence'}


def validate_window_packet(packet: WindowPacket) -> None:
    if not isinstance(packet, WindowPacket) or type(packet.version) is not int or packet.version != 2:
        raise ValueError('unsupported window packet version')
    validate_identity(packet.packet_id, 'window packet')
    if packet.side not in ('supply', 'receive'):
        raise ValueError('invalid station side')
    if (not isinstance(packet.window_ids, tuple) or len(packet.window_ids) != 2
            or len(set(packet.window_ids)) != 2
            or any(not isinstance(value, str) or not value for value in packet.window_ids)):
        raise ValueError('window_ids must be two distinct nonblank identities')
    if not isinstance(packet.totals, tuple) or len(packet.totals) != 2:
        raise ValueError('totals must hold two full-batch volumes')
    totals = tuple(parse_liters(value, positive=True) for value in packet.totals)
    if not all(isinstance(value, Fraction) for value in packet.totals):
        raise ValueError('window totals must be Fractions')
    if not isinstance(packet.ports, PortRoster):
        raise ValueError('invalid window port roster')
    canonical = canonical_port_ids(validate_roster(packet.ports))
    _validate_evidence(packet.evidence, packet.window_ids, packet.totals, canonical)


def _validate_evidence(evidence, window_ids, totals, canonical_ports) -> None:
    if not isinstance(evidence, dict) or set(evidence) != {'capabilities', 'permissions'}:
        raise ValueError('invalid window evidence')
    capabilities = evidence['capabilities']
    permissions = evidence['permissions']
    if not isinstance(capabilities, list) or not isinstance(permissions, list):
        raise ValueError('invalid window evidence lists')

    capability_revisions = {window_id: set() for window_id in window_ids}
    all_revisions = set()
    for entry in capabilities:
        if not isinstance(entry, dict) or set(entry) != {'window_id', 'revision_id', 'roster', 'limits'}:
            raise ValueError('invalid window capability entry')
        window_id = entry['window_id']
        if not isinstance(window_id, str) or not window_id or window_id not in window_ids:
            raise ValueError('window capability references an unknown window')
        revision_id = entry['revision_id']
        validate_identity(revision_id, 'capability revision')
        if revision_id in capability_revisions[window_id]:
            raise ValueError('duplicate window capability entry')
        if revision_id in all_revisions:
            raise ValueError('a capability revision cannot serve both windows')
        all_revisions.add(revision_id)
        roster = entry['roster']
        if not isinstance(roster, list) or tuple(sorted(roster)) != canonical_ports or len(set(roster)) != 4:
            raise ValueError('capability roster must list the same four port identities once each')
        index = window_ids.index(window_id)
        read_limits({'limits': entry['limits']}, totals[index])
        capability_revisions[window_id].add(revision_id)
    if not all(capability_revisions[window_id] for window_id in window_ids):
        raise ValueError('each window needs at least one capability entry')

    def revision_window(candidate):
        for window_id, revision_ids in capability_revisions.items():
            if candidate in revision_ids:
                return window_id
        return None

    seen = set()
    for pair in permissions:
        if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(value, str) and value for value in pair):
            raise ValueError('each permission must be an ordered pair of revisions')
        front, back = pair
        if revision_window(front) != window_ids[0] or revision_window(back) != window_ids[1]:
            raise ValueError('permission ends must belong to the front and back windows respectively')
        if tuple(pair) in seen:
            raise ValueError('duplicate shift permission')
        seen.add(tuple(pair))
    if not seen:
        raise ValueError('at least one shift permission is required')


def encode_window_packet(packet: WindowPacket) -> str:
    validate_window_packet(packet)
    return json.dumps({
        'version': packet.version,
        'packet_id': packet.packet_id,
        'side': packet.side,
        'window_ids': list(packet.window_ids),
        'totals': [encode_liters(value) for value in packet.totals],
        'ports': packet.ports.ids,
        'evidence': packet.evidence,
    }, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def _unique_fields(pairs):
    record = {}
    for key, value in pairs:
        if key in record:
            raise ValueError(f'duplicate window packet field: {key}')
        record[key] = value
    return record


def decode_window_packet(encoded: str | bytes) -> WindowPacket:
    try:
        record = json.loads(encoded, object_pairs_hook=_unique_fields)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('invalid window packet JSON') from error
    if not isinstance(record, dict) or set(record) != _FIELDS:
        raise ValueError('invalid window packet fields')
    if record['version'] != 2:
        raise ValueError('window admission requires a V2 packet')
    window_ids = record['window_ids']
    totals = record['totals']
    ports = record['ports']
    if (not isinstance(window_ids, list) or len(window_ids) != 2
            or not isinstance(totals, list) or len(totals) != 2
            or not isinstance(ports, list)):
        raise ValueError('invalid window packet arrays')
    packet = WindowPacket(
        record['packet_id'], record['side'], tuple(window_ids),
        tuple(_wire_liters(value, positive=True) for value in totals),
        validate_roster(ports), record['evidence'], record['version'],
    )
    validate_window_packet(packet)
    return packet
