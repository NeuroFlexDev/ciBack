"""Validate bounded local MP4/WebM uploads; never fetch or transcode external media."""

import json
import math
import subprocess
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.core.config import settings
from app.models.materials import CourseImage
from app.services.file_storage import LocalFileStorage, UploadTooLargeError
from app.services.material_quota import check_storage


def filename(value):
    return "".join(
        c
        for c in Path((value or "video").replace("\\", "/")).name
        if ord(c) >= 32 and ord(c) != 127
    )[:255]


def probe(path):
    # A container signature check prevents ffprobe from treating a disguised
    # playlist as instructions to read other local or network resources.
    with path.open("rb") as source:
        signature = source.read(4096)
    mp4 = len(signature) >= 12 and signature[4:8] == b"ftyp"
    webm = signature.startswith(b"\x1a\x45\xdf\xa3") and b"webm" in signature
    if not (mp4 or webm):
        raise HTTPException(415, "Загрузите видео MP4 (H.264) или WebM (VP8/VP9).")
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                "mov" if mp4 else "matroska,webm",
                "-probesize",
                "5000000",
                "-analyzeduration",
                "5000000",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height,duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            timeout=20,
            check=True,
        )
        data = json.loads(result.stdout)
        videos = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
        audio = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
        if len(videos) != 1 or len(audio) > 1:
            raise ValueError
        video = videos[0]
        if video.get("codec_name") not in ({"h264"} if mp4 else {"vp8", "vp9"}):
            raise HTTPException(
                415,
                "Кодек видео не поддерживается. Сохраните файл как MP4 с H.264 или WebM с VP8/VP9.",
            )
        if audio and audio[0].get("codec_name") not in (
            {"aac", "mp3"} if mp4 else {"opus", "vorbis"}
        ):
            raise HTTPException(
                415,
                "Кодек звука не поддерживается. Для MP4 используйте AAC, для WebM — Opus или Vorbis.",
            )
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
        duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
        if width <= 0 or height <= 0 or not math.isfinite(duration) or duration <= 0:
            raise ValueError
        if width * height > settings.VIDEO_MAX_PIXELS or duration > settings.VIDEO_MAX_SECONDS:
            raise HTTPException(413, "Видео превышает допустимую длительность или разрешение.")
        return ("video/mp4" if mp4 else "video/webm"), width, height, duration
    except FileNotFoundError:
        raise HTTPException(503, "Загрузка видео временно недоступна. Попробуйте позже.") from None
    except (subprocess.SubprocessError, ValueError, KeyError, TypeError):
        raise HTTPException(
            422, "Не удалось прочитать видео. Проверьте, что файл целый, и сохраните его заново."
        ) from None


def upload(db, user, course_id, file):
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if not size or size > settings.VIDEO_MAX_BYTES:
        raise HTTPException(413, "Видео превышает допустимый размер или файл пуст.")
    check_storage(db, user, size)
    storage = LocalFileStorage(settings.UPLOAD_DIR, settings.VIDEO_MAX_BYTES)
    key = f"course-media/{user.id}/{uuid4().hex}"
    try:
        stored = storage.save(file.file, key)
        mime, width, height, duration = probe(storage._target(key))
        item = CourseImage(
            course_id=course_id,
            owner_id=user.id,
            status="pending_review",
            storage_key=key,
            mime_type=mime,
            width=width,
            height=height,
            duration_seconds=duration,
            original_filename=filename(file.filename),
            size_bytes=stored.size_bytes,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item
    except Exception as exc:
        db.rollback()
        storage.delete(key)
        if isinstance(exc, UploadTooLargeError):
            raise HTTPException(413, "Видео превышает допустимый размер.") from None
        raise
