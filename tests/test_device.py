"""PLAN 10.9: Walk.device from the first clip's container tags (no ffprobe here: tag dicts only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from punchwalk.core.enums import Device
from punchwalk.pipeline import media, prepare

GLASSES = {  # footage/walk1_d.MOV as the Meta AI app writes it (ffprobe format tags)
    "major_brand": "qt  ",
    "com.apple.quicktime.copyright": "Meta AI",
    "com.apple.quicktime.comment": "app=Meta AI&device=Ray-Ban Meta Smart Glasses 2&id=DE458F6F",
    "com.apple.quicktime.model": "Ray-Ban Meta Smart Glasses 2",
}


@pytest.mark.parametrize(
    ("tags", "device"),
    [
        (GLASSES, Device.glasses),
        (
            {"com.apple.quicktime.comment": "app=Meta AI&device=Ray-Ban Meta Smart Glasses&id=x"},
            Device.glasses,
        ),
        ({"COM.APPLE.QUICKTIME.MODEL": "ray-ban meta"}, Device.glasses),  # case doesn't matter
        ({"com.apple.quicktime.make": "Apple", "com.apple.quicktime.model": "iPhone 15 Pro"}, Device.phone),
        ({"com.apple.quicktime.model": "iPad Pro (11-inch)"}, Device.phone),
        ({"com.android.manufacturer": "Google", "com.android.model": "Pixel 9"}, Device.unknown),
        ({"encoder": "Lavf61.7.100"}, Device.unknown),  # made with ffmpeg: no device
        ({}, Device.unknown),
        (None, Device.unknown),
    ],
)
def test_device_from_tags(tags, device) -> None:
    assert prepare.device_from_tags(tags) == device


def test_clip_device_reads_format_tags_and_never_fails(monkeypatch) -> None:
    monkeypatch.setattr(media, "ffprobe_json", lambda path, extra_args=None: {"format": {"tags": GLASSES}})
    assert prepare.clip_device(Path("01_walk1_d.MOV")) == Device.glasses
    monkeypatch.setattr(media, "ffprobe_json", lambda path, extra_args=None: {"format": {}})
    assert prepare.clip_device(Path("x.mp4")) == Device.unknown

    def broken(path, extra_args=None):
        raise media.MediaError("ffprobe could not read x.mp4")

    monkeypatch.setattr(media, "ffprobe_json", broken)
    assert prepare.clip_device(Path("x.mp4")) == Device.unknown
