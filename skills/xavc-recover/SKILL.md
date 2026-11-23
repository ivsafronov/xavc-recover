---
name: xavc-recover
description: Recover video clips from a formatted Sony XAVC camera card (A7S III and similar XAVC bodies). Use when someone has accidentally formatted a Sony camera SD/CFexpress card and needs the footage back, when standard undelete tools (PhotoRec, testdisk) returned nothing or produced files that will not play, or when a card image needs its XAVC clips carved and verified. Covers imaging the card safely, scanning for clip headers, splicing header cluster to body, joining clips to their sidecar XML, and proving each file decodes.
---

# Recovering a formatted Sony XAVC card

The footage is usually still there. Sony XAVC cards fail the standard recovery workflow for
reasons that are specific and fixable — but only if nothing writes to the card in the meantime.

The user's first instinct will be to plug the card in and run PhotoRec. Stop them before they do.

## Before anything else

1. **The card must not be mounted read-write again.** Every mount lets the camera or OS rewrite
   the directory and reuse freed clusters. Tell the user to pull it out and leave it out.
2. **Free space check.** The image is the full card size, and the recovered clips add roughly the
   amount of footage shot on top of that. Ask for ~2× the card size free. If they do not have it,
   say so before starting — running out of space halfway through wastes the whole attempt.
3. **Never image onto the card itself**, and never image a partition when you can image the whole
   device.

Confirm which device is the card before writing anything. `lsblk` / `diskutil list` on
Linux/macOS, `Get-Disk` on Windows. A card is removable and its size matches the label. Getting
`if=` and `of=` backwards destroys the card — check twice.

## Workflow

The tool is `xavc_recover.py` in this repo, stdlib only.

```bash
# 1. image the whole device, not a partition
sudo dd if=/dev/sdX of=/path/card.img bs=4M status=progress conv=noerror,sync

# 2. sanity check the image and the cluster size
python3 xavc_recover.py info card.img

# 3. find candidates, read the klv column
python3 xavc_recover.py scan card.img -o scan.json

# 4. extract
python3 xavc_recover.py extract card.img -o ./recovered

# 5. verify every file decodes
for f in ./recovered/*.MP4; do
  ffmpeg -v error -xerror -i "$f" -f null - && echo "OK $f"
done
```

## The two things that go wrong

**Linear carving produces unplayable files.** Do not "find `ftyp` and copy forward". The camera
writes the essence first and the 128 KiB header cluster *after* the body, and `moov` sits in the
clusters immediately before that header cluster. Copying forward yields a header with no media
behind it and the player says "moov atom not found". The tool handles this; do not improvise a
carver.

**Duration is not a unique key.** When joining clips to their `M01.XML` sidecars, two clips of the
same length are common on a real card. Join on **UMID** (`umidRef` in the XML, `umid` in
`MEDIAPRO.XML`), and only fall back to duration when there is exactly one candidate. Handing an
editor the wrong take under the right-looking name is worse than a `clip_0042.MP4` placeholder.

The best source of original names is the **deleted** `PRIVATE/M4ROOT/MEDIAPRO.XML`. The camera
rewrites it on every mount, so the live copy usually lists only the newest clip — but the old copy
survives in freed clusters and has every name, duration and UMID. The tool finds it automatically.

## Reporting results honestly

- **`klv: NO` means damaged or gone.** Do not extract it and hope. If the card was already in use
  after the format, the camera has been writing over the old clusters, and some clips are lost.
  Say which ones and how much of each is readable.
- **A partially overwritten clip may be playable past the damage.** Splice it, decode it, and find
  the timestamp where content resumes. Report a range ("readable from 1:19 to 2:32") rather than
  handing over a file that silently starts broken.
- **Verify with a decode, not with a file size.** A file can have the right length and still be
  garbage. `ffmpeg -v error -xerror -i f -f null -` must be silent.
- **Do not promise timecode from the MP4.** XAVC MP4 carries no `tmcd` track — verified on
  untouched camera files. LTC exists only in the sidecar XML, and most NLEs ignore it. Say this
  up front if the client asks about timecode.
- **Check frame rate against the XML `captureFps`.** A camera can be switched to NTSC mid-day, so
  clips from one card legitimately differ (50p and 59.94p on ours). That is not corruption.

## Compatibility

Verified on Sony ILCE-7SM3 (A7S III), XAVC-M4, `AVC200_3840_2160_H422P@L52`, LPCM 2ch.

For any other XAVC body, run `probe` on a **live** clip from the card first. If it reports
`XAVC brand: yes` and `rtmd track: present`, the layout matches and the carver will work. If
`scan` shows candidates but all `klv: NO`, the layout differs — stop and report rather than
shipping half-built files.

`docs/format.md` has the box layout, the geometry formulas and the verification checks if
something needs to be debugged by hand.
