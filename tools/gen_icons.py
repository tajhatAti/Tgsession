"""Generate simple PWA icons (blue square + white paper plane) as PNG, print base64."""
import struct, zlib, base64, sys

def make_png(size):
    ss = 4  # supersample
    W = size * ss
    # Material "send" icon polygon in a 24x24 grid, centered with margin
    poly = [(2,21),(23,12),(2,3),(2,10),(17,12),(2,16)]
    m = 4.0  # margin in grid units
    scale = (24 - 2*m) / 24.0
    pts = []
    for (x, y) in poly:
        gx = (x*scale + m) / 24.0
        gy = (y*scale + m) / 24.0
        pts.append((gx, gy))
    def inside(px, py):
        # even-odd rule point-in-polygon
        n, c = len(pts), False
        j = n - 1
        for i in range(n):
            xi, yi = pts[i]; xj, yj = pts[j]
            if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
                c = not c
            j = i
        return c
    BG = (51, 144, 236, 255)   # telegram-ish blue
    FG = (255, 255, 255, 255)
    rows = []
    for y in range(size):
        row = bytearray()
        row.append(0)  # filter none
        for x in range(size):
            # supersample 4x4
            r = g = b = a = 0
            for sy in range(ss):
                for sx in range(ss):
                    px = (x*ss + sx + 0.5) / W
                    py = (y*ss + sy + 0.5) / W
                    c = FG if inside(px, py) else BG
                    r += c[0]; g += c[1]; b += c[2]; a += c[3]
            n = ss*ss
            row += bytes((r//n, g//n, b//n, a//n))
        rows.append(bytes(row))
    raw = b"".join(rows)
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    return png

for s in (192, 512):
    b64 = base64.b64encode(make_png(s)).decode()
    print(f"ICON_{s} = {b64}")
    print(f"# len {len(b64)}")
