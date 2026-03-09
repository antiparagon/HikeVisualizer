# HikeVisualizer

Turn hiking GPS data into interactive web experiences. HikeVisualizer processes GPX tracks, heart rate data, and trail media (photos, videos, audio) into rich, self-contained HTML pages.

The project includes two tools:

- **hikevisualizer** — generates an interactive web page with Mapbox maps, elevation profiles, heart rate zones, and a media timeline
- **hike_animation.py** — generates a Three.js 3D flythrough animation of the trail in a museum-room setting

## Features

- Interactive 2D/3D maps with terrain (via Mapbox)
- Three.js 3D flythrough animation with cinematic camera
- Heart rate zone coloring (5-zone model)
- Elevation profile with gradient visualization
- Media timeline — photos, videos, and audio aligned to the trail by timestamp
- HEIC/HEIF photo support with automatic conversion
- Multi-GPX file support (automatically combined chronologically)
- Self-contained HTML output — just open in a browser

## Requirements

- **Python** >= 3.11
- **[uv](https://docs.astral.sh/uv/)** for package management
- **Mapbox access token** (for the interactive web page; not needed for the 3D animation or `--offline` mode)
- **ffprobe** (optional, for extracting video/audio duration)

## Installation

```bash
git clone https://github.com/antiparagon/HikeVisualizer.git
cd HikeVisualizer
uv sync
```

## Quick Start

```bash
# Interactive web page
uv run -m hikevisualizer --dir /path/to/hike

# 3D flythrough animation
uv run hike_animation.py --dir /path/to/hike
```

Point `--dir` at a folder containing your GPX files, FIT files, and media. Both tools auto-discover all supported files in the directory.

## Input Directory Structure

A typical hike folder:

```
my-hike/
  track.gpx              # GPS track (one or more)
  activity.fit           # Heart rate data (optional)
  IMG_1234.jpg           # Photos (optional)
  IMG_1235.HEIC
  video_clip.mp4         # Videos (optional)
  trail_notes.m4a        # Audio (optional)
```

---

## HikeVisualizer (Interactive Web Page)

Generates a Mapbox-powered interactive web page with an elevation profile, heart rate zone coloring, and a chronological media timeline.

### Usage

```bash
# Auto-discover all files in a directory
uv run -m hikevisualizer --dir /path/to/hike

# Specify files explicitly
uv run -m hikevisualizer --gpx track.gpx --fit activity.fit --media ./photos

# Offline mode (no Mapbox token needed)
uv run -m hikevisualizer --dir /path/to/hike --offline

# Generate embeddable HTML fragment
uv run -m hikevisualizer --dir /path/to/hike --publish
```

### CLI Options

| Option | Description | Default |
|---|---|---|
| `--dir`, `-d` | Directory to scan for GPX, FIT, and media files | — |
| `--gpx`, `-g` | Path to GPX file | — |
| `--fit`, `-f` | Path to FIT file (heart rate) | — |
| `--media`, `-m` | Path to media directory | — |
| `--output`, `-o` | Output directory | `./output` |
| `--title`, `-t` | Custom hike title | GPX track name |
| `--mapbox-token` | Mapbox access token (or set `MAPBOX_ACCESS_TOKEN` env var) | — |
| `--offline` | No 3D terrain (offline-compatible) | `false` |
| `--no-media-copy` | Reference original media paths | `false` |
| `--story` | Include story template sections in timeline | `false` |
| `--publish` | Generate embeddable HTML fragment | `false` |
| `--verbose`, `-v` | Verbose output | `false` |

### Output

```
output/
  index.html       # Self-contained interactive page
  assets/          # Copied/resized media files
```

---

## Hike Animation (3D Flythrough)

Generates a Three.js 3D flythrough animation that displays the trail as a floating sculpture inside a museum room. The camera follows a smoothed path along the trail while media markers appear at their GPS-matched positions.

### Usage

```bash
# Basic animation
uv run hike_animation.py --dir /path/to/hike

# Custom duration and style
uv run hike_animation.py --dir /path/to/hike --duration 120 --style realistic

# Elevation coloring with pins-only media
uv run hike_animation.py --dir /path/to/hike --trail-color elevation --media-mode pins
```

### CLI Options

| Option | Description | Default |
|---|---|---|
| `--dir`, `-d` | Directory to scan for GPX, FIT, and media files (required) | — |
| `--output`, `-o` | Output directory | `Trail3D/` inside `--dir` |
| `--title`, `-t` | Custom title | Folder name |
| `--duration` | Animation duration in seconds | `60` |
| `--style` | Visual style: `minimal`, `topographic`, `realistic` | `realistic` |
| `--trail-color` | Trail coloring: `hr`, `elevation`, `solid` | `hr` |
| `--media-mode` | Media markers: `thumbnail`, `autopause`, `pins` | `thumbnail` |
| `--exaggeration` | Vertical exaggeration factor | `2.0` |
| `--verbose`, `-v` | Verbose output | `false` |

### Interactive Controls

The generated HTML page includes full interactive controls:

- **Play/Pause** — spacebar or button
- **Progress scrubbing** — drag the slider or click anywhere on the trail
- **Speed** — 1x to 50x
- **Camera presets** — Follow, Overhead, Side, First Person
- **Visual styles** — Minimal, Topographic, Realistic
- **Trail coloring** — HR Zones, Elevation, Solid
- **Auto-show media** — automatically pauses at each photo/video during playback; click "keep viewing" to hold an image, or let it advance after the configured delay
- **Arrow keys** — step forward/backward along the trail
- **Orbit controls** — when paused, click and drag to orbit, scroll to zoom

### Running Stats

During animation, a live stats overlay shows cumulative distance, ascent, descent, and elapsed time — interpolated smoothly between trackpoints.

### Output

```
Trail3D/
  index.html       # Self-contained 3D animation
  assets/          # Resized media files
```

---

## Supported File Types

| Type | Formats |
|---|---|
| GPS tracks | `.gpx` |
| Heart rate | `.fit` |
| Photos | `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif`, `.heic`, `.heif` |
| Videos | `.mp4`, `.mov`, `.avi`, `.mkv`, `.webm`, `.m4v` |
| Audio | `.mp3`, `.wav`, `.m4a`, `.aac`, `.ogg`, `.flac` |

## How Media Is Matched

Photos and videos are aligned to the trail by their EXIF timestamp. Media taken during the hike (within 5 minutes of the start/end) is placed at the corresponding trail position. Media without EXIF timestamps or taken outside the hike window is displayed in a gallery wall (3D animation) or excluded from the timeline.

## Heart Rate Zones

When FIT data is available, the trail is color-coded by heart rate using a 5-zone model relative to observed min/max:

| Zone | Intensity | Color |
|---|---|---|
| 1 | Recovery (0–20%) | Blue |
| 2 | Easy (20–40%) | Green |
| 3 | Moderate (40–60%) | Yellow |
| 4 | Hard (60–80%) | Orange |
| 5 | Maximum (80–100%) | Red |

If no heart rate data is available, the trail falls back to elevation-based coloring.

## Project Structure

```
HikeVisualizer/
  hike_animation.py              # 3D flythrough animation script
  pyproject.toml                 # Project metadata and dependencies
  hikevisualizer/
    __init__.py
    __main__.py                  # Entry point for -m execution
    cli.py                       # CLI argument parser
    models/
      hike_data.py               # HikeData aggregate model
      trackpoint.py              # TrackPoint model
      media_item.py              # MediaItem model
    core/
      gpx_parser.py              # GPX file parser
      fit_parser.py              # FIT file parser (heart rate)
      media_scanner.py           # Media directory scanner (EXIF extraction)
      data_merger.py             # Merges GPX, FIT, and media by timestamp
      hr_zones.py                # Heart rate zone calculator
    generators/
      html_generator.py          # HTML page generator
      js_generator.py            # Mapbox JS generator
```

## Dependencies

Managed by [uv](https://docs.astral.sh/uv/). Key packages:

- **gpxpy** — GPX file parsing
- **fitparse** — FIT file parsing
- **Pillow** — image processing and resizing
- **pillow-heif** — HEIC/HEIF image support
- **Jinja2** — HTML template rendering
- **timezonefinder** — GPS coordinate to timezone lookup
- **python-dateutil** / **pytz** — timezone and date handling
