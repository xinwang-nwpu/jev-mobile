"""Standalone capture exports and frame-coordinate duplicate box rendering."""

import base64
import io
import json

from PIL import Image

from jev_mobile import visual_images
from jev_mobile.a11y import snapshot_state
from jev_mobile.selectors import annotate_duplicates
from script import capture_a11y
from helpers import node


def screenshot(size):
    output = io.BytesIO()
    Image.new("RGB", size, "white").save(output, format="PNG")
    return output.getvalue()


def test_boxes_use_duplicate_colors_and_native_coordinates_without_drawing_hidden_nodes():
    tree = [node(rid="same", bounds="[40,100][200,200]"),
            node(rid="same", bounds="[240,100][400,200]"),
            node(rid="unique", bounds="[40,300][200,400]"),
            {**node(rid="hidden", bounds="[40,500][200,600]"), "isVisibleToUser": False}]
    report = annotate_duplicates(tree)
    image, count = capture_a11y.draw_boxes(base64.b64encode(screenshot((400, 800))), report["tree"], (800, 1600))
    assert count == 3
    with Image.open(io.BytesIO(image)) as rendered:
        assert rendered.size == (400, 800)
        assert rendered.getpixel((20, 70)) == (229, 57, 53)
        assert rendered.getpixel((120, 70)) == (229, 57, 53)
        assert rendered.getpixel((20, 170)) == (0, 135, 90)
        assert rendered.getpixel((20, 270)) == (255, 255, 255)


def test_capture_writes_json_and_png_without_models_or_phone_actions(monkeypatch, tmp_path):
    tree = [node(text="same", clickable=True), node(text="same", clickable=True)]
    prepared = visual_images.prepare(screenshot((1080, 2340)))
    page = snapshot_state(tree, {"app": "com.example", "activity": "Main"}, "vision", (1080, 2340))
    page.update(prepared, a11y_source="test", a11y_fingerprint="tree")
    calls = []
    class Device:
        def __init__(self, adb_path, serial, *, prepare_portal):
            calls.append((adb_path, serial, prepare_portal))
        def observe_visual(self):
            calls.append("read")
            return page
        def close(self):
            calls.append("close")
    monkeypatch.setattr(capture_a11y, "Device", Device)
    result = capture_a11y.capture("adb", "device", tmp_path)
    report = json.loads((tmp_path / "a11y.json").read_text(encoding="utf-8"))
    assert report["node_count"] == report["duplicate_node_count"] == report["boxed_node_count"] == 2
    assert report["duplicate_group_count"] == 1 and report["source"] == "test"
    assert report["tree"][0]["is_duplicate"] is True
    assert result["screenshot_file"] == str(tmp_path / "screen-boxed.png")
    with Image.open(result["screenshot_file"]) as image:
        assert image.size == tuple(prepared["model_screen"])
    assert calls == [("adb", "device", False), "read", "close"]


def test_cli_uses_config_and_device_override(monkeypatch, tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("adb_path: configured-adb\ndevice: configured-device\n", encoding="utf-8")
    output = tmp_path / "out"
    calls = []
    def capture(adb_path, serial, output_dir):
        calls.append((adb_path, serial, output_dir))
        return {"screenshot_file": str(output / "screen-boxed.png")}
    monkeypatch.setattr(capture_a11y, "capture", capture)
    assert capture_a11y.main(["--config", str(config), "--device", "chosen-device", "--output-dir", str(output)]) == 0
    assert calls == [("configured-adb", "chosen-device", str(output))]
    assert json.loads(capsys.readouterr().out)["screenshot_file"].endswith("screen-boxed.png")


def test_capture_reports_missing_tree_and_closes_device(monkeypatch, tmp_path, capsys):
    calls = []
    class Device:
        def __init__(self, *args, **kwargs):
            pass
        def observe_visual(self):
            return {"a11y_fingerprint": None, "a11y_error": "A11Y unavailable"}
        def close(self):
            calls.append("close")
    monkeypatch.setattr(capture_a11y, "Device", Device)
    config = tmp_path / "config.yaml"
    config.write_text("{}", encoding="utf-8")
    output = tmp_path / "out"
    assert capture_a11y.main(["--config", str(config), "--output-dir", str(output)]) == 1
    assert "A11Y unavailable" in capsys.readouterr().err
    assert calls == ["close"] and not output.exists()
