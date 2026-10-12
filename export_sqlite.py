#!/usr/bin/env python3
"""Export Watchtower Library 1995 (WTLIB, Win16) publications to SQLite.

File format:
DOCSFILE stream = file offset 0x200, positions in 16-bit words.
  header[6]          number of documents
  header[0xa:0xc]    document table (12 words/entry: pascal name, area table, ..., n_areas)
  header[0xe:0x10]   minicode table, header[0x10] its length in words
  area (5 words)     type, pos(dword), len(dword); type 0x31 = text
Text is minicode-compressed MEPS (16-bit char codes + 0xFAxx..0xFExx markup): each byte expands
to a run of 1..L codes from the table, 0xFF hi 0xFF lo is a literal code.

usage: export_sqlite.py <WT_DATA_.E dir> <out.sqlite>
"""
import os, sqlite3, struct, sys, unicodedata
from collections import Counter

B = 0x200

# --- MEPS -> Unicode -------------------------------------------------------
# 0x0800..0x0850: Latin letters, digits and basic punctuation
BASE = ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz1234567890"
        "$¢&:;.,‘’?!/*()/-—–")
CH = {0x0800 + i: c for i, c in enumerate(BASE)}
# symbols, ligatures and Latin-1 letters (typographic quotes kept as such)
CH.update({
    0x00a9: '¬', 0x010c: '†', 0x010d: '‡', 0x0180: '#', 0x0181: '+', 0x0183: '×',
    0x0184: '÷', 0x0185: '=', 0x0187: '±', 0x0189: '<', 0x018a: '>', 0x0203: '@',
    0x0204: '©', 0x0205: '®', 0x0206: '™', 0x0207: '¶', 0x0208: '.', 0x0209: '..',
    0x020a: '…', 0x020b: '......', 0x020c: '_', 0x020d: '__', 0x020e: '____',
    0x020f: '______', 0x0217: '|', 0x0218: '\\', 0x021a: '{', 0x021b: '}',
    0x021d: '~', 0x0284: '¤', 0x0285: '•', 0x0723: 'µ', 0x0851: 'ff', 0x0852: 'fi',
    0x0853: 'fl', 0x0854: 'ffi', 0x0855: 'ffl', 0x0856: 'Æ', 0x0857: 'æ',
    0x0858: 'Œ', 0x0859: 'œ', 0x085a: 'ß', 0x0871: '“', 0x0872: '”', 0x0873: '"',
    0x0874: '<', 0x0875: '>', 0x0876: '«', 0x0877: '»', 0x0878: '¿', 0x0879: '¡',
    0x087a: '°', 0x087b: "'", 0x087c: ',', 0x087d: '§', 0x087e: '[', 0x087f: ']',
    0x0880: '£', 0x0881: 'ƒ', 0x0884: '¥', 0x08a0: 'Ð', 0x08a1: 'ð', 0x08a2: 'Ø',
    0x08a3: 'ø', 0x08a4: 'Þ', 0x08a5: 'þ', 0x08fb: '·', 0x08fc: '%', 0x08fd: '‰',
    0x085d: 'ı', 0x085e: 'ȷ',
    # transliteration marks seen in context (a·na′sta·sis, ʽam-ha·ʼa′rets)
    0x0200: '′', 0x0215: 'ʽ', 0x0216: 'ʼ',
})
# unverified: inferred from the ALPHABET sort groups (letter + macron pairs)
CH.update({0x08a6: 'Ā', 0x08a7: 'ā', 0x08a8: 'Ē', 0x08a9: 'ē', 0x08aa: 'Ī', 0x08ab: 'ī',
           0x08ac: 'Ū', 0x08ad: 'ū', 0x08b7: 'Ō', 0x08b8: 'ō', 0x08b5: 'Ḥ', 0x08b6: 'ḥ',
           0x08c9: 'Ḇ', 0x08ca: 'ḇ', 0x08cb: 'Ḏ', 0x08cc: 'ḏ',
           0x08cd: 'Ḵ', 0x08ce: 'ḵ'})
