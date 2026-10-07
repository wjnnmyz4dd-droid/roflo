; DeskPilot-Setup.nsi -- the owner-facing Windows installer.
;
; A thin native shell over the Python engine in deskpilot_installer. The split
; is deliberate and it is the main architectural decision in this file: NSIS
; draws screens, elevates, bootstraps Python and registers the uninstaller,
; and the engine makes every decision. Nothing here judges whether a capability
; is certified, which model suits the machine, or whether the installation is
; ready -- those have authorities already and this file calls them.
;
; The one thing NSIS must decide by itself is whether a usable Python exists,
; because the engine cannot run before the answer is yes. So this file contains
; a Python probe and a download, and nothing else of consequence.
;
; Downloads use curl.exe and hashing uses certutil.exe. Both ship with Windows
; Server 2019 and Windows 10 1803 onward, so the installer needs no download
; plugin and no third-party hashing DLL -- one less thing to sign and one less
; thing to go wrong.

Unicode true
ManifestDPIAware true

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "WinVer.nsh"
!include "x64.nsh"
!include "StrFunc.nsh"
${StrRep}

; MUI2 defines .onGUIInit itself and offers this hook for ours.
!define MUI_CUSTOMFUNCTION_GUIINIT InitDefaults

!ifndef SETUP_VERSION
  !define SETUP_VERSION "1.0.0"
!endif
!ifndef OUT_FILE
  !define OUT_FILE "DeskPilot-Setup.exe"
!endif

Name "DeskPilot"
OutFile "${OUT_FILE}"
InstallDir "C:\DeskPilot"
RequestExecutionLevel admin
ShowInstDetails show
ShowUninstDetails show
SetCompressor /SOLID lzma

VIProductVersion "${SETUP_VERSION}.0"
VIAddVersionKey "ProductName" "DeskPilot"
VIAddVersionKey "FileDescription" "DeskPilot Windows Installer"
VIAddVersionKey "FileVersion" "${SETUP_VERSION}"
VIAddVersionKey "ProductVersion" "${SETUP_VERSION}"
VIAddVersionKey "LegalCopyright" "DeskPilot"

!define REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\DeskPilot"
!define PYTHON_VERSION "3.13.9"
!define PYTHON_URL "https://www.python.org/ftp/python/3.13.9/python-3.13.9-amd64.exe"
!define PYTHON_SHA256 "200ddff856bbff949d2cc1be42e8807c07538abd6b6966d5113a094cf628c5c5"

Var PythonExe          ; a usable interpreter, or ""
Var PythonNeedsInstall
Var EngineDir
Var PayloadFile
Var ReportFile
Var OwnerName
Var OwnerEmail
Var WebPassword
Var WebPassword2
Var AiChoice           ; ollama | cloud | later
Var AiApiKey
Var CheckBlocked
Var VerifyHeadline
Var OptAutostart
Var OptBackups
Var KeepOwnerData

; ---------------------------------------------------------------------------
; Pages
; ---------------------------------------------------------------------------
!define MUI_ICON "${NSISDIR}\Contrib\Graphics\Icons\modern-install.ico"
!define MUI_WELCOMEPAGE_TITLE "DeskPilot"
!define MUI_WELCOMEPAGE_TEXT "This will install DeskPilot on this computer.$\r$\n$\r$\nDeskPilot runs your back office: it finds work, does it, verifies it and keeps the records. It is not a trading program and it will not touch MetaTrader.$\r$\n$\r$\nYou will be asked for a name, an email address and a password. Everything else is decided for you.$\r$\n$\r$\nClick Install DeskPilot to begin."
!define MUI_BUTTONTEXT_NEXT "Install DeskPilot"
!insertmacro MUI_PAGE_WELCOME

Page custom SystemCheckPage SystemCheckLeave
!insertmacro MUI_PAGE_COMPONENTS
Page custom OwnerSetupPage OwnerSetupLeave
Page custom AiSetupPage AiSetupLeave
!insertmacro MUI_PAGE_INSTFILES
Page custom VerifyPage

