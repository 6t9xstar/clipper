"""User-facing media errors. Every code has a plain-language message and a hint (shown in the UI)."""

from __future__ import annotations

from clipper.jobs.errors import JobError

MESSAGES: dict[str, tuple[str, str]] = {
    "MEDIA_UNREADABLE": (
        "This file could not be read as a video.",
        "It may be damaged or not a video. Try re-exporting it as MP4 (H.264 + AAC) and import again.",
    ),
    "MEDIA_UNSUPPORTED_CONTAINER": (
        "This file type is not supported.",
        "Supported: MP4, MOV, MKV, WebM, AVI, MPEG-TS, FLV. Re-export as MP4 if unsure.",
    ),
    "MEDIA_NO_VIDEO": (
        "This file has no video track.",
        "Audio-only files and images can't be clipped. Import a video file.",
    ),
    "MEDIA_UNSUPPORTED_CODEC": (
        "The video uses a codec Clipper can't process.",
        "Re-export as MP4 with H.264 video (any video editor or HandBrake can do this).",
    ),
    "MEDIA_TOO_LONG": (
        "This video is longer than the allowed maximum.",
        "Trim it, or raise CLIPPER_MAX_DURATION_SECONDS.",
    ),
    "MEDIA_TOO_SHORT": (
        "This video is too short to make clips from.",
        "Import a video at least 1 second long.",
    ),
    "MEDIA_RESOLUTION_UNSUPPORTED": (
        "The video resolution is outside the supported range.",
        "Supported: 64x64 up to 8K (7680x4320). Re-export at a standard resolution.",
    ),
    "MEDIA_TRUNCATED": (
        "This video appears to be cut off or incomplete.",
        "The download or copy may not have finished. Copy the original file again and re-import.",
    ),
    "MEDIA_CORRUPT": (
        "This video contains damaged data that can't be decoded.",
        "Re-export or re-download the original file and import again.",
    ),
    "MEDIA_PROCESSING_FAILED": (
        "Preparing this video failed.",
        "Check the server log for details. If it repeats, re-export the file as MP4 (H.264 + AAC).",
    ),
}


class MediaError(JobError):
    """Non-retryable: the input itself is the problem, retrying won't help."""

    retryable = False

    def __init__(self, code: str, detail: str | None = None) -> None:
        message, hint = MESSAGES[code]
        super().__init__(message, code=code, hint=hint)
        self.detail = detail  # technical detail for logs only (never contains file names)
