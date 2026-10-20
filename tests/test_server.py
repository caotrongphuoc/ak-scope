import http.client
import json
import pathlib
import threading
import unittest

from akscope.server import State, make_server

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"


class ServerCase(unittest.TestCase):
    trace = None

    def setUp(self):
        self.httpd = make_server(State(self.trace), port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def request(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, r.getheader("Content-Type"), data


class TestStatic(ServerCase):
    def test_index(self):
        status, ctype, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"<title>ak-scope</title>", body)

    def test_module_type(self):
        status, ctype, _ = self.request("GET", "/js/main.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", ctype)

    def test_no_files_outside_web(self):
        for path in ("/../pyproject.toml", "/%2e%2e/pyproject.toml", "/js/../../README.md"):
            status, _, _ = self.request("GET", path)
            self.assertEqual(status, 404, path)

    def test_listens_on_localhost_only(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")

    def test_no_trace(self):
        status, _, _ = self.request("GET", "/api/trace")
        self.assertEqual(status, 204)


class TestDecode(ServerCase):
    def test_upload(self):
        blob = (SAMPLES / "11m-game-boot-akt.bin").read_bytes()
        status, _, body = self.request("POST", "/api/decode?name=boot.bin", blob)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["name"], "boot.bin")
        self.assertEqual(len(data["execs"]), 32)
        self.assertFalse(data["has_symbols"])

    def test_empty_body(self):
        status, _, _ = self.request("POST", "/api/decode", b"")
        self.assertEqual(status, 400)


class TestTraceArg(ServerCase):
    trace = str(SAMPLES / "11m-game-boot-akt.bin")

    def test_trace(self):
        status, _, body = self.request("GET", "/api/trace")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["info"]["cpu_hz"], 32000000)


if __name__ == "__main__":
    unittest.main()
