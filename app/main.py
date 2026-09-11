"""API local do assinador.

Servidor **local, single-user**: escuta em 127.0.0.1 e não fala com mais
ninguém. Não há autenticação porque não há superfície remota — quem alcança
a porta já está dentro da máquina.

Higiene de dados, por ordem de importância:

* A senha do PKCS#12 chega no corpo do POST (nunca em query string, que o
  servidor registraria em log), vive como ``bytearray`` e é sobrescrita com
  zeros assim que a assinatura termina.
* Nada é escrito em disco. Os bytes do PDF e do `.pfx` só existem em memória —
  por isso não há diretório de uploads para esquecer de limpar. O
  ``SpooledTemporaryFile`` que o Starlette usa para uploads grandes é fechado
  explicitamente no ``finally``.
* O log não registra corpo de requisição nem cabeçalho.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.errors import (
    FileTooLargeError,
    NoCertificateError,
    NoMarksError,
    PasswordNeededError,
    SigningError,
    TooManyMarksError,
    VazioError,
)
from app.validation import validate_pdf_bytes
from app.vault import CertificateVault
from app.signing import (
    DEFAULT_TSA_URL,
    TSA_IS_ICP,
    SIGNATURE_HEIGHT,
    SIGNATURE_WIDTH,
    SignatureBox,
    SignatureRequest,
    load_pkcs12_signer,
    sign_pdf,
)

logger = logging.getLogger("assinador")

STATIC_DIR = Path(__file__).parent / "static"
CERTS_DIR = Path(__file__).resolve().parent.parent / "certs"

#: Limite de upload. PDF acima disso quase sempre é engano.
MAX_PDF_MB = 50
MAX_PFX_MB = 5

#: Teto de assinaturas numa tacada. Cada uma é uma revisão do documento (e uma
#: ida à TSA, quando há carimbo): passar disso costuma ser engano.
MAX_MARKS = 20

#: Cofre de certificados: `~/.config/sign-manager/vault.enc`, cifrado.
vault = CertificateVault()


def downloads_dir() -> Path:
    """A pasta de downloads do usuário, que é onde o navegador salva."""
    try:
        saida = subprocess.run(
            ["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True, timeout=3
        )
        caminho = Path(saida.stdout.strip())
        if caminho.is_dir():
            return caminho
    except Exception:  # pragma: no cover - xdg-user-dir pode não existir
        pass
    return Path.home() / "Downloads"

app = FastAPI(
    title="Assinador local",
    docs_url=None,  # sem superfície extra: é um app, não uma API pública
    redoc_url=None,
    openapi_url=None,
)


@app.exception_handler(SigningError)
async def handle_signing_error(_request, exc: SigningError) -> JSONResponse:
    """Erro de negócio vira JSON com código estável — nunca um 500 opaco."""
    return JSONResponse(status_code=exc.http_status, content=exc.as_dict())


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health", include_in_schema=False)
async def health() -> dict[str, object]:
    """Usado pelo launcher para saber quando a janela pode abrir."""
    return {
        "ok": True,
        "tsa": DEFAULT_TSA_URL,
        "tsa_is_icp": TSA_IS_ICP,
        "certs_dir": str(CERTS_DIR),
        # o tamanho da marca é decisão do app; a interface só a posiciona
        "signature_size": {"width": SIGNATURE_WIDTH, "height": SIGNATURE_HEIGHT},
    }


@app.get("/api/certificates")
async def api_certificates() -> list[dict]:
    """Os certificados guardados, sempre mascarados."""
    return [c.as_public() for c in vault.list()]


@app.post("/api/certificates")
async def api_add_certificate(
    pfx: UploadFile = File(...),
    password: str = Form(...),
    store_password: bool = Form(False),
) -> dict:
    """Guarda um certificado. Só entra no cofre se abrir com a senha.

    `store_password` decide se a senha fica junto. Sem ela, o app pergunta a
    cada assinatura e a senha nunca toca o disco.
    """
    senha = bytearray(password.encode("utf-8"))
    try:
        pfx_bytes = await _read_upload(pfx, MAX_PFX_MB, "pfx")
        guardado = await run_in_threadpool(
            vault.add, pfx_bytes, bytes(senha), store_password
        )
    finally:
        for i in range(len(senha)):
            senha[i] = 0
        await pfx.close()
    return guardado.as_public()


@app.put("/api/certificates/{cert_id}/password")
async def api_store_password(cert_id: str, password: str = Form(...)) -> dict:
    """Passa a guardar a senha de um certificado que já está no cofre."""
    senha = bytearray(password.encode("utf-8"))
    try:
        guardado = await run_in_threadpool(vault.set_password, cert_id, bytes(senha))
    finally:
        for i in range(len(senha)):
            senha[i] = 0
    return guardado.as_public()


@app.delete("/api/certificates/{cert_id}/password")
async def api_forget_password(cert_id: str) -> dict:
    """Esquece a senha e mantém o certificado."""
    guardado = await run_in_threadpool(vault.forget_password, cert_id)
    return guardado.as_public()


@app.delete("/api/certificates/{cert_id}")
async def api_delete_certificate(cert_id: str) -> dict:
    await run_in_threadpool(vault.delete, cert_id)
    return {"deleted": cert_id}


@app.post("/api/reveal")
async def api_reveal(filename: str = Form(...), what: str = Form("folder")) -> dict:
    """Abre o arquivo assinado, ou a pasta dele, no ambiente do usuário.

    Só serve para o que o próprio app acabou de produzir: o caminho é montado
    aqui a partir da pasta de downloads e do nome do arquivo, e nada fora dela
    é aceito. Sem isso, um `filename` com `../` viraria um "abra qualquer coisa
    nesta máquina".
    """
    pasta = downloads_dir()
    alvo = (pasta / Path(filename).name).resolve()

    if alvo.parent != pasta.resolve() or alvo.suffix.lower() != ".pdf":
        raise FileNotFoundError(filename)
    if not alvo.exists():
        return {"opened": False, "reason": "not_found", "folder": str(pasta)}

    caminho = alvo if what == "file" else alvo.parent
    subprocess.Popen(
        ["xdg-open", str(caminho)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"opened": True, "path": str(caminho)}


@app.post("/api/validate")
async def api_validate(pdf: UploadFile = File(...)) -> dict:
    """Valida as assinaturas de um PDF, sem assinar nada."""
    try:
        pdf_bytes = await _read_upload(pdf, MAX_PDF_MB, "pdf")
        relatorios = await run_in_threadpool(validate_pdf_bytes, pdf_bytes, CERTS_DIR)
    finally:
        await pdf.close()
    return {
        "filename": Path(pdf.filename or "documento.pdf").name,
        "signatures": [r.as_dict() for r in relatorios],
    }


@app.post("/api/sign")
async def api_sign(
    pdf: UploadFile = File(...),
    marks: str = Form(...),
    pfx: UploadFile | None = File(None),
    password: str = Form(""),
    certificate_id: str = Form(""),
    remember: bool = Form(False),
    remember_password: bool = Form(False),
    reason: str = Form(""),
    location: str = Form(""),
    timestamp: bool = Form(False),
) -> Response:
    """Assina o PDF e devolve o documento assinado no corpo da resposta.

    `marks` é um JSON com uma entrada por lugar marcado — `page`, `x1`, `y1`,
    `x2`, `y2`. Marcar duas páginas produz **duas assinaturas**, aplicadas em
    sequência sobre o resultado da anterior: no PDF, um campo de assinatura
    vale para uma página só, então "assinar em três páginas" é literalmente
    assinar três vezes.

    As coordenadas chegam em **pontos PDF**, com origem no canto inferior
    esquerdo: a conversão a partir do canvas é feita no frontend, que é quem
    conhece o `scale` do pdf.js e a rotação da página (ver `static/coords.js`).
    """
    lugares = _parse_marks(marks)
    senha = bytearray(password.encode("utf-8"))
    try:
        pdf_bytes = await _read_upload(pdf, MAX_PDF_MB, "pdf")

        if certificate_id:
            # certificado do cofre: o arquivo não passa pela rede, e a senha só
            # passa quando não está guardada
            guardado = await run_in_threadpool(vault.get, certificate_id)
            pfx_bytes = guardado.pfx
            if guardado.has_password:
                senha = bytearray(guardado.password.encode("utf-8"))
            elif not senha:
                raise PasswordNeededError()
            elif remember_password:
                await run_in_threadpool(vault.set_password, certificate_id, bytes(senha))
        else:
            if pfx is None:
                raise NoCertificateError()
            pfx_bytes = await _read_upload(pfx, MAX_PFX_MB, "pfx")
            if remember:
                await run_in_threadpool(
                    vault.add, pfx_bytes, bytes(senha), remember_password
                )

        pedidos = [
            SignatureRequest(
                page=lugar["page"],
                box=SignatureBox(lugar["x1"], lugar["y1"], lugar["x2"], lugar["y2"]),
                reason=reason.strip() or None,
                location=location.strip() or None,
                timestamp=timestamp,
                # o documento passa a se chamar como o arquivo que o usuário recebe
                title=Path(_signed_name(pdf.filename)).stem,
            )
            for lugar in lugares
        ]

        # Assinar é bloqueante (CPU, e rede quando há carimbo do tempo) e o
        # pyHanko abre o próprio event loop para falar com a TSA: fora da
        # thread do servidor, portanto.
        result = await run_in_threadpool(
            _sign_blocking, pdf_bytes, pfx_bytes, bytes(senha), pedidos
        )
    finally:
        # A senha some da memória mesmo no caminho de exceção.
        for i in range(len(senha)):
            senha[i] = 0
        await pdf.close()
        if pfx is not None:
            await pfx.close()

    filename = _signed_name(pdf.filename)
    return Response(
        content=result.pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Signer-Name": _ascii_header(result.signer_name),
            "X-Already-Signed": "1" if result.already_signed else "0",
            "X-Signatures-Added": str(len(pedidos)),
            "X-Timestamped": "1" if result.timestamped else "0",
        },
    )


def _sign_blocking(
    pdf_bytes: bytes, pfx_bytes: bytes, senha: bytes, pedidos: list[SignatureRequest]
):
    """Roda fora do event loop. Toda exceção aqui já é uma SigningError tipada.

    Cada pedido assina por cima do resultado do anterior — é assim que um
    documento acaba com a mesma pessoa assinando em várias páginas.
    """
    signer = load_pkcs12_signer(pfx_bytes, senha, certs_dir=CERTS_DIR)
    resultado = None
    ja_tinha = False
    for indice, pedido in enumerate(pedidos):
        resultado = sign_pdf(pdf_bytes, signer, pedido, certs_dir=CERTS_DIR)
        pdf_bytes = resultado.pdf
        # só a primeira volta enxerga o documento como ele chegou; da segunda
        # em diante ele "já tem assinatura" por causa da nossa própria
        if indice == 0:
            ja_tinha = resultado.already_signed
    return replace(resultado, already_signed=ja_tinha)


def _parse_marks(bruto: str) -> list[dict]:
    """Lê o JSON das marcas, recusando o que não dá para assinar."""
    try:
        lugares = json.loads(bruto)
    except json.JSONDecodeError as exc:
        raise NoMarksError() from exc

    if not isinstance(lugares, list) or not lugares:
        raise NoMarksError()
    if len(lugares) > MAX_MARKS:
        raise TooManyMarksError(MAX_MARKS)

    campos = ("page", "x1", "y1", "x2", "y2")
    saida = []
    for lugar in lugares:
        if not isinstance(lugar, dict) or any(c not in lugar for c in campos):
            raise NoMarksError()
        try:
            saida.append(
                {
                    "page": int(lugar["page"]),
                    **{c: float(lugar[c]) for c in campos[1:]},
                }
            )
        except (TypeError, ValueError) as exc:
            raise NoMarksError() from exc
    return saida


async def _read_upload(upload: UploadFile, limit_mb: int, campo: str) -> bytes:
    data = await upload.read()
    if not data:
        raise VazioError(campo)
    if len(data) > limit_mb * 1024 * 1024:
        raise FileTooLargeError(limit_mb)
    return data


def _signed_name(original: str | None) -> str:
    base = Path(original or "documento.pdf").name
    if base.lower().endswith(".pdf"):
        base = base[:-4]
    return f"{base}-assinado.pdf"


def _ascii_header(value: str) -> str:
    """Cabeçalho HTTP é latin-1; nome com acento vira forma sem acento."""
    import unicodedata

    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii") or "Assinante"


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
