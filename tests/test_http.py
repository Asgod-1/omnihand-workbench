import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from omnihand_usb.monitor import Monitor
from omnihand_usb.server import make_server
from omnihand_usb.control_client import ControlClient


class HttpContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.monitor = Monitor(Path(self.temp.name))
        self.server = make_server(self.monitor, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.monitor.disconnect()
        self.temp.cleanup()

    def post(self, path, data, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(self.base + path, json.dumps(data).encode(), headers, method="POST")
        return urllib.request.urlopen(request, timeout=3)

    def test_health_and_page_do_not_open_hardware(self):
        with urllib.request.urlopen(self.base + "/health") as response:
            health = json.load(response)
        self.assertTrue(health["read_only"])
        self.assertEqual(health["app"], "omnihand-workbench")
        self.assertFalse(self.monitor.snapshot()["connected"])
        with urllib.request.urlopen(self.base + "/") as response:
            page=response.read().decode()
            self.assertIn("全手触觉",page);self.assertIn('动作工作台',page)

    def test_motion_endpoint_is_not_exposed(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post("/api/move", {"positions": [0] * 10})
        self.assertEqual(error.exception.code, 404)

    def test_asset_paths_cannot_read_config_or_vendor_native_libraries(self):
        for path in ('/assets/../config.local.json','/assets/model/../../damiao_device_sdk/dm_device.dll','/assets/model/config.local.json','/assets/three/LICENSE','/config.local.json'):
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(self.base+path)
                self.assertEqual(error.exception.code,404)

    def test_cross_site_requests_are_rejected_before_device_open(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post("/api/connect", {"port": "COM5"}, "https://example.com")
        self.assertEqual(error.exception.code, 403)
        self.assertFalse(self.monitor.snapshot()["connected"])

    def test_demo_connection_is_labelled_and_recording_requires_boolean(self):
        with self.post("/api/connect", {"mode": "demo"}) as response:
            self.assertEqual(json.load(response)["mode"], "demo")
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post("/api/recording", {"enabled": "yes"})
        self.assertEqual(error.exception.code, 400)

    def test_control_is_explicit_session_bound_and_preview_does_not_move(self):
        with self.post('/api/connect',{'mode':'demo'}) as response:state=json.load(response)
        before=self.monitor.snapshot()['positions'];session=state['control']['session_id']
        with self.post('/api/control',{'action':'preview','preset_id':'open'}) as response:
            self.assertFalse(json.load(response)['hardware_write'])
        self.assertEqual(self.monitor.snapshot()['positions'],before)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post('/api/control',{'action':'move','session_id':session,'preset_id':'open'})
        self.assertEqual(error.exception.code,409)
        with self.post('/api/control',{'action':'arm','session_id':session}) as response:self.assertTrue(json.load(response)['armed'])
        with urllib.request.urlopen(self.base+'/health') as response:self.assertFalse(json.load(response)['read_only'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post('/api/control',{'action':'enable','session_id':session},'https://example.com')
        self.assertEqual(error.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post('/api/control',{'action':'enable','session_id':'old'})
        self.assertEqual(error.exception.code,409)

    def test_python_client_completes_a_demo_move_through_the_sampler(self):
        self.monitor.connect(demo=True)
        client=ControlClient(self.server.server_port)
        with self.assertRaisesRegex(RuntimeError,'arm'):client.move(positions=[0]*10)
        client.arm();client.enable()
        current=client.state()['positions'];target=[v-20 for v in current]
        result=client.move(positions=target,duration=.5)
        final=client.wait(result['motion_id'],timeout=4)
        self.assertEqual(final['status'],'completed')
        self.assertEqual(client.state()['positions'],target)
        client.disable();self.assertFalse(client.state()['control']['armed'])
        log=Path(self.temp.name)/final['log_path']
        events=[json.loads(line)['event'] for line in log.read_text(encoding='utf-8').splitlines()]
        self.assertIn('position_acknowledged',events);self.assertIn('motion_completed',events)


if __name__ == "__main__":
    unittest.main()
