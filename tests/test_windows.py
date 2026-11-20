import json
import sqlite3
import unittest
from fractions import Fraction

from issue.window_envelope import decode_window_packet, encode_window_packet
from issue.window_publish import seal_windows
from issue.window_transfer import get_windows_packet
from reports.readback import get_window_plan
from review.window_admission import admit_windows, get_accepted_window
from review.window_feasibility import feasible_vector
from review.window_reconcile import review_windows
from run_station import open_center, open_station
from tests.support import ShiftCase, fail_commit_on_insert, rows
from window_fixture import label_permissions, load_cases, seal_labeled_windows, seed


class WindowsCase(ShiftCase):
    def setUp(self):
        super().setUp()
        self.cases = load_cases()
        self.transition = self.cases['transition']
        self.fractional = self.cases['fractional']

    def seed_side(self, case, side, db):
        return seed(db, case, side)

    def seal_side(self, db, case, side, revisions, label):
        return seal_labeled_windows(db, case, side, revisions, label)

    def admit_pair(self, supply_material, receive_material):
        return admit_windows(self.center, supply_material), admit_windows(self.center, receive_material)


class TestWindowSealing(WindowsCase):
    def test_sample_transition_verdicts(self):
        case = self.transition
        window_ids = [batch for batch, _ in case['windows']]
        with open_station(self.supply_path) as supply, open_station(self.receive_path) as receive:
            supply_revisions = seed(supply, case, 'supply')
            receive_revisions = seed(receive, case, 'receive')
            supply_permissions = label_permissions(case, 'supply', supply_revisions)
            receive_permissions = label_permissions(case, 'receive', receive_revisions)
            original = get_windows_packet(supply, seal_windows(supply, window_ids, supply_permissions['original']))
            corrected = get_windows_packet(supply, seal_windows(supply, window_ids, supply_permissions['corrected']))
            ab = get_windows_packet(receive, seal_windows(receive, window_ids, receive_permissions['ab']))
            aa = get_windows_packet(receive, seal_windows(receive, window_ids, receive_permissions['aa']))
            ba = get_windows_packet(receive, seal_windows(receive, window_ids, receive_permissions['ba']))

        with open_center(self.center_path) as center:
            original_id = admit_windows(center, original)
            corrected_id = admit_windows(center, corrected)
            ab_id = admit_windows(center, ab)
            aa_id = admit_windows(center, aa)
            ba_id = admit_windows(center, ba)

            blocked = review_windows(center, original_id, ab_id)
            self.assertEqual(blocked['status'], 'infeasible')
            self.assertIsNone(blocked['witness'])

            shifted = review_windows(center, corrected_id, ab_id)
            self.assertEqual(shifted['status'], 'feasible')
            self.assertEqual(shifted['witness'],
                             ((Fraction(1), Fraction(0), Fraction(0), Fraction(0)),
                              (Fraction(0), Fraction(1), Fraction(0), Fraction(0))))

            only_a = review_windows(center, corrected_id, aa_id)
            self.assertEqual(only_a['status'], 'feasible')
            self.assertEqual(only_a['witness'],
                             ((Fraction(1), Fraction(0), Fraction(0), Fraction(0)),
                              (Fraction(1), Fraction(0), Fraction(0), Fraction(0))))

            still_blocked = review_windows(center, corrected_id, ba_id)
            self.assertEqual(still_blocked['status'], 'infeasible')
            self.assertIsNone(still_blocked['witness'])

    def test_fractional_rational_witness(self):
        case = self.fractional
        with open_station(self.supply_path) as supply, open_station(self.receive_path) as receive:
            supply_revisions = seed(supply, case, 'supply')
            receive_revisions = seed(receive, case, 'receive')
            supply_material = self.seal_side(supply, case, 'supply', supply_revisions, 'one')
            receive_material = self.seal_side(receive, case, 'receive', receive_revisions, 'one')
        with open_center(self.center_path) as center:
            plan = review_windows(center, admit_windows(center, supply_material),
                                  admit_windows(center, receive_material))
        self.assertEqual(plan['status'], 'feasible')
        early, late = plan['witness']
        self.assertEqual(plan['totals'], (Fraction(1), Fraction(2)))
        self.assertEqual(late, (Fraction(0), Fraction(2), Fraction(0), Fraction(0)))
        self.assertEqual(early[2:], (Fraction(0), Fraction(0)))
        self.assertGreaterEqual(early[0], Fraction(1, 4))
        self.assertLessEqual(early[0], Fraction(3, 4))
        self.assertEqual(early[0] + early[1], Fraction(1))
        for vector, total in zip(plan['witness'], plan['totals']):
            self.assertEqual(sum(vector), total)
            self.assertTrue(all(isinstance(amount, Fraction) and amount >= 0 for amount in vector))

    def test_packet_structure_and_private_material_kept_local(self):
        case = self.transition
        with open_station(self.supply_path) as supply:
            revisions = seed(supply, case, 'supply')
            material = self.seal_side(supply, case, 'supply', revisions, 'corrected')
        record = json.loads(material)
        self.assertEqual(record['version'], 2)
        self.assertEqual(set(record), {'version', 'packet_id', 'side', 'window_ids',
                                       'totals', 'ports', 'evidence'})
        self.assertEqual(record['side'], 'supply')
        self.assertEqual(record['window_ids'], ['early', 'late'])
        self.assertEqual(record['totals'], ['1', '1'])
        self.assertEqual(record['ports'], ['A', 'B', 'C', 'D'])
        packet = decode_window_packet(material)
        self.assertEqual(encode_window_packet(packet), material)
        self.assertEqual(packet.ports.ids, tuple(sorted(packet.ports.ids)))
        # Private root, internal nodes and measurement sources never leave.
        self.assertNotIn('private-supply', material)
        for window in revisions:
            for revision_id in window.values():
                self.assertNotIn(revision_id[:8], material.replace(revision_id, ''))
        # Only the revision ids referenced by capabilities and permissions appear.
        referenced = {entry['revision_id'] for entry in packet.evidence['capabilities']}
        referenced.update(revision_id for pair in packet.evidence['permissions'] for revision_id in pair)
        self.assertEqual(referenced, {revision_id for window in revisions for revision_id in window.values()})

    def test_reverse_roster_packet_uses_canonical_ports(self):
        case = self.fractional
        with open_station(self.receive_path) as receive:
            revisions = seed(receive, case, 'receive')
            material = self.seal_side(receive, case, 'receive', revisions, 'one')
        packet = decode_window_packet(material)
        self.assertEqual(packet.side, 'receive')
        self.assertEqual(packet.ports.ids, ('A', 'B', 'C', 'D'))
        for entry in packet.evidence['capabilities']:
            self.assertEqual(entry['roster'], ['A', 'B', 'C', 'D'])

    def test_distinct_packets_for_distinct_permission_tables(self):
        case = self.transition
        window_ids = [batch for batch, _ in case['windows']]
        with open_station(self.supply_path) as supply:
            revisions = seed(supply, case, 'supply')
            permissions = label_permissions(case, 'supply', revisions)
            first = seal_windows(supply, window_ids, permissions['original'])
            second = seal_windows(supply, window_ids, permissions['corrected'])
            self.assertNotEqual(first, second)
            # The old packet remains retrievable by its original id.
            first_material = get_windows_packet(supply, first)
            second_material = get_windows_packet(supply, second)
            self.assertNotEqual(first_material, second_material)
            self.assertEqual(json.loads(first_material)['packet_id'], first)
            self.assertEqual(rows(supply, 'window_pickups'), 2)
            self.assertEqual(rows(supply, 'window_packets'), 2)

    def test_duplicate_permission_pair_collapses(self):
        case = self.transition
        window_ids = [batch for batch, _ in case['windows']]
        with open_station(self.supply_path) as supply:
            revisions = seed(supply, case, 'supply')
            pairs = label_permissions(case, 'supply', revisions)['original']
            packet_id = seal_windows(supply, window_ids, [*pairs, pairs[0], tuple(pairs[0])])
            packet = decode_window_packet(get_windows_packet(supply, packet_id))
            self.assertEqual(packet.evidence['permissions'], [list(pair) for pair in pairs])

    def test_invalid_sealing_inputs(self):
        case = self.transition
        window_ids = [batch for batch, _ in case['windows']]
        with open_station(self.supply_path) as supply:
            revisions = seed(supply, case, 'supply')
            pairs = label_permissions(case, 'supply', revisions)['corrected']
            valid_windows = window_ids
            bad_windows = (
                ['only-one'], ['early', 'early'], ('early', ''), 'early', [],
            )
            for bad in bad_windows:
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    seal_windows(supply, bad, pairs)
            bad_pairs = (
                [], (), [['only-one']], [['a', 'b', 'c']],
                [['', pairs[0][1]]], [(pairs[0][0], '')], 'x',
            )
            for bad in bad_pairs:
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    seal_windows(supply, valid_windows, bad)
            # A well-formed pair naming an unknown revision is a KeyError.
            with self.assertRaises(KeyError):
                seal_windows(supply, valid_windows, [['rev-missing', pairs[0][1]]])
            self.assertEqual(rows(supply, 'window_packets'), 0)

    def test_unknown_and_misassigned_revisions(self):
        case = self.transition
        window_ids = [batch for batch, _ in case['windows']]
        with open_station(self.supply_path) as supply:
            revisions = seed(supply, case, 'supply')
            pairs = label_permissions(case, 'supply', revisions)['corrected']
            # Well-formed pair referencing a revision id that does not exist.
            with self.assertRaises(KeyError):
                seal_windows(supply, window_ids, [['rev-missing', pairs[0][1]]])
            # A back-window revision placed on the front position.
            swapped = [[pairs[0][1], pairs[0][1]]]
            with self.assertRaisesRegex(ValueError, 'does not belong to window'):
                seal_windows(supply, window_ids, swapped)
            self.assertEqual(rows(supply, 'window_packets'), 0)

    def test_revision_cannot_serve_both_windows(self):
        case = self.transition
        window_ids = [batch for batch, _ in case['windows']]
        with open_station(self.supply_path) as supply:
            revisions = seed(supply, case, 'supply')
            front = revisions[0]['x']
            back = revisions[1]['a']
            # Register the front revision against the back window too by
            # constructing an impossible cross-window pair directly.
            with self.assertRaisesRegex(ValueError, 'does not belong to window'):
                seal_windows(supply, window_ids, [[front, front]])
            self.assertNotEqual(front, back)

    def test_sealing_failure_is_atomic_and_retriable(self):
        case = self.transition
        window_ids = [batch for batch, _ in case['windows']]
        with open_station(self.supply_path) as supply:
            revisions = seed(supply, case, 'supply')
            pairs = label_permissions(case, 'supply', revisions)['corrected']
            fail_commit_on_insert(supply, 'window_packets')
            with self.assertRaises(sqlite3.IntegrityError):
                seal_windows(supply, window_ids, pairs)
        with open_station(self.supply_path) as db:
            self.assertEqual(rows(db, 'window_packets'), 0)
            self.assertEqual(rows(db, 'window_permissions'), 0)
            self.assertEqual(rows(db, 'commit_attempts'), 0)
            db.execute('DROP TRIGGER require_commit_permit')
            packet_id = seal_windows(db, window_ids, pairs)
            self.assertEqual(decode_window_packet(get_windows_packet(db, packet_id)).side, 'supply')


