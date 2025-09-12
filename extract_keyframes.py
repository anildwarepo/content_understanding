#!/usr/bin/env python3
"""
extract_keyframes.py

Extracts images from a video at keyframe timestamps provided in a JSON blob
and (optionally) matches transcript phrase segments to the nearest keyframes.

Usage (basic):
  python extract_keyframes.py --video "WhatsApp Video 2025-09-11 at 13.22.08_51b5dec2.mp4" --json "metadata.json" --outdir "frames"

Match transcript phrases (comma-separated segments) to keyframes and only export those:
  python extract_keyframes.py --video input.mp4 --json metadata.json --outdir frames --match_phrases --only_matched

Outputs:
- Extracted images in --outdir named like: <prefix>.<ms>.<ext>
- keyframes_index.csv (all keyframes, regardless of --only_matched)
- phrase_keyframe_map.csv (when --match_phrases is enabled)
"""
import argparse
import json
import os
import sys
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Tuple

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def ms_to_timecode(ms: int) -> str:
    s, ms_part = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms_part:03d}"

def read_json(json_arg: str | None) -> Dict[str, Any]:
    if json_arg:
        p = Path(json_arg)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        else:
            return json.loads(json_arg)
    else:
        data = sys.stdin.read()
        if not data.strip():
            raise SystemExit("No JSON provided via --json or stdin.")
        return json.loads(data)

def extract_keyframe_times(blob: Dict[str, Any]) -> List[int]:
    try:
        contents = blob["result"]["contents"]
        if contents and isinstance(contents, list):
            item = contents[0]
            times = item.get("KeyFrameTimesMs")
            if isinstance(times, list) and all(isinstance(t, int) for t in times):
                return times
    except Exception:
        pass

    # Fallback search
    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "KeyFrameTimesMs" and isinstance(v, list) and all(isinstance(t, int) for t in v):
                    return v
                got = walk(v)
                if got is not None:
                    return got
        elif isinstance(obj, list):
            for v in obj:
                got = walk(v)
                if got is not None:
                    return got
        return None
    found = walk(blob)
    if found is not None:
        return found
    raise ValueError("Could not find 'KeyFrameTimesMs' in JSON.")

