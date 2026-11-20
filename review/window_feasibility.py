from fractions import Fraction
from itertools import combinations

from issue.window_envelope import WindowPacket, read_limits


def capability_limits(packet: WindowPacket, window_index: int, revision_id: str):
    window_id = packet.window_ids[window_index]
    for entry in packet.evidence['capabilities']:
        if entry['window_id'] == window_id and entry['revision_id'] == revision_id:
            return read_limits({'limits': entry['limits']}, packet.totals[window_index])
    raise ValueError('permission names a revision absent from the window capabilities')


def _capability_rows(packet: WindowPacket, window_index: int, revision_id: str,
                     common_ports: tuple[str, ...]):
    coordinates = {port: position for position, port in enumerate(common_ports)}
    for mask, upper in capability_limits(packet, window_index, revision_id):
        coefficients = [0] * 4
        for local_position, port in enumerate(packet.ports.ids):
            if mask & (1 << local_position):
                coefficients[coordinates[port]] = 1
        yield tuple(coefficients), upper


def _square_solution(rows, values):
    """Exact solution of a full-rank square rational system, else None."""
    width = len(rows)
    matrix = [[Fraction(rows[row][column]) for column in range(width)] for row in range(width)]
    rhs = [Fraction(value) for value in values]
    for column in range(width):
        pivot = next((row for row in range(column, width) if matrix[row][column]), None)
        if pivot is None:
            return None
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        rhs[column], rhs[pivot] = rhs[pivot], rhs[column]
        divisor = matrix[column][column]
        matrix[column] = [entry / divisor for entry in matrix[column]]
        rhs[column] /= divisor
        for row in range(width):
            if row == column or not matrix[row][column]:
                continue
            factor = matrix[row][column]
            matrix[row] = [entry - factor * term for entry, term in zip(matrix[row], matrix[column])]
            rhs[row] -= factor * rhs[column]
    return tuple(rhs)


def feasible_vector(total: Fraction, inequalities) -> tuple[Fraction, Fraction, Fraction, Fraction] | None:
    """Return any nonnegative full-batch four-port vector satisfying every bound.

    The full-batch equality removes one coordinate, leaving a three-variable
    rational polytope.  Every vertex is the unique solution of an independent
    square active set; pinned sections appear as smaller square systems with
    the remaining coordinates fixed at zero.  Enumerating every active-set
    size finds a witness whenever the section is nonempty.
    """
    bounds = {}
    for normal, upper in inequalities:
        a, b, c, d = normal
        # x_d = total - (x_a + x_b + x_c) folds the fourth coordinate away.
        row = (Fraction(a - d), Fraction(b - d), Fraction(c - d))
        limit = Fraction(upper) - Fraction(d) * total
        if not any(row):
            if limit < 0:
                return None
            continue
        if row not in bounds or limit < bounds[row]:
            bounds[row] = limit

    def valid(point):
        if any(value < 0 for value in point) or sum(point) > total:
            return False
        return all(
            sum(coefficient * value for coefficient, value in zip(row, point) if coefficient) <= limit
            for row, limit in bounds.items()
        )

    ordered = tuple(bounds)
    origin = (Fraction(0), Fraction(0), Fraction(0))
    if valid(origin):
        return (*origin, total)
    seen = {origin}
    for size in range(1, 4):
        for active in combinations(ordered, size):
            for columns in combinations(range(3), size):
                square = tuple(tuple(row[column] for column in columns) for row in active)
                partial = _square_solution(square, tuple(bounds[row] for row in active))
                if partial is None:
                    continue
                point = [Fraction(0), Fraction(0), Fraction(0)]
                for column, value in zip(columns, partial):
                    point[column] = value
                point = tuple(point)
                if point in seen:
                    continue
                seen.add(point)
                if valid(point):
                    return (*point, total - sum(point))
    return None


def shared_permissions(supply: WindowPacket, receive: WindowPacket):
    """Candidate gear combinations supported by a granted permission on each side.

    Revision ids are station-local and the two stations' gears need not have
    matching capacities; the center intersects their full-batch vector sets by
    combining both sets of boundary limits.  A candidate is therefore the
    Cartesian product of every granted supply permission and every granted
    receive permission, ordered supply-first for deterministic signing.
    """
    matched = []
    for supply_front, supply_back in supply.evidence['permissions']:
        for receive_front, receive_back in receive.evidence['permissions']:
            matched.append((supply_front, supply_back, receive_front, receive_back))
    return tuple(matched)


def reconcile_windows(supply: WindowPacket, receive: WindowPacket):
    """Return (status, witness) for one permission pair supporting both windows."""
    common_ports = tuple(sorted(supply.ports.ids))
    for supply_front, supply_back, receive_front, receive_back in shared_permissions(supply, receive):
        witness = []
        for window_index, total in enumerate(supply.totals):
            supply_revision = supply_front if window_index == 0 else supply_back
            receive_revision = receive_front if window_index == 0 else receive_back
            rows = [
                *_capability_rows(supply, window_index, supply_revision, common_ports),
                *_capability_rows(receive, window_index, receive_revision, common_ports),
            ]
            candidate = feasible_vector(total, rows)
            if candidate is None:
                break
            witness.append(candidate)
        else:
            return 'feasible', tuple(witness)
    return 'infeasible', None
