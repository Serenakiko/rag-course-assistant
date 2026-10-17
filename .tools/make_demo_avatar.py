# -*- coding: utf-8 -*-
"""生成演示头像：靛蓝渐变圆角方块 + 白色首字。"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 256
img = Image.new("RGB", (SIZE, SIZE), "#2563eb")
px = img.load()
top = (37, 99, 235)   # #2563eb
bottom = (14, 165, 233)  # #0ea5e9
for y in range(SIZE):
    t = y / (SIZE - 1)
    r = int(top[0] + (bottom[0] - top[0]) * t)
    g = int(top[1] + (bottom[1] - top[1]) * t)
    b = int(top[2] + (bottom[2] - top[2]) * t)
    for x in range(SIZE):
        px[x, y] = (r, g, b)

# 圆角遮罩
mask = Image.new("L", (SIZE, SIZE), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=56, fill=255)
img.putalpha(mask)

draw = ImageDraw.Draw(img)
font = None
for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\simhei.ttf"):
    try:
        font = ImageFont.truetype(fp, 120)
        break
    except Exception:
        continue
if font is None:
    font = ImageFont.load_default()
text = "管"
bbox = draw.textbbox((0, 0), text, font=font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
draw.text(((SIZE - tw) / 2 - bbox[0], (SIZE - th) / 2 - bbox[1]), text, font=font, fill=(255, 255, 255, 255))

out = Path(__file__).resolve().parent.parent / "data" / "demo_avatar.png"
out.parent.mkdir(parents=True, exist_ok=True)
img.save(out)
print("SAVED", out, img.size)
