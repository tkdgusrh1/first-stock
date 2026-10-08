# ===========================================================
#  First Stock: install Python for this Windows user only.
#  No admin rights. Nothing is added to PATH. Called by tools\python.cmd.
#  Saved as UTF-8 with BOM so Windows PowerShell 5.1 reads the Korean text.
# ===========================================================
param([Parameter(Mandatory = $true)][string]$Target)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # PowerShell 5.1 의 진행 막대는 내려받기를 몇 배 느리게 만든다
$Version = '3.12.10'

$arch = $env:PROCESSOR_ARCHITECTURE
if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
switch ($arch) {
    'AMD64' { $file = "python-$Version-amd64.exe" }
    'ARM64' { $file = "python-$Version-arm64.exe" }
    default { $file = "python-$Version.exe" }
}
$url = "https://www.python.org/ftp/python/$Version/$file"
$exe = Join-Path $env:TEMP $file

Write-Host ''
Write-Host '  파이썬이 없어서 지금 설치합니다. (처음 한 번만, 2~5분)'
Write-Host '  이 컴퓨터의 내 계정에만 설치됩니다. 관리자 권한은 필요 없습니다.'
Write-Host "  받는 곳: $url"
Write-Host ''

Remove-Item $exe -ErrorAction SilentlyContinue
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $exe
} catch {
    Remove-Item $exe -ErrorAction SilentlyContinue
    try { & curl.exe -fsSL -o $exe $url } catch { }
}
if (-not (Test-Path $exe)) {
    Write-Host '  [!] 파이썬을 내려받지 못했습니다. 인터넷 연결을 확인한 뒤 다시 실행해 주세요.'
    exit 2
}

# 정말 python.org 의 파일인지 — 파이썬 재단의 전자서명을 확인한다.
$sig = Get-AuthenticodeSignature -FilePath $exe
if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
    Write-Host '  [!] 받은 파일의 서명이 파이썬 재단 것이 아니어서 설치하지 않았습니다.'
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

if (-not (Test-Path (Join-Path $Target 'python.exe'))) {
    Write-Host "  [!] 파이썬을 설치하지 못했습니다. (설치 프로그램 종료 코드 $($process.ExitCode))"
    exit 4
}
Write-Host '  파이썬 설치를 마쳤습니다. 이어서 준비합니다.'
Write-Host ''
exit 0
