# Sobe o Assinador no Windows. Cria o ambiente virtual na primeira execução.
#
#     powershell -ExecutionPolicy Bypass -File run.ps1
#
# O servidor escuta apenas em 127.0.0.1, numa porta livre escolhida na hora, e
# a interface abre numa janela própria, usando o WebView2 que acompanha o Edge.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Criando ambiente virtual..."
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv venv .venv
        $env:VIRTUAL_ENV = ".venv"
        uv pip install -r requirements.txt
    } else {
        python -m venv .venv
        & .\.venv\Scripts\python.exe -m pip install --upgrade pip
        & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    }
}

& .\.venv\Scripts\pythonw.exe -m app.desktop @args
