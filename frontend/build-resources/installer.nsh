!include LogicLib.nsh
!include WinMessages.nsh

; Raticode as a web browser Windows can offer in Settings > Apps > Default apps.
; This only registers Raticode: its ProgIDs, its capabilities and its entry in
; RegisteredApplications. It never writes a UserChoice key or a .html/.htm,
; http or https default, so Windows keeps the user's browser until the user picks
; Raticode in Settings. Links arrive as `Raticode.exe -- "<link>"`; the `--`
; stops Chromium from reading anything in the link as a command-line switch.
!define RATICODE_BROWSER_KEY "Software\Clients\StartMenuInternet\Raticode"

!macro RaticodeRegisterBrowser
  WriteRegStr HKCU "Software\Classes\RaticodeURL" "" "Raticode web link"
  WriteRegStr HKCU "Software\Classes\RaticodeURL" "URL Protocol" ""
  WriteRegStr HKCU "Software\Classes\RaticodeURL" "FriendlyTypeName" "Raticode web link"
  WriteRegStr HKCU "Software\Classes\RaticodeURL\DefaultIcon" "" "$INSTDIR\${APP_EXECUTABLE_FILENAME},0"
  WriteRegStr HKCU "Software\Classes\RaticodeURL\shell\open\command" "" '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" -- "%1"'
  WriteRegStr HKCU "Software\Classes\RaticodeHTML" "" "Raticode web page"
  WriteRegStr HKCU "Software\Classes\RaticodeHTML" "FriendlyTypeName" "Raticode web page"
  WriteRegStr HKCU "Software\Classes\RaticodeHTML\DefaultIcon" "" "$INSTDIR\${APP_EXECUTABLE_FILENAME},0"
  WriteRegStr HKCU "Software\Classes\RaticodeHTML\shell\open\command" "" '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" -- "%1"'
  WriteRegStr HKCU "${RATICODE_BROWSER_KEY}" "" "Raticode"
  WriteRegStr HKCU "${RATICODE_BROWSER_KEY}\DefaultIcon" "" "$INSTDIR\${APP_EXECUTABLE_FILENAME},0"
  WriteRegStr HKCU "${RATICODE_BROWSER_KEY}\shell\open\command" "" '"$INSTDIR\${APP_EXECUTABLE_FILENAME}"'
  WriteRegStr HKCU "${RATICODE_BROWSER_KEY}\Capabilities" "ApplicationName" "Raticode"
  WriteRegStr HKCU "${RATICODE_BROWSER_KEY}\Capabilities" "ApplicationDescription" "Raticode's own browsers, Raticode (chromium) and Raticode (gecko)."
  WriteRegStr HKCU "${RATICODE_BROWSER_KEY}\Capabilities" "ApplicationIcon" "$INSTDIR\${APP_EXECUTABLE_FILENAME},0"
  WriteRegStr HKCU "${RATICODE_BROWSER_KEY}\Capabilities\StartMenu" "StartMenuInternet" "Raticode"
  WriteRegStr HKCU "${RATICODE_BROWSER_KEY}\Capabilities\URLAssociations" "http" "RaticodeURL"
  WriteRegStr HKCU "${RATICODE_BROWSER_KEY}\Capabilities\URLAssociations" "https" "RaticodeURL"
  WriteRegStr HKCU "${RATICODE_BROWSER_KEY}\Capabilities\FileAssociations" ".htm" "RaticodeHTML"
  WriteRegStr HKCU "${RATICODE_BROWSER_KEY}\Capabilities\FileAssociations" ".html" "RaticodeHTML"
  WriteRegStr HKCU "${RATICODE_BROWSER_KEY}\Capabilities\FileAssociations" ".shtml" "RaticodeHTML"
  WriteRegStr HKCU "${RATICODE_BROWSER_KEY}\Capabilities\FileAssociations" ".xhtml" "RaticodeHTML"
  WriteRegStr HKCU "Software\Classes\.htm\OpenWithProgids" "RaticodeHTML" ""
  WriteRegStr HKCU "Software\Classes\.html\OpenWithProgids" "RaticodeHTML" ""
  WriteRegStr HKCU "Software\Classes\.shtml\OpenWithProgids" "RaticodeHTML" ""
  WriteRegStr HKCU "Software\Classes\.xhtml\OpenWithProgids" "RaticodeHTML" ""
  WriteRegStr HKCU "Software\RegisteredApplications" "Raticode" "${RATICODE_BROWSER_KEY}\Capabilities"
  ; SHCNE_ASSOCCHANGED: Settings and Open with notice the new handler.
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
!macroend

