# Sony XAVC file layout on exFAT cards (reverse-engineered)

Everything here was derived from one card: a 128 GB exFAT card out of a **Sony ILCE-7SM3
(A7S III)** recorded in `XAVC-M4@2.00.00`, `AVC200_3840_2160_H422P@L52`, `LPCM16`, 50p (and a few
59.94p clips).

Treat the concrete numbers below as that example, not as constants of the format. Cluster size,
moov size, the `free` box length and the frame rates all depend on the camera and recording mode.
The *structure* is what generalises; the values are what was observed.

## Why naive carving fails

The intuitive approach — find `ftyp`, copy forward until the next `ftyp` — produces files that
players reject with **"moov atom not found"**.

Reason: the camera does **not** write the file front-to-back. It writes the *essence* (mdat
payload) first and puts the **128 KiB header cluster after the body**. In linear order on the
card a clip looks like:

```
[ ... essence clusters ... ] [ header cluster (128 KiB) ]
                              ^ ftyp, uuid, free, mdat header (64-bit size), moov
```

So the `ftyp` you find by scanning is the **last** thing, not the first.

**A second trap:** the `moov` box is *not* inside that header cluster. It sits in the clusters
immediately **before** the header cluster (1..3 clusters earlier), i.e. after the body. The header
cluster only contains `ftyp` + `uuid` + `free` + the `mdat` box header.

## Boxes inside the header cluster

```
offset 0        ftyp   size 28      brand "XAVC", compatible "XAVCmp42iso6"
offset 28       uuid   size 148     Sony PROF box (encoder profile; 0x21D24FCE... UUID)
offset 176      free   size 130880  padding
offset 131056   mdat   size 1 (largesize follows at +131064, 64-bit BE)
```

`131056 = cluster_size - 16`, and the 64-bit `mdat` largesize is at `cluster_size - 8`.
Generalises to any cluster size.

## Geometry

Given a candidate header-cluster offset `F` (the `ftyp` **size field**, i.e. the `ftyp` text
offset minus 4):

```
CL   = cluster size (131072 on this card)
md   = be64(mdat largesize)                       # at F + CL - 8
M    = start of the moov box                      # found by walking back in CL steps
moov_size = (F - M)                               # 1..3 clusters, multiple of CL

body_len   = moov_size + md - 16
body_start = F - body_len

# hard checks
body_len % CL == 0
read(body_start, 4) == 00 1C 01 00                # SMPTE KLV key, Sony essence start
```

If the KLV key is absent, try `body_start ± k*CL` for k = 1..128 before giving up. That recovers
clips where a leading cluster was reused by the filesystem.

## Reassembly

The output file is just header cluster + body, then truncate:

```
out = read(F, CL)  +  read(body_start, body_len)
out.truncate(CL + body_len)
```

`CL + body_len == (CL - 16) + md + moov_size`, which is exactly where `moov` must end up.
**Do not patch the `mdat` largesize** — with this layout it lands where it should.

Seek granularity does not matter; reading in 16 MiB blocks works.

## Verification (do all four)

1. `co64` chunk offsets must be monotonic and `< CL - 16 + md` for every track.
2. `stts` sample count × delta / timescale must equal `mvhd` duration.
3. ffprobe: `h264`, expected resolution, `r_frame_rate` matching the XML `captureFps`.
4. Full decode: `ffmpeg -v error -xerror -i clip.mp4 -f null -` must print nothing.

If you see `Invalid NAL unit size` / `missing picture in access unit`, the splice is wrong or the
clip was partially overwritten — do not ship it.

## What the MP4 does *not* contain

- **No `tmcd` track — no timecode in the MP4.** Sony keeps LTC in the sidecar XML
  (`<LtcChangeTable tcFps="25">`). Verified on an untouched camera file as well as on recovered
  ones. Without the XML you get relative time only.
- Creation time *is* in `mvhd` (`creation_time`, epoch 1904) and shows up in ffprobe.
- The third track **is** present: `hdlr=meta`, `stsd=rtmd`, carrying real Sony KLV essence
  (`00 1C 01 00` + UL `060E2B34...`). Camera metadata survives.

## Sidecar files the camera writes

```
PRIVATE/M4ROOT/MEDIAPRO.XML              index of clips: uri, fps, dur, ch, umid
PRIVATE/M4ROOT/CLIP/<name>.MP4
PRIVATE/M4ROOT/CLIP/<name>M01.XML    NonRealTimeMeta: Duration, CreationDate, LtcChangeTable,
                                     umidRef, captureFps, gamma, lens, gyro
PRIVATE/M4ROOT/THMBNL/<name>T01.JPG  thumbnail
```

`MEDIAPRO.XML` is rewritten on every mount and after a format it may list only the newest clip —
but its **deleted** copy usually survives in freed clusters and is the single best source of the
original file names and `dur` values.

`umidRef` in each `M01.XML` matches the `umid` attribute in `MEDIAPRO.XML`. **Join on UMID**, not
on duration — durations are not unique.

## Compatibility

Verified on: **ILCE-7SM3 (A7S III)**, XAVC-M4, 4K 4:2:2 10-bit.

The layout depends on the camera writing `ftyp` brand `XAVC` and appending the header cluster
after the body. Other Sony bodies using XAVC S / XAVC S-I / XAVC HS are *expected* to share it,
but this was not verified. **Check before trusting it on your card:**

```bash
python3 xavc_recover.py probe /path/to/any_live_clip.MP4
```

It reports whether the file has the `XAVC` brand and an `rtmd` track. If the clip plays and the
brand matches, the carver's assumptions hold; then run `scan` and confirm the KLV check passes.
If `scan` finds zero candidates with a valid KLV key, the layout is different — open an issue with
the `--debug` output.
