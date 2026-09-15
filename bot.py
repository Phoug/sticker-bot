import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, Message
from dotenv import load_dotenv


load_dotenv()


API_BASE_URL = os.getenv(
    "VIDEO_API_URL",
    "http://127.0.0.1:8000",
).rstrip("/")

BOT_TOKEN = os.getenv("BOT_TOKEN")

MAX_TELEGRAM_DOWNLOAD_SIZE = 20 * 1024 * 1024
MAX_TELEGRAM_UPLOAD_SIZE = 50 * 1024 * 1024

HTTP_TIMEOUT = 300
CHUNK_SIZE = 1024 * 1024

URL_PATTERN = re.compile(
    r"https?://[^\s]+",
    re.IGNORECASE,
)


if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is not set"
    )


bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
    ),
)

dp = Dispatcher()

http_session: aiohttp.ClientSession | None = None


def extract_url(text: str) -> str | None:
    match = URL_PATTERN.search(text)

    if not match:
        return None

    url = match.group(0).rstrip(
        ".,!?;:)]}>"
    )

    parsed = urlparse(url)

    if parsed.scheme not in {
        "http",
        "https",
    }:
        return None

    if not parsed.netloc:
        return None

    return url


def parse_time(value: str) -> float:
    value = value.strip()

    try:
        if ":" not in value:
            result = float(value)

        else:
            parts = value.split(":")

            if len(parts) == 2:
                minutes = float(parts[0])
                seconds = float(parts[1])

                result = (
                    minutes * 60
                    + seconds
                )

            elif len(parts) == 3:
                hours = float(parts[0])
                minutes = float(parts[1])
                seconds = float(parts[2])

                result = (
                    hours * 3600
                    + minutes * 60
                    + seconds
                )

            else:
                raise ValueError

    except (TypeError, ValueError):
        raise ValueError from None

    if result < 0:
        raise ValueError

    return result


def format_size(size: int) -> str:
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"

    return f"{size / 1024 / 1024:.1f} MB"


def get_content_disposition_filename(
    header: str | None,
) -> str | None:
    if not header:
        return None

    match = re.search(
        r"filename\*=UTF-8''([^;]+)",
        header,
        re.IGNORECASE,
    )

    if match:
        return unquote(
            match.group(1).strip()
        )

    match = re.search(
        r'filename="([^"]+)"',
        header,
        re.IGNORECASE,
    )

    if match:
        return match.group(1)

    return None


async def get_http_session() -> aiohttp.ClientSession:
    global http_session

    if (
        http_session is None
        or http_session.closed
    ):
        timeout = aiohttp.ClientTimeout(
            total=HTTP_TIMEOUT
        )

        connector = aiohttp.TCPConnector(
            limit=20,
            ttl_dns_cache=300,
        )

        http_session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
        )

    return http_session


async def close_http_session() -> None:
    global http_session

    if http_session is not None:
        await http_session.close()
        http_session = None


async def api_json_get(
    endpoint: str,
    params: dict,
) -> dict:
    session = await get_http_session()

    async with session.get(
        f"{API_BASE_URL}{endpoint}",
        params=params,
    ) as response:
        data = await response.read()

        if response.status != 200:
            try:
                payload = json.loads(
                    data.decode(
                        "utf-8",
                        errors="replace",
                    )
                )

                detail = payload.get(
                    "detail",
                    "Unknown API error",
                )

            except Exception:
                detail = data.decode(
                    "utf-8",
                    errors="replace",
                )

            raise RuntimeError(
                str(detail)
            )

        try:
            return json.loads(
                data.decode("utf-8")
            )

        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError(
                "API returned invalid JSON"
            ) from None


async def download_url_to_file(
    url: str,
    output_dir: str,
) -> tuple[str, Path, int]:
    session = await get_http_session()

    async with session.get(
        f"{API_BASE_URL}/download",
        params={
            "url": url,
            "as_attachment": "true",
        },
    ) as response:
        if response.status != 200:
            data = await response.read()

            try:
                payload = json.loads(
                    data.decode(
                        "utf-8",
                        errors="replace",
                    )
                )

                detail = payload.get(
                    "detail",
                    "Unknown API error",
                )

            except Exception:
                detail = data.decode(
                    "utf-8",
                    errors="replace",
                )

            raise RuntimeError(
                str(detail)
            )

        filename = (
            get_content_disposition_filename(
                response.headers.get(
                    "Content-Disposition"
                )
            )
            or "video.mp4"
        )

        extension = Path(filename).suffix

        if not extension:
            filename = f"{filename}.mp4"

        safe_filename = Path(
            filename
        ).name

        output_path = Path(
            output_dir
        ) / safe_filename

        total_size = 0

        with open(
            output_path,
            "wb",
        ) as file:
            async for chunk in response.content.iter_chunked(
                CHUNK_SIZE
            ):
                total_size += len(chunk)

                if total_size > MAX_TELEGRAM_UPLOAD_SIZE:
                    raise RuntimeError(
                        "The downloaded file is larger "
                        "than Telegram's 50 MB send limit."
                    )

                file.write(chunk)

        return (
            safe_filename,
            output_path,
            total_size,
        )


