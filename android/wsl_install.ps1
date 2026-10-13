# =============================================================================
# SIPip: instalacion de WSL2 + Ubuntu y compilacion del APK en python-for-android
#
# Uso (PowerShell COMO ADMINISTRADOR):
#  1) La PRIMERA vez instala WSL2+Ubuntu; al terminar pide REINICIAR el PC.
#  2) Tras reiniciar, vuelve a ejecutar este mismo script: lanza la compilacion.
#
# Requisitos: Windows 10 21H2+ / Windows 11, administrador, internet,
#             ~10 GB libres en disco y 45-90 min la primera compilacion.
# =============================================================================

#requires -RunAsAdministrator
#requires -Version 5.1
$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$buildScript = Join-Path $PSScriptRoot 'ubuntu_build.sh'

Write-Host '=============================================================='
Write-Host '  SIPip - WSL2 + Ubuntu + python-for-android (APK)'
Write-Host '=============================================================='

if (-not (Test-Path $buildScript)) {
    throw "No encuentro ubuntu_build.sh en $PSScriptRoot"
}

# ---- 1) WSL: kernel actualizado ------------------------------------------
Write-Host '`n[1/4] Actualizando WSL...'
try { wsl --update 2>&1 | Out-Null } catch { }

# ---- 2) Comprobar si existe una distro Ubuntu ----------------------------
$haveDistro = $false
try {
    $lines = wsl -l -q 2>$null
    foreach ($line in $lines) {
        if ($line -match 'Ubuntu') { $haveDistro = $true }
    }
} catch { }

if (-not $haveDistro) {
    Write-Host '[2/4] Instalando WSL2 + Ubuntu (primera vez)...'
    wsl --install -d Ubuntu --no-launch
    if ($LASTEXITCODE -ne 0) {
        throw "wsl --install fallo. Comprueba que la virtualizacion esta activa en la BIOS (VT-x/AMD-V)."
    }
    Write-Host ''
    Write-Host '**********************************************************'
    Write-Host '  WSL2 y Ubuntu instalados. REINICIA la maquina y luego'
    Write-Host "  vuelve a ejecutar este script:  $($MyInvocation.Line)" 
    Write-Host '**********************************************************'
    exit 0
}

Write-Host '[2/4] CentOS -> OK, encontrada distro Ubuntu.'

# ---- 3) Default user root para que el build no pida contrasena ------------
Write-Host '[3/4] Fijando usuario default "root" en Ubuntu...'
wsl -d Ubuntu -u root -e bash -lc "printf '[user]\ndefault=root\n' > /etc/wsl.conf"
if ($LASTEXITCODE -ne 0) {
    throw 'No se pudo escribir /etc/wsl.conf. Prueba a hacer "wsl" una vez para completar la primera configuracion.'
}

# ---- 4) Lanzar provisionado + compilacion ---------------------------------
Write-Host "[4/4] Lanzando compilacion dentro de Ubuntu (apt + buildozer + p4a)."
Write-Host "     Esto descarga el Android SDK/NDK y tarda bastante. Ten paciencia..."
wsl -d Ubuntu -u root -e bash "/mnt/d/SIPip/android/ubuntu_build.sh"
if ($LASTEXITCODE -ne 0) {
    throw 'La compilacion fallo. Revisa el log de arriba.'
}

Write-Host ''
Write-Host "== APK generado en: $repo\dist\android\ =="