# Greek: 0x0700.. upper, 0x0718.. lower, final sigma last (checked against ψυχῶν, Κυρίου)
for i, c in enumerate('ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ'):
    CH[0x0700 + i] = c
for i, c in enumerate('αβγδεζηθικλμνξοπρστυφχψως'):
    CH[0x0718 + i] = c
# Hebrew 0x0600..: letters with finals interleaved (stored in visual order, see hebrew_logical)
for i, c in list(enumerate('אבגדהוזחטיכךלמםנןסעפףצץקר')) + [(0x19, 'שׂ'), (0x1a, 'שׁ'), (0x1b, 'ת')]:
    CH[0x0600 + i] = c
# unverified: sin/shin order
# points (follow their letter once the run is reversed); checked against transliterated examples
CH.update({0x0620: '\u05b1', 0x0621: '\u05b8', 0x0622: '\u05b2', 0x0623: '\u05b7', 0x0624: '\u05b0',
           0x0625: '\u05b5', 0x0626: '\u05b3', 0x0627: '\u05b6', 0x0628: '\u05bb', 0x0629: '\u05b4',
           0x062a: '\u05b9', 0x061e: '\u05c1', 0x061f: '\u05c2', 0x064e: '\u05be'})
# 0x0651 + letter index = letter with dagesh/mappiq (בּ גּ דּ כּ פּ תּ, וּ, הּ, זּ = zz)
for c in range(0x0600, 0x061c):
    CH[0x0651 + c - 0x0600] = CH[c] + '\u05bc'
# Greek spacing marks used in grammar explanations
CH.update({0x0789: '´', 0x078a: '`', 0x078b: '¨', 0x078c: '῀', 0x078e: '᾿', 0x078f: '῾'})
# footnote marks, Greek/Hebrew punctuation and signs, manuscript abbreviations
CH.update({0x0100: '*', 0x0101: '#', 0x0102: '^', 0x0103: '‡', 0x0104: '*', 0x0105: '+', 0x0106: '^',
           0x0731: 'Ϛ', 0x0751: '·', 0x0743: '.', 0x0744: ',', 0x0745: '’', 0x0746: '’', 0x074a: '*',
           0x0750: '-', 0x077a: '°', 0x077b: 'ʽ',
           0x061c: 'ℵ', 0x0640: '׃', 0x0679: '׀', 0x0641: ':', 0x0645: '’', 0x0646: '’',
           0x0635: '2', 0x0636: '3',
           0x0631: '\u05b6', 0x062c: '\u05b8', 0x0678: '\u05bd',
           0x0306: 'ΙΑΩ', 0x0313: 'Κ\u0305Σ\u0305'})
# identified from context: De Tijd, Građanski, Onyankopɔn, Jɛhowaa, ʽam ha-ʼarets,
# ₱ (Philippines 1954), ₦ (Nigeria 1978), ₵ (Ghana 1986)
CH.update({0x08b9: 'Ư', 0x08ba: 'ư', 0x085b: 'ĳ', 0x08b3: 'Đ', 0x08b4: 'đ', 0x08c0: 'Ɔ', 0x08c1: 'ɔ', 0x08bc: 'Ɛ', 0x08bd: 'ɛ',
           0x0210: 'ʽ', 0x0882: '₱', 0x0883: '₦', 0x0885: '₵'})
CH.update({0x0201: '″', 0x0182: '−', 0x08ae: 'Ł', 0x08af: 'ł'})
# 0x0280..0x02ff are dingbats (section ornaments, check boxes); simplified to a bullet
for c in range(0x0280, 0x0300):
    CH.setdefault(c, '•')
