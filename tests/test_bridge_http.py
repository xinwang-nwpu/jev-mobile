"""Real local HTTP transport, discovery, fallback and uncertain write outcomes."""
import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import socket
import subprocess
from threading import Thread

import pytest

from jev_mobile.device import Device

TOKEN = "t" * 43
STATE = {"a11y_tree": {"className": "EditText", "text": "你好", "isEditable": True,
         "isClickable": True, "boundsInScreen": {"left": 0, "top": 0, "right": 100, "bottom": 100}},
         "phone_state": {"keyboardVisible": True}}


@contextmanager
def server(mode="normal"):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            self.reply("GET")
        def do_POST(self):
            self.reply("POST")
        def reply(self, method):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or "null")
            calls.append((method, self.path, payload, self.headers.get("Authorization")))
            if method == "POST" and mode == "lost_response":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            code = 401 if mode == "unauthorized" else 409 if mode == "rejected" else 200
            row = {"status": "success", "result": STATE if method == "GET" else "verified"}
            if mode == "invalid_state":
                row["result"] = {"not_a_tree": []}
            data = b"broken" if mode == "invalid_json" else json.dumps(row).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    http = HTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=http.serve_forever, daemon=True)
    worker.start()
    try:
        yield http.server_port, calls
    finally:
        http.shutdown()
        http.server_close()
        worker.join()


def device_at(port):
    device = Device.__new__(Device)
    device._bridge_http_checked = True
    device._bridge_http = (port, TOKEN)
    device._bridge_forward = None
    device._portal_keyboard = True
    device._portal_ime_package = "ai.jev.bridge"
    device._saved_ime = None
    return device


def test_state_and_chinese_input_skip_content_and_forward_is_owned(monkeypatch):
    with server() as (port, requests):
        device = device_at(port)
        device._bridge_http_checked = False
        commands = []
        def run(*args):
            commands.append(args)
            if args[0] == "shell":
                info = {"status": "success", "result": {"port": 12345, "token": TOKEN, "protocol": 1}}
                assert args[-1] == "content://ai.jev.bridge/http_info"
                output = "Row: 0 result=" + json.dumps(info)
            elif args[:2] == ("forward", "tcp:0"):
                output = str(port)
            else:
                assert args == ("forward", "--remove", "tcp:%d" % port)
                output = ""
            return subprocess.CompletedProcess(args, 0, output, "")
        monkeypatch.setattr(device, "_run", run)
        tree, phone, source = device._portal_tree()
        assert source == "jev_bridge" and phone["keyboardVisible"] and tree[0]["text"] == "你好"
        device._send_text("中文🌟\n第二行")
        device._portal_tree()
        assert len(commands) == 2  # Discovery and forward happen once, no shell per request.
        assert all(row[3] == "Bearer " + TOKEN for row in requests)
        assert base64.b64decode(requests[1][2]["base64_text"]).decode() == "中文🌟\n第二行"
        assert requests[1][2]["clear"] is True
        device.close()
        device.close()
        assert len(commands) == 3  # Removes only its own port, once.


@pytest.mark.parametrize("mode", ["unauthorized", "invalid_json", "invalid_state"])
def test_failed_read_falls_back_and_does_not_keep_retrying_http(monkeypatch, mode):
    with server(mode) as (port, requests):
        device = device_at(port)
        row = {"status": "success", "result": STATE}
        commands = []
        def run(*args):
            commands.append(args)
            return subprocess.CompletedProcess(args, 0, "Row: 0 result=" + json.dumps(row), "")
        monkeypatch.setattr(device, "_run", run)
        assert device._portal_tree()[2] == "jev_bridge"
        assert device._portal_tree()[2] == "jev_bridge"
        assert len(requests) == 1 and len(commands) == 2


@pytest.mark.parametrize("mode", ["lost_response", "invalid_json", "rejected"])
def test_uncertain_input_is_never_replayed_through_content(monkeypatch, mode):
    with server(mode) as (port, requests):
        device = device_at(port)
        monkeypatch.setattr(device, "_run", lambda *args: pytest.fail("Unsafe input replay"))
        with pytest.raises(RuntimeError, match="inspect the field"):
            device._send_text("你好")
        assert len(requests) == 1 and device._bridge_http is None


def test_unavailable_http_before_input_falls_back_to_content(monkeypatch):
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
    device = device_at(port)
    commands = []
    def run(*args):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(device, "_run", run)
    device._send_text("你好")
    assert len(commands) == 1 and commands[0][4] == "content://ai.jev.bridge/keyboard/input"
    assert device._bridge_http is None


def test_old_apk_without_http_is_only_probed_once(monkeypatch):
    assert Device._decode_portal_state({"status": "success", "data": None, "result": STATE}, "legacy")[0][0]["text"] == "你好"
    assert Device._decode_portal_state(STATE, "legacy")[2] == "legacy"
    device = device_at(1)
    device._bridge_http_checked = False
    device._bridge_http = None
    commands = []
    def run(*args):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, 'Row: 0 result={"status":"error","message":"Unknown endpoint"}', "")
    monkeypatch.setattr(device, "_run", run)
    assert device._bridge_request("GET", "/state_full") is None
    assert device._bridge_request("GET", "/state_full") is None
    assert len(commands) == 1 and device._bridge_forward is None
