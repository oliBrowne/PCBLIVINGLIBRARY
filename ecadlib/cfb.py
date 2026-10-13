"""Minimal Compound File Binary (OLE2 / MS-CFB) *writer*: version 3, 512-byte sectors.

    write_cfb(path, {"FileHeader": b"...", "Library": {"Header": b"...", "Data": b"..."}})

A dict is a storage, bytes is a stream. Streams under the 4096-byte cutoff live in the mini
stream (64-byte mini sectors); larger ones in regular sectors. FAT/MiniFAT/DIFAT are built to
fit, so files past 109 FAT sectors (~7 MB) get DIFAT sectors too.

Directory: each storage's children form a balanced binary search tree ordered the MS-CFB way
(UTF-16 length first, then upper-cased code units) with every node black. MS-CFB 2.6.4 allows
this: "the simplest implementation of the above invariants would be to mark every node as
black, in which case the tree is simply a binary tree". Timestamps and CLSIDs are left zero.
olefile (BSD) reads files written here; tests/test_offline.py checks it raises no warnings."""
import struct

SECTOR, MINI, CUTOFF = 512, 64, 4096
FREESECT, ENDOFCHAIN, FATSECT, DIFSECT, NOSTREAM = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC, 0xFFFFFFFF
STORAGE, STREAM, ROOT = 1, 2, 5


def _key(name):
    return (len(name.encode("utf-16-le")) // 2, name.upper())


class _Entry:
    def __init__(self, name, kind, data=b""):
        if len(name) > 31 or any(c in name for c in "/\\:!"):
            raise ValueError(f"illegal CFB name {name!r} (max 31 chars, no / \\ : !)")
        self.name, self.kind, self.data = name, kind, data
        self.children = []
        self.left = self.right = self.child = NOSTREAM
        self.start, self.size = ENDOFCHAIN, 0


def _flatten(tree):
    """dict tree -> list of _Entry with sibling/child ids (index 0 = root)."""
    entries = [_Entry("Root Entry", ROOT)]

    def add(parent, node):
        for name, val in node.items():
            e = _Entry(name, STORAGE if isinstance(val, dict) else STREAM, b"" if isinstance(val, dict) else bytes(val))
            entries.append(e); parent.children.append(len(entries) - 1)
            if isinstance(val, dict):
                add(e, val)
        keys = [_key(entries[i].name) for i in parent.children]
        if len(set(keys)) != len(keys):
            raise ValueError(f"duplicate (case-insensitive) names under {parent.name!r}")

    def balance(ids):                         # sorted ids -> root id of a balanced BST
        if not ids:
            return NOSTREAM
        m = len(ids) // 2
        e = entries[ids[m]]
        e.left, e.right = balance(ids[:m]), balance(ids[m + 1:])
        return ids[m]

    add(entries[0], tree)
    for e in entries:
        if e.kind != STREAM:
            e.child = balance(sorted(e.children, key=lambda i: _key(entries[i].name)))
    return entries


def _chain(fat, first, n):
    for i in range(n):
        fat[first + i] = first + i + 1 if i < n - 1 else ENDOFCHAIN


def to_bytes(tree):
    entries = _flatten(tree)
    streams = [e for e in entries if e.kind == STREAM]
    mini = bytearray(); minifat = []
    for e in streams:                         # small streams -> mini stream
        e.size = len(e.data)
        if 0 < e.size < CUTOFF:
            n = -(-e.size // MINI)
            e.start = len(minifat); _chain_list(minifat, e.start, n)
            mini += e.data + b"\0" * (n * MINI - e.size)
    root = entries[0]
    root.data, root.size = bytes(mini), len(mini)

    # sector plan: [mini stream][big streams][minifat][directory][FAT][DIFAT]
    big = ([root] if root.size else []) + [e for e in streams if e.size >= CUTOFF]
    nsec = lambda b: -(-b // SECTOR)
    data_secs = sum(nsec(e.size) for e in big)
    n_minifat = nsec(len(minifat) * 4)
    n_dir = nsec(len(entries) * 128)
    used = data_secs + n_minifat + n_dir
    n_fat = n_difat = 0
    while True:                               # FAT must also map itself and the DIFAT sectors
        total = used + n_fat + n_difat
        nf = nsec(total * 4)
        nd = 0 if nf <= 109 else -(-(nf - 109) // 127)
        if (nf, nd) == (n_fat, n_difat):
            break
        n_fat, n_difat = nf, nd
    total = used + n_fat + n_difat
    fat = [FREESECT] * (n_fat * SECTOR // 4)

    pos = 0
    for e in big:
        n = nsec(e.size)
        e.start = pos; _chain(fat, pos, n); pos += n
    minifat_start = pos if n_minifat else ENDOFCHAIN
    if n_minifat:
        _chain(fat, pos, n_minifat); pos += n_minifat
    dir_start = pos; _chain(fat, pos, n_dir); pos += n_dir
    fat_secs = list(range(pos, pos + n_fat))
    for s in fat_secs:
        fat[s] = FATSECT
    pos += n_fat
    difat_secs = list(range(pos, pos + n_difat))
    for s in difat_secs:
        fat[s] = DIFSECT
    assert pos + n_difat == total

    out = bytearray()
    hdr = bytearray(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 16)
    hdr += struct.pack("<HHHHH6sIIIIIIIII", 0x3E, 3, 0xFFFE, 9, 6, b"\0" * 6, 0, n_fat, dir_start, 0, CUTOFF,
                       minifat_start, n_minifat, difat_secs[0] if difat_secs else ENDOFCHAIN, n_difat)
    hdr += struct.pack("<109I", *(fat_secs[:109] + [FREESECT] * (109 - min(109, n_fat))))
    out += hdr

    for e in big:
        out += e.data + b"\0" * (-len(e.data) % SECTOR)
    mf = struct.pack(f"<{len(minifat)}I", *minifat)
    out += mf + b"\xff" * (-len(mf) % SECTOR)          # unused MiniFAT slots = FREESECT
    d = b"".join(_dirent(e) for e in entries)
    d += _dirent(None) * ((-len(d) % SECTOR) // 128)
    out += d
    out += struct.pack(f"<{len(fat)}I", *fat)
    rest = fat_secs[109:]
    for i, s in enumerate(difat_secs):        # 127 FAT-sector ids + next-DIFAT pointer
        chunk = rest[i * 127:(i + 1) * 127]
        nxt = difat_secs[i + 1] if i + 1 < len(difat_secs) else ENDOFCHAIN
        out += struct.pack("<128I", *(chunk + [FREESECT] * (127 - len(chunk)) + [nxt]))
    assert len(out) == SECTOR * (1 + total), (len(out), total)
    return bytes(out)


def _chain_list(lst, first, n):
    lst.extend(first + i + 1 if i < n - 1 else ENDOFCHAIN for i in range(n))


def _dirent(e):
    if e is None:                             # unused slot
        return b"\0" * 64 + struct.pack("<HBBIII16sIQQIQ", 0, 0, 0, NOSTREAM, NOSTREAM, NOSTREAM, b"\0" * 16, 0, 0, 0, 0, 0)
    nm = (e.name + "\0").encode("utf-16-le")
    start = 0 if e.kind == STORAGE else (e.start if e.size else ENDOFCHAIN)
    return (nm + b"\0" * (64 - len(nm)) +
            struct.pack("<HBBIII16sIQQIQ", len(nm), e.kind, 1, e.left, e.right, e.child,
                        b"\0" * 16, 0, 0, 0, start, e.size))


def write_cfb(path, tree):
    data = to_bytes(tree)
    with open(path, "wb") as f:
        f.write(data)
    return len(data)
