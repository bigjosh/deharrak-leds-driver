param([string]$Compiler = 'gcc')
$ErrorActionPreference = 'Stop'
$dldRoot = Split-Path -Parent $PSScriptRoot
$dldBuild = Join-Path $dldRoot 'build\windows-pru'
New-Item -ItemType Directory -Path $dldBuild -Force | Out-Null
$dldSources = @('pasm.c','pasmpp.c','pasmexp.c','pasmop.c','pasmdot.c','pasmstruct.c','pasmmacro.c') | ForEach-Object { Join-Path (Join-Path $dldRoot 'vendor\pasm') $_ }
& $Compiler -std=c99 -O2 -D_UNIX_ -o (Join-Path $dldBuild 'pasm.exe') @dldSources
if ($LASTEXITCODE -ne 0) { throw 'PASM compilation failed' }
& (Join-Path $dldBuild 'pasm.exe') -V3 -b -L -l (Join-Path $dldRoot 'pru\ws2812_uniform.p') (Join-Path $dldBuild 'pru')
if ($LASTEXITCODE -ne 0) { throw 'PRU assembly failed' }
$dldBytes = (Get-Item -LiteralPath (Join-Path $dldBuild 'pru.bin')).Length
if ($dldBytes -le 0 -or $dldBytes -gt 8192 -or ($dldBytes % 4) -ne 0) { throw 'PRU image does not fit the 8192-byte instruction RAM' }
Write-Output "PRU image: $dldBytes / 8192 bytes"
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $dldBuild 'pru.bin')
