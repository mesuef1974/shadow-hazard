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

ORIGIN = "https://hamdanfsc.com"
MAX_BYTES = 64 * 1024 * 1024


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

        def do_POST(self):
            size = int(self.headers.get("Content-Length") or 0)
            if not 0 < size <= MAX_BYTES:
                self.send_response(413)
                self.end_headers()
                return
            # يُقرأ الجسم قبل الرفض: الرد قبل قراءته يقطع الاتصال على ويندوز.
            body = self.rfile.read(size)
            if self.headers.get("Origin") != ORIGIN:
                self.send_response(403)
                self.end_headers()
                return
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
