; dopeIPTV for Windows - the installer (NSIS 3), beside the portable zip.
;
; Built by release.yml from the PyInstaller folder (dist\dopeiptv):
;   makensis -DVERSION=1.4.5 -DVERSION4=1.4.5.0 -DARCH=x64 -DSRC=<dist\dopeiptv>
;            -DICON=<dopeIPTV.ico> -DOUT=<dopeIPTV-1.4.5-windows-x64-setup.exe> dopeiptv.nsi
;
; Per user, in %LOCALAPPDATA%\Programs\dopeIPTV: no administrator, no UAC
; prompt, and the app can update itself by running the next installer
; (/update /relaunch /waitpid=<pid> - dopeiptv/core/winupdate.py: only its
; progress, it waits for the app to close and opens the new version; the
; app's first updaters ran it silently, /S /relaunch). A Start menu
; entry, and an entry under Settings -> Apps with Solecraft AB as the
; publisher. An update replaces the program and keeps everything else;
; uninstalling removes everything dopeIPTV kept for the user (see the
; Uninstall section) except their own recordings.

Unicode true
ManifestDPIAware true
SetCompressor /SOLID lzma
RequestExecutionLevel user

!ifndef VERSION | VERSION4 | ARCH | SRC | ICON | OUT
  !error "VERSION, VERSION4, ARCH, SRC, ICON and OUT are given by release.yml"
!endif

!define APP "dopeIPTV"
!define PUBLISHER "Solecraft AB"
!define EXE "dopeiptv.exe"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\dopeIPTV"

Name "${APP}"
OutFile "${OUT}"
InstallDir "$LOCALAPPDATA\Programs\dopeIPTV"
; An update installs where the last version went, also a folder the user chose.
InstallDirRegKey HKCU "${UNINST_KEY}" "InstallLocation"
BrandingText "${APP} ${VERSION} - ${PUBLISHER}"

VIProductVersion "${VERSION4}"
VIAddVersionKey /LANG=0 "ProductName" "${APP}"
VIAddVersionKey /LANG=0 "CompanyName" "${PUBLISHER}"
VIAddVersionKey /LANG=0 "FileDescription" "${APP} installer"
VIAddVersionKey /LANG=0 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=0 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=0 "LegalCopyright" "${PUBLISHER} - GPL-3.0"

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

; An update the app asked for (/update): no pages to click through, only the
; progress, and the window closes itself when done.
Var Updating

!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}"

!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipWhenUpdating
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipWhenUpdating
!insertmacro MUI_PAGE_DIRECTORY
!define MUI_PAGE_CUSTOMFUNCTION_SHOW ShowProgress
!insertmacro MUI_PAGE_INSTFILES
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipWhenUpdating
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; The installer speaks the system's language when it is one of these
; (English otherwise). Only the ones this NSIS ships are included.
!insertmacro MUI_LANGUAGE "English"
!macro LANG NAME
  !if /FileExists "${NSISDIR}\Contrib\Language files\${NAME}.nlf"
    !insertmacro MUI_LANGUAGE "${NAME}"
  !endif
!macroend
!insertmacro LANG "Swedish"
!insertmacro LANG "German"
!insertmacro LANG "French"
!insertmacro LANG "Spanish"
!insertmacro LANG "Italian"
!insertmacro LANG "PortugueseBR"
!insertmacro LANG "Dutch"
!insertmacro LANG "Polish"
!insertmacro LANG "Russian"
!insertmacro LANG "Ukrainian"
!insertmacro LANG "Croatian"
!insertmacro LANG "SerbianLatin"
!insertmacro LANG "Greek"
!insertmacro LANG "Turkish"
!insertmacro LANG "Arabic"
!insertmacro LANG "Hebrew"
!insertmacro LANG "Farsi"
!insertmacro LANG "Hindi"
!insertmacro LANG "Urdu"
!insertmacro LANG "Indonesian"
!insertmacro LANG "Vietnamese"
!insertmacro LANG "Thai"
!insertmacro LANG "Japanese"
!insertmacro LANG "Korean"
!insertmacro LANG "SimpChinese"
!insertmacro LANG "Swahili"

Function .onInit
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "/update" $1
  ${IfNot} ${Errors}
    StrCpy $Updating 1
  ${EndIf}
FunctionEnd

Function SkipWhenUpdating
  ${If} $Updating == 1
    Abort
  ${EndIf}
FunctionEnd

; The app handed this the front (AllowSetForegroundWindow) before it closed.
Function ShowProgress
  ${If} $Updating == 1
    BringToFront
  ${EndIf}
FunctionEnd

