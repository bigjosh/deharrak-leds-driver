param(
    [string]$HostName = 'beaglebone',
    [string]$RemoteDirectory = ('/root/dld-build-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,6))
)
$ErrorActionPreference = 'Stop'
$dldRoot = Split-Path -Parent $PSScriptRoot
if ($HostName -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]*$') { throw 'Use a plain SSH hostname' }
if ($RemoteDirectory -notmatch '^/root/dld-[A-Za-z0-9_.-]+$') { throw 'Use a new /root/dld-NAME directory with no spaces or nested paths' }
$dldOutput = Join-Path $dldRoot 'build'
New-Item -ItemType Directory -Path $dldOutput -Force | Out-Null
$dldArchive = Join-Path $dldOutput 'source.tar'
Push-Location $dldRoot
try {
    # Exclude generated files even when packaging an already-built checkout.
    $dldExcludes = @('--exclude=__pycache__', '--exclude=*.pyc',
        '--exclude=kernel/*.o', '--exclude=kernel/*.ko', '--exclude=kernel/*.mod.c',
        '--exclude=kernel/.*.cmd', '--exclude=kernel/.*.d', '--exclude=kernel/.tmp_versions',
        '--exclude=kernel/Module.symvers', '--exclude=kernel/modules.order')
    & tar @dldExcludes -cf $dldArchive Makefile README.md spec.md todo.md .gitignore .gitattributes requirements-bench.txt include src pru kernel vendor config tools tests docs
    if ($LASTEXITCODE -ne 0) { throw 'Source archive failed' }
    & ssh -o BatchMode=yes -o ConnectTimeout=10 "root@$HostName" "mkdir $RemoteDirectory"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create a fresh remote build directory (existing directories are never reused by this script)' }
    & scp -O -o BatchMode=yes -o ConnectTimeout=10 $dldArchive "root@${HostName}:$RemoteDirectory/source.tar"
    if ($LASTEXITCODE -ne 0) { throw 'Source transfer failed' }
    # -m gives only our newly extracted files the board's local timestamp.
    # This avoids changing the BBG clock when its RTC date is old.
    & ssh -o BatchMode=yes -o ConnectTimeout=10 "root@$HostName" "cd $RemoteDirectory && tar -xmf source.tar && make -j2 LOCK_PATH=$RemoteDirectory/dld.lock all test report"
    if ($LASTEXITCODE -ne 0) { throw 'Native build or tests failed; inspect the dedicated remote directory' }
    Write-Output "Build and non-hardware tests completed in $RemoteDirectory"
    Write-Output 'PRU/GPIO ownership and service state have not been changed by this script.'
} finally { Pop-Location }
