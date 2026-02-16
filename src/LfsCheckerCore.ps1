# LFS Checker — core logic (dot-source from LfsChecker.ps1 or runspace)
# No GUI; functions accept optional $SharedState.Stop for cancellation.

$LFS_VERSION_LINE = 'version https://git-lfs.github.com/spec/v1'
$OID_REGEX = [regex]::new('^oid sha256:([0-9a-f]{64})\s*$', 'IgnoreCase')

function Test-LfsPointerFile {
    param([string] $FilePath, [int] $MaxSize = 500)
    if (-not [System.IO.File]::Exists($FilePath)) { return $false }
    $fi = Get-Item -LiteralPath $FilePath -ErrorAction SilentlyContinue
    if (-not $fi -or $fi.Length -gt $MaxSize) { return $false }
    $lines = [System.IO.File]::ReadAllLines($FilePath, [System.Text.Encoding]::UTF8)
    if ($lines.Count -lt 2) { return $false }
    if ($lines[0] -notlike '*version https://git-lfs.github.com/spec/v1*') { return $false }
    foreach ($line in $lines[1..($lines.Count - 1)]) {
        if ($line -match $OID_REGEX) { return $true }
    }
    $false
}

function Get-OidFromPointer {
    param([string] $FilePath)
    $lines = [System.IO.File]::ReadAllLines($FilePath, [System.Text.Encoding]::UTF8)
    foreach ($line in $lines) {
        if ($line -match $OID_REGEX) { return $Matches[1] }
    }
    $null
}

