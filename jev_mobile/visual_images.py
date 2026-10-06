"""One explicit image contract for all visual roles; never crop the device screen."""

import base64
import hashlib
import io
import os

from PIL import Image, ImageDraw


def prepare(image):
    cap = int(os.environ.get("VISION_IMAGE_MAX_SIDE", "1600"))
    if not 320 <= cap <= 4096:
        raise ValueError("VISION_IMAGE_MAX_SIDE must be 320..4096")
    with Image.open(io.BytesIO(image)) as source:
        if source.format != "PNG" or not all(1 < n <= 16384 for n in source.size):
            raise ValueError("Invalid device screenshot")
        source.load()
        native = source.size
        source = source.convert("RGB")
        signature = base64.b64encode(source.resize((32, 32)).convert("L").tobytes()).decode("ascii")
        source.thumbnail((cap, cap), Image.Resampling.LANCZOS)
        overlay = Image.new("RGBA", source.size)
        draw = ImageDraw.Draw(overlay)
        width, height = source.size
        for coordinate in range(100, 1000, 100):
            x, y = round(coordinate * (width - 1) / 1000), round(coordinate * (height - 1) / 1000)
            draw.line((x, 0, x, height - 1), fill=(255, 255, 255, 35))
            draw.line((0, y, width - 1, y), fill=(255, 255, 255, 35))
            draw.text((x + 2, 2), str(coordinate), fill=(255, 70, 70, 220))
            draw.text((2, y + 2), str(coordinate), fill=(255, 70, 70, 220))
        rendered = Image.alpha_composite(source.convert("RGBA"), overlay).convert("RGB")
        buffer = io.BytesIO()
        rendered.save(buffer, format="PNG")
    return {
        "screen": list(native), "model_screen": list(rendered.size),
        "screenshot": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "fingerprint": hashlib.sha256(image).hexdigest(), "visual_signature": signature,
    }


def difference(before, after):
    if before.get("screen") != after.get("screen"):
        return 255.0
    if before.get("visual_signature") and after.get("visual_signature"):
        a, b = (base64.b64decode(p["visual_signature"]) for p in (before, after))
        if len(a) == len(b) == 1024:
            return sum(abs(x - y) for x, y in zip(a, b)) / len(a)
    return 0.0 if before.get("fingerprint") == after.get("fingerprint") else 255.0


def changed(before, after):
    threshold = float(os.environ.get("VISION_CHANGE_THRESHOLD", "3"))
    if not 0 <= threshold <= 255:
        raise ValueError("VISION_CHANGE_THRESHOLD must be 0..255")
    return difference(before, after) > threshold
