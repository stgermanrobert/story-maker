# Story Maker

**Turn horizontal videos into vertical Instagram Stories / Reels with a blurred background — with a live preview, playback and a frame-accurate trimming timeline.**

[Leer en español](README.es.md)

Story Maker is a small desktop app for Windows built with Python (Tkinter) on top of FFmpeg. Load a video (or paste a YouTube link), adjust how the background and the main video look, pick the exact fragment you want, and export a ready-to-upload 1080×1920 MP4.

## Features

**Sources**
- Download from YouTube in up to 1080p (via [yt-dlp](https://github.com/yt-dlp/yt-dlp)) straight into the app.
- Load any local video. If it has no audio track (e.g. an unmerged yt-dlp download), the matching audio file is detected automatically.
- Output formats: Story / Reel 9:16 (1080×1920), Feed 4:5 (1080×1350), Square 1:1, Story 720p.

**Background**
- Zoom, horizontal / vertical position, mirror.
- Blur, brightness, contrast, saturation, vignette.
- Opacity over a solid color of your choice.

**Main video**
- Drag it on the preview to move it; drag a corner or use the mouse wheel to scale; double-click to center (with magnetic center guides).
- Independent crop (left / right / top / bottom).
- Opacity, rounded corners, border with color, drop shadow.
- Crop black bars baked into the source.

**Playback & trimming**
- Play / pause with audio, showing the final composition.
- Timeline with audio waveform, zoom and pan.
- In / out markers you can drag, set at the playhead (`I` / `O`) or type with millisecond precision (`1:21.200`).
- Drag the purple bar above the waveform (or `Ctrl`+drag inside the selection) to move the whole fragment while keeping its length.
- Magnetic snapping to 15 s, 30 s and 60 s lengths and to the playhead (hold `Alt` to drag freely, or turn the magnet off).
- Transport: video start, block start, stop (press twice to go back to the start of the video), play, block end, video end.
- One-click 15 s / 30 s / 60 s fragments, loop playback, frame stepping.

**Export**
- H.264 + AAC MP4 with `faststart`, adjustable CRF / preset / audio bitrate.
- Optional split into N-second parts to upload as several Stories.
- "Copy command" gives you the equivalent FFmpeg command, ready for PowerShell.
- Settings are remembered between sessions.

## Keyboard shortcuts

| Key | Action |
|---|---|
| `Space` | Play / pause |
| `I` / `O` | Set in / out at the playhead |
| `←` / `→` | Previous / next frame |
| `Shift` + `←` / `→` | −1 s / +1 s |
| `Esc` | Stop (twice = back to the start of the video) |
| `Home` / `End` | Go to block start / end |
| `Ctrl` + `Home` / `End` | Go to video start / end |
| Mouse wheel on timeline | Zoom (`Shift` + wheel = pan, double-click = show all) |
| Drag the purple bar / `Ctrl` + drag in the selection | Move the whole fragment |
| `Alt` while dragging | Disable snapping |

## Installation

### Option A — Portable build (recommended)
Download the latest `StoryMaker-vX.Y.Z-win64.zip` from [Releases](../../releases), unzip it anywhere and run `StoryMaker.exe`. FFmpeg and yt-dlp are included; nothing else to install.

> Windows SmartScreen may warn about an unsigned app: click **More info → Run anyway**.

### Option B — Run from source
Requirements: Windows 10/11, [Python 3.10+](https://www.python.org/downloads/) (Tkinter is included in the official installer) and FFmpeg in the `PATH`:

```powershell
winget install --id Gyan.FFmpeg -e
python -m pip install -U yt-dlp      # optional, for YouTube downloads
```

Then double-click `story_maker.pyw`, or run:

```powershell
python story_maker.pyw
```

## How it works

Everything is rendered by FFmpeg with a single `filter_complex`: the source is split in two, one branch is scaled to cover the canvas, blurred and color-adjusted (the background), the other is cropped, scaled and optionally given rounded corners, a border and a shadow (the main video), and both are overlaid. The preview and playback use the same graph scaled down, so what you see is what you export.

## Building the portable release

Releases are built automatically by GitHub Actions ([`.github/workflows/release.yml`](.github/workflows/release.yml)) when a version tag is pushed:

```powershell
git tag v1.0.4
git push origin v1.0.4
```

## License

Story Maker is released under the [MIT License](LICENSE).
The portable build bundles third-party software under their own licenses (FFmpeg is GPLv3) — see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Please only download and publish content you have the rights to use.
