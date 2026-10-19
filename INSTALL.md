# Installing ReefHue – step by step

This guide assumes no experience with programming or GitHub. It takes about 15 minutes; most of it
is waiting for downloads. You need an internet connection for the first start.

- [Mac (Apple Silicon: M1, M2, M3, M4 …)](#mac)
- [Windows 10 / 11](#windows)
- [Videos: installing FFmpeg (optional)](#videos-installing-ffmpeg-optional)
- [Updating ReefHue](#updating-reefhue) · [Uninstalling](#uninstalling) · [Something went wrong?](#something-went-wrong)

ReefHue consists of two parts: **Python** (a free program that ReefHue runs on, installed once) and
the **ReefHue folder** (which you download and keep anywhere you like). Photos work right away;
for videos you additionally need **FFmpeg** (also free).

---

## Mac

*Not sure which Mac you have? Apple menu  → About This Mac. "Chip: Apple M…" means Apple Silicon.
ReefHue is made for these Macs.*

### Step 1 – Install Python (once)

1. Open <https://www.python.org/downloads/release/python-31210/>
2. Scroll down to **Files** and click **macOS 64-bit universal2 installer**.
   A file `python-3.12.10-macos11.pkg` is downloaded.
3. Open it (in the *Downloads* folder) and click **Continue**, **Continue**, **Agree**, **Install**.
   Enter your Mac password when asked.
4. When it says "The installation was successful", click **Close**. A Finder window called
   *Python 3.12* may open – you can close it. Move the installer to the Trash if asked.

### Step 2 – Download ReefHue

1. Open <https://github.com/dadeppa/ReefHue/releases/latest>
2. Under **Assets**, click **ReefHue-…zip** (not "Source code").
3. Open your *Downloads* folder. Safari unpacks the ZIP automatically; otherwise double-click it.
   You now have a folder called **ReefHue**.
4. **Move the ReefHue folder into your home folder** (in Finder: *Go → Home*, the folder with the
   house icon – drag ReefHue into it). Do not put it into *Applications* – ReefHue needs to write
   into its own folder.

### Step 3 – Start ReefHue for the first time

1. Open the ReefHue folder and **double-click `start.command`**.
2. macOS will say **"start.command" Not Opened – Apple could not verify …**. This is normal for free
   apps that are not sold through Apple. Click **Done**.
3. Open **System Settings** ( → System Settings…) → **Privacy & Security**. Scroll down to the
   **Security** section. It says *"start.command" was blocked …*. Click **Open Anyway**, then
   **Open Anyway** again and enter your password (or use Touch ID).
   *(On older macOS versions you may instead see an **Open** button directly in the first message.)*
4. A **Terminal** window (white or black window with text) opens. The first time, ReefHue now
   downloads what it needs – this takes **a few minutes** and shows a lot of text. Just wait.
5. The ReefHue window appears. Done!

**Keep the Terminal window open** while you use ReefHue – it closes ReefHue when you close it. When
you quit ReefHue, the Terminal window shows *[Process completed]* and can be closed.

**From now on:** double-click `start.command` – ReefHue starts in a few seconds. Tip: drag
`start.command` to the right side of your Dock (next to the Trash) for one-click starting.

### Step 4 – Open your photos

Click **Open folder…** and choose a folder with dive photos or videos. If macOS asks
*"Terminal" would like to access files in your Documents / Desktop / Downloads folder* (or on an
external drive), click **Allow**.

To try ReefHue without your own pictures, download **ReefHue-samples.zip** from the same
[download page](https://github.com/dadeppa/ReefHue/releases/latest) and open its folder.

---

## Windows

### Step 1 – Install Python (once)

1. Open <https://www.python.org/downloads/release/python-31210/>
2. Scroll down to **Files** and click **Windows installer (64-bit)** (not the "install manager").
   A file `python-3.12.10-amd64.exe` is downloaded.
3. Open it. **Important:** at the bottom of the first window, tick **Add python.exe to PATH**.
   Then click **Install Now**, and **Yes** if Windows asks for permission.
4. When it says "Setup was successful", click **Close**.

### Step 2 – Download ReefHue

1. Open <https://github.com/dadeppa/ReefHue/releases/latest>
2. Under **Assets**, click **ReefHue-…zip** (not "Source code").
3. Open your *Downloads* folder, **right-click the ZIP → Extract All… → Extract**.
   *Do not start ReefHue from inside the ZIP – it must be extracted first.*
4. Windows creates a folder *ReefHue-…* with the **ReefHue** folder inside. Move the **ReefHue**
   folder to a place you like, e.g. your *Documents* folder.

### Step 3 – Start ReefHue for the first time

1. Open the ReefHue folder and **double-click `start.bat`**.
   (If file extensions are hidden, it is the one called *start* with a gear/terminal icon.)
2. Windows may warn **"Windows protected your PC"**. Click **More info**, then **Run anyway**.
   (Or, in a box "Open File – Security Warning", click **Run**.)
3. A black window with text opens. The first time, ReefHue downloads what it needs – this takes
   **a few minutes**. Just wait.
4. The ReefHue window appears. Done!

**Keep the black window open** while you use ReefHue. **From now on:** double-click `start.bat`.
Tip: right-click `start.bat` → *Show more options* → *Send to* → *Desktop (create shortcut)*.

### Step 4 – Open your photos

Click **Open folder…** and choose a folder with dive photos or videos. To try ReefHue without
your own pictures, download **ReefHue-samples.zip** from the
[download page](https://github.com/dadeppa/ReefHue/releases/latest), extract it and open its folder.

---

## Videos: installing FFmpeg (optional)

Without FFmpeg ReefHue edits photos only; videos are listed but cannot be played or exported.
ReefHue shows a yellow note with a **How to fix this** button as long as FFmpeg is missing.

### Mac

FFmpeg is installed with **Homebrew**, a free installer for such tools:

1. Open **Terminal** (press `Cmd + Space`, type *Terminal*, press Return).
2. Go to <https://brew.sh>, copy the command shown there (it starts with `/bin/bash -c`), paste it
   into Terminal and press Return. Enter your Mac password when asked (you will not see it while
   typing – that is normal) and press Return to confirm. This takes a few minutes.
3. Then copy this line into Terminal and press Return:

   ```
   /opt/homebrew/bin/brew install ffmpeg
   ```

4. When it is finished, start ReefHue again (or, if it is running: *Help → FFmpeg help… →
   Check again*). Video features are now available.

*(At the end of step 2 Homebrew suggests "Next steps" to add it to your PATH – you can do that, but
ReefHue does not need it.)*

### Windows

1. Open <https://www.gyan.dev/ffmpeg/builds/> and, under **release builds**, download
   **ffmpeg-release-essentials.zip**.
2. Extract it (right-click → *Extract All…*). Inside, open the folder `ffmpeg-…-essentials_build`
   and then its **bin** folder.
3. Copy **ffmpeg.exe** and **ffprobe.exe** into the **ffmpeg** folder inside your ReefHue folder.
4. Start ReefHue again (or *Help → FFmpeg help… → Check again*).

---

## Updating ReefHue

1. Download the new ZIP from the [download page](https://github.com/dadeppa/ReefHue/releases/latest)
   and unpack it.
2. Start the new version with `start.command` / `start.bat` (the first start downloads again).
3. If you imported your own LUTs, copy them from the `luts` folder of the old version into the new
   one; on Windows also copy `ffmpeg.exe` and `ffprobe.exe`. Then delete the old ReefHue folder.

Your edits are stored next to your photos (in `reefhue.project.json`) and your settings and presets
in your user account – they are kept.

## Uninstalling

Delete the ReefHue folder. To also remove your presets, logs and thumbnails, delete these folders:

- Mac (*Finder → Go → Go to Folder…*): `~/Library/Application Support/ReefHue` and `~/Library/Caches/ReefHue`
- Windows (type into the Explorer address bar): `%APPDATA%\ReefHue` and `%LOCALAPPDATA%\ReefHue`

Python can stay (other programs may use it) or be removed: Mac – delete *Python 3.12* from
*Applications*; Windows – *Settings → Apps → Installed apps → Python 3.12 → Uninstall*.

The `reefhue.project.json` files in your photo folders contain your edits; delete them only if you
no longer need the edits.

## Something went wrong?

- **The Terminal / black window says ReefHue needs Python** – Python is not installed or is too
  old. Repeat Step 1. On Windows, make sure *Add python.exe to PATH* was ticked (run the Python
  installer again and choose *Modify* if needed).
- **"Installing the dependencies failed"** – check your internet connection. Then delete the hidden
  folder `.venv` inside the ReefHue folder (Mac: press `Cmd + Shift + .` in Finder to show hidden
  files) and start again.
- **Mac: nothing happens / "start.command" is blocked again** – repeat Step 3.3 (Privacy &
  Security → Open Anyway).
- **Mac: "ReefHue is not allowed to read the folder"** – open *System Settings → Privacy & Security
  → Files and Folders*, find **Terminal** and switch on the folder (e.g. *Documents Folder*), then
  open the folder in ReefHue again.
- **Still stuck?** In ReefHue choose *Help → About → Copy system info* and *Help → Open log folder*,
  and report the problem at <https://github.com/dadeppa/ReefHue/issues> with that information and
  the file `reefhue.log`.
