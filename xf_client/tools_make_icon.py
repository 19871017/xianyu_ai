"""生成 Mac 应用图标：圆角渐变底 + 白色小鱼 + AI 火花。
输出 assets/app_icon.png (1024) 与 assets/AppIcon.icns。
"""
import os
import math
import subprocess
from PIL import Image, ImageDraw, ImageFilter

BASE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(BASE, "assets")
os.makedirs(ASSETS, exist_ok=True)

S = 1024
SS = S * 4  # 超采样抗锯齿


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size, size], radius=radius, fill=255)
    return m


def make_icon():
    img = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    # 垂直渐变背景（暖橙 -> 明黄，闲鱼调性）
    top = (255, 138, 0)
    bot = (255, 200, 38)
    grad = Image.new("RGB", (1, SS))
    gp = grad.load()
    for y in range(SS):
        gp[0, y] = lerp(top, bot, y / SS)
    grad = grad.resize((SS, SS))

    radius = int(SS * 0.225)  # macOS 圆角约 22.5%
    mask = rounded_mask(SS, radius)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    cx, cy = SS // 2, int(SS * 0.52)
    body_w = int(SS * 0.46)
    body_h = int(SS * 0.30)

    # 鱼身（白色椭圆）
    d.ellipse(
        [cx - body_w // 2, cy - body_h // 2, cx + body_w // 2, cy + body_h // 2],
        fill=(255, 255, 255, 255),
    )
    # 鱼尾（三角形，朝左）
    tail_x = cx - body_w // 2
    tail_size = int(SS * 0.13)
    d.polygon(
        [
            (tail_x + int(SS * 0.02), cy),
            (tail_x - tail_size, cy - tail_size),
            (tail_x - tail_size, cy + tail_size),
        ],
        fill=(255, 255, 255, 255),
    )
    # 鱼眼
    eye_x = cx + int(body_w * 0.26)
    eye_y = cy - int(body_h * 0.12)
    er = int(SS * 0.028)
    d.ellipse([eye_x - er, eye_y - er, eye_x + er, eye_y + er], fill=(255, 138, 0, 255))

    # AI 火花（右上角四角星 + 小星）
    def spark(scx, scy, r, color):
        pts = []
        for i in range(8):
            ang = math.pi / 4 * i
            rr = r if i % 2 == 0 else r * 0.34
            pts.append((scx + rr * math.cos(ang), scy + rr * math.sin(ang)))
        d.polygon(pts, fill=color)

    spark(int(SS * 0.70), int(SS * 0.30), int(SS * 0.075), (255, 255, 255, 255))
    spark(int(SS * 0.80), int(SS * 0.20), int(SS * 0.035), (255, 255, 255, 230))

    # 轻微高光：顶部柔光
    gloss = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gloss)
    gd.ellipse([int(SS * 0.05), int(-SS * 0.35), int(SS * 0.95), int(SS * 0.35)],
               fill=(255, 255, 255, 38))
    gloss.putalpha(Image.composite(gloss.getchannel("A"), Image.new("L", (SS, SS), 0), mask))
    img = Image.alpha_composite(img, gloss)

    img = img.resize((S, S), Image.LANCZOS)
    png = os.path.join(ASSETS, "app_icon.png")
    img.save(png)
    print("PNG:", png)
    return png


def make_icns(png):
    iconset = os.path.join(ASSETS, "AppIcon.iconset")
    os.makedirs(iconset, exist_ok=True)
    base = Image.open(png).convert("RGBA")
    specs = [
        (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
    ]
    for size, name in specs:
        base.resize((size, size), Image.LANCZOS).save(os.path.join(iconset, name))
    icns = os.path.join(ASSETS, "AppIcon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True)
    print("ICNS:", icns)
    return icns


def make_ico(png):
    """生成 Windows 多尺寸 .ico（任务栏/资源管理器图标）。"""
    ico = os.path.join(ASSETS, "AppIcon.ico")
    base = Image.open(png).convert("RGBA")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base.save(ico, format="ICO", sizes=sizes)
    print("ICO:", ico)
    return ico


if __name__ == "__main__":
    p = make_icon()
    make_icns(p)
    make_ico(p)