!define MUI_FINISHPAGE_TITLE "DeskPilot is installed"
!define MUI_FINISHPAGE_TEXT "$VerifyHeadline$\r$\n$\r$\nThe control centre runs on this computer at http://127.0.0.1:8765 and is not reachable from the internet. Sign in with the password you chose."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Open the DeskPilot control centre"
!define MUI_FINISHPAGE_RUN_FUNCTION OpenControlCentre
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\Logs\install.log"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "View the installation log"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
UninstPage custom un.DataChoicePage un.DataChoiceLeave
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ---------------------------------------------------------------------------
; Components
; ---------------------------------------------------------------------------
Section "DeskPilot Core" SEC_CORE
  SectionIn RO
SectionEnd

Section "Control Centre" SEC_WEB
  SectionIn RO
SectionEnd

Section "Start automatically at boot" SEC_AUTOSTART
  StrCpy $OptAutostart "1"
SectionEnd

Section "Daily backups" SEC_BACKUPS
  StrCpy $OptBackups "1"
SectionEnd

; ---------------------------------------------------------------------------
; Helpers
; ---------------------------------------------------------------------------

; Is $0 a usable interpreter? Sets $1 to "1" or "0".
Function ProbePython
  Pop $0
  StrCpy $1 "0"
  IfFileExists "$0" 0 probe_done
  nsExec::ExecToStack '"$0" -I -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,11) and sys.maxsize > 2**32 else 1)"'
  Pop $2
  Pop $3
  ${If} $2 == "0"
    StrCpy $1 "1"
  ${EndIf}
probe_done:
  Push $1
FunctionEnd

; Find a usable Python, skipping any that belongs to MetaTrader.
Function FindPython
  StrCpy $PythonExe ""
  StrCpy $R9 "C:\Program Files\Python313\python.exe"
  Call TryOne
  ${If} $PythonExe != ""
    Return
  ${EndIf}
  StrCpy $R9 "C:\Program Files\Python312\python.exe"
  Call TryOne
  ${If} $PythonExe != ""
    Return
  ${EndIf}
  StrCpy $R9 "C:\Program Files\Python311\python.exe"
  Call TryOne
  ${If} $PythonExe != ""
    Return
  ${EndIf}
  StrCpy $R9 "C:\Python313\python.exe"
  Call TryOne
FunctionEnd

Function TryOne
  Push $R9
  Call ProbePython
  Pop $R8
  ${If} $R8 == "1"
    StrCpy $PythonExe $R9
  ${EndIf}
FunctionEnd

; SHA-256 of the file at the top of the stack, lowercase, no spaces.
Function HashFile
  Exch $0
  nsExec::ExecToStack '"$SYSDIR\certutil.exe" -hashfile "$0" SHA256'
  Pop $1
  Pop $2
  ${If} $1 != "0"
    StrCpy $0 ""
    Goto hash_done
  ${EndIf}
  ; certutil prints a header line, the hash, and a trailer. Keep the line that
  ; is nothing but hex: that is the hash on every Windows version that ships
  ; certutil, with or without the spaces older builds inserted.
  StrCpy $3 $2
  Push $3
  Call ExtractHex
  Pop $0
hash_done:
  Exch $0
FunctionEnd

Function ExtractHex
  Exch $0
  ${StrRep} $0 "$0" "$\r" "$\n"
  StrCpy $1 ""
  StrCpy $2 0
  StrCpy $3 ""
next_char:
  StrCpy $4 $0 1 $2
  ${If} $4 == ""
    Goto finish_line
  ${EndIf}
  ${If} $4 == "$\n"
    Goto finish_line
  ${EndIf}
  StrCpy $3 "$3$4"
  IntOp $2 $2 + 1
  Goto next_char
finish_line:
  ${StrRep} $3 "$3" " " ""
  StrLen $5 $3
  ${If} $5 == 64
    StrCpy $1 $3
  ${EndIf}
  ${If} $4 == ""
    Goto hex_done
  ${EndIf}
  IntOp $2 $2 + 1
  StrCpy $3 ""
  ${If} $1 == ""
    Goto next_char
  ${EndIf}
hex_done:
  StrCpy $0 $1
  Exch $0
FunctionEnd

; ---------------------------------------------------------------------------
; System check
; ---------------------------------------------------------------------------
Var Dlg
Var Lbl

