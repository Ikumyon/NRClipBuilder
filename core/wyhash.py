import struct

SEED = 0x9C3805FC2C85CACC
WYP1 = 0xE7037ED1A0B428DB
WYP2 = 0x8EBC6AF09C88C6E3
WYP3 = 0x589965CC75374CC3
WYP4 = 0x1D8E4E27C47D124F

def wymum(a: int, b: int) -> tuple[int, int]:
    a &= 0xFFFFFFFFFFFFFFFF
    b &= 0xFFFFFFFFFFFFFFFF
    r = a * b
    return r & 0xFFFFFFFFFFFFFFFF, (r >> 64) & 0xFFFFFFFFFFFFFFFF

def read64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]

def read32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]

def wyhash_nrc1_checksum(data: bytes) -> int:
    """NIMBY Rails custom wyhash checksum."""
    length = len(data)
    r8 = SEED
    pos = 0
    remaining = length

    if length > 64:
        r11 = SEED
        remaining -= 64
        while True:
            d0 = read64(data, pos)
            d8 = read64(data, pos + 8)
            d16 = read64(data, pos + 16)
            d24 = read64(data, pos + 24)
            d32 = read64(data, pos + 32)
            d40 = read64(data, pos + 40)
            d48 = read64(data, pos + 48)
            d56 = read64(data, pos + 56)

            lo1, hi1 = wymum(r8 ^ d8, d0 ^ WYP1)
            lo2, hi2 = wymum(r8 ^ d24, d16 ^ WYP2)
            r8 = lo2 ^ hi2 ^ lo1 ^ hi1

            lo3, hi3 = wymum(r11 ^ d40, d32 ^ WYP3)
            lo4, hi4 = wymum(r11 ^ d56, d48 ^ WYP4)
            r11 = lo4 ^ hi4 ^ lo3 ^ hi3

            pos += 64
            if remaining <= 64:
                break
            remaining -= 64
        r8 ^= r11

    if remaining > 0x10:
        loop_count = ((remaining - 0x11) >> 4) + 1
        remaining -= loop_count * 16
        for _ in range(loop_count):
            a = read64(data, pos)
            b = read64(data, pos + 8)
            r8 ^= b
            lo, hi = wymum(r8, a ^ WYP1)
            r8 = lo ^ hi
            pos += 16

    if remaining > 8:
        rdx_val = read64(data, pos)
        rax_val = read64(data, pos + remaining - 8)
    elif remaining >= 4:
        rdx_val = read32(data, pos)
        rax_val = read32(data, pos + remaining - 4)
    elif remaining > 0:
        b0 = data[pos]
        b_mid = data[pos + (remaining >> 1)]
        b_last = data[pos + remaining - 1]
        rdx_val = (((b0 << 8) | b_mid) << 8) | b_last
        rax_val = 0
    else:
        rdx_val = 0
        rax_val = 0

    rax_val ^= r8
    rdx_val ^= WYP1
    lo, hi = wymum(rax_val, rdx_val)
    rax_val = lo ^ hi

    lo, hi = wymum(rax_val, length ^ WYP1)
    return lo ^ hi
