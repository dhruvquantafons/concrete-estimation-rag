; NSIS script for BuildMoreAI-Setup.exe. Built on macOS by build_on_mac.sh:
;   makensis -DSTAGE=<staged app folder> -DOUTFILE=<setup.exe> installer.nsi
; Per-user install (no admin prompt). User data and downloaded resources live in %LOCALAPPDATA%\BuildMoreAI
; (written by the app, not by this installer) and are kept on uninstall, so a reinstall doesn't download again.
Unicode true
!include "MUI2.nsh"

!define APPNAME "BuildMore AI"
!define VERSION "0.1.0"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\BuildMoreAI"
!define WV2 "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
!define LAUNCH_EXE "$INSTDIR\python\pythonw.exe"
!define LAUNCH_ARGS "-m concrete_rag.desktop.launcher"

Name "${APPNAME}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\BuildMoreAI"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
BrandingText "${APPNAME} ${VERSION}"

!define MUI_ICON "${STAGE}\buildmore.ico"
!define MUI_UNICON "${STAGE}\buildmore.ico"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Open ${APPNAME} now"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApp
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function LaunchApp
  SetOutPath "$INSTDIR"
  Exec '"${LAUNCH_EXE}" ${LAUNCH_ARGS}'
FunctionEnd

; Edge WebView2 runtime draws the app window. Built into Windows 11; some Windows 10 PCs lack it.
Function EnsureWebView2
  SetRegView 64
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\${WV2}" "pv"
  StrCmp $0 "" 0 done
  ReadRegStr $0 HKLM "SOFTWARE\Microsoft\EdgeUpdate\Clients\${WV2}" "pv"
  StrCmp $0 "" 0 done
  ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\${WV2}" "pv"
  StrCmp $0 "" 0 done
  DetailPrint "Installing Microsoft Edge WebView2..."
  InitPluginsDir
  File "/oname=$PLUGINSDIR\wv2setup.exe" "${WV2SETUP}"
  ExecWait '"$PLUGINSDIR\wv2setup.exe" /silent /install'
  done:
  SetRegView lastused
FunctionEnd

Section "Install"
  SetOutPath "$INSTDIR"
  ; upgrade: replace program files; user data is elsewhere
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\engine"
  File /r "${STAGE}\*"

  Call EnsureWebView2

  SetOutPath "$INSTDIR"
  CreateShortcut "$SMPROGRAMS\${APPNAME}.lnk" "${LAUNCH_EXE}" "${LAUNCH_ARGS}" "$INSTDIR\buildmore.ico" 0
  CreateShortcut "$DESKTOP\${APPNAME}.lnk" "${LAUNCH_EXE}" "${LAUNCH_ARGS}" "$INSTDIR\buildmore.ico" 0

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "Publisher" "QuantaFONS"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\buildmore.ico"
  WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  Delete "$SMPROGRAMS\${APPNAME}.lnk"
  Delete "$DESKTOP\${APPNAME}.lnk"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTKEY}"
SectionEnd
