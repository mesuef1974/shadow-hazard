"""مستقبِل لمرة واحدة: يأخذ ناتج tools/training_crawler.js من المتصفح ويكتبه ملفًا.

    python training_crawl_receiver.py <output.json> [--port 8733] [--minutes 10]

يستمع على 127.0.0.1 وحده، يقبل طلب POST واحدًا ثم يغلق نفسه، ويغلق بعد المهلة إن لم يصله
شيء؛ فلا يبقى خادم مفتوحًا. لا يخدم ملفات ولا يقرأ من القرص.
"""
import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ORIGIN = "https://hamdanfsc.com"
MAX_BYTES = 64 * 1024 * 1024
RELAY = """<!doctype html><meta charset="utf-8"><title>relay</title><p id="s">waiting</p><script>
addEventListener('message', async (event) => {
  if (event.origin !== '__ORIGIN__' || typeof event.data !== 'string') return;
  const reply = await fetch('/', {method: 'POST', body: event.data,
                                  headers: {'Content-Type': 'application/json'}});
  document.getElementById('s').textContent = 'saved ' + reply.status;
  event.source.postMessage('saved ' + reply.status, event.origin);
});
(window.opener || window.parent).postMessage('ready', '__ORIGIN__');
</script>"""


def make_handler(output, finished):
    class Handler(BaseHTTPRequestHandler):
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", ORIGIN)
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            # صفحة ترحيل: تستقبل الناتج برسالة postMessage من صفحة المسابقة وحدها ثم ترسله
            # إلى هذا المستقبِل من أصله نفسه، حين يمنع المتصفح الطلب المباشر إلى 127.0.0.1.
            page = RELAY.replace("__ORIGIN__", ORIGIN).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self):
            size = int(self.headers.get("Content-Length") or 0)
            if not 0 < size <= MAX_BYTES:
                self.send_response(413)
                self.end_headers()
                return
            # يُقرأ الجسم قبل الرفض: الرد قبل قراءته يقطع الاتصال على ويندوز.
            body = self.rfile.read(size)
            own = f"http://127.0.0.1:{self.server.server_port}"
            if self.headers.get("Origin") not in (ORIGIN, own):
                self.send_response(403)
                self.end_headers()
                return
            # المتصفح قد يمنع fetch من صفحة عامة إلى 127.0.0.1؛ إرسال نموذج (حقل payload)
            # انتقالٌ لا يشمله ذلك المنع.
            if self.headers.get_content_type() == "application/x-www-form-urlencoded":
                body = parse_qs(body.decode("utf-8")).get("payload", [""])[0].encode("utf-8")
            try:
                json.loads(body)
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return
            output.write_bytes(body)
            self.send_response(200)
            self._cors()
            self.end_headers()
            self.wfile.write(b"ok")
            finished.set()

        def log_message(self, *args):
            pass

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--port", type=int, default=8733)
    parser.add_argument("--minutes", type=float, default=10)
    args = parser.parse_args(argv)
    finished = threading.Event()
    server = HTTPServer(("127.0.0.1", args.port), make_handler(args.output, finished))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    received = finished.wait(args.minutes * 60)
    server.shutdown()
    server.server_close()
    return 0 if received else 1


if __name__ == "__main__":
    raise SystemExit(main())
