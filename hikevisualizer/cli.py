"""Command-line interface for HikeVisualizer."""

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(
        prog="hikevisualizer",
        description="Generate a static webpage from GPX hike data with 3D visualization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  hikevisualizer --dir ./my-hike
  hikevisualizer --gpx hike.gpx --output ./output
  hikevisualizer --gpx hike.gpx --fit activity.fit --media ./photos
  hikevisualizer --gpx hike.gpx --mapbox-token YOUR_TOKEN --title "Summer Hike"

Environment Variables:
  MAPBOX_ACCESS_TOKEN    Mapbox access token (alternative to --mapbox-token)
        """,
    )

    # Input directory (auto-discover files)
    parser.add_argument(
        "--dir",
        "-d",
        type=str,
        default=None,
        help="Directory to scan for GPX, FIT, and media files (auto-discovery)",
    )

    # Specific file arguments (override auto-discovery)
    parser.add_argument(
        "--gpx",
        "-g",
        type=str,
        default=None,
        help="Path to the GPX file containing track data",
    )

    # Optional arguments
    parser.add_argument(
        "--fit",
        "-f",
        type=str,
        default=None,
        help="Path to FIT file containing heart rate data",
    )

    parser.add_argument(
        "--media",
        "-m",
        type=str,
        default=None,
        help="Path to directory containing photos, videos, and audio files",
    )

    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="./output",
        help="Output directory for generated website (default: ./output)",
    )

    parser.add_argument(
        "--title",
        "-t",
        type=str,
        default=None,
        help="Custom title for the hike (overrides GPX track name)",
    )

    parser.add_argument(
        "--mapbox-token",
        type=str,
        default=None,
        help="Mapbox access token (or set MAPBOX_ACCESS_TOKEN env var)",
    )

    parser.add_argument(
        "--offline",
        action="store_true",
        help="Generate offline-compatible version (no 3D terrain)",
    )

    parser.add_argument(
        "--no-media-copy",
        action="store_true",
        help="Reference original media paths instead of copying",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )

    return parser


def find_files_by_extension(directory: Path, extensions: List[str]) -> List[Path]:
    """Find all files in directory matching given extensions."""
    files = []
    for ext in extensions:
        files.extend(directory.glob(f"*{ext}"))
        files.extend(directory.glob(f"*{ext.upper()}"))
    return sorted(files)


def auto_discover_files(input_dir: Path, args: argparse.Namespace) -> None:
    """Auto-discover GPX, FIT, and media files from input directory."""
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Input directory not found: {input_dir}")

    # Find GPX files if not specified
    if not args.gpx:
        gpx_files = find_files_by_extension(input_dir, [".gpx"])
        if gpx_files:
            # Store as comma-separated string for multiple files
            args.gpx = ",".join(str(f) for f in gpx_files)
            if len(gpx_files) == 1:
                print(f"Auto-discovered GPX: {gpx_files[0].name}")
            else:
                file_list = ", ".join(f.name for f in gpx_files)
                print(f"Auto-discovered {len(gpx_files)} GPX files: {file_list}")
        else:
            raise FileNotFoundError(f"No GPX file found in {input_dir}")

    # Find FIT files if not specified
    if not args.fit:
        fit_files = find_files_by_extension(input_dir, [".fit"])
        if fit_files:
            # Store as comma-separated string for multiple files
            args.fit = ",".join(str(f) for f in fit_files)
            if len(fit_files) == 1:
                print(f"Auto-discovered FIT: {fit_files[0].name}")
            else:
                file_list = ", ".join(f.name for f in fit_files)
                print(f"Auto-discovered {len(fit_files)} FIT files: {file_list}")

    # Use input directory as media directory if not specified
    if not args.media:
        args.media = str(input_dir)
        print(f"Using directory for media: {input_dir}")

    # Set output directory inside input directory if not explicitly specified
    if args.output == "./output":
        args.output = str(input_dir / "output")
        print(f"Output directory: {args.output}")


def parse_file_list(file_arg: Optional[str]) -> List[str]:
    """Parse comma-separated file list into list of paths."""
    if not file_arg:
        return []
    return [f.strip() for f in file_arg.split(",") if f.strip()]


def validate_args(args: argparse.Namespace) -> None:
    """Validate command line arguments."""
    # Auto-discover files from input directory if provided
    if args.dir:
        auto_discover_files(Path(args.dir), args)

    # Parse file lists
    args.gpx_files = parse_file_list(args.gpx)
    args.fit_files = parse_file_list(args.fit)

    # Check GPX files are specified and exist
    if not args.gpx_files:
        raise ValueError(
            "GPX file required. Provide via --gpx or use --dir for auto-discovery."
        )

    for gpx_path in args.gpx_files:
        if not Path(gpx_path).exists():
            raise FileNotFoundError(f"GPX file not found: {gpx_path}")

    # Check FIT files if provided
    for fit_path in args.fit_files:
        if not Path(fit_path).exists():
            raise FileNotFoundError(f"FIT file not found: {fit_path}")

    # Check media directory if provided
    if args.media:
        media_path = Path(args.media)
        if not media_path.is_dir():
            raise NotADirectoryError(f"Media path is not a directory: {args.media}")

    # Check Mapbox token
    if not args.mapbox_token:
        args.mapbox_token = os.environ.get("MAPBOX_ACCESS_TOKEN")

    if not args.mapbox_token:
        raise ValueError(
            "Mapbox access token required. "
            "Provide via --mapbox-token or MAPBOX_ACCESS_TOKEN environment variable. "
            "Get a free token at https://account.mapbox.com/access-tokens/"
        )


def main():
    """Main entry point for the CLI."""
    parser = create_parser()
    args = parser.parse_args()

    try:
        validate_args(args)
    except (FileNotFoundError, NotADirectoryError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Import and run main generation
    from .generators.html_generator import generate_site

    try:
        output_path = generate_site(
            gpx_paths=args.gpx_files,
            fit_paths=args.fit_files,
            media_path=args.media,
            output_dir=args.output,
            title=args.title,
            mapbox_token=args.mapbox_token,
            offline=args.offline,
            copy_media=not args.no_media_copy,
            verbose=args.verbose,
        )
        print(f"Successfully generated site at: {output_path}")
        print(f"Open {output_path / 'index.html'} in your browser to view.")
    except Exception as e:
        print(f"Error generating site: {e}", file=sys.stderr)
        if args.verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
