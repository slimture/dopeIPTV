dopeIPTV - Windows (portable)
=============================

RUN
  Unzip this whole folder somewhere, then run dopeiptv.exe.
  Keep the _internal folder next to the exe - don't move the exe out alone.

FIRST LAUNCH - "unknown publisher" / SmartScreen warning
  The app isn't code-signed yet, so Windows may show
  "Windows protected your PC". Click:   More info  ->  Run anyway
  It's only a warning - nothing is blocked or removed.

IF DEFENDER CALLS IT A TROJAN AND DELETES IT
  This is a false positive, and a well-known one. dopeIPTV is built with
  PyInstaller, which wraps a Python program in a small launcher; that
  launcher is the same for every PyInstaller app in the world, and some
  engines flag the pattern itself rather than anything this program does.
  Names you may see: Wacatac, Bearfoos, Zusy, "Trojan:Win32/...".

  What it actually means: nothing was found in dopeIPTV. The whole source
  is public - github.com/slimture/dopeIPTV - and every release is built in
  the open by GitHub Actions from that source; the build log for your exact
  download is on the release page.

  To run it anyway:
    1. Windows Security -> Virus & threat protection
    2. Protection history -> find the dopeIPTV item -> Actions -> Allow
    3. If the file was already deleted, unzip it again after step 2.
  Or exclude the folder you unzipped to:
    Virus & threat protection -> Manage settings ->
    Exclusions -> Add an exclusion -> Folder

  Please also report it to Microsoft as a false positive - it is what
  eventually clears these for everyone:
    https://www.microsoft.com/en-us/wdsi/filesubmission

  The permanent fix is an authenticode signature on the exe, which needs a
  paid certificate. It is on the list.

OPTIONAL - Start-menu / desktop shortcut
  In the app: Settings -> Interface -> Maintenance -> Create shortcut.
  (Each shortcut is a single .lnk file you can delete anytime.)

REMOVING dopeIPTV COMPLETELY (leave no trace)
  1. Delete this app folder (the one you unzipped).
  2. Delete your cache/data folder. Paste this into the Explorer address bar
     and delete the folder it opens:
        %LOCALAPPDATA%\dopeiptv
     (EPG cache, channel logos, posters.)
  3. Delete your settings (playlists, favourites, preferences), which live in
     the registry, not on disk. Open Registry Editor (press Win+R, type
     regedit) and delete this key:
        HKEY_CURRENT_USER\Software\dopeiptv
  4. If you created shortcuts, delete them:
        Desktop\dopeIPTV.lnk
        %APPDATA%\Microsoft\Windows\Start Menu\Programs\dopeIPTV.lnk
  5. If you recorded anything, delete your recordings folder
     (default: %USERPROFILE%\Videos\dopeIPTV, or wherever you set it).

  That's everything - dopeIPTV writes nowhere else on the system.

More info: https://iptv.dope.rs