def extract_phrase_segments(blob: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Partition transcript words into comma/period-delimited segments.
    Returns a list of dicts with: text, startTimeMs, endTimeMs
    """
    contents = blob["result"]["contents"]
    if not contents:
        return []
    item = contents[0]
    phrases = item.get("transcriptPhrases", [])
    segments = []
    for p in phrases:
        words = p.get("words", [])
        if not words:
            continue
        cur_words = []
        seg_start = None
        for w in words:
            wtext = (w.get("text") or "").strip()
            ws = int(w.get("startTimeMs"))
            we = int(w.get("endTimeMs"))
            if seg_start is None:
                seg_start = ws
            cur_words.append(wtext.rstrip())
            # If a word ends with a comma or period, we close the segment
            if wtext.endswith(",") or wtext.endswith("."):
                # Clean trailing punctuation in segment text
                joined = " ".join(cur_words).strip()
                # Normalize spaces before punctuation introduced by tokenization
                joined = joined.replace(" ,", ",").replace(" .", ".")
                # Remove trailing comma/period for the human-readable segment
                human = joined.rstrip(",.").strip()
                segments.append({
                    "text": human,
                    "startTimeMs": seg_start,
                    "endTimeMs": we,
                })
                cur_words = []
                seg_start = None
        # Flush any trailing words without punctuation
        if cur_words:
            joined = " ".join(cur_words).strip()
            human = joined.rstrip(",.").strip()
            end_ms = words[-1].get("endTimeMs")
            segments.append({
                "text": human,
                "startTimeMs": seg_start if seg_start is not None else int(words[0].get("startTimeMs", 0)),
                "endTimeMs": int(end_ms) if end_ms is not None else int(words[-1]["endTimeMs"]),
            })
    return segments

def nearest_keyframe(target_ms: int, keyframes: List[int]) -> int:
    return min(keyframes, key=lambda k: abs(k - target_ms))

def run_ffmpeg_extract(video: Path, timestamp_ms: int, out_path: Path, quality: int, scale_width: int | None, fmt: str, dry_run: bool = False):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-ss", f"{timestamp_ms/1000:.3f}", "-frames:v", "1"]
    if scale_width:
        vf = f"scale=w={scale_width}:h=-1:flags=bicubic"
        cmd += ["-vf", vf]
    if fmt.lower() in ("jpg", "jpeg"):
        cmd += ["-q:v", str(quality)]
    cmd.append(str(out_path))
    if dry_run:
        print(" ".join(cmd))
        return
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        eprint("Error: ffmpeg not found. Please install ffmpeg and ensure it's on your PATH.")
        raise
    except subprocess.CalledProcessError as e:
        eprint(f"ffmpeg failed for {timestamp_ms} ms -> {out_path}: {e}")
        raise

def main():
    ap = argparse.ArgumentParser(description="Extract images from a video at keyframe timestamps provided in JSON metadata and match transcript phrase segments to keyframes.")
    ap.add_argument("--video", required=True, help="Path to the video file")
    ap.add_argument("--json", help="Path to JSON metadata file OR inline JSON string. If omitted, read from stdin.")
    ap.add_argument("--outdir", default="keyframes", help="Output directory (default: keyframes)")
    ap.add_argument("--prefix", default="keyFrame", help="Filename prefix for extracted keyframes (default: keyFrame)")
    ap.add_argument("--format", default="jpg", choices=["jpg", "jpeg", "png"], help="Image format (default: jpg)")
    ap.add_argument("--quality", type=int, default=2, help="JPG quality for ffmpeg -q:v (1=best, 31=worst) (default: 2)")
    ap.add_argument("--scale_width", type=int, default=None, help="Optional output width; keeps aspect ratio")
    ap.add_argument("--dry_run", action="store_true", help="Show ffmpeg commands without executing")
    ap.add_argument("--timestamps_only", action="store_true", help="Print keyframe timestamps and exit")
    ap.add_argument("--match_phrases", action="store_true", help="Create a phrase->keyframe mapping using transcript segments")
    ap.add_argument("--only_matched", action="store_true", help="If set with --match_phrases, extract only frames that were matched to phrases")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.exists():
        raise SystemExit(f"Video not found: {video}")

    blob = read_json(args.json)
    keyframes = extract_keyframe_times(blob)

    if args.timestamps_only:
        for t in keyframes:
            print(f"{t}\t{ms_to_timecode(t)}")
        return

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    fmt = "jpg" if args.format.lower() == "jpeg" else args.format.lower()

    # Always write an index of all keyframes we know about
    csv_index = outdir / "keyframes_index.csv"
    with csv_index.open("w", encoding="utf-8") as f:
        f.write("timestamp_ms,timecode,filename\n")
        for t in keyframes:
            f.write(f"{t},{ms_to_timecode(t)},{args.prefix}.{t}.{fmt}\n")

    matched_set = set()
    if args.match_phrases:
        segments = extract_phrase_segments(blob)
        map_csv = outdir / "phrase_keyframe_map.csv"
        with map_csv.open("w", encoding="utf-8") as f:
            f.write("phrase_idx,phrase_text,start_ms,start_tc,end_ms,end_tc,anchor_ms,anchor_tc,matched_keyframe_ms,matched_keyframe_tc,matched_filename\n")
            for idx, seg in enumerate(segments, start=1):
                start_ms = int(seg["startTimeMs"])
                end_ms = int(seg["endTimeMs"])
                # Use midpoint of the phrase as the anchor
                anchor = (start_ms + end_ms) // 2
                matched = nearest_keyframe(anchor, keyframes)
                matched_set.add(matched)
                phrase_text = seg["text"].replace('"', "'")
                line = (
                    f'{idx},"{phrase_text}",'
                    f"{start_ms},{ms_to_timecode(start_ms)},"
                    f"{end_ms},{ms_to_timecode(end_ms)},"
                    f"{anchor},{ms_to_timecode(anchor)},"
                    f"{matched},{ms_to_timecode(matched)},"
                    f"{args.prefix}.{matched}.{fmt}\n"
                )
                f.write(line)
        print(f"Wrote phrase->keyframe map: {map_csv}")

    # Decide which frames to actually extract
    to_extract: List[int] = []
    if args.match_phrases and args.only_matched:
        # Extract only the matched keyframes (unique)
        to_extract = sorted(matched_set)
    else:
        to_extract = keyframes

    # Extract frames
    for t in to_extract:
        out_path = outdir / f"{args.prefix}.{t}.{fmt}"
        run_ffmpeg_extract(video, t, out_path, args.quality, args.scale_width, fmt, args.dry_run)

    print(f"Saved {len(to_extract)} frames to {outdir}")
    print(f"All keyframes index: {csv_index}")
    if args.match_phrases:
        print(f"Phrase map: {outdir/'phrase_keyframe_map.csv'}")

if __name__ == "__main__":
    main()
