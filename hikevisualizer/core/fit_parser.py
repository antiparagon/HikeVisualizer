"""FIT file parser for extracting heart rate and GPS track data."""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fitparse import FitFile

from ..models.trackpoint import TrackPoint


SEMICIRCLE_TO_DEG = 180.0 / (2 ** 31)


@dataclass
class HRRecord:
    """A heart rate reading from the FIT file."""

    timestamp: datetime
    heart_rate: int  # bpm


@dataclass
class FITTrackData:
    """Complete track and HR data extracted from a FIT file."""

    trackpoints: List[TrackPoint] = field(default_factory=list)
    hr_records: List[HRRecord] = field(default_factory=list)
    has_gps: bool = False


class FITParser:
    """Parser for Garmin/ANT+ FIT files."""

    def __init__(self, fit_path: str):
        self.fit_path = fit_path
        self._records: List[HRRecord] = []

    def parse(self) -> List[HRRecord]:
        """Parse the FIT file and extract heart rate records only."""
        fitfile = FitFile(self.fit_path)

        for record in fitfile.get_messages("record"):
            hr_data = self._extract_hr_from_record(record)
            if hr_data:
                self._records.append(hr_data)

        return self._records

    def parse_track(self) -> FITTrackData:
        """Parse the FIT file, extracting GPS trackpoints and HR records."""
        fitfile = FitFile(self.fit_path)

        trackpoints = []
        hr_records = []
        cumulative_distance = 0.0
        prev_lat = None
        prev_lon = None

        for record in fitfile.get_messages("record"):
            values = record.get_values()
            timestamp = values.get("timestamp")
            if not timestamp:
                continue
            timestamp = self._make_aware(timestamp)

            # Extract HR
            heart_rate = values.get("heart_rate")
            if heart_rate is not None:
                hr_records.append(HRRecord(timestamp=timestamp, heart_rate=int(heart_rate)))

            # Extract GPS position (semicircles → degrees)
            raw_lat = values.get("position_lat")
            raw_lon = values.get("position_long")
            if raw_lat is None or raw_lon is None:
                continue

            lat = raw_lat * SEMICIRCLE_TO_DEG
            lon = raw_lon * SEMICIRCLE_TO_DEG

            # Prefer enhanced_altitude over altitude
            elevation = values.get("enhanced_altitude")
            if elevation is None:
                elevation = values.get("altitude")
            if elevation is None:
                elevation = 0.0

            # Cumulative distance
            if prev_lat is not None and prev_lon is not None:
                cumulative_distance += self._haversine_distance(prev_lat, prev_lon, lat, lon)

            trackpoints.append(TrackPoint(
                latitude=lat,
                longitude=lon,
                elevation=float(elevation),
                timestamp=timestamp,
                heart_rate=int(heart_rate) if heart_rate is not None else None,
                distance_from_start=cumulative_distance,
            ))
            prev_lat, prev_lon = lat, lon

        return FITTrackData(
            trackpoints=trackpoints,
            hr_records=hr_records,
            has_gps=len(trackpoints) > 0,
        )

    @staticmethod
    def _make_aware(dt: datetime) -> datetime:
        """Ensure datetime is timezone-aware (assume UTC if naive)."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def _extract_hr_from_record(self, record) -> Optional[HRRecord]:
        """Extract timestamp and heart rate from a record message."""
        values = record.get_values()

        timestamp = values.get("timestamp")
        heart_rate = values.get("heart_rate")

        if timestamp and heart_rate:
            return HRRecord(
                timestamp=self._make_aware(timestamp),
                heart_rate=int(heart_rate)
            )
        return None

    @staticmethod
    def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two points in meters using Haversine formula."""
        R = 6371000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def get_hr_stats(self) -> Dict[str, int]:
        """Get min, max, average heart rate statistics."""
        if not self._records:
            return {"min": 0, "max": 0, "avg": 0}

        hrs = [r.heart_rate for r in self._records]
        return {
            "min": min(hrs),
            "max": max(hrs),
            "avg": int(sum(hrs) / len(hrs)),
        }
