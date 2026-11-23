#!/usr/bin/env python3
"""
Recover Sony XAVC clips from a formatted card image.

    python3 xavc_recover.py info    card.img
    python3 xavc_recover.py scan    card.img
    python3 xavc_recover.py extract card.img -o ./out

Reads the image only. Never touches a card. Python 3.8+, no dependencies.
The layout being exploited is described in README.md.
"""

import argparse
import os
import re
import struct
import sys

CL = 131072              # cluster size on Sony XAVC cards
KLV = b"\x00\x1c\x01\x00"  # SMPTE key that starts Sony essence
BLOCK = 64 * 1024 * 1024
READ = 16 * 1024 * 1024


def rd(f, off, n):
    f.seek(off)
    return f.read(n)


def be32(b, o=0):
    return struct.unpack_from(">I", b, o)[0]


def be64(b, o=0):
    return struct.unpack_from(">Q", b, o)[0]


def boxes(buf, start, end):
    out, p = [], start
    while p + 8 <= end:
        size, name = be32(buf, p), buf[p + 4:p + 8]
        hdr = 8
        if size == 1:
            size, hdr = be64(buf, p + 8), 16
        elif size == 0:
            size = end - p
        if size < hdr or p + size > end:
            break
        out.append((name.decode("latin1", "replace"), p, size, p + hdr, size - hdr))
        p += size
    return out


# ---------------------------------------------------------------- geometry

def find_moov(f, F, cl=CL):
    """moov sits in the clusters just before the header cluster."""
    for k in range(1, 65):
        cand = F - k * cl
        if cand < 0:
            return None
        head = rd(f, cand, 8)
        if head[:4] == b"\x00\x00\x00\x1c" and head[4:8] == b"ftyp":
            return None                      # previous clip, moov is not here
        if head[4:8] == b"moov":
            return cand, k
    return None


def parse_moov(mb):
    info = {"tracks": []}
    for name, p, size, po, pl in boxes(mb, 0, len(mb)):
        if name != "moov":
            continue
        for n2, p2, s2, po2, pl2 in boxes(mb, po, min(po + pl, len(mb))):
            if n2 == "mvhd":
                v = mb[po2]
                if v == 0:
                    info["scale"], info["dur"] = be32(mb, po2 + 12), be32(mb, po2 + 16)
                else:
                    info["scale"], info["dur"] = be32(mb, po2 + 20), be64(mb, po2 + 24)
            elif n2 == "trak":
                tr = {}
                for n3, p3, s3, po3, pl3 in boxes(mb, po2, min(po2 + pl2, len(mb))):
                    if n3 != "mdia":
                        continue
                    for n4, p4, s4, po4, pl4 in boxes(mb, po3, min(po3 + pl3, len(mb))):
                        if n4 == "hdlr":
                            tr["kind"] = mb[po4 + 8:po4 + 12].decode("latin1", "replace")
                        elif n4 == "minf":
                            for n5, p5, s5, po5, pl5 in boxes(mb, po4, min(po4 + pl4, len(mb))):
                                if n5 != "stbl":
                                    continue
                                for n6, p6, s6, po6, pl6 in boxes(mb, po5, min(po5 + pl5, len(mb))):
                                    if n6 == "stsz":
                                        tr["samples"] = be32(mb, po6 + 8)
                info["tracks"].append(tr)
    return info


def frames(info):
    for t in info["tracks"]:
        if t.get("kind") == "vide":
            return t.get("samples")
    return None


def locate(f, F, size):
    """Full geometry of one clip, or None if the arithmetic does not check out."""
    head = rd(f, F, 16)
    if len(head) < 16 or head[4:8] != b"ftyp" or head[8:12] != b"XAVC":
        return None
    md = be64(rd(f, F + CL - 8, 8))
    if md < CL or md > size:
        return None
    mv = find_moov(f, F)
    if not mv:
        return None
    M, k = mv
    body_len = k * CL + md - 16
    if body_len % CL:
        return None
    body = F - body_len
    if body < 0:
        return None
    klv = body if rd(f, body, 4) == KLV else None
    if not klv:                              # tolerate a leading reused cluster
        for step in range(1, 129):
            for s in (1, -1):
                cand = body + s * step * CL
                if cand >= 0 and rd(f, cand, 4) == KLV:
                    klv = cand
                    break
            if klv:
                break
    info = parse_moov(rd(f, M, k * CL))
    return {"F": F, "M": M, "md": md, "moov": k * CL, "body_len": body_len,
            "body": body, "klv": klv, "info": info, "frames": frames(info)}


