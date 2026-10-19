"""Media type classification by file extension (SPEC §5). No Qt."""

from __future__ import annotations

from pathlib import PurePath

PHOTO_STANDARD = ("jpg", "jpeg", "png", "heic", "heif", "tif", "tiff", "webp")
VIDEO_STANDARD = ("mp4", "mov", "m4v", "avi", "mkv")
# GoPro proxy / thumbnail files: known, but off by default.
PHOTO_OPTIONAL = ("thm",)
VIDEO_OPTIONAL = ("lrv",)

PHOTO_CHOICES = PHOTO_STANDARD + PHOTO_OPTIONAL
VIDEO_CHOICES = VIDEO_STANDARD + VIDEO_OPTIONAL
DEFAULT_FILTER = PHOTO_STANDARD + VIDEO_STANDARD

# Extensions the app can actually decode (others are listed but "cannot display").
DISPLAYABLE_PHOTO = frozenset(PHOTO_CHOICES)
DISPLAYABLE_VIDEO = frozenset(VIDEO_CHOICES)

PHOTO = "photo"
VIDEO = "video"
OTHER = "other"


def ext_of(path: str | PurePath) -> str:
    return PurePath(path).suffix.lower().lstrip(".")


def kind_of(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    if ext in DISPLAYABLE_PHOTO:
        return PHOTO
    if ext in DISPLAYABLE_VIDEO:
        return VIDEO
    return OTHER


def is_displayable(ext: str) -> bool:
    return kind_of(ext) != OTHER


def parse_filter_text(text: str) -> list[str]:
    """``"jpg, .JPEG ,mp4"`` → ``["jpg", "jpeg", "mp4"]`` (deduplicated, order kept)."""
    result: list[str] = []
    for part in text.replace(";", ",").replace(" ", ",").split(","):
        ext = part.strip().lower().lstrip(".").lstrip("*").lstrip(".")
        if ext and ext.isascii() and all(ch.isalnum() or ch in "-_" for ch in ext) and ext not in result:
            result.append(ext)
    return result


def format_filter_text(exts: list[str]) -> str:
    return ", ".join(exts)


def normalize_filter(exts) -> list[str]:
    if not isinstance(exts, (list, tuple)):
        return list(DEFAULT_FILTER)
    return parse_filter_text(",".join(str(e) for e in exts))