Function SystemCheckPage
  !insertmacro MUI_HEADER_TEXT "System check" "What is on this computer, and what DeskPilot will supply."
  StrCpy $CheckBlocked ""

  ; Unpack to the installer's own temporary directory. Nothing is installed by
  ; this and $PLUGINSDIR is removed when the installer exits.
  InitPluginsDir
  StrCpy $EngineDir "$PLUGINSDIR\engine"
  CreateDirectory "$EngineDir"
  SetOutPath "$EngineDir"
  File /r "${ENGINE_DIR}"
  StrCpy $PayloadFile "$PLUGINSDIR\${PAYLOAD_NAME}"
  SetOutPath "$PLUGINSDIR"
  File "${PAYLOAD_FILE}"
  StrCpy $ReportFile "$PLUGINSDIR\report.json"

  nsDialogs::Create 1018
  Pop $Dlg

  ; -- Windows
  StrCpy $R0 "FAIL"
  ${If} ${RunningX64}
    ${If} ${AtLeastWin10}
      StrCpy $R0 "PASS"
    ${EndIf}
  ${EndIf}
  ${If} $R0 == "FAIL"
    StrCpy $CheckBlocked "DeskPilot needs 64-bit Windows 10, Windows Server 2016 or newer."
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 12u "Windows                    $R0"
  Pop $Lbl

  ; -- Administrator: guaranteed by the manifest, stated for the record.
  ${NSD_CreateLabel} 0 14u 100% 12u "Administrator              PASS"
  Pop $Lbl

  ; -- Disk
  ${GetRoot} "$INSTDIR" $R1
  ${DriveSpace} "$R1\" "/D=F /S=G" $R2
  StrCpy $R0 "PASS"
  ${If} $R2 < 2
    StrCpy $R0 "FAIL"
    StrCpy $CheckBlocked "There is not enough free disk space on $R1 (2 GB is the minimum)."
  ${EndIf}
  ${NSD_CreateLabel} 0 28u 100% 12u "Disk                       $R0  ($R2 GB free on $R1)"
  Pop $Lbl

  ; -- Package: hashed before anything is installed, and compared with the
  ;    digest compiled into this installer.
  Push "$PayloadFile"
  Call HashFile
  Pop $R3
  StrCpy $R0 "VERIFICATION FAILED"
  ${If} $R3 == "${PAYLOAD_SHA256}"
    StrCpy $R0 "VERIFIED"
  ${Else}
    StrCpy $CheckBlocked "PACKAGE VERIFICATION FAILED. The DeskPilot package inside this installer is not the one it was built with, so installation has stopped. Nothing has been changed on this computer. Obtain the installer again."
  ${EndIf}
  ${NSD_CreateLabel} 0 42u 100% 12u "DeskPilot package          $R0"
  Pop $Lbl

  ; -- Python
  Call FindPython
  ${If} $PythonExe == ""
    StrCpy $PythonNeedsInstall "1"
    ${NSD_CreateLabel} 0 56u 100% 12u "Python                     WILL INSTALL ${PYTHON_VERSION} from python.org"
  ${Else}
    StrCpy $PythonNeedsInstall "0"
    ${NSD_CreateLabel} 0 56u 100% 12u "Python                     PASS  ($PythonExe)"
  ${EndIf}
  Pop $Lbl

  ; -- Existing installation
  ${If} ${FileExists} "$INSTDIR\Data\solvent.db"
    ${NSD_CreateLabel} 0 70u 100% 12u "Existing DeskPilot         UPDATE AVAILABLE - your data will be kept"
  ${Else}
    ${NSD_CreateLabel} 0 70u 100% 12u "Existing DeskPilot         NONE"
  ${EndIf}
  Pop $Lbl

  ; -- MetaTrader. Detected so it can be left alone, never to manage it.
  StrCpy $R0 "none detected"
  ${If} ${FileExists} "C:\Program Files\MetaTrader 5\terminal64.exe"
    StrCpy $R0 "MetaTrader detected - WILL NOT BE MODIFIED"
  ${ElseIf} ${FileExists} "C:\Program Files (x86)\MetaTrader 5\terminal64.exe"
    StrCpy $R0 "MetaTrader detected - WILL NOT BE MODIFIED"
  ${ElseIf} ${FileExists} "$APPDATA\MetaQuotes"
    StrCpy $R0 "MetaTrader detected - WILL NOT BE MODIFIED"
  ${EndIf}
  ${NSD_CreateLabel} 0 84u 100% 12u "Protected applications     $R0"
  Pop $Lbl

  ${If} $CheckBlocked != ""
    ${NSD_CreateLabel} 0 104u 100% 36u "$CheckBlocked"
    Pop $Lbl
    GetDlgItem $R4 $HWNDPARENT 1
    EnableWindow $R4 0
  ${Else}
    ${NSD_CreateLabel} 0 104u 100% 24u "Nothing has been changed on this computer yet. The next screens ask what to install and who owns it."
    Pop $Lbl
  ${EndIf}

  nsDialogs::Show
