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
        for coordinate in range(0, 1001, 100):
            x, y = round(coordinate * (width - 1) / 1000), round(coordinate * (height - 1) / 1000)
            color = (255, 225, 130, 115) if coordinate in (0, 500, 1000) else (255, 255, 255, 60)
            draw.line((x, 0, x, height - 1), fill=color)
            draw.line((0, y, width - 1, y), fill=color)
        # Paint labels last so grid lines cannot cross their backgrounds.
        for coordinate in range(0, 1001, 100):
            x, y = round(coordinate * (width - 1) / 1000), round(coordinate * (height - 1) / 1000)
            for label, (left, top) in ((f"x={coordinate}", (x + 4, 4)),
                                       (f"y={coordinate}", (4, max(20, y + 4)))):
                box = draw.textbbox((0, 0), label)
                left = max(2, min(left, width - (box[2] - box[0]) - 3))
                top = max(2, min(top, height - (box[3] - box[1]) - 3))
                position = (left - box[0], top - box[1])
                draw.rectangle((left - 2, top - 2, left + box[2] - box[0] + 2,
                                top + box[3] - box[1] + 2), fill=(0, 0, 0, 150))
                draw.text((position[0] + 1, position[1] + 1), label, fill=(0, 0, 0, 230))
                draw.text(position, label, fill=(255, 255, 255, 245))
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