# diacritics PRECEDE their base letter in MEPS (caf⟦0865⟧e, S⟦0869⟧ao, ⟦0861⟧T⟦086a⟧ariq)
COMB = {0x085f: '\u0327', 0x0861: '\u0323', 0x0863: '\u0326', 0x0865: '\u0301',
        0x0866: '\u0300', 0x0867: '\u0308', 0x0868: '\u0302', 0x0869: '\u0303',
        0x086a: '\u0304', 0x086b: '\u0306', 0x086c: '\u030c', 0x086e: '\u030a',
        0x086f: '\u030b', 0x0767: '\u0313\u0300', 0x0769: '\u0313\u0342', 0x076a: '\u0314\u0301',
        0x076d: '\u0314\u0342', 0x0797: '\u0313\u0342', 0x0798: '\u0313\u0301', 0x086d: '\u0307',  # dot above: Żur
        0x0864: '\u032d',  # circumflex below: Venda Ṱhanzi
        0x076e: '\u0301',  # modern Greek tonos: Μάρτυρες, Ιεχωβά
        # Greek (ψυχ⟦075f⟧ων, Κυρ⟦075b⟧ιου, ⟦0760⟧εναντ⟦075b⟧ιον, ⟦0761⟧η)
        0x075a: '\u0345', 0x075b: '\u0301', 0x075c: '\u0300', 0x075f: '\u0342',
        0x0760: '\u0313', 0x0761: '\u0314', 0x0766: '\u0313\u0301', 0x075e: '\u0342',
        0x075d: '\u0308'}  # diaeresis (only on ι/υ)
SPACING = {0x0865: '´', 0x0866: '`', 0x0867: '¨', 0x0868: '^', 0x0869: '˜', 0x086a: '¯', 0x085f: '¸'}
NOBASE = 0x0214


def is_hebrew(c):
    return 0x0600 <= c <= 0x06ff


def hebrew_logical(codes):
    """Hebrew runs (letters, points, spaces/no-base between them) are stored visually: reverse them."""
    out = []; i = 0; n = len(codes)
    while i < n:
        if is_hebrew(codes[i]):
            j = i
            while j < n and (is_hebrew(codes[j]) or codes[j] in (0xfb61, NOBASE)):
                j += 1
            while codes[j - 1] == 0xfb61: j -= 1
            out.extend(reversed(codes[i:j])); i = j
        else:
            out.append(codes[i]); i += 1
    return out


