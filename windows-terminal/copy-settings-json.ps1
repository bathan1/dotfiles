# Find all Windows Terminal packages (stable + preview)
$pkgDirs = Get-ChildItem "$env:LOCALAPPDATA\Packages" |
  Where-Object Name -like "Microsoft.WindowsTerminal*"

if (-not $pkgDirs) {
  Write-Error "Windows Terminal package not found."
  exit 1
}

# Prefer stable over preview if both exist
$pkgDir = $pkgDirs | Where-Object Name -eq "Microsoft.WindowsTerminal_8wekyb3d8bbwe"
if (-not $pkgDir) {
  $pkgDir = $pkgDirs | Select-Object -First 1
}

# Build source + destination paths
$src = Join-Path $pkgDir.FullName "LocalState\settings.json"
$dest = Join-Path $PSScriptRoot "settings.json"

if (-not (Test-Path $src)) {
  Write-Error "settings.json not found at $src"
  exit 1
}

# Copy file
Copy-Item $src $dest -Force

# Output info
Write-Output "Source (Windows Terminal): $src"
Write-Output "Copied to (repo):          $dest"
