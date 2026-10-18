# Third-party software

The source code in this repository is MIT-licensed. The **portable release** (`StoryMaker-vX.Y.Z-win64.zip`) also bundles the following programs, each under its own license. Their license texts are included in the `licenses` folder of the release.

| Component | Used for | License | Source |
|---|---|---|---|
| FFmpeg (`ffmpeg.exe`, `ffprobe.exe`) — "essentials" build by Gyan Doshi | All video/audio processing, preview and playback | GPLv3 | https://ffmpeg.org · builds: https://www.gyan.dev/ffmpeg/builds/ |
| yt-dlp (`yt-dlp.exe`) | YouTube downloads | Unlicense | https://github.com/yt-dlp/yt-dlp |
| Python + Tkinter (bundled by PyInstaller) | Runtime and user interface | PSF License / Tcl-Tk license | https://www.python.org · https://www.tcl.tk |
| PyInstaller bootloader | Packaging the executable | GPLv2 with bootloader exception | https://pyinstaller.org |

FFmpeg and yt-dlp are shipped as separate, unmodified executables that Story Maker runs as external processes. The corresponding FFmpeg source code is available from the links above.
