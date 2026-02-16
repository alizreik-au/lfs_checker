#Requires -Version 5.1
<#
.SYNOPSIS
    Finds which file(s) in a Git repo correspond to a given Git LFS object ID (OID).
    Shows a Windows Forms GUI with folder picker and result display.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string] $RepoPath = '',

    [Parameter(Position = 1)]
    [string] $OID = ''
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function Get-LfsFilesByOid {
    param(
        [string] $LiteralRepoPath,
        [string] $OidValue
    )
    $repoPath = $LiteralRepoPath.Trim().Trim('"')
    if ([string]::IsNullOrWhiteSpace($repoPath)) { return @{ Success = $false; Message = 'No folder selected.'; Files = @() } }
    $repoPath = [System.IO.Path]::GetFullPath($repoPath)
    if (-not [System.IO.Directory]::Exists($repoPath)) {
        return @{ Success = $false; Message = "Folder does not exist: $repoPath"; Files = @() }
    }
    $gitDir = [System.IO.Path]::Combine($repoPath, '.git')
    if (-not [System.IO.Directory]::Exists($gitDir)) {
        return @{ Success = $false; Message = "Not a Git repository (no .git folder)."; Files = @() }
    }
    $oidNorm = ($OidValue -replace '\s', '').Trim().ToLowerInvariant()
    if ($oidNorm.Length -ne 64 -or $oidNorm -notmatch '^[a-f0-9]{64}$') {
        return @{ Success = $false; Message = 'Invalid LFS OID: must be exactly 64 hexadecimal characters.'; Files = @() }
    }

    $prevDir = Get-Location
    $env:GIT_DIR = $gitDir
    $env:GIT_WORK_TREE = $repoPath
    try {
        Set-Location -LiteralPath $repoPath
        $lsOutput = & git lfs ls-files --all --long 2>&1
        if ($LASTEXITCODE -ne 0) {
            return @{ Success = $false; Message = "git lfs ls-files failed: $lsOutput"; Files = @() }
        }
    } finally {
        Set-Location -LiteralPath $prevDir
        Remove-Item env:GIT_DIR -ErrorAction SilentlyContinue
        Remove-Item env:GIT_WORK_TREE -ErrorAction SilentlyContinue
    }

    $found = [System.Collections.Generic.List[string]]::new()
    foreach ($line in $lsOutput) {
        $line = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line -match '^([a-fA-F0-9]{64})\s+.\s+(.+)$') {
            if ($matches[1].ToLowerInvariant() -eq $oidNorm) {
                $found.Add($matches[2].Trim())
            }
        }
    }
    if ($found.Count -eq 0) {
        return @{ Success = $true; Message = "No file found for this LFS object ID. It may be from another branch or commit."; Files = @() }
    }
    return @{ Success = $true; Message = ''; Files = $found }
}

# Resolve initial repo path for display (handle spaces and quoting)
$initialRepo = ''
if (-not [string]::IsNullOrWhiteSpace($RepoPath)) {
    $t = $RepoPath.Trim().Trim('"')
    if ($t.Length -gt 0) {
        try {
            $initialRepo = [System.IO.Path]::GetFullPath($t)
            if (-not [System.IO.Directory]::Exists($initialRepo)) { $initialRepo = '' }
        } catch { $initialRepo = $t }
    }
}

$fontUi = New-Object System.Drawing.Font('Segoe UI', 9)
$fontMono = New-Object System.Drawing.Font('Consolas', 9)

$form = New-Object System.Windows.Forms.Form
$form.Text = 'LFS Object Lookup – Find file by LFS object ID'
$form.Size = New-Object System.Drawing.Size(620, 420)
$form.StartPosition = 'CenterScreen'
$form.Font = $fontUi
$form.MinimumSize = New-Object System.Drawing.Size(520, 380)

$lblFolder = New-Object System.Windows.Forms.Label
$lblFolder.Text = 'Git repository folder:'
$lblFolder.Location = New-Object System.Drawing.Point(12, 14)
$lblFolder.AutoSize = $true

$txtFolder = New-Object System.Windows.Forms.TextBox
$txtFolder.Location = New-Object System.Drawing.Point(12, 34)
$txtFolder.Size = New-Object System.Drawing.Size(450, 22)
$txtFolder.ReadOnly = $true
$txtFolder.Text = $initialRepo
$txtFolder.Font = $fontMono
$txtFolder.Anchor = 'Top,Left,Right'

