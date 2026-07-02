<#
.SYNOPSIS
Configure the current PowerShell session for UTF-8 repository work.

.DESCRIPTION
Dot-source this script before reading or writing non-ASCII files from Windows
PowerShell. It makes console IO and common child tools prefer UTF-8 without BOM.

Usage:
  . .\ai\pwsh-utf8.ps1

Optional persistent setup:
  . .\ai\pwsh-utf8.ps1 -Persist
#>

[CmdletBinding()]
param(
    [switch]$Persist
)

$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$script:OutputEncoding = $Utf8NoBom
$global:OutputEncoding = $Utf8NoBom

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:LESSCHARSET = 'utf-8'
$env:LC_ALL = 'C.UTF-8'
$env:LANG = 'C.UTF-8'

if (Get-Command chcp.com -ErrorAction SilentlyContinue) {
    chcp.com 65001 > $null
}

if ($Persist) {
    if (-not $PSCommandPath) {
        throw 'Cannot persist UTF-8 setup because PSCommandPath is not available.'
    }

    $profileDir = Split-Path -Parent $PROFILE.CurrentUserCurrentHost
    if ($profileDir) {
        New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
    }

    $scriptPath = $PSCommandPath
    $block = @"
# AI-CODING-WORKFLOW:BEGIN utf8
. '$scriptPath'
# AI-CODING-WORKFLOW:END utf8
"@

    $existing = ''
    if (Test-Path -LiteralPath $PROFILE.CurrentUserCurrentHost) {
        $existing = Get-Content -Raw -Encoding UTF8 -LiteralPath $PROFILE.CurrentUserCurrentHost
    }

    $pattern = '(?s)# AI-CODING-WORKFLOW:BEGIN utf8.*?# AI-CODING-WORKFLOW:END utf8\r?\n?'
    if ($existing -match $pattern) {
        $updated = [regex]::Replace($existing, $pattern, $block)
    } elseif ([string]::IsNullOrWhiteSpace($existing)) {
        $updated = $block
    } else {
        $updated = $existing.TrimEnd() + [Environment]::NewLine + [Environment]::NewLine + $block
    }

    Set-Content -LiteralPath $PROFILE.CurrentUserCurrentHost -Value $updated -Encoding UTF8
    Write-Host "Updated PowerShell profile: $($PROFILE.CurrentUserCurrentHost)"
}

Write-Host 'PowerShell UTF-8 session configured.'
