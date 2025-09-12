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

Implementation detail:
- Frames are read with OpenCV (cv2) using CAP_PROP_POS_MSEC seeking.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any


try:
    import cv2
except Exception as e:
    print(
        "Error: OpenCV (cv2) is required to run this script without ffmpeg.\n"
        "Install with: pip install opencv-python",
        file=sys.stderr,
    )
    raise

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
                joined = " ".join(cur_words).strip()
                joined = joined.replace(" ,", ",").replace(" .", ".")
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

# --- Helpers for image saving with OpenCV ---

def map_ffmpeg_q_to_jpeg_quality(q: int) -> int:
    """
    Map ffmpeg -q:v range [1(best)..31(worst)] to OpenCV JPEG quality [0..100].
    We'll clamp and use a simple monotonic mapping that keeps 1->100, 31->10.
    """
    q = max(1, min(31, q))
    # Linear map: 1 -> 100, 31 -> 10
    # slope = (10 - 100) / (31 - 1) = -90/30 = -3
    # quality = 100 + (q-1)*(-3)
    return max(0, min(100, 100 - 3 * (q - 1)))

def save_image(out_path: Path, frame, fmt: str, jpeg_q: int):
    params = []
    ext = fmt.lower()
    if ext in ("jpg", "jpeg"):
        params = [cv2.IMWRITE_JPEG_QUALITY, jpeg_q]
    elif ext == "png":
        # PNG compression 0 (none) .. 9 (max). Map JPEG quality inversely.
        # Use a gentle inverse mapping: high quality -> low compression.
        # e.g., jpeg_q 100 -> 1, 70 -> 3, 40 -> 6, <=10 -> 9
        comp = int(round(max(0, min(9, (100 - jpeg_q) * 0.09 + 1))))
        params = [cv2.IMWRITE_PNG_COMPRESSION, comp]
    ok = cv2.imwrite(str(out_path), frame, params)
    if not ok:
        raise RuntimeError(f"Failed to write image: {out_path}")

def get_frame_at_ms(cap: cv2.VideoCapture, timestamp_ms: int):
    """
    Seek to the requested ms and read a frame.
    Note: Many codecs only allow accurate seeks to the nearest keyframe. This returns the closest decodable frame.
    """
    # Attempt time-based seek
    cap.set(cv2.CAP_PROP_POS_MSEC, float(timestamp_ms))
    ok, frame = cap.read()
    if not ok or frame is None:
        # Fallback: compute frame index by fps
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        if fps > 0:
            frame_idx = int(round((timestamp_ms / 1000.0) * fps))
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
    return frame if ok and frame is not None else None

def resize_keep_aspect(frame, target_w: int | None):
    if not target_w:
        return frame
    h, w = frame.shape[:2]
    if w == target_w:
        return frame
    scale = target_w / float(w)
    target_h = max(1, int(round(h * scale)))
    return cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_CUBIC)

def extract_and_write_frame(video: Path, timestamp_ms: int, out_path: Path, quality_q: int, scale_width: int | None, fmt: str, dry_run: bool = False):
    if dry_run:
        print(f"# DRY RUN: extract frame @ {timestamp_ms} ms -> {out_path}")
        return

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video}")

    try:
        frame = get_frame_at_ms(cap, timestamp_ms)
        if frame is None:
            raise RuntimeError(f"Failed to read frame near {timestamp_ms} ms (timecode {ms_to_timecode(timestamp_ms)})")

        # OpenCV reads in BGR; for JPEG/PNG via cv2.imwrite this is fine.
        frame = resize_keep_aspect(frame, scale_width)
        jpeg_q = map_ffmpeg_q_to_jpeg_quality(quality_q)
        save_image(out_path, frame, fmt, jpeg_q)
    finally:
        cap.release()

def main():
    ap = argparse.ArgumentParser(description="Extract images from a video at keyframe timestamps provided in JSON metadata and match transcript phrase segments to keyframes.")
    ap.add_argument("--video", required=True, help="Path to the video file")
    ap.add_argument("--json", help="Path to JSON metadata file OR inline JSON string. If omitted, read from stdin.")
    ap.add_argument("--outdir", default="keyframes", help="Output directory (default: keyframes)")
    ap.add_argument("--prefix", default="keyFrame", help="Filename prefix for extracted keyframes (default: keyFrame)")
    ap.add_argument("--format", default="jpg", choices=["jpg", "jpeg", "png"], help="Image format (default: jpg)")
    ap.add_argument("--quality", type=int, default=2, help="JPG quality compatible with ffmpeg -q:v semantics (1=best, 31=worst) (default: 2)")
    ap.add_argument("--scale_width", type=int, default=None, help="Optional output width; keeps aspect ratio")
    ap.add_argument("--dry_run", action="store_true", help="Show operations without writing images")
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
    if args.match_phrases and args.only_matched:
        to_extract: List[int] = sorted(matched_set)
    else:
        to_extract = keyframes

    # Extract frames
    for t in to_extract:
        out_path = outdir / f"{args.prefix}.{t}.{fmt}"
        extract_and_write_frame(video, t, out_path, args.quality, args.scale_width, fmt, args.dry_run)

    print(f"Saved {len(to_extract)} frames to {outdir}")
    print(f"All keyframes index: {csv_index}")
    if args.match_phrases:
        print(f"Phrase map: {outdir/'phrase_keyframe_map.csv'}")

if __name__ == "__main__":
    main()
