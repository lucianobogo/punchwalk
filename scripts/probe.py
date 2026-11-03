"""`make probe`: one live call per API, latency + cost printed, outputs saved to fixtures/probe/.

Calls (each failure is logged to fixtures/probe/ERRORS.md and the probe keeps going):
  1. Files upload of a 3 s clip
  2. SAM 3.1 concept on one frame, 3 tape phrasings (best -> fixtures/probe/tape_concept.json)
  3. Muse Voice Transcribe on 5 s of audio
  4. Muse Spark Open (Baseten) with the frame + a small json_schema
  5. SAM 3.1 concept on the 3 s video (captures the video stream format)
  6. Muse Spark 1.3 (Meta fallback) with the same schema
Then renders fixtures/probe/mask_check.png (SAM mask + box over the frame).

Media: footage/walk1* when present, else a synthetic portrait clip (720x1280) with a strip of
blue painter's tape on a beige wall and `say` narration.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from punchwalk.clients import asr, calls, cost, llm, meta_files, sam  # noqa: E402
from punchwalk.clients.base import ApiError  # noqa: E402
from punchwalk.config import get_settings, reset_settings  # noqa: E402
from punchwalk.render import masks  # noqa: E402

PROBE_DIR = ROOT / "fixtures" / "probe"
MEDIA = PROBE_DIR / "media"  # probe inputs (generated once, then reused)
VERIFY_DIR = ROOT / ".verify" / "probe"
# Outputs go to fixtures/probe only when the run may make live calls (readwrite); readonly/off
# replays write to .verify/probe so they never overwrite the recorded live results.
OUT = PROBE_DIR
PHRASINGS = ["blue painter's tape", "blue tape", "strip of blue painter's tape"]
NARRATION = "Kitchen. Upper cabinet left of the sink, knob missing."
ERRORS: list[str] = []
SUMMARY: dict = {"started_at": datetime.now(UTC).isoformat(timespec="seconds"), "calls": {}}


# --------------------------------------------------------------------------- media


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def synth_frame(path: Path, w: int = 760, h: int = 1320) -> None:
    """Beige wall with texture, a cabinet edge, and a slightly rotated strip of blue tape."""
    rng = np.random.default_rng(7)
    base = np.zeros((h, w, 3), dtype=np.float32)
    base[:] = (222, 205, 176)
    yy = np.linspace(0, 1, h)[:, None, None]
    base *= 0.92 + 0.10 * (1 - yy)  # soft vertical light falloff
    base += rng.normal(0, 3.0, base.shape)
    img = Image.fromarray(base.clip(0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))
    d = ImageDraw.Draw(img)
    # cabinet door (white) in the lower-left with a knob hole
    d.rectangle([40, 760, 330, 1240], fill=(238, 236, 230), outline=(190, 186, 178), width=4)
    d.ellipse([290, 980, 306, 996], fill=(120, 110, 95))
    # tape: ~130x40, rotated -8 degrees, near center-right of the cabinet
    tape = Image.new("RGBA", (130, 40), (90, 162, 234, 255))
    td = ImageDraw.Draw(tape)
    for x in range(0, 130, 6):  # torn ends
        td.point((0, x % 40), fill=(0, 0, 0, 0))
        td.point((129, (x * 3) % 40), fill=(0, 0, 0, 0))
    tape = tape.rotate(8, expand=True, resample=Image.BICUBIC)
    shadow = Image.new("RGBA", tape.size, (0, 0, 0, 0))
    shadow.paste((60, 50, 40, 70), mask=tape.split()[3])
    cx, cy = 380, 940
    img.paste(shadow.filter(ImageFilter.GaussianBlur(2)), (cx + 3, cy + 4), shadow)
    img.paste(tape, (cx, cy), tape)
    img.save(path)


def make_synthetic(clip: Path, frame: Path, wav: Path) -> dict:
    MEDIA.mkdir(parents=True, exist_ok=True)
    big = MEDIA / "synthetic_scene.png"
    synth_frame(big)
    # narration (the default macOS voice may be an undownloaded Siri voice, so name one)
    raw_wav = MEDIA / "narration_raw.wav"
    voice = "sine"
    if shutil.which("say"):
        for v in ("Samantha", "Alex", "Daniel", "Fred", "Albert"):
            subprocess.run(["say", "-v", v, "--file-format=WAVE", "--data-format=LEI16@16000", "-o", str(raw_wav),
                            NARRATION], capture_output=True)
            if raw_wav.exists() and raw_wav.stat().st_size > 16000:  # > 0.5 s of 16 kHz s16le
                voice = f"say -v {v}"
                break
    if voice == "sine":
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(raw_wav)])
    # 3 s portrait clip with a slow pan (so frames differ), audio = first 3 s of narration
    run([
        "ffmpeg", "-y", "-loop", "1", "-framerate", "30", "-i", str(big), "-i", str(raw_wav),
        "-t", "3", "-vf", "crop=720:1280:x='10+10*t':y='10+8*t',format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-af", "apad", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(clip),
    ])
    run(["ffmpeg", "-y", "-ss", "1.0", "-i", str(clip), "-frames:v", "1", "-q:v", "2", str(frame)])
    big.unlink(missing_ok=True)
    # 5 s WAV: narration padded with silence
    run(["ffmpeg", "-y", "-i", str(raw_wav), "-af", "apad=whole_dur=5", "-t", "5", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", "-map_metadata", "-1", str(wav)])
    raw_wav.unlink(missing_ok=True)
    return {"source": "synthetic", "voice": voice, "narration": NARRATION}


def make_from_footage(src: Path, clip: Path, frame: Path, wav: Path) -> dict:
    MEDIA.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-y", "-i", str(src), "-t", "3", "-vf", "scale='if(gt(iw,ih),-2,720)':'if(gt(iw,ih),720,-2)'",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-movflags", "+faststart", str(clip),
    ])
    # sharpest of ~20 frames in the first 10 s
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    best, best_score = None, -1.0
    for i in range(int(fps * 10)):
        ok, img = cap.read()
        if not ok:
            break
        if i % max(1, int(fps / 2)):
            continue
        score = cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
        if score > best_score:
            best, best_score = img, score
    cap.release()
    if best is None:
        raise RuntimeError(f"could not read frames from {src}")
    h, w = best.shape[:2]
    scale = 1600 / max(h, w)
    if scale < 1:
        best = cv2.resize(best, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(frame), best, [cv2.IMWRITE_JPEG_QUALITY, 92])
    run(["ffmpeg", "-y", "-ss", "2", "-t", "5", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", "-map_metadata", "-1", str(wav)])
    return {"source": str(src.relative_to(ROOT)), "sharpness": round(best_score, 1)}


# --------------------------------------------------------------------------- helpers


def save(name: str, data) -> Path:
    p = OUT / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_text(json.dumps(data, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    return p


def log_error(label: str, err: BaseException) -> None:
    body = [f"## {label}", "", f"- time: {datetime.now(UTC).isoformat(timespec='seconds')}"]
    if isinstance(err, ApiError):
        body += [f"- provider: {err.provider}", f"- endpoint: {err.endpoint}", f"- model: {err.model}",
                 f"- status: {err.status}", "", "```", err.body, "```"]
    else:
        body += [f"- error: {type(err).__name__}: {err}", "", "```", traceback.format_exc()[-2500:], "```"]
    ERRORS.append("\n".join(body))
    print(f"  !! {label} failed: {err}"[:600])


def timed(label: str, fn):
    records: list[calls.CallRecord] = []
    sink = records.append
    calls.add_sink(sink)
    t0 = time.monotonic()
    try:
        result = fn()
        ok = True
    except Exception as err:  # noqa: BLE001 - the probe logs and keeps going
        log_error(label, err)
        result, ok = None, False
    finally:
        calls.remove_sink(sink)
    dt = time.monotonic() - t0
    spend = sum(r.est_cost_usd for r in records if not r.cached)
    cached = any(r.cached for r in records) and not any(not r.cached for r in records)
    SUMMARY["calls"][label] = {
        "ok": ok, "wall_s": round(dt, 2), "est_cost_usd": round(spend, 5), "cached": cached,
        "records": [r.model_dump(exclude={"ts"}) for r in records],
    }
    tag = "cached" if cached else "live"
    print(f"  {'ok ' if ok else 'ERR'} {label:<34} {dt * 1000:8.0f} ms  ${spend:.4f}  ({tag})")
    return result


def sam_summary(res: sam.SamResult) -> dict:
    return {
        "request": res.request_summary,
        "media_size": res.media_size,
        "complete": res.complete,
        "usage": res.usage,
        "diagnostics": res.diagnostics,
        "n_frames_with_output": len(res.frames),
        "frame_indices": sorted(res.frames)[:400],
        "object_ids": res.object_ids,
        "records": [
            {
                "frame": r.frame_index, "object_id": r.object_id, "box": [round(v, 1) for v in r.box],
                "confidence": r.confidence, "mask_confidence": r.mask_confidence, "mask_wh": r.mask_size,
                "mask_pixels": int(r.raster().sum()), "mapping_evidence": masks.infer_mask_space(r),
            }
            for r in res.all_records()[:60]
        ],
    }


class ProbeSeen(BaseModel):
    object_seen: str = Field(description="the most prominent object or mark on the wall, a short noun phrase")
    color: str | None = Field(description="its main color, or null")
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(description="one short sentence, at most 200 characters")


SPARK_MESSAGES = [
    {"role": "system", "content": "You inspect construction photos. Answer only from what the image shows."},
    {"role": "user", "content": "Is there a strip of painter's tape in this photo? Name the most prominent "
                                "object or mark on the wall and its color."},
]


# --------------------------------------------------------------------------- summary


def _call_kind(row: dict) -> str:
    ep = row.get("endpoint", "")
    if ep == "/files":
        return "files.upload"
    if ep == "/asr/transcribe":
        return "asr.transcribe"
    if ep == "/responses":
        return "sam.concept_on_video" if row.get("unit_kind") == "frames" else "sam.concept_on_image"
    if ep == "/chat/completions":
        return "llm Spark Open (Baseten)" if row.get("provider") == "baseten" else "llm Spark 1.3 (Meta)"
    return f"{row.get('provider')} {ep}"


def write_summary_from_ledger(media: dict | None = None, tape: dict | None = None) -> Path:
    """fixtures/probe/summary.json from the ledger's live probe rows (stage == "probe"), so a
    cached rerun can never replace the live latencies and costs with replay zeros."""
    rows = [r for r in cost.read_ledger() if r.get("stage") == "probe" and not r.get("cached")]
    calls_out = [
        {"call": _call_kind(r), **{k: r.get(k) for k in (
            "ts", "provider", "model", "endpoint", "latency_ms", "units", "unit_kind", "frames",
            "tokens_in", "tokens_out", "est_cost_usd", "ok", "error")}}
        for r in rows
    ]
    by_kind: dict[str, dict] = {}
    for c in calls_out:
        k = by_kind.setdefault(c["call"], {"calls": 0, "ok": 0, "est_cost_usd": 0.0, "latency_ms": []})
        k["calls"] += 1
        k["ok"] += int(bool(c["ok"]))
        k["est_cost_usd"] = round(k["est_cost_usd"] + (c["est_cost_usd"] or 0), 5)
        k["latency_ms"].append(c["latency_ms"])
    summary = {
        "source": "data/cost_ledger.jsonl rows with stage == 'probe' (live calls only, all probe runs)",
        "media": media or json.loads((MEDIA / "source.json").read_text()),
        "tape_concept": tape or json.loads((PROBE_DIR / "tape_concept.json").read_text()),
        "live_calls": len(calls_out),
        "live_cost_usd": round(sum(c["est_cost_usd"] or 0 for c in calls_out), 5),
        "by_call": by_kind,
        "calls": calls_out,
    }
    out = PROBE_DIR / "summary.json"
    out.write_text(json.dumps(summary, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- main


def main() -> int:
    reset_settings()
    s = get_settings()
    print(f"probe: cache={s.PUNCHWALK_CACHE}  spend so far ${cost.total_usd():.4f} of ${s.COST_CAP_USD:.2f}")
    global OUT
    OUT = PROBE_DIR if s.PUNCHWALK_CACHE == "readwrite" else VERIFY_DIR
    print(f"outputs -> {OUT.relative_to(ROOT)}")
    OUT.mkdir(parents=True, exist_ok=True)
    MEDIA.mkdir(parents=True, exist_ok=True)
    clip, frame, wav = MEDIA / "clip3s.mp4", MEDIA / "frame.jpg", MEDIA / "audio5s.wav"

    walk1 = sorted(p for p in (ROOT / "footage").glob("walk1*") if p.suffix.lower() in (".mp4", ".mov"))
    if not (clip.exists() and frame.exists() and wav.exists()):
        info = make_from_footage(walk1[0], clip, frame, wav) if walk1 else make_synthetic(clip, frame, wav)
        (MEDIA / "source.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    info = json.loads((MEDIA / "source.json").read_text())
    synthetic = info.get("source") == "synthetic"
    print(f"media: {info.get('source')}  clip={clip.stat().st_size // 1024} KB  frame={Image.open(frame).size}")
    SUMMARY["media"] = info

    # 1. Files upload
    file_id = timed("1 files.upload (3 s clip)", lambda: meta_files.upload(clip))
    if file_id:
        save("files_upload.json", {"file_id": file_id, "index": meta_files.file_index().get(file_id)})

    # 2. SAM concept on the frame, 3 phrasings
    comparison = []
    best: tuple[float, str, sam.SamResult] | None = None
    for phr in PHRASINGS:
        res = timed(f"2 sam.concept_on_image {phr!r}", lambda p=phr: sam.concept_on_image(frame, p))
        if res is None:
            continue
        slug = phr.replace("'", "").replace(" ", "_")
        save(f"sam_image_{slug}.txt", res.raw_text)
        save(f"sam_image_{slug}.json", sam_summary(res))
        recs = res.all_records()
        top = max((r.confidence or 0.0 for r in recs), default=0.0)
        comparison.append({
            "concept": phr, "detections": len(recs), "max_confidence": top,
            "boxes": [[round(v) for v in r.box] for r in recs], "confidences": [r.confidence for r in recs],
        })
        # prefer exactly-one-detection results, then the highest confidence
        score = top - (0.25 * max(0, len(recs) - 1))
        if recs and (best is None or score > best[0]):
            best = (score, phr, res)
    tape = {
        "best": best[1] if best else None,
        "provisional": synthetic,
        "image_input_mode": sam.IMAGE_INPUT_MODE,
        "comparison": comparison,
        "note": "synthetic frame; re-check on real footage" if synthetic else "real footage frame",
    }
    save("tape_concept.json", tape)
    SUMMARY["tape_concept"] = tape

    # 2b. a large object, to see whether big masks are downscaled (mask scale rule)
    big = timed("2b sam.concept_on_image 'white cabinet door'", lambda: sam.concept_on_image(frame, "white cabinet door"))
    if big is not None:
        save("sam_image_white_cabinet_door.txt", big.raw_text)
        save("sam_image_white_cabinet_door.json", sam_summary(big))
        if big.frames:
            masks.render_overlay(frame, big.all_records(), OUT / "mask_check_large.png", tape_color="#D93B30")

    # 3. ASR
    tr = timed("3 asr.transcribe (5 s)", lambda: asr.transcribe(wav, keywords=["cabinet", "knob", "sink", "kitchen"]))
    if tr:
        save("asr.json", tr.model_dump())
        print(f"     transcript: {tr.transcript!r}  turns={len(tr.turns)}")

    # 4. Spark Open (Baseten)
    def spark(provider: str):
        prev = s.REASONING_PROVIDER
        s.REASONING_PROVIDER = provider
        try:
            return llm.structured(ProbeSeen, SPARK_MESSAGES, images=[frame], reasoning_effort="low",
                                  max_tokens=1500, stage="probe")
        finally:
            s.REASONING_PROVIDER = prev

    out = timed("4 llm.structured Spark Open", lambda: spark("baseten"))
    if out:
        inst, rec = out
        # no CallRecord here: it differs between live and cached runs (live numbers: summary.json)
        save("spark_open.json", {"result": inst.model_dump(), "provider": rec.provider, "model": rec.model,
                                 "format_mode": llm._format_mode,
                                 "resolved_model": llm._resolved_model, "tried_models": llm.model_candidates("baseten"),
                                 "schema": llm.strict_schema(ProbeSeen)})
        print(f"     {rec.provider}: {inst.model_dump()}")

    # 5. SAM concept on the 3 s video
    concept = tape["best"] or s.SAM_TAPE_CONCEPT
    vres = timed(f"5 sam.concept_on_video {concept!r}", lambda: sam.concept_on_video(clip, concept))
    if vres:
        save("sam_video.txt", vres.raw_text)
        save("sam_video.json", sam_summary(vres))
        print(f"     frames with output: {len(vres.frames)}  ids: {vres.object_ids}  first idx: "
              f"{sorted(vres.frames)[:5]}")

    # 6. Meta fallback model
    out6 = timed("6 llm.structured Spark 1.3 (Meta)", lambda: spark("meta"))
    if out6:
        inst6, rec6 = out6
        save("spark_meta.json", {"result": inst6.model_dump(), "provider": rec6.provider, "model": rec6.model})
        print(f"     {rec6.provider}: {inst6.model_dump()}")

    # mask check
    if best:
        res = best[2]
        png = masks.render_overlay(frame, res.all_records(), OUT / "mask_check.png")
        svg = {f"{r.frame_index}:{r.object_id}": masks.mask_to_frame_svg(r, *Image.open(frame).size)
               for r in res.all_records()}
        save("mask_check_svg.json", svg)
        print(f"mask check: {png.relative_to(ROOT)}  evidence: {masks.infer_mask_space(res.all_records()[0])}")
    if vres and vres.frames:
        # overlay the first video frame with output, extracted from the clip at that index
        fi = sorted(vres.frames)[len(vres.frames) // 2]
        VERIFY_DIR.mkdir(parents=True, exist_ok=True)
        vf = VERIFY_DIR / f"clip_frame_{fi}.jpg"
        run(["ffmpeg", "-y", "-i", str(clip), "-vf", f"select=eq(n\\,{fi})", "-frames:v", "1", "-q:v", "2", str(vf)])
        masks.render_overlay(vf, vres.frames[fi], OUT / "mask_check_video.png")

    SUMMARY["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    SUMMARY["probe_cost_usd"] = round(sum(c["est_cost_usd"] for c in SUMMARY["calls"].values()), 5)
    SUMMARY["ledger_total_usd"] = cost.total_usd()
    probe_rows = [r for r in cost.read_ledger() if r.get("stage") == "probe"]
    SUMMARY["probe_live_calls_all_runs"] = len(probe_rows)
    SUMMARY["probe_live_cost_all_runs_usd"] = round(sum(r.get("est_cost_usd") or 0 for r in probe_rows), 5)
    VERIFY_DIR.mkdir(parents=True, exist_ok=True)
    (VERIFY_DIR / "last_run.json").write_text(json.dumps(SUMMARY, indent=1, default=str), encoding="utf-8")
    if OUT == PROBE_DIR:
        write_summary_from_ledger(info, tape)
    if ERRORS:
        save("ERRORS.md", "# Probe errors\n\n" + "\n\n".join(ERRORS) + "\n")
    elif (OUT / "ERRORS.md").exists():
        (OUT / "ERRORS.md").unlink()
    n_ok = sum(1 for c in SUMMARY["calls"].values() if c["ok"])
    print(f"\nprobe: {n_ok}/{len(SUMMARY['calls'])} calls ok, probe cost ${SUMMARY['probe_cost_usd']:.4f}, "
          f"probe live spend (all runs) ${SUMMARY['probe_live_cost_all_runs_usd']:.4f}, "
          f"ledger total ${SUMMARY['ledger_total_usd']:.4f}")
    return 0 if not ERRORS else 1


if __name__ == "__main__":
    if "--rebuild-summary" in sys.argv:  # no API calls: just re-derive summary.json from the ledger
        reset_settings()
        print(f"wrote {write_summary_from_ledger().relative_to(ROOT)}")
        raise SystemExit(0)
    with calls.call_context(stage="probe"):
        raise SystemExit(main())
