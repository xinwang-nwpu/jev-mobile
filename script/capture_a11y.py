"""Read one phone screen, mark duplicate A11Y nodes, and export a boxed screenshot."""

import argparse
import base64
import io
import json
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

from jev_mobile.a11y import parse_bounds
from jev_mobile.config import load_config
from jev_mobile.device import Device
from jev_mobile.selectors import _walk, annotate_duplicates


def draw_boxes(screenshot, tree, screen):
    """Red means a four-field duplicate; green means a unique visible node."""
    with Image.open(io.BytesIO(base64.b64decode(screenshot))) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    boxes = []
    for node in _walk(tree):
        if node.get("is_duplicate") is None:
            continue
        bounds = parse_bounds(node.get("bounds"))
        if not bounds or node.get("isVisibleToUser") is False:
            continue
        left, top, right, bottom = bounds
        if right <= left or bottom <= top or right <= 0 or bottom <= 0 or left >= screen[0] or top >= screen[1]:
            continue
        scaled = [max(0, min(image.size[axis % 2] - 1,
                            round(value * (image.size[axis % 2] - 1) / (screen[axis % 2] - 1))))
                  for axis, value in enumerate(bounds)]
        if scaled[2] <= scaled[0] or scaled[3] <= scaled[1]:
            continue
        boxes.append((bool(node["is_duplicate"]), scaled))
    # Draw duplicate boxes last so coincident unique bounds cannot hide them.
    for duplicate, bounds in sorted(boxes):
        draw.rectangle(bounds, outline="#E53935" if duplicate else "#00875A", width=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), len(boxes)


def capture(adb_path, serial, output_dir):
    with closing(Device(adb_path, serial, prepare_portal=False)) as device:
        page = device.observe_visual()
    if not page.get("a11y_fingerprint"):
        raise RuntimeError("Cannot read A11Y tree. Turn on and unlock the phone and check Jev Bridge accessibility: "
                           + (page.get("a11y_error") or "no tree returned"))
    report = annotate_duplicates(page["a11y_tree"], page["screen"])
    boxed, box_count = draw_boxes(page["screenshot"], report["tree"], page["screen"])
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    tree_path, screenshot_path = output_dir / "a11y.json", output_dir / "screen-boxed.png"
    report.update({key: page.get(key) for key in ("app", "activity", "screen", "model_screen")})
    report.update(source=page.get("a11y_source"), boxed_node_count=box_count,
                  screenshot=screenshot_path.name)
    tree_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    screenshot_path.write_bytes(boxed)
    return {
        "app": page["app"], "source": report["source"],
        **{key: report[key] for key in ("node_count", "eligible_node_count", "duplicate_group_count", "duplicate_node_count")},
        "boxed_node_count": box_count,
        "colors": {"duplicate": "red", "unique": "green"},
        "a11y_file": str(tree_path), "screenshot_file": str(screenshot_path),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Configuration file; defaults to the project's config.yaml.")
    parser.add_argument("--adb-path", help="ADB executable; overrides config.yaml.")
    parser.add_argument("--device", help="Connected ADB device serial; overrides config.yaml.")
    parser.add_argument("--output-dir", help="Output directory for a11y.json and screen-boxed.png.")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        output = args.output_dir or ROOT / "runs" / "a11y_capture" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        result = capture(args.adb_path or config["adb_path"] or None,
                         args.device or config["device"], output)
    except (OSError, RuntimeError, ValueError) as error:
        print("Capture failed: %s" % error, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
