import sqlite3
from uuid import uuid4

from capacity.qualification import qualify
from issue.window_catalog import insert_window_packet
from issue.window_envelope import WindowPacket
from station.batches import get_batch
from station.ports import PortRoster, canonical_port_ids
from station.revisions import get_revision


def _window_ids(window_ids) -> tuple[str, str]:
    if isinstance(window_ids, str) or not isinstance(window_ids, (list, tuple)):
        raise ValueError('window_ids must be an ordered list or tuple of two batch ids')
    ids = tuple(window_ids)
    if len(ids) != 2 or len(set(ids)) != 2:
        raise ValueError('window_ids must contain two distinct batch ids')
    if any(not isinstance(value, str) or not value or value.strip() != value for value in ids):
        raise ValueError('window_ids must be nonblank identities without surrounding whitespace')
    return ids


def _permission_pairs(allowed_pairs) -> tuple[tuple[str, str], ...]:
    if isinstance(allowed_pairs, str) or not isinstance(allowed_pairs, (list, tuple)) or not allowed_pairs:
        raise ValueError('allowed_pairs must be a nonempty ordered list of revision pairs')
    pairs = []
    seen = set()
    for pair in allowed_pairs:
        if isinstance(pair, str) or not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError('each permission must be an ordered pair of two revision ids')
        if any(not isinstance(revision_id, str) or not revision_id or revision_id.strip() != revision_id
               for revision_id in pair):
            raise ValueError('revision ids must be nonblank identities without surrounding whitespace')
        normalized = tuple(pair)
        if normalized not in seen:
            seen.add(normalized)
            pairs.append(normalized)
    return tuple(pairs)


def _remap_limits(limits, source_ports: tuple[str, ...], packet_ports: tuple[str, ...]) -> list:
    """Re-express boundary masks from a batch roster into the packet roster.

    The qualification evidence numbers mask bits by the revision's own batch
    roster.  Each named port is looked up in that roster and re-numbered by its
    position in the packet's shared four-port coordinate order.
    """
    target_index = {port: position for position, port in enumerate(packet_ports)}
    remapped = []
    for mask, encoded in limits:
        new_mask = 0
        for local_position, port in enumerate(source_ports):
            if mask & (1 << local_position):
                new_mask |= 1 << target_index[port]
        remapped.append([new_mask, encoded])
    # build_boundary_evidence enumerates masks in increasing numeric order;
    # remapping may reorder them, so keep a stable, unique ordering in the packet.
    return sorted(remapped, key=lambda row: row[0])


def seal_windows(db: sqlite3.Connection, window_ids, allowed_pairs) -> str:
    """Seal one station side's two ordered windows under its directed permissions."""
    ordered_windows = _window_ids(window_ids)
    pairs = _permission_pairs(allowed_pairs)

    db.execute('BEGIN IMMEDIATE')
    with db:
        batches = tuple(get_batch(db, batch_id) for batch_id in ordered_windows)
        shared = canonical_port_ids(batches[0].ports)
        if any(canonical_port_ids(batch.ports) != shared for batch in batches[1:]):
            raise ValueError('the two windows must share the same four port identities')
        # One shared coordinate order for every limit in this packet; the
        # canonical (sorted) port order is identical across both stations.
        packet_ports = shared

        # Gear order is the order in which each window's candidate revisions
        # first appear in the permission table issued at this sealing.
        ordered_revisions = ([], [])
        for pair in pairs:
            for window_index, revision_id in enumerate(pair):
                if revision_id not in ordered_revisions[window_index]:
                    ordered_revisions[window_index].append(revision_id)
        ordered_revisions = tuple(tuple(group) for group in ordered_revisions)
        sides = set()
        qualifications = {}
        for window_index, revision_ids in enumerate(ordered_revisions):
            for revision_id in revision_ids:
                revision = get_revision(db, revision_id)
                if revision.batch_id != ordered_windows[window_index]:
                    raise ValueError(
                        f'permission revision {revision_id} does not belong to window '
                        f'{ordered_windows[window_index]}'
                    )
                sides.add(revision.side)
                qualification = qualify(db, revision_id)
                limits = _remap_limits(
                    qualification.evidence['limits'],
                    batches[window_index].ports.ids,
                    packet_ports,
                )
                qualifications[(window_index, revision_id)] = limits
        shared_revisions = set(ordered_revisions[0]) & set(ordered_revisions[1])
        if shared_revisions:
            raise ValueError(
                f'a permission revision cannot serve both windows: {sorted(shared_revisions)[0]}'
            )
        if len(sides) != 1:
            raise ValueError('all permission revisions must belong to the same station side')
        side = sides.pop()

        capabilities = []
        for window_index, batch in enumerate(batches):
            for revision_id in ordered_revisions[window_index]:
                capabilities.append({
                    'window_id': ordered_windows[window_index],
                    'revision_id': revision_id,
                    'roster': list(packet_ports),
                    'limits': qualifications[(window_index, revision_id)],
                })
        evidence = {
            'capabilities': capabilities,
            'permissions': [list(pair) for pair in pairs],
        }
        packet_id = 'window-' + uuid4().hex
        packet = WindowPacket(
            packet_id, side, ordered_windows, tuple(batch.total for batch in batches),
            PortRoster(packet_ports), evidence,
        )
        insert_window_packet(db, packet, pairs)
    return packet_id
