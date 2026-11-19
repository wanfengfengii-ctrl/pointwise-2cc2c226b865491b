from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations

from capacity.linear import solve_linear_system


@dataclass(frozen=True)
class PortRange:
    status: str
    lower: Fraction | None
    upper: Fraction | None

    def __post_init__(self):
        if self.status == 'infeasible' and self.lower is None and self.upper is None:
            return
        if (self.status == 'feasible' and isinstance(self.lower, Fraction)
                and isinstance(self.upper, Fraction) and 0 <= self.lower <= self.upper):
            return
        raise ValueError('invalid port range')


def compute_port_range(total: Fraction, inequalities, target_index: int) -> PortRange:
    """Optimize a coordinate over a nonnegative fixed-total four-dimensional section."""
    if not 0 <= target_index < 4:
        raise ValueError('port coordinate is out of range')
    nonnegative = [(tuple(-int(i == j) for i in range(4)), Fraction(0)) for j in range(4)]
    bounds = {}
    for normal, upper in (*inequalities, *nonnegative):
        a, b, c, d = normal
        # Eliminate the fourth coordinate using the full-batch equality.
        row = (a - d, b - d, c - d)
        limit = upper - d * total
        if not any(row):
            if limit < 0:
                return PortRange('infeasible', None, None)
            continue
        if row not in bounds or limit < bounds[row]:
            bounds[row] = limit

    seen = set()
    endpoints = []
    # The nonnegativity rows bound the section. At each vertex there are
    # three independent active rows even when the feasible region is lower-dimensional.
    for active in combinations(bounds, 3):
        point = solve_linear_system(active, (bounds[row] for row in active))
        if point is None or point in seen:
            continue
        seen.add(point)
        if any(sum(coefficient * value for coefficient, value in zip(row, point) if coefficient) > limit
               for row, limit in bounds.items()):
            continue
        four_ports = (*point, total - sum(point))
        endpoints.append(four_ports[target_index])
    if not endpoints:
        return PortRange('infeasible', None, None)
    return PortRange('feasible', min(endpoints), max(endpoints))
