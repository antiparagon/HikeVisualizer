"""GPX file parser for extracting track data."""

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import gpxpy
import gpxpy.gpx

from ..models.trackpoint import TrackPoint
from ..models.hike_data import HikeData, ElevationStats


class GPXParser:
    """Parser for GPX track files."""

    def __init__(self, gpx_path: str):
        self.gpx_path = Path(gpx_path)
        self._gpx: Optional[gpxpy.gpx.GPX] = None

    @staticmethod
    def _make_aware(dt: Optional[datetime]) -> Optional[datetime]:
        """Ensure datetime is timezone-aware (assume UTC if naive)."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def parse(self) -> HikeData:
        """Parse the GPX file and return HikeData with trackpoints."""
        with open(self.gpx_path, "r", encoding="utf-8") as f:
            self._gpx = gpxpy.parse(f)

        trackpoints = self._extract_trackpoints()
        hike_data = HikeData(trackpoints=trackpoints)

        # Set metadata
        if self._gpx.tracks and self._gpx.tracks[0].name:
            hike_data.name = self._gpx.tracks[0].name

        if trackpoints:
            hike_data.start_time = trackpoints[0].timestamp
            hike_data.end_time = trackpoints[-1].timestamp
            if hike_data.start_time and hike_data.end_time:
                hike_data.duration = hike_data.end_time - hike_data.start_time
            hike_data.total_distance = trackpoints[-1].distance_from_start
            hike_data.elevation_stats = self._calculate_elevation_stats(trackpoints)

        return hike_data

    def _extract_trackpoints(self) -> List[TrackPoint]:
        """Extract all trackpoints from all tracks and segments."""
        points = []
        cumulative_distance = 0.0
        prev_point = None

        for track in self._gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    if prev_point:
                        cumulative_distance += self._haversine_distance(
                            prev_point.latitude,
                            prev_point.longitude,
                            point.latitude,
                            point.longitude,
                        )

                    tp = TrackPoint(
                        latitude=point.latitude,
                        longitude=point.longitude,
                        elevation=point.elevation or 0.0,
                        timestamp=self._make_aware(point.time),
                        distance_from_start=cumulative_distance,
                    )
                    points.append(tp)
                    prev_point = point

        return points

    def _calculate_elevation_stats(
        self, trackpoints: List[TrackPoint]
    ) -> ElevationStats:
        """Calculate elevation statistics with smoothing."""
        elevations = [tp.elevation for tp in trackpoints]

        if not elevations:
            return ElevationStats(
                min_elevation=0,
                max_elevation=0,
                total_ascent=0,
                total_descent=0,
            )

        # Apply simple moving average smoothing (window of 5)
        smoothed = self._smooth_elevations(elevations, window=5)

        total_ascent = 0.0
        total_descent = 0.0

        # Calculate ascent/descent from smoothed data (no threshold needed after smoothing)
        for i in range(1, len(smoothed)):
            diff = smoothed[i] - smoothed[i - 1]
            if diff > 0:
                total_ascent += diff
            elif diff < 0:
                total_descent += abs(diff)

        return ElevationStats(
            min_elevation=min(elevations),
            max_elevation=max(elevations),
            total_ascent=round(total_ascent, 1),
            total_descent=round(total_descent, 1),
        )

    @staticmethod
    def _smooth_elevations(elevations: List[float], window: int = 5) -> List[float]:
        """Apply moving average smoothing to elevation data."""
        if len(elevations) < window:
            return elevations

        smoothed = []
        half_window = window // 2

        for i in range(len(elevations)):
            start = max(0, i - half_window)
            end = min(len(elevations), i + half_window + 1)
            smoothed.append(sum(elevations[start:end]) / (end - start))

        return smoothed

    @staticmethod
    def _haversine_distance(
        lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        """Calculate distance between two points in meters using Haversine formula."""
        R = 6371000  # Earth's radius in meters

        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)

        a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(
            phi2
        ) * math.sin(delta_lambda / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return R * c