# A multilingual chart in one book stores non-Latin scripts as font glyph codes (visual order,
# conjunct glyphs), so each entry is matched as a whole code sequence.
PHRASES = {
    # Tetragrammaton facsimiles, drawn as two-glyph images
    (0x0300, 0x0301): 'יהוה', (0x0302, 0x0303): '𐤉𐤄𐤅𐤄', (0x0304, 0x0305): '𐤉𐤄𐤅𐤄',
    (0x0307, 0x0308): '𐤉𐤉', (0x0309, 0x030a): '𐤉𐤄𐤅𐤄', (0x030b, 0x030c): '𐤉𐤄𐤅𐤄',
    (0x030d, 0x030e): '𐤉𐤄𐤅𐤄', (0x030f, 0x0310): '𐤉𐤄𐤅𐤄', (0x0311, 0x0312): '𐤉𐤄𐤅𐤄',
    (0xbb11, 0xbb18, 0xbb13, 0xbb1e, 0xfb61, 0xba9a, 0xbb18, 0xbb13, 0xbaa9): 'شهود يهوه',
    (0x0504, 0x0554, 0x055c, 0x0562, 0x0526, 0x0559, 0x0530, 0xfb61, 0x051d, 0x0553, 0x0526, 0x055a, 0x052a, 0x0564): 'Եհովայի Վկաներ',
    (0xcd07, 0xcd3a, 0xcd2f, 0xccad, 0xd379, 0xd3cd): 'エホバの証人',
    (0x1c7a, 0x1e3c, 0x1c93, 0x1cc1, 0xfb61, 0x1d0f, 0x1cc4): '여호와의 증인',
    (0xc28f, 0xc25d, 0xc291, 0xc268, 0xc28e, 0xc257, 0xc28e, 0xc25f, 0xfb61, 0xc266, 0xc28e, 0xc2f7, 0xc290, 0xc291, 0xc230, 0xc25f): 'যিহোবার সাক্ষীদের',
    (0x2cfb, 0x2b48, 0x3081, 0x2af5, 0x35a9, 0x289b): '耶和華見證人',
    (0x0e10, 0x0e2f, 0x0e24, 0x0e52, 0x0e2e, 0x0e12, 0x0e2e, 0x0e2f, 0x0e24, 0x0e0b, 0x0e2e, 0x0e2f, 0xfb61, 0x0e16, 0x0e2e, 0x0e2f, 0x0e8d, 0x0e2e, 0x0e32, 0x0e27, 0x0e2e, 0x0e52, 0x0e2f): 'યહોવાહના સાક્ષીઓ',
    (0x0c92, 0x0cdc, 0x0caa, 0x0ceb, 0x0cdc, 0x0c95, 0x0cdc, 0x0cdc, 0xfb61, 0x0ceb, 0x0c9e, 0xfb61, 0x0c82, 0x0cdc, 0x0c95, 0x0cdc, 0x0cdc, 0x0caa): 'यहोवा के साक्षी',
    (0x1010, 0x10a9, 0x10ab, 0x1014, 0x10a3, 0x10ab, 0x1015, 0x1013, 0x109c, 0x10aa, 0x1070, 0x10aa, 0xfb61, 0x10a1, 0x1012, 0x10e2, 0x101a, 0x1020, 0x10aa, 0x10a5, 0x10aa, 0x1014): 'ಯೆಹೋವನ ಸಾಕ್ಷಿಗಳು',
    (0x1119, 0x1156, 0x1120, 0x11ce, 0x1132, 0x111c, 0x1119, 0x1152, 0x1155, 0x110a, 0xfb61, 0x111f, 0x11cd, 0x1132, 0x1167, 0x11d7, 0x1133, 0x1100, 0x1125): 'യഹോവയുടെ സാക്ഷികൾ',
    (0x0c92, 0x0cdc, 0x0caa, 0x0cea, 0x0cdc, 0x0c95, 0x0cdc, 0x0cdc, 0x0c84, 0x0ceb, 0x0cdc, 0xfb61, 0x0c99, 0x0cdc, 0x0cdc, 0x0c9c, 0x0cdc, 0x0cdf, 0x0ca5, 0x0cdc, 0x0ca8): 'यहोवाचे साक्षीदार',
    (0xc62b, 0xc619, 0xc62b, 0xc61e, 0xc623, 0xc61c, 0xc624, 0xc61d, 0xc62f, 0xc600, 0xc62b, 0xc61d, 0xc618, 0xc660, 0xc623, 0xc631): 'ယေဟောဝါသက်သေမျာ',
    (0x0c92, 0x0cdc, 0x0caa, 0x0ceb, 0x0cdc, 0x0c95, 0x0cdc, 0x0cdc, 0x0c9e, 0x0cdc, 0xfb61, 0x0c99, 0x0cdc, 0x0cdc, 0x0c9c, 0x0cdc, 0x0cdf, 0x0caa, 0x0d4d): 'यहोवाका साक्षीहरू',
    (0x0918, 0x092b, 0x0952, 0x092e, 0x0930, 0x0961, 0x0930, 0x0956, 0x0952, 0xfb61, 0x090c, 0x0930, 0x092c, 0x095b, 0x092b, 0x096c): 'Свидетели Иеговы',
    (0x149e, 0x1468, 0x149e, 0x1410, 0x149b, 0x14a9, 0x1460, 0x14a8, 0xfb61, 0x1460, 0x1410, 0x14a2, 0x1409, 0x149e, 0x14a2, 0x1469, 0x149e, 0x14a2, 0x140f, 0xfb61, 0x1469, 0x14a9, 0x14a2, 0x1407, 0x148d, 0x1456, 0x1407, 0x14a9, 0x141c, 0x149e, 0x1468, 0x149b, 0x14a9): 'යෙහෝවාගේ සාක්ෂිකරුවෝ',
    (0x122a, 0x120a, 0x122b, 0x1200, 0x1225, 0x120d, 0x1225, 0x1298, 0x1224, 0x1211, 0xfb61, 0x1202, 0x1225, 0x1224, 0x1204, 0x1252, 0x1200, 0x1224, 0x120f): 'யெகோவாவின் சாட்சிகள்',
    (0x135c, 0x13b3, 0x130d, 0x130e, 0x13a9, 0x1354, 0x130c, 0x13b0, 0x13bb, 0x1362, 0x13ad, 0xfb61, 0x1369, 0x13ad, 0x13dc, 0x1318, 0x135e, 0x130e): 'యెహోవాసాక్షులు',
    (0x151b, 0x151f, 0x1533, 0x1516, 0x151b, 0x1520, 0x1531, 0x151f, 0x1531, 0x1564, 0x1529, 0x1522, 0x1533): 'พยานพระยะโฮวา',
    (0x031c, 0x031d, 0x031e, 0x031f): 'یہوواہ کے گواہ',
    (0x0ad4, 0x0ad9, 0x0a14, 0x0abb, 0xfb61, 0x0a1a, 0x0a21, 0x0aaf, 0x0a29, 0x0a75): 'የይሖዋ ምሥክሮች',
    (0x0a98, 0x0ad9, 0xfb61, 0x0ad4, 0x0a06, 0x0abb, 0xfb61, 0x0a15, 0x0a2a, 0x0ab4, 0x0ab6, 0x0a28): 'ናይ የሆዋ መሰኻኽር',
    (0x0918, 0x092b, 0x0952, 0x092e, 0x0930, 0x0961, 0x0930, 0x0956, 0x0952, 0x0961, 0x0930, 0xfb61, 0x0959, 0x0929, 0xfb61, 0x0994, 0x0930, 0x0965, 0x095b, 0x092b, 0x0929): 'Свидетелите на Йехова',
    (0x0918, 0x092b, 0x0953, 0x092e, 0x0955, 0x0952, 0xfb61, 0x0908, 0x092c, 0x095b, 0x092b, 0x0952): 'Свідки Єгови',
    (0x090e, 0x0930, 0x0965, 0x095b, 0x092b, 0x0952, 0x0959, 0x0952, 0xfb61, 0x0960, 0x092b, 0x0930, 0x092e, 0x095b, 0x0966, 0x0952): 'Јеховини сведоци',
}
PHRASE_START = {k[0] for k in PHRASES}