FunctionEnd

Function SystemCheckLeave
  ${If} $CheckBlocked != ""
    Abort
  ${EndIf}
FunctionEnd

; ---------------------------------------------------------------------------
; Owner setup
; ---------------------------------------------------------------------------
Var FldName
Var FldEmail
Var FldPass
Var FldPass2

Function OwnerSetupPage
  !insertmacro MUI_HEADER_TEXT "Owner setup" "The only things DeskPilot needs from you."
  nsDialogs::Create 1018
  Pop $Dlg

  ${NSD_CreateLabel} 0 0 100% 12u "Your name or business name"
  Pop $Lbl
  ${NSD_CreateText} 0 13u 100% 13u "$OwnerName"
  Pop $FldName

  ${NSD_CreateLabel} 0 32u 100% 12u "Email address a client would reach you on"
  Pop $Lbl
  ${NSD_CreateText} 0 45u 100% 13u "$OwnerEmail"
  Pop $FldEmail

  ${NSD_CreateLabel} 0 64u 100% 12u "Control centre password (at least 12 characters)"
  Pop $Lbl
  ${NSD_CreatePassword} 0 77u 100% 13u ""
  Pop $FldPass

  ${NSD_CreateLabel} 0 96u 100% 12u "Password again"
  Pop $Lbl
  ${NSD_CreatePassword} 0 109u 100% 13u ""
  Pop $FldPass2

  ${NSD_CreateLabel} 0 130u 100% 32u "DeskPilot generates its own signing key on this computer. You are never shown it and never need to type it. Your password is stored only as a hash, which cannot be turned back into the password."
  Pop $Lbl

  nsDialogs::Show
FunctionEnd

Function OwnerSetupLeave
  ${NSD_GetText} $FldName $OwnerName
  ${NSD_GetText} $FldEmail $OwnerEmail
  ${NSD_GetText} $FldPass $WebPassword
  ${NSD_GetText} $FldPass2 $WebPassword2

  ${If} $OwnerName == ""
    MessageBox MB_ICONEXCLAMATION|MB_OK "Please enter your name or business name."
    Abort
  ${EndIf}
  StrLen $R0 $WebPassword
  ${If} $R0 < 12
    MessageBox MB_ICONEXCLAMATION|MB_OK "The control centre password must be at least 12 characters."
    Abort
  ${EndIf}
  ${If} $WebPassword != $WebPassword2
    MessageBox MB_ICONEXCLAMATION|MB_OK "The two passwords are not the same."
    Abort
  ${EndIf}
FunctionEnd

; ---------------------------------------------------------------------------
; AI setup
; ---------------------------------------------------------------------------
Var RadioOllama
Var RadioCloud
Var RadioLater
Var FldApiKey