class TestWindowAdmission(WindowsCase):
    def _material(self, side='supply', label='corrected'):
        case = self.transition
        with open_station(self.supply_path) as supply, open_station(self.receive_path) as receive:
            if side == 'supply':
                revisions = seed(supply, case, 'supply')
                return self.seal_side(supply, case, 'supply', revisions, label)
            revisions = seed(receive, case, 'receive')
            return self.seal_side(receive, case, 'receive', revisions, label)

    def test_admit_returns_opaque_id_and_persists(self):
        material = self._material()
        with open_center(self.center_path) as center:
            accepted_id = admit_windows(center, material)
            self.assertIsInstance(accepted_id, str)
            accepted = get_accepted_window(center, accepted_id)
            self.assertEqual(accepted.accepted_id, accepted_id)
            self.assertEqual(encode_window_packet(accepted.packet), material)
            # Re-admitting the same fixed material creates a new admission id.
            self.assertNotEqual(admit_windows(center, material), accepted_id)

    def test_admit_accepts_bytes_and_rejects_bad_material(self):
        material = self._material()
        with open_center(self.center_path) as center:
            self.assertIsInstance(admit_windows(center, material.encode('utf-8')), str)
            bad_records = [
                b'not json', '', '{"version": 1}',
                json.dumps({**json.loads(material), 'version': 1}),
                json.dumps({**json.loads(material), 'version': 3}),
                json.dumps({**json.loads(material), 'side': 'monitor'}),
                json.dumps({**json.loads(material), 'window_ids': ['only']}),
            ]
            for bad in bad_records:
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    admit_windows(center, bad)
            self.assertEqual(rows(center, 'accepted_window_packets'), 1)

    def test_unknown_admission_and_plan_ids(self):
        with open_center(self.center_path) as center:
            with self.assertRaises(KeyError):
                get_accepted_window(center, 'win-admit-unknown')
            with self.assertRaises(KeyError):
                get_window_plan(center, 'window-plan-unknown')
            with self.assertRaises(ValueError):
                review_windows(center, '', 'whatever')


