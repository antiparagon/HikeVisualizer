"""MediaItem data model for photos, videos, and audio files."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class MediaType(Enum):
    """Types of media files supported."""

    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"


@dataclass
class MediaItem:
    """A media file with extracted timestamp and metadata."""

    file_path: str  # Original absolute path
    media_type: MediaType
    timestamp: datetime
    filename: str  # Original filename
    output_filename: str  # Sanitized filename for output

    # Optional metadata
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None  # For video/audio
    is_360: bool = False  # True for 360/panoramic photos

    # Calculated during merge
    nearest_trackpoint_index: Optional[int] = None
    distance_from_start: Optional[float] = None  # meters
    track_id: Optional[str] = None  # Which track this media belongs to

    @property
    def is_landscape(self) -> bool:
        """Check if media is landscape orientation."""
        if self.width and self.height:
            return self.width > self.height
        return False

    @property
    def icon_class(self) -> str:
        """Return CSS icon class for timeline."""
        if self.media_type == MediaType.PHOTO:
            return "icon-camera"
        elif self.media_type == MediaType.VIDEO:
            return "icon-video"
        else:
            return "icon-audio"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "file_path": self.file_path,
            "type": self.media_type.value,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "filename": self.filename,
            "output_filename": self.output_filename,
            "width": self.width,
            "height": self.height,
            "duration": self.duration_seconds,
            "trackpoint_index": self.nearest_trackpoint_index,
            "distance": self.distance_from_start,
            "track_id": self.track_id,
            "is_360": self.is_360,
        }
