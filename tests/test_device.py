"""Tree-source selection: a portal without interaction flags falls back to uiautomator."""

import subprocess

import jev_mobile.device as device_module
from jev_mobile.device import Device, _tree_has_interaction_flags

from helpers import node

FLAGLESS_PORTAL = [{"className": "FrameLayout", "text": "x", "bounds": "0,0,10,10", "children": []}]
FLAGGED_PORTAL = [{"className": "TextView", "text": "x", "bounds": "0,0,10,10", "clickable": False, "children": []}]

UIA_XML = (
    "<?xml version='1.0' encoding='UTF-8'?><hierarchy>"
    "<node bounds='[0,0][100,100]' class='android.widget.TextView' text='Go' clickable='true'/>"
    "</hierarchy>"
)
# What `uiautomator dump /dev/tty` prints: a status line, then the XML itself.
TTY_DUMP = "UI hierchary dumped to: /dev/tty.\n" + UIA_XML


def bare_device(monkeypatch, portal_result):
    device = Device.__new__(Device)
    device.adb_path = "adb"
    device.device = None
    device.screen = (1080, 2340)
    device.last_source = ""
    device._portal_usable = None
    device._portal_keyboard = None
    device._saved_ime = None
    monkeypatch.setattr(device, "_portal_tree", lambda: portal_result)
    return device


def fake_run(outputs):
    calls = []

    def _run(*args, text=True):
        calls.append(args)
        out = outputs.pop(0)
        stdout = out if isinstance(out, str) else out[0]
        stderr = out[1] if isinstance(out, tuple) else ""
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout=stdout, stderr=stderr)

    return _run, calls


def test_flagless_tree_detected():
    assert _tree_has_interaction_flags(FLAGLESS_PORTAL) is False
    # Key presence is the signal, even when every value is False.
    assert _tree_has_interaction_flags(FLAGGED_PORTAL) is True
    assert _tree_has_interaction_flags([]) is False


def test_flagless_portal_falls_back_to_uiautomator(monkeypatch):
    device = bare_device(monkeypatch, (FLAGLESS_PORTAL, {}, "mobilerun_portal"))
    run, calls = fake_run([TTY_DUMP])
    monkeypatch.setattr(device, "_run", run)
    elements, phone_state, source = device._read_tree()
    assert source == "uiautomator"
    assert elements[0]["text"] == "Go"
    # The dump streams the XML back over /dev/tty: one round trip, no cat/rm follow-ups.
    assert len(calls) == 1
    assert " ".join(str(a) for a in calls[0]).endswith("uiautomator dump /dev/tty")
    # The capability decision is cached: a second read skips the portal query entirely.
    monkeypatch.setattr(device, "_portal_tree", lambda: (_ for _ in ()).throw(AssertionError("portal re-queried")))
    run2, _ = fake_run([TTY_DUMP])
    monkeypatch.setattr(device, "_run", run2)
    _, _, source = device._read_tree()
    assert source == "uiautomator"


def test_uiautomator_file_fallback_when_dev_tty_refuses(monkeypatch):
    device = bare_device(monkeypatch, (FLAGLESS_PORTAL, {}, "mobilerun_portal"))
    run, calls = fake_run(["ERROR: could not get idle state.", UIA_XML])
    monkeypatch.setattr(device, "_run", run)
    _, _, source = device._read_tree()
    assert source == "uiautomator"
    # The fallback dumps to a file and returns it in a single combined shell command.
    fallback = " ".join(str(a) for a in calls[1])
    assert "cat" in fallback and "rm -f" in fallback


def test_flagged_portal_is_used_without_uiautomator(monkeypatch):
    device = bare_device(monkeypatch, (FLAGGED_PORTAL, {"keyboardVisible": True}, "mobilerun_portal"))
    run, calls = fake_run([])
    monkeypatch.setattr(device, "_run", run)
    elements, phone_state, source = device._read_tree()
    assert source == "mobilerun_portal"
    assert calls == []


def test_portal_state_full_uri_is_preferred():
    from jev_mobile.a11y import PORTAL_STATE_URIS

    uris = [uri for _, uri in PORTAL_STATE_URIS]
    assert uris.index("content://com.mobilerun.portal/state_full") < uris.index("content://com.mobilerun.portal/state")
    assert "content://com.droidrun.portal/state_full" in uris


def test_portal_keyboard_input_is_the_preferred_path(monkeypatch):
    device = bare_device(monkeypatch, None)
    calls = []

    def fake_run(*args, text=True):
        calls.append(" ".join(str(a) for a in args))
        if "dumpsys" in calls[-1]:
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="mCurMethodId=com.mobilerun.portal/.input.MobilerunKeyboardIME", stderr="")
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(device, "_run", fake_run)
    monkeypatch.setattr(device, "_shell", lambda cmd: fake_run("shell", cmd))
    device._send_text("流沙 ABC", delete=6)
    inserts = [c for c in calls if c.startswith("shell content insert")]
    assert len(inserts) == 1 and "keyboard/input" in inserts[0]
    assert "--bind base64_text:s:" in inserts[0]
    # The portal endpoint clears the field itself; no delete keyevents are issued.
    assert not any("keyevent 67" in c for c in calls)


def test_ascii_input_text_fallback_without_any_keyboard(monkeypatch):
    device = bare_device(monkeypatch, None)
    calls = []

    def fake_run(*args, text=True):
        calls.append(" ".join(str(a) for a in args))
        if "dumpsys" in calls[-1]:
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="mCurMethodId=com.sogou.inputmethod", stderr="")
        if "ime" in calls[-1] and "list" in calls[-1]:
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(device, "_run", fake_run)
    monkeypatch.setattr(device, "_shell", lambda cmd: fake_run("shell", cmd))
    device._send_text("hello world", delete=5)
    assert any("input text" in c and "hello%sworld" in c for c in calls)
    assert any("keyevent 67" in c for c in calls)

    import pytest

    with pytest.raises(RuntimeError):
        device._send_text("流沙")


def test_ime_switch_waits_for_the_bound_method(monkeypatch):
    device = bare_device(monkeypatch, None)

    class Outputs:
        def __init__(self):
            self.ime_list = 0
            self.bound = 0

    out = Outputs()

    def fake_run(*args, text=True):
        joined = " ".join(str(a) for a in args)
        if "ime" in joined and "list" in joined:
            out.ime_list += 1
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="com.android.adbkeyboard/.AdbIME\n", stderr="")
        if "settings" in joined:
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="com.mobilerun.portal/.input.MobilerunKeyboardIME\n", stderr="")
        if "ime" in joined and "set" in joined:
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")
        if "dumpsys" in joined:
            out.bound += 1
            # First poll still reports the old IME; the second reports AdbKeyboard bound.
            ime = device_module.ADB_KEYBOARD if out.bound > 1 else "com.mobilerun.portal/.input.MobilerunKeyboardIME"
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="mCurMethodId=%s" % ime, stderr="")
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(device, "_run", fake_run)
    monkeypatch.setattr(device, "_shell", lambda cmd: fake_run("shell", cmd))
    assert device._use_adb_keyboard() is True
    assert out.ime_list == 1
    assert out.bound >= 2  # the switch was actually polled before being trusted
    assert device._saved_ime == "com.mobilerun.portal/.input.MobilerunKeyboardIME"


def test_node_flags_survive_the_pipeline():
    tree = [node(text="Go", clickable=True)]
    assert _tree_has_interaction_flags(tree) is True