class TestWindowReconcile(WindowsCase):
    def _seal_both(self, case, supply_label, receive_label):
        with open_station(self.supply_path) as supply, open_station(self.receive_path) as receive:
            supply_revisions = seed(supply, case, 'supply')
            receive_revisions = seed(receive, case, 'receive')
            supply_material = self.seal_side(supply, case, 'supply', supply_revisions, supply_label)
            receive_material = self.seal_side(receive, case, 'receive', receive_revisions, receive_label)
        return supply_material, receive_material

    def test_plan_fields_and_provenance(self):
        supply_material, receive_material = self._seal_both(self.transition, 'corrected', 'ab')
        with open_center(self.center_path) as center:
            supply_id = admit_windows(center, supply_material)
            receive_id = admit_windows(center, receive_material)
            plan = review_windows(center, supply_id, receive_id)
        supply_packet = decode_window_packet(supply_material)
        receive_packet = decode_window_packet(receive_material)
        self.assertEqual(plan['supply_id'], supply_id)
        self.assertEqual(plan['receive_id'], receive_id)
        self.assertEqual(plan['supply_packet_id'], supply_packet.packet_id)
        self.assertEqual(plan['receive_packet_id'], receive_packet.packet_id)
        self.assertEqual(plan['window_ids'], tuple(supply_packet.window_ids))
        self.assertEqual(plan['totals'], supply_packet.totals)
        self.assertEqual(plan['ports'], ('A', 'B', 'C', 'D'))
        self.assertTrue(plan['plan_id'].startswith('window-plan-'))
        self.assertTrue(plan['receipt_id'].startswith('window-receipt-'))
        self.assertEqual(len(plan['witness']), 2)
        self.assertEqual(all(len(vector) == 4 for vector in plan['witness']), True)

    def test_wrong_side_and_mismatched_selection(self):
        supply_material, receive_material = self._seal_both(self.transition, 'corrected', 'ab')
        with open_center(self.center_path) as center:
            supply_id = admit_windows(center, supply_material)
            receive_id = admit_windows(center, receive_material)
            with self.assertRaisesRegex(ValueError, 'supply window packet'):
                review_windows(center, receive_id, supply_id)
            # Different window batch identities.
            other_case = self.fractional
            other_supply, other_receive = self._seal_both(other_case, 'one', 'one')
            other_id = admit_windows(center, other_supply)
            with self.assertRaisesRegex(ValueError, 'disagree on the ordered window'):
                review_windows(center, other_id, receive_id)
            self.assertEqual(rows(center, 'window_plans'), 0)
            self.assertEqual(rows(center, 'window_plan_receipts'), 0)

    def test_unknown_admission_id_is_key_error(self):
        supply_material, receive_material = self._seal_both(self.transition, 'corrected', 'ab')
        with open_center(self.center_path) as center:
            supply_id = admit_windows(center, supply_material)
            admit_windows(center, receive_material)
            with self.assertRaises(KeyError):
                review_windows(center, supply_id, 'win-admit-missing')

    def test_reopen_center_readback(self):
        supply_material, receive_material = self._seal_both(self.transition, 'original', 'ab')
        with open_center(self.center_path) as center:
            plan = review_windows(center, admit_windows(center, supply_material),
                                  admit_windows(center, receive_material))
            self.assertEqual(plan['status'], 'infeasible')
        with open_center(self.center_path) as reopened:
            stored = get_window_plan(reopened, plan['plan_id'])
            self.assertEqual(stored, plan)
            self.assertEqual(stored['receipt_id'], plan['receipt_id'])
            self.assertIsNone(stored['witness'])

    def test_infeasible_plan_is_still_published_with_receipt(self):
        supply_material, receive_material = self._seal_both(self.transition, 'original', 'ab')
        with open_center(self.center_path) as center:
            before_plans = rows(center, 'window_plans')
            plan = review_windows(center, admit_windows(center, supply_material),
                                  admit_windows(center, receive_material))
            self.assertEqual(plan['status'], 'infeasible')
            self.assertIsNone(plan['witness'])
            self.assertEqual(rows(center, 'window_plans'), before_plans + 1)
            self.assertEqual(rows(center, 'window_plan_receipts'), before_plans + 1)
            stored = get_window_plan(center, plan['plan_id'])
            self.assertEqual(stored, plan)
            self.assertTrue(stored['receipt_id'])

    def test_review_failure_is_atomic_and_retriable(self):
        supply_material, receive_material = self._seal_both(self.transition, 'corrected', 'ab')
        with open_center(self.center_path) as center:
            supply_id = admit_windows(center, supply_material)
            receive_id = admit_windows(center, receive_material)
            before = rows(center, 'window_plans')
            fail_commit_on_insert(center, 'window_plans')
            with self.assertRaises(sqlite3.IntegrityError):
                review_windows(center, supply_id, receive_id)
        with open_center(self.center_path) as center:
            self.assertEqual(rows(center, 'window_plans'), before)
            self.assertEqual(rows(center, 'window_plan_receipts'), 0)
            self.assertEqual(rows(center, 'commit_attempts'), 0)
            center.execute('DROP TRIGGER require_commit_permit')
            plan = review_windows(center, supply_id, receive_id)
            self.assertEqual(plan['status'], 'feasible')
            self.assertEqual(get_window_plan(center, plan['plan_id']), plan)


