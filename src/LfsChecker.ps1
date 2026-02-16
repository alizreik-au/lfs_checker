#Requires -Version 5.1
<#
.SYNOPSIS
    LFS Checker â€” Find missing Git LFS objects and local assets not in LFS.
    Modern Windows Forms GUI; core logic runs in PowerShell for speed.
.DESCRIPTION
    Pick a repo folder, run "Check missing" and/or "Check local not in LFS".
    Add & commit local-only assets, then Push to remote. All operations are local
    except Push (no remote scan).
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string] $InitialRepoPath = ''
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# ---------- Theme (modern, clean) ----------
$script:Colors = @{
    FormBg       = [System.Drawing.Color]::FromArgb(245, 245, 250)
    CardBg       = [System.Drawing.Color]::FromArgb(255, 255, 255)
    Accent       = [System.Drawing.Color]::FromArgb(0, 120, 212)
    AccentHover  = [System.Drawing.Color]::FromArgb(0, 99, 177)
    Text         = [System.Drawing.Color]::FromArgb(45, 45, 48)
    TextMuted    = [System.Drawing.Color]::FromArgb(96, 94, 92)
    Border       = [System.Drawing.Color]::FromArgb(237, 235, 233)
    Error        = [System.Drawing.Color]::FromArgb(196, 43, 28)
    Success      = [System.Drawing.Color]::FromArgb(16, 124, 16)
}
$script:FontTitle = New-Object System.Drawing.Font('Segoe UI', 11, [System.Drawing.FontStyle]::Bold)
$script:FontBody  = New-Object System.Drawing.Font('Segoe UI', 9)
$script:FontMono  = New-Object System.Drawing.Font('Cascadia Code', 9)
if (-not $FontMono) { $script:FontMono = New-Object System.Drawing.Font('Consolas', 9) }

# Dot-source core LFS logic (same script is used by runspaces)
$script:CorePath = Join-Path $PSScriptRoot 'LfsCheckerCore.ps1'
. $script:CorePath

# Shared cancel flag for background checks (synchronized so runspace can see it)
$script:SharedStop = [hashtable]::Synchronized(@{ Stop = $false })

# ---------- GUI ----------
$script:StopRequested = $false
$script:LastMissing = @()
$script:LastLocalNotLfs = @()
$script:ViewMode = 'Simple'  # Simple | Detailed

function Add-Log {
    param([string] $Text, [System.Windows.Forms.TextBox] $LogBox)
    if (-not $LogBox) { return }
    $LogBox.Invoke([Action]{
        param($msg)
        $LogBox.AppendText($msg + "`r`n")
        $LogBox.ScrollToCaret()
    }, $Text)
}

function Set-Status {
    param([string] $Text, [System.Windows.Forms.Label] $StatusLabel)
    if ($StatusLabel) {
        $StatusLabel.Invoke([Action]{ param($msg) $StatusLabel.Text = $msg }, $Text)
    }
}

function Update-ResultsGrid {
    param(
        [System.Windows.Forms.DataGridView] $MissingGrid,
        [System.Windows.Forms.DataGridView] $LocalGrid,
        [array] $Missing,
        [array] $LocalNotLfs,
        [System.Windows.Forms.TextBox] $DetailBox
    )
    $MissingGrid.Invoke([Action]{
        param($m, $l, $dgM, $dgL, $detail)
        $dgM.Rows.Clear()
        $dgL.Rows.Clear()
        $idx = 1
        foreach ($item in $m) {
            $dgM.Rows.Add($idx, $item.RelativePath, $item.Oid) | Out-Null
            $idx++
        }
        $idx = 1
        foreach ($path in $l) {
            $dgL.Rows.Add($idx, $path) | Out-Null
            $idx++
        }
        if ($m.Count -eq 0 -and $l.Count -eq 0) {
            $dgM.Rows.Add('', 'No missing LFS objects.', '') | Out-Null
            $dgL.Rows.Add('', 'No local-only assets under LFS patterns.') | Out-Null
        }
        # Detailed view
        $sb = [System.Text.StringBuilder]::new()
        [void]$sb.AppendLine('1) Missing LFS objects (pointer exists, blob missing)')
        [void]$sb.AppendLine('   ' + ('-' * 50))
        if ($m.Count -eq 0) { [void]$sb.AppendLine('   None.') }
        else {
            foreach ($item in $m) {
                [void]$sb.AppendLine("   $($item.RelativePath)")
                [void]$sb.AppendLine("      oid sha256:$($item.Oid)")
            }
        }
        [void]$sb.AppendLine('')
        [void]$sb.AppendLine('2) Local assets not in LFS (match .gitattributes but are real files)')
        [void]$sb.AppendLine('   ' + ('-' * 50))
        if ($l.Count -eq 0) { [void]$sb.AppendLine('   None.') }
        else { foreach ($path in $l) { [void]$sb.AppendLine("   $path") } }
        $detail.Text = $sb.ToString()
    }, $Missing, $LocalNotLfs, $MissingGrid, $LocalGrid, $DetailBox)
}

