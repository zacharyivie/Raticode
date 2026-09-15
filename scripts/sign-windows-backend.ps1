$ErrorActionPreference = "Stop"
if (-not $env:CSC_LINK -or -not $env:CSC_KEY_PASSWORD) {
    throw "Release requires the Windows code-signing certificate and password."
}
$certificatePath = Join-Path $env:RUNNER_TEMP "raticode-signing.pfx"
try {
    [IO.File]::WriteAllBytes($certificatePath, [Convert]::FromBase64String($env:CSC_LINK))
    $password = ConvertTo-SecureString $env:CSC_KEY_PASSWORD -AsPlainText -Force
    $certificate = Get-PfxCertificate -FilePath $certificatePath -Password $password
    $signature = Set-AuthenticodeSignature -FilePath dist/gof.exe -Certificate $certificate -HashAlgorithm SHA256 -TimestampServer "http://timestamp.digicert.com"
    if ($signature.Status -ne "Valid") { throw "Backend signing failed: $($signature.Status)" }
} finally {
    Remove-Item $certificatePath -Force -ErrorAction SilentlyContinue
}
