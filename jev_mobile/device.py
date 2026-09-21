"""ADB device connection: one A11Y observation per snapshot, one input batch per action.

State comes from the Droidrun/Mobilerun Portal content provider when installed, else a
uiautomator XML dump. App and activity always come from the window focus line so the cheap
freshness signal and the observed state agree on one source.
"""

import base64
import json
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from .a11y import (
    PORTAL_STATE_URIS,
    normalize_tree,
    parse_content_provider_output,
    parse_uiautomator_xml,
    snapshot_state,
)

ADB_KEYBOARD = "com.android.adbkeyboard/.AdbIME"
# The portal's own IME serves the keyboard/input insert endpoint through its live
# InputConnection, so text works even where the shell `ime` command is disabled.
PORTAL_IME_PREFIXES = ("com.mobilerun.portal/", "com.droidrun.portal/")
PORTAL_KEYBOARD_URIS = (
    "content://com.mobilerun.portal/keyboard/input",
    "content://com.droidrun.portal/keyboard/input",
)
SCROLL_SWIPE_MS = 300
TYPE_SETTLE_S = 0.35
PORTAL_SETTLE_S = 0.15
WAIT_S = 0.5
KEYBOARD_WAIT_S = 1.2
IME_SWITCH_WAIT_S = 2.0
LAUNCH_SETTLE_S = 1.0


def _tree_has_interaction_flags(elements: List[Dict[str, Any]]) -> bool:
    """True when the tree exposes clickability/editability attributes on its nodes."""

    def visit(node: Any) -> bool:
        if not isinstance(node, dict):
            return False
        if "clickable" in node or "editable" in node:
            return True
        return any(visit(child) for child in node.get("children") or [])

    return any(visit(element) for element in elements or [])


class StalePage(ValueError):
    """A decision no longer refers to the observed screen."""


