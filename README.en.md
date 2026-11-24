# xavc-recover

[Русский](README.md) · **English**

Recover **MP4 video** from a **formatted Sony camera card** by reading a raw image of the card.

An [Agent Skills](https://agentskills.io) skill plus the CLI script it calls. The format is supported
by **Claude Code, OpenAI Codex, VS Code Copilot, OpenCode** and other clients: give an agent the repo
URL.

> **Scope of testing.** The skill has been run in **OpenCode on Windows** and **Codex on macOS** —
> not in Claude Code or Copilot. The format is shared, the integration may differ. If it does not
> pick up, please open an issue.
>
> Verified against two cards from a **Sony ILCE-7SM3 (A7S III)**, XAVC-M4, 4K 4:2:2 10-bit.
> Every figure in this documentation comes from those cases, not from your camera. Other Sony
> bodies *likely* share the layout but **unverified** — run `scan` and confirm the clips are found
> before trusting the output. The tool only reads the image and never writes to a card.

Sony cards (A7S III, FX3, FX6, A7 IV and others) store video as ordinary `.MP4` files in
`PRIVATE/M4ROOT/CLIP/`. PhotoRec, testdisk and FAT chain walkers recover **nothing** from such a
card — the files are XAVC, and it is laid out unusually ([why](#why-the-usual-tools-fail)). This
tool finds the clips in the image, splices them, and verifies that every file decodes.

> **This tool only ever reads the image file.** It never touches a card. Make the image first.

> **Important.** Recovery is only possible if **nothing was written** to the card after the
> format. Any new recording overwrites the clusters holding the old clips.

## Skill

`SKILL.md` lives in `.agents/skills/xavc-recover/` and `.claude/skills/xavc-recover/`. From there
it is the usual routine: give an agent the repo URL and it works it out. The skill calls
`xavc_recover.py` from this repo, so clone the whole repository.

## Manually

Without an agent — five steps.

## Requirements

- Python 3.8+, standard library only.
- For the dump itself: `dd` (Linux/macOS/WSL) or a Windows equivalent — see below.
- Optional, for verification: `ffmpeg` / `ffprobe`.

### Step 0 — stop using the card

Pull it out. Every mount of the card lets the camera or OS rewrite the directory and reuse freed
clusters, which is exactly what destroys the footage. Do not "repair", do not "safely remove and
reinsert to check", do not let anything auto-mount it read-write. Prefer a write blocker if you
have one.

### Step 1 — free the space you need

**Budget at least 2× the card size.** A 128 GB card produces a ~128 GB image, and the recovered
clips add up to roughly the card's used space on top of that. In practice:

```
image (card size)  +  recovered clips (~= the footage you actually shot)
```

In other words, a card that was 3/4 full needs ~1.75× its capacity free. Reserve the space
before you start; running out halfway wastes the attempt.

Find that space **somewhere other than the card itself**. Writing the image onto the same device
you are imaging is a reliable way to overwrite exactly the data you are trying to recover. Use a
different disk, formatted exFAT or NTFS — FAT32 will not take a file over 4 GB, and a 128 GB card
produces a 128 GB image.

If you are short on space, extract clips one at a time and delete the image only after checking
the output.

### Step 2 — make the image

**Linux / macOS**

```bash
# find the device
lsblk            # or: diskutil list

# full device, not a partition — you want the exFAT volume and everything before it
sudo dd if=/dev/sdX of=/path/to/card.img bs=4M status=progress conv=noerror,sync
sync
```

**Windows (WSL)**

```powershell
wsl --mount \\.\PHYSICALDRIVE1 --bare
# then inside WSL:
sudo dd if=/dev/sdX of=/mnt/d/card.img bs=4M status=progress
```

**Windows without WSL** — `dd` from [chrysocome](http://www.chrysocome.net/dd) run **as
Administrator**:

```cmd
dd if=\\.\PhysicalDrive1 of=C:\path\to\card.img bs=4M --progress
```

`PhysicalDriveN` (the whole disk) — **not** `\\.\D:` (the volume). Confirm the right number:
a card is removable, and its size matches the card. Getting this wrong and swapping `if=`/`of=`
writes over the card.

The image will be exactly the device size. A trailing `Error reading file: 27 (device not
ready)` on the last block is normal — the reader tried one block past the end.

### Step 3 — look before you cut

```bash
python3 xavc_recover.py info card.img
```

Shows the exFAT geometry and cluster size. If your card uses a different cluster size than
131072, pass `--cluster-size`.

```bash
python3 xavc_recover.py scan card.img
```

Lists every candidate clip with its duration and status. **Read the status column.** `ok` means
the clip's essence starts exactly where the geometry predicts and the clip is worth extracting.
`DAMAGED` means the geometry did not validate — usually because the card was already reusing those
clusters; those clips are damaged or gone.

Indices are assigned in image order, which is **not** shooting order. Use durations and the XML
timestamps to sort.

### Step 4 — extract

```bash
python3 xavc_recover.py extract card.img -o ./recovered
```

Writes each clip plus its sidecar XML. Clips that fail validation are skipped with a reason.

### Step 5 — check that the clips decode

```bash
for f in ./recovered/*.MP4; do
  echo "== $f"
  ffprobe -v error -select_streams v:0 \
    -show_entries stream=codec_name,width,height,avg_frame_rate,nb_frames -of default=nw=1 "$f"
  ffmpeg -v error -xerror -i "$f" -f null - && echo "decode OK"
done
```

Any output at all from the `ffmpeg` line is a failure. `Invalid NAL unit size` or
`missing picture in access unit` means the splice is wrong or the clip was partially overwritten.

## Naming and the original file names

The camera rewrites `PRIVATE/M4ROOT/MEDIAPRO.XML` on every mount. After a format it often lists
only the newest clip — but its **deleted copy usually survives** in freed clusters, and it is the
best source of the original file names, their durations and their UMIDs.

The tool finds that deleted copy automatically and joins it to the clips on **UMID**. Duration is
not used as a key: two clips of the same length are common on a real card, so a duration match
alone is not proof of identity. If a clip cannot be named safely it is written as
`clip_<offset>.MP4` rather than given a wrong name.

Names are not cosmetic: two adjacent clips differing by one digit in the name is the difference
between handing an editor the right take and the wrong one. If the join is ambiguous, check
manually rather than trusting a sequence number.

## What you get back

- **MP4 video**: H.264, the recorded resolution and frame rate.
- **Audio**: LPCM (`pcm_s16be`, `twos`) — untouched.
- **Camera metadata**: the third `rtmd` track survives, so lens, gamma, gyro and settings are
  still in the file.
- **Timecode: in the file.** XAVC MP4 has no `tmcd` track, but the LTC sits in the third `rtmd`
  track (`hdlr=meta`, `stsd=rtmd`) — Sony KLV, one packet per frame. That is why Resolve and
  Catalyst Browse show source timecode with no XML at all, while `ffprobe` prints nothing and
  reports the track as `codec_name=unknown`. The sidecar `M01.XML` (`<LtcChangeTable tcFps="25">`)
  duplicates the LTC and records discontinuities: worth shipping with the clips, but the timecode
  survives without it.

## Why the usual tools fail

Three independent reasons:

1. **PhotoRec and friends reject the brand.** They match known `ftyp` brands; `XAVC` is not in
   their tables, so valid files are discarded as unknown.
2. **The FAT chain is gone.** A format zeroes the allocation table, and old clips are fragmented
   anyway, so chain walking has nothing to walk.
3. **Linear carving runs the wrong way.** The camera writes the essence first and the header
   cluster *after* it, so "start at `ftyp`, copy forward" produces a header with no media behind
   it — "moov atom not found". Details in [docs/format.md](docs/format.md).

## Compatibility

Verified on two cards from a Sony **ILCE-7SM3 (A7S III)**, `XAVC-M4@2.00.00`,
`AVC200_3840_2160_H422P@L52`, 50p and 59.94p, LPCM 2ch.

Other Sony bodies recording XAVC S / XAVC S-I / XAVC HS are *likely* to share the layout, but
that is unverified. Check before trusting it:

```bash
python3 xavc_recover.py scan card.img
```

If candidates are found with status `ok`, the layout matches. If candidates are found but all show
`DAMAGED`, the layout differs — please open an issue with the `info` output and the `scan` result.

Not applicable to: Sony cameras writing AVCHD (`.MTS`), or cards formatted to a different
filesystem.

## Limitations

- **Overwritten clusters cannot come back.** If the camera reused a region, those bytes are gone.
  Check the status column in `scan`.
- **One clip per header cluster.** A header whose body ran into the previous clip's clusters is
  reported as damaged rather than silently mis-spliced.
- **Fragmented bodies are out of scope.** The splice assumes the body is contiguous behind the
  header cluster, which is what the camera produces. If a clip's clusters were interleaved with
  other files, this tool will not rebuild it.
- No file is written unless the KLV key validates at the predicted offset.

## Prior art, and what is different here

Existing XAVC recovery projects work **one step later** than this tool:

- [`open-recovery`](https://github.com/bendrape1-byte/open-recovery) takes files that a recovery
  tool already returned — full size, right name, unplayable — and rebuilds the lost `moov` by
  grafting codec settings from a *reference clip* shot in the same mode. It needs the clip to
  exist as a file first.
- [`secondtake`](https://github.com/felipelimas8/secondtake) repairs corrupted XAVC S clips:
  header repair, `moov` rebuild, resync.

Both assume you already have a broken file on disk. On a formatted card you usually do not: nothing
has carved the clip yet, and generic carvers refuse it (brand) or produce a header with no media
behind it (direction).

This tool starts earlier — from the **raw image** — and because the camera leaves the `moov` intact
right next to the header cluster, it never has to reconstruct one. That is why it needs **no
reference clip and no per-mode table**: the index is not lost, it is just not where you would look
for it.

If you already have broken files and no image, use those projects — they are the right tool. If you
have the card (or an image of it), start here.

## Photos are a different problem

This tool recovers **video only**. Stills on a Sony card live in `DCIM/` as ordinary JPEG/ARW files
with none of this structure, and PhotoRec handles them fine. If a card held only stills, a generic
carver is the right tool and this one will find nothing.

A card that recorded video through `PRIVATE/M4ROOT` has no `DCIM/` at all — that is normal for a
video-mode card, not a sign that photos were lost.

## Licence

MIT.
