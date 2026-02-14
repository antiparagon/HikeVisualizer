"""Media file scanner for discovering and extracting metadata from photos, videos, and audio."""

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Set

from dateutil import parser as date_parser
from PIL import Image

from ..models.media_item import MediaItem, MediaType


class MediaScanner:
    """Scans a directory for media files and extracts timestamps."""

    PHOTO_EXTENSIONS: Set[str] = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    HEIC_EXTENSIONS: Set[str] = {".heic", ".heif"}
    VIDEO_EXTENSIONS: Set[str] = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
    AUDIO_EXTENSIONS: Set[str] = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}

    def __init__(self, media_dir: str):
        self.media_dir = Path(media_dir)

    def scan(self) -> List[MediaItem]:
        """Scan directory recursively for media files."""
        media_items = []

        if not self.media_dir.exists():
            return media_items

        for file_path in self.media_dir.rglob("*"):
            if file_path.is_file():
                media_item = self._process_file(file_path)
                if media_item:
                    media_items.append(media_item)

        # Sort by timestamp
        media_items.sort(key=lambda x: x.timestamp)
        return media_items

    def _process_file(self, file_path: Path) -> Optional[MediaItem]:
        """Process a single file and extract metadata."""
        ext = file_path.suffix.lower()

        if ext in self.PHOTO_EXTENSIONS:
            return self._process_photo(file_path)
        elif ext in self.HEIC_EXTENSIONS:
            return self._process_heic(file_path)
        elif ext in self.VIDEO_EXTENSIONS:
            return self._process_video(file_path)
        elif ext in self.AUDIO_EXTENSIONS:
            return self._process_audio(file_path)

        return None

    def _process_photo(self, file_path: Path) -> Optional[MediaItem]:
        """Extract EXIF timestamp from photo."""
        try:
            with Image.open(file_path) as img:
                # Try multiple methods to get EXIF datetime
                timestamp = self._extract_photo_datetime(img)
                width, height = img.size

                return MediaItem(
                    file_path=str(file_path.absolute()),
                    media_type=MediaType.PHOTO,
                    timestamp=timestamp or self._get_file_mtime(file_path),
                    filename=file_path.name,
                    output_filename=self._sanitize_filename(file_path.name),
                    width=width,
                    height=height,
                )
        except Exception:
            # If PIL fails, try to create a basic entry
            return MediaItem(
                file_path=str(file_path.absolute()),
                media_type=MediaType.PHOTO,
                timestamp=self._get_file_mtime(file_path),
                filename=file_path.name,
                output_filename=self._sanitize_filename(file_path.name),
            )

    def _extract_photo_datetime(self, img: Image.Image) -> Optional[datetime]:
        """Extract creation datetime from photo using multiple methods."""
        # Try modern getexif() API first
        try:
            exif = img.getexif()
            if exif:
                # Check main EXIF tags: DateTimeOriginal, DateTimeDigitized, DateTime
                for tag_id in [36867, 36868, 306]:
                    if tag_id in exif:
                        dt_str = exif[tag_id]
                        if dt_str:
                            dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                            return self._make_aware(dt)

                # Check EXIF IFD for DateTimeOriginal
                try:
                    from PIL.ExifTags import IFD
                    exif_ifd = exif.get_ifd(IFD.Exif)
                    if exif_ifd:
                        for tag_id in [36867, 36868]:
                            if tag_id in exif_ifd:
                                dt_str = exif_ifd[tag_id]
                                if dt_str:
                                    dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                                    return self._make_aware(dt)
                except (ImportError, AttributeError):
                    pass
        except Exception:
            pass

        # Fallback to legacy _getexif() API
        try:
            if hasattr(img, '_getexif'):
                exif_data = img._getexif()
                if exif_data:
                    return self._extract_exif_datetime(exif_data)
        except Exception:
            pass

        return None

    def _process_heic(self, file_path: Path) -> Optional[MediaItem]:
        """Process HEIC/HEIF image file. Output filename will be .jpg for web compatibility."""
        try:
            # Try to use pillow-heif for HEIC support
            from pillow_heif import register_heif_opener
            register_heif_opener()

            with Image.open(file_path) as img:
                # Extract EXIF using modern API
                timestamp = self._extract_heic_datetime(img)
                width, height = img.size

                # Change output extension to .jpg for web compatibility
                base_name = file_path.stem
                output_name = self._sanitize_filename(base_name + ".jpg")

                return MediaItem(
                    file_path=str(file_path.absolute()),
                    media_type=MediaType.PHOTO,
                    timestamp=timestamp or self._get_file_mtime(file_path),
                    filename=file_path.name,
                    output_filename=output_name,
                    width=width,
                    height=height,
                )
        except ImportError:
            # pillow-heif not installed, create basic entry
            base_name = file_path.stem
            output_name = self._sanitize_filename(base_name + ".jpg")

            return MediaItem(
                file_path=str(file_path.absolute()),
                media_type=MediaType.PHOTO,
                timestamp=self._get_file_mtime(file_path),
                filename=file_path.name,
                output_filename=output_name,
            )
        except Exception:
            # If processing fails, create basic entry
            base_name = file_path.stem
            output_name = self._sanitize_filename(base_name + ".jpg")

            return MediaItem(
                file_path=str(file_path.absolute()),
                media_type=MediaType.PHOTO,
                timestamp=self._get_file_mtime(file_path),
                filename=file_path.name,
                output_filename=output_name,
            )

    def _extract_heic_datetime(self, img: Image.Image) -> Optional[datetime]:
        """Extract creation datetime from HEIC image using multiple methods."""
        # Try modern getexif() API first
        try:
            exif = img.getexif()
            if exif:
                # Check main EXIF tags
                # 306 = DateTime, 36867 = DateTimeOriginal, 36868 = DateTimeDigitized
                for tag_id in [36867, 36868, 306]:
                    if tag_id in exif:
                        dt_str = exif[tag_id]
                        if dt_str:
                            dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                            return self._make_aware(dt)

                # Check EXIF IFD for DateTimeOriginal
                from PIL.ExifTags import IFD
                exif_ifd = exif.get_ifd(IFD.Exif)
                if exif_ifd:
                    for tag_id in [36867, 36868]:
                        if tag_id in exif_ifd:
                            dt_str = exif_ifd[tag_id]
                            if dt_str:
                                dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                                return self._make_aware(dt)
        except Exception:
            pass

        # Fallback to legacy _getexif() API
        try:
            if hasattr(img, '_getexif'):
                exif_data = img._getexif()
                if exif_data:
                    return self._extract_exif_datetime(exif_data)
        except Exception:
            pass

        return None

    def _extract_exif_datetime(self, exif_data: dict) -> Optional[datetime]:
        """Extract DateTimeOriginal from EXIF data."""
        if not exif_data:
            return None

        # Priority: DateTimeOriginal > DateTimeDigitized > DateTime
        datetime_tags = [36867, 36868, 306]  # Tag IDs

        for tag_id in datetime_tags:
            if tag_id in exif_data:
                try:
                    # EXIF datetime format: "YYYY:MM:DD HH:MM:SS"
                    dt_str = exif_data[tag_id]
                    dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                    return self._make_aware(dt)
                except (ValueError, TypeError):
                    continue

        return None

    def _process_video(self, file_path: Path) -> Optional[MediaItem]:
        """Extract metadata from video using ffprobe."""
        metadata = self._ffprobe_metadata(file_path)

        timestamp = self._get_file_mtime(file_path)
        duration = None
        width, height = None, None

        if metadata:
            timestamp = self._extract_ffprobe_timestamp(metadata) or timestamp
            duration = self._extract_duration(metadata)
            width, height = self._extract_dimensions(metadata)

        return MediaItem(
            file_path=str(file_path.absolute()),
            media_type=MediaType.VIDEO,
            timestamp=timestamp,
            filename=file_path.name,
            output_filename=self._sanitize_filename(file_path.name),
            width=width,
            height=height,
            duration_seconds=duration,
        )

    def _process_audio(self, file_path: Path) -> Optional[MediaItem]:
        """Extract metadata from audio using ffprobe."""
        metadata = self._ffprobe_metadata(file_path)

        timestamp = self._get_file_mtime(file_path)
        duration = None

        if metadata:
            timestamp = self._extract_ffprobe_timestamp(metadata) or timestamp
            duration = self._extract_duration(metadata)

        return MediaItem(
            file_path=str(file_path.absolute()),
            media_type=MediaType.AUDIO,
            timestamp=timestamp,
            filename=file_path.name,
            output_filename=self._sanitize_filename(file_path.name),
            duration_seconds=duration,
        )

    def _ffprobe_metadata(self, file_path: Path) -> Optional[dict]:
        """Run ffprobe and return JSON metadata."""
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(file_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            pass
        return None

    def _extract_ffprobe_timestamp(self, metadata: dict) -> Optional[datetime]:
        """Extract creation_time from ffprobe metadata."""
        tags = metadata.get("format", {}).get("tags", {})
        creation_time = tags.get("creation_time")

        if creation_time:
            try:
                dt = date_parser.parse(creation_time)
                return self._make_aware(dt)
            except (ValueError, TypeError):
                pass
        return None

    def _extract_duration(self, metadata: dict) -> Optional[float]:
        """Extract duration in seconds from ffprobe metadata."""
        duration = metadata.get("format", {}).get("duration")
        if duration:
            try:
                return float(duration)
            except ValueError:
                pass
        return None

    def _extract_dimensions(self, metadata: dict) -> tuple:
        """Extract width, height from video stream."""
        for stream in metadata.get("streams", []):
            if stream.get("codec_type") == "video":
                return stream.get("width"), stream.get("height")
        return None, None

    @staticmethod
    def _get_file_mtime(file_path: Path) -> datetime:
        """Get file modification time as fallback (UTC)."""
        return datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)

    @staticmethod
    def _make_aware(dt: datetime) -> datetime:
        """Ensure datetime is timezone-aware (assume UTC if naive)."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Sanitize filename for web output."""
        # Replace spaces and special chars
        sanitized = re.sub(r"[^\w\-_.]", "_", filename)
        return sanitized.lower()
