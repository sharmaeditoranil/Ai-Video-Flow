!include "MUI2.nsh"

Name "AI Video Flow"
OutFile "../dist/AI-Video-Flow-Windows-Setup.exe"
InstallDir "$LOCALAPPDATA\AI Video Flow"
RequestExecutionLevel user

!define MUI_ABORTWARNING
!define MUI_ICON "../assets/icon.ico"
!define MUI_UNICON "../assets/icon.ico"

; Installer Pages
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

; Uninstaller Pages
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "$INSTDIR"
  
  ; Write uninstaller
  WriteUninstaller "$INSTDIR\uninstall.exe"
  
  ; Copy all application files
  File /r "../win_staging/*.*"
  
  ; Create Desktop Shortcut pointing to launch runner
  CreateShortcut "$DESKTOP\AI Video Flow.lnk" "$INSTDIR\AI-Video-Flow.vbs" "" "$INSTDIR\assets\icon.ico" 0
  
  ; Create Start Menu Shortcuts
  CreateDirectory "$SMPROGRAMS\AI Video Flow"
  CreateShortcut "$SMPROGRAMS\AI Video Flow\AI Video Flow.lnk" "$INSTDIR\AI-Video-Flow.vbs" "" "$INSTDIR\assets\icon.ico" 0
  CreateShortcut "$SMPROGRAMS\AI Video Flow\Uninstall AI Video Flow.lnk" "$INSTDIR\uninstall.exe"
  
  ; Add to Windows Installed Programs (Add/Remove Programs)
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIVideoFlow" "DisplayName" "AI Video Flow"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIVideoFlow" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIVideoFlow" "DisplayIcon" "$INSTDIR\assets\icon.ico"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIVideoFlow" "DisplayVersion" "1.1.21"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIVideoFlow" "Publisher" "AI Video Flow"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\AI Video Flow.lnk"
  Delete "$SMPROGRAMS\AI Video Flow\*.*"
  RMDir "$SMPROGRAMS\AI Video Flow"
  
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AIVideoFlow"
SectionEnd
