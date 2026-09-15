import os
import tempfile
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, File, UploadFile
from fastapi.responses import Response, FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field, HttpUrl

from downloader import DownloadFailed, download_to_memory
from extractor import ExtractError, get_video_info
from sticker_converter import StickerConversionError, convert_video_to_sticker, validate_sticker_file

app = FastAPI(
    title="Video Extractor API",
    version="1.0.0",
)

class FormatInfo(BaseModel):
    format_id: str | None = None
    ext: str | None = None
    resolution: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    vcodec: str | None = None
    acodec: str | None = None
    filesize: int | None = None
    filesize_approx: int | None = None
    tbr: float | None = None
    abr: float | None = None
    vbr: float | None = None
    protocol: str | None = None
    url: str | None = None
    has_audio: bool = False
    has_video: bool = False


class VideoInfo(BaseModel):
    source_url: str
    id: str | None = None
    title: str | None = None
    description: str | None = None
    uploader: str | None = None
    uploader_id: str | None = None
    uploader_url: str | None = None
    duration: float | None = None
    thumbnail: str | None = None
    thumbnails: list[dict] = Field(
        default_factory=list
    )
    webpage_url: str | None = None
    extractor: str | None = None
    extractor_key: str | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    repost_count: int | None = None
    timestamp: int | None = None
    upload_date: str | None = None
    formats: list[FormatInfo] = Field(
        default_factory=list
    )


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "service": "video-extractor",
    }


@app.get(
    "/info",
    response_model=VideoInfo,
)
def info(
    url: HttpUrl = Query(
        ...,
        description="Video URL",
    ),
    only_with_video: bool = Query(
        True,
        description="Return only formats containing video",
    ),
):
    try:
        return get_video_info(
            str(url),
            only_with_video=only_with_video,
        )

    except ExtractError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


@app.get("/download")
def download(
    url: HttpUrl = Query(
        ...,
        description="Video URL",
    ),
    format_id: str | None = Query(
        None,
        description=(
            "Format ID returned by /info. "
            "If omitted, the best available quality is selected."
        ),
    ),
    name: str | None = Query(
        None,
        description=(
            "Output filename without extension. "
            "If omitted, a source name is used."
        ),
    ),
    as_attachment: bool = Query(
        True,
        description="Return the file as an attachment",
    ),
):
    try:
        data, filename, mime_type = download_to_memory(
            str(url),
            format_id=format_id,
            name=name,
        )

    except DownloadFailed as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    headers = {
        "Content-Length": str(len(data)),
        "Cache-Control": "no-store",
    }

    if as_attachment:
        headers["Content-Disposition"] = (
            f"attachment; filename*=UTF-8''{quote(filename)}"
        )

    return Response(
        content=data,
        media_type=mime_type,
        headers=headers,
    )

@app.post("/convert/sticker")
def convert_sticker(
    video: UploadFile = File(
        ...,
        description="Source video file",
    ),
    start: float = Query(
        0.0,
        ge=0.0,
        description="Start time in seconds",
    ),
    end: float | None = Query(
        None,
        gt=0.0,
        description="End time in seconds",
    ),
):
    if not video.filename:
        raise HTTPException(
            status_code=400,
            detail="No input filename provided",
        )

    input_suffix = (
        os.path.splitext(video.filename)[1]
        or ".mp4"
    )

    input_fd, input_path = tempfile.mkstemp(
        suffix=input_suffix,
        prefix="sticker_input_",
    )
    os.close(input_fd)

    try:
        with open(input_path, "wb") as output:
            while chunk := video.file.read(1024 * 1024):
                output.write(chunk)

    except OSError as exc:
        if os.path.exists(input_path):
            os.remove(input_path)

        raise HTTPException(
            status_code=500,
            detail=f"Failed to save uploaded video: {exc}",
        ) from exc

    finally:
        video.file.close()

    try:
        output_path, duration = (
            convert_video_to_sticker(
                input_path=input_path,
                start=start,
                end=end,
            )
        )

        validate_sticker_file(output_path)

    except StickerConversionError as exc:
        if os.path.exists(input_path):
            os.remove(input_path)

        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    cleanup = BackgroundTask(
        _cleanup_files,
        input_path,
        output_path,
    )

    return FileResponse(
        path=output_path,
        media_type="video/webm",
        filename="sticker.webm",
        background=cleanup,
        headers={
            "X-Sticker-Duration": f"{duration:.3f}",
        },
    )

def _cleanup_files(*paths: str) -> None:
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass