#!/usr/bin/env python3
"""Reader for the Watchtower Library 2015 publication format (URES container, .PUB files).

Bit streams are read LSB-first. Formats involved:

Codec header  4-bit width code, then two fields of that width:
              compressed bit count, uncompressed byte count.
NIBBLE        LZ codec with a 27-entry move-to-front list; its output is stored as
              4 interleaved nibble planes.
MTEC3         prefix code whose symbols are runs of 16-bit MEPS character codes; its
              dictionary is stored NIBBLE-compressed behind the magic 0x07d0.
BTEC1         bit-level variable-length integers (document headers).
"""
import struct

# NIBBLE opcode prefixes, (bit length, LSB-first pattern), indexed by opcode 0..38.
_PREFIXES = [
    (4, 0b0010), (6, 0b010110), (6, 0b010111), (6, 0b011010), (6, 0b011011), (6, 0b011101),
    (7, 0b0110111), (7, 0b0111010), (7, 0b0111011), (7, 0b0111101), (7, 0b0111110), (5, 0b01011),
    (5, 0b00110), (2, 0b00), (3, 0b001), (4, 0b0011), (5, 0b00111), (5, 0b01010), (7, 0b0111111),
    (7, 0b1110111), (5, 0b01111), (9, 0b011111110), (10, 0b0111111110), (6, 0b011110),
    (6, 0b011111), (6, 0b110110), (8, 0b01111101), (5, 0b01101), (8, 0b01111110),
    (8, 0b01111111), (9, 0b011111111), (7, 0b1111010), (10, 0b0111111111), (10, 0b1111111110),
    (10, 0b1111111111), (4, 0b0101), (5, 0b01110), (7, 0b1111011), (8, 0b11111101),
]
# 10-bit lookup: low bits of the stream -> opcode
DISPATCH = bytes(next(op for op, (k, pat) in enumerate(_PREFIXES) if i & ((1 << k) - 1) == pat)
                 for i in range(1024))


def u32(buf, p):
    b = buf[p:p + 4]
    return int.from_bytes(b + bytes(4 - len(b)), 'little')


def codec_header(buf, pos):
    """-> (header_bits, compressed_bits, uncompressed_bytes); pos is a byte offset."""
    w = 24 if buf[pos] & 0xf == 0xf else (buf[pos] & 0xf) + 7
    mask = (1 << w) - 1
    a = (u32(buf, pos) >> 4) & mask
    bit = 4 + w
    b = (u32(buf, pos + (bit >> 3)) >> (bit & 7)) & mask
    return 4 + 2 * w, a, b


# --- NIBBLE -----------------------------------------------------------------
def _vlen1(v):
    s, v = v & 3, v >> 2
    return [(6, (v & 0xf) + 1), (10, (v & 0xff) + 0x11), (12, (v & 0x3ff) + 0x111), (15, (v & 0x1fff) + 0x511)][s]


def _vlen2(v):
    s, v = v & 3, v >> 2
    return [(6, (v & 0xf) + 1), (10, (v & 0xff) + 0x11), (14, (v & 0xfff) + 0x111), (16, (v & 0x3fff) + 0x1111)][s]