function New-AccentButton {
    param([string] $Text, [scriptblock] $OnClick)
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = $Text
    $btn.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btn.FlatAppearance.BorderSize = 0
    $btn.BackColor = $script:Colors.Accent
    $btn.ForeColor = [System.Drawing.Color]::White
    $btn.Font = $script:FontBody
    $btn.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btn.Add_Click($OnClick)
    $btn.Add_MouseEnter({ $this.BackColor = $script:Colors.AccentHover })
    $btn.Add_MouseLeave({ $this.BackColor = $script:Colors.Accent })
    $btn
}

function New-SecondaryButton {
    param([string] $Text, [scriptblock] $OnClick)
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = $Text
    $btn.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btn.FlatAppearance.BorderColor = $script:Colors.Border
    $btn.BackColor = $script:Colors.CardBg
    $btn.ForeColor = $script:Colors.Text
    $btn.Font = $script:FontBody
    $btn.Cursor = [System.Windows.Forms.Cursors]::Hand
    $btn.Add_Click($OnClick)
    $btn
}

# Build form
$form = New-Object System.Windows.Forms.Form
$form.Text = 'LFS Checker'
$form.Size = New-Object System.Drawing.Size(820, 640)
$form.MinimumSize = New-Object System.Drawing.Size(640, 480)
$form.StartPosition = 'CenterScreen'
$form.BackColor = $script:Colors.FormBg
$form.Font = $script:FontBody
$form.FormBorderStyle = 'Sizable'

$mainPanel = New-Object System.Windows.Forms.Panel
$mainPanel.Dock = 'Fill'
$mainPanel.Padding = New-Object System.Windows.Forms.Padding(20, 16, 20, 16)
$mainPanel.BackColor = $script:Colors.FormBg
$form.Controls.Add($mainPanel)

# Header
$header = New-Object System.Windows.Forms.Label
$header.Text = 'LFS Checker'
$header.Font = $script:FontTitle
$header.ForeColor = $script:Colors.Text
$header.Location = New-Object System.Drawing.Point(20, 12)
$header.AutoSize = $true
$mainPanel.Controls.Add($header)

$subHeader = New-Object System.Windows.Forms.Label
$subHeader.Text = 'Find missing LFS objects and local assets not in LFS'
$subHeader.Font = $script:FontBody
$subHeader.ForeColor = $script:Colors.TextMuted
$subHeader.Location = New-Object System.Drawing.Point(22, 38)
$subHeader.AutoSize = $true
$mainPanel.Controls.Add($subHeader)

# Repo row
$lblRepo = New-Object System.Windows.Forms.Label
$lblRepo.Text = 'Repo folder:'
$lblRepo.Location = New-Object System.Drawing.Point(20, 72)
$lblRepo.AutoSize = $true
$mainPanel.Controls.Add($lblRepo)

$txtRepo = New-Object System.Windows.Forms.TextBox
$txtRepo.Location = New-Object System.Drawing.Point(100, 68)
$txtRepo.Size = New-Object System.Drawing.Size(520, 24)
$txtRepo.Anchor = 'Left, Top, Right'
$txtRepo.Font = $script:FontBody
if ($InitialRepoPath) {
    try {
        $resolved = [System.IO.Path]::GetFullPath($InitialRepoPath.Trim().Trim('"'))
        if ([System.IO.Directory]::Exists($resolved)) { $txtRepo.Text = $resolved }
    } catch { $txtRepo.Text = $InitialRepoPath }
}
$mainPanel.Controls.Add($txtRepo)

