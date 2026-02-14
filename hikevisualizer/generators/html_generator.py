"""HTML generator for creating the static hike visualization webpage."""

import logging
import shutil
from datetime import datetime, timezone as tz
from pathlib import Path
from typing import List, Optional

from jinja2 import Environment, PackageLoader, select_autoescape
from timezonefinder import TimezoneFinder
import pytz

from ..core.gpx_parser import GPXParser
from ..core.fit_parser import FITParser
from ..core.media_scanner import MediaScanner
from ..core.data_merger import DataMerger
from ..core.hr_zones import HRZoneCalculator
from ..models.hike_data import HikeData
from ..models.media_item import MediaItem, MediaType
from .js_generator import MapboxJSGenerator

logger = logging.getLogger(__name__)


def _detect_timezone(hike_data: HikeData) -> Optional[pytz.BaseTzInfo]:
    """Detect timezone from GPS coordinates of the hike."""
    if not hike_data.trackpoints:
        return None

    # Use the first trackpoint to determine timezone
    tp = hike_data.trackpoints[0]
    try:
        tf = TimezoneFinder()
        tz_name = tf.timezone_at(lat=tp.latitude, lng=tp.longitude)
        if tz_name:
            return pytz.timezone(tz_name)
    except Exception as e:
        logger.warning(f"Could not detect timezone: {e}")

    return None


def _make_local_time_filter(local_tz):
    """Create a Jinja2 filter to convert UTC times to local timezone."""
    def local_time(dt, fmt='%I:%M %p'):
        if dt is None:
            return ''
        try:
            # Convert to local timezone
            if dt.tzinfo is not None:
                local_dt = dt.astimezone(local_tz)
            else:
                # Assume UTC if naive
                local_dt = dt.replace(tzinfo=tz.utc).astimezone(local_tz)
            return local_dt.strftime(fmt)
        except Exception:
            return dt.strftime(fmt) if dt else ''
    return local_time