def apply_phrases(codes):
    out = []; i = 0; n = len(codes)
    while i < n:
        c = codes[i]
        if c in PHRASE_START:
            for k, v in PHRASES.items():
                if tuple(codes[i:i + len(k)]) == k:
                    out.append(v); i += len(k); break
            else:
                out.append(c); i += 1
        else:
            out.append(c); i += 1
    return out


# markup
PAR_END = (0xfb68, 0xfb6a)
SPACE = {0xfb61: ' ', 0xfb62: ' ', 0xfb65: '\n'}
HIDE_ON, HIDE_OFF = 0xfb50, 0xfb51          # hidden citation helper text
FIELD = {0xfbbc: 'doc_ref', 0xfbbd: 'date', 0xfbbb: 'page'}  # followed by 0xfa34.. digits
CLASS = {0x40: 'body', 0x41: 'title', 0x42: 'heading', 0x43: 'question', 0x44: 'footnote'}


def hidden_digit(c):
    return 0xfa34 <= c <= 0xfa3d and '1234567890'[c - 0xfa34]


# --- minicode --------------------------------------------------------------
def load_docsfile(path):
    d = open(path, 'rb').read()
    W = lambda p, n=1: struct.unpack_from('<%dH' % n, d, B + p * 2)
    h = W(0, 64)
    if h[0] != 0xd0c5 or h[1] != 1:
        raise ValueError('bad DOCSFILE magic')
    t = W(h[0xe] | h[0xf] << 16, h[0x10]) if h[0x10] else ()
    expand = [None] * 256
    if t:
        base, off = 0, t[0] + 1
        for k in range(1, t[0] + 1):
            n = t[k] & 0xff
            for b in range(base, base + n):
                j = (b - base) * k + off
                expand[b] = t[j:j + k]
            base += n; off += n * k
        assert off == len(t)
    return d, W, h, expand