def nibble_decode(src, bitpos, nbits, outlen):
    out = bytearray()
    mtf = list(range(27))
    present = [False] * 256
    for i in range(27):
        present[i] = True

    def touch(bs):
        for b in bs:
            if not present[b]:
                present[b] = True
                present[mtf[26]] = False
                mtf.pop()
            else:
                mtf.remove(b)
            mtf.insert(0, b)

    def emit(k):
        v = mtf[k]
        out.append(v)
        if k:
            del mtf[k]
            mtf.insert(0, v)

    def copy(n, d):                       # LZ match; source starts n+d bytes back
        s = len(out) - n - d
        for j in range(n):
            out.append(out[s + j])
        touch(out[-n:])

    def rle(n):                           # repeat the last byte
        if not out:
            out.append(mtf[0]); n -= 1
        out.extend(bytes([out[-1]]) * n)

    def period(p, r):                     # repeat the last p bytes r times
        s = len(out) - p
        for j in range(p * r):
            out.append(out[s + j])

    end = (bitpos + nbits + 7) >> 3
    bp = bitpos
    while len(out) < outlen and (bp >> 3) < end:
        v = u32(src, bp >> 3) >> (bp & 7)
        c = DISPATCH[v & 0x3ff]
        if c == 0:
            emit(0); bp += 4
        elif c <= 5:
            emit(c); bp += 6
        elif c <= 10:
            emit(c); bp += 7
        elif c == 0xb:
            emit(((v >> 5) & 0xf) + 0xb); bp += 9
        elif c == 0xc:
            copy(2, ((v >> 5) & 0xf) + 1); bp += 9
        elif c == 0xd:
            n, d = _vlen1(v >> 2); copy(3, d); bp += n + 2
        elif 0xe <= c <= 0x10:
            k = c - 0xb                   # 3..5 prefix bits, copy 4..6
            n, d = _vlen2(v >> k); copy(c - 0xa, d); bp += n + k
        elif c == 0x11:
            n, d = _vlen2(v >> 7); copy(((v >> 5) & 3) + 7, d); bp += n + 7
        elif c in (0x12, 0x13):
            n, d = _vlen2(v >> 9); copy(((v >> 7) & 3) + (0xb if c == 0x12 else 0xf), d); bp += n + 9
        elif c in (0x14, 0x15, 0x16):
            bp += {0x14: 5, 0x15: 9, 0x16: 10}[c]
            v2 = u32(src, bp >> 3) >> (bp & 7)
            n, d = _vlen2(v2 >> 6)
            copy((v2 & 0x3f) + {0x14: 0x13, 0x15: 0x53, 0x16: 0x93}[c], d)
            bp += n + 6
        elif c == 0x17:
            rle(((v >> 6) & 1) + 1); bp += 7
        elif c == 0x18:
            rle(((v >> 6) & 3) + 3); bp += 8
        elif c == 0x19:
            rle(((v >> 6) & 0x1f) + 7); bp += 11
        elif c == 0x1a:
            rle(((v >> 8) & 0x1ff) + 0x27); bp += 17
        elif c == 0x1b:
            period(((v >> 5) & 3) + 2, ((v >> 7) & 1) + 1); bp += 8
        elif c == 0x1c:
            period(((v >> 8) & 3) + 2, ((v >> 10) & 3) + 3); bp += 12
        elif c == 0x1d:
            period(((v >> 8) & 3) + 2, ((v >> 10) & 0x1f) + 7); bp += 15
        elif c == 0x1e:
            period(((v >> 9) & 3) + 2, ((v >> 11) & 0x1ff) + 0x27); bp += 20
        elif c == 0x1f:
            period(((v >> 7) & 0xff) + 6, ((v >> 15) & 1) + 1); bp += 16
        elif c == 0x20:
            period(((v >> 10) & 0xff) + 6, ((v >> 18) & 3) + 3); bp += 20
        elif c == 0x21:
            period(((v >> 10) & 0xff) + 6, ((v >> 18) & 0x1f) + 7); bp += 23
        elif c == 0x22:
            bp += 10
            v2 = u32(src, bp >> 3) >> (bp & 7)
            period((v2 & 0xff) + 6, ((v2 >> 8) & 0x1ff) + 0x27); bp += 17
        elif c in (0x23, 0x24, 0x25, 0x26):
            if c == 0x23:
                skip, n = 4, 1
            elif c == 0x24:
                skip, n = 9, ((v >> 5) & 0xf) + 3
            elif c == 0x25:
                skip, n = 13, ((v >> 7) & 0x3f) + 0x13
            else:
                skip, n = 19, ((v >> 8) & 0x7ff) + 0x53
            bp += skip
            lit = bytes((u32(src, (bp >> 3) + j) >> (bp & 7)) & 0xff for j in range(n))
            out.extend(lit); touch(lit)
            bp += n * 8
        else:
            raise ValueError('bad NIBBLE opcode %d' % c)
    if len(out) != outlen:
        raise ValueError('NIBBLE length %d != %d' % (len(out), outlen))
    return deinterleave(out)


