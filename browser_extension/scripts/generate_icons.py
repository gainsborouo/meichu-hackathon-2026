from pathlib import Path
import struct
import zlib


def png(width: int, height: int, pixels: bytes) -> bytes:
    def chunk(name: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data))

    rows = b''.join(b'\0' + pixels[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b'')


def inside_round_rect(x: float, y: float, left: float, top: float, right: float, bottom: float, radius: float) -> bool:
    cx = min(max(x, left + radius), right - radius)
    cy = min(max(y, top + radius), bottom - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def render(size: int) -> bytes:
    scale = 4
    large = size * scale
    samples = bytearray()
    for oy in range(size):
        for ox in range(size):
            total = [0, 0, 0, 0]
            for sy in range(scale):
                for sx in range(scale):
                    x = (ox * scale + sx + .5) * 128 / large
                    y = (oy * scale + sy + .5) * 128 / large
                    color = (0, 0, 0, 0)
                    if inside_round_rect(x, y, 0, 0, 128, 128, 28):
                        color = (27, 89, 70, 255)
                    if inside_round_rect(x, y, 26, 28, 102, 100, 10):
                        color = (255, 255, 255, 255)
                    if 26 <= x <= 102 and 45 <= y <= 61:
                        color = (217, 238, 229, 255)
                    if 39 <= x <= 63 and 74 <= y <= 82:
                        color = (27, 89, 70, 255)
                    if (x - 86) ** 2 + (y - 80) ** 2 <= 9 ** 2:
                        color = (239, 189, 76, 255)
                    for index, value in enumerate(color):
                        total[index] += value
            samples.extend(value // (scale * scale) for value in total)
    return png(size, size, bytes(samples))


public = Path(__file__).resolve().parents[1] / 'public'
for icon_size in (16, 32, 48, 128):
    (public / f'icon-{icon_size}.png').write_bytes(render(icon_size))
