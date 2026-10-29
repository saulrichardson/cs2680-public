"""An HTTP error preserves diagnostics and never silently repeats its mutation."""
import threading,unittest
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from src.bridge import post

class SandboxTransport(unittest.TestCase):
    def test_error_body_and_single_dispatch(self):
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                calls.append(self.rfile.read(int(self.headers['Content-Length'])))
                body=b'{"error":"device missing after operation"}'
                self.send_response(500);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            with self.assertRaisesRegex(RuntimeError,'Uncertain sandbox operation; not replayed.*HTTP 500.*device missing after operation'):
                post(f'http://127.0.0.1:{server.server_port}/exec',{'command':'mutate'})
            self.assertEqual(len(calls),1)
        finally:server.shutdown();server.server_close();thread.join()
