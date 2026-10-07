"""ADB device connection: one A11Y observation per snapshot, one input batch per action.

State prefers Jev Bridge HTTP over ADB forwarding, then the Portal content provider,
then a uiautomator XML dump. App and activity always come from the window focus line so the cheap
freshness signal and the observed state agree on one source.
"""

import base64
import http.client
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple
from xml.etree.ElementTree import ParseError

from . import visual_images

from .a11y import (
    PORTAL_STATE_URIS,
    fingerprint,
    normalize_tree,
    parse_content_provider_output,
    parse_uiautomator_xml,
    snapshot_state,
)

ADB_KEYBOARD = "com.android.adbkeyboard/.AdbIME"
# The portal's own IME serves the keyboard/input insert endpoint through its live
# InputConnection, so text works even where the shell `ime` command is disabled.
PORTAL_PACKAGES = ("ai.jev.bridge", "com.mobilerun.portal", "com.droidrun.portal")
PORTAL_IME_PREFIXES = tuple(package + "/" for package in PORTAL_PACKAGES)
PORTAL_KEYBOARD_URIS = (
    "content://ai.jev.bridge/keyboard/input",
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


def _extract_xml(output: str) -> str:
    """The XML inside a uiautomator dump stream; empty when the dump produced none."""
    for marker in ("<?xml", "<hierarchy"):
        index = output.find(marker)
        if index >= 0:
            return output[index:]
    return ""


class Device:
    def __init__(self, adb_path: Optional[str] = None, device: Optional[str] = None, *, prepare_portal: bool = True):
        self.adb_path = adb_path or os.environ.get("ADB_PATH", "adb")
        self.device = device or os.environ.get("ANDROID_DEVICE") or None
        self.screen = self._read_screen()
        self.last_source = ""
        self._portal_usable: Optional[bool] = None
        self._portal_keyboard: Optional[bool] = None
        self._saved_ime: Optional[str] = None
        self._visual_a11y_error: Optional[str] = None
        self.timings = []
        self._bridge_http_checked = False
        self._bridge_http = None
        self._bridge_forward = None
        if prepare_portal:
            try:
                self._ensure_portal_started()
            except RuntimeError:
                # Portal is an optional accelerator; a failed probe must not prevent recovery.
                self._portal_usable = False

    # -- shell helpers ----------------------------------------------------

    @contextmanager
    def _timed(self, stage):
        started, success = time.perf_counter(), False
        try:
            yield
            success = True
        finally:
            if not hasattr(self, "timings"):
                self.timings = []
            self.timings.append({"stage": stage, "started_at": started,
                                 "duration_ms": round((time.perf_counter() - started) * 1000), "success": success})

    def _measure(self, stage, function, *args, **kwargs):
        with self._timed(stage):
            return function(*args, **kwargs)

    def _run(self, *args: Any, text: bool = True) -> subprocess.CompletedProcess:
        cmd = [self.adb_path]
        if self.device:
            cmd.extend(["-s", self.device])
        cmd.extend(str(arg) for arg in args)
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=text,
                encoding="utf-8" if text else None,
                errors="replace" if text else None,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("ADB command timed out after 30 seconds") from None

    def _shell(self, command: str) -> subprocess.CompletedProcess:
        return self._run("shell", command)

    def _checked(self, *args):
        result = self._run(*args)
        if result.returncode != 0:
            raise RuntimeError("ADB operation failed (exit %d)" % result.returncode)
        return result

    def _read_screen(self) -> Tuple[int, int]:
        res = self._shell("wm size")
        sizes = re.findall(r"(\d+)x(\d+)", res.stdout or "")
        if not sizes:
            raise RuntimeError("Cannot read screen size; is a device connected? %s" % (res.stderr or "").strip())
        width, height = (int(v) for v in sizes[-1])  # Override size wins when present.
        return width, height

    # -- observation ------------------------------------------------------

    def observe(self, screenshot: bool = False) -> Dict[str, Any]:
        # Tree, focus, and screenshot are independent reads; run them concurrently so
        # one observation costs the slowest read, not the sum of all three.
        with ThreadPoolExecutor(max_workers=3) as pool:
            tree_future = pool.submit(self._measure, "observe.a11y", self._read_tree)
            focus_future = pool.submit(self._measure, "observe.focus", self._focus_app_activity)
            shot_future = pool.submit(self._measure, "observe.screenshot", self._screencap_bytes) if screenshot else None
            elements, phone_state, source = tree_future.result()
            app, activity = focus_future.result()
        state = snapshot_state(elements, {"app": app, "activity": activity}, source, self.screen)
        if source != "uiautomator":
            state["keyboard_visible"] = bool(phone_state.get("keyboardVisible"))
        if screenshot:
            state["screenshot"] = base64.b64encode(shot_future.result()).decode("ascii")
        self.last_source = source
        return state

    def fresh(self, page: Dict[str, Any]) -> bool:
        """Full semantic comparison; used before accepting DONE or BLOCKED."""
        return self.observe()["fingerprint"] == page["fingerprint"]

    def observe_visual(self) -> Dict[str, Any]:
        """Observe pixels with optional indexed A11Y; a failed tree cannot block recovery."""
        with self._timed("observe_visual.total"):
            return self._observe_visual()

    def _observe_visual(self):
        app, activity = self._measure("observe_visual.focus", self._focus_app_activity)
        elements, tree_source = [], None
        error = getattr(self, "_visual_a11y_error", None)
        # Independent reads share one observation window. Prepare the screenshot
        # while the optional tree query is in flight, then confirm the window.
        with ThreadPoolExecutor(max_workers=1) as pool:
            tree_future = (pool.submit(self._measure, "observe_visual.a11y", self._read_tree, attempts=1)
                           if not error else None)
            image = self._measure("observe_visual.screenshot", self._screencap_bytes)
            try:
                prepared = self._measure("observe_visual.image_prepare", visual_images.prepare, image)
            except (OSError, ValueError) as failure:
                raise RuntimeError("Cannot prepare visual screenshot: %s" % failure) from None
            self.screen = tuple(prepared["screen"])
            if tree_future:
                try:
                    elements, _, tree_source = tree_future.result()
                except (OSError, RuntimeError, ValueError, ParseError) as failure:
                    # ponytail: stop probing after a failed tree for this run; coordinates
                    # keep working without repeated slow dumps. Retry on a fresh Device.
                    error = self._visual_a11y_error = (str(failure) or type(failure).__name__)[:500]
                if self._measure("observe_visual.confirm_focus", self._focus_app_activity) != (app, activity):
                    elements, tree_source = [], None
                    error = "Window changed while observing; indexes unavailable for this image"
        page = snapshot_state(elements, {"app": app, "activity": activity}, "vision", self.screen)
        page.update(a11y_source=tree_source, a11y_error=error,
                    a11y_fingerprint=fingerprint({**page, "text": ""}) if tree_source else None)
        page.update(prepared)
        self.last_source = "vision"
        return page

    def fresh_index(self, page: Dict[str, Any]) -> bool:
        """A number belongs to one semantic snapshot, not to a screen across time."""
        with self._timed("guard.index.total"):
            if not page.get("a11y_fingerprint") or getattr(self, "_visual_a11y_error", None):
                return False
            try:
                window = self._measure("guard.index.window", self._index_window)
                if window is None:
                    # OEM dumps can omit geometry. Preserve the full guard rather
                    # than guessing rotation from cached wm size.
                    fresh = self._measure("guard.index.fallback", self.observe_visual)
                    return bool(fresh.get("a11y_fingerprint") and all(fresh.get(k) == page.get(k) for k in
                                ("app", "activity", "screen", "a11y_fingerprint")))
                app, activity, screen = window
                if (app, activity) != (page["app"], page["activity"]) or list(screen) != list(page["screen"]):
                    return False
                elements, _, _ = self._measure("guard.index.a11y", self._read_tree, attempts=1)
                current = snapshot_state(elements, {"app": app, "activity": activity}, "vision", screen)
                return (fingerprint({**current, "text": ""}) == page["a11y_fingerprint"]
                        and self._measure("guard.index.confirm_window", self._index_window) == window)
            except (OSError, RuntimeError, ValueError, ParseError):
                return False

    def _index_window(self):
        result = self._shell("dumpsys window displays | grep -E 'Display: mDisplayId=|cur=|mCurrentFocus='")
        block = re.search(r"Display: mDisplayId=0\b(.*?)(?=Display: mDisplayId=|\Z)",
                          result.stdout or "", re.DOTALL) if result.returncode == 0 else None
        if block:
            size = re.search(r"\bcur=(\d+)x(\d+)", block[1])
            focus = re.search(r"mCurrentFocus=Window\{[^}]*\s(\S+)/(\S+?)(?:\s|\})", block[1])
            if size and focus and all(int(n) > 1 for n in size.groups()):
                return focus[1], focus[2], tuple(int(n) for n in size.groups())
        return None

    def activity_changed(self, page: Dict[str, Any]) -> bool:
        """Cheap guard: the focused window differs from the observed one."""
        try:
            return self._measure("guard.focus", self._focus_app_activity) != (page["app"], page["activity"])
        except RuntimeError:
            return False

    def _read_tree(self, attempts: int = 3) -> Tuple[List[Dict[str, Any]], Dict[str, Any], str]:
        if self._portal_usable is not False:
            portal = self._portal_tree()
            # A portal tree without interaction flags cannot drive CLICK/TYPE_TEXT decisions.
            usable = portal is not None and _tree_has_interaction_flags(portal[0])
            self._portal_usable = usable
            if usable:
                return portal
        last_error = ""
        for _ in range(attempts):
            # /dev/tty streams the XML back on stdout: one round trip instead of dump+cat+rm.
            res = self._shell("uiautomator dump /dev/tty")
            xml = _extract_xml(res.stdout or "")
            if xml:
                return parse_uiautomator_xml(xml), {}, "uiautomator"
            # The dump always prints a reason (e.g. "could not get idle state"); keep it.
            detail = " | ".join(s.strip() for s in (res.stdout, res.stderr) if s and s.strip())
            if detail:
                last_error = detail[:300]
            if attempts == 1:
                raise RuntimeError("Optional A11Y read failed: " + (last_error or "empty dump"))
            # A crashed dump leaves a uiautomator process that makes every retry return
            # empty; kill it before trying the file fallback (the standard remedy).
            self._shell("pkill uiautomator 2>/dev/null")
            # Some builds refuse to dump to a character device; try a file, still in one round trip.
            remote = "/sdcard/jev_mobile_dump_%d.xml" % os.getpid()
            combined = self._shell(
                "uiautomator dump %s >/dev/null 2>&1; cat %s 2>/dev/null; rm -f %s" % (remote, remote, remote)
            )
            xml = _extract_xml(combined.stdout or "")
            if xml:
                return parse_uiautomator_xml(xml), {}, "uiautomator"
            time.sleep(0.5)
        raise RuntimeError(
            "Could not read the A11Y tree: %s (display=%s; the screen must be on and unlocked)"
            % (last_error or "empty dump", self._display_state())
        )

    def _display_state(self) -> str:
        res = self._shell("dumpsys power | grep -m1 mWakefulness")
        return (res.stdout or "").strip().split("=")[-1] or "unknown"

    def _portal_tree(self) -> Optional[Tuple[List[Dict[str, Any]], Dict[str, Any], str]]:
        row = self._bridge_request("GET", "/state_full")
        if row is not None:
            state = self._decode_portal_state(row, "jev_bridge")
            if state is not None:
                return state
            self._bridge_http = None
        for source, state_uri in PORTAL_STATE_URIS:
            res = self._measure("a11y.portal_query", self._run, "shell", "content", "query", "--uri", state_uri)
            if res.returncode != 0 or not (res.stdout or "").strip():
                continue
            row = parse_content_provider_output(res.stdout)
            state = self._decode_portal_state(row, source)
            if state is not None:
                return state
        return None

    @staticmethod
    def _decode_portal_state(row, source):
        if not isinstance(row, dict) or row.get("status") == "error":
            return None
        data = row.get("data")
        if data is None and row.get("status") == "success":
            data = row.get("result")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                return None
        if not isinstance(data, dict):
            data = row
        tree = data.get("a11y_tree")
        if isinstance(tree, dict):
            tree = [tree]
        if not isinstance(tree, list):
            return None
        phone = data.get("phone_state")
        return normalize_tree(tree), phone if isinstance(phone, dict) else {}, source

    def _connect_bridge_http(self):
        # One ADB discovery per Device, including old APKs without HTTP support.
        if getattr(self, "_bridge_http_checked", True):
            return
        self._bridge_http_checked = True
        try:
            result = self._measure("bridge.http_connect", self._run, "shell", "content", "query",
                                   "--uri", "content://ai.jev.bridge/http_info")
            row = parse_content_provider_output(result.stdout or "")
            info = row.get("result") if isinstance(row, dict) and row.get("status") == "success" else None
            if not isinstance(info, dict) or info.get("protocol") != 1:
                return
            port, token = info.get("port"), info.get("token")
            if not isinstance(port, int) or not 0 < port < 65536 or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,100}", token):
                return
            forward = self._run("forward", "tcp:0", "tcp:%d" % port)
            if forward.returncode != 0:
                return
            local = int((forward.stdout or "").strip())
            if not 0 < local < 65536:
                return
            self._bridge_forward = local  # This Device owns only the forward it created.
            self._bridge_http = (local, token)
        except (OSError, RuntimeError, ValueError):
            self._bridge_http = None

    def _bridge_request(self, method, path, payload=None):
        self._connect_bridge_http()
        endpoint = getattr(self, "_bridge_http", None)
        if endpoint is None:
            return None
        connection = http.client.HTTPConnection("127.0.0.1", endpoint[0], timeout=3)
        try:
            try:
                # A failure before connecting is safe to fall back, including for writes.
                connection.connect()
            except OSError:
                self._bridge_http = None
                return None
            try:
                with self._timed("a11y.http_query" if method == "GET" else "input.http"):
                    connection.request(method, path,
                        body=json.dumps(payload).encode("utf-8") if payload is not None else None,
                        headers={"Authorization": "Bearer " + endpoint[1], "Content-Type": "application/json"})
                    response = connection.getresponse()
                    # These rejections occur before the editor is called.
                    if response.status in (401, 403, 404):
                        self._bridge_http = None
                        return None
                    raw = response.read(8 * 1024 * 1024 + 1)
                    if len(raw) > 8 * 1024 * 1024:
                        raise ValueError("Bridge response exceeds limit")
                    row = json.loads(raw)
                    if response.status != 200 or not isinstance(row, dict) or row.get("status") != "success":
                        detail = row.get("message") if isinstance(row, dict) else None
                        raise ValueError(detail[:300] if isinstance(detail, str) else "Bridge HTTP %d rejected" % response.status)
                    if method == "POST" and row.get("result") not in ("verified", "accepted_unverified"):
                        raise ValueError("Bridge input returned an unknown result")
                    return row
            except (OSError, ValueError, http.client.HTTPException) as failure:
                self._bridge_http = None
                if method == "POST":
                    raise RuntimeError("Jev Bridge HTTP input failed or its outcome is unknown (%s); inspect the field before retrying."
                                       % str(failure)[:300]) from None
                return None
        finally:
            connection.close()

    def _ensure_portal_started(self) -> None:
        """A freshly installed Portal sits in the stopped state, where Android hides its
        ContentProvider entirely; launching it once makes the tree and keyboard endpoints
        reachable for the rest of the run (and for future runs)."""
        res = self._shell("pm list packages")
        packages = [p for p in PORTAL_PACKAGES if p in (res.stdout or "")]
        if not packages or self._portal_tree() is not None:
            return
        for package in packages:
            self._run("shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(LAUNCH_SETTLE_S)

    def _focus_app_activity(self) -> Tuple[str, str]:
        # The full dumpsys window output is hundreds of KB over the wire; grep on the device.
        res = self._shell("dumpsys window | grep -m1 mCurrentFocus")
        match = re.search(r"mCurrentFocus=Window\{[^}]*\s(\S+)/(\S+?)(?:\s|\})", res.stdout or "")
        if not match:
            return "", ""
        return match.group(1), match.group(2)

    def _screencap_bytes(self) -> bytes:
        for _ in range(3):
            res = self._measure("capture.screencap", self._run, "exec-out", "screencap", "-p", text=False)
            data = res.stdout or b""
            if res.returncode == 0 and data[:8] == b"\x89PNG\r\n\x1a\n":
                return data
            time.sleep(0.1)
        raise RuntimeError("Failed to capture a screenshot")

    # -- execution --------------------------------------------------------

    def act(self, action: Dict[str, Any], text: Optional[str] = None) -> None:
        with self._timed("action.total"):
            self._act(action, text)

    def _act(self, action, text):
        kind = action["kind"]
        if kind == "click":
            self._tap(action["center"])
        elif kind == "long_press":
            x, y = action["center"]
            self._checked("shell", "input", "swipe", x, y, x, y, action.get("duration_ms", 700))
        elif kind == "swipe":
            self._checked("shell", "input", "swipe", *action["start"], *action["end"], action.get("duration_ms", SCROLL_SWIPE_MS))
        elif kind == "fill":
            if "center" in action:
                self._measure("input.focus", self._tap, action["center"])
            self._measure("input.keyboard_wait", self._wait_keyboard)
            self._measure("input.text", self._send_text, text or "", delete=len(str(action.get("value") or "")))
            self._measure("input.settle", time.sleep, TYPE_SETTLE_S)
        elif kind == "scroll":
            self._swipe_scroll(action["center"], action["direction"])
        elif kind == "key":
            self._checked("shell", "input", "keyevent", int(action["keycode"]))
        elif kind == "home":
            self._checked(
                "shell", "am", "start",
                "-a", "android.intent.action.MAIN",
                "-c", "android.intent.category.HOME",
            )
        elif kind == "wait":
            time.sleep(action.get("duration", WAIT_S))
        elif kind == "launch":
            self.launch(action["package"])
        else:
            raise ValueError("Unsupported action kind: %r" % kind)
        if self.last_source != "uiautomator":
            # The portal read is fast; give animations a moment before the next observation.
            self._measure("action.settle", time.sleep, PORTAL_SETTLE_S)

    def launch(self, package: str) -> None:
        if not isinstance(package, str) or not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", package):
            raise ValueError("Invalid Android package identifier")
        result = self._checked(
            "shell", "monkey", "-p", package,
            "-c", "android.intent.category.LAUNCHER",
            "1",
        )
        output = (result.stdout or "") + (result.stderr or "")
        if "No activities found" in output or "Error:" in output:
            raise RuntimeError("The installed package has no launchable activity")
        time.sleep(LAUNCH_SETTLE_S)

    def list_apps(self):
        result = self._measure("apps.list", self._checked, "shell", "pm", "list", "packages")
        return sorted(set(re.findall(r"^package:([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)$", result.stdout or "", re.MULTILINE)))

    def _tap(self, center: List[int]) -> None:
        width, height = self.screen
        x = min(max(int(center[0]), 1), width - 1)
        y = min(max(int(center[1]), 1), height - 1)
        self._checked("shell", "input", "tap", x, y)

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
        self._checked("shell", "input", "swipe", x, start, x, end, SCROLL_SWIPE_MS)

    def _wait_keyboard(self) -> None:
        if self.last_source in {"uiautomator", "vision"}:
            time.sleep(0.45)
            return
        deadline = time.monotonic() + KEYBOARD_WAIT_S
        while time.monotonic() < deadline:
            portal = self._portal_tree()
            if portal is not None and portal[1].get("keyboardVisible"):
                return
            time.sleep(0.1)

    def _send_text(self, text: str, delete: int = 0) -> None:
        if self._measure("input.portal_ime", self._portal_keyboard_ready):
            # One round trip; the endpoint clears the field itself before typing.
            encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
            if getattr(self, "_portal_ime_package", None) == "ai.jev.bridge":
                if self._bridge_request("POST", "/keyboard/input", {"base64_text": encoded, "clear": True}) is not None:
                    return
            for uri in PORTAL_KEYBOARD_URIS:
                res = self._run("shell", "content", "insert", "--uri", uri, "--bind", "base64_text:s:" + encoded)
                output = (res.stdout or "") + (res.stderr or "")
                if res.returncode == 0 and "Error" not in output and "status=error" not in output:
                    return
            raise RuntimeError("Jev Bridge / Portal keyboard input failed; inspect the field before retrying.")
        if self._measure("input.adb_ime", self._use_adb_keyboard):
            self._delete_chars(delete)
            escaped = text.replace("'", "'\\''")
            self._checked("shell", "am broadcast -a ADB_INPUT_TEXT --es msg '%s'" % escaped)
            return
        if not re.fullmatch(r"[ -~]+", text):
            raise RuntimeError("Non-ASCII text needs Jev Bridge, the Portal keyboard or ADB Keyboard installed on the device.")
        self._delete_chars(delete)
        safe = text.replace("%", "%%").replace(" ", "%s")
        self._checked("shell", "input text '%s'" % safe.replace("'", "'\\''"))

    def _delete_chars(self, count: int) -> None:
        count = min(max(int(count or 0), 0), 2000)
        if count <= 0:
            return
        # One round trip: move the cursor to the end, then delete in place.
        self._checked("shell", "input keyevent 123; for i in $(seq 1 %d); do input keyevent 67; done" % count)

    def _portal_keyboard_ready(self) -> bool:
        if self._portal_keyboard is None:
            current = self._current_ime()
            # Bound already? Use it. Installed but not bound? Switch to it like the
            # ADB Keyboard path does, so Chinese input works out of the box.
            if current.startswith(PORTAL_IME_PREFIXES):
                self._portal_ime_package = current.split("/", 1)[0]
                self._portal_keyboard = True
            else:
                self._portal_keyboard = self._switch_ime_to_portal()
        return self._portal_keyboard

    def _switch_ime_to_portal(self) -> bool:
        listed = self._run("shell", "ime", "list", "-s")
        installed = (listed.stdout or "").split()
        portal_ime = next(
            (ime for prefix in PORTAL_IME_PREFIXES for ime in installed if ime.startswith(prefix)),
            None,
        )
        if portal_ime is None:
            return False
        saved = self._shell("settings get secure default_input_method")
        if self._run("shell", "ime", "set", portal_ime).returncode != 0:
            # Some builds disable the shell `ime` command entirely.
            return False
        self._portal_ime_package = portal_ime.split("/", 1)[0]
        previous = (saved.stdout or "").strip()
        if previous and previous != portal_ime and not self._saved_ime:
            self._saved_ime = previous
        # Binding can lag the command; the insert endpoint itself is the real check,
        # so succeed here and let a failed insert raise with its own clearer message.
        return True

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
        # Android 16 reports the bound IME as mCurId; selected and bound may differ.
        match = re.search(r"\b(?:mCurMethodId|mCurId)=(\S+)", res.stdout or "")
        return match.group(1) if match else ""

    def close(self) -> None:
        self._bridge_http_checked = True
        self._bridge_http = None
        if getattr(self, "_bridge_forward", None) is not None:
            self._run("forward", "--remove", "tcp:%d" % self._bridge_forward)
            self._bridge_forward = None
        if self._saved_ime and self._saved_ime != ADB_KEYBOARD:
            self._run("shell", "ime", "set", self._saved_ime)
        self._saved_ime = None
