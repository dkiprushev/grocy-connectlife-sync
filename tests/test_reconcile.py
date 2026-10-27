"""Reconciliation must not echo stale changes or guess conflict winners."""
import unittest

from hisense_food_manager.reconcile import Decision, reconcile


class ReconcileTests(unittest.TestCase):
    def test_first_contact(self):
        self.assertEqual(reconcile(2, None).decision, Decision.TO_CONNECTLIFE)
        self.assertEqual(reconcile(None, 2).decision, Decision.TO_GROCY)
        self.assertEqual(reconcile(2, 3).decision, Decision.CONFLICT)
        self.assertEqual(reconcile(2, 2).decision, Decision.UNCHANGED)

    def test_single_sided_change(self):
        self.assertEqual(reconcile(3, 2, baseline=2, has_baseline=True).decision, Decision.TO_CONNECTLIFE)
        self.assertEqual(reconcile(2, 3, baseline=2, has_baseline=True).decision, Decision.TO_GROCY)

    def test_simultaneous_changes_and_deletion(self):
        self.assertEqual(reconcile(3, 4, baseline=2, has_baseline=True).decision, Decision.CONFLICT)
        self.assertEqual(reconcile(None, 2, baseline=2, has_baseline=True).decision, Decision.DELETE_CONFLICT)
        self.assertEqual(reconcile(2, None, baseline=2, has_baseline=True).decision, Decision.DELETE_CONFLICT)
        self.assertEqual(reconcile(None, None, baseline=2, has_baseline=True).decision, Decision.UNCHANGED)