def decode(buf, expand):
    out = []; i = 0; n = len(buf)
    while i < n:
        b = buf[i]
        if b == 0xff:
            if i + 3 >= n: break
            out.append(buf[i + 1] << 8 | buf[i + 3]); i += 4
        else:
            e = expand[b]
            if e is None: raise ValueError('bad minicode byte %02x' % b)
            out.extend(e); i += 1
    return out


def area(W, d, entry, typ):
    at = entry[6] | entry[7] << 16
    for a in range(entry[11]):
        ty, pl, ph, ll, lh = W(at + a * 5, 5)
        if ty == typ:
            p, l = pl | ph << 16, ll | lh << 16
            return d[B + p * 2: B + (p + l) * 2]


# --- MEPS stream -> paragraphs ---------------------------------------------
def paragraphs(codes, unknown, sample):
    """Yield dicts: cls, style, page, text; plus doc-level fields via 'fields'."""
    fields = {}; page = None
    cur = []; cls = None; style = None; hide = False; field = None
    pars = []; marks = []
    codes = apply_phrases(hebrew_logical([c for c in codes if not 0xfc00 <= c <= 0xfeff]))

    def flush():
        nonlocal cur, style
        txt = unicodedata.normalize('NFC', ''.join(cur)).strip()
        if txt:
            pars.append({'cls': cls, 'style': style, 'page': page, 'text': txt})
        cur = []; style = None

    for k, c in enumerate(codes):
        if isinstance(c, str):
            field = None; cur.append(c); continue
        if field:
            dg = hidden_digit(c)
            if dg:
                fields[field] = fields.get(field, '') + dg
                if field == 'page': page = int(fields['page'])
                continue
            field = None
        if c in FIELD:
            field = FIELD[c]; fields.pop(field, None); continue
        if c == HIDE_ON: hide = True; continue
        if c == HIDE_OFF: hide = False; continue
        if c in PAR_END: flush(); continue
        if 0xfb40 <= c <= 0xfb4e: cls = CLASS.get(c - 0xfb00, 'class%02x' % (c - 0xfb00)); continue
        if 0xfa80 <= c <= 0xfabd: style = c - 0xfa80; continue
        if 0xfa40 <= c <= 0xfa7d: cur.append('\t'); continue
        if 0xfc00 <= c <= 0xfeff: continue           # per-character attribute (bold/italic...)
        if hide: continue
        if c in COMB: marks.append(c); continue
        if marks:
            if c == NOBASE:
                s = SPACING.get(marks[0]) if len(marks) == 1 and marks[0] in SPACING else '◌' + ''.join(COMB[m] for m in marks)
            else:
                b = CH.get(c, '\ufffd')
                s = ('i' if b == 'ı' else 'j' if b == 'ȷ' else b) + ''.join(COMB[m] for m in marks)
            marks = []; cur.append(s); continue
        if c == NOBASE: cur.append('◌'); continue
        s = SPACE.get(c) or CH.get(c)
        if s is None:
            if c >= 0xf900: continue                 # other markup (f9xx: verse markers) (fb56 par-no, fb9c.., fba0..)
            unknown[c] += 1
            if c not in sample:
                sample[c] = codes[max(0, k - 30):k + 30]
            s = '�'
        cur.append(s)
    flush()
    return fields, pars


def plain(codes):
    return ''.join(CH.get(c) or COMB.get(c) or SPACE.get(c) or
                   ('⟦%04x⟧' % c if c < 0xfa00 else '') for c in codes)


# --- PUBINFO ---------------------------------------------------------------
def meps_str(d, off):
    n = struct.unpack_from('<H', d, off)[0]
    w = struct.unpack_from('<%dH' % n, d, off + 2)
    return unicodedata.normalize('NFC', ''.join(CH.get(c) or SPACE.get(c, '') for c in w))


