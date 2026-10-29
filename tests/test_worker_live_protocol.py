import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
from threading import Thread
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
class LocalCamera(BaseHTTPRequestHandler):
    images=[]
    def do_GET(self):
        data=self.images.pop(0) if self.images else b'bad'
        self.send_response(200);self.send_header('Content-Type','image/jpeg');self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data)
    def log_message(self,*args):pass

class WorkerIntegration(unittest.TestCase):
    def test_real_persistent_worker_two_stage_and_failure_recovery(self):
        try:import cv2
        except ImportError:self.skipTest('cv2 unavailable; run in AutoXY venv')
        f=ROOT/'tests/fixtures'
        a=(f/'094_endurance_022_t0_a_attempt_1.jpg').read_bytes()
        b=(f/'098_endurance_024_t0_a_attempt_2.jpg').read_bytes()
        LocalCamera.images=[a,b,b'corrupt JPEG',a]
        server=ThreadingHTTPServer(('127.0.0.1',0),LocalCamera)
        th=Thread(target=server.serve_forever,daemon=True);th.start()
        try:
            with tempfile.TemporaryDirectory() as d:
                with subprocess.Popen([sys.executable,str(ROOT/'src/auto_xy_vision_worker.py'),'--serve'],
                                      stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                      text=True,bufsize=1) as proc:
                    for index,(data,ok) in enumerate(((a,True),(b,True),(b'corrupt',False),(a,True)),1):
                        req={'request_id':index,'url':f'http://127.0.0.1:{server.server_port}/snapshot',
                             'image':str(Path(d)/f'{index}.jpg'),'roi':'auto','timeout':4.0,
                             'detector_mode':'two_stage',
                             'expected_center':'957.2916666667,544.375','expected_radius':149.875}
                        proc.stdin.write(json.dumps(req)+'\n');proc.stdin.flush()
                        reply=json.loads(proc.stdout.readline())
                        self.assertEqual(reply['request_id'],index)
                        self.assertEqual(reply['ok'],ok,reply.get('error'))
                        if ok:
                            self.assertEqual(reply['result']['sha256'],hashlib.sha256(data).hexdigest())
                            self.assertTrue(935 < reply['result']['center_px'][0] < 955)
                    proc.stdin.close();self.assertEqual(proc.wait(timeout=5),0)
        finally:
            server.shutdown();server.server_close()

if __name__=='__main__':unittest.main(verbosity=2)