Section "dopeIPTV"
  SetOutPath "$INSTDIR"
  ${If} $Updating == 1
    SetAutoClose true
    ; Started by the app while it was still in front (so this window may
    ; come to the front, and the new version after it): the app closes as
    ; this starts, and nothing is replaced before it has, so its settings
    ; are written first. Two minutes at most; a copy still open then is
    ; closed below.
    ${GetParameters} $0
    ClearErrors
    ${GetOptions} $0 "/waitpid=" $1
    ${IfNot} ${Errors}
      System::Call 'kernel32::OpenProcess(i 0x00100000, i 0, i r1) p .r2'
      ${If} $2 != 0
        System::Call 'kernel32::WaitForSingleObject(p r2, i 120000) i .r3'
        System::Call 'kernel32::CloseHandle(p r2)'
      ${EndIf}
    ${EndIf}
  ${EndIf}
  ; An update (silent from the app's first updaters): another copy left open
  ; would hold its files, and the update would skip those - a mix of old and
  ; new files. It is closed first, as the uninstaller does.
  ${If} $Updating == 1
  ${OrIf} ${Silent}
    nsExec::Exec `powershell.exe -NoProfile -NonInteractive -Command "$$p = Get-Process dopeiptv -ErrorAction SilentlyContinue | Where-Object { $$_.Path -like '$INSTDIR\*' }; if ($$p) { $$p | Stop-Process -Force; $$p | Wait-Process -Timeout 10 -ErrorAction SilentlyContinue }"`
    Pop $0
  ${EndIf}
  ; The last version's program files go first, so nothing it had and this
  ; one has not is left behind (the app is closed: by the updater before it
  ; runs this, by hand otherwise - a file in use stops here with NSIS's own
  ; Retry). Only the program's own folder and exe: never the folder itself.
  RMDir /r "$INSTDIR\_internal"
  File /r "${SRC}\*.*"
  WriteUninstaller "$INSTDIR\uninstall.exe"
  CreateShortcut "$SMPROGRAMS\${APP}.lnk" "$INSTDIR\${EXE}"

  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${PUBLISHER}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${EXE}"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegStr HKCU "${UNINST_KEY}" "URLInfoAbout" "https://dopeiptv.org"
  WriteRegStr HKCU "${UNINST_KEY}" "HelpLink" "https://dopeiptv.org/help/"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" $0

  ; dopeiptv:// links open the app (core/commands.py): the per-user
  ; protocol handler, the app's path followed by the link.
  WriteRegStr HKCU "Software\Classes\dopeiptv" "" "URL:dopeIPTV"
  WriteRegStr HKCU "Software\Classes\dopeiptv" "URL Protocol" ""
  WriteRegStr HKCU "Software\Classes\dopeiptv\DefaultIcon" "" '"$INSTDIR\${EXE}",0'
  WriteRegStr HKCU "Software\Classes\dopeiptv\shell\open\command" "" '"$INSTDIR\${EXE}" "%1"'

  ; An update opens the new version while this window still shows - it is
  ; in front, so the new window comes to the front too - and stays a few
  ; seconds while the app starts: no gap with nothing on screen.
  ${If} $Updating == 1
    ${GetParameters} $0
    ClearErrors
    ${GetOptions} $0 "/relaunch" $1
    ${IfNot} ${Errors}
      Exec '"$INSTDIR\${EXE}" --updated'
      Sleep 3000
    ${EndIf}
  ${EndIf}
SectionEnd

; Run by the app's own update (/relaunch): open it again once installed. By
; hand, the last page offers the same. --updated tells the
; app to ask for the front: Windows opens a window started by a process in
; the background behind the one in front (dopeiptv/ui/mw_updates.py).
Function .onInstSuccess
  ${If} $Updating == 1
    Return                      ; opened already, at the end of the section
  ${EndIf}
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "/relaunch" $1
  IfErrors done
    Exec '"$INSTDIR\${EXE}" --updated'
  done:
FunctionEnd

Section "Uninstall"
  ; A copy still running from this folder is closed first, or its files stay.
  nsExec::Exec `powershell.exe -NoProfile -NonInteractive -Command "Get-Process dopeiptv -ErrorAction SilentlyContinue | Where-Object { $$_.Path -like '$INSTDIR\*' } | Stop-Process -Force"`
  Pop $0
  Sleep 1000
  Delete "$SMPROGRAMS\${APP}.lnk"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\${EXE}"
  Delete "$INSTDIR\uninstall.exe"
  ; Empty now unless the user kept something of their own in it: then it stays.
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\Classes\dopeiptv"

  ; Nothing of dopeIPTV is left behind (the list packaging/WINDOWS-README.txt
  ; gives for the portable zip): settings, playlists, favourites and history
  ; (the registry), the guide cache, logos, posters and the rewind buffer's
  ; default folder (%LOCALAPPDATA%\dopeiptv), shortcuts the app made itself,
  ; and an update it downloaded. The user's recordings (Videos\dopeIPTV by
  ; default) are their own files and stay.
  DeleteRegKey HKCU "Software\dopeiptv"
  RMDir /r "$LOCALAPPDATA\dopeiptv"
  Delete "$DESKTOP\${APP}.lnk"
  Delete "$PROFILE\Desktop\${APP}.lnk"
  RMDir /r "$TEMP\dopeIPTV-update"
SectionEnd