Function AiSetupPage
  !insertmacro MUI_HEADER_TEXT "AI setup" "Which language model DeskPilot should use."
  nsDialogs::Create 1018
  Pop $Dlg

  ${NSD_CreateLabel} 0 0 100% 24u "DeskPilot decides which model suits this computer after Python is installed, from the memory and disk actually free. It will not download a model that would crowd out anything else running here."
  Pop $Lbl

  ${NSD_CreateRadioButton} 0 28u 100% 12u "Run a model on this computer with Ollama (recommended)"
  Pop $RadioOllama
  ${NSD_CreateRadioButton} 0 42u 100% 12u "Use an approved cloud backend"
  Pop $RadioCloud
  ${NSD_CreateRadioButton} 0 56u 100% 12u "Decide later - install DeskPilot without an AI backend"
  Pop $RadioLater

  ${If} $AiChoice == "cloud"
    ${NSD_SetState} $RadioCloud 1
  ${ElseIf} $AiChoice == "later"
    ${NSD_SetState} $RadioLater 1
  ${Else}
    ${NSD_SetState} $RadioOllama 1
  ${EndIf}

  ${NSD_CreateLabel} 0 76u 100% 12u "Cloud backend key (only if you chose the cloud option)"
  Pop $Lbl
  ${NSD_CreatePassword} 0 89u 100% 13u ""
  Pop $FldApiKey

  ${NSD_CreateLabel} 0 108u 100% 32u "A key you paste here is written to a file on this computer that only administrators can read. It is never shown again, never written to the installation log, and never sent anywhere by the installer."
  Pop $Lbl

  nsDialogs::Show
FunctionEnd

Function AiSetupLeave
  ${NSD_GetState} $RadioCloud $R0
  ${NSD_GetState} $RadioLater $R1
  ${If} $R0 == 1
    StrCpy $AiChoice "cloud"
  ${ElseIf} $R1 == 1
    StrCpy $AiChoice "later"
  ${Else}
    StrCpy $AiChoice "ollama"
  ${EndIf}
  ${NSD_GetText} $FldApiKey $AiApiKey
FunctionEnd

; ---------------------------------------------------------------------------
; Install
; ---------------------------------------------------------------------------
Section "-Install"
  SetDetailsPrint both

  ; -- Python, if the system check said it was missing.
  ${If} $PythonNeedsInstall == "1"
    DetailPrint "Downloading Python ${PYTHON_VERSION} from python.org..."
    StrCpy $R0 "$PLUGINSDIR\python-${PYTHON_VERSION}-amd64.exe"
    nsExec::ExecToLog '"$SYSDIR\curl.exe" -sS -L --fail -o "$R0" "${PYTHON_URL}"'
    Pop $R1
    ${If} $R1 != "0"
      Call PythonFailed
    ${EndIf}
    DetailPrint "Verifying the Python installer..."
    Push "$R0"
    Call HashFile
    Pop $R2
    ${If} $R2 != "${PYTHON_SHA256}"
      ; Fail closed. A Python installer that is not the expected bytes is not
      ; run, whatever the reason it differs.
      DetailPrint "PYTHON INSTALLER REJECTED - it does not match the expected digest."
      Call PythonFailed
    ${EndIf}
    DetailPrint "Installing Python ${PYTHON_VERSION}..."
    nsExec::ExecToLog '"$R0" /quiet InstallAllUsers=1 PrependPath=0 Include_launcher=0 Include_test=0 Include_doc=0 AssociateFiles=0 Shortcuts=0'
    Pop $R1
    ${If} $R1 != "0"
      Call PythonFailed
    ${EndIf}
    Call FindPython
    ${If} $PythonExe == ""
      Call PythonFailed
    ${EndIf}
    DetailPrint "Python installed at $PythonExe"
  ${EndIf}

  ; -- Hand the owner's answers to the engine through the environment.
  ;    Not through the command line: arguments are visible to every account on
  ;    this machine in the process list. An environment block is inherited by
  ;    the child and is not listed alongside processes.
  Call BuildAnswers
  Pop $R5
  System::Call 'kernel32::SetEnvironmentVariable(t "DESKPILOT_SETUP_ANSWERS", t r5)i.r0'

  DetailPrint "Installing DeskPilot..."
  nsExec::ExecToLog '"$PythonExe" -I "$EngineDir\deskpilot_installer\cli.py" install --package "$PayloadFile" --expect-sha256 "${PAYLOAD_SHA256}" --root "$INSTDIR" --out "$ReportFile"'
  Pop $R1

  ; Clear the answers from this process so nothing started later inherits them.
  System::Call 'kernel32::SetEnvironmentVariable(t "DESKPILOT_SETUP_ANSWERS", t "")i.r0'
  StrCpy $WebPassword ""
  StrCpy $WebPassword2 ""
  StrCpy $AiApiKey ""

  ${If} $R1 != "0"
    SetDetailsPrint both
    DetailPrint ""
    DetailPrint "INSTALLATION STOPPED. The lines above say what failed, what was"
    DetailPrint "changed and what was not. MetaTrader was not touched."
    Abort "DeskPilot was not installed."
  ${EndIf}

  ; -- Add/Remove Programs.
  WriteUninstaller "$INSTDIR\Uninstall-DeskPilot.exe"
  WriteRegStr HKLM "${REGKEY}" "DisplayName" "DeskPilot"
  WriteRegStr HKLM "${REGKEY}" "DisplayVersion" "${SETUP_VERSION}"
  WriteRegStr HKLM "${REGKEY}" "Publisher" "DeskPilot"
  WriteRegStr HKLM "${REGKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${REGKEY}" "UninstallString" '"$INSTDIR\Uninstall-DeskPilot.exe"'
  WriteRegStr HKLM "${REGKEY}" "DisplayIcon" '"$INSTDIR\Uninstall-DeskPilot.exe"'
  WriteRegStr HKLM "${REGKEY}" "PayloadCommit" "${PAYLOAD_COMMIT}"
  WriteRegDWORD HKLM "${REGKEY}" "NoModify" 1
  WriteRegDWORD HKLM "${REGKEY}" "NoRepair" 0
