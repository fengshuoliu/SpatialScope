Unicode true

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!ifndef APP_VERSION
  !error "APP_VERSION must be supplied by build_native.ps1"
!endif
!ifndef SOURCE_DIR
  !error "SOURCE_DIR must be supplied by build_native.ps1"
!endif
!ifndef OUTPUT_DIR
  !error "OUTPUT_DIR must be supplied by build_native.ps1"
!endif
!ifndef ICON_PATH
  !error "ICON_PATH must be supplied by build_native.ps1"
!endif

!define APP_NAME "SpatialScope"
!define APP_PUBLISHER "SpatialScope"
!define APP_REGISTRY_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\SpatialScope"
!define INSTALL_MARKER ".spatialscope-install"
!define SMOKE_MARKER ".spatialscope-smoke"

Var SmokeMode

Name "${APP_NAME}"
OutFile "${OUTPUT_DIR}\SpatialScope-Windows-x64-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\SpatialScope"
InstallDirRegKey HKCU "${APP_REGISTRY_KEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
BrandingText "SpatialScope ${APP_VERSION}"
Icon "${ICON_PATH}"
UninstallIcon "${ICON_PATH}"

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=1033 "ProductName" "SpatialScope for Windows"
VIAddVersionKey /LANG=1033 "ProductVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1033 "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Copyright SpatialScope contributors"
VIAddVersionKey /LANG=1033 "FileDescription" "SpatialScope installer"
VIAddVersionKey /LANG=1033 "FileVersion" "${APP_VERSION}.0"

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\SpatialScope.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Launch SpatialScope"
!define MUI_UNFINISHPAGE_NOAUTOCLOSE

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "English"

Function .onInit
  StrCpy $SmokeMode "0"
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/SMOKETEST" $R1
  ${IfNot} ${Errors}
    StrCpy $SmokeMode "1"
  ${EndIf}
FunctionEnd

Function un.onInit
  StrCpy $SmokeMode "0"
  IfFileExists "$INSTDIR\${SMOKE_MARKER}" 0 +2
    StrCpy $SmokeMode "1"
FunctionEnd

Section "SpatialScope" SEC_MAIN
  SetShellVarContext current

  ; Only replace files in a directory that a previous SpatialScope installer
  ; explicitly marked as its own. This prevents a custom installation path
  ; from losing an unrelated user-owned "engine" directory.
  IfFileExists "$INSTDIR\${INSTALL_MARKER}" owned_install 0
  IfFileExists "$INSTDIR\*.*" 0 fresh_install
    MessageBox MB_OK|MB_ICONSTOP "The selected folder is not empty and is not owned by SpatialScope. Choose an empty folder or the existing SpatialScope installation folder." /SD IDOK
    SetErrorLevel 2
    Abort

  owned_install:
    Delete "$INSTDIR\SpatialScope.exe"
    RMDir /r "$INSTDIR\engine"

  fresh_install:
  SetOutPath "$INSTDIR"
  File "${SOURCE_DIR}\SpatialScope.exe"
  File /r "${SOURCE_DIR}\engine"

  WriteUninstaller "$INSTDIR\Uninstall SpatialScope.exe"

  FileOpen $R0 "$INSTDIR\${INSTALL_MARKER}" w
  FileWrite $R0 "SpatialScope ${APP_VERSION}$\r$\n"
  FileClose $R0

  ${If} $SmokeMode == "1"
    FileOpen $R0 "$INSTDIR\${SMOKE_MARKER}" w
    FileWrite $R0 "Installer smoke test$\r$\n"
    FileClose $R0
  ${Else}
    Delete "$INSTDIR\${SMOKE_MARKER}"

    CreateDirectory "$SMPROGRAMS\SpatialScope"
    CreateShortcut "$SMPROGRAMS\SpatialScope\SpatialScope.lnk" "$INSTDIR\SpatialScope.exe" "" "$INSTDIR\SpatialScope.exe"
    CreateShortcut "$SMPROGRAMS\SpatialScope\Uninstall SpatialScope.lnk" "$INSTDIR\Uninstall SpatialScope.exe"
    CreateShortcut "$DESKTOP\SpatialScope.lnk" "$INSTDIR\SpatialScope.exe" "" "$INSTDIR\SpatialScope.exe"

    WriteRegStr HKCU "${APP_REGISTRY_KEY}" "DisplayName" "SpatialScope"
    WriteRegStr HKCU "${APP_REGISTRY_KEY}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "${APP_REGISTRY_KEY}" "Publisher" "${APP_PUBLISHER}"
    WriteRegStr HKCU "${APP_REGISTRY_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${APP_REGISTRY_KEY}" "DisplayIcon" "$INSTDIR\SpatialScope.exe"
    WriteRegStr HKCU "${APP_REGISTRY_KEY}" "UninstallString" "$\"$INSTDIR\Uninstall SpatialScope.exe$\""
    WriteRegDWORD HKCU "${APP_REGISTRY_KEY}" "NoModify" 1
    WriteRegDWORD HKCU "${APP_REGISTRY_KEY}" "NoRepair" 1
  ${EndIf}
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  ${If} $SmokeMode != "1"
    Delete "$DESKTOP\SpatialScope.lnk"
    Delete "$SMPROGRAMS\SpatialScope\SpatialScope.lnk"
    Delete "$SMPROGRAMS\SpatialScope\Uninstall SpatialScope.lnk"
    RMDir "$SMPROGRAMS\SpatialScope"
    DeleteRegKey HKCU "${APP_REGISTRY_KEY}"
  ${EndIf}

  Delete "$INSTDIR\SpatialScope.exe"
  RMDir /r "$INSTDIR\engine"
  Delete "$INSTDIR\${SMOKE_MARKER}"
  Delete "$INSTDIR\${INSTALL_MARKER}"
  Delete "$INSTDIR\Uninstall SpatialScope.exe"
  RMDir "$INSTDIR"
SectionEnd
