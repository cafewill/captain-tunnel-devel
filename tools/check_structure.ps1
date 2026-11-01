$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$required = @(
    'AGENTS.md', 'GLANCE.md', 'README.md', 'pyproject.toml', 'requirements.txt', 'run.py',
    'src/captain_tunnel/__init__.py', 'src/captain_tunnel/__main__.py',
    'src/captain_tunnel/cli.py', 'src/captain_tunnel/application.py',
    'src/captain_tunnel/configuration.py', 'src/captain_tunnel/paths.py',
    'src/captain_tunnel/icons_data.py', 'src/captain_tunnel/assets/captain_tunnel.ico',
    'config/captain_tunnel.example.json', 'tests/integration_engine.py',
    'tests/test_lifecycle.py', 'tests/test_configuration.py', 'tests/test_cli.py',
    'tools/build.py', 'tools/entrypoint.py', 'docs/docs.md', 'docs/user-guide.md',
    'docs/development.md', 'docs/architecture.md', 'docs/migration.md', 'docs/review.md'
)
foreach ($relative in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $relative) -PathType Leaf)) {
        throw "Missing file: $relative"
    }
}
Write-Output "PASS required files ($($required.Count))"

$markdown = @('AGENTS.md', 'GLANCE.md', 'README.md') | ForEach-Object { Get-Item -LiteralPath (Join-Path $root $_) }
$markdown += @(Get-ChildItem -LiteralPath (Join-Path $root 'docs') -Filter '*.md' -File)
$linkCount = 0
foreach ($document in $markdown) {
    $body = [IO.File]::ReadAllText($document.FullName)
    foreach ($match in [regex]::Matches($body, '\]\(([^)]+)\)')) {
        $target = $match.Groups[1].Value
        if ($target -match '^(https?://|#|mailto:)') { continue }
        $local = $target.Split('#')[0]
        if (-not (Test-Path -LiteralPath (Join-Path $document.DirectoryName $local))) {
            throw "Broken link in $($document.Name): $target"
        }
        $linkCount++
    }
}
Write-Output "PASS document links ($linkCount)"

$sample = Get-Content -LiteralPath (Join-Path $root 'config/captain_tunnel.example.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($sample.server.password_dpapi -or $sample.server.password_svc -or $sample.server.remember_password) {
    throw 'Example contains a remembered password'
}
if ($sample.tunnels[0].bind -ne '127.0.0.1') { throw 'Example must bind loopback' }
Write-Output 'PASS example JSON and secret-free defaults'

$app = [IO.File]::ReadAllText((Join-Path $root 'src/captain_tunnel/application.py'))
foreach ($expected in @('APP_NAME = "캡틴 터널링"', 'SVC_NAME = "CaptainTunnel"', 'RUN_VALUE = "CaptainTunnel"',
                         'Local\\CaptainTunnel_SingleInstance', 'ConnectionAttemptCancelled', 'validate_config(cfg)')) {
    if (-not $app.Contains($expected)) { throw "Missing runtime invariant: $expected" }
}
if ($app -match 'IFMSTunnel|IFMS_Tunnel|ifms_tunnel\.json') { throw 'Old runtime identifier remains' }
Write-Output 'PASS product and lifecycle identifiers'

$original = Join-Path $root 'legacy/20261030-IFMS터널링유틸/src'
if (Test-Path -LiteralPath $original) {
    $old = [IO.File]::ReadAllText((Join-Path $original 'ifms_tunnel.py'))
    $pattern = '(?s)STD_TUNNELS = \[.*?\n\]'
    $oldPreset = [regex]::Match($old.Replace("`r`n", "`n"), $pattern).Value
    $newPreset = [regex]::Match($app.Replace("`r`n", "`n"), $pattern).Value
    if (-not $oldPreset -or $oldPreset -cne $newPreset) {
        throw 'Default IFMS tunnel list differs from original'
    }
    foreach ($pair in @(@('icons_data.py', 'src/captain_tunnel/icons_data.py'),
                         @('ifms_tunnel.ico', 'src/captain_tunnel/assets/captain_tunnel.ico'))) {
        $before = (Get-FileHash -LiteralPath (Join-Path $original $pair[0]) -Algorithm SHA256).Hash
        $after = (Get-FileHash -LiteralPath (Join-Path $root $pair[1]) -Algorithm SHA256).Hash
        if ($before -cne $after) { throw "Asset differs: $($pair[0])" }
    }
    Write-Output 'PASS original preset and asset equivalence'
}
Write-Output 'Structure checks passed. Python tests and Windows service checks are separate.'