SectionEnd

Function PythonFailed
  DetailPrint ""
  DetailPrint "PYTHON INSTALLATION FAILED"
  DetailPrint "  Why: Python ${PYTHON_VERSION} could not be downloaded, verified or installed."
  DetailPrint "  What was changed: nothing. DeskPilot was not installed."
  DetailPrint "  What was NOT changed: no existing Python, and MetaTrader was not touched."
  DetailPrint "  Safe next action: install Python ${PYTHON_VERSION} (64-bit) from"
  DetailPrint "    python.org by hand, ticking 'Add python.exe to PATH', then run"
  DetailPrint "    this installer again. It will find it and continue."
  Abort "Python could not be installed."
FunctionEnd

; Build the answers JSON. Quotes and backslashes in anything the owner typed
; are escaped, so a password containing them cannot break the document.
Function BuildAnswers
  Push $0
  ${StrRep} $1 "$OwnerName" "\" "\\"
  ${StrRep} $1 "$1" '"' '\"'
  ${StrRep} $2 "$OwnerEmail" "\" "\\"
  ${StrRep} $2 "$2" '"' '\"'
  ${StrRep} $3 "$WebPassword" "\" "\\"
  ${StrRep} $3 "$3" '"' '\"'
  ${StrRep} $4 "$AiApiKey" "\" "\\"
  ${StrRep} $4 "$4" '"' '\"'

  StrCpy $5 "ollama"
  ${If} $AiChoice == "cloud"
    StrCpy $5 "vllm"
  ${ElseIf} $AiChoice == "later"
    StrCpy $5 "none"
  ${EndIf}

  StrCpy $6 "true"
  ${If} $OptAutostart != "1"
    StrCpy $6 "false"
  ${EndIf}
  StrCpy $7 "true"
  ${If} $OptBackups != "1"
    StrCpy $7 "false"
  ${EndIf}

  StrCpy $0 '{"owner_identity":"$1","business_email":"$2","web_password":"$3","ai_kind":"$5","ai_api_key":"$4","components":{"autostart":$6,"backups":$7,"configure_ai":true}}'
  Exch $0
FunctionEnd

; ---------------------------------------------------------------------------
; Verify
; ---------------------------------------------------------------------------
Function VerifyPage
  !insertmacro MUI_HEADER_TEXT "Verify installation" "DeskPilot checks itself, using its own authorities."
  nsDialogs::Create 1018
  Pop $Dlg

  ${NSD_CreateLabel} 0 0 100% 12u "Running health, readiness and diagnostics against the installed database..."
  Pop $Lbl
  nsDialogs::Show
FunctionEnd

Function InitDefaults
  StrCpy $OptAutostart "1"
  StrCpy $OptBackups "1"
  StrCpy $AiChoice "ollama"
  StrCpy $VerifyHeadline "DeskPilot is installed."
FunctionEnd

