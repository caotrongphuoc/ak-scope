import http.client
import json
import pathlib
import threading
import unittest
import unittest.mock

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



class TestControl(unittest.TestCase):
    def setUp(self):
        from akscope.live import LiveSession

        self.live = LiveSession()
        self.httpd = make_server(State(live=self.live, port="/dev/ttyUSB0"), port=0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.live.stop()
        self.httpd.shutdown()
        self.httpd.server_close()

    def request(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_config(self):
        status, body = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"flash_target": None, "live": True, "verify_static": False,
                                            "port": "/dev/ttyUSB0", "baud": 115200})

    def test_foreign_host_and_origin(self):
        status, _ = self.request("GET", "/api/config", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.request("POST", "/api/record/stop", b"{}",
                                 {"Content-Type": "application/json", "Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_control_needs_json(self):
        status, _ = self.request("POST", "/api/record/stop", b"{}", {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_only_listed_ports(self):
        status, body = self.request("POST", "/api/record/start", b'{"port": "/etc/passwd"}',
                                    {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertIn(b"unknown port", body)

    def test_flash_without_target(self):
        with unittest.mock.patch("serial.tools.list_ports.comports",
                                 return_value=[unittest.mock.Mock(device="/dev/ttyUSB0")]):
            status, body = self.request("POST", "/api/flash", b'{"port": "/dev/ttyUSB0"}',
                                        {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["ok"])


class TestDecodedCache(unittest.TestCase):
    def test_second_open_from_cache(self):
        import tempfile

        from akscope import server

        data = (SAMPLES / "11m-game-boot-akt.bin").read_bytes()
        with tempfile.TemporaryDirectory() as d:
            with unittest.mock.patch.object(server, "_decoded_dir", return_value=pathlib.Path(d)):
                first = State()
                a = first.open({}, data, "boot.bin")
                self.assertIsNotNone(first.current)
                self.assertEqual(len(list(pathlib.Path(d).glob("*.json"))), 1)

                again = State()
                with unittest.mock.patch.object(server, "decode_capture", wraps=server.decode_capture) as dec:
                    b = again.open({}, data, "boot.bin")
                    self.assertEqual(dec.call_count, 0)
                    self.assertEqual(a, b)
                    # verify still gets a timeline, built when it is asked for
                    self.assertEqual(len(again._current_timeline()[0].execs), 32)
                    self.assertEqual(dec.call_count, 1)

    def test_prune(self):
        import os
        import tempfile

        from akscope import server

        with tempfile.TemporaryDirectory() as d:
            for i in range(5):
                f = pathlib.Path(d) / ("%d.json" % i)
                f.write_text("{}")
                os.utime(f, (i, i))
            server._prune(pathlib.Path(d), 2)
            self.assertEqual(sorted(p.name for p in pathlib.Path(d).glob("*.json")), ["3.json", "4.json"])


if __name__ == "__main__":
    unittest.main()
