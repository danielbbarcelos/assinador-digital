# Instala o Assinador no Windows, sem privilégios de administrador.
#
#     powershell -ExecutionPolicy Bypass -File install.ps1
#
# Cria um atalho no Menu Iniciar apontando para o run.ps1 deste diretório,
# então `git pull` já atualiza o app instalado. Nada é copiado para fora daqui.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$raiz = $PSScriptRoot

Write-Host "Instalando a partir de $raiz"

# ── ícone ──
# O Windows quer .ico no atalho. Converte o PNG se o .NET estiver disponível,
# e segue sem ícone próprio se não estiver: o atalho continua funcionando.
$png = Join-Path $raiz "app\static\icon.png"
$ico = Join-Path $raiz "app\static\icon.ico"
if ((Test-Path $png) -and (-not (Test-Path $ico))) {
    try {
        Add-Type -AssemblyName System.Drawing
        $bitmap = [System.Drawing.Bitmap]::FromFile($png)
        $handle = $bitmap.GetHicon()
        $icone = [System.Drawing.Icon]::FromHandle($handle)
        $arquivo = [System.IO.File]::Create($ico)
        $icone.Save($arquivo)
        $arquivo.Close()
        $bitmap.Dispose()
        Write-Host "-> ícone"
    } catch {
        Write-Host "-> sem ícone próprio (segue assim mesmo)"
    }
}

# ── atalho no Menu Iniciar ──
Write-Host "-> atalho no Menu Iniciar"
$menu = [Environment]::GetFolderPath("Programs")
$atalho = Join-Path $menu "Assinador.lnk"

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($atalho)
$link.TargetPath = "powershell.exe"
$link.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$raiz\run.ps1`""
$link.WorkingDirectory = $raiz
$link.Description = "Assina PDF com certificado ICP-Brasil, sem que o documento saia da máquina"
if (Test-Path $ico) { $link.IconLocation = $ico }
$link.Save()

Write-Host ""
Write-Host "Pronto. O Assinador está no Menu Iniciar."
Write-Host "Para abrir pelo terminal: .\run.ps1"