!macro RaticodeUnregisterBrowser
  DeleteRegValue HKCU "Software\RegisteredApplications" "Raticode"
  DeleteRegValue HKCU "Software\Classes\.htm\OpenWithProgids" "RaticodeHTML"
  DeleteRegValue HKCU "Software\Classes\.html\OpenWithProgids" "RaticodeHTML"
  DeleteRegValue HKCU "Software\Classes\.shtml\OpenWithProgids" "RaticodeHTML"
  DeleteRegValue HKCU "Software\Classes\.xhtml\OpenWithProgids" "RaticodeHTML"
  DeleteRegKey HKCU "${RATICODE_BROWSER_KEY}"
  DeleteRegKey HKCU "Software\Classes\RaticodeURL"
  DeleteRegKey HKCU "Software\Classes\RaticodeHTML"
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
!macroend

!ifndef BUILD_UNINSTALLER
!include nsDialogs.nsh

Var GoferCliPathCheckbox
Var GoferCliPathChoice

!macro customInit
  StrCpy $GoferCliPathChoice ${BST_CHECKED}
!macroend

!macro customPageAfterChangeDir
  Page custom GoferCliPathPageCreate GoferCliPathPageLeave
!macroend

!macro customInstall
  ${If} $GoferCliPathChoice == ${BST_CHECKED}
    Call AddGoferCliToUserPath
  ${EndIf}
  !insertmacro RaticodeRegisterBrowser
!macroend

Function GoferCliPathPageCreate
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 12u "Command line"
  Pop $0

  ${NSD_CreateLabel} 0 18u 100% 30u "Raticode includes the Raticode command line tool for running workflows from PowerShell, Command Prompt, scripts, and automation."
  Pop $0

  ${NSD_CreateCheckbox} 0 56u 100% 12u "Add the Raticode CLI to my user PATH"
  Pop $GoferCliPathCheckbox
  ${NSD_Check} $GoferCliPathCheckbox

  nsDialogs::Show
FunctionEnd

Function GoferCliPathPageLeave
  ${NSD_GetState} $GoferCliPathCheckbox $GoferCliPathChoice
FunctionEnd

Function AddGoferCliToUserPath
  nsExec::ExecToLog "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command $\"$$entry = '$INSTDIR\resources\backend'; $$path = [Environment]::GetEnvironmentVariable('Path', 'User'); $$parts = @($$path -split ';' | Where-Object { $$_ }); if ($$parts -notcontains $$entry) { $$parts += $$entry; [Environment]::SetEnvironmentVariable('Path', ($$parts -join ';'), 'User') }$\""
  SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
FunctionEnd
!endif

!ifdef BUILD_UNINSTALLER
!macro customUnInstall
  Call un.RemoveGoferCliFromUserPath
  !insertmacro RaticodeUnregisterBrowser
!macroend

Function un.RemoveGoferCliFromUserPath
  nsExec::ExecToLog "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command $\"$$entry = '$INSTDIR\resources\backend'; $$path = [Environment]::GetEnvironmentVariable('Path', 'User'); $$parts = @($$path -split ';' | Where-Object { $$_ -and $$_ -ne $$entry }); [Environment]::SetEnvironmentVariable('Path', ($$parts -join ';'), 'User')$\""
  SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
FunctionEnd
!endif