# ---------------------------------------------------------------- scanning

def scan(f, size):
    found, off = [], 0
    while off < size:
        block = rd(f, off, min(BLOCK, size - off) + 16)
        if not block:
            break
        p = 0
        while True:
            i = block.find(b"ftyp", p)
            if i < 0:
                break
            if i >= 4 and block[i - 4:i] == b"\x00\x00\x00\x1c" and block[i + 4:i + 8] == b"XAVC":
                found.append(off + i - 4)
            p = i + 1
        off += min(BLOCK, size - off)
    return sorted(set(found))


def scan_xml(f, size):
    """Offsets of every NonRealTimeMeta sidecar and every MEDIAPRO Material ref."""
    nrtm, refs, off = [], [], 0
    while off < size:
        block = rd(f, off, min(BLOCK, size - off) + 64)
        if not block:
            break
        for needle, sink in ((b"<NonRealTimeMeta", nrtm), (b'uri="./CLIP/', refs)):
            p = 0
            while True:
                i = block.find(needle, p)
                if i < 0:
                    break
                sink.append(off + i)
                p = i + 1
        off += min(BLOCK, size - off)
    return nrtm, refs


def xml_at(f, pos, close, back=3000, limit=65536):
    start = max(0, pos - back)
    pre = rd(f, start, pos - start + 64)
    j = pre.rfind(b"<?xml")
    if j >= 0:
        start += j
    raw = rd(f, start, limit)
    end = raw.find(close)
    if end >= 0:
        raw = raw[:end + len(close)]
    else:
        nul = raw.find(b"\x00")
        if nul >= 0:
            raw = raw[:nul]
    return raw


def xml_fields(raw):
    t = raw.decode("utf-8", "replace")

    def g(pat):
        m = re.search(pat, t)
        return m.group(1) if m else None

    dur = g(r'<Duration value="(\d+)"')
    return {"dur": int(dur) if dur else None,
            "umid": (g(r'umidRef="([0-9A-Fa-f]+)"') or "").upper() or None,
            "date": g(r'<CreationDate value="([^"]+)"')}


def mediapro(f, refs, window=200000):
    """
    Original names live in MEDIAPRO.XML, which the camera rewrites on every mount.
    Its root tag is usually destroyed by a format, so parse the surviving Material
    refs instead: each entry ends where the next `uri="./CLIP/` begins.
    """
    out = {}
    for pos in refs[:1]:
        raw = rd(f, max(0, pos - 4096), window).decode("latin1", "replace")
        for part in raw.split('uri="./CLIP/')[1:]:
            name = part.split('"', 1)[0]
            if not name.upper().endswith(".MP4"):
                continue
            dur = re.search(r'dur="(\d+)"', part)
            umid = re.search(r'umid="([0-9A-Fa-f]{40,})"', part)
            out.setdefault(name, (int(dur.group(1)) if dur else None,
                                  umid.group(1).upper() if umid else None))
        if out:
            break
    return out


# ---------------------------------------------------------------- commands

def cmd_info(a):
    f = open(a.image, "rb")
    size = os.path.getsize(a.image)
    head = rd(f, 0, 64 << 20)
    i = head.find(b"EXFAT   ")
    if i < 0:
        print("no exFAT boot sector in the first 64 MiB (recovery still works)")
    else:
        bs = head[i - 3:i - 3 + 512]
        cl = (1 << bs[108]) * (1 << bs[109])
        print("image        : %s" % a.image)
        print("size         : %d bytes" % size)
        print("exFAT base   : %d" % (i - 3))
        print("cluster size : %d" % cl)
        if cl != CL:
            print("NOTE: pass --cluster-size %d" % cl)
    f.close()
    return 0


