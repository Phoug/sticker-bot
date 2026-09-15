from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


class ExtractError(Exception):
    pass


YDL_BASE_OPTIONS: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "noplaylist": True,
    "retries": 3,
    "fragment_retries": 3,
    "extractor_retries": 3,
    "socket_timeout": 30,
}


def _extract_info(url: str) -> dict:
    try:
        with YoutubeDL(YDL_BASE_OPTIONS) as ydl:
            info = ydl.extract_info(
                url,
                download=False,
            )

    except DownloadError as exc:
        raise ExtractError(str(exc)) from exc

    except Exception as exc:
        raise ExtractError(
            f"Unexpected error: {exc}"
        ) from exc

    if not info:
        raise ExtractError(
            "yt-dlp returned empty metadata"
        )

    if info.get("_type") == "playlist":
        entries = [
            entry
            for entry in (info.get("entries") or [])
            if entry
        ]

        if not entries:
            raise ExtractError(
                "Playlist is empty"
            )

        info = entries[0]

    return info


def _normalize_format(fmt: dict) -> dict:
    video_codec = fmt.get("vcodec")
    audio_codec = fmt.get("acodec")

    return {
        "format_id": fmt.get("format_id"),
        "ext": fmt.get("ext"),
        "resolution": fmt.get("resolution"),
        "width": fmt.get("width"),
        "height": fmt.get("height"),
        "fps": fmt.get("fps"),
        "vcodec": video_codec,
        "acodec": audio_codec,
        "filesize": fmt.get("filesize"),
        "filesize_approx": fmt.get("filesize_approx"),
        "tbr": fmt.get("tbr"),
        "abr": fmt.get("abr"),
        "vbr": fmt.get("vbr"),
        "protocol": fmt.get("protocol"),
        "url": fmt.get("url"),
        "has_audio": audio_codec not in (None, "none"),
        "has_video": video_codec not in (None, "none"),
    }


def _format_score(fmt: dict) -> tuple:
    height = fmt.get("height") or 0
    fps = fmt.get("fps") or 0
    tbr = fmt.get("tbr") or 0

    has_audio = (
        fmt.get("acodec") not in (None, "none")
    )
    has_video = (
        fmt.get("vcodec") not in (None, "none")
    )
    is_mp4 = fmt.get("ext") == "mp4"

    return (
        int(has_video),
        int(has_audio),
        int(is_mp4),
        height,
        fps,
        tbr,
    )


def _extract_formats(
    info: dict,
    only_with_video: bool = True,
) -> list[dict]:
    formats: list[dict] = []

    for fmt in info.get("formats") or []:
        if not fmt.get("format_id"):
            continue

        if not fmt.get("url"):
            continue

        has_video = fmt.get("vcodec") not in (
            None,
            "none",
        )

        if only_with_video and not has_video:
            continue

        formats.append(
            _normalize_format(fmt)
        )

    formats.sort(
        key=_format_score,
        reverse=True,
    )

    return formats


def get_video_info(
    url: str,
    only_with_video: bool = True,
) -> dict:
    info = _extract_info(url)

    formats = _extract_formats(
        info,
        only_with_video=only_with_video,
    )

    return {
        "source_url": url,
        "id": info.get("id"),
        "title": info.get("title"),
        "description": info.get("description"),
        "uploader": info.get("uploader"),
        "uploader_id": info.get("uploader_id"),
        "uploader_url": info.get("uploader_url"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "thumbnails": info.get("thumbnails") or [],
        "webpage_url": info.get("webpage_url"),
        "extractor": info.get("extractor"),
        "extractor_key": info.get("extractor_key"),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "repost_count": info.get("repost_count"),
        "timestamp": info.get("timestamp"),
        "upload_date": info.get("upload_date"),
        "formats": formats,
    }