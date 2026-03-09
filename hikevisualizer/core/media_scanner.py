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

    def __init__(self, media_dir: str, exclude_dirs: Optional[List[str]] = None):
        self.media_dir = Path(media_dir)
        self.exclude_dirs = exclude_dirs or ["output", "assets"]

    def scan(self) -> List[MediaItem]:
        """Scan directory recursively for media files."""
        media_items = []

        if not self.media_dir.exists():
            return media_items

        for file_path in self.media_dir.rglob("*"):
            # Skip files in excluded directories
            if any(excluded in file_path.parts for excluded in self.exclude_dirs):
                continue
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
                is_360 = self._detect_360_photo(file_path, img, width, height)

                return MediaItem(
                    file_path=str(file_path.absolute()),
                    media_type=MediaType.PHOTO,
                    timestamp=timestamp or self._get_file_mtime(file_path),
                    filename=file_path.name,
                    output_filename=self._sanitize_filename(file_path.name),
                    width=width,
                    height=height,
                    is_360=is_360,
                    has_exif_timestamp=timestamp is not None,
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
                # Get the EXIF IFD (contains offset tags and datetime tags)
                exif_ifd = {}
                try:
                    from PIL.ExifTags import IFD
                    exif_ifd = exif.get_ifd(IFD.Exif) or {}
                except (ImportError, AttributeError):
                    pass

                # Check main EXIF tags: DateTimeOriginal, DateTimeDigitized, DateTime
                for tag_id in [36867, 36868, 306]:
                    dt_str = exif.get(tag_id) or exif_ifd.get(tag_id)
                    if dt_str:
                        dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                        # Look for matching OffsetTime tag in both IFDs
                        offset = self._get_exif_offset(exif_ifd, tag_id) or self._get_exif_offset(exif, tag_id)
                        return self._apply_offset(dt, offset)
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
                is_360 = self._detect_360_photo(file_path, img, width, height)

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
                    is_360=is_360,
                    has_exif_timestamp=timestamp is not None,
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
                # Get the EXIF IFD (contains offset tags and datetime tags)
                exif_ifd = {}
                try:
                    from PIL.ExifTags import IFD
                    exif_ifd = exif.get_ifd(IFD.Exif) or {}
                except (ImportError, AttributeError):
                    pass

                for tag_id in [36867, 36868, 306]:
                    dt_str = exif.get(tag_id) or exif_ifd.get(tag_id)
                    if dt_str:
                        dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                        offset = self._get_exif_offset(exif_ifd, tag_id) or self._get_exif_offset(exif, tag_id)
                        return self._apply_offset(dt, offset)
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
        """Extract DateTimeOriginal from EXIF data (legacy _getexif() API)."""
        if not exif_data:
            return None

        # Priority: DateTimeOriginal > DateTimeDigitized > DateTime
        datetime_tags = [36867, 36868, 306]

        for tag_id in datetime_tags:
            if tag_id in exif_data:
                try:
                    dt_str = exif_data[tag_id]
                    dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
                    offset = self._get_exif_offset(exif_data, tag_id)
                    return self._apply_offset(dt, offset)
                except (ValueError, TypeError):
                    continue

        return None

    def _process_video(self, file_path: Path) -> Optional[MediaItem]:
        """Extract metadata from video using ffprobe."""
        metadata = self._ffprobe_metadata(file_path)

        timestamp = self._get_file_mtime(file_path)
        has_exif = False
        duration = None
        width, height = None, None

        if metadata:
            ffprobe_ts = self._extract_ffprobe_timestamp(metadata)
            if ffprobe_ts:
                timestamp = ffprobe_ts
                has_exif = True
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
            has_exif_timestamp=has_exif,
        )

    def _process_audio(self, file_path: Path) -> Optional[MediaItem]:
        """Extract metadata from audio using ffprobe."""
        metadata = self._ffprobe_metadata(file_path)

        timestamp = self._get_file_mtime(file_path)
        has_exif = False
        duration = None

        if metadata:
            ffprobe_ts = self._extract_ffprobe_timestamp(metadata)
            if ffprobe_ts:
                timestamp = ffprobe_ts
                has_exif = True
            duration = self._extract_duration(metadata)

        return MediaItem(
            file_path=str(file_path.absolute()),
            media_type=MediaType.AUDIO,
            timestamp=timestamp,
            filename=file_path.name,
            output_filename=self._sanitize_filename(file_path.name),
            duration_seconds=duration,
            has_exif_timestamp=has_exif,
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
    def _apply_offset(dt: datetime, offset_str: Optional[str]) -> datetime:
        """Apply an EXIF OffsetTime string (e.g. '-04:00', '+05:30') to a naive datetime.

        If offset_str is valid, returns a timezone-aware datetime in UTC.
        Otherwise falls back to assuming UTC.
        """
        if offset_str and isinstance(offset_str, str):
            try:
                offset_str = offset_str.strip()
                sign = 1 if offset_str.startswith('+') else -1
                parts = offset_str.lstrip('+-').split(':')
                hours = int(parts[0])
                minutes = int(parts[1]) if len(parts) > 1 else 0
                from datetime import timedelta
                tz = timezone(timedelta(hours=sign * hours, minutes=sign * minutes))
                return dt.replace(tzinfo=tz)
            except (ValueError, IndexError):
                pass
        # Fallback: assume UTC
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    @staticmethod
    def _get_exif_offset(exif_source: dict, dt_tag_id: int) -> Optional[str]:
        """Get the OffsetTime string matching a DateTime EXIF tag.

        Mapping: DateTimeOriginal(36867)->OffsetTimeOriginal(36881),
                 DateTimeDigitized(36868)->OffsetTimeDigitized(36882),
                 DateTime(306)->OffsetTime(36880).
        """
        offset_map = {36867: 36881, 36868: 36882, 306: 36880}
        offset_tag = offset_map.get(dt_tag_id)
        if offset_tag and offset_tag in exif_source:
            return exif_source[offset_tag]
        return None

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Sanitize filename for web output."""
        # Replace spaces and special chars
        sanitized = re.sub(r"[^\w\-_.]", "_", filename)
        return sanitized.lower()

    def _detect_360_photo(
        self, file_path: Path, img: Image.Image, width: int, height: int
    ) -> bool:
        """
        Detect if a photo is a 360/equirectangular panorama.

        Detection methods:
        1. XMP metadata with GPano:ProjectionType = "equirectangular"
        2. Aspect ratio close to 2:1 (equirectangular projection standard)
        3. Filename contains "pano", "360", or "sphere"
        """
        # Method 1: Check XMP metadata for GPano projection type
        try:
            # Read raw file bytes to find XMP data
            with open(file_path, "rb") as f:
                # Read first 64KB which should contain XMP header
                data = f.read(65536)
                data_str = data.decode("utf-8", errors="ignore")

                # Look for GPano namespace indicators
                if "GPano:ProjectionType" in data_str:
                    if "equirectangular" in data_str.lower():
                        return True

                # Also check for Google Photo Sphere XMP
                if "ProjectionType" in data_str and "equirectangular" in data_str.lower():
                    return True

                # Check for UsePanoramaViewer tag
                if "GPano:UsePanoramaViewer" in data_str:
                    if ">True<" in data_str or ">true<" in data_str:
                        return True
        except Exception:
            pass

        # Method 2: Check aspect ratio (2:1 is standard for equirectangular)
        if width and height and height > 0:
            aspect_ratio = width / height
            # Allow some tolerance (1.9 to 2.1)
            if 1.9 <= aspect_ratio <= 2.1:
                # Additional check: must be high resolution for 360
                if width >= 4000:
                    return True

        # Method 3: Check filename for common 360 indicators
        filename_lower = file_path.name.lower()
        if any(indicator in filename_lower for indicator in ["_pano", "360", "sphere", "equirect"]):
            return True

        return False
