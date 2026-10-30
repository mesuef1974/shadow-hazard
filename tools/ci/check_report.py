"""Browser regressions for navigation, responsive layout and complete printing."""
import os
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(ROOT / 'docs')))
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
url = f'http://127.0.0.1:{server.server_port}/blueprint/review-ar.html'
try:
    with sync_playwright() as playwright, tempfile.TemporaryDirectory() as temporary:
        options = {'headless': True}
        if os.environ.get('REPORT_BROWSER_CHANNEL'):
            options['channel'] = os.environ['REPORT_BROWSER_CHANNEL']
        browser = playwright.chromium.launch(**options)
        for width in (1440, 768, 390, 320):
            page = browser.new_page(viewport={'width': width, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            page.evaluate('document.fonts.ready')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            assert page.locator('.report-logo').evaluate('(e) => e.complete && e.naturalWidth > 0')
            ids = ['agreement', 'relations', 'schema', 'plan', 'review']
            for section in ids + list(reversed(ids)):
                page.locator(f'nav a[href="#{section}"]').click()
                page.wait_for_function('(id) => document.querySelector("nav [aria-current]")?.hash === "#" + id', arg=section)
            page.goto(url + '#schema')
            page.wait_for_function('document.querySelector("nav [aria-current]")?.hash === "#schema"')
            assert not errors, errors
            page.close()
        page = browser.new_page()
        page.goto(url)
        row_count = page.locator('#schema tbody tr').count()
        assert row_count > 0
        for state in ('filtered', 'collapsed'):
            page.locator('#clear-search').click()
            if state == 'filtered':
                page.locator('#search').fill('zzzzzz-no-match')
                assert page.locator('#search-empty').is_visible()
            else:
                page.locator('#collapse-groups').click()
            output = Path(temporary) / f'{state}.pdf'
            page.pdf(path=str(output), format='A4', print_background=True)
            text = '\n'.join(p.extract_text() for p in PdfReader(output).pages)
            assert 'SensorInstallation' in text and 'ExhibitionRelease' in text, state
            if state == 'filtered':
                assert page.locator('#search').input_value() == 'zzzzzz-no-match'
                assert page.locator('#schema details:visible').count() == 0
            else:
                assert page.locator('#schema details[open]').count() == 0
        browser.close()
finally:
    server.shutdown()
    server.server_close()
print('Report navigation, responsive layout, search and complete printing: OK')
