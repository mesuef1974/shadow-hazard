import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase


class SidebarTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='mesuef', role='supervisor')
        self.client.force_login(self.user)

    def test_group_of_current_page_is_open(self):
        html = self.client.get('/tasks/').content.decode()
        group = re.search(r'<details class="nav-group"( open)?><summary>العمل</summary>', html)
        self.assertIsNotNone(group)
        self.assertTrue(group.group(1), 'مجموعة العمل يجب أن تُفتح في صفحة المهام')
        other = re.search(r'<details class="nav-group"( open)?><summary>المعرفة</summary>', html)
        self.assertFalse(other.group(1))

    def test_current_link_is_marked(self):
        html = self.client.get('/tasks/').content.decode()
        self.assertRegex(html, r'<a href="/tasks/" aria-current="page">المهام</a>')

    def test_landmark_and_assets(self):
        html = self.client.get('/tasks/').content.decode()
        self.assertIn('aria-label="التنقل الرئيسي"', html)
        for asset in ('sidebar.css', 'layout.css', 'sidebar.js'):
            self.assertIn(asset, html)


class StaticRulesTests(TestCase):
    def test_scrollers_are_named_regions(self):
        offenders = []
        for path in settings.BASE_DIR.rglob('templates/**/*.html'):
            for tag in re.findall(r'<div class="usage-history"[^>]*>', path.read_text(encoding='utf-8')):
                if not all(part in tag for part in ('role="region"', 'aria-label=', 'tabindex="0"')):
                    offenders.append(f'{path.name}: {tag}')
        self.assertEqual(offenders, [])

    def test_corner_radius_is_at_most_two_pixels(self):
        files = [settings.BASE_DIR.parent / 'docs' / 'brand' / 'portal.css']
        files += list(settings.BASE_DIR.glob('*/static/**/*.css'))
        offenders = []
        for path in files:
            for value in re.findall(r'border-radius\s*:\s*([^;}"\']+)', path.read_text(encoding='utf-8')):
                if value.strip() not in ('0', '2px', 'inherit'):
                    offenders.append(f'{path.name}: {value.strip()}')
        self.assertEqual(offenders, [])


class LibraryFilterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='mesuef', role='supervisor')
        self.client.force_login(self.user)

    def test_default_page_size_is_25(self):
        response = self.client.get('/library/')
        self.assertEqual(response.context['size'], 25)
        self.assertLessEqual(len(response.context['documents']), 25)

    def test_size_choices_and_fallback(self):
        self.assertEqual(self.client.get('/library/?size=50').context['size'], 50)
        self.assertEqual(self.client.get('/library/?size=7').context['size'], 25)

    def test_kind_filter_keeps_only_that_kind(self):
        response = self.client.get('/library/?kind=MD&size=50')
        self.assertEqual(response.context['kind'], 'MD')
        self.assertTrue(all(d['kind'] == 'MD' for d in response.context['documents']))
        self.assertEqual(self.client.get('/library/?kind=NOPE').context['kind'], '')

    def test_pagination_links_keep_filters(self):
        response = self.client.get('/library/?kind=MD&size=10')
        if response.context['page_obj'].has_next():
            self.assertContains(response, 'kind=MD&amp;size=10&amp;page=2')
