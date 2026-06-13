import asyncio
import base64
import html
import io
import logging
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import status
from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.exceptions import AppException, NotFoundException
from app.model.response.story import StoryVideoResponse
from app.repository.story_repository import StoryRepository
from app.service.story_video_storage_service import (
    StoryVideoStorageResult,
    local_story_video_storage_service,
)

logger = logging.getLogger(__name__)


VIDEO_STATUS_NOT_STARTED = "NOT_STARTED"
VIDEO_STATUS_IN_PROGRESS = "IN_PROGRESS"
VIDEO_STATUS_COMPLETED = "COMPLETED"
VIDEO_STATUS_FAILED = "FAILED"


@dataclass(slots=True)
class VideoSlide:
    kind: str
    image_url: str
    title: str | None
    text: str | None
    page_number: int | None
    audio_url: str | None
    duration_seconds: float | None


class StoryVideoService:
    """Builds and stores language-specific custom story slideshow videos."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.stories = StoryRepository(session)
        self.video_storage = local_story_video_storage_service

    async def get_video_status(
        self,
        *,
        user_id: UUID,
        story_id: UUID,
        language: str,
    ) -> StoryVideoResponse:
        normalized_language = self._normalize_language(language)
        story = await self.stories.get_for_user(user_id, story_id)
        if story is None:
            raise NotFoundException("Story not found", "STORY_NOT_FOUND")
        return self._response_from_story(story, normalized_language)

    async def prepare_generation(
        self,
        *,
        user_id: UUID,
        story_id: UUID,
        language: str,
        overwrite: bool,
    ) -> tuple[StoryVideoResponse, bool]:
        normalized_language = self._normalize_language(language)
        story = await self.stories.get_for_user_for_update(user_id, story_id)
        if story is None:
            raise NotFoundException("Story not found", "STORY_NOT_FOUND")

        existing = self._language_metadata(story, normalized_language)
        if (
            not overwrite
            and existing.get("status") == VIDEO_STATUS_COMPLETED
            and (existing.get("video_url") or existing.get("local_video_path"))
        ):
            return self._response_from_story(story, normalized_language), False

        content = await self.stories.get_content_by_story_and_language(
            story_id=story.id,
            language=normalized_language,
        )
        if content is None:
            raise NotFoundException("Story content not found", "STORY_CONTENT_NOT_FOUND")

        self.build_slide_manifest(content.story_json, story_title=story.title)
        await self._set_language_metadata(
            story,
            normalized_language,
            {
                "status": VIDEO_STATUS_IN_PROGRESS,
                "video_url": None,
                "local_video_path": None,
                "error_message": None,
                "requested_at": self._now(),
                "started_at": None,
                "completed_at": None,
                "updated_at": self._now(),
            },
            commit=True,
        )
        return self._response_from_story(story, normalized_language), True

    async def generate_video(
        self,
        *,
        user_id: UUID,
        story_id: UUID,
        language: str,
        overwrite: bool = False,
    ) -> StoryVideoResponse:
        normalized_language = self._normalize_language(language)
        logger.info(
            "Custom story video generation started: story_id=%s user_id=%s language=%s overwrite=%s",
            story_id,
            user_id,
            normalized_language,
            overwrite,
        )
        story = await self.stories.get_for_user_for_update(user_id, story_id)
        if story is None:
            logger.warning(
                "Custom story video generation story not found: story_id=%s user_id=%s language=%s",
                story_id,
                user_id,
                normalized_language,
            )
            raise NotFoundException("Story not found", "STORY_NOT_FOUND")

        existing = self._language_metadata(story, normalized_language)
        if (
            not overwrite
            and existing.get("status") == VIDEO_STATUS_COMPLETED
            and (existing.get("video_url") or existing.get("local_video_path"))
        ):
            logger.info(
                "Custom story video generation reused existing video: story_id=%s user_id=%s language=%s video_url=%s local_video_path=%s",
                story_id,
                user_id,
                normalized_language,
                existing.get("video_url"),
                existing.get("local_video_path"),
            )
            return self._response_from_story(story, normalized_language)

        content = await self.stories.get_content_by_story_and_language(
            story_id=story.id,
            language=normalized_language,
        )
        if content is None:
            logger.warning(
                "Custom story video generation content not found: story_id=%s user_id=%s language=%s",
                story_id,
                user_id,
                normalized_language,
            )
            raise NotFoundException("Story content not found", "STORY_CONTENT_NOT_FOUND")

        started_at = self._now()
        await self._set_language_metadata(
            story,
            normalized_language,
            {
                "status": VIDEO_STATUS_IN_PROGRESS,
                "video_url": None,
                "local_video_path": None,
                "error_message": None,
                "requested_at": existing.get("requested_at") or started_at,
                "started_at": started_at,
                "completed_at": None,
                "updated_at": started_at,
            },
            commit=True,
        )

        try:
            slides = self.build_slide_manifest(content.story_json, story_title=story.title)
            logger.info(
                "Custom story video render starting: story_id=%s language=%s slide_count=%s",
                story_id,
                normalized_language,
                len(slides),
            )
            storage_result = await self._render_and_upload_video(
                story_id=story.id,
                language=normalized_language,
                slides=slides,
            )
            video_url, local_video_path = self._storage_result_values(storage_result)
            logger.info(
                "Custom story video render stored: story_id=%s language=%s video_url=%s local_video_path=%s",
                story_id,
                normalized_language,
                video_url,
                local_video_path,
            )
            story = await self.stories.get_for_user_for_update(user_id, story_id)
            if story is None:
                raise NotFoundException("Story not found", "STORY_NOT_FOUND")
            completed_at = self._now()
            await self._set_language_metadata(
                story,
                normalized_language,
                {
                    "status": VIDEO_STATUS_COMPLETED,
                    "video_url": video_url,
                    "local_video_path": local_video_path,
                    "error_message": None,
                    "requested_at": existing.get("requested_at") or started_at,
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "updated_at": completed_at,
                },
                commit=True,
            )
            logger.info(
                "Custom story video generation completed: story_id=%s user_id=%s language=%s video_url=%s local_video_path=%s",
                story_id,
                user_id,
                normalized_language,
                video_url,
                local_video_path,
            )
            return self._response_from_story(story, normalized_language)
        except Exception as exc:
            logger.exception("Custom story video generation failed: story_id=%s language=%s", story_id, normalized_language)
            story = await self.stories.get_for_user_for_update(user_id, story_id)
            if story is not None:
                failed_at = self._now()
                await self._set_language_metadata(
                    story,
                    normalized_language,
                    {
                        "status": VIDEO_STATUS_FAILED,
                        "video_url": None,
                        "local_video_path": None,
                        "error_message": str(exc),
                        "requested_at": existing.get("requested_at") or started_at,
                        "started_at": started_at,
                        "completed_at": None,
                        "updated_at": failed_at,
                    },
                    commit=True,
                )
            if isinstance(exc, AppException):
                raise
            raise AppException(
                f"Failed to generate story video: {exc}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "STORY_VIDEO_GENERATION_FAILED",
            ) from exc

    def build_slide_manifest(self, story_json: dict[str, Any], *, story_title: str | None = None) -> list[VideoSlide]:
        if not isinstance(story_json, dict):
            raise AppException("Story content must contain a story_json object", code="STORY_VIDEO_JSON_INVALID")

        title = self._clean_text(story_json.get("title") or story_title)
        cover_image_url = self._clean_text(
            story_json.get("cover_image_url")
            or story_json.get("coverImageUrl")
            or story_json.get("cover_image")
            or story_json.get("coverImage")
        )
        if not cover_image_url:
            raise AppException("Story video requires cover_image_url", code="STORY_VIDEO_COVER_IMAGE_REQUIRED")

        raw_pages = story_json.get("pages")
        if not isinstance(raw_pages, list) or not raw_pages:
            raise AppException("Story video requires at least one page", code="STORY_VIDEO_PAGES_REQUIRED")

        slides = [
            VideoSlide(
                kind="cover",
                image_url=cover_image_url,
                title=title,
                text=None,
                page_number=None,
                audio_url=None,
                duration_seconds=float(settings.STORY_VIDEO_COVER_DURATION_SECONDS),
            )
        ]

        page_items = [(index, page) for index, page in enumerate(raw_pages) if isinstance(page, dict)]
        if len(page_items) != len(raw_pages):
            raise AppException("Story video pages must be objects", code="STORY_VIDEO_PAGE_INVALID")

        pages = sorted(page_items, key=lambda item: self._page_number(item[1], item[0] + 1))
        for fallback_index, page in pages:
            page_number = self._page_number(page, fallback_index + 1)
            text = self._clean_text(page.get("text"))
            image_url = self._clean_text(page.get("image_url") or page.get("imageUrl"))
            audio_url = self._clean_text(page.get("audio_url") or page.get("audioUrl"))
            duration_seconds = self._page_duration_seconds(page)
            missing = [
                name
                for name, value in {
                    "text": text,
                    "image_url": image_url,
                    "audio_url": audio_url,
                    "duration": duration_seconds,
                }.items()
                if not value
            ]
            if missing:
                raise AppException(
                    f"Story video page {page_number} is missing: {', '.join(missing)}",
                    code="STORY_VIDEO_PAGE_ASSETS_MISSING",
                    details={"page_number": page_number, "missing": missing},
                )
            slides.append(
                VideoSlide(
                    kind="page",
                    image_url=image_url,
                    title=None,
                    text=text,
                    page_number=page_number,
                    audio_url=audio_url,
                    duration_seconds=duration_seconds,
                )
            )

        end_text = self._moral_text(story_json.get("moral"))
        slides.append(
            VideoSlide(
                kind="end",
                image_url=self._clean_text(story_json.get("back_cover_image_url")) or cover_image_url,
                title="The End",
                text=end_text,
                page_number=None,
                audio_url=None,
                duration_seconds=float(settings.STORY_VIDEO_END_DURATION_SECONDS),
            )
        )
        return slides

    async def _render_and_upload_video(
        self,
        *,
        story_id: UUID,
        language: str,
        slides: list[VideoSlide],
    ) -> StoryVideoStorageResult:
        video_bytes = await self._render_video(slides)
        version = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return await self.video_storage.save_story_video(
            story_id=story_id,
            language=language,
            video_bytes=video_bytes,
            filename=f"story_{version}.mp4",
            content_type="video/mp4",
        )

    async def _render_video(self, slides: list[VideoSlide]) -> bytes:
        if not slides:
            raise AppException("Story video requires at least one slide", code="STORY_VIDEO_SLIDES_REQUIRED")

        with tempfile.TemporaryDirectory(prefix="story-video-") as tmp:
            tmpdir = Path(tmp)
            segment_paths: list[Path] = []
            for index, slide in enumerate(slides):
                image_bytes = await self._read_asset(slide.image_url, asset_type="image")
                image_path = tmpdir / f"slide_{index:03d}.png"
                audio_path = tmpdir / f"slide_{index:03d}.wav"
                segment_path = tmpdir / f"segment_{index:03d}.mp4"

                await asyncio.to_thread(self._write_slide_image, image_path, image_bytes, slide)
                if slide.audio_url:
                    audio_path = tmpdir / f"slide_{index:03d}{self._asset_suffix(slide.audio_url, '.audio')}"
                    audio_bytes = await self._read_asset(slide.audio_url, asset_type="audio")
                    await asyncio.to_thread(audio_path.write_bytes, audio_bytes)
                else:
                    await asyncio.to_thread(
                        self._write_silent_wav,
                        audio_path,
                        slide.duration_seconds or 1.0,
                    )
                await asyncio.to_thread(
                    self._render_segment,
                    image_path,
                    audio_path,
                    segment_path,
                    slide.duration_seconds,
                )
                segment_paths.append(segment_path)

            output_path = tmpdir / "story.mp4"
            await asyncio.to_thread(self._concat_segments, tmpdir, segment_paths, output_path)
            return await asyncio.to_thread(output_path.read_bytes)

    async def _read_asset(self, url: str, *, asset_type: str) -> bytes:
        data_url = self._data_url_bytes(url)
        if data_url is not None:
            return data_url

        local_path = self._local_asset_path(url)
        if local_path is not None:
            try:
                content = await asyncio.to_thread(local_path.read_bytes)
            except OSError as exc:
                raise AppException(
                    f"Failed to read local {asset_type}: {local_path}",
                    status.HTTP_502_BAD_GATEWAY,
                    "STORY_VIDEO_LOCAL_ASSET_READ_FAILED",
                ) from exc
            self._validate_asset_size(content, asset_type)
            return content

        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise AppException(
                    f"Failed to download {asset_type} asset for story video: {url}",
                    status.HTTP_502_BAD_GATEWAY,
                    "STORY_VIDEO_ASSET_DOWNLOAD_FAILED",
                ) from exc
        self._validate_asset_size(response.content, asset_type)
        return response.content

    def _write_slide_image(self, output_path: Path, image_bytes: bytes, slide: VideoSlide) -> None:
        width = int(settings.STORY_VIDEO_WIDTH)
        height = int(settings.STORY_VIDEO_HEIGHT)
        with Image.open(io.BytesIO(image_bytes)) as image:
            image = ImageOps.exif_transpose(image.convert("RGB"))
            image = self._compose_contained_frame(image, (width, height))

        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        if slide.kind == "cover" and slide.title:
            self._draw_center_title(draw, overlay.size, slide.title)
        elif slide.kind == "end":
            self._draw_center_title(draw, overlay.size, slide.title or "The End", body=slide.text)
        elif slide.text:
            self._draw_bottom_text(draw, overlay.size, slide.text)

        composed = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        composed.save(output_path, format="PNG")

    @staticmethod
    def _compose_contained_frame(image: Image.Image, size: tuple[int, int]) -> Image.Image:
        width, height = size
        background = Image.new("RGB", size, StoryVideoService._image_padding_color(image))
        image_ratio = image.width / image.height
        target_ratio = width / height
        if image_ratio > target_ratio:
            fitted_width = width
            fitted_height = round(width / image_ratio)
        else:
            fitted_height = height
            fitted_width = round(height * image_ratio)
        fitted = image.resize((fitted_width, fitted_height), Image.Resampling.LANCZOS)
        left = (width - fitted.width) // 2
        top = (height - fitted.height) // 2
        background.paste(fitted, (left, top))
        return background

    @staticmethod
    def _image_padding_color(image: Image.Image) -> tuple[int, int, int]:
        rgb = image.convert("RGB")
        width, height = rgb.size
        pixels = [
            rgb.getpixel((0, 0)),
            rgb.getpixel((max(0, width - 1), 0)),
            rgb.getpixel((0, max(0, height - 1))),
            rgb.getpixel((max(0, width - 1), max(0, height - 1))),
        ]
        return tuple(round(sum(pixel[channel] for pixel in pixels) / len(pixels)) for channel in range(3))

    def _render_segment(
        self,
        image_path: Path,
        audio_path: Path,
        output_path: Path,
        duration_seconds: float | None,
    ) -> None:
        ffmpeg = self._ffmpeg_executable()
        duration = f"{max(0.1, float(duration_seconds or 1.0)):.3f}"
        cmd = [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-i",
            str(audio_path),
            "-c:v",
            "libx264",
            "-tune",
            "stillimage",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "24000",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(int(settings.STORY_VIDEO_FPS)),
            "-t",
            duration,
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        self._run_ffmpeg(cmd)

    def _concat_segments(self, tmpdir: Path, segment_paths: list[Path], output_path: Path) -> None:
        concat_path = tmpdir / "concat.txt"
        concat_path.write_text(
            "\n".join(f"file '{self._concat_path(path)}'" for path in segment_paths),
            encoding="utf-8",
        )
        cmd = [
            self._ffmpeg_executable(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "24000",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(int(settings.STORY_VIDEO_FPS)),
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        self._run_ffmpeg(cmd)

    @staticmethod
    def _run_ffmpeg(cmd: list[str]) -> None:
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise AppException(
                "ffmpeg executable was not found. Install imageio-ffmpeg dependencies.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "STORY_VIDEO_FFMPEG_MISSING",
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise AppException(
                f"ffmpeg failed while rendering story video: {exc.stderr[-1000:]}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "STORY_VIDEO_FFMPEG_FAILED",
            ) from exc

    @staticmethod
    def _ffmpeg_executable() -> str:
        try:
            import imageio_ffmpeg
        except ImportError as exc:
            raise AppException(
                "imageio-ffmpeg is required for story video generation. Install requirements.txt.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "STORY_VIDEO_DEPENDENCY_MISSING",
            ) from exc
        return imageio_ffmpeg.get_ffmpeg_exe()

    @staticmethod
    def _write_silent_wav(path: Path, duration_seconds: float) -> None:
        sample_rate = 24000
        frame_count = max(1, int(sample_rate * max(0.1, duration_seconds)))
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(b"\x00\x00" * frame_count)

    @staticmethod
    def _draw_center_title(
        draw: ImageDraw.ImageDraw,
        size: tuple[int, int],
        title: str,
        *,
        body: str | None = None,
    ) -> None:
        width, height = size
        title_font = StoryVideoService._font(54)
        body_font = StoryVideoService._font(30)
        title_lines = StoryVideoService._wrap_text(title, title_font, width - 220)
        body_lines = StoryVideoService._wrap_text(body or "", body_font, width - 260) if body else []
        line_gap = 14
        title_height = sum(StoryVideoService._text_height(draw, line, title_font) + line_gap for line in title_lines)
        body_height = sum(StoryVideoService._text_height(draw, line, body_font) + 8 for line in body_lines)
        block_height = title_height + body_height + (22 if body_lines else 0)
        top = max(80, (height - block_height) // 2)
        StoryVideoService._draw_text_panel(draw, (90, top - 40, width - 90, top + block_height + 40))

        y = top
        for line in title_lines:
            text_width = StoryVideoService._text_width(draw, line, title_font)
            draw.text(((width - text_width) / 2, y), line, font=title_font, fill=(255, 255, 255, 255))
            y += StoryVideoService._text_height(draw, line, title_font) + line_gap
        if body_lines:
            y += 10
            for line in body_lines:
                text_width = StoryVideoService._text_width(draw, line, body_font)
                draw.text(((width - text_width) / 2, y), line, font=body_font, fill=(255, 255, 255, 245))
                y += StoryVideoService._text_height(draw, line, body_font) + 8

    @staticmethod
    def _draw_bottom_text(draw: ImageDraw.ImageDraw, size: tuple[int, int], text: str) -> None:
        width, height = size
        font = StoryVideoService._font(30)
        lines = StoryVideoService._wrap_text(text, font, width - 170)
        max_lines = 5
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1].rstrip(".") + "..."
        line_height = StoryVideoService._text_height(draw, "Ag", font) + 10
        panel_height = max(120, line_height * len(lines) + 46)
        top = height - panel_height - 26
        StoryVideoService._draw_text_panel(draw, (60, top, width - 60, height - 26))
        y = top + 23
        for line in lines:
            draw.text((86, y), line, font=font, fill=(255, 255, 255, 255))
            y += line_height

    @staticmethod
    def _draw_text_panel(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
        draw.rounded_rectangle(box, radius=24, fill=(0, 0, 0, 168))

    @staticmethod
    def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        words = html.unescape(text or "").split()
        if not words:
            return []
        lines: list[str] = []
        current: list[str] = []
        probe_image = Image.new("RGB", (10, 10))
        probe_draw = ImageDraw.Draw(probe_image)
        for word in words:
            candidate = " ".join([*current, word])
            if current and StoryVideoService._text_width(probe_draw, candidate, font) > max_width:
                lines.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            lines.append(" ".join(current))
        return lines

    @staticmethod
    def _font(size: int) -> ImageFont.ImageFont:
        for name in ("arial.ttf", "DejaVuSans.ttf", "NotoSans-Regular.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]

    @staticmethod
    def _text_height(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[3] - bbox[1]

    async def _set_language_metadata(
        self,
        story: Any,
        language: str,
        metadata: dict[str, Any],
        *,
        commit: bool,
    ) -> None:
        video_metadata = dict(story.video_metadata or {})
        video_metadata[language] = metadata
        story.video_metadata = video_metadata
        story.video_created = any(
            isinstance(item, dict)
            and item.get("status") == VIDEO_STATUS_COMPLETED
            and (item.get("video_url") or item.get("local_video_path"))
            for item in video_metadata.values()
        )
        self._flag_story_video_fields(story)
        await self.stories.update(story)
        if commit:
            await self.session.commit()

    @staticmethod
    def _flag_story_video_fields(story: Any) -> None:
        try:
            flag_modified(story, "video_metadata")
        except Exception:
            pass

    @staticmethod
    def _response_from_story(story: Any, language: str) -> StoryVideoResponse:
        record = StoryVideoService._language_metadata(story, language)
        return StoryVideoResponse(
            story_id=story.id,
            language=language,
            status=str(record.get("status") or VIDEO_STATUS_NOT_STARTED),
            video_url=record.get("video_url"),
            local_video_path=record.get("local_video_path"),
            error_message=record.get("error_message"),
            requested_at=record.get("requested_at"),
            started_at=record.get("started_at"),
            completed_at=record.get("completed_at"),
            updated_at=record.get("updated_at"),
        )

    @staticmethod
    def _language_metadata(story: Any, language: str) -> dict[str, Any]:
        metadata = story.video_metadata if isinstance(getattr(story, "video_metadata", None), dict) else {}
        record = metadata.get(language)
        return dict(record) if isinstance(record, dict) else {}

    @staticmethod
    def _storage_result_values(storage_result: StoryVideoStorageResult | str) -> tuple[str | None, str | None]:
        if isinstance(storage_result, StoryVideoStorageResult):
            return storage_result.video_url, storage_result.local_video_path
        return str(storage_result), None

    @staticmethod
    def _page_number(page: dict[str, Any], fallback: int) -> int:
        raw = page.get("page_number", page.get("page"))
        if isinstance(raw, bool):
            return fallback
        if isinstance(raw, int) and raw > 0:
            return raw
        if isinstance(raw, str) and raw.strip().isdigit():
            return int(raw.strip())
        return fallback

    @staticmethod
    def _page_duration_seconds(page: dict[str, Any]) -> float | None:
        raw = page.get("duration")
        if isinstance(raw, bool):
            return None
        if isinstance(raw, (int, float)) and raw > 0:
            return float(raw)
        if isinstance(raw, str):
            try:
                duration = float(raw.strip())
            except ValueError:
                return None
            return duration if duration > 0 else None
        return None

    @staticmethod
    def _moral_text(value: Any) -> str | None:
        if isinstance(value, dict):
            return StoryVideoService._clean_text(value.get("text"))
        return StoryVideoService._clean_text(value)

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _normalize_language(language: str) -> str:
        normalized = str(language or "").strip().lower()
        if not normalized:
            raise AppException("Language is required", code="LANGUAGE_REQUIRED")
        return normalized

    @staticmethod
    def _validate_asset_size(content: bytes, asset_type: str) -> None:
        if not content:
            raise AppException(f"Story video {asset_type} asset is empty", code="STORY_VIDEO_EMPTY_ASSET")
        if len(content) > settings.STORY_VIDEO_MAX_ASSET_BYTES:
            raise AppException(
                f"Story video {asset_type} asset is too large",
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                "STORY_VIDEO_ASSET_TOO_LARGE",
            )

    @staticmethod
    def _local_asset_path(url: str) -> Path | None:
        value = str(url or "").strip()
        parsed = urlparse(value)
        path = parsed.path if parsed.scheme in {"http", "https"} else value
        for prefix, root in (
            (settings.MEDIA_URL_PREFIX.rstrip("/") + "/", settings.media_root_path),
            (settings.AUDIO_URL_PREFIX.rstrip("/") + "/", settings.audio_root_path),
        ):
            if not path.startswith(prefix):
                continue
            candidate = (root / path[len(prefix) :]).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                return None
            return candidate if candidate.exists() else None
        return None

    @staticmethod
    def _data_url_bytes(url: str) -> bytes | None:
        value = str(url or "")
        if not value.startswith("data:") or "," not in value:
            return None
        header, payload = value.split(",", 1)
        if ";base64" not in header:
            return payload.encode("utf-8")
        try:
            return base64.b64decode(payload)
        except ValueError as exc:
            raise AppException("Invalid data URL asset", code="STORY_VIDEO_DATA_URL_INVALID") from exc

    @staticmethod
    def _asset_suffix(url: str, default: str) -> str:
        suffix = Path(urlparse(str(url or "")).path).suffix.lower()
        if suffix and len(suffix) <= 12 and all(character.isalnum() or character == "." for character in suffix):
            return suffix
        return default

    @staticmethod
    def _concat_path(path: Path) -> str:
        return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
