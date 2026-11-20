import argparse
import json
import sqlite3
from contextlib import closing, contextmanager
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from capacity.qualification import initialize_qualifications
from issue.catalog import initialize_catalog
from issue.publish import seal
from issue.transfer import get_packet
from issue.window_catalog import initialize_window_catalog
from issue.window_publish import seal_windows
from issue.window_transfer import get_windows_packet
from reports.export import export
from reports.publish import initialize_reports
from reports.readback import get_report, get_window_plan
from review.admission import admit, initialize_admissions
from review.reconcile import review
from review.window_admission import admit_windows, initialize_window_admissions
from review.window_reconcile import review_windows
from reports.window_plans import initialize_window_plans
from station.batches import initialize_batches, register
from station.measurements import Measurement
from station.revisions import initialize_revisions, revise
from station.volumes import parse_liters


_SAMPLE = Path(__file__).resolve().parent / 'samples' / 'shift.json'


def initialize_station(db):
    db.execute('PRAGMA foreign_keys = ON')
    db.execute('BEGIN IMMEDIATE')
    with db:
        initialize_batches(db)
        initialize_revisions(db)
        initialize_qualifications(db)
        initialize_catalog(db)
        initialize_window_catalog(db)


def initialize_center(db):
    db.execute('PRAGMA foreign_keys = ON')
    db.execute('BEGIN IMMEDIATE')
    with db:
        initialize_admissions(db)
        initialize_reports(db)
        initialize_window_admissions(db)
        initialize_window_plans(db)


@contextmanager
def open_station(path):
    with closing(sqlite3.connect(path)) as db:
        initialize_station(db)
        yield db


@contextmanager
def open_center(path):
    with closing(sqlite3.connect(path)) as db:
        initialize_center(db)
        yield db


def load_shift():
    return json.loads(_SAMPLE.read_text(encoding='utf-8'))


def register_shift(db, sample, *, batch_id=None, ports=None, scale=Fraction(1)):
    return register(
        db, sample['batch_id'] if batch_id is None else batch_id,
        sample['ports'] if ports is None else ports, parse_liters(sample['total']) * scale,
    )


def measured_layout(sample, side, layout, *, scale=Fraction(1)):
    return tuple(Measurement(start, end, parse_liters(amount) * scale, source)
                 for start, end, amount, source in sample[side]['layouts'][layout])


def record_layout(db, sample, side, layout, *, batch_id=None, parent_id=None, scale=Fraction(1)):
    return revise(
        db, sample['batch_id'] if batch_id is None else batch_id, side, sample[side]['root'],
        measured_layout(sample, side, layout, scale=scale), parent_id=parent_id,
    )


def describe_range(result):
    amounts = None if result.lower is None else [str(result.lower), str(result.upper)]
    return {'status': result.status, 'range': amounts}


def _require_a(report, lower, upper):
    actual = describe_range(report.snapshot.result)
    expected = {'status': 'feasible', 'range': [lower, upper]}
    if actual != expected:
        raise AssertionError(f'A_PORT_RANGE_MISMATCH actual={actual} expected={expected}')
    return actual


def _window_permissions(case, side, revisions):
    profile = case[side]
    return {
        label: [[revisions[0][front], revisions[1][back]] for front, back in pairs]
        for label, pairs in profile['permissions'].items()
    }


def _plan_summary(plan):
    return {
        'plan_id': plan['plan_id'],
        'receipt_id': plan['receipt_id'],
        'windows': list(plan['window_ids']),
        'ports': list(plan['ports']),
        'status': plan['status'],
        'witness': None if plan['witness'] is None
        else [[str(amount) for amount in vector] for vector in plan['witness']],
    }


def _assert_witness(plan, expected):
    if plan['status'] != 'feasible':
        raise AssertionError(f'expected a feasible plan, got {plan["status"]}')
    actual = tuple(tuple(amount for amount in vector) for vector in plan['witness'])
    totals = plan['totals']
    for window_index, vector in enumerate(actual):
        if len(vector) != 4 or any(amount < 0 for amount in vector) or sum(vector) != totals[window_index]:
            raise AssertionError(f'witness fails the full-batch check for window {window_index}: {vector}')
    if expected is not None and actual != tuple(tuple(Fraction(value) for value in vector) for vector in expected):
        raise AssertionError(f'WITNESS_MISMATCH actual={actual} expected={expected}')
    return actual


