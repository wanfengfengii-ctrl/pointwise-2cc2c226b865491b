from capacity.flow import max_flow
from capacity.model import FlowNetwork
from station.volumes import encode_liters


def build_boundary_evidence(network: FlowNetwork) -> dict:
    # Each root cut bounds the total demand on the ports outside that cut.
    # Conversely, satisfying every subset bound makes every cut large enough
    # after adding outlet arcs whose capacities are the requested port amounts.
    limits = []
    full_mask = (1 << len(network.ports)) - 1
    for mask in range(1, full_mask):
        ports = [port for index, port in enumerate(network.ports) if mask & (1 << index)]
        sink, arcs = network.with_outlets(ports)
        upper = min(network.total, max_flow(arcs, network.root, sink).value)
        limits.append([mask, encode_liters(upper)])
    return {'limits': limits}
