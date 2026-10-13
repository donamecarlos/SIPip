$out = 'D:\SIPip\android\build.out'
$err = 'D:\SIPip\android\build.err'
Remove-Item $out, $err -ErrorAction SilentlyContinue
$p = Start-Process -FilePath wsl.exe `
    -ArgumentList '-d', 'Ubuntu', '-u', 'root', '-e', 'bash', '/mnt/d/SIPip/android/ubuntu_build.sh' `
    -WindowStyle Hidden `
    -RedirectStandardOutput $out `
    -RedirectStandardError $err `
    -PassThru
"PID=" + $p.Id | Out-File -FilePath 'D:\SIPip\android\build.pid' -Encoding utf8
Write-Output ("LAUNCHED PID=" + $p.Id)