class TestFeasibilitySolver(unittest.TestCase):
    def test_vertex_and_pinned_sections(self):
        a_only = [
            ((1, 0, 0, 0), 1), ((0, 1, 0, 0), 0), ((1, 1, 0, 0), 1),
            ((0, 0, 1, 0), 0), ((1, 0, 1, 0), 1), ((0, 1, 1, 0), 0),
            ((1, 1, 1, 0), 1), ((0, 0, 0, 1), 0), ((1, 0, 0, 1), 1),
            ((0, 1, 0, 1), 0), ((1, 1, 0, 1), 1), ((0, 0, 1, 1), 0),
            ((1, 0, 1, 1), 1), ((0, 1, 1, 1), 0),
        ]
        self.assertEqual(feasible_vector(Fraction(1), a_only),
                         (Fraction(1), Fraction(0), Fraction(0), Fraction(0)))
        b_rows = [
            ((1, 0, 0, 0), 0), ((0, 1, 0, 0), 1), ((1, 1, 0, 0), 1),
            ((0, 0, 1, 0), 0), ((1, 0, 1, 0), 0), ((0, 1, 1, 0), 1),
            ((1, 1, 1, 0), 1), ((0, 0, 0, 1), 0), ((1, 0, 0, 1), 0),
            ((0, 1, 0, 1), 1), ((1, 1, 0, 1), 1), ((0, 0, 1, 1), 0),
            ((1, 0, 1, 1), 0), ((0, 1, 1, 1), 1),
        ]
        self.assertEqual(feasible_vector(Fraction(1), b_rows),
                         (Fraction(0), Fraction(1), Fraction(0), Fraction(0)))

    def test_rational_range_and_infeasibility(self):
        ranged = [
            ((1, 0, 0, 0), Fraction(3, 4)), ((0, 1, 0, 0), 1),
            ((1, 1, 0, 0), 1), ((-1, 0, 0, 0), Fraction(-1, 4)),
            ((0, 0, 1, 0), 0), ((0, 0, 0, 1), 0),
        ]
        vector = feasible_vector(Fraction(1), ranged)
        self.assertIsNotNone(vector)
        self.assertEqual(sum(vector), Fraction(1))
        self.assertGreaterEqual(vector[0], Fraction(1, 4))
        self.assertLessEqual(vector[0], Fraction(3, 4))
        self.assertEqual(vector[2:], (Fraction(0), Fraction(0)))
        self.assertIsNone(feasible_vector(Fraction(1), [
            ((1, 0, 0, 0), 0), ((-1, 0, 0, 0), -1),
        ]))

    def test_zero_capacity_and_full_batch_conservation(self):
        vector = feasible_vector(Fraction(2), [
            ((0, 1, 0, 0), 2), ((0, -1, 0, 0), -2),
            ((1, 0, 0, 0), 0), ((0, 0, 1, 0), 0), ((0, 0, 0, 1), 0),
        ])
        self.assertEqual(vector, (Fraction(0), Fraction(2), Fraction(0), Fraction(0)))
