"""HikeData aggregate model containing all hike information."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

from .trackpoint import TrackPoint
from .media_item import MediaItem


@dataclass
class ElevationStats:
    """Elevation statistics for the hike."""

    min_elevation: float  # meters
    max_elevation: float  # meters
    total_ascent: float  # meters
    total_descent: float  # meters

    # Conversion factor: 1 meter = 3.28084 feet
    METERS_TO_FEET = 3.28084

    @property
    def elevation_gain(self) -> float:
        """Net elevation gain (max - min)."""
        return self.max_elevation - self.min_elevation

    @property
    def min_elevation_ft(self) -> float:
        """Minimum elevation in feet."""
        return self.min_elevation * self.METERS_TO_FEET

    @property
    def max_elevation_ft(self) -> float:
        """Maximum elevation in feet."""
        return self.max_elevation * self.METERS_TO_FEET

    @property
    def total_ascent_ft(self) -> float:
        """Total ascent in feet."""
        return self.total_ascent * self.METERS_TO_FEET

    @property
    def total_descent_ft(self) -> float:
        """Total descent in feet."""
        return self.total_descent * self.METERS_TO_FEET


@dataclass
class HikeData:
    """Complete aggregated hike data."""

    trackpoints: List[TrackPoint] = field(default_factory=list)
    media_items: List[MediaItem] = field(default_factory=list)

    # Metadata
    name: str = "Untitled Hike"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    # Calculated stats
    total_distance: float = 0.0  # meters
    duration: Optional[timedelta] = None
    elevation_stats: Optional[ElevationStats] = None

    # HR stats (from FIT)
    min_hr: Optional[int] = None
    max_hr: Optional[int] = None
    avg_hr: Optional[int] = None

    @property
    def distance_km(self) -> float:
        """Total distance in kilometers."""
        return self.total_distance / 1000.0

    @property
    def distance_miles(self) -> float:
        """Total distance in miles."""
        return self.total_distance / 1609.344

    @property
    def pace_min_per_mile(self) -> Optional[float]:
        """Average pace in minutes per mile."""
        if not self.duration or self.distance_miles == 0:
            return None
        total_minutes = self.duration.total_seconds() / 60
        return total_minutes / self.distance_miles

    @property
    def pace_formatted(self) -> str:
        """Format pace as MM:SS per mile."""
        pace = self.pace_min_per_mile
        if pace is None:
            return "--:--"
        minutes = int(pace)
        seconds = int((pace - minutes) * 60)
        return f"{minutes}:{seconds:02d}"

    @property
    def duration_formatted(self) -> str:
        """Format duration as HH:MM:SS."""
        if not self.duration:
            return "00:00:00"
        total_seconds = int(self.duration.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def to_geojson(self) -> dict:
        """Convert track to GeoJSON LineString."""
        return {
            "type": "Feature",
            "properties": {
                "name": self.name,
                "total_distance": self.total_distance,
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [tp.to_geojson_coord() for tp in self.trackpoints],
            },
        }

    def get_bounds(self) -> dict:
        """Calculate bounding box of track."""
        if not self.trackpoints:
            return {"north": 0, "south": 0, "east": 0, "west": 0}

        lats = [tp.latitude for tp in self.trackpoints]
        lngs = [tp.longitude for tp in self.trackpoints]

        return {
            "north": max(lats),
            "south": min(lats),
            "east": max(lngs),
            "west": min(lngs),
        }

    def get_center(self) -> tuple:
        """Calculate center point of track (lng, lat)."""
        if not self.trackpoints:
            return (0, 0)

        lats = [tp.latitude for tp in self.trackpoints]
        lngs = [tp.longitude for tp in self.trackpoints]

        return (sum(lngs) / len(lngs), sum(lats) / len(lats))