def deinterleave(src):
    """Undo the 4-plane nibble layout of NIBBLE output."""
    n = len(src); q = n >> 2
    dst = bytearray(n)
    dst[q * 4:] = src[q * 4:]
    for i in range(q):
        c1, B, b3, c2 = src[i], src[q + i], src[2 * q + i], src[3 * q + i]
        dst[4 * i + 0] = (c2 & 0xf0) | (b3 >> 4)
        dst[4 * i + 1] = (c1 & 0xf0) | (B >> 4)
        dst[4 * i + 2] = ((c2 << 4) | (b3 & 0xf)) & 0xff
        dst[4 * i + 3] = ((B & 0xf) | (c1 << 4)) & 0xff
    return bytes(dst)


def nibble_stream(buf, pos):
    """NIBBLE context at pos: 0x07cf magic, then codec header + data at pos+2."""
    assert struct.unpack_from('<H', buf, pos)[0] == 0x07cf, 'not a NIBBLE context'
    hb, cbits, ulen = codec_header(buf, pos + 2)
    return nibble_decode(buf, (pos + 2) * 8 + hb, cbits, ulen), pos + 2 + ((hb + cbits + 7) >> 3)


# --- MTEC3 -------------------------------------------------------------------
class MTEC3:
    def __init__(self, ctx):
        """ctx: decompressed dictionary bytes: per-group bit increments (0-terminated),
        u16 code counts per group, u16 run-length histograms, then the MEPS words."""
        n = ctx.index(0)
        incs = list(ctx[:n])
        off = (n + 2) & ~1
        counts = struct.unpack_from('<%dH' % n, ctx, off)
        p = off + 2 * n
        # canonical code generation: each group extends the still unused codes by
        # incs[g] high bits and takes the first counts[g] of them, in order
        codes, bits, consumed = [], 0, 0
        self.lens = {}          # (length, code) -> symbol
        sym = 0
        for g in range(n):
            rem = codes[consumed:]
            if bits == 0:
                codes = list(range(1 << incs[g]))
            else:
                codes = [c | (k << bits) for k in range(1 << incs[g]) for c in rem]
            bits += incs[g]; consumed = 0
            for c in codes[:counts[g]]:
                self.lens[(bits, c)] = sym; sym += 1
            consumed = counts[g]
        # per-group run-length histograms, then the MEPS words
        total = 0; runs = []
        for g in range(n):
            target = total + counts[g]
            while total < target:
                k = struct.unpack_from('<H', ctx, p)[0]; p += 2
                runs.append(k); total += k
            runs.append(None)   # group separator
        words = struct.unpack_from('<%dH' % ((len(ctx) - p) // 2), ctx, p)
        self.syms = []; w = 0; ln = 0
        for k in runs:
            if k is None:
                ln = 0; continue
            ln += 1
            for _ in range(k):
                self.syms.append(words[w:w + ln]); w += ln
        if len(self.syms) != sym or w != len(words):
            raise ValueError('MTEC3 context inconsistent: %d/%d syms, %d/%d words' % (len(self.syms), sym, w, len(words)))
        self.maxlen = bits
        self.table = {}
        for (L, c), s in self.lens.items():
            self.table.setdefault(L, {})[c] = s

    def decode(self, buf, bitpos, nbits):
        out = []; bp = bitpos; end = bitpos + nbits
        lens = sorted(self.table)
        while bp < end:
            v = u32(buf, bp >> 3) >> (bp & 7)
            if self.maxlen > 25:
                v |= u32(buf, (bp >> 3) + 4) << (32 - (bp & 7))
            for L in lens:
                s = self.table[L].get(v & ((1 << L) - 1))
                if s is not None:
                    out.extend(self.syms[s]); bp += L; break
            else:
                raise ValueError('MTEC3: no code at bit %d' % bp)
        return out


def mtec3_context(buf, pos):
    assert struct.unpack_from('<H', buf, pos)[0] == 0x07d0, 'not an MTEC3 context'
    ctx, end = nibble_stream(buf, pos + 2)
    return MTEC3(ctx), end


# --- BTEC1: bit-level variable-length integers ---------------------------------
def btec1(buf, bp):
    """-> (value, new_bitpos)"""
    v = u32(buf, bp >> 3) >> (bp & 7)
    if not v & 1:
        return 0, bp + 1
    if not v & 2:
        return ((v >> 2) & 0xf) + 1, bp + 6
    k = (v >> 2) & 7
    if k in (0, 4):
        return ((v >> 4) & 0xff) + 0x11, bp + 12
    if k in (1, 5):
        return ((v >> 4) & 0xfff) + 0x111, bp + 16
    if k == 2:
        return ((v >> 5) & 0xffff) + 0x1111, bp + 21
    if k == 3:
        w = u32(buf, (bp + 5) >> 3) >> ((bp + 5) & 7)
        return (w & 0xfffff) + 0x11111, bp + 25
    if k == 6:
        w = u32(buf, (bp + 17) >> 3) >> ((bp + 17) & 7)
        return (((w & 0xfff) << 12) | ((v >> 5) & 0xfff)) + 0x111111, bp + 29
    w = u32(buf, (bp + 21) >> 3) >> ((bp + 21) & 7)
    return ((w << 16) | ((v >> 5) & 0xffff)) & 0xffffffff, bp + 37


# --- URES container (.PUB) -----------------------------------------------------
def ures_sections(d):
    """Section table after the 'URES' header: u32 doc count at 0x12, then (id, size, offset)."""
    assert d[:4] == b'URES', 'not a URES file'
    ndocs = struct.unpack_from('<I', d, 0x12)[0]
    nsec = struct.unpack_from('<H', d, 0x17)[0]
    secs = {}
    sid = d[0x16]
    p = 0x19
    for _ in range(nsec):
        size, off = struct.unpack_from('<II', d, p)
        secs[sid] = (off, size)
        sid = d[p + 8]
        p += 9
    return ndocs, secs


def pub_documents(d):
    """Yield (name, doc_class, meps_codes) for every document of a 2015 .PUB file (bytes)."""
    if isinstance(d, str):
        d = open(d, 'rb').read()
    ndocs, secs = ures_sections(d)
    ctx_at = d.find(b'\xd0\x07\xcf\x07', secs[8][0])
    m, _ = mtec3_context(d, ctx_at)
    base = secs[4][0]
    offs = struct.unpack_from('<%dI' % (ndocs + 1), d, base + 17)
    data = base + 17 + 4 * (ndocs + 1)
    for i in range(ndocs):
        b = d[data + offs[i]: data + offs[i + 1]]
        bp = 0; hdr = []
        for _ in range(3):
            v, bp = btec1(b, bp); hdr.append(v)
        nareas, dclass, namelen = hdr
        areas = []
        for _ in range(nareas):
            t, bp = btec1(b, bp); n, bp = btec1(b, bp); areas.append((t, n))
        p = (bp + 7) >> 3
        name = b[p:p + namelen].decode('latin1'); p += namelen
        codes = None
        for t, n in areas:
            if t == 0:                    # text area: codec header + MTEC3 stream
                hb, cb, ul = codec_header(b, p)
                codes = m.decode(b, p * 8 + hb, cb)
                if len(codes) * 2 != ul:
                    raise ValueError('%s: MTEC3 length %d != %d' % (name, len(codes) * 2, ul))
            p += n
        yield name, dclass, codes


def pub_info(d, secs):
    """Publication record (section 3): 16-byte hash, u16 size, then TLV records:
    tag < 0x40: u8 value; tag < 0x80: u16 value; else u16 byte length + MEPS string."""
    o = secs[3][0]
    end = o + struct.unpack_from('<H', d, o + 16)[0]
    p = o + 18
    info = {}
    while p < end:
        tag = d[p]
        if tag in info:           # later records use tags whose value sizes are unknown; stop there
            break
        if tag < 0x40:
            info[tag] = d[p + 1]; p += 2
        elif tag < 0x80:
            info[tag] = struct.unpack_from('<H', d, p + 1)[0]; p += 3
        else:
            n = struct.unpack_from('<H', d, p + 1)[0]
            info[tag] = struct.unpack_from('<%dH' % (n // 2), d, p + 3); p += 3 + n
    return info
