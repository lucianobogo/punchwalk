"""Generate small synthetic walk-like clips for tests and probes (no real footage needed).

Scene: a beige wall with a white baseboard at the bottom (so orientation mistakes are obvious), a door
casing on the left, an outlet, and a small dark scuff (the defect). A hand (drawn with Pillow: sleeve,
palm, curled fingers, extended index finger) slides in from the right edge, points at the scuff, holds
still for ~2 s and withdraws, the way an inspector points at a defect while describing it. The hand is
drawn in scene space, so it moves with the wall while the camera pans slowly. `--tape` adds the old strip
of blue painter's tape (#5AA2EA) next to the scuff; `--no-hand` leaves the hand out. Audio is macOS `say`
narration converted to 16 kHz mono, or a quiet sine tone when `say` is unavailable.

    uv run python scripts/synth_clip.py --out .verify/synth --duration 6 --portrait --parts 2 --rotate-meta 90

Options: --landscape, --no-hand, --tape, --point-at SECONDS, --hold SECONDS, --hevc, --hdr (10-bit HEVC HLG / BT.2020, like an iPhone), --rotate-meta 90|180|270
(pixels stored rotated + a display matrix, like a phone held upright), --parts N (walk_a.mp4, walk_b.mp4,
…; each part lasts --duration seconds), --no-audio, --tone.

Importable: `make_clip(...)` and `make_walk(...)` (tests use them).
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import string
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from punchwalk.pipeline import media  # noqa: E402

WALL = "0xD9CBB0"
BASEBOARD = "0xF4F1EA"
CASING = "0xC4B38E"
OUTLET = "0xEDE8DC"
OUTLET_EDGE = "0x9A9384"
TAPE = "0x5AA2EA"
SCUFF = "0x6F6457"

SAY_VOICE = "Samantha"  # the default system voice sometimes renders silence

DEFAULT_TEXTS = [
    "Kitchen. Upper cabinet left of the sink, knob missing.",
    "Hallway. Switch plate is crooked, needs to be straightened.",
    "Bedroom. Scuff marks on the wall by the door.",
    "Bathroom. Towel bar is loose.",
]


def say_available() -> bool:
    return shutil.which("say") is not None


def _narration_wav(text: str, work: Path) -> Path | None:
    """`say` → 16 kHz mono s16le WAV, cached by text. None if `say` is missing or fails."""
    if not say_available():
        return None
    cache = Path(tempfile.gettempdir()) / "punchwalk_say_cache"
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{SAY_VOICE}|{text}".encode()).hexdigest()[:16]
    wav = cache / f"{key}.wav"
    if wav.is_file() and wav.stat().st_size > 1000:
        return wav
    aiff = work / f"{key}.aiff"
    proc = subprocess.run(["say", "-v", SAY_VOICE, "-o", str(aiff), text], capture_output=True, check=False)
    if proc.returncode != 0 or not aiff.is_file() or aiff.stat().st_size < 8000:
        # the default system voice can render silence; a near-empty file means no speech
        return None
    tmp = cache / f"{key}.partial.wav"
    media.run_ffmpeg(
        ["-i", str(aiff), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-map_metadata", "-1", str(tmp)],
        label="say→wav",
    )
    tmp.replace(wav)
    return wav


SKIN = (214, 160, 122, 255)
SKIN_SHADE = (176, 122, 92, 255)
NAIL = (236, 200, 182, 255)
SLEEVE = (58, 74, 92, 255)


@dataclass
class Scene:
    """Canvas geometry (pixels of the panned canvas, before the crop to the frame)."""

    width: int
    height: int
    canvas_w: int
    canvas_h: int
    pan_x: int
    pan_y: int
    scuff: tuple[int, int, int]  # x, y, size
    tape: tuple[int, int, int, int]  # x, y, w, h
    fingertip: tuple[int, int]  # where the extended index finger ends (just right of the scuff)


def _scene(width: int, height: int, variant: int) -> Scene:
    pan_x, pan_y = int(width * 0.12), int(height * 0.04)
    cw, ch = width + 2 * pan_x, height + 2 * pan_y
    short = min(width, height)
    shift = (variant % 3 - 1) * 0.12  # parts put the defect in a slightly different spot
    tape_w, tape_h = int(short * 0.22), max(8, int(short * 0.045))
    tape_x, tape_y = int(cw * (0.42 + shift)), int(ch * (0.42 + shift / 2))
    size = max(6, int(short * 0.035))
    # The scuff sits low on the wall (canvas y ~ 0.6) so the hand stays clear of the pixels that the
    # ingest tests sample (frame edges at mid-height, the top row, the baseboard).
    sx, sy = int(cw * (0.40 + shift)), int(ch * 0.60)
    return Scene(
        width=width, height=height, canvas_w=cw, canvas_h=ch, pan_x=pan_x, pan_y=pan_y,
        scuff=(sx, sy, size), tape=(tape_x, tape_y, tape_w, tape_h),
        fingertip=(sx + size + max(4, size // 2), sy + size // 2),
    )


def hand_png(path: Path, scene: Scene) -> tuple[int, int]:
    """Draw a right hand pointing left (RGBA PNG). Returns the fingertip's (x, y) inside the PNG."""
    from PIL import Image, ImageDraw

    short = min(scene.width, scene.height)
    finger_len, finger_th = int(short * 0.13), max(6, int(short * 0.028))
    palm_w, palm_h = int(short * 0.13), int(short * 0.11)
    arm_th = int(short * 0.09)
    # long enough to reach past the canvas edge from the fingertip
    total_w = scene.canvas_w - scene.fingertip[0] + int(short * 0.05)
    total_h = palm_h + finger_th * 2
    img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fy = finger_th  # finger row (top part of the hand)
    palm_x = finger_len
    palm_y = fy - finger_th // 3
    # sleeve / forearm to the edge
    arm_y = palm_y + (palm_h - arm_th) // 2
    d.rounded_rectangle([palm_x + palm_w * 0.7, arm_y, total_w, arm_y + arm_th], radius=arm_th // 3, fill=SKIN)
    d.rectangle([palm_x + palm_w * 1.6, arm_y - 2, total_w, arm_y + arm_th + 2], fill=SLEEVE)
    # palm with curled fingers (three knuckle bumps under the index finger)
    d.rounded_rectangle([palm_x, palm_y, palm_x + palm_w, palm_y + palm_h], radius=palm_h // 3, fill=SKIN)
    knuckle_h = (palm_h - finger_th) // 3
    for i in range(3):
        y0 = palm_y + finger_th + i * knuckle_h
        d.rounded_rectangle([palm_x - finger_th * 0.6, y0, palm_x + palm_w * 0.45, y0 + knuckle_h - 1],
                            radius=knuckle_h // 2, fill=SKIN, outline=SKIN_SHADE, width=1)
    # thumb along the top of the palm
    d.rounded_rectangle([palm_x + palm_w * 0.25, palm_y - finger_th * 0.5, palm_x + palm_w * 0.85,
                         palm_y + finger_th * 0.6], radius=finger_th // 2, fill=SKIN, outline=SKIN_SHADE, width=1)
    # extended index finger, tip at x = 0
    d.rounded_rectangle([0, fy - finger_th // 2, palm_x + palm_w * 0.3, fy + finger_th // 2],
                        radius=finger_th // 2, fill=SKIN, outline=SKIN_SHADE, width=1)
    d.ellipse([2, fy - finger_th // 3, 2 + finger_th, fy + finger_th // 3], fill=NAIL)
    img.save(path)
    return 0, fy


def _hand_x_expr(tip_x: int, slide: int, point_at: float, hold_s: float, move_s: float = 0.4) -> str:
    """overlay x(t): slides in over `move_s`, holds still for `hold_s`, slides back out."""
    a, b = point_at + move_s, point_at + move_s + hold_s
    return f"{tip_x}+{slide}*max(0,min(1,max(({a:.3f}-t)/{move_s},(t-{b:.3f})/{move_s})))"


def _scene_graph(
    width: int, height: int, fps: int, duration_s: float, variant: int, *, tape: bool = False,
    hand: tuple[int, int] | None = None, point_at: float = 0.3, hold_s: float = 2.0,
) -> str:
    """filter_complex: wall canvas larger than the frame, features drawn on it, the hand overlaid
    (input 0, when `hand` = its fingertip inside the PNG), then a panning crop. Output label [scene]."""
    sc = _scene(width, height, variant)
    cw, ch = sc.canvas_w, sc.canvas_h
    base_h = int(ch * 0.12)
    outlet_w, outlet_h = int(cw * 0.06), int(cw * 0.09)
    sx, sy, size = sc.scuff
    boxes = [
        f"drawbox=x=0:y={ch - base_h}:w={cw}:h={base_h}:color={BASEBOARD}:t=fill",
        f"drawbox=x={int(cw * 0.06)}:y=0:w={int(cw * 0.05)}:h={ch - base_h}:color={CASING}:t=fill",
        f"drawbox=x={int(cw * 0.72)}:y={int(ch * 0.68)}:w={outlet_w}:h={outlet_h}:color={OUTLET}:t=fill",
        f"drawbox=x={int(cw * 0.72)}:y={int(ch * 0.68)}:w={outlet_w}:h={outlet_h}:color={OUTLET_EDGE}:t=3",
        f"drawbox=x={sx}:y={sy}:w={size}:h={size}:color={SCUFF}:t=fill",
    ]
    if tape:
        tx, ty, tw, th = sc.tape
        boxes.append(f"drawbox=x={tx}:y={ty}:w={tw}:h={th}:color={TAPE}:t=fill")
    period = 5.0 + variant
    crop = (
        f"crop=w={width}:h={height}"
        f":x='{sc.pan_x}+{sc.pan_x * 0.9:.1f}*sin(2*PI*t/{period:.2f})'"
        f":y='{sc.pan_y}+{sc.pan_y * 0.8:.1f}*sin(2*PI*t/{period * 0.7:.2f})'"
    )
    graph = f"color=c={WALL}:s={cw}x{ch}:r={fps}:d={duration_s:.3f}," + ",".join(boxes)
    if hand is None:
        return f"{graph},{crop}[scene]"
    hx, hy = hand
    tip_x, tip_y = sc.fingertip
    hold = max(0.4, min(hold_s, duration_s - point_at - 0.9))
    x_expr = _hand_x_expr(tip_x - hx, cw - tip_x + 4, point_at, hold)
    return (
        f"{graph}[wall];[0:v]format=rgba[hand];"
        f"[wall][hand]overlay=x='{x_expr}':y={tip_y - hy}:eval=frame:shortest=1,{crop}[scene]"
    )


def _rotation_transform(rotate_meta: int) -> str | None:
    """Filter that stores the pixels so a player rotating `rotate_meta`° clockwise shows them upright."""
    return {0: None, 90: "transpose=cclock", 180: "hflip,vflip", 270: "transpose=clock"}[rotate_meta]


def make_clip(
    out_path: Path,
    duration_s: float = 4.0,
    *,
    portrait: bool = True,
    rotate_meta: int = 0,
    hevc: bool = False,
    hdr: bool = False,
    fps: int = 30,
    size: tuple[int, int] | None = None,
    audio: str = "auto",
    text: str | None = None,
    variant: int = 0,
    hand: bool = True,
    tape: bool = False,
    point_at: float = 0.3,
    hold_s: float = 2.0,
) -> Path:
    """Write one synthetic clip to `out_path` and return it.

    size: upright (display) size; default 720x1280 portrait / 1280x720 landscape.
    rotate_meta: clockwise rotation a player must apply (iPhone portrait = 90).
    hdr: 10-bit HEVC tagged HLG / BT.2020 (implies HEVC); pixels are converted when this ffmpeg can.
    audio: "auto" (say, else tone) | "say" | "tone" | "none".
    hand: draw the pointing hand (enters at `point_at` s, holds `hold_s` s, withdraws).
    tape: also draw the strip of blue painter's tape next to the scuff.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if rotate_meta not in (0, 90, 180, 270):
        raise ValueError("rotate_meta must be 0, 90, 180 or 270")
    if audio not in ("auto", "say", "tone", "none"):
        raise ValueError("audio must be auto, say, tone or none")
    width, height = size or ((720, 1280) if portrait else (1280, 720))
    hevc = hevc or hdr

    with tempfile.TemporaryDirectory(prefix="synth_clip_") as tmp:
        work = Path(tmp)
        vf: list[str] = []
        transform = _rotation_transform(rotate_meta)
        if transform:
            vf.append(transform)
        if hdr and media.scale_has_color_management():
            vf.append(
                "scale=in_transfer=bt709:in_primaries=bt709:in_color_matrix=bt709"
                ":out_transfer=arib-std-b67:out_primaries=bt2020:out_color_matrix=bt2020nc"
            )
        vf.append("format=yuv420p10le" if hdr else "format=yuv420p")

        args: list[str] = []
        hand_xy = None
        if hand:
            png = work / "hand.png"
            hand_xy = hand_png(png, _scene(width, height, variant))
            args += ["-loop", "1", "-framerate", str(fps), "-i", str(png)]
        graph = _scene_graph(width, height, fps, duration_s, variant, tape=tape, hand=hand_xy,
                             point_at=point_at, hold_s=hold_s)
        audio_index = 1 if hand else 0
        wav = None
        if audio in ("auto", "say"):
            wav = _narration_wav(text or DEFAULT_TEXTS[variant % len(DEFAULT_TEXTS)], work)
            if wav is None and audio == "say":
                raise RuntimeError("macOS `say` is not available")
        if wav is not None:
            args += ["-i", str(wav)]
            af = "adelay=400:all=1,apad"
        elif audio != "none":
            args += ["-f", "lavfi", "-i", f"sine=f=440:sample_rate=16000:d={duration_s:.3f}"]
            af = "volume=0.15,apad"
        args += ["-filter_complex", f"{graph};[scene]{','.join(vf)}[v]", "-map", "[v]"]
        if audio != "none":
            args += ["-map", f"{audio_index}:a:0", "-af", af, "-c:a", "aac", "-b:a", "48k"]
        if hevc:
            x265 = "log-level=error"
            if hdr:
                x265 += ":colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc"
            args += [
                "-c:v",
                "libx265",
                "-preset",
                "fast",
                "-crf",
                "30",
                "-tag:v",
                "hvc1",
                "-x265-params",
                x265,
            ]
        else:
            args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"]
        if hdr:
            args += ["-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc"]
        args += ["-t", f"{duration_s:.3f}", "-movflags", "+faststart"]

        encoded = work / f"encoded{out_path.suffix or '.mp4'}"
        media.run_ffmpeg([*args, str(encoded)], label=f"synth {out_path.name}")
        if rotate_meta:
            # -display_rotation is counterclockwise; a player then rotates clockwise by rotate_meta.
            media.run_ffmpeg(
                ["-display_rotation", str(-rotate_meta), "-i", str(encoded), "-c", "copy", str(out_path)],
                label=f"rotate-meta {rotate_meta}",
            )
        else:
            shutil.move(str(encoded), out_path)
    return out_path


def make_walk(
    out_dir: Path, duration_s: float = 4.0, *, parts: int = 1, name: str = "walk", **kwargs
) -> list[Path]:
    """`parts` == 1 → [out_dir/<name>.mp4]; otherwise <name>_a.mp4, <name>_b.mp4, … (each `duration_s`)."""
    out_dir = Path(out_dir)
    if parts < 1 or parts > 26:
        raise ValueError("parts must be between 1 and 26")
    if parts == 1:
        return [make_clip(out_dir / f"{name}.mp4", duration_s, **kwargs)]
    return [
        make_clip(out_dir / f"{name}_{string.ascii_lowercase[i]}.mp4", duration_s, variant=i, **kwargs)
        for i in range(parts)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic walk-like clips.")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--duration", type=float, default=4.0, help="seconds per clip (default 4)")
    orient = parser.add_mutually_exclusive_group()
    orient.add_argument("--portrait", action="store_true", help="portrait (default)")
    orient.add_argument("--landscape", action="store_true")
    parser.add_argument("--rotate-meta", type=int, default=0, choices=[0, 90, 180, 270])
    parser.add_argument("--hevc", action="store_true")
    parser.add_argument("--hdr", action="store_true", help="10-bit HEVC HLG / BT.2020 (implies --hevc)")
    parser.add_argument("--parts", type=int, default=1)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--size", type=str, default=None, help="upright size WxH, e.g. 720x1280")
    parser.add_argument("--name", default="walk", help="file name stem (default walk)")
    parser.add_argument("--no-hand", action="store_true", help="leave out the pointing hand")
    parser.add_argument("--tape", action="store_true", help="also draw a strip of blue painter's tape")
    parser.add_argument("--point-at", type=float, default=0.3, help="when the hand starts entering (s)")
    parser.add_argument("--hold", type=float, default=2.0, help="how long the hand holds still (s)")
    audio = parser.add_mutually_exclusive_group()
    audio.add_argument("--no-audio", action="store_true")
    audio.add_argument("--tone", action="store_true", help="sine tone instead of `say`")
    args = parser.parse_args(argv)

    size = None
    if args.size:
        w, _, h = args.size.lower().partition("x")
        size = (int(w), int(h))
    paths = make_walk(
        args.out,
        args.duration,
        parts=args.parts,
        name=args.name,
        portrait=not args.landscape,
        rotate_meta=args.rotate_meta,
        hevc=args.hevc,
        hdr=args.hdr,
        fps=args.fps,
        size=size,
        audio="none" if args.no_audio else ("tone" if args.tone else "auto"),
        hand=not args.no_hand,
        tape=args.tape,
        point_at=args.point_at,
        hold_s=args.hold,
    )
    for p in paths:
        info = media.probe(p)
        print(
            f"{p}  {p.stat().st_size / 1024:.0f} KB  {info.codec} {info.width}x{info.height} rot={info.rotation}"
            f" → {info.display_width}x{info.display_height}  {info.duration_s:.2f}s hdr={info.hdr}"
            f" audio={info.has_audio}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
