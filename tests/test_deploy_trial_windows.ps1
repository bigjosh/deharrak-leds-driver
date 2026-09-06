# Offline tests of the actual .bat and Windows PowerShell 5.1 -File entry points.
# Every ssh/scp invocation resolves to a local logging executable; no network is used.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$dldWindowsPowerShell = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
if ($PSVersionTable.PSVersion.Major -ne 5) {
    & $dldWindowsPowerShell -NoLogo -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath
    exit $LASTEXITCODE
}

$dldRepository = Split-Path -Parent $PSScriptRoot
$dldTempParent = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\')
$dldTestDirectory = Join-Path $dldTempParent ('dld deploy windows ' + [guid]::NewGuid().ToString('N'))
[void][System.IO.Directory]::CreateDirectory($dldTestDirectory)
$dldCases = 0

function Assert-Equal($Actual, $Expected, [string]$Context) {
    if ($Actual -cne $Expected) { throw "$Context`: expected <$Expected>, got <$Actual>." }
}

function ConvertTo-NativeArgument([string]$Value) {
    $Value = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $Value = [regex]::Replace($Value, '(\\+)$', '$1$1')
    return '"' + $Value + '"'
}

function Invoke-Launcher([string]$Mode, [string[]]$Options, [int]$PreflightExit = 0) {
    $dldLog = Join-Path $dldTestDirectory ([guid]::NewGuid().ToString('N') + '.calls')
    $dldStart = New-Object System.Diagnostics.ProcessStartInfo
    $dldStart.UseShellExecute = $false
    $dldStart.CreateNoWindow = $true
    $dldStart.RedirectStandardOutput = $true
    $dldStart.RedirectStandardError = $true
    $dldStart.WorkingDirectory = $dldOtherDirectory
    $dldStart.EnvironmentVariables['PATH'] = $dldFakeBin + ';' + $env:PATH
    $dldStart.EnvironmentVariables['DLD_WINDOWS_TEST_LOG'] = $dldLog
    $dldStart.EnvironmentVariables['DLD_WINDOWS_TEST_PREFLIGHT_EXIT'] = [string]$PreflightExit
    if ($Mode -eq 'bat') {
        $dldStart.FileName = Join-Path $env:SystemRoot 'System32/cmd.exe'
        $dldArguments = @((Join-Path $dldCheckout 'tools/deploy-trial.bat')) + $Options
        $dldStart.Arguments = '/d /s /c "' + (($dldArguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ') + '"'
    } else {
        $dldStart.FileName = $dldWindowsPowerShell
        $dldArguments = @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            (Join-Path $dldCheckout 'tools/deploy-trial.ps1')) + $Options
        $dldStart.Arguments = ($dldArguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' '
    }
    $dldProcess = New-Object System.Diagnostics.Process
    $dldProcess.StartInfo = $dldStart
    try {
        [void]$dldProcess.Start()
        $dldOutputTask = $dldProcess.StandardOutput.ReadToEndAsync()
        $dldErrorTask = $dldProcess.StandardError.ReadToEndAsync()
        if (-not $dldProcess.WaitForExit(30000)) {
            $dldProcess.Kill()
            throw "Timed out running the offline $Mode launcher."
        }
        $dldCalls = New-Object 'System.Collections.Generic.List[object]'
        if (Test-Path -LiteralPath $dldLog) {
            foreach ($dldLine in [System.IO.File]::ReadAllLines($dldLog)) {
                $dldDecoded = @($dldLine.Split("`t") | ForEach-Object {
                    [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_))
                })
                $dldCalls.Add(@{ Tool = $dldDecoded[0]; Arguments = @($dldDecoded | Select-Object -Skip 1) })
            }
        }
        return @{
            ExitCode = $dldProcess.ExitCode
            Output = $dldOutputTask.Result
            Error = $dldErrorTask.Result
            Calls = $dldCalls.ToArray()
        }
    } finally {
        $dldProcess.Dispose()
    }
}

function Assert-Handover($Run, [string]$Mode, [string]$Bundle, [string]$HostName, [string]$Known = '', [switch]$NoFlashes) {
    Assert-Equal $Run.ExitCode 0 "$Mode successful exit ($($Run.Error))"
    Assert-Equal $Run.Calls.Count 5 "$Mode call count"
    Assert-Equal (($Run.Calls | ForEach-Object { $_.Tool }) -join ',') 'ssh,scp,scp,scp,ssh' "$Mode call sequence"
    foreach ($dldCall in $Run.Calls) {
        foreach ($dldOption in @('BatchMode=yes', 'ConnectTimeout=10', 'StrictHostKeyChecking=yes')) {
            Assert-Equal ($dldCall.Arguments -ccontains $dldOption) $true "$Mode strict SSH option $dldOption"
        }
        if ($Known) {
            Assert-Equal ($dldCall.Arguments -ccontains ('UserKnownHostsFile="' + $Known.Replace('\', '/') + '"')) $true "$Mode known-hosts quoting"
        }
    }
    Assert-Equal $Run.Calls[0].Arguments[-2] "root@$HostName" "$Mode SSH destination"
    $dldScpHost = $HostName
    if ($HostName.Contains(':')) { $dldScpHost = '[' + $HostName + ']' }
    $dldSources = @($Bundle, $dldPanel, (Join-Path $dldCheckout 'tools/trial-remote.py'))
    $dldDestinations = @('bundle.tar.gz', 'panel.json', 'trial-bootstrap.py')
    for ($dldIndex = 0; $dldIndex -lt 3; ++$dldIndex) {
        $dldCall = $Run.Calls[$dldIndex + 1]
        Assert-Equal ($dldCall.Arguments -ccontains '-O') $true "$Mode legacy SCP"
        Assert-Equal $dldCall.Arguments[-2] $dldSources[$dldIndex] "$Mode source path $dldIndex"
        Assert-Equal $dldCall.Arguments[-1] "root@${dldScpHost}:/run/dld-trial.aB3456/$($dldDestinations[$dldIndex])" "$Mode remote path $dldIndex"
    }
    $dldExpected = 'cd /run/dld-trial.aB3456 && python3 -B trial-bootstrap.py --bundle bundle.tar.gz --panel panel.json'
    if ($NoFlashes) { $dldExpected += ' --no-startup-flash --no-idle-flash' }
    Assert-Equal $Run.Calls[4].Arguments[-1] $dldExpected "$Mode bootstrap command"
}

try {
    $dldCheckout = Join-Path $dldTestDirectory 'checkout with spaces'
    $dldOtherDirectory = Join-Path $dldTestDirectory 'separate working directory'
    $dldFakeBin = Join-Path $dldTestDirectory 'fake commands'
    foreach ($dldDirectory in @($dldCheckout, $dldOtherDirectory, $dldFakeBin,
            (Join-Path $dldCheckout 'tools'), (Join-Path $dldCheckout 'build'), (Join-Path $dldCheckout 'config'))) {
        [void][System.IO.Directory]::CreateDirectory($dldDirectory)
    }
    foreach ($dldName in @('deploy-trial.bat', 'deploy-trial.ps1', 'trial-remote.py')) {
        Copy-Item -LiteralPath (Join-Path $dldRepository "tools/$dldName") -Destination (Join-Path $dldCheckout "tools/$dldName")
    }
    $dldDefaultBundle = Join-Path $dldCheckout 'build/dld-trial.tar.gz'
    $dldExplicitBundle = Join-Path $dldTestDirectory 'override bundle with spaces.tar.gz'
    $dldPanel = Join-Path $dldCheckout 'config/panel.json'
    $dldKnownHosts = Join-Path $dldTestDirectory 'known hosts with spaces'
    foreach ($dldFile in @($dldDefaultBundle, $dldExplicitBundle, $dldPanel, $dldKnownHosts)) {
        [System.IO.File]::WriteAllText($dldFile, "offline fixture`n")
    }
    $dldFakeSource = @'
using System;
using System.IO;
using System.Text;
public class DldOfflineCommand {
    public static int Main(string[] args) {
        string name = Path.GetFileNameWithoutExtension(Environment.GetCommandLineArgs()[0]);
        string[] record = new string[args.Length + 1];
        record[0] = name;
        Array.Copy(args, 0, record, 1, args.Length);
        for (int i = 0; i < record.Length; ++i)
            record[i] = Convert.ToBase64String(Encoding.UTF8.GetBytes(record[i]));
        File.AppendAllText(Environment.GetEnvironmentVariable("DLD_WINDOWS_TEST_LOG"),
            String.Join("\t", record) + "\n");
        if (name == "ssh" && args.Length > 0 && args[args.Length - 1].Contains("mktemp -d")) {
            int status = Int32.Parse(Environment.GetEnvironmentVariable("DLD_WINDOWS_TEST_PREFLIGHT_EXIT"));
            if (status != 0) return status;
            Console.WriteLine("/run/dld-trial.aB3456");
        }
        return 0;
    }
}
'@
    $dldFakeExe = Join-Path $dldFakeBin 'ssh.exe'
    Add-Type -TypeDefinition $dldFakeSource -OutputType ConsoleApplication -OutputAssembly $dldFakeExe
    Copy-Item -LiteralPath $dldFakeExe -Destination (Join-Path $dldFakeBin 'scp.exe')
    # This relative path is resolved from another directory, while the default
    # bundle must be resolved from the copied launcher, never the working directory.
    $dldRelativePanel = '../checkout with spaces/config/panel.json'
    foreach ($dldMode in @('bat', 'powershell')) {
        $dldRun = Invoke-Launcher $dldMode @('192.0.2.50', $dldRelativePanel)
        Assert-Handover $dldRun $dldMode $dldDefaultBundle '192.0.2.50'
        ++$dldCases

        $dldRun = Invoke-Launcher $dldMode @('2001:db8::50', $dldPanel, '-Bundle', $dldExplicitBundle,
            '-KnownHosts', $dldKnownHosts, '-NoStartupFlash', '-NoIdleFlash')
        Assert-Handover $dldRun $dldMode $dldExplicitBundle '2001:db8::50' $dldKnownHosts -NoFlashes
        ++$dldCases

        $dldRun = Invoke-Launcher $dldMode @('192.0.2.50', (Join-Path $dldCheckout 'config/missing.json'))
        Assert-Equal $dldRun.ExitCode 2 "$dldMode missing panel exit"
        Assert-Equal $dldRun.Calls.Count 0 "$dldMode missing panel must precede SSH"
        Assert-Equal ($dldRun.Error -match 'Missing local file:') $true "$dldMode missing panel diagnostic"
        ++$dldCases

        Remove-Item -LiteralPath $dldDefaultBundle
        try {
            $dldRun = Invoke-Launcher $dldMode @('192.0.2.50', $dldRelativePanel)
            Assert-Equal $dldRun.ExitCode 2 "$dldMode missing default bundle exit"
            Assert-Equal $dldRun.Calls.Count 0 "$dldMode missing bundle must precede SSH"
            Assert-Equal ($dldRun.Error -match 'Missing local file:') $true "$dldMode missing bundle diagnostic"
        } finally {
            [System.IO.File]::WriteAllText($dldDefaultBundle, "offline fixture`n")
        }
        ++$dldCases

        $dldRun = Invoke-Launcher $dldMode @('192.0.2.50', $dldRelativePanel) -PreflightExit 73
        Assert-Equal $dldRun.ExitCode 73 "$dldMode native exit propagation"
        Assert-Equal $dldRun.Calls.Count 1 "$dldMode failed preflight must prevent transfers"
        Assert-Equal $dldRun.Calls[0].Tool 'ssh' "$dldMode failed preflight command"
        ++$dldCases
    }
    Write-Output "Windows deployment launcher: $dldCases offline cases passed (Windows PowerShell $($PSVersionTable.PSVersion))."
} finally {
    # Only remove this newly created, direct child of the system temporary path.
    $dldCleanup = [System.IO.Path]::GetFullPath($dldTestDirectory).TrimEnd('\')
    if ([System.IO.Path]::GetDirectoryName($dldCleanup) -ine $dldTempParent -or
        -not [System.IO.Path]::GetFileName($dldCleanup).StartsWith('dld deploy windows ')) {
        throw "Refusing cleanup outside the owned test directory: $dldCleanup"
    }
    Remove-Item -LiteralPath $dldCleanup -Recurse -Force
}