def generate_site(
    gpx_paths: List[str],
    fit_paths: List[str],
    media_path: Optional[str],
    output_dir: str,
    title: Optional[str],
    mapbox_token: str,
    offline: bool = False,
    copy_media: bool = True,
    verbose: bool = False,
) -> Path:
    """
    Main site generation orchestrator.

    Flow:
    1. Parse GPX files -> Combined HikeData with trackpoints
    2. Parse FIT files -> Combined HR records (if provided)
    3. Merge HR data into trackpoints
    4. Scan media -> MediaItems (if provided)
    5. Merge media with trackpoints
    6. Calculate HR zones and colors
    7. Generate timeline layout
    8. Render templates
    9. Generate JS
    10. Write output files
    11. Copy media assets
    """
    if verbose:
        logging.basicConfig(level=logging.INFO)

    # Setup output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    assets_path = output_path / "assets"
    assets_path.mkdir(exist_ok=True)

    # 1. Parse all GPX files
    import copy

    hike_data = None
    parsed_tracks = []  # Temporarily store parsed data for sorting

    for gpx_path in gpx_paths:
        logger.info(f"Parsing GPX: {gpx_path}")
        gpx_parser = GPXParser(gpx_path)
        parsed_data = gpx_parser.parse()

        track_name = parsed_data.name or Path(gpx_path).stem
        start_time = parsed_data.trackpoints[0].timestamp if parsed_data.trackpoints else None

        parsed_tracks.append({
            'name': track_name,
            'gpx_path': gpx_path,
            'parsed_data': parsed_data,
            'start_time': start_time,
        })

    # Sort tracks by start time to concatenate them in chronological order
    min_dt = datetime.min.replace(tzinfo=tz.utc)
    parsed_tracks.sort(key=lambda t: t['start_time'] if t['start_time'] else min_dt)

    # Track start/end indices for later splitting
    track_boundaries = []
    current_index = 0

    # Now combine tracks in sorted order
    for track_info in parsed_tracks:
        parsed_data = track_info['parsed_data']
        num_points = len(parsed_data.trackpoints)

        track_boundaries.append({
            'name': track_info['name'],
            'gpx_path': track_info['gpx_path'],
            'start_index': current_index,
            'end_index': current_index + num_points,
        })
        current_index += num_points

        if hike_data is None:
            hike_data = parsed_data
        else:
            # Concatenate trackpoints (don't interleave - keep each track's points together)
            hike_data.trackpoints.extend(parsed_data.trackpoints)

    # Recalculate cumulative distances for the concatenated tracks
    if hike_data and hike_data.trackpoints:
        _recalculate_distances(hike_data)
        _recalculate_stats(hike_data)
        logger.info(f"Combined {len(gpx_paths)} GPX files: {len(hike_data.trackpoints)} trackpoints")

    # Override title if provided
    if title:
        hike_data.name = title

    # 2-3. Parse all FIT files and merge HR data
    if fit_paths:
        all_hr_records = []
        for fit_path in fit_paths:
            logger.info(f"Parsing FIT: {fit_path}")
            try:
                fit_parser = FITParser(fit_path)
                hr_records = fit_parser.parse()
                all_hr_records.extend(hr_records)
            except Exception as e:
                logger.warning(f"Could not parse FIT file {fit_path}: {e}")

        if all_hr_records:
            merger = DataMerger(hike_data)
            merger.merge_heart_rate(all_hr_records)
            logger.info(f"Merged {len(all_hr_records)} HR records from {len(fit_paths)} FIT files")

    # 4-5. Scan and merge media
    if media_path:
        logger.info(f"Scanning media: {media_path}")
        try:
            scanner = MediaScanner(media_path)
            media_items = scanner.scan()

            merger = DataMerger(hike_data)
            merger.merge_media(media_items)
            logger.info(f"Found {len(media_items)} media files")
        except Exception as e:
            logger.warning(f"Could not scan media directory: {e}")

    # 6. Calculate HR zones for combined data
    hr_calc = HRZoneCalculator(hike_data)
    hr_calc.calculate_zones()
    gradient_stops = hr_calc.get_gradient_stops()

    # Now create individual_tracks from combined data (AFTER HR merge so trackpoints have HR data)
    individual_tracks = []
    for boundary in track_boundaries:
        track_points = hike_data.trackpoints[boundary['start_index']:boundary['end_index']]
        individual_tracks.append({
            'name': boundary['name'],
            'trackpoints': copy.deepcopy(track_points),  # Deep copy with HR data
            'gpx_path': boundary['gpx_path'],
        })

    # Also calculate HR zones and stats for individual tracks
    for i, track in enumerate(individual_tracks):
        # Create a temporary HikeData for this track
        from ..models.hike_data import HikeData as HikeDataModel, ElevationStats
        trackpoints = track['trackpoints']

        # Recalculate distances for this track independently
        _recalculate_track_distances(trackpoints)

        track_hike = HikeDataModel(trackpoints=trackpoints)
        track_hike.min_hr = hike_data.min_hr
        track_hike.max_hr = hike_data.max_hr
        track_hike.total_distance = trackpoints[-1].distance_from_start if trackpoints else 0

        # Calculate track time range
        if trackpoints:
            track_hike.start_time = trackpoints[0].timestamp
            track_hike.end_time = trackpoints[-1].timestamp
            if track_hike.start_time and track_hike.end_time:
                track_hike.duration = track_hike.end_time - track_hike.start_time

        # Calculate elevation stats for this track
        track_hike.elevation_stats = _calculate_track_elevation_stats(trackpoints)

        track_hr_calc = HRZoneCalculator(track_hike)
        track_hr_calc.calculate_zones()
        track['gradient_stops'] = track_hr_calc.get_gradient_stops()
        track['hike_data'] = track_hike
        track['id'] = f'track-{i}'

        # Store stats for JavaScript
        track['stats'] = {
            'distance_miles': track_hike.distance_miles,
            'duration_seconds': track_hike.duration.total_seconds() if track_hike.duration else 0,
            'pace_min_per_mile': track_hike.pace_min_per_mile,
            'total_ascent_ft': track_hike.elevation_stats.total_ascent_ft if track_hike.elevation_stats else 0,
            'total_descent_ft': track_hike.elevation_stats.total_descent_ft if track_hike.elevation_stats else 0,
            'min_elevation_ft': track_hike.elevation_stats.min_elevation_ft if track_hike.elevation_stats else 0,
            'max_elevation_ft': track_hike.elevation_stats.max_elevation_ft if track_hike.elevation_stats else 0,
            'start_time': track_hike.start_time.isoformat() if track_hike.start_time else None,
            'end_time': track_hike.end_time.isoformat() if track_hike.end_time else None,
        }

        # Store elevation profile data points for chart
        track['elevation_points'] = [
            {'distance': tp.distance_from_start, 'elevation': tp.elevation}
            for tp in trackpoints[::max(1, len(trackpoints) // 100)]  # Sample ~100 points
        ]

    # 7. Separate media into timeline (during hike) and gallery (outside hike)
    timeline_media = [m for m in hike_data.media_items if m.nearest_trackpoint_index is not None]
    gallery_media = [m for m in hike_data.media_items if m.nearest_trackpoint_index is None]

    if gallery_media:
        logger.info(f"Found {len(gallery_media)} media files outside hike time range")

    # Associate media with specific tracks based on timestamp
    _associate_media_with_tracks(hike_data.media_items, individual_tracks)

    # Generate timeline layout only for media during the hike
    timeline_layout = _calculate_timeline_layout(timeline_media)
    timeline_items = _calculate_timeline_items(hike_data, timeline_media)

    # 7b. Detect timezone from GPS coordinates
    local_tz = _detect_timezone(hike_data)
    if local_tz:
        logger.info(f"Detected timezone: {local_tz}")
    else:
        local_tz = pytz.UTC
        logger.info("Using UTC timezone")

    # 8. Setup Jinja environment
    env = Environment(
        loader=PackageLoader("hikevisualizer", "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )

    # Add custom filter for local time conversion
    env.filters['localtime'] = _make_local_time_filter(local_tz)

    # 9. Generate JS
    js_gen = MapboxJSGenerator(hike_data, mapbox_token, offline, individual_tracks)
    js_content = js_gen.generate(gradient_stops)
    track_info = js_gen.get_track_info_for_template()

    # Generate elevation chart data
    chart_width = 800
    chart_height = 200
    elevation_path, elevation_line = _generate_elevation_paths(
        hike_data, chart_width, chart_height
    )

    # Calculate gallery layout for media outside hike time
    gallery_layout = _calculate_gallery_layout(gallery_media, len(timeline_media))

    # 10. Render template
    template = env.get_template("base.html")
    html_content = template.render(
        hike=hike_data,
        timeline_layout=timeline_layout,
        timeline_items=timeline_items,
        media_items=hike_data.media_items,
        gallery_layout=gallery_layout,
        track_info=track_info,
        css_content=_get_css_content(),
        js_content=js_content,
        lightbox_js=_get_lightbox_js(hike_data.media_items),
        chart_width=chart_width,
        chart_height=chart_height,
        elevation_path=elevation_path,
        elevation_line=elevation_line,
    )

    # Write HTML
    index_path = output_path / "index.html"
    index_path.write_text(html_content, encoding="utf-8")
    logger.info(f"Generated HTML: {index_path}")

    # 11. Copy media assets (converting HEIC to JPG)
    if copy_media and hike_data.media_items:
        logger.info(f"Copying {len(hike_data.media_items)} media files")
        for media in hike_data.media_items:
            src = Path(media.file_path)
            dst = assets_path / media.output_filename
            try:
                _copy_media_file(src, dst, logger)
            except Exception as e:
                logger.warning(f"Could not copy {src}: {e}")

    logger.info(f"Site generated at: {output_path}")
    return output_path


def _copy_media_file(src: Path, dst: Path, logger) -> None:
    """Copy a media file, converting HEIC/HEIF to JPG if necessary."""
    src_ext = src.suffix.lower()

    if src_ext in {'.heic', '.heif'}:
        # Convert HEIC to JPG
        try:
            from PIL import Image
            from pillow_heif import register_heif_opener
            register_heif_opener()

            with Image.open(src) as img:
                # Extract EXIF data before any conversion
                exif_data = None
                try:
                    # Try to get EXIF from img.info first (raw bytes)
                    exif_data = img.info.get('exif')

                    # If not available, try getexif() and convert to bytes
                    if not exif_data:
                        exif_obj = img.getexif()
                        if exif_obj:
                            import io
                            exif_bytes = io.BytesIO()
                            exif_obj.save(exif_bytes)
                            exif_data = exif_bytes.getvalue()
                except Exception as exif_err:
                    logger.warning(f"Could not extract EXIF from {src.name}: {exif_err}")

                # Convert to RGB if necessary (HEIC may have alpha)
                rgb_img = img
                if img.mode in ('RGBA', 'P'):
                    rgb_img = img.convert('RGB')
                elif img.mode != 'RGB':
                    rgb_img = img.convert('RGB')

                # Save as JPEG with good quality and EXIF data
                save_kwargs = {'quality': 92}
                if exif_data:
                    save_kwargs['exif'] = exif_data

                rgb_img.save(dst, 'JPEG', **save_kwargs)
                logger.info(f"Converted HEIC to JPG: {src.name} -> {dst.name}")
        except ImportError:
            logger.warning(f"pillow-heif not installed, cannot convert {src.name}")
            # Try to copy anyway in case system has other HEIC support
            shutil.copy2(src, dst)
        except Exception as e:
            logger.warning(f"Failed to convert HEIC {src.name}: {e}")
            raise
    else:
        # Regular copy for non-HEIC files
        shutil.copy2(src, dst)


def _recalculate_distances(hike_data: HikeData) -> None:
    """Recalculate cumulative distances after combining multiple GPX files."""
    import math

    def haversine(lat1, lon1, lat2, lon2):
        R = 6371000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    cumulative = 0.0
    prev = None
    for tp in hike_data.trackpoints:
        if prev:
            cumulative += haversine(prev.latitude, prev.longitude, tp.latitude, tp.longitude)
        tp.distance_from_start = cumulative
        prev = tp

    hike_data.total_distance = cumulative


def _recalculate_stats(hike_data: HikeData) -> None:
    """Recalculate elevation stats and time range after combining files."""
    from ..models.hike_data import ElevationStats

    if not hike_data.trackpoints:
        return

    # Update time range
    hike_data.start_time = hike_data.trackpoints[0].timestamp
    hike_data.end_time = hike_data.trackpoints[-1].timestamp
    if hike_data.start_time and hike_data.end_time:
        hike_data.duration = hike_data.end_time - hike_data.start_time

    # Recalculate elevation stats
    elevations = [tp.elevation for tp in hike_data.trackpoints]

    if not elevations:
        return

    # Smooth elevations
    def smooth(data, window=5):
        if len(data) < window:
            return data
        result = []
        half = window // 2
        for i in range(len(data)):
            start, end = max(0, i - half), min(len(data), i + half + 1)
            result.append(sum(data[start:end]) / (end - start))
        return result

    smoothed = smooth(elevations)
    total_ascent = total_descent = 0.0

    for i in range(1, len(smoothed)):
        diff = smoothed[i] - smoothed[i - 1]
        if diff > 0:
            total_ascent += diff
        elif diff < 0:
            total_descent += abs(diff)

    hike_data.elevation_stats = ElevationStats(
        min_elevation=min(elevations),
        max_elevation=max(elevations),
        total_ascent=round(total_ascent, 1),
        total_descent=round(total_descent, 1),
    )


def _recalculate_track_distances(trackpoints: list) -> None:
    """Recalculate cumulative distances for a single track."""
    import math

    def haversine(lat1, lon1, lat2, lon2):
        R = 6371000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    cumulative = 0.0
    prev = None
    for tp in trackpoints:
        if prev:
            cumulative += haversine(prev.latitude, prev.longitude, tp.latitude, tp.longitude)
        tp.distance_from_start = cumulative
        prev = tp


def _calculate_track_elevation_stats(trackpoints: list):
    """Calculate elevation stats for a single track."""
    from ..models.hike_data import ElevationStats

    if not trackpoints:
        return ElevationStats(
            min_elevation=0, max_elevation=0, total_ascent=0, total_descent=0
        )

    elevations = [tp.elevation for tp in trackpoints]

    def smooth(data, window=5):
        if len(data) < window:
            return data
        result = []
        half = window // 2
        for i in range(len(data)):
            start, end = max(0, i - half), min(len(data), i + half + 1)
            result.append(sum(data[start:end]) / (end - start))
        return result

    smoothed = smooth(elevations)
    total_ascent = total_descent = 0.0

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


def _associate_media_with_tracks(media_items: list, tracks: list) -> None:
    """Associate each media item with the track it belongs to based on timestamp."""
    from datetime import timedelta

    for media in media_items:
        if not media.timestamp:
            media.track_id = None
            continue

        # Find which track this media belongs to
        best_track_id = None
        for track in tracks:
            hike_data = track.get('hike_data')
            if not hike_data or not hike_data.start_time or not hike_data.end_time:
                continue

            # Check if media timestamp falls within this track's time range
            # Allow some buffer (30 minutes before/after)
            buffer = timedelta(minutes=30)
            if (hike_data.start_time - buffer) <= media.timestamp <= (hike_data.end_time + buffer):
                best_track_id = track['id']
                break

        media.track_id = best_track_id


def _calculate_timeline_layout(media_items: List[MediaItem]) -> List[dict]:
    """
    Calculate magazine-style timeline layout.

    Rules:
    - First image is FEATURED (8 columns, 2 rows)
    - Every 5th image is WIDE (6 columns)
    - Landscape images prefer WIDE
    - Videos are always WIDE
    - Audio files are STANDARD with player
    """
    layout = []

    for i, media in enumerate(media_items):
        layout_item = {
            "type": "media",
            "media": media,
            "index": i,
            "class": "media-card--standard",
            "columns": 4,
            "rows": 1,
        }

        # Rule 1: First image is featured
        if i == 0 and media.media_type == MediaType.PHOTO:
            layout_item["class"] = "media-card--featured"
            layout_item["columns"] = 8
            layout_item["rows"] = 2

        # Rule: Videos are wide
        elif media.media_type == MediaType.VIDEO:
            layout_item["class"] = "media-card--wide"
            layout_item["columns"] = 6

        # Rule: Audio stays standard
        elif media.media_type == MediaType.AUDIO:
            layout_item["class"] = "media-card--audio"
            layout_item["columns"] = 4

        # Rule: Every 5th or landscape is wide
        elif media.media_type == MediaType.PHOTO:
            is_landscape = media.is_landscape
            is_fifth = (i % 5 == 0) and i > 0

            if is_fifth or is_landscape:
                layout_item["class"] = "media-card--wide"
                layout_item["columns"] = 6

        layout.append(layout_item)

    # Insert text blocks at intervals
    layout = _insert_text_blocks(layout, interval=3)

    return layout


def _insert_text_blocks(layout: List[dict], interval: int = 3) -> List[dict]:
    """Insert placeholder text blocks for narrative content."""
    result = []
    items_since_text = 0

    for item in layout:
        # Check if we need a text block
        if items_since_text >= interval:
            result.append(
                {
                    "type": "text_block",
                    "class": "text-block",
                    "columns": 4,
                    "placeholder": "Add your story here...",
                }
            )
            items_since_text = 0

        result.append(item)
        if item["type"] == "media":
            items_since_text += 1

    return result


def _calculate_timeline_items(hike_data: HikeData, media_list: List[MediaItem]) -> List[dict]:
    """Calculate vertical timeline item positions."""
    items = []

    if not hike_data.start_time or not hike_data.end_time or not media_list:
        return items

    total_duration = (hike_data.end_time - hike_data.start_time).total_seconds()
    if total_duration == 0:
        return items

    for i, media in enumerate(media_list):
        elapsed = (media.timestamp - hike_data.start_time).total_seconds()
        position = (elapsed / total_duration) * 100
        position = max(5, min(95, position))  # Clamp to avoid edges

        items.append(
            {
                "type": "media",
                "media": media,
                "index": i,
                "position": position,
            }
        )

    return items


def _calculate_gallery_layout(media_items: List[MediaItem], index_offset: int) -> List[dict]:
    """
    Calculate gallery layout for media outside the hike time range.

    Args:
        media_items: Media items that fall outside the hike time
        index_offset: Starting index for these items (to maintain correct lightbox indices)
    """
    layout = []

    for i, media in enumerate(media_items):
        # Calculate global index for lightbox navigation
        global_index = index_offset + i

        layout_item = {
            "type": "media",
            "media": media,
            "index": global_index,
            "class": "gallery-card",
            "columns": 4,
            "rows": 1,
        }

        # Videos are wider
        if media.media_type == MediaType.VIDEO:
            layout_item["class"] = "gallery-card gallery-card--wide"
            layout_item["columns"] = 6

        # Audio stays standard
        elif media.media_type == MediaType.AUDIO:
            layout_item["class"] = "gallery-card gallery-card--audio"

        # Landscape photos are wider
        elif media.media_type == MediaType.PHOTO and media.is_landscape:
            layout_item["class"] = "gallery-card gallery-card--wide"
            layout_item["columns"] = 6

        layout.append(layout_item)

    return layout


def _generate_elevation_paths(
    hike_data: HikeData, width: int, height: int
) -> tuple:
    """Generate SVG paths for elevation chart."""
    if not hike_data.trackpoints or not hike_data.elevation_stats:
        return "", ""

    points = hike_data.trackpoints
    stats = hike_data.elevation_stats
    total_distance = hike_data.total_distance

    if total_distance == 0 or stats.max_elevation == stats.min_elevation:
        return "", ""

    # Calculate path points
    path_points = []
    for tp in points:
        x = (tp.distance_from_start / total_distance) * width
        y_normalized = (tp.elevation - stats.min_elevation) / (
            stats.max_elevation - stats.min_elevation
        )
        y = height - (y_normalized * height * 0.9) - (height * 0.05)
        path_points.append((x, y))

    # Create line path
    line_parts = [f"M {path_points[0][0]:.1f} {path_points[0][1]:.1f}"]
    for x, y in path_points[1:]:
        line_parts.append(f"L {x:.1f} {y:.1f}")
    elevation_line = " ".join(line_parts)

    # Create filled area path
    area_parts = [f"M 0 {height}"]
    area_parts.append(f"L {path_points[0][0]:.1f} {path_points[0][1]:.1f}")
    for x, y in path_points[1:]:
        area_parts.append(f"L {x:.1f} {y:.1f}")
    area_parts.append(f"L {width} {height}")
    area_parts.append("Z")
    elevation_path = " ".join(area_parts)

    return elevation_path, elevation_line


def _get_lightbox_js(media_items: List[MediaItem]) -> str:
    """Generate lightbox JavaScript code."""
    media_data = []
    for media in media_items:
        media_data.append(
            {
                "type": media.media_type.value,
                "src": f"assets/{media.output_filename}",
                "filename": media.filename,
            }
        )

    import json

    media_json = json.dumps(media_data)

    return f"""
const mediaItems = {media_json};
let currentIndex = 0;

function openLightbox(index) {{
    currentIndex = index;
    const lightbox = document.getElementById('lightbox');
    const content = lightbox.querySelector('.lightbox__content');
    const media = mediaItems[index];

    let html = '';
    if (media.type === 'photo') {{
        html = `<img src="${{media.src}}" alt="${{media.filename}}" class="lightbox__image">`;
    }} else if (media.type === 'video') {{
        html = `<video src="${{media.src}}" controls autoplay class="lightbox__video"></video>`;
    }} else {{
        html = `<div class="lightbox__audio">
            <div class="lightbox__audio-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M9 18V5l12-2v13"></path>
                    <circle cx="6" cy="18" r="3"></circle>
                    <circle cx="18" cy="16" r="3"></circle>
                </svg>
            </div>
            <p class="lightbox__audio-filename">${{media.filename}}</p>
            <audio src="${{media.src}}" controls autoplay></audio>
        </div>`;
    }}

    content.innerHTML = html;
    document.getElementById('lightbox-current').textContent = index + 1;
    document.getElementById('lightbox-total').textContent = mediaItems.length;
    lightbox.classList.add('lightbox--active');
    document.body.style.overflow = 'hidden';
}}

function closeLightbox(event) {{
    if (event.target.closest('.lightbox__content') && !event.target.closest('.lightbox__close')) {{
        return;
    }}
    const lightbox = document.getElementById('lightbox');
    lightbox.classList.remove('lightbox--active');
    document.body.style.overflow = '';

    // Stop any playing media
    const video = lightbox.querySelector('video');
    const audio = lightbox.querySelector('audio');
    if (video) video.pause();
    if (audio) audio.pause();
}}

function navigateLightbox(direction, event) {{
    event.stopPropagation();
    const newIndex = currentIndex + direction;
    if (newIndex >= 0 && newIndex < mediaItems.length) {{
        openLightbox(newIndex);
    }}
}}

// Keyboard navigation
document.addEventListener('keydown', (e) => {{
    const lightbox = document.getElementById('lightbox');
    if (!lightbox.classList.contains('lightbox--active')) return;

    if (e.key === 'Escape') closeLightbox(e);
    if (e.key === 'ArrowLeft') navigateLightbox(-1, e);
    if (e.key === 'ArrowRight') navigateLightbox(1, e);
}});
"""


def _get_css_content() -> str:
    """Return the complete CSS stylesheet."""
    return """
/* CSS Reset and Base */
*, *::before, *::after {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

:root {
    --font-sans: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --font-display: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --color-text: #1a1a1a;
    --color-text-muted: #6b7280;
    --color-bg: #ffffff;
    --color-bg-alt: #f9fafb;
    --color-border: #e5e7eb;
    --color-accent: #3b82f6;
    --color-success: #22c55e;
    --color-danger: #ef4444;
    --max-width: 1200px;
    --spacing-unit: 1rem;
}

body {
    font-family: var(--font-sans);
    color: var(--color-text);
    background: var(--color-bg);
    line-height: 1.6;
}

/* Editable placeholder styling */
.editable:empty::before {
    content: attr(data-placeholder);
    color: var(--color-text-muted);
    font-style: italic;
}

.editable:focus {
    outline: 2px dashed var(--color-accent);
    outline-offset: 4px;
}

/* Header Section */
.hike-header {
    max-width: var(--max-width);
    margin: 0 auto;
    padding: calc(var(--spacing-unit) * 4) var(--spacing-unit);
    text-align: center;
}

.hike-header h1 {
    font-family: var(--font-display);
    font-size: clamp(2rem, 5vw, 3.5rem);
    font-weight: 700;
    margin-bottom: var(--spacing-unit);
}

.hike-meta {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
    color: var(--color-text-muted);
    font-size: 1rem;
}

.meta-item {
    display: flex;
    align-items: center;
    gap: 0.25rem;
}

.meta-icon {
    width: 18px;
    height: 18px;
}

.meta-separator {
    color: var(--color-border);
}

/* Section Titles */
.section-title {
    font-family: var(--font-display);
    font-size: 1.75rem;
    font-weight: 700;
    margin-bottom: calc(var(--spacing-unit) * 1.5);
    text-align: center;
}

/* Map Section */
.map-section {
    position: relative;
    width: 100%;
    height: 70vh;
    min-height: 500px;
}

.map-container {
    width: 100%;
    height: 100%;
}

.map-legend {
    position: absolute;
    bottom: 20px;
    left: 20px;
    background: rgba(255, 255, 255, 0.95);
    padding: 12px 16px;
    border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
    font-size: 0.875rem;
}

.legend-gradient {
    width: 200px;
    height: 12px;
    background: linear-gradient(to right, #3B82F6, #22C55E, #EAB308, #F97316, #EF4444);
    border-radius: 4px;
    margin: 8px 0;
}

.legend-labels {
    display: flex;
    justify-content: space-between;
    color: var(--color-text-muted);
    font-size: 0.75rem;
}

/* Track Toggles */
.track-toggles {
    position: absolute;
    top: 20px;
    right: 20px;
    background: rgba(255, 255, 255, 0.95);
    padding: 12px 16px;
    border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
    font-size: 0.875rem;
    display: none;
    max-height: calc(100% - 40px);
    overflow-y: auto;
}

.track-toggles__title {
    display: block;
    font-weight: 600;
    margin-bottom: 8px;
    color: var(--color-text);
}

.track-toggle {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 0;
    cursor: pointer;
    user-select: none;
}

.track-toggle:hover {
    opacity: 0.8;
}

.track-toggle input[type="checkbox"] {
    width: 16px;
    height: 16px;
    cursor: pointer;
}

.track-toggle__name {
    color: var(--color-text);
    font-size: 0.8125rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 150px;
}

/* Track Filter (before Timeline) */
.track-filter {
    max-width: var(--max-width);
    margin: 0 auto;
    padding: calc(var(--spacing-unit) * 2) var(--spacing-unit);
    display: flex;
    align-items: center;
    gap: var(--spacing-unit);
    flex-wrap: wrap;
    border-bottom: 1px solid var(--color-border);
}

.track-filter__label {
    font-weight: 600;
    color: var(--color-text);
    font-size: 0.875rem;
}

.track-filter__options {
    display: flex;
    gap: calc(var(--spacing-unit) * 1.5);
    flex-wrap: wrap;
}

.track-filter__option {
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
    user-select: none;
    padding: 6px 12px;
    background: var(--color-bg-alt);
    border-radius: 20px;
    transition: background 0.2s;
}

.track-filter__option:hover {
    background: var(--color-border);
}

.track-filter__option input[type="checkbox"] {
    width: 14px;
    height: 14px;
    cursor: pointer;
}

.track-filter__name {
    color: var(--color-text);
    font-size: 0.8125rem;
}

/* Summary Stats Section */
.summary-section {
    max-width: var(--max-width);
    margin: 0 auto;
    padding: calc(var(--spacing-unit) * 2) var(--spacing-unit);
}

.summary-stats {
    display: flex;
    justify-content: center;
    gap: calc(var(--spacing-unit) * 3);
    flex-wrap: wrap;
}

.summary-stat {
    display: flex;
    align-items: center;
    gap: calc(var(--spacing-unit) * 0.75);
}

.summary-stat__icon {
    width: 48px;
    height: 48px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
}

.summary-stat__icon svg {
    width: 24px;
    height: 24px;
}

.summary-stat__icon--distance {
    background: var(--color-accent);
}

.summary-stat__icon--pace {
    background: #8B5CF6;
}

.summary-stat__icon--duration {
    background: #EC4899;
}

.summary-stat__content {
    display: flex;
    flex-direction: column;
}

.summary-stat__value {
    font-size: 1.75rem;
    font-weight: 700;
    line-height: 1.2;
    color: var(--color-text);
}

.summary-stat__label {
    font-size: 0.875rem;
    color: var(--color-text-muted);
    text-transform: lowercase;
}

/* Elevation Section */
.elevation-section {
    max-width: var(--max-width);
    margin: 0 auto;
    padding: calc(var(--spacing-unit) * 3) var(--spacing-unit);
}

.elevation-chart {
    position: relative;
    width: 100%;
    height: 200px;
    background: var(--color-bg-alt);
    border-radius: 8px;
    overflow: hidden;
}

.elevation-svg {
    width: 100%;
    height: 100%;
}

.elevation-axis-labels {
    position: absolute;
    left: 8px;
    top: 0;
    bottom: 0;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    padding: 8px 0;
    font-size: 0.75rem;
    color: var(--color-text-muted);
}

.distance-axis-labels {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 4px;
    display: flex;
    justify-content: space-between;
    padding: 0 8px;
    font-size: 0.75rem;
    color: var(--color-text-muted);
}

.elevation-stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: var(--spacing-unit);
    margin-top: calc(var(--spacing-unit) * 1.5);
}

.stat-card {
    background: var(--color-bg-alt);
    padding: var(--spacing-unit);
    border-radius: 8px;
    text-align: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.5rem;
}

.stat-icon {
    width: 32px;
    height: 32px;
    padding: 6px;
    border-radius: 50%;
    background: var(--color-accent);
    color: white;
}

.stat-icon-ascent { background: var(--color-success); }
.stat-icon-descent { background: var(--color-danger); }
.stat-icon-max { background: #8B5CF6; }
.stat-icon-min { background: #06B6D4; }
.stat-icon-hr { background: #EC4899; }

.stat-content {
    display: flex;
    align-items: baseline;
    gap: 4px;
}

.stat-value {
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--color-text);
}

.stat-unit {
    font-size: 0.875rem;
    color: var(--color-text-muted);
}

.stat-label {
    font-size: 0.875rem;
    color: var(--color-text-muted);
}

/* Timeline Section */
.timeline-section {
    max-width: var(--max-width);
    margin: 0 auto;
    padding: calc(var(--spacing-unit) * 4) var(--spacing-unit);
}

.timeline-container {
    display: grid;
    grid-template-columns: 80px 1fr;
    gap: calc(var(--spacing-unit) * 2);
}

/* Vertical Timeline */
.vertical-timeline {
    position: relative;
    height: 100%;
    min-height: 500px;
}

.timeline-line {
    position: absolute;
    left: 50%;
    top: 0;
    bottom: 0;
    width: 3px;
    background: var(--color-border);
    transform: translateX(-50%);
}

.timeline-marker {
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
}

.timeline-marker-start { top: 0; }
.timeline-marker-end { bottom: 0; top: auto; }

.marker-dot {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 3px solid white;
    box-shadow: 0 2px 4px rgba(0,0,0,0.2);
}

.marker-dot-start { background: var(--color-success); }
.marker-dot-end { background: var(--color-danger); }

.marker-connector {
    position: absolute;
    left: 50%;
    width: 20px;
    height: 2px;
    background: var(--color-border);
}

.marker-icon {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: #8B5CF6;
    color: white;
    border: 2px solid white;
    box-shadow: 0 2px 6px rgba(0,0,0,0.2);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: transform 0.2s, box-shadow 0.2s;
}

.marker-icon:hover {
    transform: scale(1.1);
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
}

.marker-icon svg {
    width: 16px;
    height: 16px;
}

.marker-label {
    position: absolute;
    left: calc(100% + 8px);
    white-space: nowrap;
    font-size: 0.75rem;
    color: var(--color-text-muted);
}

.marker-time {
    font-weight: 600;
    color: var(--color-text);
}

.marker-text {
    display: block;
}

/* Magazine Article Grid */
.timeline-article {
    display: grid;
    grid-template-columns: repeat(12, 1fr);
    gap: calc(var(--spacing-unit) * 1.5);
}

/* Media Cards */
.media-card {
    position: relative;
    border-radius: 8px;
    overflow: hidden;
    background: var(--color-bg-alt);
    cursor: pointer;
    transition: transform 0.2s, box-shadow 0.2s;
}

.media-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(0,0,0,0.15);
}

.media-card--featured {
    grid-column: span 8;
    grid-row: span 2;
}

.media-card--standard {
    grid-column: span 4;
}

.media-card--wide {
    grid-column: span 6;
}

.media-card--audio {
    grid-column: span 4;
    cursor: default;
}

.media-card__image,
.media-card__video {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    min-height: 200px;
}

.media-card--featured .media-card__image {
    min-height: 400px;
}

.media-card__video-container {
    position: relative;
    width: 100%;
    height: 100%;
}

.media-card__play-overlay {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: 60px;
    height: 60px;
    background: rgba(0,0,0,0.7);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
}

.media-card__play-overlay svg {
    width: 24px;
    height: 24px;
    margin-left: 4px;
}

.media-card__audio-container {
    padding: var(--spacing-unit);
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.audio-card__icon {
    width: 48px;
    height: 48px;
    background: #8B5CF6;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
}

.audio-card__icon svg {
    width: 24px;
    height: 24px;
}

.audio-card__info {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
}

.audio-card__filename {
    font-weight: 500;
    font-size: 0.875rem;
}

.audio-card__duration {
    font-size: 0.75rem;
    color: var(--color-text-muted);
}

.audio-card__player {
    width: 100%;
}

.media-card__caption {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    padding: var(--spacing-unit);
    background: linear-gradient(transparent, rgba(0, 0, 0, 0.7));
    color: white;
}

.media-card--audio .media-card__caption {
    position: relative;
    background: none;
    color: var(--color-text);
    padding: 0;
}

.media-card__time {
    font-size: 0.75rem;
    opacity: 0.8;
}

.media-card__description {
    font-size: 0.875rem;
    margin-top: 0.25rem;
    min-height: 1.5em;
}

/* Text Blocks */
.text-block {
    grid-column: span 4;
    padding: var(--spacing-unit);
    display: flex;
    align-items: center;
}

.text-block p {
    font-family: var(--font-display);
    font-size: 1.125rem;
    line-height: 1.8;
    min-height: 3em;
}

.no-media-message {
    grid-column: span 12;
    text-align: center;
    padding: calc(var(--spacing-unit) * 4);
    color: var(--color-text-muted);
}

/* Gallery Section (Media outside hike time range) */
.gallery-section {
    max-width: var(--max-width);
    margin: 0 auto;
    padding: calc(var(--spacing-unit) * 4) var(--spacing-unit);
    border-top: 1px solid var(--color-border);
}

.gallery-grid {
    display: grid;
    grid-template-columns: repeat(12, 1fr);
    gap: calc(var(--spacing-unit) * 1.5);
}

.gallery-card {
    position: relative;
    border-radius: 8px;
    overflow: hidden;
    background: var(--color-bg-alt);
    cursor: pointer;
    transition: transform 0.2s, box-shadow 0.2s;
    grid-column: span 4;
}

.gallery-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(0,0,0,0.15);
}

.gallery-card--wide {
    grid-column: span 6;
}

.gallery-card--audio {
    cursor: default;
}

.gallery-card__image,
.gallery-card__video {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    min-height: 200px;
}

.gallery-card__video-container {
    position: relative;
    width: 100%;
    height: 100%;
}

.gallery-card__play-overlay {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: 60px;
    height: 60px;
    background: rgba(0,0,0,0.7);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: white;
}

.gallery-card__play-overlay svg {
    width: 24px;
    height: 24px;
    margin-left: 4px;
}

.gallery-card__audio-container {
    padding: var(--spacing-unit);
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

/* Lightbox */
.lightbox {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.95);
    z-index: 1000;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    visibility: hidden;
    transition: opacity 0.3s, visibility 0.3s;
}

.lightbox--active {
    opacity: 1;
    visibility: visible;
}

.lightbox__close {
    position: absolute;
    top: 20px;
    right: 20px;
    width: 44px;
    height: 44px;
    background: rgba(255,255,255,0.1);
    border: none;
    border-radius: 50%;
    color: white;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.2s;
}

.lightbox__close:hover {
    background: rgba(255,255,255,0.2);
}

.lightbox__close svg {
    width: 24px;
    height: 24px;
}

.lightbox__nav {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    width: 50px;
    height: 50px;
    background: rgba(255,255,255,0.1);
    border: none;
    border-radius: 50%;
    color: white;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.2s;
}

.lightbox__nav:hover {
    background: rgba(255,255,255,0.2);
}

.lightbox__nav--prev { left: 20px; }
.lightbox__nav--next { right: 20px; }

.lightbox__nav svg {
    width: 24px;
    height: 24px;
}

.lightbox__content {
    max-width: 90vw;
    max-height: 90vh;
    display: flex;
    align-items: center;
    justify-content: center;
}

.lightbox__image {
    max-width: 100%;
    max-height: 90vh;
    object-fit: contain;
}

.lightbox__video {
    max-width: 100%;
    max-height: 90vh;
}

.lightbox__audio {
    background: rgba(255,255,255,0.1);
    padding: calc(var(--spacing-unit) * 2);
    border-radius: 12px;
    text-align: center;
    color: white;
}

.lightbox__audio-icon {
    width: 80px;
    height: 80px;
    background: #8B5CF6;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    margin: 0 auto var(--spacing-unit);
}

.lightbox__audio-icon svg {
    width: 40px;
    height: 40px;
}

.lightbox__audio-filename {
    margin-bottom: var(--spacing-unit);
    font-size: 1.125rem;
}

.lightbox__audio audio {
    width: 300px;
}

.lightbox__counter {
    position: absolute;
    bottom: 20px;
    left: 50%;
    transform: translateX(-50%);
    color: rgba(255,255,255,0.7);
    font-size: 0.875rem;
}

/* Responsive */
@media (max-width: 768px) {
    .timeline-container {
        grid-template-columns: 1fr;
    }

    .vertical-timeline {
        display: none;
    }

    .timeline-article {
        grid-template-columns: 1fr;
    }

    .summary-stats {
        gap: calc(var(--spacing-unit) * 2);
    }

    .summary-stat__value {
        font-size: 1.5rem;
    }

    .track-filter {
        flex-direction: column;
        align-items: flex-start;
    }

    .track-filter__options {
        gap: calc(var(--spacing-unit) * 0.75);
    }

    .track-filter__option {
        padding: 4px 10px;
    }

    .media-card--featured,
    .media-card--standard,
    .media-card--wide,
    .media-card--audio,
    .text-block {
        grid-column: span 1;
    }

    .media-card--featured {
        grid-row: span 1;
    }

    .gallery-grid {
        grid-template-columns: 1fr;
    }

    .gallery-card,
    .gallery-card--wide,
    .gallery-card--audio {
        grid-column: span 1;
    }

    .lightbox__nav {
        width: 40px;
        height: 40px;
    }

    .lightbox__nav--prev { left: 10px; }
    .lightbox__nav--next { right: 10px; }
}
"""
