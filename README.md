# PubHorror

PubHorror is a desktop library for the movies and shows you own. It keeps track of what's in your collection, what you've watched and what you thought of it. It plays your video files and discs inside the app, and it has a notes system made for people who write about film.

The look is horror, but it works for any kind of media.

**Version 1.0.7** · Windows 10/11 (64-bit) · Python + pywebview, with the whole interface in one HTML file.

**Download:** [trinketgoblin.itch.io/pubhorror](https://trinketgoblin.itch.io/pubhorror) (pay what you want, free is fine)
**Community:** [Discord](https://discord.gg/NvfEq4myu3)
**User guide:** [Instructions.html](Instructions.html)

---

## Contents

- [Install](#install)
- [What it does](#what-it-does)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Where your data lives](#where-your-data-lives)
- [Privacy and network use](#privacy-and-network-use)
- [Troubleshooting](#troubleshooting)
- [Building from source](#building-from-source)
- [Project layout](#project-layout)
- [Releasing](#releasing)
- [Third-party software](#third-party-software)
- [Support the project](#support-the-project)
- [License](#license)
- [Disclaimer](#disclaimer)

---

## Install

1. Download `PubHorror_Setup_1.0.7.exe` from [itch.io](https://trinketgoblin.itch.io/pubhorror) or the [GitHub releases](https://github.com/TrinketGoblin/PubHorror/releases/latest).
2. Run it. PubHorror installs for your user account only, into `%LocalAppData%\Programs\PubHorror`. It doesn't need admin rights.
3. If the Microsoft Edge WebView2 runtime is missing, the installer adds it. ffmpeg comes bundled.
4. **MakeMKV is not included.** You only need it to play discs. If it isn't installed, the last screen of the installer has a checkbox that opens the [MakeMKV download page](https://www.makemkv.com/download/), and the app offers the same link the first time you play a disc. DVD ripping in MakeMKV is free. Blu-ray needs a MakeMKV key.

Upgrading: run the newer installer over the old one. Your library, notes and posters are kept.

Uninstalling: use Windows' "Apps & features". The `data\`, `library\` and `posters\` folders are left behind on purpose, so you don't lose your library by accident. Delete them yourself if you want everything gone.

---

## What it does

### Library

Click a poster to play it. Click ✏ or right-click a poster to edit it.

- Four ways to add things, all from the **＋** menu:
  - **Add Movie** opens a blank entry you fill in yourself, or with TMDB.
  - **Add Series** takes a folder and finds the episodes inside it.
  - **Import Folder** adds every video in a folder as its own movie.
  - **Add Disc** adds a DVD or Blu-ray from a drive.
- Every title has a poster, year, director, cast, categories, description, notes, content warnings and a source type: **File**, **DVD**, **Series** or **Wishlist** (for movies you want but don't own yet).
- Search covers titles, directors and cast.
- You can filter by collection, by watch status (unwatched, watching, watched), by minimum star rating and by genre chips.
- Sorting options: A→Z, Z→A, newest, oldest, top rated, or grouped by collection.
- 1–5 star ratings, a random movie picker, and a stats panel (total titles, watched count, average rating, top category).
- **Collections**: make, rename and delete your own. A movie can be in as many as you like.
- Sliders resize the poster grid and the interface text.
- Themes: Horror (the default), Midnight, Forest, Classic, or Custom, where you pick the accent, highlight and background colors yourself.
- The interface uses the OpenDyslexic font throughout.

### Metadata

- **TMDB auto-fill** fetches the title, year, director, cast, genres, description and poster, and lets you pick the right match from the search results. A key is built in. You can paste your own free TMDB key in Settings if you'd rather use that.
- **Wikidata lookup** fills in the studio, country of origin, Wikidata ID and IMDb ID.
- Posters: upload your own, or use the one TMDB gives you.
- **Trailers** play inside the app, using the YouTube trailer TMDB lists for the film.
- **Edition fields** record which version you watched: cut, aspect ratio, release or restoration, region and format.
- **Content warnings** are set per title, as a comma-separated list. When a title has any, a warning screen appears before it plays, with **Play Anyway** and **Cancel** buttons.

### Playback

- The built-in player has 10-second skips, volume (remembered between sessions) and fullscreen.
- **Resume** picks up where you stopped. A Resume button appears in the toolbar while something is in progress.
- Watched and in-progress status updates on its own as you watch.
- Files play through a small local server on `127.0.0.1` that needs a per-session token, so nothing else on your network can reach it.
- Supported files: `.mp4 .mkv .avi .mov .webm .m4v .m4a .wav .ac3`.

**Format fixes.** The player is built on Edge WebView2, and some common formats won't play in it directly. PubHorror converts those with ffmpeg:

| Problem | Fix |
|---|---|
| AC3, DTS, TrueHD or other audio | Converted to AAC |
| MPEG-2 video (DVDs) | Re-encoded to H.264 |

A converted copy is saved, so each file only gets converted once.

### Series

- Episodes are found and sorted on their own. Season folders such as `Season 1`, `S01` or `Series 2` are recognized.
- Each episode shows a progress bar and a watched tick.
- When an episode ends, an **Up next** countdown starts, with **Play Now** and **Cancel**. The player also has a **Next** button.
- **Rescan** picks up newly added episodes. Use it from the edit screen for one series, or from the toolbar for all of them.

### Subtitles

- PubHorror reads subtitle tracks embedded in the video, plus sidecar files sitting next to it: `.srt`, `.vtt`, `.ass` and `.ssa`.
- If it doesn't find one, you can load a subtitle file by hand.
- While a video plays, the **CC** menu lets you shift subtitle timing in half-second steps and change the text size. The size you pick is remembered.

### Discs

- **Add Disc** lists the drives that have a disc in them, and says whether each disc is a DVD, a Blu-ray, or something it can't play.
- The first time you play a disc title, PubHorror asks to rip it with MakeMKV. That takes roughly 15–40 minutes. You can watch the progress, and closing the player doesn't stop the rip.
- Once the rip is done, the converted copy is saved in `library\dvd_rips\`. After that the title plays without the disc.
- If you try to play a disc title that's never been ripped and the disc isn't in the drive, PubHorror tells you which disc to put in.

### Notes and research tools

This part is for film writing, reviews and coursework.

- **Timestamped notes** while you watch. Episode notes are saved per episode.
- **Tags** on each note: cinematography, sound, score, editing, performance, theme, symbolism, production design, continuity and context, or any tag you type yourself.
- **Passage notes**: press **⏱ In** where the passage starts, then **Add Note** where it ends. The note covers that whole range.
- **Jump scare bookmarks** with the 💀 button. A skull appears on the progress bar at each one.
- **Annotations** (🔎) searches every note in your library by text, title or tag.
- **Citations**: turn any note into a Chicago or MLA citation. It includes the director, studio, year, edition and timestamp.
- **Export notes** as `.txt`, `.md` or `.csv`.
- **Export clip**: save a noted passage as an `.mp4`. PubHorror also saves a `_source.txt` file beside the clip, recording the film, passage, edition, tag, note and citation.

### Backups

- A backup is saved automatically every time the app starts, in `data\backups\`. The 10 most recent are kept.
- You can restore any of those backups from Settings.
- **Export Backup** and **Import Backup** move your whole library (movies, ratings, collections, settings) to another computer.

---

## Keyboard shortcuts

These work while the player is open, as long as you aren't typing in a text box.

| Key | Action |
|---|---|
| `Space` or `K` | Play / pause |
| `→` | Forward 10 seconds |
| `←` | Back 10 seconds |
| `↑` | Volume up |
| `↓` | Volume down |
| `M` | Mute |
| `F` | Fullscreen |
| `C` | Subtitles on/off |
| `Esc` | Leave fullscreen, or close the player |

---

## Where your data lives

All of it is next to `PubHorror.exe` (by default `%LocalAppData%\Programs\PubHorror`):

| Path | What's in it |
|---|---|
| `data\movies.json` | Your library: titles, metadata, notes |
| `data\collections.json` | Collections |
| `data\ratings.json` | Star ratings |
| `data\progress.json` | Resume positions and watched status |
| `data\config.json` | Settings |
| `data\backups\` | The automatic backups |
| `data\rip_log.txt` | MakeMKV rip log |
| `data\ffmpeg_last.log` | Output from the most recent ffmpeg conversion |
| `posters\` | Poster images |
| `library\dvd_rips\` | Ripped and converted discs |

Your video files aren't copied or moved. PubHorror only stores the path to each one.

**Moving disc rips to another drive.** A ripped DVD takes several GB. To keep rips off your C: drive, close PubHorror and add this line to `data\config.json`:

```json
"dvd_rips_path": "D:/dvd_rips"
```

---

## Privacy and network use

PubHorror has no account, no telemetry and no analytics. It only goes online when you use a feature that needs to:

| Feature | Contacts |
|---|---|
| TMDB auto-fill and poster search | `api.themoviedb.org`, `image.tmdb.org` |
| Wikidata lookup | `www.wikidata.org` |
| Trailers | YouTube (embedded player) |
| "Get MakeMKV" link | Opens `makemkv.com` in your browser |

Everything else, including playback, notes, backups, conversion and ripping, runs on your own computer.

---

## Troubleshooting

**The app opens to a blank or white window.**
The WebView2 runtime is missing or broken. Install it from [Microsoft](https://developer.microsoft.com/microsoft-edge/webview2/) using the "Evergreen Bootstrapper", then reopen PubHorror.

**A file has video but no sound, or won't play at all.**
The format probably needs converting, which happens on its own the first time you press play. If it fails, the error is recorded in `data\ffmpeg_last.log`. Check that `ffmpeg.exe` and `ffprobe.exe` are still in the PubHorror folder. Reinstalling puts them back.

**"MakeMKV not found."**
Install MakeMKV from [makemkv.com](https://www.makemkv.com/download/) and keep the default install location (`C:\Program Files (x86)\MakeMKV\`). PubHorror looks for it there.

**The disc shows up but won't rip.**
- Open MakeMKV itself once and see whether it can read the disc. If MakeMKV can't, PubHorror can't either.
- MakeMKV needs regular updates to keep up with disc protection. Update it if your copy is old.
- Blu-rays need a MakeMKV key once the trial runs out.
- Scratched discs often fail. Try cleaning the disc.
- `data\rip_log.txt` shows what happened.

**A rip or conversion failed with "disk full".**
Free up space on C:, or move your rips to another drive with `dvd_rips_path` (see [Where your data lives](#where-your-data-lives)). Then press play again. If the rip itself already finished, PubHorror converts the existing rip instead of ripping the disc again.

**TMDB auto-fill finds nothing.**
- Try the title without the year, or check the spelling.
- If it never finds anything at all, paste your own free key from [themoviedb.org](https://www.themoviedb.org/settings/api) into Settings.

**Subtitles are out of sync.**
Click **CC** in the player and use the **Sync** buttons (−0.5s / +0.5s). Subtitles that show up too late need −0.5s. **A−** and **A+** next to them change the text size.

**My library is empty after a crash.**
Your video files aren't touched. Open **Settings** and restore one of the recent automatic backups. They're taken every time the app starts.

**Moving to a new computer.**
On the old computer, use **Settings → Export Backup**. Install PubHorror on the new one and use **Import Backup**. If your video files are in a different place on the new machine, edit each title to point at the new location.

Still stuck? Ask in the [Discord](https://discord.gg/NvfEq4myu3) or open a GitHub issue. Include whatever's in `data\ffmpeg_last.log` or `data\rip_log.txt`.

---

## Building from source

### Requirements

- Windows 10/11, 64-bit
- Python 3.13 (other recent 3.x versions probably work)
- [Inno Setup 6](https://jrsoftware.org/isinfo.php), for the installer

### Run it without building

```bat
cd "source code"
python -m venv venv
venv\Scripts\activate
pip install "pywebview[http]" requests
python main.py
```

Put `ffmpeg.exe` and `ffprobe.exe` on your PATH, or next to `main.py`, so conversion works.

### Build the app and installer

1. In `source code\`, run `build.bat`. It creates a fresh virtual environment, installs the dependencies and runs PyInstaller with `PubHorror.spec`. The output goes to `dist\PubHorror\`.
2. Put these next to `PubHorror.iss`, in `source code\`:
   - `MicrosoftEdgeWebview2Setup.exe`, the [WebView2 Evergreen Bootstrapper](https://developer.microsoft.com/microsoft-edge/webview2/)
   - `THIRD_PARTY_NOTICES.txt` (already in the repo)
   - an `ffmpeg\` folder containing `ffmpeg.exe`, `ffprobe.exe` and that build's `LICENSE.txt`. The release builds use the gyan.dev 9.0.2 "essentials" build.
3. When `build.bat` asks, say yes to running Inno Setup, or compile `PubHorror.iss` yourself. The installer is written to the repo root as `PubHorror_Setup_<version>.exe`.

**Don't bundle MakeMKV** in the installer or in the repo. See [Third-party software](#third-party-software) for why.

The version number is set in two places: `MyAppVersion` in `PubHorror.iss`, and the top of this README.

---

## Project layout

```
PubHorror/
├─ README.md
├─ Instructions.html          user guide
├─ LICENSE                    CC0 1.0
└─ source code/
   ├─ main.py                 Python backend (pywebview API, ripping, ffmpeg, local media server)
   ├─ app/
   │  ├─ index.html           the entire interface (HTML/CSS/JS)
   │  └─ assets/              OpenDyslexic fonts, app icon
   ├─ PubHorror.spec          PyInstaller config
   ├─ PubHorror.iss           Inno Setup installer script
   ├─ build.bat               one-click build
   ├─ requirements.txt
   ├─ THIRD_PARTY_NOTICES.txt
   └─ ffmpeg/                 (not in git) ffmpeg.exe, ffprobe.exe, LICENSE.txt
```

`.exe` files, the `ffmpeg\` folder and source tarballs are git-ignored. Installers belong on the releases page, not in the repo.

---

## Releasing

1. Bump `MyAppVersion` in `PubHorror.iss` and the version at the top of this README.
2. Run `build.bat` and compile the installer.
3. Create a GitHub release and attach:
   - `PubHorror_Setup_<version>.exe`
   - `ffmpeg-9.0.2.tar.xz`, the exact FFmpeg source from [ffmpeg.org/releases](https://ffmpeg.org/releases/). The GPL requires this.
4. Upload the same two files to the itch.io page.

If you change ffmpeg builds, update the version in `THIRD_PARTY_NOTICES.txt`, in this README, and in the source tarball you attach.

---

## Third-party software

**FFmpeg 9.0.2** ("essentials" build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)) is bundled under the GNU GPL v3.
- The license is installed in `licenses\` next to the app.
- The complete source for this exact version, `ffmpeg-9.0.2.tar.xz`, is attached to every PubHorror release. The build's configuration and library list are published on gyan.dev.
- PubHorror runs ffmpeg as a separate program and doesn't modify it. Because of that, PubHorror's own code isn't covered by the GPL.

**MakeMKV** is not included or distributed with PubHorror. PubHorror only calls it if you've installed it yourself. It's made by GuinpinSoft inc. and has its own license. It isn't bundled because it decrypts discs: distributing it is legally sensitive in some countries, and bundled copies go out of date quickly.

**Microsoft Edge WebView2** runtime is installed by Microsoft's own bootstrapper, under Microsoft's terms.

**OpenDyslexic** font, by Abbie Gonzalez, under the SIL Open Font License.

**Python packages** bundled by PyInstaller: pywebview (BSD), requests (Apache 2.0) and their dependencies, each under its own license.

**Data sources:** This product uses the TMDB API but is not endorsed or certified by TMDB. Wikidata content is CC0.

---

## Support the project

PubHorror is free. If it's useful to you and you want to chip in, you can pay what you like on [itch.io](https://trinketgoblin.itch.io/pubhorror). Bug reports and feature ideas in the [Discord](https://discord.gg/NvfEq4myu3) help just as much.

---

## License

PubHorror's own code is released under [CC0 1.0](LICENSE), so you can do whatever you want with it. The bundled third-party software keeps its own licenses (see above).

---

## Disclaimer

PubHorror is a personal library tool. It reads and rips discs you own, for your own use. It doesn't come with anything that breaks copy protection: disc reading happens in MakeMKV, which you install separately. The laws on ripping discs vary by country, so check yours. Clip export is meant for criticism, commentary and scholarship, and the source record saved with each clip is there to support that.

We don't encourage or condone piracy.
