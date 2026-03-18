$Desktop = [Environment]::GetFolderPath('Desktop')
$WshShell = New-Object -comObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$Desktop\Crypthos.lnk")
$Shortcut.TargetPath = "python.exe"
$Shortcut.Arguments = "main.py"
$Shortcut.WorkingDirectory = "C:\Users\emrem\AndroidStudioProjects\Ranking\Crypthos"
$Shortcut.IconLocation = "C:\Users\emrem\AndroidStudioProjects\Ranking\Crypthos\crypthos.ico"
$Shortcut.Save()