async def api_upload_sticker(
    input_path: str,
    start: float,
    end: float | None,
) -> bytes:
    session = await get_http_session()

    form = aiohttp.FormData()

    with open(
        input_path,
        "rb",
    ) as file:
        form.add_field(
            "video",
            file,
            filename=Path(
                input_path
            ).name,
            content_type="application/octet-stream",
        )

        form.add_field(
            "start",
            str(start),
        )

        if end is not None:
            form.add_field(
                "end",
                str(end),
            )

        async with session.post(
            f"{API_BASE_URL}/convert/sticker",
            data=form,
        ) as response:
            data = await response.read()

            if response.status != 200:
                try:
                    payload = json.loads(
                        data.decode(
                            "utf-8",
                            errors="replace",
                        )
                    )

                    detail = payload.get(
                        "detail",
                        "Unknown API error",
                    )

                except Exception:
                    detail = data.decode(
                        "utf-8",
                        errors="replace",
                    )

                raise RuntimeError(
                    str(detail)
                )

            return data


async def get_telegram_file(
    message: Message,
) -> tuple[str, str, int | None]:
    if message.video:
        return (
            message.video.file_id,
            message.video.file_name
            or "video.mp4",
            message.video.file_size,
        )

    if message.document:
        filename = (
            message.document.file_name
            or "video.mp4"
        )

        return (
            message.document.file_id,
            filename,
            message.document.file_size,
        )

    raise ValueError(
        "Message does not contain a video"
    )


async def download_telegram_file(
    message: Message,
    output_path: str,
) -> str:
    file_id, filename, file_size = (
        await get_telegram_file(message)
    )

    if (
        file_size is not None
        and file_size > MAX_TELEGRAM_DOWNLOAD_SIZE
    ):
        raise ValueError(
            "The video is larger than Telegram's "
            "20 MB bot download limit."
        )

    telegram_file = await bot.get_file(
        file_id
    )

    if not telegram_file.file_path:
        raise RuntimeError(
            "Telegram did not return a file path"
        )

    await bot.download_file(
        telegram_file.file_path,
        destination=output_path,
    )

    return filename


async def send_downloaded_file(
    message: Message,
    path: Path,
    filename: str,
    caption: str | None = None,
) -> None:
    size = path.stat().st_size

    if size > MAX_TELEGRAM_UPLOAD_SIZE:
        raise RuntimeError(
            "The file is larger than Telegram's "
            "50 MB send limit."
        )

    await message.answer_document(
        document=FSInputFile(
            path,
            filename=filename,
        ),
        caption=caption,
    )


@dp.message(CommandStart())
async def start_handler(
    message: Message,
):
    await message.answer(
        "<b>Video Downloader & Sticker Bot</b>\n\n"
        "Send a supported video URL to download it "
        "as a file without Telegram video compression.\n\n"
        "Supported sources:\n"
        "YouTube, YouTube Shorts, TikTok, "
        "Instagram, Instagram Reels and X/Twitter.\n\n"
        "You can also upload a video and reply to it "
        "with <code>/sticker</code>."
    )


@dp.message(Command("help"))
async def help_handler(
    message: Message,
):
    await message.answer(
        "<b>Available commands</b>\n\n"
        "<code>/start</code> — show bot information\n"
        "<code>/help</code> — show available commands\n"
        "<code>/sticker</code> — convert a replied video\n"
        "<code>/sticker 5 8</code> — convert a custom segment\n\n"
        "You can also simply send a supported video URL."
    )


