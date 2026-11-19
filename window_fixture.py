import json
from pathlib import Path

from issue.envelope import encode_packet
from issue.publish import seal
from issue.transfer import get_packet
from review.admission import admit
from review.reconcile import review
from run_station import describe_range, open_center, open_station
from station.batches import register
from station.measurements import Measurement
from station.revisions import revise


_SAMPLE = Path(__file__).resolve().parent / "samples" / "windows.json"


def load_cases():
    return json.loads(_SAMPLE.read_text(encoding="utf-8"))


def seed(db, case, side):
    profile = case[side]
    root = "private-" + side
    by_window = []
    for index, (batch, total) in enumerate(case["windows"]):
        register(db, batch, profile["ports"], total)
        revisions = {}
        for label, capacities in profile["layouts"][index].items():
            measurements = []
            for port, amount in capacities.items():
                start, end = (root, port) if side == "supply" else (port, root)
                source = f"{side}:{batch}:{label}:{port}"
                measurements.append(Measurement(start, end, amount, source))
            revisions[label] = revise(db, batch, side, root, measurements)
        by_window.append(revisions)
    return tuple(by_window)


def _v1_material(case, side):
    with open_station(":memory:") as station:
        revisions = seed(station, case, side)
        return tuple({label: encode_packet(get_packet(station, seal(station, revision)))
                      for label, revision in window.items()} for window in revisions)


def check_v1_controls(cases):
    for name, case in cases.items():
        supply = _v1_material(case, "supply")
        receive = _v1_material(case, "receive")
        with open_center(":memory:") as center:
            for index, supply_label, receive_label, port, low, high in case["v1_checks"]:
                left = admit(center, supply[index][supply_label])
                right = admit(center, receive[index][receive_label])
                actual = describe_range(review(center, left, right, port).snapshot.result)
                expected = {"status": "feasible", "range": [low, high]}
                if actual != expected:
                    raise AssertionError((name, index, supply_label, receive_label, port, actual, expected))
                print(json.dumps({"case": name, "window": case["windows"][index][0],
                                  "supply": supply_label, "receive": receive_label,
                                  "port": port, **actual}, sort_keys=True))


if __name__ == "__main__":
    check_v1_controls(load_cases())
