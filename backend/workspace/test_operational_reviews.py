from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from .operational_reviews import operational_reviews


class OperationalReviewTests(TestCase):
    def setUp(self):
        self.job = SimpleNamespace(pk=50, charter=SimpleNamespace(project_id=1))
        self.doc = SimpleNamespace(pk=417, project_id=1, summary='PASS', created_at=None,
            evidence={'checks': {'job': 50, 'dispatch': 29, 'turn': 'turn1',
                'message_id_actual': 28, 'journal': 2, 'caller': 'operator',
                'coordination_activation_receipt': 412}})
        self.mocks = {}
        for name in ('ProjectDocumentation', 'SessionDispatch', 'SessionMessage',
                     'AuthorityTransferJournal', 'AutoRenewalPolicy'):
            patcher = patch('workspace.operational_reviews.' + name)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.mocks['ProjectDocumentation'].objects.filter.return_value.order_by.return_value = [self.doc]
        dispatch = self.mocks['SessionDispatch'].objects.filter.return_value.first.return_value
        dispatch.pk = 29
        dispatch.turns.filter.return_value.exists.return_value = True
        self.mocks['SessionMessage'].objects.filter.return_value.exists.return_value = True
        self.mocks['AuthorityTransferJournal'].objects.select_related.return_value.filter.return_value.first.return_value.pk = 2
        self.mocks['AutoRenewalPolicy'].objects.select_related.return_value.filter.return_value.first.return_value.activation_receipt_id = 412

    @patch('workspace.operational_reviews.activation_valid', return_value=True)
    @patch('workspace.operational_reviews.verified_document', return_value=True)
    def test_maps_verified_operational_proof_without_review_assignment(self, verified, active):
        result = operational_reviews([self.job])[50]
        self.assertEqual((result['receipt'], result['message'], result['dispatch']), (417, 28, 29))
        self.assertEqual(result['journal'], 2)
        self.assertEqual(result['activation_receipt'], 412)

    @patch('workspace.operational_reviews.verified_document', return_value=False)
    def test_bad_digest_is_not_linked(self, verified):
        self.assertEqual(operational_reviews([self.job]), {})

    @patch('workspace.operational_reviews.verified_document', return_value=True)
    def test_cross_project_proof_is_not_linked(self, verified):
        self.doc.project_id = 2
        self.assertEqual(operational_reviews([self.job]), {})

    @patch('workspace.operational_reviews.verified_document', return_value=True)
    def test_missing_message_is_not_linked(self, verified):
        self.mocks['SessionMessage'].objects.filter.return_value.exists.return_value = False
        self.assertEqual(operational_reviews([self.job]), {})
