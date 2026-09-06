# Copies a RAM-only BBG trial over OpenSSH; reboot restores the boot setup.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Target,
    [Parameter(Mandatory = $true, Position = 1)][string]$PanelConfig,
    [string]$Bundle = (Join-Path (Split-Path -Parent $PSScriptRoot) 'build/dld-trial.tar.gz'),
    [string]$KnownHosts,
    [switch]$NoStartupFlash,
    [switch]$NoIdleFlash
)
$ErrorActionPreference = 'Stop'
$dldRemoteDirectory = $null
$dldExit = 2

try {
    if ($Target.Contains(':')) {
        $dldAddress = $null
        if ($Target -notmatch '^[0-9A-Fa-f:]+$' -or
            -not [System.Net.IPAddress]::TryParse($Target, [ref]$dldAddress) -or
            $dldAddress.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetworkV6) {
            throw 'Use an unbracketed numeric IPv6 address.'
        }
        $dldScpHost = '[' + $Target + ']'
    } else {
        if ($Target.Length -gt 253 -or $Target -notmatch '^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$' -or $Target.Contains('..')) {
            throw 'Use a plain hostname, IPv4 address, or unbracketed numeric IPv6 address.'
        }
        $dldScpHost = $Target
    }
    foreach ($dldTool in @('ssh', 'scp')) {
        if (-not (Get-Command $dldTool -CommandType Application -ErrorAction SilentlyContinue)) { throw "$dldTool is required." }
    }
    $dldBootstrap = Join-Path $PSScriptRoot 'trial-remote.py'
    foreach ($dldFile in @($Bundle, $PanelConfig, $dldBootstrap)) {
        if (-not (Test-Path -LiteralPath $dldFile -PathType Leaf)) { throw "Missing local file: $dldFile" }
        if ((Get-Item -LiteralPath $dldFile).Length -eq 0) { throw "Empty local file: $dldFile" }
    }
    $dldBundle = (Resolve-Path -LiteralPath $Bundle).ProviderPath
    $dldPanel = (Resolve-Path -LiteralPath $PanelConfig).ProviderPath
    $dldSshOptions = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes')
    if ($KnownHosts) {
        if (-not (Test-Path -LiteralPath $KnownHosts -PathType Leaf)) { throw "Cannot read known-hosts file: $KnownHosts" }
        $dldKnownHosts = (Resolve-Path -LiteralPath $KnownHosts).ProviderPath.Replace('\', '/')
        if ($dldKnownHosts.Contains('"') -or $dldKnownHosts.Contains("`n") -or $dldKnownHosts.Contains("`r")) { throw 'Invalid known-hosts path.' }
        $dldSshOptions += @('-o', ('UserKnownHostsFile="{0}"' -f $dldKnownHosts))
    }

    # Bypass Windows PowerShell's legacy native argument binder: it can split
    # a quoted UserKnownHostsFile path containing spaces. ArgumentList preserves
    # argv on modern .NET; the fallback applies Windows' standard quote and
    # backslash rules on the .NET Framework used by Windows PowerShell 5.1.
    function Invoke-DldNative([string]$Program, [string[]]$Arguments, [switch]$Capture) {
        $dldStart = New-Object System.Diagnostics.ProcessStartInfo
        $dldStart.FileName = @(Get-Command $Program -CommandType Application -ErrorAction Stop)[0].Source
        $dldStart.UseShellExecute = $false
        $dldStart.CreateNoWindow = $true
        $dldStart.RedirectStandardOutput = $true
        if ($null -ne $dldStart.PSObject.Properties['ArgumentList']) {
            foreach ($dldArgument in $Arguments) { $dldStart.ArgumentList.Add($dldArgument) }
        } else {
            $dldQuoted = foreach ($dldArgument in $Arguments) {
                $dldArgument = [regex]::Replace($dldArgument, '(\\*)"', '$1$1\"')
                $dldArgument = [regex]::Replace($dldArgument, '(\\+)$', '$1$1')
                '"' + $dldArgument + '"'
            }
            $dldStart.Arguments = $dldQuoted -join ' '
        }
        $dldProcess = New-Object System.Diagnostics.Process
        $dldProcess.StartInfo = $dldStart
        $dldLines = New-Object 'System.Collections.Generic.List[string]'
        try {
            [void]$dldProcess.Start()
            while ($null -ne ($dldLine = $dldProcess.StandardOutput.ReadLine())) {
                if ($Capture) { $dldLines.Add($dldLine) } else { [Console]::Out.WriteLine($dldLine) }
            }
            $dldProcess.WaitForExit()
            return @{ ExitCode = $dldProcess.ExitCode; Output = $dldLines.ToArray() }
        } finally {
            $dldProcess.Dispose()
        }
    }

    $dldPrepare = @'
set -eu
[ `id -u` -eq 0 ] || { printf "%s\n" "root SSH access is required" >&2; exit 3; }
command -v python3 >/dev/null
command -v mktemp >/dev/null
python3 -B -c 'import sys
mounts = [line.split() for line in open("/proc/mounts")]
run = [m for m in mounts if m[1] == "/run"]
if len(run) != 1 or run[0][2] != "tmpfs" or "noexec" in run[0][3].split(","):
    sys.exit("/run must be executable tmpfs")
if len(open("/proc/swaps").read().splitlines()) != 1:
    sys.exit("active swap would prevent a RAM-only trial")'
umask 077
mktemp -d /run/dld-trial.XXXXXX
'@
    $dldPrepare = $dldPrepare.Replace("`r`n", "`n")
    $dldRun = Invoke-DldNative ssh ($dldSshOptions + @("root@$Target", $dldPrepare)) -Capture
    if ($dldRun.ExitCode -ne 0) { $dldExit = $dldRun.ExitCode; throw "Remote RAM-directory preflight failed (exit $dldExit)." }
    $dldOutput = @($dldRun.Output)
    if ($dldOutput.Count -ne 1 -or $dldOutput[0] -cnotmatch '^/run/dld-trial\.[A-Za-z0-9]{6}$') { throw 'Unexpected remote directory response.' }
    $dldRemoteDirectory = $dldOutput[0]
    Write-Output "Trial directory: ${Target}:$dldRemoteDirectory"
    Write-Output 'On failure, files/logs stay in RAM; reboot the BBG to recover.'

    foreach ($dldCopy in @(
        @($dldBundle, 'bundle.tar.gz'),
        @($dldPanel, 'panel.json'),
        @($dldBootstrap, 'trial-bootstrap.py')
    )) {
        $dldRun = Invoke-DldNative scp (@('-O') + $dldSshOptions + @($dldCopy[0], "root@${dldScpHost}:$dldRemoteDirectory/$($dldCopy[1])"))
        if ($dldRun.ExitCode -ne 0) { $dldExit = $dldRun.ExitCode; throw "Transfer failed (exit $dldExit)." }
    }
    $dldCommand = "cd $dldRemoteDirectory && python3 -B trial-bootstrap.py --bundle bundle.tar.gz --panel panel.json"
    if ($NoStartupFlash) { $dldCommand += ' --no-startup-flash' }
    if ($NoIdleFlash) { $dldCommand += ' --no-idle-flash' }
    $dldRun = Invoke-DldNative ssh ($dldSshOptions + @("root@$Target", $dldCommand))
    if ($dldRun.ExitCode -ne 0) { $dldExit = $dldRun.ExitCode; throw "Handover failed (exit $dldExit)." }
    Write-Output "DLD trial is running from ${Target}:$dldRemoteDirectory"
    exit 0
} catch {
    [Console]::Error.WriteLine('deploy-trial: ' + $_.Exception.Message)
    if ($dldRemoteDirectory) { [Console]::Error.WriteLine("Retained $dldRemoteDirectory; inspect its logs or reboot the BBG to recover.") }
    exit $dldExit
}
