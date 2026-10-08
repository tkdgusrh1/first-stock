# ===========================================================
#  First Stock: install Python for this Windows user only.
#  No admin rights. Nothing is added to PATH. Called by tools\python.cmd.
#  Saved as UTF-8 with BOM so Windows PowerShell 5.1 reads the Korean text.
# ===========================================================
param([Parameter(Mandatory = $true)][string]$Target)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # PowerShell 5.1 의 진행 막대는 내려받기를 몇 배 느리게 만든다
# 위에서부터 차례로 시도한다. 둘 다 python.org 에 윈도우 설치 파일이 있는 판이다.
$Versions = @('3.13.7', '3.12.10')
$Python = Join-Path $Target 'python.exe'

# 시작하기를 두 번 눌러도 설치는 한 번만 — 먼저 시작한 쪽이 끝날 때까지 기다린다.
$mutex = New-Object System.Threading.Mutex($false, 'Local\FirstStockPythonInstall')
try {
    if (-not $mutex.WaitOne(0)) {
        Write-Host '  다른 창에서 파이썬을 설치하는 중입니다. 끝날 때까지 기다립니다...'
        $null = $mutex.WaitOne()
    }
} catch { }    # 앞 창이 설치 도중 닫혔으면(버려진 잠금) 그냥 이어서 한다
if (Test-Path $Python) { exit 0 }

$arch = $env:PROCESSOR_ARCHITECTURE
if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }

Write-Host ''
Write-Host '  파이썬이 없어서 지금 설치합니다. (처음 한 번만, 2~5분)'
Write-Host '  이 컴퓨터의 내 계정에만 설치됩니다. 관리자 권한은 필요 없습니다.'
Write-Host ''

try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$exe = $null
foreach ($Version in $Versions) {
    switch ($arch) {
        'AMD64' { $file = "python-$Version-amd64.exe" }
        'ARM64' { $file = "python-$Version-arm64.exe" }
        default { $file = "python-$Version.exe" }
    }
    $url = "https://www.python.org/ftp/python/$Version/$file"
    # 실행할 때마다 다른 이름 — 두 창이 같은 파일을 덮어쓰거나 지우지 않게
    $candidate = Join-Path $env:TEMP ('FirstStock-' + [guid]::NewGuid().ToString('N') + '-' + $file)
    Write-Host "  받는 곳: $url"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $candidate
    } catch {
        Remove-Item $candidate -ErrorAction SilentlyContinue
        try { & curl.exe -fsL -o $candidate $url } catch { }
    }
    if ((Test-Path $candidate) -and (Get-Item $candidate).Length -gt 1MB) { $exe = $candidate; break }
    Remove-Item $candidate -ErrorAction SilentlyContinue
}
if (-not $exe) {
    Write-Host '  [!] 파이썬을 내려받지 못했습니다. 인터넷 연결을 확인한 뒤 다시 실행해 주세요.'
    exit 2
}

# 정말 python.org 의 파일인지 — 파이썬 재단의 전자서명을 확인한다.
$sig = Get-AuthenticodeSignature -FilePath $exe
if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch '^CN=Python Software Foundation,') {
    Write-Host '  [!] 받은 파일이 손상됐거나 파이썬 재단의 서명이 아니어서 설치하지 않았습니다.'
    Remove-Item $exe -ErrorAction SilentlyContinue
    exit 3
}

Write-Host '  설치하는 중... (따로 창이 뜨지 않습니다. 잠시 기다려 주세요)'
$options = @(
    '/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'Include_launcher=0',
    'Include_test=0', 'Include_doc=0', 'Include_tcltk=0', 'Shortcuts=0',
    'AssociateFiles=0', "TargetDir=`"$Target`""
)
$process = Start-Process -FilePath $exe -ArgumentList $options -Wait -PassThru
Remove-Item $exe -ErrorAction SilentlyContinue

if (-not (Test-Path $Python)) {
    Write-Host "  [!] 파이썬을 설치하지 못했습니다. (설치 프로그램 종료 코드 $($process.ExitCode))"
    exit 4
}
Write-Host '  파이썬 설치를 마쳤습니다. 이어서 준비합니다.'
Write-Host ''
exit 0
