"""Heart rate zone calculator for color-coded track visualization."""

from typing import List, Tuple

from ..models.hike_data import HikeData


class HRZoneCalculator:
    """
    Calculate heart rate zones relative to observed min/max HR.

    Uses a simplified 5-zone model based on percentage of HR range:
    - Zone 1 (Recovery): 0-20% of range -> Blue
    - Zone 2 (Easy): 20-40% of range -> Green
    - Zone 3 (Moderate): 40-60% of range -> Yellow
    - Zone 4 (Hard): 60-80% of range -> Orange
    - Zone 5 (Maximum): 80-100% of range -> Red
    """

    # Zone definitions: (min_pct, max_pct, zone_number, color)
    ZONES: List[Tuple[float, float, int, str]] = [
        (0.0, 0.2, 1, "#3B82F6"),  # Blue
        (0.2, 0.4, 2, "#22C55E"),  # Green
        (0.4, 0.6, 3, "#EAB308"),  # Yellow
        (0.6, 0.8, 4, "#F97316"),  # Orange
        (0.8, 1.0, 5, "#EF4444"),  # Red
    ]

    # Default color for missing HR data
    DEFAULT_COLOR = "#9CA3AF"  # Gray

    def __init__(self, hike_data: HikeData):
        self.hike_data = hike_data
        self.min_hr = hike_data.min_hr
        self.max_hr = hike_data.max_hr
        self.hr_range = (
            (self.max_hr - self.min_hr) if self.min_hr and self.max_hr else 0
        )

    def calculate_zones(self) -> None:
        """Assign HR zone and color to each trackpoint."""
        if not self.hr_range:
            # No HR data - use elevation-based coloring as fallback
            self._calculate_elevation_colors()
            return

        for tp in self.hike_data.trackpoints:
            if tp.heart_rate:
                zone, color = self._get_zone_and_color(tp.heart_rate)
                tp.hr_zone = zone
                tp.hr_color = color
            else:
                # Default for missing HR data
                tp.hr_zone = None
                tp.hr_color = self.DEFAULT_COLOR

    def _calculate_elevation_colors(self) -> None:
        """Fallback: color track by elevation when no HR data available."""
        if not self.hike_data.trackpoints:
            return

        elevations = [tp.elevation for tp in self.hike_data.trackpoints]
        min_elev = min(elevations)
        max_elev = max(elevations)
        elev_range = max_elev - min_elev

        if elev_range == 0:
            # Flat track - use default color
            for tp in self.hike_data.trackpoints:
                tp.hr_zone = None
                tp.hr_color = self.DEFAULT_COLOR
            return

        # Color by elevation: low = blue, high = red
        for tp in self.hike_data.trackpoints:
            pct = (tp.elevation - min_elev) / elev_range
            pct = max(0.0, min(1.0, pct))

            # Find the appropriate zone color based on elevation percentage
            for min_pct, max_pct, zone, color in self.ZONES:
                if min_pct <= pct < max_pct:
                    tp.hr_zone = None  # Not an HR zone
                    tp.hr_color = color
                    break
            else:
                # Edge case: exactly 100%
                tp.hr_zone = None
                tp.hr_color = "#EF4444"

    def _get_zone_and_color(self, heart_rate: int) -> Tuple[int, str]:
        """Determine zone and color for a given heart rate."""
        # Calculate percentage of HR range
        pct = (heart_rate - self.min_hr) / self.hr_range
        pct = max(0.0, min(1.0, pct))  # Clamp to [0, 1]

        for min_pct, max_pct, zone, color in self.ZONES:
            if min_pct <= pct < max_pct:
                return zone, color

        # Edge case: exactly 100%
        return 5, "#EF4444"

    def get_gradient_stops(self) -> List[Tuple[float, str]]:
        """
        Generate gradient stops for Mapbox line-gradient.

        Returns list of (progress, color) tuples where progress is 0-1
        along the track length. Stops are guaranteed to be in strictly
        ascending order of progress.
        """
        if not self.hike_data.trackpoints:
            return [(0, self.DEFAULT_COLOR), (1, self.DEFAULT_COLOR)]

        total_distance = self.hike_data.total_distance
        if total_distance == 0:
            return [(0, self.DEFAULT_COLOR), (1, self.DEFAULT_COLOR)]

        # Collect all stops with their progress and color
        raw_stops = []
        for tp in self.hike_data.trackpoints:
            progress = tp.distance_from_start / total_distance
            color = tp.hr_color or self.DEFAULT_COLOR
            raw_stops.append((progress, color))

        # Ensure we have start and end
        first_color = self.hike_data.trackpoints[0].hr_color or self.DEFAULT_COLOR
        last_color = self.hike_data.trackpoints[-1].hr_color or self.DEFAULT_COLOR

        # Build final stops list with strictly ascending progress values
        stops = [(0, first_color)]
        last_progress = 0
        min_increment = 0.0001  # Minimum distance between stops

        for progress, color in raw_stops:
            # Skip if progress hasn't increased enough
            if progress <= last_progress + min_increment:
                continue
            # Only add if color is different from last stop
            if color != stops[-1][1]:
                stops.append((progress, color))
                last_progress = progress

        # Ensure end stop at 1.0
        if stops[-1][0] < 1.0 - min_increment:
            stops.append((1.0, last_color))
        elif stops[-1][0] != 1.0:
            # Adjust last stop to exactly 1.0
            stops[-1] = (1.0, stops[-1][1])

        return stops

    def interpolate_color(self, hr: int) -> str:
        """
        Get smoothly interpolated color for a heart rate value.
        Uses linear interpolation between zone boundary colors.
        """
        if not self.hr_range:
            return self.DEFAULT_COLOR

        pct = (hr - self.min_hr) / self.hr_range
        pct = max(0.0, min(1.0, pct))

        # Find bounding zones
        for i, (min_pct, max_pct, _, _) in enumerate(self.ZONES):
            if min_pct <= pct < max_pct:
                # Interpolate within zone
                local_pct = (pct - min_pct) / (max_pct - min_pct)

                color1 = self.ZONES[i][3]
                color2 = self.ZONES[min(i + 1, len(self.ZONES) - 1)][3]

                return self._lerp_hex_color(color1, color2, local_pct)

        return "#EF4444"

    @staticmethod
    def _lerp_hex_color(c1: str, c2: str, t: float) -> str:
        """Linear interpolation between two hex colors."""
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)

        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)

        return f"#{r:02x}{g:02x}{b:02x}"