def pubinfo(path):
    d = open(path, 'rb').read()
    magic, = struct.unpack_from('<H', d, B)
    if magic != 0xb1f0:
        raise ValueError('bad PUBINFO magic')
    is_bible, = struct.unpack_from('<H', d, B + 6)
    compress, = struct.unpack_from('<H', d, B + 0xa)
    return {'symbol': meps_str(d, B + 0x14), 'title': meps_str(d, B + 0x2a),
            'is_bible': is_bible, 'compressed': compress == 3}


SCHEMA = """
CREATE TABLE publications(id INTEGER PRIMARY KEY, dir TEXT UNIQUE, symbol TEXT, title TEXT,
  is_bible INTEGER, doc_count INTEGER);
CREATE TABLE documents(id INTEGER PRIMARY KEY, pub_id INTEGER REFERENCES publications,
  seq INTEGER, name TEXT, title TEXT, doc_ref TEXT, date TEXT, first_page INTEGER, last_page INTEGER);
CREATE TABLE paragraphs(id INTEGER PRIMARY KEY, doc_id INTEGER REFERENCES documents,
  seq INTEGER, class TEXT, style INTEGER, page INTEGER, text TEXT);
CREATE INDEX paragraphs_doc ON paragraphs(doc_id, seq);
CREATE INDEX documents_pub ON documents(pub_id, seq);
CREATE VIRTUAL TABLE paragraphs_fts USING fts5(text, content=paragraphs, content_rowid=id,
  tokenize='unicode61 remove_diacritics 2');
CREATE TABLE unknown_codes(code TEXT PRIMARY KEY, count INTEGER, sample TEXT);
"""


def main(src, out):
    if os.path.exists(out): os.remove(out)
    db = sqlite3.connect(out)
    db.executescript(SCHEMA)
    unknown, sample = Counter(), {}
    for pdir in sorted(os.listdir(src)):
        dpath = os.path.join(src, pdir, 'DOCSFILE')
        if not os.path.exists(dpath): continue
        info = pubinfo(os.path.join(src, pdir, 'PUBINFO'))
        d, W, h, expand = load_docsfile(dpath)
        ndoc = h[6]; dt = h[0xa] | h[0xb] << 16
        pub_id = db.execute('INSERT INTO publications(dir,symbol,title,is_bible,doc_count) VALUES(?,?,?,?,?)',
                            (pdir, info['symbol'], info['title'], info['is_bible'], ndoc)).lastrowid
        for i in range(ndoc):
            e = W(dt + i * 12, 12)
            raw = struct.pack('<6H', *e[:6])
            name = raw[1:1 + raw[0]].decode('latin1')
            buf = area(W, d, e, 0x31)
            if buf is None: continue
            codes = decode(buf, expand) if any(expand) else list(struct.unpack('<%dH' % (len(buf) // 2), buf))
            fields, pars = paragraphs(codes, unknown, sample)
            title = next((p['text'] for p in pars if p['cls'] == 'title'), pars[0]['text'] if pars else '')
            pages = [p['page'] for p in pars if p['page'] is not None]
            doc_id = db.execute('INSERT INTO documents(pub_id,seq,name,title,doc_ref,date,first_page,last_page) '
                                'VALUES(?,?,?,?,?,?,?,?)',
                                (pub_id, i, name, title, fields.get('doc_ref'), fields.get('date'),
                                 min(pages) if pages else None, max(pages) if pages else None)).lastrowid
            db.executemany('INSERT INTO paragraphs(doc_id,seq,class,style,page,text) VALUES(?,?,?,?,?,?)',
                           [(doc_id, j, p['cls'], p['style'], p['page'], p['text']) for j, p in enumerate(pars)])
        db.commit()
        print('%-12s %-40s %4d docs' % (pdir, info['title'], ndoc), flush=True)
    db.execute("INSERT INTO paragraphs_fts(paragraphs_fts) VALUES('rebuild')")
    db.executemany('INSERT INTO unknown_codes VALUES(?,?,?)',
                   [('%04x' % c, n, plain(sample[c])) for c, n in unknown.most_common()])
    db.commit()
    print('unknown codes:', len(unknown), sum(unknown.values()))


if __name__ == '__main__':
    main(*sys.argv[1:3])
