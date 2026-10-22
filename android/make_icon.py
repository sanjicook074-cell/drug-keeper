# -*- coding: utf-8 -*-
"""画 App 图标（蓝底胶囊），输出到 res/mipmap-*/ic_launcher.png。

★ 图标是**生成**的，不是手工放一张图进去 —— 想改配色就改这里再跑一遍。
   base 512 画完用 LANCZOS 缩到各档，比直接画小图干净。
"""
import os
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.abspath(__file__))
SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}

C1 = (59, 130, 246)      # 蓝，同页面 logo 的渐变起点 #3b82f6
C2 = (30, 64, 175)       # 深蓝 #1e40af


def base(size=512):
    # 竖向渐变底
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        grad.putpixel((0, y), tuple(int(C1[i] + (C2[i] - C1[i]) * t) for i in range(3)))
    grad = grad.resize((size, size))

    # 圆角蒙版（iOS/Android 常见的 22% 圆角）
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255)

    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.paste(grad, (0, 0), mask)

    # 胶囊：白色圆角长条，转 45°，压在正中
    cw, ch = int(size * 0.46), int(size * 0.235)
    cap = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(cap)
    d.rounded_rectangle([0, 0, cw - 1, ch - 1], radius=ch // 2, fill=(255, 255, 255, 255))
    # 中间一道缝，让"胶囊"这层意思读得出来
    d.line([(cw // 2, int(ch * 0.16)), (cw // 2, int(ch * 0.84))],
           fill=C2 + (110,), width=max(2, size // 96))
    cap = cap.rotate(45, expand=True, resample=Image.BICUBIC)
    icon.alpha_composite(cap, ((size - cap.width) // 2, (size - cap.height) // 2))
    return icon


def main():
    img = base(512)
    for name, px in SIZES.items():
        out_dir = os.path.join(ROOT, "res", "mipmap-" + name)
        os.makedirs(out_dir, exist_ok=True)
        img.resize((px, px), Image.LANCZOS).save(os.path.join(out_dir, "ic_launcher.png"))
        print("icon -> res/mipmap-%s/ic_launcher.png (%dpx)" % (name, px))


if __name__ == "__main__":
    main()
