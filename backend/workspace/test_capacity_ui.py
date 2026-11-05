from pathlib import Path
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class CapacityUiTests(SimpleTestCase):
    def test_usage_note_reflects_automatic_policy_instead_of_fixed_disabled_text(self):
        data = {'thresholds_enabled': True, 'automatic_renewal_enabled': True,
            'automatic_renewal_sessions': ['واجهة المشروع', 'مراجعة المشروع']}
        context = {'capacity_snapshot': data, 'runtime_context': {'sessions': []}}
        html = render_to_string('token_usage/runtime_context.html', context)
        self.assertIn('التجديد التلقائي مفعّل للجلسات', html)
        self.assertIn('واجهة المشروع', html)
        self.assertIn('مراجعة المشروع', html)
        self.assertNotIn('ظل الخطر مفعّل', html)
        self.assertNotIn('التجديد التلقائي غير مفعّل', html)
        data['automatic_renewal_enabled'] = False
        data['automatic_renewal_sessions'] = []
        html = render_to_string('token_usage/runtime_context.html', context)
        self.assertIn('التجديد التلقائي غير مفعّل', html)

    def snapshot(self, reason='display_only_last_request'):
        return {'policy_version': 'context-capacity-v2-display-only',
                'thresholds_enabled': False, 'policy': {'max_age_seconds': 300},
                'sessions': [{'name': 'الواجهة', 'trusted': False, 'mode': 'display_only',
                    'uncertain_reason': reason, 'measurement': {'current': 42000,
                    'limit': 100000, 'percent': None, 'display_percent': 42,
                    'source_kind': 'last_request', 'sampled_at': '2026-11-01T01:00:00Z',
                    'imported_at': '2026-11-01T01:01:00Z', 'source_id': 321},
                    # Even historical events must not appear as approved actions.
                    'recent_warning': {'id': 987}, 'recent_preparation': {'id': 654}}]}

    def test_display_only_source_and_no_historical_actions(self):
        html = render_to_string('registry/_capacity.html', {'capacity_snapshot': self.snapshot()})
        for text in ['حجم آخر طلب، وليس إشغال نافذة السياق', 'العتبتان غير مفعّلتين',
                     'غير مؤكدة', '42,0', '321', 'وقت العينة', 'وقت الاستيراد']:
            self.assertIn(text, html)
        self.assertNotIn('تنبيه مسجل #987', html)
        self.assertNotIn('تجهيز مسجل #654', html)
        self.assertNotIn('<progress', html)
        self.assertNotIn('<details open', html)

    def test_missing_stale_and_conflicting_are_uncertain(self):
        html = render_to_string('registry/_capacity.html', {})
        self.assertIn('لقطة المراقبة غير متاحة', html)
        for reason, text in [('sample_stale', 'متقادمان'), ('conflicting_samples', 'تضارب'),
                             ('missing_sample', 'مفقودة')]:
            html = render_to_string('registry/_capacity.html', {'capacity_snapshot': self.snapshot(reason)})
            self.assertIn(text, html)
            self.assertIn('غير مؤكدة', html)

    def test_shared_integration_and_conditional_map(self):
        root = Path(__file__).resolve().parents[2]
        for name in ['backend/workspace/templates/workspace/sessions.html',
                     'backend/workspace/templates/workspace/automation.html',
                     'backend/token_usage/templates/token_usage/dashboard.html']:
            self.assertIn("registry/_capacity.html", (root / name).read_text(encoding='utf-8'))
        map_template = (root / 'backend/workspace/templates/workspace/automation_model.html').read_text(encoding='utf-8')
        self.assertNotIn("registry/_capacity.html", map_template)  # Codex card removed from the map page
        diagram = (root / 'docs/blueprint/automation-map-ar.html').read_text(encoding='utf-8')
        self.assertNotIn('تنبيه 70٪ وتجهيز 85٪', diagram)