$btnBrowse = New-Object System.Windows.Forms.Button
$btnBrowse.Text = 'Browse...'
$btnBrowse.Location = New-Object System.Drawing.Point(628, 66)
$btnBrowse.Size = New-Object System.Drawing.Size(90, 28)
$btnBrowse.FlatStyle = 'Flat'
$btnBrowse.Cursor = [System.Windows.Forms.Cursors]::Hand
$mainPanel.Controls.Add($btnBrowse)

$folderDialog = New-Object System.Windows.Forms.FolderBrowserDialog
$folderDialog.Description = 'Select repo folder'
$btnBrowse.Add_Click({
    if ($folderDialog.ShowDialog() -eq 'OK') { $txtRepo.Text = $folderDialog.SelectedPath }
})

# Buttons row
$yButtons = 108
$btnCheckMissing = New-AccentButton 'Check missing (local)' {}
$btnCheckMissing.Location = New-Object System.Drawing.Point(20, $yButtons)
$btnCheckMissing.Size = New-Object System.Drawing.Size(140, 32)
$mainPanel.Controls.Add($btnCheckMissing)

$btnCheckLocal = New-AccentButton 'Check local not in LFS' {}
$btnCheckLocal.Location = New-Object System.Drawing.Point(168, $yButtons)
$btnCheckLocal.Size = New-Object System.Drawing.Size(165, 32)
$mainPanel.Controls.Add($btnCheckLocal)

$btnCommit = New-SecondaryButton "Add & commit" {}
$btnCommit.Location = New-Object System.Drawing.Point(341, $yButtons)
$btnCommit.Size = New-Object System.Drawing.Size(100, 32)
$mainPanel.Controls.Add($btnCommit)

$btnPush = New-SecondaryButton 'Push to remote' {}
$btnPush.Location = New-Object System.Drawing.Point(449, $yButtons)
$btnPush.Size = New-Object System.Drawing.Size(110, 32)
$mainPanel.Controls.Add($btnPush)

$btnStop = New-Object System.Windows.Forms.Button
$btnStop.Text = 'Stop'
$btnStop.Location = New-Object System.Drawing.Point(567, $yButtons)
$btnStop.Size = New-Object System.Drawing.Size(75, 32)
$btnStop.FlatStyle = 'Flat'
$btnStop.BackColor = $script:Colors.CardBg
$btnStop.Enabled = $false
$mainPanel.Controls.Add($btnStop)

# View mode
$rbSimple = New-Object System.Windows.Forms.RadioButton
$rbSimple.Text = 'Simple (table)'
$rbSimple.Location = New-Object System.Drawing.Point(652, ($yButtons + 6))
$rbSimple.Checked = $true
$rbSimple.Font = $script:FontBody
$mainPanel.Controls.Add($rbSimple)

$rbDetailed = New-Object System.Windows.Forms.RadioButton
$rbDetailed.Text = 'Detailed (hashes)'
$rbDetailed.Location = New-Object System.Drawing.Point(752, ($yButtons + 6))
$rbDetailed.Font = $script:FontBody
$mainPanel.Controls.Add($rbDetailed)

# Status
$statusLabel = New-Object System.Windows.Forms.Label
$statusLabel.Text = 'Choose a repo folder, then run one or both checks.'
$statusLabel.Location = New-Object System.Drawing.Point(20, 148)
$statusLabel.AutoSize = $true
$statusLabel.ForeColor = $script:Colors.TextMuted
$mainPanel.Controls.Add($statusLabel)

# Progress
$progress = New-Object System.Windows.Forms.ProgressBar
$progress.Location = New-Object System.Drawing.Point(20, 172)
$progress.Size = New-Object System.Drawing.Size(400, 8)
$progress.Style = 'Marquee'
$progress.MarqueeAnimationSpeed = 30
$progress.Visible = $false
$mainPanel.Controls.Add($progress)

# Log area
$lblLog = New-Object System.Windows.Forms.Label
$lblLog.Text = 'Log (local scan only, no remote)'
$lblLog.Location = New-Object System.Drawing.Point(20, 192)
$lblLog.Font = $script:FontTitle
$lblLog.AutoSize = $true
$mainPanel.Controls.Add($lblLog)

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Location = New-Object System.Drawing.Point(20, 216)
$logBox.Multiline = $true
$logBox.ReadOnly = $true
$logBox.ScrollBars = 'Vertical'
$logBox.Font = $script:FontMono
$logBox.BorderStyle = 'FixedSingle'
$logBox.BackColor = $script:Colors.CardBg
$logBox.Anchor = 'Top, Left, Right'
$logBox.Height = 88
$logBox.Width = 780
$mainPanel.Controls.Add($logBox)

