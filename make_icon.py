"""Рисует icon.ico и icon.png: красная плашка со стрелкой вниз. Запускать один раз."""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024  # рисуем крупно и уменьшаем, чтобы края были гладкими
here = Path(__file__).parent

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((40, 140, SIZE - 40, SIZE - 140), radius=200, fill=(230, 33, 23, 255))

white = (255, 255, 255, 255)
cx = SIZE // 2
d.rectangle((cx - 70, 250, cx + 70, 540), fill=white)  # древко стрелки
d.polygon([(cx - 210, 500), (cx + 210, 500), (cx, 710)], fill=white)  # наконечник
d.rounded_rectangle((cx - 240, 750, cx + 240, 820), radius=35, fill=white)  # «полка» под стрелкой

img.resize((256, 256), Image.LANCZOS).save(here / "icon.png")
img.save(here / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
