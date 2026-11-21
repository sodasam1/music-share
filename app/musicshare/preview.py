"""The picture chat apps show when a link is pasted: the track's waveform, white on the site's blue. Drawn here with the standard library only
(a two-colour PNG, written by hand with zlib), so no image package is needed and a card is about 2 KB. No text is drawn: a font would be a dependency."""
import struct
import zlib

W, H = 1200, 630
BG, FG = (29, 95, 209), (255, 255, 255)
BARS, BAR_W, PITCH = 96, 8, 12          # 96 bars of 8 px every 12 px = 1140 px, centred


def _chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def waveform_png(peaks):
    """`peaks`: a list of numbers 0..255 (any length); returns the PNG bytes."""
    n = BARS
    if peaks:
        step = len(peaks) / n
        vals = [max(peaks[int(i * step):max(int((i + 1) * step), int(i * step) + 1)]) for i in range(n)]
    else:
        vals = [0] * n
    top = H * 0.34
    half = [int(10 + (min(255, max(0, v)) / 255.0) * (top - 10)) for v in vals]      # half the bar's height in pixels; never less than 10
    margin = (W - (n * PITCH - (PITCH - BAR_W))) // 2
    masks = []
    for i in range(n):
        x0 = margin + i * PITCH
        m = 0
        for x in range(x0, x0 + BAR_W):
            m |= 1 << (W - 1 - x)                                  # leftmost pixel is the highest bit
        masks.append(m)
    order = sorted(range(n), key=lambda i: -half[i])
    rows, mid = [], H // 2
    mask, k = 0, 0
    row_for = {}
    for dy in range(top.__int__() + 1, -1, -1):                    # from the outer rows inwards: bars join as the rows get closer to the middle
        while k < n and half[order[k]] >= dy:
            mask |= masks[order[k]]
            k += 1
        row_for[dy] = b"\x00" + mask.to_bytes(W // 8, "big")
    blank = b"\x00" + bytes(W // 8)
    for y in range(H):
        dy = abs(y - mid)
        rows.append(row_for.get(dy, blank))
    ihdr = struct.pack(">IIBBBBB", W, H, 1, 3, 0, 0, 0)            # 1 bit per pixel, palette
    plte = bytes(BG) + bytes(FG)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"PLTE", plte) + _chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + _chunk(b"IEND", b""))