# Results: two panels (Simple = grids, Detailed = text)
$resultsPanel = New-Object System.Windows.Forms.Panel
$resultsPanel.Location = New-Object System.Drawing.Point(20, 312)
$resultsPanel.Anchor = 'Top, Bottom, Left, Right'
$resultsPanel.BackColor = $script:Colors.FormBg
$mainPanel.Controls.Add($resultsPanel)

# Simple: two DataGridViews
$panelSimple = New-Object System.Windows.Forms.Panel
$panelSimple.Dock = 'Fill'
$panelSimple.Padding = New-Object System.Windows.Forms.Padding(0)

$lblMissing = New-Object System.Windows.Forms.Label
$lblMissing.Text = 'Missing LFS objects (pointer exists, blob missing)'
$lblMissing.Font = $script:FontTitle
$lblMissing.Location = New-Object System.Drawing.Point(0, 0)
$panelSimple.Controls.Add($lblMissing)

$dgMissing = New-Object System.Windows.Forms.DataGridView
$dgMissing.Location = New-Object System.Drawing.Point(0, 24)
$dgMissing.Size = New-Object System.Drawing.Size(760, 120)
$dgMissing.Anchor = 'Top, Left, Right'
$dgMissing.AllowUserToAddRows = $false
$dgMissing.ReadOnly = $true
$dgMissing.SelectionMode = 'FullRowSelect'
$dgMissing.MultiSelect = $true
$dgMissing.RowHeadersVisible = $false
$dgMissing.BackgroundColor = $script:Colors.CardBg
$dgMissing.BorderStyle = 'None'
$dgMissing.ColumnHeadersHeightSizeMode = 'AutoSize'
$dgMissing.EnableHeadersVisualStyles = $true
$dgMissing.Font = $script:FontBody
$dgMissing.ColumnCount = 3
$dgMissing.Columns[0].Name = '#'
$dgMissing.Columns[0].Width = 36
$dgMissing.Columns[1].Name = 'File path'
$dgMissing.Columns[1].Width = 420
$dgMissing.Columns[2].Name = 'OID'
$dgMissing.Columns[2].Width = 280
$panelSimple.Controls.Add($dgMissing)

$lblLocal = New-Object System.Windows.Forms.Label
$lblLocal.Text = 'Local assets not in LFS (match .gitattributes but are real files)'
$lblLocal.Font = $script:FontTitle
$lblLocal.Location = New-Object System.Drawing.Point(0, 152)
$panelSimple.Controls.Add($lblLocal)

$dgLocal = New-Object System.Windows.Forms.DataGridView
$dgLocal.Location = New-Object System.Drawing.Point(0, 176)
$dgLocal.Size = New-Object System.Drawing.Size(760, 120)
$dgLocal.Anchor = 'Top, Left, Right'
$dgLocal.AllowUserToAddRows = $false
$dgLocal.ReadOnly = $true
$dgLocal.SelectionMode = 'FullRowSelect'
$dgLocal.MultiSelect = $true
$dgLocal.RowHeadersVisible = $false
$dgLocal.BackgroundColor = $script:Colors.CardBg
$dgLocal.BorderStyle = 'None'
$dgLocal.ColumnHeadersHeightSizeMode = 'AutoSize'
$dgLocal.Font = $script:FontBody
$dgLocal.ColumnCount = 2
$dgLocal.Columns[0].Name = '#'
$dgLocal.Columns[0].Width = 36
$dgLocal.Columns[1].Name = 'File path'
$dgLocal.Columns[1].Width = 700
$panelSimple.Controls.Add($dgLocal)

# Detailed: one text box
$panelDetailed = New-Object System.Windows.Forms.Panel
$panelDetailed.Dock = 'Fill'
$panelDetailed.Visible = $false

$detailBox = New-Object System.Windows.Forms.TextBox
$detailBox.Multiline = $true
$detailBox.ReadOnly = $true
$detailBox.ScrollBars = 'Both'
$detailBox.Dock = 'Fill'
$detailBox.Font = $script:FontMono
$detailBox.BackColor = $script:Colors.CardBg
$detailBox.BorderStyle = 'None'
$panelDetailed.Controls.Add($detailBox)

