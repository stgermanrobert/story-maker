# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.0.3] - 2026-10-18
### Added
- Magnetic snapping on the timeline: in/out markers snap to 15 s, 30 s and 60 s lengths and to the playhead; the playhead snaps to in/out. Snap radius is measured in pixels, so zooming in makes it finer.
- Hold `Alt` while dragging to ignore snapping; "Magnet" checkbox to turn it off.
- One-click 15 s / 30 s / 60 s fragment buttons; the length turns green when it is exact.
- Portable mode: binaries in a `bin` folder next to the app are used before the system `PATH`.
- Version number in the window title.

## [1.0.2] - 2026-10-18
### Added
- Direct editing on the preview: drag to move the main video, drag corners or use the mouse wheel to scale, double-click to center, magnetic center guides.
- Independent crop for the main video.
- Play / pause with audio, showing the final composition; live restart when a setting changes; loop.
- Timeline with audio waveform, zoom and pan, draggable in/out markers, millisecond-precision entry fields, frame stepping and keyboard shortcuts.
- Scrollable settings tabs.

### Changed
- The exported fragment is now taken from the timeline in/out markers.

## [1.0.1] - 2026-10-18
### Added
- First version: live preview, background (zoom, position, mirror, blur, brightness, contrast, saturation, vignette, opacity, base color), main video (width, offset, opacity, rounded corners, border, shadow), source crop, output formats, H.264 export with progress, split into parts, YouTube download via yt-dlp, automatic detection of separate audio, "Copy command", persistent settings.
