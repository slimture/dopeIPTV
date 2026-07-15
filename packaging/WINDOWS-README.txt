dopeIPTV - Windows (portable)
=============================

RUN
  Unzip this whole folder somewhere, then run dopeiptv.exe.
  Keep the _internal folder next to the exe - don't move the exe out alone.

FIRST LAUNCH - "unknown publisher" / SmartScreen warning
  The app isn't code-signed yet, so Windows may show
  "Windows protected your PC". Click:   More info  ->  Run anyway
  It's only a warning - nothing is blocked or removed.

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