def window_demo():
    from window_fixture import load_cases, seed

    cases = load_cases()
    with TemporaryDirectory(prefix='water-windows-') as directory:
        root = Path(directory)
        supply_path, receive_path, center_path = (root / name for name in ('supply.db', 'receive.db', 'center.db'))
        transition = cases['transition']
        window_ids = [batch for batch, _ in transition['windows']]

        with open_station(supply_path) as supply, open_station(receive_path) as receive:
            supply_revisions = seed(supply, transition, 'supply')
            receive_revisions = seed(receive, transition, 'receive')
            supply_permissions = _window_permissions(transition, 'supply', supply_revisions)
            receive_permissions = _window_permissions(transition, 'receive', receive_revisions)
            original_packet = get_windows_packet(supply, seal_windows(supply, window_ids, supply_permissions['original']))
            corrected_packet = get_windows_packet(supply, seal_windows(supply, window_ids, supply_permissions['corrected']))
            ab_packet = get_windows_packet(receive, seal_windows(receive, window_ids, receive_permissions['ab']))
            aa_packet = get_windows_packet(receive, seal_windows(receive, window_ids, receive_permissions['aa']))
            ba_packet = get_windows_packet(receive, seal_windows(receive, window_ids, receive_permissions['ba']))

        # The stations close; the night shift works only from fixed center materials.
        with open_center(center_path) as center:
            supply_original = admit_windows(center, original_packet)
            supply_corrected = admit_windows(center, corrected_packet)
            receive_ab = admit_windows(center, ab_packet)
            receive_aa = admit_windows(center, aa_packet)
            receive_ba = admit_windows(center, ba_packet)

            blocked = review_windows(center, supply_original, receive_ab)
            if blocked['status'] != 'infeasible' or blocked['witness'] is not None:
                raise AssertionError(f'expected an infeasible plan, got {blocked}')

            shifted = review_windows(center, supply_corrected, receive_ab)
            shifted_witness = _assert_witness(shifted, (('1', '0', '0', '0'), ('0', '1', '0', '0')))

            only_a = review_windows(center, supply_corrected, receive_aa)
            _assert_witness(only_a, (('1', '0', '0', '0'), ('1', '0', '0', '0')))

            still_blocked = review_windows(center, supply_corrected, receive_ba)
            if still_blocked['status'] != 'infeasible':
                raise AssertionError(f'expected the ba permission to stay infeasible, got {still_blocked}')
            plan_ids = [plan['plan_id'] for plan in (blocked, shifted, only_a, still_blocked)]

        # The fractional case exercises exact rational amounts and a reversed roster.
        fractional = cases['fractional']
        fractional_windows = [batch for batch, _ in fractional['windows']]
        with open_station(root / 'frac-supply.db') as supply, open_station(root / 'frac-receive.db') as receive:
            supply_revisions = seed(supply, fractional, 'supply')
            receive_revisions = seed(receive, fractional, 'receive')
            fractional_supply = get_windows_packet(
                supply, seal_windows(supply, fractional_windows,
                                     _window_permissions(fractional, 'supply', supply_revisions)['one']))
            fractional_receive = get_windows_packet(
                receive, seal_windows(receive, fractional_windows,
                                      _window_permissions(fractional, 'receive', receive_revisions)['one']))
        with open_center(center_path) as center:
            fractional_plan = review_windows(
                center, admit_windows(center, fractional_supply), admit_windows(center, fractional_receive))
            fractional_witness = _assert_witness(fractional_plan, None)
            early, late = fractional_witness
            if not (Fraction(1, 4) <= early[0] <= Fraction(3, 4)) or early[2:] != (Fraction(0), Fraction(0)):
                raise AssertionError(f'front window witness outside the allowed range: {early}')
            if late != (Fraction(0), Fraction(2), Fraction(0), Fraction(0)):
                raise AssertionError(f'back window witness must be fixed on B: {late}')
            plan_ids.append(fractional_plan['plan_id'])
            fractional_id = fractional_plan['plan_id']

        # Reopen the center and collect every stored plan and receipt by number.
        with open_center(center_path) as center:
            reopened = [get_window_plan(center, plan_id) for plan_id in plan_ids]
            summaries = [_plan_summary(plan) for plan in reopened]
            if get_window_plan(center, fractional_id) != fractional_plan:
                raise AssertionError('the fractional plan changed after the center reopened')
            print(json.dumps({
                'case': 'transition',
                'original_against_ab': summaries[0],
                'corrected_against_ab': summaries[1],
                'corrected_against_aa': summaries[2],
                'corrected_against_ba': summaries[3],
                'shifted_witness': [[str(amount) for amount in vector] for vector in shifted_witness],
            }, sort_keys=True))
            print(json.dumps({'case': 'fractional', 'plan': summaries[4]}, sort_keys=True))


def demo():
    sample = load_shift()
    with TemporaryDirectory(prefix='water-shift-') as directory:
        path = Path(directory)
        supply_path, receive_path, center_path = (path / name for name in ('supply.db', 'receive.db', 'center.db'))
        with open_station(supply_path) as supply, open_station(receive_path) as receive:
            register_shift(supply, sample)
            register_shift(receive, sample)
            original = record_layout(supply, sample, 'supply', 'initial')
            original_packet = get_packet(supply, seal(supply, original))
            receive_revision = record_layout(receive, sample, 'receive', 'b_required')
            receive_packet = get_packet(receive, seal(receive, receive_revision))

        with open_center(center_path) as center:
            old_id = admit(center, original_packet)
            receive_id = admit(center, receive_packet)
            old_report = review(center, old_id, receive_id, 'A')
            before = _require_a(old_report, '0', '0')
            old_export = export(center, old_report.report_id)

        with open_station(supply_path) as supply:
            changed = record_layout(supply, sample, 'supply', 'correction', parent_id=original)
            changed_packet = get_packet(supply, seal(supply, changed))

        with open_center(center_path) as center:
            new_id = admit(center, changed_packet)
            new_report = review(center, new_id, receive_id, 'A')
            after = _require_a(new_report, '0', '1')

        with open_center(center_path) as center:
            archived = get_report(center, old_report.report_id)
            retained = archived == old_report and export(center, archived.report_id) == old_export
            if not retained or get_report(center, new_report.report_id) != new_report:
                raise AssertionError('the signed reports changed on reopen')
            print(json.dumps({
                'batch': sample['batch_id'], 'port': archived.snapshot.port, 'unit': 'L',
                'before': before, 'after': after, 'original_retrieved': retained,
                'old_report': archived.report_id, 'new_report': new_report.report_id,
                'old_receipt': archived.receipt.receipt_id, 'new_receipt': new_report.receipt.receipt_id,
            }, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description='Reuse water station handover')
    parser.add_argument('command', choices=['demo'])
    parser.parse_args()
    demo()
    window_demo()


if __name__ == '__main__':
    main()