$btnBrowse = New-Object System.Windows.Forms.Button
$btnBrowse.Text = 'Browse...'
$btnBrowse.Location = New-Object System.Drawing.Point(468, 32)
$btnBrowse.Size = New-Object System.Drawing.Size(120, 26)
$btnBrowse.Anchor = 'Top,Right'
$btnBrowse.Add_Click({
    $browse = New-Object System.Windows.Forms.FolderBrowserDialog
    $browse.Description = 'Select the Git repository folder (containing .git)'
    $browse.UseDescriptionForTitle = $true
    $cur = $txtFolder.Text.Trim()
    if ($cur.Length -gt 0 -and [System.IO.Directory]::Exists($cur)) { $browse.SelectedPath = $cur }
    if ($browse.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        $p = $browse.SelectedPath
        $gitPath = [System.IO.Path]::Combine($p, '.git')
        if (-not [System.IO.Directory]::Exists($gitPath)) {
            [System.Windows.Forms.MessageBox]::Show("This folder does not contain a .git directory. Choose the repository root.", 'Not a Git repo', 'OK', 'Warning')
        }
        $txtFolder.Text = $p
    }
})

$lblOid = New-Object System.Windows.Forms.Label
$lblOid.Text = 'LFS object ID (64 hex characters):'
$lblOid.Location = New-Object System.Drawing.Point(12, 68)
$lblOid.AutoSize = $true

$txtOid = New-Object System.Windows.Forms.TextBox
$txtOid.Location = New-Object System.Drawing.Point(12, 88)
$txtOid.Size = New-Object System.Drawing.Size(450, 22)
$txtOid.Font = $fontMono
$txtOid.Anchor = 'Top,Left,Right'
if (-not [string]::IsNullOrWhiteSpace($OID)) { $txtOid.Text = ($OID -replace '\s', '').Trim() }

$btnPaste = New-Object System.Windows.Forms.Button
$btnPaste.Text = 'Paste from clipboard'
$btnPaste.Location = New-Object System.Drawing.Point(468, 86)
$btnPaste.Size = New-Object System.Drawing.Size(120, 26)
$btnPaste.Anchor = 'Top,Right'
$btnPaste.Add_Click({
    try {
        $clip = [System.Windows.Forms.Clipboard]::GetText()
        $trimmed = ($clip -replace '\s', '').Trim()
        if ($trimmed -match '^[a-fA-F0-9]{64}$') {
            $txtOid.Text = $trimmed.ToLowerInvariant()
        } else {
            [System.Windows.Forms.MessageBox]::Show('Clipboard does not contain a valid 64-character hex LFS OID.', 'Paste', 'OK', 'Information')
        }
    } catch {
        [System.Windows.Forms.MessageBox]::Show('Could not read clipboard.', 'Paste', 'OK', 'Warning')
    }
})

$btnFind = New-Object System.Windows.Forms.Button
$btnFind.Text = 'Find file(s)'
$btnFind.Location = New-Object System.Drawing.Point(12, 122)
$btnFind.Size = New-Object System.Drawing.Size(120, 28)
$btnFind.Add_Click({
    $repo = $txtFolder.Text.Trim().Trim('"')
    $oid = $txtOid.Text.Trim()
    $result = Get-LfsFilesByOid -LiteralRepoPath $repo -OidValue $oid
    if (-not $result.Success) {
        $txtResult.Text = $result.Message
        $txtResult.ForeColor = [System.Drawing.Color]::DarkRed
        return
    }
    if ($result.Files.Count -eq 0) {
        $txtResult.Text = $result.Message
        $txtResult.ForeColor = [System.Drawing.Color]::DarkOrange
        return
    }
    $txtResult.ForeColor = [System.Drawing.Color]::DarkGreen
    $txtResult.Text = "LFS object ID matches the following file(s):`r`n`r`n" + ($result.Files -join "`r`n")
})

$lblResult = New-Object System.Windows.Forms.Label
$lblResult.Text = 'Result:'
$lblResult.Location = New-Object System.Drawing.Point(12, 158)
$lblResult.AutoSize = $true

$txtResult = New-Object System.Windows.Forms.TextBox
$txtResult.Location = New-Object System.Drawing.Point(12, 178)
$txtResult.Size = New-Object System.Drawing.Size(576, 180)
$txtResult.Multiline = $true
$txtResult.ReadOnly = $true
$txtResult.ScrollBars = 'Vertical'
$txtResult.Font = $fontMono
$txtResult.Anchor = 'Top,Bottom,Left,Right'
$txtResult.Text = 'Select a Git repo folder, enter the LFS object ID, then click Find file(s).'

$form.Controls.AddRange(@($lblFolder, $txtFolder, $btnBrowse, $lblOid, $txtOid, $btnPaste, $btnFind, $lblResult, $txtResult))
$form.Add_Shown({ $form.Activate(); if ($txtOid.Text.Length -gt 0) { $txtOid.Select() } else { $txtFolder.Select() } })
[void] $form.ShowDialog()
