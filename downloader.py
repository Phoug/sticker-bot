import os
import re
import tempfile
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


class DownloadFailed(Exception):
    pass


EXTRACTOR_NAMES = {
    "TikTok": "tiktok",
    "TikTokIE": "tiktok",
    "YouTube": "youtube",
    "Youtube": "youtube",
    "YoutubeIE": "youtube",
    "Twitter": "x",
    "TwitterIE": "x",
    "Instagram": "instagram",
    "InstagramIE": "instagram",
    "InstagramIOS": "instagram",
    "Vimeo": "vimeo",
    "Facebook": "facebook",
    "Reddit": "reddit",
    "Twitch": "twitch",
}


BASE_YDL_OPTIONS: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "retries": 3,
    "fragment_retries": 3,
    "file_access_retries": 3,
    "extractor_retries": 3,
    "socket_timeout": 30,
    "continuedl": True,
    "concurrent_fragment_downloads": 8,
    "http_chunk_size": 10 * 1024 * 1024,
    "restrictfilenames": False,
    "windowsfilenames": True,
}


def _source_name(info: dict) -> str:
    key = info.get("extractor_key") or info.get("extractor") or ""

    if key in EXTRACTOR_NAMES:
        return EXTRACTOR_NAMES[key]

    key_lower = str(key).lower()

    aliases = {
        "tiktok": "tiktok",
        "youtube": "youtube",
        "twitter": "x",
        "instagram": "instagram",
        "vimeo": "vimeo",
        "facebook": "facebook",
        "reddit": "reddit",
        "twitch": "twitch",
    }

    for source, name in aliases.items():
        if source in key_lower:
            return name

    return key_lower or "video"


def _sanitize_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)
    name = re.sub(r"\s+", " ", name)
    name = name.rstrip(". ")

    return name[:180] or "video"


def _ydl_opts(
    format_selector: str,
    output_template: str,
) -> dict[str, Any]:
    return {
        **BASE_YDL_OPTIONS,
        "format": format_selector,
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "postprocessors": [
            {
                "key": "FFmpegVideoRemuxer",
                "preferedformat": "mp4",
            }
        ],
    }


def _find_output_file(tmpdir: str) -> str:
    files = [
        os.path.join(tmpdir, filename)
        for filename in os.listdir(tmpdir)
        if os.path.isfile(os.path.join(tmpdir, filename))
    ]

    if not files:
        raise DownloadFailed("yt-dlp did not create an output file")

    mp4_files = [
        path
        for path in files
        if path.lower().endswith(".mp4")
    ]

    if mp4_files:
        return max(mp4_files, key=os.path.getsize)

    return max(files, key=os.path.getsize)


def _get_selected_format(url: str, format_id: str) -> dict:
    try:
        options = {
            **BASE_YDL_OPTIONS,
            "skip_download": True,
        }

        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)

    except DownloadError as exc:
        raise DownloadFailed(str(exc)) from exc

    except Exception as exc:
        raise DownloadFailed(f"Unexpected error: {exc}") from exc

    if not info:
        raise DownloadFailed("yt-dlp returned empty metadata")

    formats = info.get("formats") or []

    for fmt in formats:
        if str(fmt.get("format_id")) == str(format_id):
            return fmt

    raise DownloadFailed(f"Format '{format_id}' was not found")


def _build_format_selector(
    url: str,
    format_id: str | None,
) -> str:
    if not format_id:
        return "bv*+ba/b"

    selected_format = _get_selected_format(url, format_id)

    has_audio = selected_format.get("acodec") not in (None, "none")

    if has_audio:
        return format_id

    return f"{format_id}+ba/{format_id}/b"


def download_to_memory(
    url: str,
    format_id: str | None = None,
    name: str | None = None,
) -> tuple[bytes, str, str]:
    with tempfile.TemporaryDirectory(prefix="video_dl_") as tmpdir:
        output_template = os.path.join(
            tmpdir,
            "video.%(ext)s",
        )

        format_selector = _build_format_selector(
            url,
            format_id,
        )

        try:
            options = _ydl_opts(
                format_selector,
                output_template,
            )

            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(
                    url,
                    download=True,
                )

        except DownloadError as exc:
            raise DownloadFailed(str(exc)) from exc

        except Exception as exc:
            raise DownloadFailed(
                f"Unexpected error: {exc}"
            ) from exc

        if not info:
            raise DownloadFailed(
                "yt-dlp returned empty metadata"
            )

        output_path = _find_output_file(tmpdir)

        extension = os.path.splitext(output_path)[1].lstrip(".").lower()

        if name:
            base_name = _sanitize_filename(name)
        else:
            base_name = _source_name(info)

        filename = f"{base_name}.{extension}"

        try:
            with open(output_path, "rb") as file:
                data = file.read()

        except OSError as exc:
            raise DownloadFailed(
                f"Failed to read downloaded file: {exc}"
            ) from exc

    return data, filename, _guess_mime(filename)


def _guess_mime(filename: str) -> str:
    extension = os.path.splitext(filename)[1].lower()

    mime_types = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".opus": "audio/opus",
        ".ogg": "audio/ogg",
    }

    return mime_types.get(
        extension,
        "application/octet-stream",
    )