def cmd_scan(a):
    f = open(a.image, "rb")
    size = os.path.getsize(a.image)
    print("scanning %d bytes..." % size, file=sys.stderr)
    hits = scan(f, size)
    print("\n%-4s %-14s %-9s %-7s %s" % ("idx", "offset", "dur_s", "frames", "status"))
    for i, F in enumerate(hits):
        g = locate(f, F, size)
        if not g:
            print("%-4d %-14d %-9s %-7s %s" % (i, F, "-", "-", "geometry failed"))
            continue
        scale = g["info"].get("scale") or 1
        print("%-4d %-14d %-9.2f %-7s %s" % (
            i, F, (g["info"].get("dur") or 0) / scale, g["frames"],
            "ok" if g["klv"] else "DAMAGED (essence not found: overwritten)"))
    print("\n%d candidate(s)" % len(hits))
    f.close()
    return 0


def cmd_extract(a):
    f = open(a.image, "rb")
    size = os.path.getsize(a.image)
    os.makedirs(a.output, exist_ok=True)

    print("scanning...", file=sys.stderr)
    hits = [F for F in scan(f, size)]
    geos = []
    for F in hits:
        g = locate(f, F, size)
        if g and g["klv"]:
            geos.append(g)
        elif g:
            print("skip %d: essence overwritten" % F, file=sys.stderr)

    nrtm_pos, refs = scan_xml(f, size)
    sidecars, seen = [], set()
    for pos in nrtm_pos:
        raw = xml_at(f, pos, b"</NonRealTimeMeta>")
        if b"<NonRealTimeMeta" not in raw:
            continue
        d = xml_fields(raw)
        if not d["dur"] or (d["umid"] or raw) in seen:
            continue
        seen.add(d["umid"] or raw)
        d["raw"] = raw
        sidecars.append(d)
    names = mediapro(f, refs)
    print("sidecars: %d   MEDIAPRO names: %d" % (len(sidecars), len(names)), file=sys.stderr)

    by_umid = {u: n for n, (d, u) in names.items() if u}
    used, plan = set(), []
    for g in geos:
        xml = next((s for s in sidecars if s["dur"] == g["frames"]), None)
        name = by_umid.get(xml["umid"]) if xml else None
        if not name or name in used:
            name = "clip_%04d.MP4" % g["F"]
        used.add(name)
        plan.append((g, os.path.splitext(name)[0], xml))

    for g, stem, xml in plan:
        out = os.path.join(a.output, stem + ".MP4")
        with open(out, "wb") as o:
            o.write(rd(f, g["F"], CL))
            off, left = g["klv"], g["body_len"]
            while left:
                n = min(READ, left)
                buf = rd(f, off, n)
                if len(buf) != n:
                    raise IOError("short read at %d" % off)
                o.write(buf)
                off, left = off + n, left - n
            o.truncate(CL + g["body_len"])
        if xml:
            with open(os.path.join(a.output, stem + "M01.XML"), "wb") as o:
                o.write(xml["raw"])
        print("  %-24s %7.2f GB  %s" % (
            os.path.basename(out), os.path.getsize(out) / 1e9,
            os.path.basename(stem + "M01.XML") if xml else "no xml"), flush=True)
    print("\n%d clip(s) -> %s" % (len(plan), a.output))
    print("verify every file:  ffmpeg -v error -xerror -i FILE -f null -")
    f.close()
    return 0


def main():
    global CL
    ap = argparse.ArgumentParser(description="recover Sony XAVC clips from a card image")
    ap.add_argument("--cluster-size", type=int, default=CL)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("info", cmd_info), ("scan", cmd_scan), ("extract", cmd_extract)):
        p = sub.add_parser(name)
        p.add_argument("image")
        if name == "extract":
            p.add_argument("-o", "--output", required=True)
        p.set_defaults(func=fn)
    a = ap.parse_args()
    CL = a.cluster_size
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
