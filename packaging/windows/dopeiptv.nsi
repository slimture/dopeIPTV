; dopeIPTV for Windows - the installer (NSIS 3), beside the portable zip.
;
; Built by release.yml from the PyInstaller folder (dist\dopeiptv):
;   makensis -DVERSION=1.4.5 -DVERSION4=1.4.5.0 -DARCH=x64 -DSRC=<dist\dopeiptv>
;            -DICON=<dopeIPTV.ico> -DOUT=<dopeIPTV-1.4.5-windows-x64-setup.exe> dopeiptv.nsi
;
; Per user, in %LOCALAPPDATA%\Programs\dopeIPTV: no administrator, no UAC
; prompt, and the app can update itself by running the next installer
; silently (/S /relaunch - dopeiptv/core/winupdate.py). A Start menu
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

!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
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

Section "dopeIPTV"
  SetOutPath "$INSTDIR"
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
SectionEnd

; Run silently by the app's own update (/S /relaunch): open it again once
; installed. By hand, the last page offers the same.
Function .onInstSuccess
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "/relaunch" $1
  IfErrors done
    Exec '"$INSTDIR\${EXE}"'
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
