"""make_icon.py -- draws the "News Intelligence" app icon as a PNG of any size (I3),
standard library only: a newspaper page -- black masthead, ochre lead box, grey column
rules -- in the app's own colours (`app/app.css`): page #FFFEFA, ink #1A1A1A,
ochre #7A5A12, muted #878276. Drawn at each size directly, so no resizing tool is needed.

Usage: .venv/bin/python launch/make_icon.py SIZE OUT.png
       .venv/bin/python launch/make_icon.py icns OUT.icns   (every size, one file)
"""

import struct
import sys
import zlib

PAGE, INK, OCHRE, MUTED = ((0xFF, 0xFE, 0xFA), (0x1A, 0x1A, 0x1A),
                           (0x7A, 0x5A, 0x12), (0x87, 0x82, 0x76))

# On a 1024 grid; scaled to the size asked for. Painted in order over a rounded page.
RECTS = [(96, 96, 928, 928, INK), (112, 112, 912, 912, PAGE),      # page with a rule
         (176, 176, 848, 300, INK),                                 # masthead
         (176, 344, 496, 600, OCHRE)]                               # the lead story
RECTS += [(544, y, 848, y + 24, MUTED) for y in range(344, 600, 64)]   # its column
RECTS += [(176, y, 848, y + 24, MUTED) for y in range(648, 856, 64)]   # the rest
RADIUS = 160


def pixel(x, y):
    """Colour at a point of the 1024 grid, or None outside the rounded page."""
    cx = min(max(x, 96 + RADIUS), 928 - RADIUS)
    cy = min(max(y, 96 + RADIUS), 928 - RADIUS)
    if (x - cx) ** 2 + (y - cy) ** 2 > RADIUS * RADIUS:
        return None
    colour = None
    for x0, y0, x1, y1, c in RECTS:
        if x0 <= x < x1 and y0 <= y < y1:
            colour = c
    return colour


def png(size):
    scale = 1024 / size
    raw = bytearray()
    for y in range(size):
        raw.append(0)                                               # filter: none
        for x in range(size):
            c = pixel((x + 0.5) * scale, (y + 0.5) * scale)         # sample the centre
            raw += bytes((0, 0, 0, 0)) if c is None else bytes((*c, 255))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    head = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)      # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


# The icns entry types that hold a PNG, and the pixel size of each. Written here rather
# than with `iconutil`, which fails inside the build sandbox (even on Apple's own icons).
ICNS_TYPES = [(b"icp4", 16), (b"icp5", 32), (b"ic11", 32), (b"ic12", 64), (b"ic07", 128),
              (b"ic13", 256), (b"ic08", 256), (b"ic14", 512), (b"ic09", 512),
              (b"ic10", 1024)]


def icns():
    made = {}
    body = b""
    for kind, size in ICNS_TYPES:
        if size not in made:
            made[size] = png(size)
        data = made[size]
        body += kind + struct.pack(">I", len(data) + 8) + data
    return b"icns" + struct.pack(">I", len(body) + 8) + body


if __name__ == "__main__":
    if len(sys.argv) != 3 or not (sys.argv[1].isdigit() or sys.argv[1] == "icns"):
        sys.exit(__doc__)
    with open(sys.argv[2], "wb") as f:
        f.write(icns() if sys.argv[1] == "icns" else png(int(sys.argv[1])))