$resultsPanel.Controls.Add($panelSimple)
$resultsPanel.Controls.Add($panelDetailed)

$rbSimple.Add_CheckedChanged({
    if ($rbSimple.Checked) {
        $panelDetailed.Visible = $false
        $panelSimple.Visible = $true
        Update-ResultsGrid -MissingGrid $dgMissing -LocalGrid $dgLocal -Missing $script:LastMissing -LocalNotLfs $script:LastLocalNotLfs -DetailBox $detailBox
    }
})
$rbDetailed.Add_CheckedChanged({
    if ($rbDetailed.Checked) {
        $panelSimple.Visible = $false
        $panelDetailed.Visible = $true
        Update-ResultsGrid -MissingGrid $dgMissing -LocalGrid $dgLocal -Missing $script:LastMissing -LocalNotLfs $script:LastLocalNotLfs -DetailBox $detailBox
    }
})

# Resize: anchor results panel height
$form.Add_Resize({
    $mainPanel.SuspendLayout()
    $delta = $form.ClientSize.Height - 320
    if ($delta -gt 0) {
        $resultsPanel.Height = $delta
        $resultsPanel.Top = 312
        $logBox.Height = 88
    }
    $mainPanel.ResumeLayout($true)
})
$resultsPanel.Height = $form.ClientSize.Height - 320
$logBox.Width = $form.ClientSize.Width - 44

# ---------- Actions ----------
function Set-Busy {
    param([bool] $Busy)
    $btnCheckMissing.Enabled = -not $Busy
    $btnCheckLocal.Enabled = -not $Busy
    $btnCommit.Enabled = -not $Busy
    $btnPush.Enabled = -not $Busy
    $btnStop.Enabled = $Busy
    $progress.Visible = $Busy
}

$script:RunspaceCorePath = $script:CorePath

# Check Missing: run in background runspace so UI stays responsive and Stop works
$btnCheckMissing.Add_Click({
    $repoPath = $txtRepo.Text.Trim()
    if (-not $repoPath -or -not [System.IO.Directory]::Exists($repoPath)) {
        [System.Windows.Forms.MessageBox]::Show('Choose a repo folder first.', 'No folder', 'OK', 'Information')
        return
    }
    $root = Find-GitRoot -StartPath $repoPath
    if (-not $root) {
        [System.Windows.Forms.MessageBox]::Show('No .git found.', 'Not a Git repo', 'OK', 'Error')
        return
    }
    $txtRepo.Text = $root
    $script:SharedStop.Stop = $false
    Set-Busy -Busy $true
    Set-Status -Text 'Checking missing (local)...' -StatusLabel $statusLabel
    $logBox.Clear()
    $logBox.AppendText("Checking missing LFS objects (local only)...`r`n")
    $dgMissing.Rows.Clear()
    $dgMissing.Rows.Add('', 'Scanning...', '') | Out-Null

    $runspace = [runspacefactory]::CreateRunspace()
    $runspace.Open()
    $ps = [powershell]::Create().AddScript({
        param($CorePath, $RepoRoot, $Shared)
        . $CorePath
        $logList = [System.Collections.Generic.List[string]]::new()
        $missing = Get-MissingLfsObjects -RepoRoot $RepoRoot -Log $logList -SharedState $Shared
        return @{ Missing = $missing; Log = $logList }
    }).AddArgument($script:RunspaceCorePath).AddArgument($root).AddArgument($script:SharedStop)
    $ps.Runspace = $runspace
    $async = $ps.BeginInvoke()
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 180
    $timer.Add_Tick({
        if ($async.IsCompleted) {
            $timer.Stop()
            $timer.Dispose()
            try {
                $out = $ps.EndInvoke($async)
                $missing = $out.Missing
                $logList = $out.Log
            } catch { $missing = @(); $logList = @() }
            $ps.Dispose()
            $runspace.Close()
            $runspace.Dispose()
            $script:LastMissing = @($missing)
            $logBox.Invoke([Action]{
                $logBox.Clear()
                $logBox.AppendText("Checking missing LFS objects (local only)...`r`n")
                foreach ($line in $logList) { $logBox.AppendText($line + "`r`n") }
            })
            $statusText = if ($script:SharedStop.Stop) { 'Stopped.' } else { ('Done - ' + $script:LastMissing.Count + ' missing LFS (local).') }
            Set-Status -Text $statusText -StatusLabel $statusLabel
            Update-ResultsGrid -MissingGrid $dgMissing -LocalGrid $dgLocal -Missing $script:LastMissing -LocalNotLfs $script:LastLocalNotLfs -DetailBox $detailBox
            Set-Busy -Busy $false
        }
    })
    $timer.Start()
})

