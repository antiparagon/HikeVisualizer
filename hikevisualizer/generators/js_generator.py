"""Mapbox GL JS code generator for 3D map visualization."""

import json
from typing import List, Tuple, Optional

from ..models.hike_data import HikeData
from ..models.media_item import MediaType


# Track colors for multiple GPX files
TRACK_COLORS = [
    '#3B82F6',  # Blue
    '#EF4444',  # Red
    '#22C55E',  # Green
    '#F97316',  # Orange
    '#8B5CF6',  # Purple
    '#EC4899',  # Pink
    '#14B8A6',  # Teal
    '#F59E0B',  # Amber
]


class MapboxJSGenerator:
    """Generates Mapbox GL JS code for the 3D map visualization."""

    def __init__(
        self,
        hike_data: HikeData,
        mapbox_token: str,
        use_offline: bool = False,
        individual_tracks: Optional[List[dict]] = None,
    ):
        self.hike_data = hike_data
        self.mapbox_token = mapbox_token
        self.use_offline = use_offline
        self.individual_tracks = individual_tracks or []

    def generate(self, gradient_stops: List[Tuple[float, str]]) -> str:
        """Generate complete Mapbox JS initialization code."""
        center = self.hike_data.get_center()
        bounds = self.hike_data.get_bounds()

        terrain_code = self._generate_terrain_code()
        tracks_code = self._generate_tracks_code()
        toggle_code = self._generate_toggle_code()
        marker_code = self._generate_marker_code()
        media_markers_code = self._generate_media_markers_code()

        js_code = f"""
// Initialize Mapbox
mapboxgl.accessToken = '{self.mapbox_token}';

const map = new mapboxgl.Map({{
    container: 'map',
    style: 'mapbox://styles/mapbox/outdoors-v12',
    center: [{center[0]}, {center[1]}],
    zoom: 12,
    pitch: 60,
    bearing: -17,
    antialias: true
}});

// Add navigation controls
map.addControl(new mapboxgl.NavigationControl());

// Track visibility state
const trackVisibility = {{}};

map.on('style.load', () => {{
    console.log('Map style loaded, adding tracks...');
    {terrain_code}

    {tracks_code}

    {marker_code}

    // Fit to bounds
    map.fitBounds([
        [{bounds['west']}, {bounds['south']}],
        [{bounds['east']}, {bounds['north']}]
    ], {{
        padding: 50,
        pitch: 60
    }});
}});

{toggle_code}

{media_markers_code}
"""
        return js_code

    def _generate_tracks_code(self) -> str:
        """Generate code for all track layers."""
        if not self.individual_tracks:
            # Fallback to single combined track
            return self._generate_single_track_code()

        tracks_js = []
        for i, track in enumerate(self.individual_tracks):
            track_id = f"track-{i}"
            track_name = track['name']
            color = TRACK_COLORS[i % len(TRACK_COLORS)]

            # Generate GeoJSON for this track
            geojson = self._track_to_geojson(track)
            gradient_expr = self._build_gradient_expression(track.get('gradient_stops', []))

            tracks_js.append(f"""
    // Track: {track_name}
    const track{i}GeoJSON = {json.dumps(geojson)};
    trackVisibility['{track_id}'] = true;

    map.addSource('{track_id}', {{
        type: 'geojson',
        lineMetrics: true,
        data: track{i}GeoJSON
    }});

    // Track outline
    map.addLayer({{
        id: '{track_id}-outline',
        type: 'line',
        source: '{track_id}',
        layout: {{
            'line-join': 'round',
            'line-cap': 'round'
        }},
        paint: {{
            'line-width': 8,
            'line-color': '#000000',
            'line-opacity': 0.5
        }}
    }});

    // Track line with gradient
    try {{
        map.addLayer({{
            id: '{track_id}-line',
            type: 'line',
            source: '{track_id}',
            layout: {{
                'line-join': 'round',
                'line-cap': 'round'
            }},
            paint: {{
                'line-width': 5,
                'line-gradient': {gradient_expr}
            }}
        }});
    }} catch (e) {{
        console.error('Gradient failed for {track_name}, using solid color:', e);
        map.addLayer({{
            id: '{track_id}-line',
            type: 'line',
            source: '{track_id}',
            layout: {{
                'line-join': 'round',
                'line-cap': 'round'
            }},
            paint: {{
                'line-width': 5,
                'line-color': '{color}'
            }}
        }});
    }}
""")

        return "\n".join(tracks_js)

    def _generate_single_track_code(self) -> str:
        """Generate code for a single combined track (fallback)."""
        geojson = json.dumps(self.hike_data.to_geojson())
        return f"""
    // Single combined track
    const hikeGeoJSON = {geojson};
    trackVisibility['track-0'] = true;

    map.addSource('track-0', {{
        type: 'geojson',
        lineMetrics: true,
        data: hikeGeoJSON
    }});

    map.addLayer({{
        id: 'track-0-outline',
        type: 'line',
        source: 'track-0',
        layout: {{
            'line-join': 'round',
            'line-cap': 'round'
        }},
        paint: {{
            'line-width': 8,
            'line-color': '#000000',
            'line-opacity': 0.5
        }}
    }});

    map.addLayer({{
        id: 'track-0-line',
        type: 'line',
        source: 'track-0',
        layout: {{
            'line-join': 'round',
            'line-cap': 'round'
        }},
        paint: {{
            'line-width': 5,
            'line-color': '#3B82F6'
        }}
    }});
"""

    def _generate_toggle_code(self) -> str:
        """Generate JavaScript for toggle functionality."""
        track_data_js = self.get_track_data_js()

        return f"""
// Track data for dynamic updates
const trackData = {track_data_js};

// Toggle track visibility and update page content
function toggleTrack(trackId) {{
    const isVisible = trackVisibility[trackId];
    const newVisibility = isVisible ? 'none' : 'visible';

    map.setLayoutProperty(trackId + '-outline', 'visibility', newVisibility);
    map.setLayoutProperty(trackId + '-line', 'visibility', newVisibility);

    trackVisibility[trackId] = !isVisible;

    // Update checkbox state
    const checkbox = document.getElementById('toggle-' + trackId);
    if (checkbox) {{
        checkbox.checked = !isVisible;
    }}

    // Update page content based on visible tracks
    updatePageContent();
}}

// Get list of currently visible track IDs
function getVisibleTrackIds() {{
    return Object.keys(trackVisibility).filter(id => trackVisibility[id]);
}}

// Update all page content based on visible tracks
function updatePageContent() {{
    const visibleIds = getVisibleTrackIds();
    updateSummaryStats(visibleIds);
    updateElevationStats(visibleIds);
    updateElevationChart(visibleIds);
    updateTimelineVisibility(visibleIds);
}}

// Update summary stats section
function updateSummaryStats(visibleIds) {{
    let totalDistanceMiles = 0;
    let totalDurationSeconds = 0;

    visibleIds.forEach(id => {{
        const data = trackData[id];
        if (data && data.stats) {{
            totalDistanceMiles += data.stats.distance_miles || 0;
            totalDurationSeconds += data.stats.duration_seconds || 0;
        }}
    }});

    // Calculate pace
    const paceMinPerMile = totalDistanceMiles > 0 ? totalDurationSeconds / 60 / totalDistanceMiles : 0;
    const paceMinutes = Math.floor(paceMinPerMile);
    const paceSeconds = Math.round((paceMinPerMile - paceMinutes) * 60);
    const paceFormatted = paceMinPerMile > 0 ? `${{paceMinutes}}:${{paceSeconds.toString().padStart(2, '0')}}` : '--:--';

    // Format duration
    const hours = Math.floor(totalDurationSeconds / 3600);
    const minutes = Math.floor((totalDurationSeconds % 3600) / 60);
    const seconds = Math.floor(totalDurationSeconds % 60);
    const durationFormatted = `${{hours.toString().padStart(2, '0')}}:${{minutes.toString().padStart(2, '0')}}:${{seconds.toString().padStart(2, '0')}}`;

    // Update DOM
    const distanceEl = document.querySelector('.summary-stat:nth-child(1) .summary-stat__value');
    const paceEl = document.querySelector('.summary-stat:nth-child(2) .summary-stat__value');
    const durationEl = document.querySelector('.summary-stat:nth-child(3) .summary-stat__value');

    if (distanceEl) distanceEl.textContent = totalDistanceMiles.toFixed(2);
    if (paceEl) paceEl.textContent = paceFormatted;
    if (durationEl) durationEl.textContent = durationFormatted;
}}

// Update elevation stats section
function updateElevationStats(visibleIds) {{
    let totalAscentFt = 0;
    let totalDescentFt = 0;
    let minElevFt = 0;
    let maxElevFt = 0;

    if (visibleIds.length > 0) {{
        minElevFt = Infinity;
        maxElevFt = -Infinity;

        visibleIds.forEach(id => {{
            const data = trackData[id];
            if (data && data.stats) {{
                totalAscentFt += data.stats.total_ascent_ft || 0;
                totalDescentFt += data.stats.total_descent_ft || 0;
                if (data.stats.min_elevation_ft < minElevFt) minElevFt = data.stats.min_elevation_ft;
                if (data.stats.max_elevation_ft > maxElevFt) maxElevFt = data.stats.max_elevation_ft;
            }}
        }});

        if (minElevFt === Infinity) minElevFt = 0;
        if (maxElevFt === -Infinity) maxElevFt = 0;
    }}

    // Update DOM
    const statCards = document.querySelectorAll('.elevation-stats .stat-card');
    if (statCards[0]) statCards[0].querySelector('.stat-value').textContent = Math.round(totalAscentFt);
    if (statCards[1]) statCards[1].querySelector('.stat-value').textContent = Math.round(totalDescentFt);
    if (statCards[2]) statCards[2].querySelector('.stat-value').textContent = Math.round(maxElevFt);
    if (statCards[3]) statCards[3].querySelector('.stat-value').textContent = Math.round(minElevFt);

    // Update chart axis labels
    const maxLabel = document.querySelector('.elevation-label-max');
    const minLabel = document.querySelector('.elevation-label-min');
    if (maxLabel) maxLabel.textContent = Math.round(maxElevFt) + ' ft';
    if (minLabel) minLabel.textContent = Math.round(minElevFt) + ' ft';
}}

// Update elevation chart SVG
function updateElevationChart(visibleIds) {{
    const svg = document.querySelector('.elevation-svg');
    const distanceLabels = document.querySelector('.distance-axis-labels');

    // Handle empty selection - clear the chart
    if (visibleIds.length === 0) {{
        if (svg) {{
            const areaEl = svg.querySelector('path:first-of-type');
            const lineEl = svg.querySelector('path:last-of-type');
            if (areaEl) areaEl.setAttribute('d', '');
            if (lineEl) lineEl.setAttribute('d', '');
        }}
        if (distanceLabels) {{
            const spans = distanceLabels.querySelectorAll('span');
            if (spans[1]) spans[1].textContent = '0.00 mi';
        }}
        return;
    }}

    // Collect all elevation points from visible tracks
    let allPoints = [];
    let totalDistance = 0;

    visibleIds.forEach(id => {{
        const data = trackData[id];
        if (data && data.elevation_points) {{
            data.elevation_points.forEach(p => {{
                allPoints.push({{
                    distance: p.distance + totalDistance,
                    elevation: p.elevation
                }});
            }});
            // Get max distance from this track
            if (data.elevation_points.length > 0) {{
                const lastPoint = data.elevation_points[data.elevation_points.length - 1];
                totalDistance += lastPoint.distance;
            }}
        }}
    }});

    if (allPoints.length < 2) return;

    // Calculate bounds
    const minElev = Math.min(...allPoints.map(p => p.elevation));
    const maxElev = Math.max(...allPoints.map(p => p.elevation));
    const maxDist = totalDistance;

    if (maxDist === 0 || maxElev === minElev) return;

    // Chart dimensions
    const chartWidth = 800;
    const chartHeight = 200;

    // Generate SVG paths
    let linePath = '';
    let areaPath = 'M 0 ' + chartHeight;

    allPoints.forEach((p, i) => {{
        const x = (p.distance / maxDist) * chartWidth;
        const yNorm = (p.elevation - minElev) / (maxElev - minElev);
        const y = chartHeight - (yNorm * chartHeight * 0.9) - (chartHeight * 0.05);

        if (i === 0) {{
            linePath = 'M ' + x.toFixed(1) + ' ' + y.toFixed(1);
            areaPath += ' L ' + x.toFixed(1) + ' ' + y.toFixed(1);
        }} else {{
            linePath += ' L ' + x.toFixed(1) + ' ' + y.toFixed(1);
            areaPath += ' L ' + x.toFixed(1) + ' ' + y.toFixed(1);
        }}
    }});

    areaPath += ' L ' + chartWidth + ' ' + chartHeight + ' Z';

    // Update SVG paths
    if (svg) {{
        const areaEl = svg.querySelector('path:first-of-type');
        const lineEl = svg.querySelector('path:last-of-type');
        if (areaEl) areaEl.setAttribute('d', areaPath);
        if (lineEl) lineEl.setAttribute('d', linePath);
    }}

    // Update distance axis
    if (distanceLabels) {{
        const spans = distanceLabels.querySelectorAll('span');
        if (spans[1]) spans[1].textContent = (maxDist / 1609.344).toFixed(2) + ' mi';
    }}
}}

// Update timeline/gallery visibility based on tracks
function updateTimelineVisibility(visibleIds) {{
    // Show/hide media cards based on track (skip gallery items - they always show)
    document.querySelectorAll('.timeline-article [data-track-id]').forEach(el => {{
        const trackId = el.getAttribute('data-track-id');
        if (trackId && !visibleIds.includes(trackId)) {{
            el.style.display = 'none';
        }} else {{
            el.style.display = '';
        }}
    }});

    // Also update vertical timeline markers
    document.querySelectorAll('.timeline-marker[data-track-id]').forEach(el => {{
        const trackId = el.getAttribute('data-track-id');
        if (trackId && !visibleIds.includes(trackId)) {{
            el.style.display = 'none';
        }} else {{
            el.style.display = '';
        }}
    }});
}}

// Sync toggle checkboxes between map and timeline filter
function syncToggle(trackId) {{
    const isChecked = trackVisibility[trackId];

    // Sync map toggle
    const mapToggle = document.getElementById('toggle-' + trackId);
    if (mapToggle) mapToggle.checked = isChecked;

    // Sync filter toggle
    const filterToggle = document.getElementById('filter-' + trackId);
    if (filterToggle) filterToggle.checked = isChecked;
}}

// Initialize toggle controls after map loads
map.on('load', () => {{
    const toggleContainer = document.getElementById('track-toggles');
    if (toggleContainer) {{
        toggleContainer.style.display = 'block';
    }}
}});
"""

    def _track_to_geojson(self, track: dict) -> dict:
        """Convert a track to GeoJSON format."""
        trackpoints = track['trackpoints']
        return {
            "type": "Feature",
            "properties": {
                "name": track['name'],
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [tp.longitude, tp.latitude, tp.elevation]
                    for tp in trackpoints
                ],
            },
        }

    def _generate_terrain_code(self) -> str:
        """Generate terrain source and setup code."""
        if self.use_offline:
            return """
    // Offline mode: 3D terrain disabled
    console.log('Running in offline mode - 3D terrain disabled');"""
        else:
            return """
    // Add 3D terrain
    map.addSource('mapbox-dem', {
        type: 'raster-dem',
        url: 'mapbox://mapbox.mapbox-terrain-dem-v1',
        tileSize: 512,
        maxzoom: 14
    });

    map.setTerrain({
        source: 'mapbox-dem',
        exaggeration: 2.5
    });

    // Add sky layer for atmosphere
    map.addLayer({
        id: 'sky',
        type: 'sky',
        paint: {
            'sky-type': 'atmosphere',
            'sky-atmosphere-sun': [0.0, 90.0],
            'sky-atmosphere-sun-intensity': 15
        }
    });"""

    def _build_gradient_expression(self, stops: List[Tuple[float, str]]) -> str:
        """Build Mapbox expression for line-gradient."""
        if not stops:
            return json.dumps(["interpolate", ["linear"], ["line-progress"], 0, "#3B82F6", 1, "#3B82F6"])

        # Build the expression as a proper JavaScript array using JSON
        expression = ["interpolate", ["linear"], ["line-progress"]]

        for progress, color in stops:
            expression.append(progress)
            expression.append(color)

        return json.dumps(expression)

    def _generate_marker_code(self) -> str:
        """Generate code for start/end markers."""
        if not self.hike_data.trackpoints:
            return ""

        start = self.hike_data.trackpoints[0]
        end = self.hike_data.trackpoints[-1]

        return f"""
    // Start marker
    new mapboxgl.Marker({{ color: '#22C55E' }})
        .setLngLat([{start.longitude}, {start.latitude}])
        .setPopup(new mapboxgl.Popup().setHTML('<b>Start</b>'))
        .addTo(map);

    // End marker
    new mapboxgl.Marker({{ color: '#EF4444' }})
        .setLngLat([{end.longitude}, {end.latitude}])
        .setPopup(new mapboxgl.Popup().setHTML('<b>Finish</b>'))
        .addTo(map);"""

    def _generate_media_markers_code(self) -> str:
        """Generate code for media location markers."""
        if not self.hike_data.media_items:
            return "// No media markers"

        markers_js = ["// Media markers"]
        markers_js.append("map.on('load', () => {")

        for i, media in enumerate(self.hike_data.media_items):
            if media.nearest_trackpoint_index is not None:
                tp = self.hike_data.trackpoints[media.nearest_trackpoint_index]

                # Different popup content based on media type
                if media.media_type == MediaType.PHOTO:
                    popup_content = f'<img src="assets/{media.output_filename}" style="max-width:200px;border-radius:4px;">'
                elif media.media_type == MediaType.VIDEO:
                    popup_content = f'<video src="assets/{media.output_filename}" style="max-width:200px;" controls></video>'
                else:
                    popup_content = f'<audio src="assets/{media.output_filename}" controls></audio>'

                markers_js.append(f"""
    new mapboxgl.Marker({{ color: '#8B5CF6', scale: 0.7 }})
        .setLngLat([{tp.longitude}, {tp.latitude}])
        .setPopup(new mapboxgl.Popup().setHTML('{popup_content}'))
        .addTo(map);""")

        markers_js.append("});")

        return "\n".join(markers_js)

    def get_track_info_for_template(self) -> List[dict]:
        """Return track info for template rendering."""
        tracks_info = []
        for i, track in enumerate(self.individual_tracks):
            tracks_info.append({
                'id': f'track-{i}',
                'name': track['name'],
                'color': TRACK_COLORS[i % len(TRACK_COLORS)],
                'stats': track.get('stats', {}),
                'elevation_points': track.get('elevation_points', []),
            })
        return tracks_info

    def get_track_data_js(self) -> str:
        """Generate JavaScript object containing all track data for dynamic updates."""
        tracks_data = {}
        for i, track in enumerate(self.individual_tracks):
            track_id = f'track-{i}'
            tracks_data[track_id] = {
                'name': track['name'],
                'color': TRACK_COLORS[i % len(TRACK_COLORS)],
                'stats': track.get('stats', {}),
                'elevation_points': track.get('elevation_points', []),
            }
        return json.dumps(tracks_data)