function Get-LfsObjectPath {
    param([string] $GitDir, [string] $Oid)
    Join-Path $GitDir ('lfs\objects\' + $Oid.Substring(0, 2) + '\' + $Oid.Substring(2, 2) + '\' + $Oid)
}

function Find-GitRoot {
    param([string] $StartPath)
    $current = [System.IO.Path]::GetFullPath($StartPath)
    if (-not [System.IO.Directory]::Exists($current)) { return $null }
    while ($current) {
        $gitDir = Join-Path $current '.git'
        if ([System.IO.Directory]::Exists($gitDir)) { return $current }
        $parent = [System.IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
    $null
}

function Get-LfsPointers {
    param(
        [string] $RepoRoot,
        [System.Collections.Generic.List[string]] $Log,
        [hashtable] $SharedState
    )
    $gitDir = Join-Path $RepoRoot '.git'
    $results = [System.Collections.Generic.List[object]]::new()
    if ($Log) { $Log.Add('Scanning local repo only (no remote).'); $Log.Add('Phase 1: Finding LFS pointer files…') }
    $allDirs = @(Get-ChildItem -LiteralPath $RepoRoot -Recurse -Directory -ErrorAction SilentlyContinue)
    $dirsToScan = @($RepoRoot)
    foreach ($d in $allDirs) {
        $rel = $d.FullName.Replace($RepoRoot, '').TrimStart('\', '/')
        if ($rel -eq '.git' -or $rel.StartsWith('.git\') -or $rel.StartsWith('.git/')) { continue }
        $dirsToScan += $d.FullName
    }
    foreach ($dir in $dirsToScan) {
        if ($SharedState -and $SharedState.Stop) { if ($Log) { $Log.Add('  Stopped by user.') }; return @($results) }
        $files = Get-ChildItem -LiteralPath $dir -File -ErrorAction SilentlyContinue
        foreach ($f in $files) {
            if ($SharedState -and $SharedState.Stop) { if ($Log) { $Log.Add('  Stopped by user.') }; return @($results) }
            if (-not (Test-LfsPointerFile -FilePath $f.FullName)) { continue }
            $oid = Get-OidFromPointer -FilePath $f.FullName
            if ($oid) {
                $rel = $f.FullName.Replace($RepoRoot, '').TrimStart('\', '/')
                $results.Add([PSCustomObject]@{ RelativePath = $rel; Oid = $oid })
            }
        }
    }
    if ($Log) { $Log.Add("  Found $($results.Count) LFS pointer file(s).") }
    return @($results)
}

function Get-MissingLfsObjects {
    param(
        [string] $RepoRoot,
        [System.Collections.Generic.List[string]] $Log,
        [hashtable] $SharedState
    )
    $gitDir = Join-Path $RepoRoot '.git'
    if (-not [System.IO.Directory]::Exists($gitDir)) { return @() }
    $pointers = Get-LfsPointers -RepoRoot $RepoRoot -Log $Log -SharedState $SharedState
    if ($SharedState -and $SharedState.Stop) { return @() }
    if ($Log) { $Log.Add('Phase 2: Checking which LFS objects are missing locally…') }
    $missing = [System.Collections.Generic.List[object]]::new()
    foreach ($p in $pointers) {
        if ($SharedState -and $SharedState.Stop) { if ($Log) { $Log.Add('  Stopped by user.') }; break }
        $objPath = Get-LfsObjectPath -GitDir $gitDir -Oid $p.Oid
        if (-not [System.IO.File]::Exists($objPath)) {
            $missing.Add([PSCustomObject]@{ RelativePath = $p.RelativePath; Oid = $p.Oid })
        }
    }
    if ($Log) { $Log.Add("  Missing: $($missing.Count) object(s).") }
    return @($missing)
}

function Get-LfsPatternsFromGitattributes {
    param([string] $Path)
    $patterns = [System.Collections.Generic.List[string]]::new()
    if (-not [System.IO.File]::Exists($Path)) { return @($patterns) }
    foreach ($line in [System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8)) {
        $line = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#')) { continue }
        if ($line -notlike '*filter=lfs*') { continue }
        $first = ($line -split '\s+')[0]
        if ($first) { $patterns.Add($first) }
    }
    return @($patterns)
}

function Test-PathMatchesLfsPattern {
    param([string] $RelativePathPosix, [string] $Pattern)
    $posix = $RelativePathPosix.Replace('\', '/').ToLowerInvariant()
    $patternLower = $Pattern.ToLowerInvariant()
    if ($Pattern -like '*/**') {
        $prefix = $Pattern.Substring(0, $Pattern.Length - 3).ToLowerInvariant()
        return ($posix -eq $prefix -or $posix.StartsWith($prefix + '/'))
    }
    if ($Pattern -like '*/*') {
        return ($posix -like $patternLower)
    }
    $name = [System.IO.Path]::GetFileName($RelativePathPosix).ToLowerInvariant()
    return ($name -like $patternLower)
}

$script:GITIGNORE_BATCH = 200
function Get-GitIgnoredPaths {
    param([string] $RepoRoot, [string[]] $Paths)
    $ignored = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    for ($i = 0; $i -lt $Paths.Length; $i += $script:GITIGNORE_BATCH) {
        $end = [Math]::Min($i + $script:GITIGNORE_BATCH - 1, $Paths.Length - 1)
        $batch = $Paths[$i..$end]
        $inputText = $batch -join "`n"
        try {
            $result = $inputText | & git -C $RepoRoot check-ignore --stdin 2>$null
            if ($result) {
                foreach ($p in ($result | ForEach-Object { $_.Trim() })) {
                    if ($p -and $batch -contains $p) { [void]$ignored.Add($p) }
                }
            }
        } catch { }
    }
    return $ignored
}

function Get-LocalAssetsNotInLfs {
    param(
        [string] $RepoRoot,
        [System.Collections.Generic.List[string]] $Log,
        [hashtable] $SharedState
    )
    $gitattributes = Join-Path $RepoRoot '.gitattributes'
    if (-not [System.IO.File]::Exists($gitattributes)) { return @() }
    $patterns = Get-LfsPatternsFromGitattributes -Path $gitattributes
    if ($patterns.Count -eq 0) { return @() }
    $gitDir = Join-Path $RepoRoot '.git'
    $candidates = [System.Collections.Generic.List[string]]::new()
    if ($Log) { $Log.Add('Finding local assets (match .gitattributes, not LFS pointers)…') }
    $allDirs = @(Get-ChildItem -LiteralPath $RepoRoot -Recurse -Directory -ErrorAction SilentlyContinue)
    $dirsToScan = @($RepoRoot)
    foreach ($d in $allDirs) {
        $rel = $d.FullName.Replace($RepoRoot, '').TrimStart('\', '/')
        if ($rel -eq '.git' -or $rel.StartsWith('.git\') -or $rel.StartsWith('.git/')) { continue }
        $dirsToScan += $d.FullName
    }
    foreach ($dir in $dirsToScan) {
        if ($SharedState -and $SharedState.Stop) { if ($Log) { $Log.Add('  Stopped by user.') }; break }
        $files = Get-ChildItem -LiteralPath $dir -File -ErrorAction SilentlyContinue
        foreach ($f in $files) {
            if ($SharedState -and $SharedState.Stop) { break }
            $rel = $f.FullName.Replace($RepoRoot, '').TrimStart('\', '/')
            $relPosix = $rel.Replace('\', '/')
            $matchesPattern = $false
            foreach ($p in $patterns) { if (Test-PathMatchesLfsPattern -RelativePathPosix $relPosix -Pattern $p) { $matchesPattern = $true; break } }
            if (-not $matchesPattern) { continue }
            if (Test-LfsPointerFile -FilePath $f.FullName) { continue }
            $candidates.Add($relPosix)
        }
    }
    if ($Log) { $Log.Add("  Found $($candidates.Count) candidate(s).") }
    if ($candidates.Count -eq 0) { return @() }
    if ($Log) { $Log.Add('Filtering by .gitignore (batched)…') }
    $ignored = Get-GitIgnoredPaths -RepoRoot $RepoRoot -Paths $candidates
    $result = $candidates | Where-Object { -not $ignored.Contains($_) } | Sort-Object
    if ($Log) { $Log.Add("  After .gitignore: $($result.Count) local-only asset(s).") }
    return @($result)
}