class Device:
    def __init__(self, adb_path: Optional[str] = None, device: Optional[str] = None):
        self.adb_path = adb_path or os.environ.get("ADB_PATH", "adb")
        self.device = device or os.environ.get("ANDROID_DEVICE") or None
        self.screen = self._read_screen()
        self.last_source = ""
        self._portal_usable: Optional[bool] = None
        self._portal_keyboard: Optional[bool] = None
        self._saved_ime: Optional[str] = None

    # -- shell helpers ----------------------------------------------------

    def _run(self, *args: Any, text: bool = True) -> subprocess.CompletedProcess:
        cmd = [self.adb_path]
        if self.device:
            cmd.extend(["-s", self.device])
        cmd.extend(str(arg) for arg in args)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=text,
            encoding="utf-8" if text else None,
            errors="replace" if text else None,
            check=False,
        )

    def _shell(self, command: str) -> subprocess.CompletedProcess:
        return self._run("shell", command)

    def _read_screen(self) -> Tuple[int, int]:
        res = self._shell("wm size")
        sizes = re.findall(r"(\d+)x(\d+)", res.stdout or "")
        if not sizes:
            raise RuntimeError("Cannot read screen size; is a device connected? %s" % (res.stderr or "").strip())
        width, height = (int(v) for v in sizes[-1])  # Override size wins when present.
        return width, height

    # -- observation ------------------------------------------------------

    def observe(self, screenshot: bool = False) -> Dict[str, Any]:
        elements, phone_state, source = self._read_tree()
        app, activity = self._focus_app_activity()
        state = snapshot_state(elements, {"app": app, "activity": activity}, source, self.screen)
        if source != "uiautomator":
            state["keyboard_visible"] = bool(phone_state.get("keyboardVisible"))
        if screenshot:
            state["screenshot"] = base64.b64encode(self._screencap_bytes()).decode("ascii")
        self.last_source = source
        return state

    def fresh(self, page: Dict[str, Any]) -> bool:
        """Full semantic comparison; used before accepting DONE or BLOCKED."""
        return self.observe()["fingerprint"] == page["fingerprint"]

    def activity_changed(self, page: Dict[str, Any]) -> bool:
        """Cheap guard: the focused window differs from the observed one."""
        try:
            return self._focus_app_activity() != (page["app"], page["activity"])
        except RuntimeError:
            return False

    def _read_tree(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any], str]:
        if self._portal_usable is not False:
            portal = self._portal_tree()
            # A portal tree without interaction flags cannot drive CLICK/TYPE_TEXT decisions.
            usable = portal is not None and _tree_has_interaction_flags(portal[0])
            self._portal_usable = usable
            if usable:
                return portal
        last_error = ""
        for attempt in range(3):
            remote = "/sdcard/jev_mobile_dump_%d.xml" % int(time.time() * 1000)
            dump = self._run("shell", "uiautomator", "dump", remote)
            if dump.returncode != 0:
                last_error = (dump.stderr or dump.stdout or "").strip()
                time.sleep(0.5)
                continue
            cat = self._run("shell", "cat", remote)
            self._run("shell", "rm", remote)
            if cat.returncode == 0 and cat.stdout and cat.stdout.strip().startswith("<"):
                return parse_uiautomator_xml(cat.stdout), {}, "uiautomator"
            last_error = "empty uiautomator dump"
            time.sleep(0.5)
        raise RuntimeError("Could not read the A11Y tree: %s" % last_error)

    def _portal_tree(self) -> Optional[Tuple[List[Dict[str, Any]], Dict[str, Any], str]]:
        for source, state_uri in PORTAL_STATE_URIS:
            res = self._run("shell", "content", "query", "--uri", state_uri)
            if res.returncode != 0 or not (res.stdout or "").strip():
                continue
            row = parse_content_provider_output(res.stdout)
            if not row:
                continue
            data = row.get("data")
            if data is None and row.get("status") == "success":
                data = row.get("result")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    continue
            if not isinstance(data, dict):
                data = row
            tree = data.get("a11y_tree")
            if isinstance(tree, dict):
                # The full-state endpoint returns one root object instead of a root list.
                tree = [tree]
            if not isinstance(tree, list):
                continue
            phone_state = data.get("phone_state")
            if not isinstance(phone_state, dict):
                phone_state = {}
            return normalize_tree(tree), phone_state, source
        return None

    def _focus_app_activity(self) -> Tuple[str, str]:
        res = self._shell("dumpsys window")
        match = re.search(r"mCurrentFocus=Window\{[^}]*\s(\S+)/(\S+?)(?:\s|\})", res.stdout or "")
        if not match:
            return "", ""
        return match.group(1), match.group(2)

    def _screencap_bytes(self) -> bytes:
        for _ in range(3):
            res = self._run("exec-out", "screencap", "-p", text=False)
            data = res.stdout or b""
            if res.returncode == 0 and data[:8] == b"\x89PNG\r\n\x1a\n":
                return data
            time.sleep(0.1)
        raise RuntimeError("Failed to capture a screenshot")

    # -- execution --------------------------------------------------------

    def act(self, action: Dict[str, Any], text: Optional[str] = None) -> None:
        kind = action["kind"]
        if kind == "click":
            self._tap(action["center"])
        elif kind == "fill":
            self._tap(action["center"])
            self._wait_keyboard()
            self._send_text(text or "", delete=len(str(action.get("value") or "")))
            time.sleep(TYPE_SETTLE_S)
        elif kind == "scroll":
            self._swipe_scroll(action["center"], action["direction"])
        elif kind == "key":
            self._run("shell", "input", "keyevent", int(action["keycode"]))
        elif kind == "home":
            self._run(
                "shell", "am", "start",
                "-a", "android.intent.action.MAIN",
                "-c", "android.intent.category.HOME",
            )
        elif kind == "wait":
            time.sleep(WAIT_S)
        else:
            raise ValueError("Unsupported action kind: %r" % kind)
        if self.last_source != "uiautomator":
            # The portal read is fast; give animations a moment before the next observation.
            time.sleep(PORTAL_SETTLE_S)

    def launch(self, package: str) -> None:
        self._run(
            "shell", "monkey", "-p", package,
            "-c", "android.intent.category.LAUNCHER",
            "1",
        )
        time.sleep(LAUNCH_SETTLE_S)

    def _tap(self, center: List[int]) -> None:
        width, height = self.screen
        x = min(max(int(center[0]), 1), width - 1)
        y = min(max(int(center[1]), 1), height - 1)
        self._run("shell", "input", "tap", x, y)

    def _swipe_scroll(self, center: List[int], direction: str) -> None:
        _, height = self.screen
        span = int(height * 0.2)
        x, y = int(center[0]), int(center[1])
        if direction == "down":
            start, end = y + span, y - span
        else:
            start, end = y - span, y + span
        start = min(max(start, 1), height - 1)
        end = min(max(end, 1), height - 1)
        self._run("shell", "input", "swipe", x, start, x, end, SCROLL_SWIPE_MS)

    def _wait_keyboard(self) -> None:
        if self.last_source == "uiautomator":
            time.sleep(0.45)
            return
        deadline = time.monotonic() + KEYBOARD_WAIT_S
        while time.monotonic() < deadline:
            portal = self._portal_tree()
            if portal is not None and portal[1].get("keyboardVisible"):
                return
            time.sleep(0.1)

    def _send_text(self, text: str, delete: int = 0) -> None:
        if self._portal_keyboard_ready():
            # One round trip; the endpoint clears the field itself before typing.
            encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
            for uri in PORTAL_KEYBOARD_URIS:
                res = self._run("shell", "content", "insert", "--uri", uri, "--bind", "base64_text:s:" + encoded)
                if res.returncode == 0 and "Error" not in (res.stdout or "") + (res.stderr or ""):
                    return
            raise RuntimeError("Portal keyboard input failed; nothing typed.")
        if self._use_adb_keyboard():
            self._delete_chars(delete)
            escaped = text.replace("'", "'\\''")
            self._shell("am broadcast -a ADB_INPUT_TEXT --es msg '%s'" % escaped)
            return
        if not re.fullmatch(r"[ -~]+", text):
            raise RuntimeError("Non-ASCII text needs the Portal keyboard or ADB Keyboard installed on the device.")
        self._delete_chars(delete)
        safe = text.replace("%", "%%").replace(" ", "%s")
        self._shell("input text '%s'" % safe.replace("'", "'\\''"))

    def _delete_chars(self, count: int) -> None:
        count = min(max(int(count or 0), 0), 100)
        if count <= 0:
            return
        # One round trip: move the cursor to the end, then delete in place.
        self._shell("input keyevent 123; for i in $(seq 1 %d); do input keyevent 67; done" % count)

    def _portal_keyboard_ready(self) -> bool:
        if self._portal_keyboard is None:
            current = self._current_ime()
            self._portal_keyboard = current.startswith(PORTAL_IME_PREFIXES)
        return self._portal_keyboard

    def _use_adb_keyboard(self) -> bool:
        if self._saved_ime is None:
            listed = self._run("shell", "ime", "list", "-s")
            if ADB_KEYBOARD not in (listed.stdout or ""):
                self._saved_ime = ""
                return False
            if self._current_ime() != ADB_KEYBOARD:
                current = self._shell("settings get secure default_input_method")
                self._saved_ime = (current.stdout or "").strip() or ADB_KEYBOARD
                switched = self._run("shell", "ime", "set", ADB_KEYBOARD)
                if switched.returncode != 0:
                    # Some builds disable the shell `ime` command entirely.
                    self._saved_ime = ""
                    return False
                # The broadcast is dropped unless AdbIME is the bound method, not just the
                # selected one; wait for the switch to complete before typing.
                deadline = time.monotonic() + IME_SWITCH_WAIT_S
                while time.monotonic() < deadline:
                    if self._current_ime() == ADB_KEYBOARD:
                        break
                    time.sleep(0.15)
            else:
                self._saved_ime = ADB_KEYBOARD
        return bool(self._saved_ime)

    def _current_ime(self) -> str:
        res = self._shell("dumpsys input_method")
        match = re.search(r"mCurMethodId=(\S+)", res.stdout or "")
        return match.group(1) if match else ""

    def close(self) -> None:
        if self._saved_ime and self._saved_ime != ADB_KEYBOARD:
            self._run("shell", "ime", "set", self._saved_ime)
        self._saved_ime = None