# Check Local not in LFS: same runspace pattern
$btnCheckLocal.Add_Click({
    $repoPath = $txtRepo.Text.Trim()
    if (-not $repoPath -or -not [System.IO.Directory]::Exists($repoPath)) {
        [System.Windows.Forms.MessageBox]::Show('Choose a repo folder first.', 'No folder', 'OK', 'Information')
        return
    }
    $root = Find-GitRoot -StartPath $repoPath
    if (-not $root) {
        [System.Windows.Forms.MessageBox]::Show('No .git found.', 'Not a Git repo', 'OK', 'Error')
        return
    }
    $txtRepo.Text = $root
    $script:SharedStop.Stop = $false
    Set-Busy -Busy $true
    Set-Status -Text 'Checking local not in LFS...' -StatusLabel $statusLabel
    $logBox.Clear()
    $logBox.AppendText("Checking local assets not in LFS...`r`n")
    $dgLocal.Rows.Clear()
    $dgLocal.Rows.Add('', 'Scanning...') | Out-Null

    $runspace = [runspacefactory]::CreateRunspace()
    $runspace.Open()
    $ps = [powershell]::Create().AddScript({
        param($CorePath, $RepoRoot, $Shared)
        . $CorePath
        $logList = [System.Collections.Generic.List[string]]::new()
        $localNotLfs = Get-LocalAssetsNotInLfs -RepoRoot $RepoRoot -Log $logList -SharedState $Shared
        return @{ LocalNotLfs = $localNotLfs; Log = $logList }
    }).AddArgument($script:RunspaceCorePath).AddArgument($root).AddArgument($script:SharedStop)
    $ps.Runspace = $runspace
    $async = $ps.BeginInvoke()
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 180
    $timer.Add_Tick({
        if ($async.IsCompleted) {
            $timer.Stop()
            $timer.Dispose()
            try {
                $out = $ps.EndInvoke($async)
                $localNotLfs = $out.LocalNotLfs
                $logList = $out.Log
            } catch { $localNotLfs = @(); $logList = @() }
            $ps.Dispose()
            $runspace.Close()
            $runspace.Dispose()
            $script:LastLocalNotLfs = @($localNotLfs)
            $logBox.Invoke([Action]{
                $logBox.Clear()
                $logBox.AppendText("Checking local assets not in LFS...`r`n")
                foreach ($line in $logList) { $logBox.AppendText($line + "`r`n") }
            })
            $statusText = if ($script:SharedStop.Stop) { 'Stopped.' } else { ('Done - ' + $script:LastLocalNotLfs.Count + ' local asset(s) not in LFS.') }
            Set-Status -Text $statusText -StatusLabel $statusLabel
            Update-ResultsGrid -MissingGrid $dgMissing -LocalGrid $dgLocal -Missing $script:LastMissing -LocalNotLfs $script:LastLocalNotLfs -DetailBox $detailBox
            Set-Busy -Busy $false
        }
    })
    $timer.Start()
})

$btnStop.Add_Click({ $script:SharedStop.Stop = $true; Set-Status -Text 'Stopping...' -StatusLabel $statusLabel })

