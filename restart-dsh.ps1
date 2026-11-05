# dsh-tan: restart DSH so memory_save / memory_search tools take effect.
# All ASCII on purpose: PowerShell 5.1 reads BOM-less UTF-8 as ANSI and mangles CJK,
# which broke the previous version (unterminated string).

$ErrorActionPreference = "Continue"
$log  = "D:\dsh-tan\restart.log"
$exe  = "C:\Users\admin\AppData\Local\Programs\DSH NEXT\DSH NEXT.exe"
$work = "C:\Users\admin\AppData\Local\Programs\DSH NEXT"
$js   = "D:\dsh-tan\plugin\lib\index.js"

function L($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg" | Out-File -Append -Encoding ascii $log
}

L "===== restart begin ====="

Start-Sleep -Seconds 12

$procs = Get-Process -Name "DSH NEXT" -ErrorAction SilentlyContinue
if ($procs) {
    L "killing $($procs.Count) processes"
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 4
}
$left = Get-Process -Name "DSH NEXT" -ErrorAction SilentlyContinue
if ($left) {
    L "second pass kill $($left.Count)"
    $left | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}
L "all stopped"

try {
    Start-Process -FilePath $exe -WorkingDirectory $work
    L "launched"
} catch {
    L "launch failed: $_"
}

Start-Sleep -Seconds 40
$p = Get-Process -Name "DSH NEXT" -ErrorAction SilentlyContinue
if (-not $p) {
    L "GUARD: DSH did not start, disabling memory tools"
    try {
        $c = Get-Content $js -Raw
        $c = $c -replace 'const disposeTools = registerMemoryTools\(ctx, base\);', 'const disposeTools = null; /* disabled by restart guard */'
        Set-Content -Path $js -Value $c -NoNewline
        L "GUARD: index.js patched"
    } catch {
        L "GUARD: patch failed: $_"
    }
    try {
        Start-Process -FilePath $exe -WorkingDirectory $work
        Start-Sleep -Seconds 35
        $p2 = Get-Process -Name "DSH NEXT" -ErrorAction SilentlyContinue
        if ($p2) { L "GUARD OK: running without memory tools" }
        else     { L "CRITICAL: still down, manual start needed" }
    } catch {
        L "GUARD launch failed: $_"
    }
} else {
    L "OK: DSH running, $($p.Count) processes"
}

Start-Sleep -Seconds 10
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:18765/health" -TimeoutSec 5 -UseBasicParsing
    L "memory service: $($r.StatusCode)"
} catch {
    L "memory service: no response (plugin will spawn it on next turn)"
}

L "===== restart end ====="
