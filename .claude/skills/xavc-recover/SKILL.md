---
name: xavc-recover
description: Recover MP4 video from a formatted Sony camera card by reading a raw card image. Use when a Sony card was formatted and clips are lost, when PhotoRec/Recuva/Disk Drill/EaseUS/4DDiG find nothing or return MP4s that will not play ("moov atom not found", black frames), or when a dd/.img/.dd image needs XAVC clips carved out. Handles XAVC, XAVC S, XAVC S-I. Recovers MP4 and sidecar XML, restores original filenames, verifies each file decodes. Also: восстановить mp4 с отформатированной флешки Sony, битые mp4 после восстановления.
license: MIT
compatibility: Python 3.8+, stdlib only. ffmpeg optional for verification. Reads the image only, never writes to a card.
metadata:
  author: ivsafronov
  repo: https://github.com/ivsafronov/xavc-recover
---

# Recover MP4 from a formatted Sony card

The user wants their **`.MP4` files** back. XAVC is just the container brand — say MP4, that is the
word they search for. Files live in `PRIVATE/M4ROOT/CLIP/`.

**One important thing first:** recovery only works if nothing was written to the card after the
format. Any new recording overwrites the clusters holding the old clips. If the card was reused,
some clips will be damaged or gone — say so before starting, not after.

## Stop them before they make it worse

The first instinct is to plug the card in and run PhotoRec. That can destroy the footage.

1. **Do not mount the card again.** Every mount lets the camera or OS rewrite the directory and
   reuse freed clusters. Pull it out and leave it out.
2. **Image the whole device, not a partition.** A card is removable and its size matches — confirm
   with `lsblk` / `diskutil list` / `Get-Disk` before writing anything. Swapping `if=` and `of=`
   destroys the card.
3. **Check free space.** Image = card size, plus the recovered clips ≈ the footage actually shot.
   Ask for ~2× the card size. Running out halfway wastes the attempt.
4. Never write the image to a partition of the card.

## Workflow

The tool is `xavc_recover.py` in this repo. Stdlib only.

```bash
sudo dd if=/dev/sdX of=/path/card.img bs=4M status=progress conv=noerror,sync
python3 xavc_recover.py info    card.img
python3 xavc_recover.py scan    card.img
python3 xavc_recover.py extract card.img -o ./recovered
```

`info` shows exFAT geometry and cluster size (pass `--cluster-size` if not 131072).

`scan` lists clips with status. **Read the status column:** `ok` = worth extracting, `DAMAGED` =
essence not found at the predicted offset, usually because the clusters were reused. Indices are
image order, **not** shooting order.

`extract` writes each clip plus its sidecar XML, skipping anything that fails validation.

Then verify every file — never ship one you have not decoded:

```bash
for f in ./recovered/*.MP4; do
  ffmpeg -v error -xerror -i "$f" -f null - && echo "OK $f"
done
```

Any output from that `ffmpeg` line is a failure.

## The two traps

**Do not carve forward from `ftyp`.** The camera writes the essence first and the 128 KiB header
cluster *after* the body, with `moov` in the clusters just before that header. Copying forward
gives a header with no media behind it — that is the "moov atom not found" error. The tool handles
this; do not improvise a carver.

**Duration is not a unique key.** When matching clips to their `M01.XML`, two clips of the same
length are common. Join on **UMID** (`umidRef` in the XML, `umid` in `MEDIAPRO.XML`). Naming a clip
wrongly is worse than a `clip_0042.MP4` placeholder — an editor will cut the wrong take.

Names come from the **deleted** `MEDIAPRO.XML`: the camera rewrites it on every mount, so the live
copy usually lists only the newest clip, but the old copy survives in freed clusters. The tool
finds it automatically.

## Report honestly

- `DAMAGED` in `scan` means the clip is gone. Do not extract it and hope.
- A partly overwritten clip may still play past the damage. Find where content resumes and report
  a range ("readable 1:19–2:32") rather than handing over a file that silently starts broken.
- Verify by decoding, not by file size. Right length, garbage content is common.
- **Timecode is in the file, not only in the XML.** XAVC MP4 has no `tmcd` track, but the LTC sits
  in the third `rtmd` track (Sony KLV, one packet per frame), so Resolve and Catalyst Browse read
  it with no XML at all. `ffprobe` does not show it — never tell the user the timecode is lost.
- Frame rate can legitimately differ within one card (50p and 59.94p if the camera was switched to
  NTSC). That is not corruption.

## Compatibility

Verified on **two** cards: Sony ILCE-7SM3 (A7S III), XAVC-M4, 4K 4:2:2 10-bit, LPCM 2ch. Clip
counts, modes and names in this skill are from those cases, not a spec for the user's camera.

Other Sony bodies likely share the layout but **are unverified**. Make the user confirm with `scan`
before trusting output. If candidates appear but all are `DAMAGED`, the layout differs — stop and
report rather than shipping half-built files.

Video only. Stills (JPEG/ARW in `DCIM/`) need a normal carver; PhotoRec handles those.

`docs/format.md` has the box layout and geometry formulas if something needs debugging by hand.