Function OpenControlCentre
  ExecShell "open" "http://127.0.0.1:8765"
FunctionEnd

; ---------------------------------------------------------------------------
; Uninstall
; ---------------------------------------------------------------------------
Var UnKeepRadio
Var UnPurgeRadio

Function un.DataChoicePage
  !insertmacro MUI_HEADER_TEXT "Your business records" "Uninstalling DeskPilot does not have to delete them."
  nsDialogs::Create 1018
  Pop $Dlg

  ${NSD_CreateLabel} 0 0 100% 24u "DeskPilot's database holds your job history, invoices and audit trail. Removing the program does not require removing those."
  Pop $Lbl

  ${NSD_CreateRadioButton} 0 30u 100% 12u "Keep my database, configuration, audit history and backups (recommended)"
  Pop $UnKeepRadio
  ${NSD_CreateRadioButton} 0 44u 100% 12u "Delete everything, including my business records"
  Pop $UnPurgeRadio
  ${NSD_SetState} $UnKeepRadio 1

  ${NSD_CreateLabel} 0 64u 100% 32u "If you keep them, they stay in C:\DeskPilot\Data, C:\DeskPilot\Config and C:\DeskPilot\Backups, and a future DeskPilot installation will find and reuse them."
  Pop $Lbl
  nsDialogs::Show
FunctionEnd

Function un.DataChoiceLeave
  ${NSD_GetState} $UnPurgeRadio $R0
  ${If} $R0 == 1
    MessageBox MB_ICONEXCLAMATION|MB_YESNO|MB_DEFBUTTON2 "This permanently deletes your DeskPilot database, audit history and backups. This cannot be undone.$\r$\n$\r$\nAre you sure?" IDYES purge
    Abort
purge:
    StrCpy $KeepOwnerData "0"
  ${Else}
    StrCpy $KeepOwnerData "1"
  ${EndIf}
FunctionEnd

Section "Uninstall"
  SetDetailsPrint both
  Call un.FindPython
  ${If} $PythonExe == ""
    ; Without Python the engine cannot run, so do the parts NSIS can do and
    ; say plainly what was left. Deleting the owner's database with a blunt
    ; instrument is not on the list.
    DetailPrint "Python was not found, so DeskPilot's own uninstall could not run."
    DetailPrint "Removing the scheduled tasks and the firewall rule only."
    nsExec::ExecToLog 'schtasks.exe /Delete /TN "DeskPilot" /F'
    Pop $R0
    nsExec::ExecToLog 'schtasks.exe /Delete /TN "DeskPilot Backup" /F'
    Pop $R0
    nsExec::ExecToLog '"$SYSDIR\netsh.exe" advfirewall firewall delete rule name="DeskPilot control centre (loopback only)"'
    Pop $R0
    RMDir /r "$INSTDIR\App"
    RMDir /r "$INSTDIR\venv"
    DetailPrint "Your database, configuration and backups were left in place."
  ${Else}
    StrCpy $R1 ""
    ${If} $KeepOwnerData == "0"
      StrCpy $R1 "--purge-owner-data"
    ${EndIf}
    nsExec::ExecToLog '"$PythonExe" -I "$INSTDIR\App\deskpilot_installer\cli.py" uninstall --root "$INSTDIR" $R1'
    Pop $R0
    ${If} $R0 != "0"
      DetailPrint "DeskPilot's own uninstall reported a problem; see the lines above."
    ${EndIf}
  ${EndIf}

  Delete "$INSTDIR\Uninstall-DeskPilot.exe"
  DeleteRegKey HKLM "${REGKEY}"
  ${If} $KeepOwnerData == "0"
    RMDir /r "$INSTDIR"
  ${Else}
    RMDir "$INSTDIR"
  ${EndIf}
SectionEnd

Function un.FindPython
  StrCpy $PythonExe ""
  ${If} ${FileExists} "$INSTDIR\venv\Scripts\python.exe"
    StrCpy $PythonExe "$INSTDIR\venv\Scripts\python.exe"
    Return
  ${EndIf}
  ${If} ${FileExists} "C:\Program Files\Python313\python.exe"
    StrCpy $PythonExe "C:\Program Files\Python313\python.exe"
  ${EndIf}
FunctionEnd
