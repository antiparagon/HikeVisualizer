"""TrackPoint data model for GPS track data."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class TrackPoint:
    """A single point on the track with all associated data."""

    latitude: float
    longitude: float
    elevation: float  # meters
    timestamp: datetime
    heart_rate: Optional[int] = None  # bpm, merged from FIT
    hr_zone: Optional[int] = None  # 1-5, calculated
    hr_color: Optional[str] = None  # hex color for visualization
    distance_from_start: float = 0.0  # meters, cumulative

    def to_geojson_coord(self) -> list:
        """Return [lng, lat, elevation] for GeoJSON."""
        return [self.longitude, self.latitude, self.elevation]

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "lat": self.latitude,
            "lng": self.longitude,
            "elevation": self.elevation,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "heart_rate": self.heart_rate,
            "hr_zone": self.hr_zone,
            "hr_color": self.hr_color,
            "distance": self.distance_from_start,
        }
