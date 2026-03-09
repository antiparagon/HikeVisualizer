"""
3D Hike Animation Generator

Generates a self-contained HTML file with a Three.js-based 3D flythrough
animation of a hike trail, using GPX track data and optional media files.

Usage:
    uv run hike_animation.py --dir /path/to/hike/
    uv run hike_animation.py --dir /path/to/hike/ --duration 120 --style realistic
"""

import argparse
import json
import logging
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from hikevisualizer.core.data_merger import DataMerger
from hikevisualizer.core.fit_parser import FITParser
from hikevisualizer.core.gpx_parser import GPXParser
from hikevisualizer.core.hr_zones import HRZoneCalculator
from hikevisualizer.core.media_scanner import MediaScanner
from hikevisualizer.models.hike_data import HikeData
from hikevisualizer.models.media_item import MediaType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a 3D hike animation from GPX data and media files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run hike_animation.py --dir ./my-hike
  uv run hike_animation.py --dir ./my-hike --duration 120 --style realistic
  uv run hike_animation.py --dir ./my-hike --trail-color elevation --media-mode pins
        """,
    )
    parser.add_argument("--dir", "-d", required=True, help="Directory to scan for GPX, FIT, and media files")
    parser.add_argument("--output", "-o", default=None, help="Output directory (default: Trail3D/ inside --dir)")
    parser.add_argument("--title", "-t", default=None, help="Custom title for the animation")
    parser.add_argument("--duration", type=int, default=60, help="Animation duration in seconds (default: 60)")
    parser.add_argument("--style", choices=["minimal", "topographic", "realistic"], default="realistic",
                        help="Visual style (default: realistic)")
    parser.add_argument("--trail-color", choices=["hr", "elevation", "solid"], default="hr",
                        help="Trail coloring mode (default: hr)")
    parser.add_argument("--media-mode", choices=["thumbnail", "autopause", "pins"], default="thumbnail",
                        help="Media marker behavior (default: thumbnail)")
    parser.add_argument("--exaggeration", type=float, default=2.0, help="Vertical exaggeration factor (default: 2.0)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    return parser


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def find_files(directory: Path, extensions: List[str]) -> List[Path]:
    files = []
    for ext in extensions:
        files.extend(directory.glob(f"*{ext}"))
        files.extend(directory.glob(f"*{ext.upper()}"))
    return sorted(files)


def discover_files(input_dir: Path) -> dict:
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Directory not found: {input_dir}")

    gpx_files = find_files(input_dir, [".gpx"])
    if not gpx_files:
        raise FileNotFoundError(f"No GPX files found in {input_dir}")

    fit_files = find_files(input_dir, [".fit"])

    print(f"Found {len(gpx_files)} GPX file(s): {', '.join(f.name for f in gpx_files)}")
    if fit_files:
        print(f"Found {len(fit_files)} FIT file(s): {', '.join(f.name for f in fit_files)}")

    return {"gpx": gpx_files, "fit": fit_files, "media_dir": input_dir}


# ---------------------------------------------------------------------------
# Data processing pipeline
# ---------------------------------------------------------------------------

def _recalculate_distances(hike_data: HikeData) -> None:
    """Recalculate cumulative distances after combining multiple GPX files."""
    cumulative = 0.0
    hike_data.trackpoints[0].distance_from_start = 0.0
    for i in range(1, len(hike_data.trackpoints)):
        prev = hike_data.trackpoints[i - 1]
        curr = hike_data.trackpoints[i]
        cumulative += _haversine(prev.latitude, prev.longitude, curr.latitude, curr.longitude)
        curr.distance_from_start = cumulative
    hike_data.total_distance = cumulative


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def process_hike_data(files: dict, title: Optional[str], verbose: bool) -> HikeData:
    """Parse GPX/FIT files, merge data, calculate HR zones."""
    min_dt = datetime.min.replace(tzinfo=timezone.utc)

    # Parse all GPX files
    parsed_tracks = []
    for gpx_path in files["gpx"]:
        logger.info(f"Parsing GPX: {gpx_path}")
        parsed = GPXParser(str(gpx_path)).parse()
        start = parsed.trackpoints[0].timestamp if parsed.trackpoints else None
        parsed_tracks.append({"name": parsed.name or gpx_path.stem, "data": parsed, "start": start})

    parsed_tracks.sort(key=lambda t: t["start"] if t["start"] else min_dt)

    # Combine into single HikeData
    hike_data = parsed_tracks[0]["data"]
    for track in parsed_tracks[1:]:
        hike_data.trackpoints.extend(track["data"].trackpoints)

    if len(parsed_tracks) > 1:
        _recalculate_distances(hike_data)
        # Recalculate elevation stats
        elevations = [tp.elevation for tp in hike_data.trackpoints]
        hike_data.elevation_stats = hike_data.elevation_stats  # keep existing from first parse
        if hike_data.trackpoints:
            hike_data.start_time = hike_data.trackpoints[0].timestamp
            hike_data.end_time = hike_data.trackpoints[-1].timestamp
            if hike_data.start_time and hike_data.end_time:
                hike_data.duration = hike_data.end_time - hike_data.start_time
            hike_data.total_distance = hike_data.trackpoints[-1].distance_from_start

    if title:
        hike_data.name = title

    logger.info(f"Track: {len(hike_data.trackpoints)} points, {hike_data.distance_miles:.1f} miles")

    # Parse FIT files and merge HR
    if files["fit"]:
        all_hr = []
        for fit_path in files["fit"]:
            try:
                all_hr.extend(FITParser(str(fit_path)).parse())
            except Exception as e:
                logger.warning(f"Could not parse FIT {fit_path}: {e}")
        if all_hr:
            DataMerger(hike_data).merge_heart_rate(all_hr)
            logger.info(f"Merged {len(all_hr)} HR records")

    # Scan and merge media
    scanner = MediaScanner(str(files["media_dir"]), exclude_dirs=["output", "assets", "Trail3D"])
    media_items = scanner.scan()
    if media_items:
        DataMerger(hike_data).merge_media(media_items)
        logger.info(f"Found {len(media_items)} media files")

    # Calculate HR zones
    HRZoneCalculator(hike_data).calculate_zones()

    return hike_data


# ---------------------------------------------------------------------------
# Coordinate conversion
# ---------------------------------------------------------------------------

def _smooth_array(values: List[float], window: int) -> List[float]:
    """Apply moving-average smoothing with the given window size."""
    if len(values) <= window:
        return list(values)
    half = window // 2
    out = []
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def convert_to_local_xyz(hike_data: HikeData, vertical_exaggeration: float = 2.0) -> List[dict]:
    """Convert lat/lon/elevation to local XYZ meters centered on trail midpoint.

    Returns both raw and smoothed coordinate arrays.  The smoothed arrays
    are used by the JS side for the camera path so the flythrough feels
    cinematic rather than jittery from GPS noise.
    """
    tps = hike_data.trackpoints
    if not tps:
        return []

    center_lat = sum(tp.latitude for tp in tps) / len(tps)
    center_lon = sum(tp.longitude for tp in tps) / len(tps)
    center_lat_rad = math.radians(center_lat)
    min_elev = min(tp.elevation for tp in tps)

    raw_x, raw_y, raw_z = [], [], []
    for tp in tps:
        raw_x.append((tp.longitude - center_lon) * math.cos(center_lat_rad) * 111320)
        raw_z.append((tp.latitude - center_lat) * 110540)
        raw_y.append((tp.elevation - min_elev) * vertical_exaggeration)

    # Light smoothing for the display trail (removes GPS jitter but keeps shape)
    trail_window = max(3, len(tps) // 80)  # ~1-2% of points
    sx = _smooth_array(raw_x, trail_window)
    sy = _smooth_array(raw_y, trail_window)
    sz = _smooth_array(raw_z, trail_window)

    # Heavy smoothing for the camera path (very fluid motion)
    cam_window = max(7, len(tps) // 20)  # ~5% of points
    cx = _smooth_array(raw_x, cam_window)
    cy = _smooth_array(raw_y, cam_window)
    cz = _smooth_array(raw_z, cam_window)

    points = []
    for i in range(len(tps)):
        points.append({
            "x": round(sx[i], 2),
            "y": round(sy[i], 2),
            "z": round(sz[i], 2),
            # Smoothed camera-path coordinates
            "cx": round(cx[i], 2),
            "cy": round(cy[i], 2),
            "cz": round(cz[i], 2),
            "hr_color": tps[i].hr_color,
            "elevation": tps[i].elevation,
        })

    return points


# ---------------------------------------------------------------------------
# Trail color preparation
# ---------------------------------------------------------------------------

def prepare_trail_colors(hike_data: HikeData, xyz_points: List[dict]) -> dict:
    """Prepare color arrays for all three coloring modes."""
    tps = hike_data.trackpoints
    n = len(tps)

    # HR colors (already calculated by HRZoneCalculator)
    hr_colors = [tp.hr_color or "#9CA3AF" for tp in tps]

    # Elevation gradient
    elevations = [tp.elevation for tp in tps]
    min_e, max_e = min(elevations), max(elevations)
    elev_range = max_e - min_e if max_e > min_e else 1.0
    elev_colors = []
    for tp in tps:
        pct = (tp.elevation - min_e) / elev_range
        pct = max(0.0, min(1.0, pct))
        elev_colors.append(_lerp_color("#3B82F6", "#EF4444", pct))

    # Solid
    solid_colors = ["#3B82F6"] * n

    return {"hr": hr_colors, "elevation": elev_colors, "solid": solid_colors}


def _lerp_color(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------------------------------------------------------------------------
# Media data preparation
# ---------------------------------------------------------------------------

def prepare_media_data(hike_data: HikeData, xyz_points: List[dict]) -> List[dict]:
    """Build media marker data with 3D positions."""
    media_data = []
    for media in hike_data.media_items:
        idx = media.nearest_trackpoint_index
        if idx is None or idx >= len(xyz_points):
            continue
        pt = xyz_points[idx]
        media_data.append({
            "x": pt["x"],
            "y": pt["y"],
            "z": pt["z"],
            "trackpoint_index": idx,
            "filename": media.filename,
            "output_filename": media.output_filename,
            "media_type": media.media_type.value,
            "is_360": media.is_360,
            "duration": media.duration_seconds,
        })
    return media_data


# ---------------------------------------------------------------------------
# Media asset copying
# ---------------------------------------------------------------------------

def copy_media_assets(hike_data: HikeData, output_dir: Path) -> None:
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(exist_ok=True)

    for media in hike_data.media_items:
        if media.nearest_trackpoint_index is None:
            continue
        src = Path(media.file_path)
        dst = assets_dir / media.output_filename
        try:
            src_ext = src.suffix.lower()
            if src_ext in {".heic", ".heif"}:
                _convert_heic(src, dst)
            else:
                shutil.copy2(src, dst)
        except Exception as e:
            logger.warning(f"Could not copy {src.name}: {e}")


def _convert_heic(src: Path, dst: Path) -> None:
    from PIL import Image
    from pillow_heif import register_heif_opener
    register_heif_opener()
    with Image.open(src) as img:
        rgb = img.convert("RGB") if img.mode != "RGB" else img
        exif_data = img.info.get("exif")
        save_kwargs = {"quality": 92}
        if exif_data:
            save_kwargs["exif"] = exif_data
        rgb.save(dst, "JPEG", **save_kwargs)


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def generate_html(
    hike_data: HikeData,
    xyz_points: List[dict],
    trail_colors: dict,
    media_data: List[dict],
    config: dict,
) -> str:
    """Generate the complete self-contained HTML file."""
    # Prepare data for JSON embedding
    # Trail points (smoothed display path) and camera points (heavily smoothed)
    js_points = [{"x": p["x"], "y": p["y"], "z": p["z"]} for p in xyz_points]
    cam_points = [{"x": p["cx"], "y": p["cy"], "z": p["cz"]} for p in xyz_points]

    trail_data = json.dumps(js_points)
    cam_data = json.dumps(cam_points)
    color_data = json.dumps(trail_colors)
    media_json = json.dumps(media_data)

    # Hike metadata
    meta = {
        "name": hike_data.name,
        "distance_miles": round(hike_data.distance_miles, 1),
        "duration": hike_data.duration_formatted,
        "elevation_gain_ft": round(hike_data.elevation_stats.total_ascent_ft) if hike_data.elevation_stats else 0,
        "max_elevation_ft": round(hike_data.elevation_stats.max_elevation_ft) if hike_data.elevation_stats else 0,
        "total_points": len(hike_data.trackpoints),
    }
    meta_json = json.dumps(meta)
    config_json = json.dumps(config)

    return _HTML_TEMPLATE.replace("__TRAIL_DATA__", trail_data) \
        .replace("__CAM_DATA__", cam_data) \
        .replace("__COLOR_DATA__", color_data) \
        .replace("__MEDIA_DATA__", media_json) \
        .replace("__HIKE_META__", meta_json) \
        .replace("__CONFIG__", config_json)


# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trail 3D Animation</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#000}
#canvas-container{width:100vw;height:100vh;position:relative}

/* Bottom controls */
#controls{
  position:fixed;bottom:0;left:0;right:0;
  display:flex;align-items:center;gap:12px;
  padding:12px 20px;
  background:rgba(0,0,0,0.75);backdrop-filter:blur(10px);
  color:#fff;z-index:10;font-size:13px;
}
#play-pause{
  width:36px;height:36px;border:none;border-radius:50%;
  background:rgba(255,255,255,0.15);color:#fff;font-size:16px;
  cursor:pointer;display:flex;align-items:center;justify-content:center;
  flex-shrink:0;transition:background .2s;
}
#play-pause:hover{background:rgba(255,255,255,0.25)}
#progress{flex:1;height:4px;-webkit-appearance:none;appearance:none;background:rgba(255,255,255,0.2);border-radius:2px;cursor:pointer}
#progress::-webkit-slider-thumb{-webkit-appearance:none;width:14px;height:14px;border-radius:50%;background:#ff6b35;cursor:pointer}
#progress::-moz-range-thumb{width:14px;height:14px;border-radius:50%;background:#ff6b35;border:none;cursor:pointer}
#time-display{min-width:90px;text-align:center;font-variant-numeric:tabular-nums}
.speed-control{display:flex;align-items:center;gap:6px;flex-shrink:0}
.speed-control label{font-size:12px;opacity:0.7}
#speed{width:80px;height:4px;-webkit-appearance:none;appearance:none;background:rgba(255,255,255,0.2);border-radius:2px}
#speed::-webkit-slider-thumb{-webkit-appearance:none;width:12px;height:12px;border-radius:50%;background:#fff;cursor:pointer}
#speed::-moz-range-thumb{width:12px;height:12px;border-radius:50%;background:#fff;border:none;cursor:pointer}
#speed-val{font-size:12px;min-width:28px;text-align:right}

/* Top-right settings */
#settings{
  position:fixed;top:16px;right:16px;
  display:flex;flex-direction:column;gap:6px;z-index:10;
}
.btn-group{
  display:flex;gap:2px;
  background:rgba(0,0,0,0.6);backdrop-filter:blur(10px);
  border-radius:8px;padding:3px;
}
.btn-group button{
  padding:5px 10px;border:none;border-radius:6px;
  background:transparent;color:rgba(255,255,255,0.7);cursor:pointer;
  font-size:11px;white-space:nowrap;transition:all .2s;
}
.btn-group button:hover{color:#fff;background:rgba(255,255,255,0.1)}
.btn-group button.active{background:rgba(255,255,255,0.2);color:#fff}

/* Top-left info */
#info-panel{
  position:fixed;top:16px;left:16px;z-index:10;
  background:rgba(0,0,0,0.6);backdrop-filter:blur(10px);
  border-radius:10px;padding:14px 18px;color:#fff;
  max-width:280px;
}
#info-panel h2{font-size:15px;font-weight:600;margin-bottom:6px}
.info-stats{display:flex;flex-wrap:wrap;gap:8px 16px;font-size:12px;opacity:0.8}
.info-stat span{display:block;font-size:14px;font-weight:500;opacity:1}

/* Media popup overlay */
#media-popup{
  display:none;position:fixed;inset:0;z-index:30;
  background:rgba(0,0,0,0.85);backdrop-filter:blur(4px);
  align-items:center;justify-content:center;
}
#media-popup.visible{display:flex}
#media-popup-content{max-width:90vw;max-height:85vh;position:relative}
#media-popup-content img{max-width:90vw;max-height:85vh;object-fit:contain;border-radius:8px}
#media-popup-content video{max-width:90vw;max-height:85vh;border-radius:8px}
#media-popup-close{
  position:absolute;top:16px;right:16px;width:40px;height:40px;
  border:none;border-radius:50%;background:rgba(255,255,255,0.2);
  color:#fff;font-size:24px;cursor:pointer;z-index:31;
  display:flex;align-items:center;justify-content:center;
}

/* Loading overlay */
#loading{
  position:fixed;inset:0;z-index:50;background:#111;
  display:flex;align-items:center;justify-content:center;
  color:#fff;font-size:16px;flex-direction:column;gap:12px;
}
.spinner{width:36px;height:36px;border:3px solid rgba(255,255,255,0.2);
  border-top-color:#ff6b35;border-radius:50%;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>

<div id="loading"><div class="spinner"></div><div>Loading trail data...</div></div>
<div id="canvas-container"></div>

<!-- Info panel -->
<div id="info-panel">
  <h2 id="hike-title"></h2>
  <div class="info-stats">
    <div class="info-stat"><span id="stat-dist"></span>miles</div>
    <div class="info-stat"><span id="stat-dur"></span>duration</div>
    <div class="info-stat"><span id="stat-elev"></span>ft gain</div>
  </div>
</div>

<!-- Settings -->
<div id="settings">
  <div class="btn-group" id="camera-group">
    <button data-cam="follow" class="active">Follow</button>
    <button data-cam="overhead">Overhead</button>
    <button data-cam="side">Side</button>
    <button data-cam="fpv">FPV</button>
  </div>
  <div class="btn-group" id="style-group">
    <button data-style="minimal">Minimal</button>
    <button data-style="topographic">Topo</button>
    <button data-style="realistic" class="active">Realistic</button>
  </div>
  <div class="btn-group" id="color-group">
    <button data-color="hr" class="active">HR Zones</button>
    <button data-color="elevation">Elevation</button>
    <button data-color="solid">Solid</button>
  </div>
</div>

<!-- Controls -->
<div id="controls">
  <button id="play-pause" title="Play/Pause">&#9654;</button>
  <input type="range" id="progress" min="0" max="1" step="0.0005" value="0">
  <div id="time-display">0:00 / 0:00</div>
  <div class="speed-control">
    <label>Speed</label>
    <input type="range" id="speed" min="1" max="50" step="1" value="1">
    <span id="speed-val">1x</span>
  </div>
</div>

<!-- Media popup -->
<div id="media-popup">
  <button id="media-popup-close">&times;</button>
  <div id="media-popup-content"></div>
</div>

<script type="importmap">
{
  "imports": {
    "three": "https://cdn.jsdelivr.net/npm/three@0.169.0/build/three.module.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.169.0/examples/jsm/"
  }
}
</script>

<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';

// === EMBEDDED DATA ===
const TRAIL_POINTS = __TRAIL_DATA__;
const CAM_POINTS = __CAM_DATA__;
const TRAIL_COLORS = __COLOR_DATA__;
const MEDIA_MARKERS = __MEDIA_DATA__;
const HIKE_META = __HIKE_META__;
const CONFIG = __CONFIG__;

// === STATE ===
let isPlaying = false;
let progress = 0;
let speedMultiplier = 1;
let currentCamPreset = CONFIG.style === 'minimal' ? 'follow' : 'follow';
let currentStyle = CONFIG.style;
let currentColorMode = CONFIG.trailColor;
let animationDuration = CONFIG.duration;

// Camera smoothing state (frame-rate independent exponential smoothing)
const camPos = new THREE.Vector3();
const camTarget = new THREE.Vector3();
let camInitialized = false;
// Smoothing half-life in seconds — larger = smoother/slower response
const CAM_POS_HALFLIFE = 0.6;   // camera position smoothing
const CAM_TARGET_HALFLIFE = 0.4; // look-at target smoothing
// Hiker sphere smoothing (lighter than camera for responsiveness)
const hikerSmoothedPos = new THREE.Vector3();
let hikerInitialized = false;
const HIKER_HALFLIFE = 0.15;

// Media trigger tracking
const triggeredMedia = new Set();
const TRIGGER_THRESHOLD = 0.006;

// === SCENE SETUP ===
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 100000);
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.6;
document.getElementById('canvas-container').appendChild(renderer.domElement);

// CSS2D renderer for labels
const labelRenderer = new CSS2DRenderer();
labelRenderer.setSize(window.innerWidth, window.innerHeight);
labelRenderer.domElement.style.position = 'absolute';
labelRenderer.domElement.style.top = '0';
labelRenderer.domElement.style.pointerEvents = 'none';
document.getElementById('canvas-container').appendChild(labelRenderer.domElement);

// Orbit controls
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.enabled = true;
controls.maxDistance = 50000;

// === FRAME-RATE INDEPENDENT SMOOTHING HELPER ===
// Attempt to reach the target by halving the error every `halflife` seconds.
function expSmoothFactor(halflife, dt) {
    if (halflife <= 0) return 1;
    return 1 - Math.pow(0.5, dt / halflife);
}

// === BUILD TRAIL CURVE (display — lightly smoothed in Python) ===
const curvePoints = TRAIL_POINTS.map(p => new THREE.Vector3(p.x, p.y, p.z));

// Downsample if too many points (keep curve smooth but manageable)
function downsample(pts, maxCount) {
    if (pts.length <= maxCount) return pts;
    const step = Math.ceil(pts.length / maxCount);
    const out = pts.filter((_, i) => i % step === 0);
    if (out[out.length - 1] !== pts[pts.length - 1]) out.push(pts[pts.length - 1]);
    return out;
}

const trailCurve = new THREE.CatmullRomCurve3(
    downsample(curvePoints, 2000), false, 'centripetal', 0.5
);

// === BUILD CAMERA CURVE (heavily smoothed in Python — very fluid) ===
const camCurvePoints = CAM_POINTS.map(p => new THREE.Vector3(p.x, p.y, p.z));
const cameraCurve = new THREE.CatmullRomCurve3(
    downsample(camCurvePoints, 2000), false, 'centripetal', 0.5
);

// Calculate trail extent for sizing
const trailBBox = new THREE.Box3();
curvePoints.forEach(p => trailBBox.expandByPoint(p));
const trailSize = new THREE.Vector3();
trailBBox.getSize(trailSize);
const trailCenter = new THREE.Vector3();
trailBBox.getCenter(trailCenter);
const maxExtent = Math.max(trailSize.x, trailSize.z, 100);

// === TRAIL MESH ===
let trailMesh = null;
let trailLine = null;

function buildTrailColors(mode) {
    const colors = TRAIL_COLORS[mode];
    const result = [];
    for (let i = 0; i < colors.length; i++) {
        const hex = colors[i];
        const r = parseInt(hex.slice(1, 3), 16) / 255;
        const g = parseInt(hex.slice(3, 5), 16) / 255;
        const b = parseInt(hex.slice(5, 7), 16) / 255;
        result.push(r, g, b);
    }
    return result;
}

function createTrailGeometry(style, colorMode) {
    // Remove old trail
    if (trailMesh) { scene.remove(trailMesh); trailMesh.geometry.dispose(); trailMesh = null; }
    if (trailLine) { scene.remove(trailLine); trailLine.geometry.dispose(); trailLine = null; }

    const rawColors = buildTrailColors(colorMode);
    const numSamples = Math.min(curvePoints.length, 2000);

    if (style === 'minimal') {
        // Line geometry
        const pts = trailCurve.getPoints(numSamples);
        const geom = new THREE.BufferGeometry().setFromPoints(pts);
        // Interpolate colors to match sampled points
        const colorArr = [];
        for (let i = 0; i <= numSamples; i++) {
            const t = i / numSamples;
            const srcIdx = Math.min(Math.floor(t * (TRAIL_POINTS.length - 1)), TRAIL_POINTS.length - 1);
            colorArr.push(rawColors[srcIdx * 3], rawColors[srcIdx * 3 + 1], rawColors[srcIdx * 3 + 2]);
        }
        geom.setAttribute('color', new THREE.Float32BufferAttribute(colorArr, 3));
        const mat = new THREE.LineBasicMaterial({ vertexColors: true, linewidth: 2 });
        trailLine = new THREE.Line(geom, mat);
        scene.add(trailLine);
    } else {
        // Tube geometry
        const radius = style === 'realistic' ? maxExtent * 0.003 : maxExtent * 0.002;
        const tubularSegments = numSamples;
        const radialSegments = 8;
        const geom = new THREE.TubeGeometry(trailCurve, tubularSegments, radius, radialSegments, false);

        // Apply vertex colors - each ring of radialSegments+1 gets the same color
        const posCount = geom.attributes.position.count;
        const vertsPerRing = radialSegments + 1;
        const numRings = Math.floor(posCount / vertsPerRing);
        const colorArr = [];

        for (let ring = 0; ring < numRings; ring++) {
            const t = ring / (numRings - 1);
            const srcIdx = Math.min(Math.floor(t * (TRAIL_POINTS.length - 1)), TRAIL_POINTS.length - 1);
            const r = rawColors[srcIdx * 3] || 0.23;
            const g = rawColors[srcIdx * 3 + 1] || 0.51;
            const b = rawColors[srcIdx * 3 + 2] || 0.96;
            for (let v = 0; v < vertsPerRing; v++) {
                colorArr.push(r, g, b);
            }
        }
        // Fill any remaining vertices
        while (colorArr.length < posCount * 3) {
            colorArr.push(0.23, 0.51, 0.96);
        }

        geom.setAttribute('color', new THREE.Float32BufferAttribute(colorArr, 3));
        const mat = new THREE.MeshStandardMaterial({
            vertexColors: true, roughness: 0.5, metalness: 0.1,
        });
        trailMesh = new THREE.Mesh(geom, mat);
        trailMesh.castShadow = true;
        scene.add(trailMesh);
    }
}

// === HIKER SPHERE ===
const hikerRadius = maxExtent * 0.006;
const hikerGeom = new THREE.SphereGeometry(hikerRadius, 20, 20);
const hikerMat = new THREE.MeshStandardMaterial({
    color: 0xff6b35, emissive: 0xff6b35, emissiveIntensity: 0.4,
});
const hiker = new THREE.Mesh(hikerGeom, hikerMat);
hiker.castShadow = true;
scene.add(hiker);

// Hiker glow
const glowGeom = new THREE.SphereGeometry(hikerRadius * 2, 16, 16);
const glowMat = new THREE.MeshBasicMaterial({
    color: 0xff6b35, transparent: true, opacity: 0.15,
});
const hikerGlow = new THREE.Mesh(glowGeom, glowMat);
scene.add(hikerGlow);

function updateHikerPosition(t, dt) {
    const clamped = Math.max(0, Math.min(1, t));
    const point = trailCurve.getPointAt(clamped);
    point.y += hikerRadius * 1.2;

    if (!hikerInitialized || dt === undefined) {
        hikerSmoothedPos.copy(point);
        hikerInitialized = true;
    } else {
        const f = expSmoothFactor(HIKER_HALFLIFE, dt);
        hikerSmoothedPos.lerp(point, f);
    }
    hiker.position.copy(hikerSmoothedPos);
    hikerGlow.position.copy(hikerSmoothedPos);
}

// === GROUND PLANE (dark gallery floor) ===
let ground = null;
let gridHelper = null;

function createGround(style) {
    if (ground) { scene.remove(ground); ground.geometry.dispose(); ground = null; }
    if (gridHelper) { scene.remove(gridHelper); gridHelper = null; }

    const size = maxExtent * 4;
    const floorColor = style === 'minimal' ? 0x686868 : style === 'topographic' ? 0x707070 : 0x585858;

    if (style === 'minimal') {
        gridHelper = new THREE.GridHelper(size, 60, 0x888888, 0x777777);
        gridHelper.position.y = -1;
        gridHelper.position.x = trailCenter.x;
        gridHelper.position.z = trailCenter.z;
        scene.add(gridHelper);
    } else if (style === 'topographic') {
        gridHelper = new THREE.GridHelper(size, 80, 0x888888, 0x787878);
        gridHelper.position.y = -1;
        gridHelper.position.x = trailCenter.x;
        gridHelper.position.z = trailCenter.z;
        scene.add(gridHelper);
    }

    // Gallery floor for all styles
    const geom = new THREE.PlaneGeometry(size, size);
    geom.rotateX(-Math.PI / 2);
    const mat = new THREE.MeshStandardMaterial({
        color: floorColor, roughness: 0.35, metalness: 0.05,
    });
    ground = new THREE.Mesh(geom, mat);
    ground.position.set(trailCenter.x, -2, trailCenter.z);
    ground.receiveShadow = true;
    scene.add(ground);
}

// === LIGHTING (gallery / museum ambience) ===
let lights = [];

function setupLighting(style) {
    lights.forEach(l => scene.remove(l));
    lights = [];

    if (style === 'minimal') {
        scene.background = new THREE.Color(0x4a4a4a);
        const ambient = new THREE.AmbientLight(0xffffff, 0.9);
        const dir = new THREE.DirectionalLight(0xffffff, 0.7);
        dir.position.set(maxExtent, maxExtent * 2, maxExtent);
        lights.push(ambient, dir);
        scene.add(ambient, dir);
    } else if (style === 'topographic') {
        scene.background = new THREE.Color(0x454545);
        const ambient = new THREE.AmbientLight(0xfff8f0, 0.8);
        const dir = new THREE.DirectionalLight(0xfff5e6, 0.9);
        dir.position.set(maxExtent, maxExtent * 2, maxExtent * 0.5);
        dir.castShadow = true;
        dir.shadow.mapSize.width = 2048;
        dir.shadow.mapSize.height = 2048;
        lights.push(ambient, dir);
        scene.add(ambient, dir);
    } else {
        // Realistic — museum lighting (bright gallery)
        scene.background = new THREE.Color(0x3e3e3e);
        const hemi = new THREE.HemisphereLight(0x444466, 0x333333, 0.7);
        const sun = new THREE.DirectionalLight(0xfff5e6, 1.2);
        sun.position.set(maxExtent * 0.5, maxExtent * 1.5, maxExtent * 0.8);
        sun.castShadow = true;
        sun.shadow.mapSize.width = 2048;
        sun.shadow.mapSize.height = 2048;
        const shadowSize = maxExtent * 2;
        sun.shadow.camera.left = -shadowSize;
        sun.shadow.camera.right = shadowSize;
        sun.shadow.camera.top = shadowSize;
        sun.shadow.camera.bottom = -shadowSize;
        sun.shadow.camera.far = maxExtent * 5;
        // Warm fill light from below (gallery floor bounce)
        const fill = new THREE.DirectionalLight(0xffeedd, 0.35);
        fill.position.set(0, -maxExtent, 0);
        const ambient = new THREE.AmbientLight(0xfff8f0, 0.5);
        lights.push(hemi, sun, fill, ambient);
        scene.add(hemi, sun, fill, ambient);
    }
}

// === GALLERY MEDIA DISPLAYS ===
const mediaMarkerGroup = new THREE.Group();
const markerMeshes = [];  // clickable meshes for raycaster
const gallerySpotlights = [];

function createMediaMarkers() {
    // Clear existing
    while (mediaMarkerGroup.children.length > 0) {
        const child = mediaMarkerGroup.children[0];
        mediaMarkerGroup.remove(child);
        if (child.geometry) child.geometry.dispose();
    }
    markerMeshes.length = 0;
    gallerySpotlights.forEach(s => scene.remove(s));
    gallerySpotlights.length = 0;

    // Gallery sizing relative to trail scale
    const frameW = maxExtent * 0.04;   // frame width
    const frameH = frameW * 0.75;      // 4:3 aspect
    const frameDepth = frameW * 0.03;  // thin frame
    const wallW = frameW * 1.6;        // wall panel behind frame
    const wallH = frameH * 1.8;
    const wallDepth = frameDepth * 0.5;
    const standoffDist = maxExtent * 0.06; // distance from trail

    MEDIA_MARKERS.forEach((media, index) => {
        const group = new THREE.Group();

        // Determine wall position: offset perpendicular to trail direction
        // Alternate sides for variety
        const t = media.trackpoint_index / Math.max(1, TRAIL_POINTS.length - 1);
        const trailPt = trailCurve.getPointAt(Math.max(0, Math.min(1, t)));
        const tangent = getSmoothTangent(Math.max(0.001, Math.min(0.999, t)));
        const up = new THREE.Vector3(0, 1, 0);
        const perpendicular = new THREE.Vector3().crossVectors(tangent, up).normalize();
        const side = (index % 2 === 0) ? 1 : -1;

        // Wall position: next to trail, at a comfortable viewing height
        const wallPos = trailPt.clone()
            .add(perpendicular.clone().multiplyScalar(standoffDist * side));
        wallPos.y = Math.max(trailPt.y, 0) + wallH * 0.5 + hikerRadius * 2;

        // Face the wall toward the trail
        const faceDir = trailPt.clone().sub(wallPos);
        faceDir.y = 0;
        faceDir.normalize();
        const angle = Math.atan2(faceDir.x, faceDir.z);

        // --- Wall panel (dark gallery wall) ---
        const wallGeom = new THREE.BoxGeometry(wallW, wallH, wallDepth);
        const wallMat = new THREE.MeshStandardMaterial({
            color: 0x454545, roughness: 0.85, metalness: 0.0,
        });
        const wall = new THREE.Mesh(wallGeom, wallMat);
        wall.castShadow = true;
        wall.receiveShadow = true;
        group.add(wall);

        // --- Frame (elegant dark wood / black) ---
        const frameBorder = frameW * 0.04;
        const outerW = frameW + frameBorder * 2;
        const outerH = frameH + frameBorder * 2;
        const frameGeom = new THREE.BoxGeometry(outerW, outerH, frameDepth * 1.5);
        const frameMat = new THREE.MeshStandardMaterial({
            color: 0x1a1a1a, roughness: 0.3, metalness: 0.4,
        });
        const frame = new THREE.Mesh(frameGeom, frameMat);
        frame.position.z = wallDepth * 0.5 + frameDepth * 0.5;
        frame.castShadow = true;
        group.add(frame);

        // --- Photo/image plane (textured with the actual image) ---
        const imgGeom = new THREE.PlaneGeometry(frameW, frameH);
        let imgMat;
        if (media.media_type === 'photo') {
            const texture = new THREE.TextureLoader().load('assets/' + media.output_filename);
            texture.colorSpace = THREE.SRGBColorSpace;
            imgMat = new THREE.MeshStandardMaterial({
                map: texture, roughness: 0.4, metalness: 0.0,
            });
        } else if (media.media_type === 'video') {
            // Video thumbnail placeholder — dark with play icon
            const canvas = document.createElement('canvas');
            canvas.width = 256; canvas.height = 192;
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = '#1a1a2e';
            ctx.fillRect(0, 0, 256, 192);
            // Play triangle
            ctx.fillStyle = '#ffffff';
            ctx.beginPath();
            ctx.moveTo(100, 56); ctx.lineTo(100, 136); ctx.lineTo(170, 96);
            ctx.closePath(); ctx.fill();
            // Label
            ctx.fillStyle = '#888';
            ctx.font = '14px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('VIDEO', 128, 176);
            const tex = new THREE.CanvasTexture(canvas);
            tex.colorSpace = THREE.SRGBColorSpace;
            imgMat = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.4 });
        } else {
            // Audio placeholder
            const canvas = document.createElement('canvas');
            canvas.width = 256; canvas.height = 192;
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = '#1a1a2e';
            ctx.fillRect(0, 0, 256, 192);
            ctx.fillStyle = '#888';
            ctx.font = '48px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('\u266B', 128, 110);
            ctx.font = '14px sans-serif';
            ctx.fillText('AUDIO', 128, 150);
            const tex = new THREE.CanvasTexture(canvas);
            imgMat = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.4 });
        }
        const imgPlane = new THREE.Mesh(imgGeom, imgMat);
        imgPlane.position.z = wallDepth * 0.5 + frameDepth * 1.6;
        imgPlane.userData = { mediaIndex: index, ...media };
        group.add(imgPlane);
        markerMeshes.push(imgPlane); // clickable

        // --- Small label plaque beneath frame ---
        const plaqueCanvas = document.createElement('canvas');
        plaqueCanvas.width = 256; plaqueCanvas.height = 48;
        const pCtx = plaqueCanvas.getContext('2d');
        pCtx.fillStyle = '#c0a060';
        pCtx.fillRect(0, 0, 256, 48);
        pCtx.fillStyle = '#1a1a1a';
        pCtx.font = 'bold 16px sans-serif';
        pCtx.textAlign = 'center';
        const label = media.filename.length > 28 ? media.filename.substring(0, 25) + '...' : media.filename;
        pCtx.fillText(label, 128, 30);
        const plaqueTex = new THREE.CanvasTexture(plaqueCanvas);
        const plaqueGeom = new THREE.PlaneGeometry(frameW * 0.6, frameW * 0.08);
        const plaqueMat = new THREE.MeshStandardMaterial({
            map: plaqueTex, roughness: 0.3, metalness: 0.6,
        });
        const plaque = new THREE.Mesh(plaqueGeom, plaqueMat);
        plaque.position.y = -(outerH * 0.5 + frameW * 0.06);
        plaque.position.z = wallDepth * 0.5 + frameDepth * 0.5;
        group.add(plaque);

        // Position and orient the whole group
        group.position.copy(wallPos);
        group.rotation.y = angle;
        mediaMarkerGroup.add(group);

        // --- Gallery spotlight aimed at each frame ---
        const spotTarget = new THREE.Object3D();
        spotTarget.position.copy(wallPos);
        scene.add(spotTarget);

        const spot = new THREE.SpotLight(0xfff5e6, 3.5, maxExtent * 0.3, Math.PI / 8, 0.5, 1.5);
        spot.position.copy(wallPos.clone().add(new THREE.Vector3(0, wallH * 1.0, 0))
            .add(faceDir.clone().multiplyScalar(standoffDist * 0.5)));
        spot.target = spotTarget;
        spot.castShadow = false; // perf: skip shadow for spotlights
        scene.add(spot);
        gallerySpotlights.push(spot);
    });

    scene.add(mediaMarkerGroup);
}

// === CAMERA PRESETS ===
const CAMERA_OFFSETS = {
    follow: { behind: 50, above: 30, lookAhead: 30 },
    overhead: { behind: 0, above: 120, lookAhead: 0 },
    side: { behind: 0, above: 20, lookAhead: 0, sideOffset: 60 },
    fpv: { behind: 0, above: 2, lookAhead: 40 },
};

// Compute a smooth tangent from the *camera* curve using a wide window
// instead of the instantaneous derivative, which is noisy.
const TANGENT_WINDOW = 0.02; // 2% of total trail for direction sampling
function getSmoothTangent(t) {
    const lo = Math.max(0, t - TANGENT_WINDOW);
    const hi = Math.min(1, t + TANGENT_WINDOW);
    const pLo = cameraCurve.getPointAt(lo);
    const pHi = cameraCurve.getPointAt(hi);
    return pHi.sub(pLo).normalize();
}

function getCameraTarget(t) {
    const clamped = Math.max(0.0001, Math.min(0.9999, t));
    // Use the heavily-smoothed camera curve for position & direction
    const point = cameraCurve.getPointAt(clamped);
    const tangent = getSmoothTangent(clamped);
    const up = new THREE.Vector3(0, 1, 0);
    const right = new THREE.Vector3().crossVectors(tangent, up).normalize();

    const preset = CAMERA_OFFSETS[currentCamPreset];
    const scaleFactor = maxExtent * 0.01;

    let pos, lookAt;

    if (currentCamPreset === 'follow') {
        const offset = tangent.clone().multiplyScalar(-preset.behind * scaleFactor)
            .add(new THREE.Vector3(0, preset.above * scaleFactor, 0));
        pos = point.clone().add(offset);
        lookAt = point.clone().add(tangent.clone().multiplyScalar(preset.lookAhead * scaleFactor));
    } else if (currentCamPreset === 'overhead') {
        pos = point.clone().add(new THREE.Vector3(0, preset.above * scaleFactor, 0));
        lookAt = point.clone();
    } else if (currentCamPreset === 'side') {
        const sideVec = right.multiplyScalar(preset.sideOffset * scaleFactor);
        pos = point.clone().add(sideVec).add(new THREE.Vector3(0, preset.above * scaleFactor, 0));
        lookAt = point.clone();
    } else { // fpv
        pos = point.clone().add(new THREE.Vector3(0, preset.above * scaleFactor, 0));
        lookAt = point.clone().add(tangent.clone().multiplyScalar(preset.lookAhead * scaleFactor));
    }

    return { pos, lookAt };
}

function updateCamera(t, dt) {
    const { pos, lookAt } = getCameraTarget(t);

    if (!camInitialized || dt === undefined) {
        camPos.copy(pos);
        camTarget.copy(lookAt);
        camInitialized = true;
    } else {
        // Frame-rate independent exponential smoothing
        const fPos = expSmoothFactor(CAM_POS_HALFLIFE, dt);
        const fTarget = expSmoothFactor(CAM_TARGET_HALFLIFE, dt);
        camPos.lerp(pos, fPos);
        camTarget.lerp(lookAt, fTarget);
    }

    camera.position.copy(camPos);
    camera.lookAt(camTarget);
    controls.target.copy(camTarget);
}

// === MEDIA TRIGGERS ===
function checkMediaTriggers(currentProgress) {
    const totalPoints = TRAIL_POINTS.length;
    MEDIA_MARKERS.forEach((media, index) => {
        if (triggeredMedia.has(index)) return;
        const mediaProgress = media.trackpoint_index / (totalPoints - 1);
        if (Math.abs(currentProgress - mediaProgress) < TRIGGER_THRESHOLD) {
            triggeredMedia.add(index);
            if (CONFIG.mediaMode === 'thumbnail') {
                showThumbnailPopup(media, index);
            } else if (CONFIG.mediaMode === 'autopause') {
                isPlaying = false;
                controls.enabled = true;
                updatePlayPauseBtn();
                showFullMedia(media);
            }
        }
    });
}

// Gallery-style notification label when hiker passes a display
const activePopups = [];
function showThumbnailPopup(media, index) {
    const div = document.createElement('div');
    div.style.cssText = 'pointer-events:auto;cursor:pointer;padding:8px 14px;background:rgba(0,0,0,0.75);backdrop-filter:blur(6px);border:1px solid rgba(192,160,96,0.5);border-radius:6px;color:#fff;font-size:12px;font-family:sans-serif;text-align:center;white-space:nowrap;';
    const typeIcon = media.media_type === 'photo' ? '\uD83D\uDDBC' : media.media_type === 'video' ? '\uD83C\uDFAC' : '\uD83C\uDFB5';
    const shortName = media.filename.length > 24 ? media.filename.substring(0, 21) + '...' : media.filename;
    div.innerHTML = typeIcon + ' <b>' + shortName + '</b><br><span style="opacity:0.6;font-size:10px">Click to view</span>';
    div.addEventListener('click', (e) => {
        e.stopPropagation();
        isPlaying = false;
        controls.enabled = true;
        updatePlayPauseBtn();
        showFullMedia(media);
    });

    const label = new CSS2DObject(div);
    label.position.set(media.x, media.y + maxExtent * 0.05, media.z);
    scene.add(label);
    activePopups.push({ label, time: Date.now() });
}

function showFullMedia(media) {
    const popup = document.getElementById('media-popup');
    const content = document.getElementById('media-popup-content');
    if (media.media_type === 'photo') {
        content.innerHTML = '<img src="assets/' + media.output_filename + '">';
    } else if (media.media_type === 'video') {
        content.innerHTML = '<video src="assets/' + media.output_filename + '" controls autoplay style="max-width:90vw;max-height:85vh"></video>';
    } else {
        content.innerHTML = '<div style="background:#222;padding:40px;border-radius:12px;text-align:center;color:#fff"><p style="margin-bottom:16px">' + media.filename + '</p><audio src="assets/' + media.output_filename + '" controls autoplay></audio></div>';
    }
    popup.classList.add('visible');
}

// Raycaster for marker clicks
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();

renderer.domElement.addEventListener('click', (e) => {
    mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
    mouse.y = -(e.clientY / window.innerHeight) * 2 + 1;
    raycaster.setFromCamera(mouse, camera);
    // Check image planes (direct) and all gallery group children
    const hits = raycaster.intersectObjects(mediaMarkerGroup.children, true);
    if (hits.length > 0) {
        // Walk up to find the mesh with userData.mediaIndex
        let data = null;
        for (const hit of hits) {
            if (hit.object.userData && hit.object.userData.mediaIndex !== undefined) {
                data = hit.object.userData;
                break;
            }
        }
        if (data) showFullMedia(data);
    }
});

// === APPLY STYLE ===
function applyStyle(style) {
    currentStyle = style;
    setupLighting(style);
    createGround(style);
    createTrailGeometry(style, currentColorMode);
}

function applyColorMode(mode) {
    currentColorMode = mode;
    createTrailGeometry(currentStyle, mode);
}

// === UI BINDINGS ===
const playPauseBtn = document.getElementById('play-pause');
const progressSlider = document.getElementById('progress');
const speedSlider = document.getElementById('speed');
const speedVal = document.getElementById('speed-val');
const timeDisplay = document.getElementById('time-display');

function updatePlayPauseBtn() {
    playPauseBtn.innerHTML = isPlaying ? '&#9646;&#9646;' : '&#9654;';
}

playPauseBtn.addEventListener('click', () => {
    isPlaying = !isPlaying;
    if (isPlaying) {
        controls.enabled = false;
        // Re-seed smoothing from current orbit position for seamless resume
        camInitialized = false;
        hikerInitialized = false;
        if (progress >= 1) progress = 0;
    } else {
        controls.enabled = true;
    }
    updatePlayPauseBtn();
});

progressSlider.addEventListener('input', (e) => {
    progress = parseFloat(e.target.value);
    // Instant snap on scrub — reset smoothing
    hikerInitialized = false;
    camInitialized = false;
    updateHikerPosition(progress);
    updateCamera(progress);
    updateTimeDisplay();
});

speedSlider.addEventListener('input', (e) => {
    speedMultiplier = parseInt(e.target.value);
    speedVal.textContent = speedMultiplier + 'x';
});

function updateTimeDisplay() {
    const elapsed = Math.floor(progress * animationDuration);
    const total = animationDuration;
    const fmt = (s) => Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
    timeDisplay.textContent = fmt(elapsed) + ' / ' + fmt(total);
}

// Camera preset buttons
document.querySelectorAll('#camera-group button').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('#camera-group button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentCamPreset = btn.dataset.cam;
        camInitialized = false;
    });
});

// Style buttons
document.querySelectorAll('#style-group button').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('#style-group button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        applyStyle(btn.dataset.style);
    });
});

// Color buttons
document.querySelectorAll('#color-group button').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('#color-group button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        applyColorMode(btn.dataset.color);
    });
});

// Media popup close
document.getElementById('media-popup-close').addEventListener('click', () => {
    document.getElementById('media-popup').classList.remove('visible');
    document.getElementById('media-popup-content').innerHTML = '';
});
document.getElementById('media-popup').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) {
        document.getElementById('media-popup').classList.remove('visible');
        document.getElementById('media-popup-content').innerHTML = '';
    }
});

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
    if (e.code === 'Space') {
        e.preventDefault();
        playPauseBtn.click();
    } else if (e.code === 'ArrowRight') {
        progress = Math.min(1, progress + 0.01);
        hikerInitialized = false;
        camInitialized = false;
        updateHikerPosition(progress);
        updateCamera(progress);
    } else if (e.code === 'ArrowLeft') {
        progress = Math.max(0, progress - 0.01);
        hikerInitialized = false;
        camInitialized = false;
        updateHikerPosition(progress);
        updateCamera(progress);
    }
});

// Window resize
window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
    labelRenderer.setSize(window.innerWidth, window.innerHeight);
});

// === POPULATE INFO ===
document.getElementById('hike-title').textContent = HIKE_META.name;
document.getElementById('stat-dist').textContent = HIKE_META.distance_miles;
document.getElementById('stat-dur').textContent = HIKE_META.duration;
document.getElementById('stat-elev').textContent = HIKE_META.elevation_gain_ft.toLocaleString();

// === INIT ===
applyStyle(currentStyle);
createMediaMarkers();
updateHikerPosition(0);
updateCamera(0);
camInitialized = true;

// Set initial style button active state
document.querySelectorAll('#style-group button').forEach(b => b.classList.remove('active'));
document.querySelector('#style-group button[data-style="' + currentStyle + '"]').classList.add('active');
document.querySelectorAll('#color-group button').forEach(b => b.classList.remove('active'));
document.querySelector('#color-group button[data-color="' + currentColorMode + '"]').classList.add('active');

// Hide HR button if no heart rate data
if (!CONFIG.hasHR) {
    const hrBtn = document.querySelector('#color-group button[data-color="hr"]');
    if (hrBtn) hrBtn.style.display = 'none';
}

// Remove loading
document.getElementById('loading').style.display = 'none';

// === ANIMATION LOOP ===
let lastTime = 0;

function animate(timestamp) {
    requestAnimationFrame(animate);

    const dt = lastTime ? (timestamp - lastTime) / 1000 : 0;
    lastTime = timestamp;

    if (isPlaying && dt > 0 && dt < 0.1) {
        progress += (dt * speedMultiplier) / animationDuration;
        if (progress >= 1.0) {
            progress = 1.0;
            isPlaying = false;
            controls.enabled = true;
            updatePlayPauseBtn();
        }
        updateHikerPosition(progress, dt);
        updateCamera(progress, dt);
        checkMediaTriggers(progress);
        progressSlider.value = progress;
        updateTimeDisplay();
    }

    // Clean old popups
    const now = Date.now();
    for (let i = activePopups.length - 1; i >= 0; i--) {
        if (isPlaying && now - activePopups[i].time > 5000) {
            scene.remove(activePopups[i].label);
            activePopups.splice(i, 1);
        }
    }

    if (!isPlaying) {
        controls.update();
    }

    renderer.render(scene, camera);
    labelRenderer.render(scene, camera);
}

requestAnimationFrame(animate);
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = create_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        # Discover files
        input_dir = Path(args.dir).resolve()
        files = discover_files(input_dir)

        # Process data
        hike_data = process_hike_data(files, args.title, args.verbose)

        if not hike_data.trackpoints:
            print("Error: No trackpoints found in GPX files.", file=sys.stderr)
            sys.exit(1)

        print(f"Trail: {len(hike_data.trackpoints)} points, {hike_data.distance_miles:.1f} miles")

        # Convert coordinates
        xyz_points = convert_to_local_xyz(hike_data, args.exaggeration)
        trail_colors = prepare_trail_colors(hike_data, xyz_points)
        media_data = prepare_media_data(hike_data, xyz_points)

        print(f"Media markers: {len(media_data)}")

        # Check if HR data actually exists
        has_hr = any(tp.heart_rate is not None for tp in hike_data.trackpoints)
        trail_color = args.trail_color
        if trail_color == "hr" and not has_hr:
            trail_color = "elevation"
            if args.verbose:
                print("No heart rate data found — defaulting trail color to elevation")

        # Config for JS
        config = {
            "duration": args.duration,
            "style": args.style,
            "trailColor": trail_color,
            "mediaMode": args.media_mode,
            "exaggeration": args.exaggeration,
            "hasHR": has_hr,
        }

        # Generate HTML
        html = generate_html(hike_data, xyz_points, trail_colors, media_data, config)

        # Write output
        output_dir = Path(args.output) if args.output else input_dir / "Trail3D"
        output_dir.mkdir(parents=True, exist_ok=True)

        html_path = output_dir / "index.html"
        html_path.write_text(html, encoding="utf-8")

        # Copy media
        if media_data:
            copy_media_assets(hike_data, output_dir)
            print(f"Copied {len(media_data)} media files to assets/")

        print(f"\nGenerated: {html_path}")
        print(f"Open in your browser to view the 3D animation.")

    except (FileNotFoundError, NotADirectoryError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
