# PubHorror

A desktop library for your horror movie collection. It tracks what you own, what you've watched, and what you thought, plays files and discs you own inside the app, and has a notes system built for people who write about film.

Version 1.0.5. Built with Python and pywebview, with the interface in a single HTML file. Windows installer included.

## Install

Run `PubHorror_Setup_1.0.5.exe`. It installs per-user (no admin needed for PubHorror itself) into `%LocalAppData%\Programs\PubHorror`.

The installer also checks for two things the app depends on and installs them if they're missing:

- **Microsoft Edge WebView2 runtime**, which draws the interface.
- **MakeMKV**, which reads discs. Its installer needs admin, so Windows will show a UAC prompt.

MakeMKV is still on its own license. After install, open it once and enter your registration key.

Your data lives next to the exe in `data\`, `library\` and `posters\`. Upgrades and uninstalls leave those folders alone.

## Library

- Add a single movie, a series folder, a whole folder of movies at once, or a disc.
- Each title has a poster, year, director, cast, categories, description, notes and a source type: File, DVD, Series, or Wishlist (for movies you want but don't have yet).
- Search across titles, directors and cast.
- Filter by collection, watch status (unwatched, watching, watched), minimum star rating, and genre chips.
- Sort A to Z, Z to A, newest, oldest, top rated, or by collection.
- 1 to 5 star ratings.
- Random movie picker.
- Library stats: total titles, watched count, average rating, top category.
- Collections: create, rename and delete your own, and put a movie in as many as you like.
- Resize the poster grid and the interface text with sliders.
- Themes: Horror (default), Midnight, Forest, Classic, or a custom theme where you pick the accent, highlight and background colors.

## Metadata

- **TMDB auto-fill** pulls title, year, director, cast, genres, description and poster. You need your own free TMDB API key (Settings).
- **Wikidata lookup** fills in studio, country of origin, and the Wikidata and IMDb IDs.
- Upload your own poster or pick one from TMDB results.
- Trailers play in the app, using the YouTube trailer TMDB lists.
- **Edition fields** record which version you watched: cut, aspect ratio, release or restoration, region, and format.
- **Content warnings** are per title. A warning screen shows before playback and you choose Play Anyway or Cancel.

## Playback

- Built-in player with custom controls, 10 second skip, volume (remembered between sessions), and fullscreen.
- Keyboard shortcuts: `Space` or `K` play/pause, `F` fullscreen, `M` mute, `C` subtitles, left/right arrows skip 10s, up/down arrows volume, `Esc` leaves fullscreen or closes the player.
- Resume where you left off. A Resume button and a Continue Watching list appear when something is in progress.
- Watched/in-progress state is tracked for you.
- Files stream through a local server that only answers requests from your own machine.

### Series

- Add a folder and episodes are found and sorted automatically.
- Per-episode progress bars and watched ticks.
- "Up next" countdown with Play Now and Cancel, plus a Next button in the player.
- Rescan one series, or all of them, to pick up new episodes.

### Subtitles

- Reads embedded subtitle tracks and sidecar files (`.srt`, `.vtt`, `.ass`, `.ssa`).
- Load a subtitle file by hand if the app doesn't find one.
- Adjust timing delay and text size while playing.

### Audio and video fixes

WebView2 can't play some formats directly, so the app converts them with ffmpeg:

- AC3, DTS, TrueHD and other unsupported audio is converted to AAC.
- DVD video (MPEG-2) is re-encoded to H.264.
- Converted rips are saved so you only wait once.

## Discs

- **Add Disc** lists the drives with a disc in them and tells you whether each is a DVD, Blu-ray, or something it can't play.
- The first time you play a disc title, the app asks to rip it with MakeMKV. This can take 15 to 40 minutes. Progress is tracked and the rip survives you closing the player.
- After the rip, the playable copy is stored in `library\dvd_rips`. From then on the title plays without the disc.
- If the disc isn't in the drive, the app tells you which one to insert.
- Only rip discs you own.

## Notes and research tools

This is the part aimed at film writing and coursework.

- **Timestamped notes** while you watch. Notes on episodes are saved per episode.
- **Tags** on each note: cinematography, sound, score, editing, performance, theme, symbolism, production design, continuity, context. You can type your own.
- **Passage notes**: press ⏱ In to mark the start, then Add Note at the end to attach a note to a range.
- **Jump scare bookmarks** with the 💀 button. Skulls show on the progress bar where you marked them.
- **Annotation search** looks through every note in your whole library by text, title or tag.
- **Citations**: a note can be turned into a Chicago or MLA citation. It includes the director, studio, year, edition and the timestamp.
- **Export notes** as `.txt`, `.md` or `.csv`.
- **Export clip**: cut a passage out of a note as an `.mp4`. A text file with the source, passage, edition, tag, note and citation is saved beside it.

## Backups

- An automatic backup is saved each time the app starts. The 10 most recent are kept.
- Restore any recent backup from Settings.
- Export a full backup to a file, or import one, to move your library to another machine.
- Backups include movies, ratings, collections and settings.

## Requirements

- Windows 10 or 11, 64-bit.
- WebView2 runtime (the installer handles it).
- MakeMKV, only for playing discs (the installer handles it).
- ffmpeg, for audio and video conversion, subtitle extraction, and clip export. The app looks for `ffmpeg.exe` next to PubHorror or on your PATH.
- A TMDB API key, only for auto-fill and trailers.

## Building

1. Run `build.bat`. This produces `dist\PubHorror\`.
2. Put `MicrosoftEdgeWebview2Setup.exe` and `MakeMKV_setup.exe` next to `PubHorror.iss`.
3. Compile `PubHorror.iss` with Inno Setup. The output is `PubHorror_Setup_<version>.exe`.

## Disclaimer

PubHorror reads and rips discs you own, for your own use. Check the laws where you live before doing so. Clip export is meant for criticism, comment and scholarship, and the source record it writes next to each clip is there to support that.
