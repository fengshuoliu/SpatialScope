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
!define INSTANCE_MUTEX "Local\SpatialScope.Windows.Application"
!define QA_INSTANCE_MUTEX_PREFIX "Local\SpatialScope.Windows.Application.QA."
!define QA_INSTANCE_MUTEX_PREFIX_LENGTH 42
!define QA_INSTANCE_MUTEX_LENGTH 74
!define QA_SMOKE_ARGUMENT "--qa-smoke"

Var SmokeMode
Var UpdateMode
Var UpdatePid
Var InstanceMutexHandle
Var InstanceMutexName

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
  StrCpy $UpdateMode "0"
  StrCpy $UpdatePid ""
  StrCpy $InstanceMutexHandle "0"
  StrCpy $InstanceMutexName "${INSTANCE_MUTEX}"
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/SMOKETEST" $R1
  ${IfNot} ${Errors}
    StrCpy $SmokeMode "1"
    ReadEnvStr $R2 "SPATIALSCOPE_QA_INSTANCE_MUTEX"
    ${If} $R2 != ""
      StrLen $R3 $R2
      ${If} $R3 == ${QA_INSTANCE_MUTEX_LENGTH}
        StrCpy $R3 $R2 ${QA_INSTANCE_MUTEX_PREFIX_LENGTH}
        ${If} $R3 == "${QA_INSTANCE_MUTEX_PREFIX}"
          StrCpy $R4 ${QA_INSTANCE_MUTEX_PREFIX_LENGTH}
          Goto qa_mutex_validate_loop
        ${EndIf}
      ${EndIf}
    ${EndIf}
  ${EndIf}
  Goto qa_mutex_validation_done

  qa_mutex_validate_loop:
    IntCmp $R4 ${QA_INSTANCE_MUTEX_LENGTH} qa_mutex_valid qa_mutex_validate_character qa_mutex_invalid

  qa_mutex_validate_character:
    StrCpy $R5 $R2 1 $R4
    StrCmp $R5 "0" qa_mutex_character_valid
    StrCmp $R5 "1" qa_mutex_character_valid
    StrCmp $R5 "2" qa_mutex_character_valid
    StrCmp $R5 "3" qa_mutex_character_valid
    StrCmp $R5 "4" qa_mutex_character_valid
    StrCmp $R5 "5" qa_mutex_character_valid
    StrCmp $R5 "6" qa_mutex_character_valid
    StrCmp $R5 "7" qa_mutex_character_valid
    StrCmp $R5 "8" qa_mutex_character_valid
    StrCmp $R5 "9" qa_mutex_character_valid
    StrCmp $R5 "a" qa_mutex_character_valid
    StrCmp $R5 "b" qa_mutex_character_valid
    StrCmp $R5 "c" qa_mutex_character_valid
    StrCmp $R5 "d" qa_mutex_character_valid
    StrCmp $R5 "e" qa_mutex_character_valid
    StrCmp $R5 "f" qa_mutex_character_valid
    StrCmp $R5 "A" qa_mutex_character_valid
    StrCmp $R5 "B" qa_mutex_character_valid
    StrCmp $R5 "C" qa_mutex_character_valid
    StrCmp $R5 "D" qa_mutex_character_valid
    StrCmp $R5 "E" qa_mutex_character_valid
    StrCmp $R5 "F" qa_mutex_character_valid
    Goto qa_mutex_invalid

  qa_mutex_character_valid:
    IntOp $R4 $R4 + 1
    Goto qa_mutex_validate_loop

  qa_mutex_valid:
    StrCpy $InstanceMutexName $R2
    Goto qa_mutex_validation_done

  qa_mutex_invalid:
    StrCpy $InstanceMutexName "${INSTANCE_MUTEX}"

  qa_mutex_validation_done:

  ; The verified installer is started before SpatialScope closes. Wait for
  ; that exact process to finish so the executable and analysis engine are
  ; never replaced while they are still running.
  ClearErrors
  ${GetOptions} $R0 "/UPDATEPID=" $UpdatePid
  ${IfNot} ${Errors}
    StrCpy $UpdateMode "1"
    ; Atomically open the app's named mutex or create it if the old process
    ; already exited. Keeping this handle alive prevents a new app launch from
    ; entering the installation while files are being replaced.
    System::Call 'kernel32::CreateMutexW(p 0, i 0, w "$InstanceMutexName") p.R4'
    StrCpy $InstanceMutexHandle $R4
    ${If} $InstanceMutexHandle == 0
      SetErrorLevel 6
      Quit
    ${EndIf}
    System::Call 'kernel32::OpenProcess(i 0x00100000, i 0, i $UpdatePid) p.R2'
    ${If} $R2 != 0
      System::Call 'kernel32::WaitForSingleObject(p $R2, i 300000) i.R3'
      System::Call 'kernel32::CloseHandle(p $R2)'
      ${If} $R3 != 0
        SetErrorLevel 4
        Quit
      ${EndIf}
    ${EndIf}
    ${If} $InstanceMutexHandle != 0
      System::Call 'kernel32::WaitForSingleObject(p $InstanceMutexHandle, i 300000) i.R3'
      ${If} $R3 != 0
        ; WAIT_ABANDONED (0x80) still grants safe ownership if the old app
        ; terminated unexpectedly instead of releasing the mutex cleanly.
        ${If} $R3 != 0x80
          System::Call 'kernel32::CloseHandle(p $InstanceMutexHandle)'
          StrCpy $InstanceMutexHandle "0"
          SetErrorLevel 5
          Quit
        ${EndIf}
      ${EndIf}
    ${EndIf}
  ${EndIf}
FunctionEnd

Function un.onInit
  StrCpy $SmokeMode "0"
  IfFileExists "$INSTDIR\${SMOKE_MARKER}" 0 +2
    StrCpy $SmokeMode "1"
FunctionEnd

Section "SpatialScope" SEC_MAIN
  SetShellVarContext current

  ${If} $UpdateMode == "1"
    IfFileExists "$INSTDIR\${INSTALL_MARKER}" updater_owned_install 0
      SetErrorLevel 3
      Abort
    updater_owned_install:
  ${EndIf}

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

  ; Update mode always relaunches, including the isolated SMOKETEST path used
  ; by CI to prove the complete wait-install-reopen handoff.
  ${If} $UpdateMode == "1"
    ${If} $InstanceMutexHandle != 0
      System::Call 'kernel32::ReleaseMutex(p $InstanceMutexHandle)'
      System::Call 'kernel32::CloseHandle(p $InstanceMutexHandle)'
      StrCpy $InstanceMutexHandle "0"
    ${EndIf}
    ${If} $SmokeMode == "1"
      Exec '"$INSTDIR\SpatialScope.exe" ${QA_SMOKE_ARGUMENT}'
    ${Else}
      Exec '"$INSTDIR\SpatialScope.exe"'
    ${EndIf}
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
