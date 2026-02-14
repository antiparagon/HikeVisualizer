"""FIT file parser for extracting heart rate data."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fitparse import FitFile


@dataclass
class HRRecord:
    """A heart rate reading from the FIT file."""

    timestamp: datetime
    heart_rate: int  # bpm


class FITParser:
    """Parser for Garmin/ANT+ FIT files."""

    def __init__(self, fit_path: str):
        self.fit_path = fit_path
        self._records: List[HRRecord] = []

    def parse(self) -> List[HRRecord]:
        """Parse the FIT file and extract heart rate records."""
        fitfile = FitFile(self.fit_path)

        for record in fitfile.get_messages("record"):
            hr_data = self._extract_hr_from_record(record)
            if hr_data:
                self._records.append(hr_data)

        return self._records

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
