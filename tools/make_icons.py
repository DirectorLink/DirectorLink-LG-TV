"""Generate the driver icons in the DirectorLink style (no maker logos).

    pip install pillow
    python tools/make_icons.py

Writes (paths in driver.xml are relative to src/www/):
  src/www/icons/device_sm.png, device_lg.png     the driver in Composer (16 / 32 px)
  src/www/icons/tv_300.png                       documentation header

Style (matches the other DirectorLink drivers): round grey disc with a vertical
gradient, thick cyan ring, white glyph. The glyph is a TV on its stand with a
pop-up card in the corner of the screen.
"""
import os

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICONS = os.path.join(ROOT, "src", "www", "icons")

CYAN = (40, 210, 255, 255)
BLUE = (66, 119, 255, 255)
DISC_TOP = (157, 157, 157)
DISC_BOTTOM = (99, 99, 99)
WHITE = (250, 250, 250, 255)
DARK = (60, 60, 60, 255)

SS = 2048          # supersampled canvas
S = SS / 1024.0    # scale from the 1024 design grid


def p(*v):
    return [int(round(x * S)) for x in v]


def disc(ring):
    """Grey gradient disc with a ring and a soft drop shadow."""
    img = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse(p(52, 60, 972, 980), fill=(0, 0, 0, 90))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14 * S)))
    ImageDraw.Draw(img).ellipse(p(44, 44, 980, 980), fill=ring)
    grad = Image.new("RGBA", (SS, SS))
    gd = ImageDraw.Draw(grad)
    top, bottom = p(124)[0], p(900)[0]
    for y in range(SS):
        t = min(1.0, max(0.0, (y - top) / float(bottom - top)))
        c = tuple(int(DISC_TOP[i] + (DISC_BOTTOM[i] - DISC_TOP[i]) * t) for i in range(3))
        gd.line([(0, y), (SS, y)], fill=c + (255,))
    mask = Image.new("L", (SS, SS), 0)
    ImageDraw.Draw(mask).ellipse(p(124, 124, 900, 900), fill=255)
    img.paste(grad, (0, 0), mask)
    return img


def tv(img, frame, screen, accent):
    """The TV: frame, screen, stand, and a pop-up card with two lines of text in the screen's corner."""
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(p(232, 300, 792, 652), radius=p(34)[0], fill=frame)        # frame
    d.rounded_rectangle(p(262, 330, 762, 622), radius=p(16)[0], fill=screen)       # screen
    d.polygon([tuple(p(470, 652)), tuple(p(554, 652)), tuple(p(590, 724)), tuple(p(434, 724))], fill=frame)  # stand
    d.rounded_rectangle(p(392, 716, 632, 748), radius=p(14)[0], fill=frame)        # foot
    d.rounded_rectangle(p(486, 374, 734, 488), radius=p(22)[0], fill=accent)       # the pop-up
    d.rounded_rectangle(p(514, 402, 704, 424), radius=p(11)[0], fill=frame)        # its title
    d.rounded_rectangle(p(514, 440, 660, 460), radius=p(10)[0], fill=frame)        # its message


def header():
    img = disc(CYAN)
    tv(img, WHITE, DARK, CYAN)
    return img


def composer_icon(size):
    if size >= 32:
        img = disc(BLUE)
        tv(img, WHITE, DARK, BLUE)
        return img.resize((size, size), Image.LANCZOS)
    img = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse(p(16, 16, 1008, 1008), fill=BLUE)
    d.ellipse(p(176, 176, 848, 848), fill=(118, 118, 118, 255))
    d.rounded_rectangle(p(264, 336, 760, 640), radius=p(30)[0], fill=WHITE)
    d.rounded_rectangle(p(424, 640, 600, 720), radius=p(20)[0], fill=WHITE)
    d.rounded_rectangle(p(470, 380, 720, 500), radius=p(24)[0], fill=BLUE)
    return img.resize((size, size), Image.LANCZOS)


def save(img, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, optimize=True)


def main():
    save(composer_icon(16), os.path.join(ICONS, "device_sm.png"))
    save(composer_icon(32), os.path.join(ICONS, "device_lg.png"))
    save(header().resize((300, 300), Image.LANCZOS), os.path.join(ICONS, "tv_300.png"))
    print("icons written to", ICONS)


if __name__ == "__main__":
    main()