@dp.message(Command("sticker"))
async def sticker_command_handler(
    message: Message,
):
    source_message = message.reply_to_message

    if source_message is None:
        await message.answer(
            "Reply to a video with "
            "<code>/sticker</code>."
        )
        return

    if (
        not source_message.video
        and not source_message.document
    ):
        await message.answer(
            "The replied message must contain a video."
        )
        return

    arguments = (
        message.text or ""
    ).split()[1:]

    if len(arguments) > 2:
        await message.answer(
            "Usage: <code>/sticker 5 8</code>"
        )
        return

    start = 0.0
    end = None

    try:
        if len(arguments) >= 1:
            start = parse_time(
                arguments[0]
            )

        if len(arguments) == 2:
            end = parse_time(
                arguments[1]
            )

        if end is not None and end <= start:
            raise ValueError

    except ValueError:
        await message.answer(
            "Invalid time range.\n"
            "Example: <code>/sticker 5 8</code>"
        )
        return

    status_message = await message.answer(
        "Downloading video..."
    )

    input_path = None
    output_path = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".mp4",
            delete=False,
        ) as input_file:
            input_path = input_file.name

        await download_telegram_file(
            source_message,
            input_path,
        )

        await status_message.edit_text(
            "Converting video to Telegram sticker..."
        )

        output_data = await api_upload_sticker(
            input_path,
            start,
            end,
        )

        with tempfile.NamedTemporaryFile(
            suffix=".webm",
            delete=False,
        ) as output_file:
            output_file.write(
                output_data
            )
            output_path = output_file.name

        output_size = os.path.getsize(
            output_path
        )

        await status_message.edit_text(
            f"Sticker ready.\n"
            f"Size: {format_size(output_size)}"
        )

        await message.answer_document(
            document=FSInputFile(
                output_path,
                filename="sticker.webm",
            ),
            caption=(
                "Your Telegram video sticker "
                "is ready."
            ),
        )

        await status_message.delete()

    except Exception as exc:
        try:
            await status_message.edit_text(
                "Conversion failed:\n"
                f"<code>{str(exc)[:3500]}</code>"
            )
        except Exception:
            pass

    finally:
        if (
            input_path
            and os.path.exists(input_path)
        ):
            os.remove(input_path)

        if (
            output_path
            and os.path.exists(output_path)
        ):
            os.remove(output_path)


@dp.message(F.video)
async def video_handler(
    message: Message,
):
    file_size = (
        message.video.file_size
        or 0
    )

    if file_size > MAX_TELEGRAM_DOWNLOAD_SIZE:
        await message.answer(
            "This video is larger than "
            "Telegram's 20 MB bot download limit."
        )
        return

    await message.answer(
        "Video received.\n\n"
        "Reply to this message with "
        "<code>/sticker</code> to convert the "
        "first 3 seconds.\n\n"
        "For a custom segment:\n"
        "<code>/sticker 5 8</code>"
    )


@dp.message(F.document)
async def document_handler(
    message: Message,
):
    document = message.document

    if document is None:
        return

    filename = (
        document.file_name
        or ""
    )

    mime_type = (
        document.mime_type
        or ""
    )

    is_video = (
        mime_type.startswith("video/")
        or filename.lower().endswith(
            (
                ".mp4",
                ".mov",
                ".mkv",
                ".webm",
                ".avi",
                ".m4v",
            )
        )
    )

    if not is_video:
        await message.answer(
            "Please send a video file."
        )
        return

    file_size = (
        document.file_size
        or 0
    )

    if file_size > MAX_TELEGRAM_DOWNLOAD_SIZE:
        await message.answer(
            "This video is larger than "
            "Telegram's 20 MB bot download limit."
        )
        return

    await message.answer(
        "Video file received.\n\n"
        "Reply to this message with "
        "<code>/sticker</code> to convert it."
    )


@dp.message(F.text)
async def url_handler(
    message: Message,
):
    text = message.text or ""

    url = extract_url(text)

    if not url:
        await message.answer(
            "Send a supported video URL "
            "or upload a video."
        )
        return

    status_message = await message.answer(
        "Analyzing video..."
    )

    try:
        info = await api_json_get(
            "/info",
            {
                "url": url,
            },
        )

        title = (
            info.get("title")
            or "Video"
        )

        uploader = info.get(
            "uploader"
        )

        source = (
            info.get("extractor_key")
            or info.get("extractor")
            or "Unknown"
        )

        lines = [
            f"<b>{title}</b>",
            f"Source: {source}",
        ]

        if uploader:
            lines.append(
                f"Author: {uploader}"
            )

        lines.append(
            "\nDownloading..."
        )

        await status_message.edit_text(
            "\n".join(lines)
        )

        with tempfile.TemporaryDirectory(
            prefix="telegram_video_"
        ) as temp_dir:
            (
                filename,
                output_path,
                size,
            ) = await download_url_to_file(
                url,
                temp_dir,
            )

            await send_downloaded_file(
                message,
                output_path,
                filename,
                caption=(
                    f"<b>{title}</b>\n"
                    f"{format_size(size)}"
                ),
            )

        await status_message.delete()

    except Exception as exc:
        try:
            await status_message.edit_text(
                "Download failed:\n"
                f"<code>{str(exc)[:3500]}</code>"
            )
        except Exception:
            pass


async def main():
    global http_session

    await get_http_session()

    try:
        await dp.start_polling(
            bot,
            allowed_updates=(
                dp.resolve_used_update_types()
            ),
        )
    finally:
        await close_http_session()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())