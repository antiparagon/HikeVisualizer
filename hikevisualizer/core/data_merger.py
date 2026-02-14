"""Data merger for combining GPX, FIT, and media data by timestamp."""

from bisect import bisect_left
from datetime import timedelta
from typing import List

from ..models.hike_data import HikeData
from ..models.media_item import MediaItem
from .fit_parser import HRRecord


class DataMerger:
    """Merges GPX trackpoints with FIT HR data and media timestamps."""

    def __init__(self, hike_data: HikeData):
        self.hike_data = hike_data

    def merge_heart_rate(
        self, hr_records: List[HRRecord], max_time_diff_seconds: int = 30
    ) -> None:
        """
        Merge heart rate data into trackpoints by timestamp.

        Algorithm:
        1. Sort both trackpoints and HR records by timestamp
        2. For each trackpoint, binary search for nearest HR record
        3. If within max_time_diff, assign HR to trackpoint
        """
        if not hr_records:
            return

        # Sort HR records by timestamp
        hr_records_sorted = sorted(hr_records, key=lambda x: x.timestamp)
        hr_timestamps = [r.timestamp for r in hr_records_sorted]

        for tp in self.hike_data.trackpoints:
            if tp.timestamp is None:
                continue

            # Binary search for nearest HR record
            idx = bisect_left(hr_timestamps, tp.timestamp)

            # Check both idx and idx-1 for closest match
            candidates = []
            if idx < len(hr_records_sorted):
                time_diff = abs(
                    (hr_timestamps[idx] - tp.timestamp).total_seconds()
                )
                candidates.append((idx, time_diff))
            if idx > 0:
                time_diff = abs(
                    (hr_timestamps[idx - 1] - tp.timestamp).total_seconds()
                )
                candidates.append((idx - 1, time_diff))

            if candidates:
                best_idx, time_diff = min(candidates, key=lambda x: x[1])
                if time_diff <= max_time_diff_seconds:
                    tp.heart_rate = hr_records_sorted[best_idx].heart_rate

        # Update hike-level HR stats
        hrs = [tp.heart_rate for tp in self.hike_data.trackpoints if tp.heart_rate]
        if hrs:
            self.hike_data.min_hr = min(hrs)
            self.hike_data.max_hr = max(hrs)
            self.hike_data.avg_hr = int(sum(hrs) / len(hrs))

    def merge_media(
        self, media_items: List[MediaItem], max_time_diff_minutes: int = 60
    ) -> None:
        """
        Associate media items with nearest trackpoints.

        Algorithm:
        1. For each media item, binary search trackpoints by timestamp
        2. Find nearest trackpoint within time threshold
        3. Assign trackpoint index and distance to media item
        """
        if not self.hike_data.trackpoints or not media_items:
            return

        tp_timestamps = [tp.timestamp for tp in self.hike_data.trackpoints]
        max_diff = timedelta(minutes=max_time_diff_minutes)

        for media in media_items:
            idx = bisect_left(tp_timestamps, media.timestamp)

            candidates = []
            if idx < len(tp_timestamps):
                diff = abs(tp_timestamps[idx] - media.timestamp)
                candidates.append((idx, diff))
            if idx > 0:
                diff = abs(tp_timestamps[idx - 1] - media.timestamp)
                candidates.append((idx - 1, diff))

            if candidates:
                best_idx, time_diff = min(candidates, key=lambda x: x[1])
                if time_diff <= max_diff:
                    media.nearest_trackpoint_index = best_idx
                    media.distance_from_start = self.hike_data.trackpoints[
                        best_idx
                    ].distance_from_start

        # Sort media by timestamp and store in hike_data
        self.hike_data.media_items = sorted(media_items, key=lambda x: x.timestamp)