# Commit: add local-only assets to LFS and commit
$btnCommit.Add_Click({
    $repoPath = $txtRepo.Text.Trim()
    if (-not $repoPath -or -not [System.IO.Directory]::Exists($repoPath)) {
        [System.Windows.Forms.MessageBox]::Show('Choose a repo folder first.', 'No repo', 'OK', 'Information')
        return
    }
    $root = Find-GitRoot -StartPath $repoPath
    if (-not $root) {
        [System.Windows.Forms.MessageBox]::Show('No .git found.', 'Not a Git repo', 'OK', 'Error')
        return
    }
    if ($script:LastLocalNotLfs.Count -eq 0) {
        [System.Windows.Forms.MessageBox]::Show('No local-only assets to commit. Run "Check local not in LFS" first.', "Add & commit", 'OK', 'Information')
        return
    }
    $txtRepo.Text = $root
    Set-Busy -Busy $true
    Set-Status -Text 'Adding and committing...' -StatusLabel $statusLabel
    $logBox.Clear()
    $log = [System.Collections.Generic.List[string]]::new()
    $ok = $true
    foreach ($rel in $script:LastLocalNotLfs) {
        $r = & git -C $root add $rel 2>&1
        $log.Add("git add $rel")
        if ($LASTEXITCODE -ne 0) { $log.Add($r); $ok = $false; break }
    }
    if ($ok) {
        $r = & git -C $root commit -m 'Add local assets to LFS' 2>&1
        $log.Add("git commit -m 'Add local assets to LFS'"); $log.Add($r)
        if ($LASTEXITCODE -ne 0 -and $r -notmatch 'nothing to commit|no changes') { $ok = $false }
    }
    Set-Busy -Busy $false
    Set-Status -Text $(if ($ok) { 'Commit finished.' } else { 'Commit failed.' }) -StatusLabel $statusLabel
    $logBox.Text = $log -join "`r`n"
    [System.Windows.Forms.MessageBox]::Show($(if ($ok) { 'Local assets committed to LFS. Verify, then use Push to remote when ready.' } else { 'Commit failed.' }) + "`r`n`r`n" + ($log -join "`r`n"), $(if ($ok) { "Add & commit" } else { "Add & commit" }), 'OK', $(if ($ok) { 'Information' } else { 'Error' }))
})

# Push: LFS push then git push
$btnPush.Add_Click({
    $repoPath = $txtRepo.Text.Trim()
    if (-not $repoPath -or -not [System.IO.Directory]::Exists($repoPath)) {
        [System.Windows.Forms.MessageBox]::Show('Choose a repo folder first.', 'No repo', 'OK', 'Information')
        return
    }
    $root = Find-GitRoot -StartPath $repoPath
    if (-not $root) {
        [System.Windows.Forms.MessageBox]::Show('No .git found.', 'Not a Git repo', 'OK', 'Error')
        return
    }
    $txtRepo.Text = $root
    Set-Busy -Busy $true
    Set-Status -Text 'Pushing...' -StatusLabel $statusLabel
    $logBox.Clear()
    $branch = (& git -C $root rev-parse --abbrev-ref HEAD 2>$null).Trim()
    if (-not $branch -or $branch -eq 'HEAD') {
        Set-Busy -Busy $false
        [System.Windows.Forms.MessageBox]::Show('Detached HEAD or no branch.', 'Push to remote', 'OK', 'Error')
        return
    }
    $upstream = (& git -C $root rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>$null).Trim()
    if ($upstream -and $upstream -match '^(.+)/(.+)$') { $remote = $Matches[1]; $remoteBranch = $Matches[2] }
    else {
        $remote = (& git -C $root config --get "branch.$branch.remote" 2>$null).Trim()
        if (-not $remote) { $remote = (git -C $root remote 2>$null | Select-Object -First 1); $remoteBranch = $branch }
        else { $remoteBranch = $branch }
    }
    $logLines = @("Branch: $branch, remote: $remote")
    $err = & git -C $root lfs push $remote $remoteBranch --all 2>&1
    $logLines += $err
    if ($LASTEXITCODE -ne 0) {
        Set-Busy -Busy $false
        Set-Status -Text 'Push failed.' -StatusLabel $statusLabel
        $logBox.Text = $logLines -join "`r`n"
        [System.Windows.Forms.MessageBox]::Show('Push failed.`r`n`r`n' + ($logLines -join "`r`n"), 'Push to remote', 'OK', 'Error')
        return
    }
    $err2 = & git -C $root push $remote $remoteBranch 2>&1
    $logLines += $err2
    $ok = ($LASTEXITCODE -eq 0)
    Set-Busy -Busy $false
    Set-Status -Text $(if ($ok) { 'Push finished.' } else { 'Push failed.' }) -StatusLabel $statusLabel
    $logBox.Text = $logLines -join "`r`n"
    [System.Windows.Forms.MessageBox]::Show($(if ($ok) { 'Push completed.' } else { 'Push failed.' }) + "`r`n`r`n" + ($logLines -join "`r`n"), 'Push to remote', 'OK', $(if ($ok) { 'Information' } else { 'Error' }))
})

[void]$form.ShowDialog()
