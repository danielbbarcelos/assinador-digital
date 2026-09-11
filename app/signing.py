"""Assinatura de PDF com pyHanko — isolado de HTTP.

Este módulo não importa nada de FastAPI de propósito: dá para usá-lo num
script, num teste ou numa futura CLI sem subir servidor nenhum.

Contrato de coordenadas
-----------------------
``SignatureBox`` está sempre em **pontos PDF** (1/72"), com origem no canto
**inferior esquerdo** da página e já considerando a rotação (``/Rotate``) do
documento. A conversão a partir de pixels de tela acontece no **frontend**
(``static/coords.js``), porque é lá que existem o ``scale`` do pdf.js, o
``devicePixelRatio`` e o tamanho CSS do canvas. O backend confia nos pontos
recebidos e apenas normaliza/valida.

Extensão para A3 (token PKCS#11)
--------------------------------
A assinatura depende apenas de um ``Signer`` do pyHanko. ``load_pkcs12_signer``
produz um a partir de um arquivo A1; um futuro ``load_pkcs11_signer`` devolveria
outro a partir de um token, e ``sign_pdf`` não muda uma linha.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from asn1crypto import x509 as asn1_x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import pkcs12
from pyhanko.pdf_utils.content import PdfContent
from pyhanko.pdf_utils.images import PdfImage
from pyhanko.pdf_utils.font.basic import SimpleFontEngineFactory
from pyhanko.pdf_utils.font.opentype import GlyphAccumulatorFactory
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.layout import (
    AxisAlignment,
    BoxConstraints,
    InnerScaling,
    Margins,
    SimpleBoxLayoutRule,
)
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.pdf_utils.text import TextBoxStyle
from pyhanko.sign import signers, timestamps
from pyhanko.sign.fields import SigFieldSpec, SigSeedSubFilter, enumerate_sig_fields
from pyhanko.stamp import TextStampStyle
from pyhanko_certvalidator import ValidationContext

from app.platform_support import stamp_font
from app.errors import (
    CertificateExpiredError,
    CertificateNotYetValidError,
    ChainIncompleteError,
    EncryptedPdfError,
    InvalidBoxError,
    InvalidCertificateError,
    InvalidPdfError,
    LegacyCryptoError,
    NoInternetError,
    PageOutOfRangeError,
    TimestampError,
    WrongPasswordError,
)

logger = logging.getLogger(__name__)

#: TSA padrão, configurável por SIGN_TSA_URL.
#:
#: Atenção ao que este carimbo vale no Brasil. A ICP-Brasil só reconhece
#: carimbo emitido por uma **ACT credenciada**, e as ACTs brasileiras são
#: serviços pagos, com credencial — nenhuma responde publicamente. A DigiCert
#: responde, é criptograficamente impecável e **o validador oficial do ITI a
#: reprova**: "carimbo de tempo reprovado". Pior, sem âncora temporal aceita
#: ele também não consegue fixar o momento da assinatura, e o resultado é
#: "assinatura indeterminada" — inclusive para assinaturas de terceiros que já
#: estavam no documento.
#:
#: Por isso o carimbo é **opcional e vem desligado**: sem uma ACT da ICP-Brasil
#: configurada aqui, uma assinatura sem carimbo vale mais do que uma com.
DEFAULT_TSA_URL = os.environ.get("SIGN_TSA_URL", "http://timestamp.digicert.com")

#: A TSA padrão é credenciada na ICP-Brasil? Só deixa de ser falso quando o
#: usuário aponta SIGN_TSA_URL para uma ACT de verdade.
TSA_IS_ICP = bool(os.environ.get("SIGN_TSA_IS_ICP"))

#: Âncoras de confiança do sistema (pacote ca-certificates do Ubuntu).
SYSTEM_TRUST_ROOTS = Path("/etc/ssl/certs/ca-certificates.crt")

PDF_MAGIC = b"%PDF-"

#: Tamanho da marca de assinatura, em pontos. É o app que decide: o usuário só
#: escolhe *onde* ela entra. Dimensionado para o carimbo caber inteiro, com o
#: nome e o CPF em corpo legível — a conta está em `_fit_font_size`.
SIGNATURE_WIDTH = 235
SIGNATURE_HEIGHT = 88

#: A logo do app, usada como marca d'água do carimbo.
STAMP_LOGO = Path(__file__).parent / "static" / "icon.png"

#: Quase transparente: precisa se ver que está lá sem disputar com o texto.
STAMP_LOGO_OPACITY = 0.13


# ---------------------------------------------------------------------------
# Tipos de entrada
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignatureBox:
    """Retângulo da assinatura, em pontos PDF (origem inferior esquerda)."""

    x1: float
    y1: float
    x2: float
    y2: float

    def normalized(self) -> "SignatureBox":
        """Garante x1 < x2 e y1 < y2, qualquer que tenha sido o arrasto."""
        x1, x2 = sorted((self.x1, self.x2))
        y1, y2 = sorted((self.y1, self.y2))
        return SignatureBox(x1, y1, x2, y2)

    def validate(self) -> "SignatureBox":
        box = self.normalized()
        if not all(map(_finite, (box.x1, box.y1, box.x2, box.y2))):
            raise InvalidBoxError()
        # 20x10pt é o mínimo em que um carimbo de texto ainda diz alguma coisa.
        if (box.x2 - box.x1) < 20 or (box.y2 - box.y1) < 10:
            raise InvalidBoxError(
                "Área da assinatura pequena demais — desenhe um retângulo maior."
            )
        return box

    def as_tuple(self) -> tuple[int, int, int, int]:
        b = self.normalized()
        return (round(b.x1), round(b.y1), round(b.x2), round(b.y2))


@dataclass(frozen=True)
class SignatureRequest:
    """Tudo que descreve *como* assinar, menos a chave e o documento."""

    page: int  # 1-based, como o usuário vê
    box: SignatureBox
    reason: str | None = None
    location: str | None = None
    timestamp: bool = False
    tsa_url: str = DEFAULT_TSA_URL
    #: Título a gravar nos metadados. PDF que saiu do Word costuma trazer
    #: "Microsoft Word - algo" ali, que é o que o leitor mostra na aba — pior
    #: que o nome do próprio arquivo. Escrito na revisão da assinatura, o que
    #: não invalida assinaturas anteriores (entra como FORM_FILLING).
    title: str | None = None


@dataclass(frozen=True)
class SignResult:
    pdf: bytes
    signer_name: str
    already_signed: bool  #: o documento já tinha assinatura antes desta
    timestamped: bool


def _finite(v: float) -> bool:
    return v == v and v not in (float("inf"), float("-inf"))


# ---------------------------------------------------------------------------
# Certificado (A1)
# ---------------------------------------------------------------------------


def _load_extra_certs(certs_dir: Path | None) -> list[asn1_x509.Certificate]:
    """Lê PEM/DER extras (ACs intermediárias da ICP-Brasil) de um diretório."""
    if certs_dir is None or not certs_dir.is_dir():
        return []
    out: list[asn1_x509.Certificate] = []
    for path in sorted(certs_dir.iterdir()):
        if path.suffix.lower() not in {".pem", ".crt", ".cer", ".der"}:
            continue
        try:
            from pyhanko.keys import load_certs_from_pemder_data

            out.extend(load_certs_from_pemder_data(path.read_bytes()))
        except Exception as exc:  # um arquivo ruim não derruba o resto
            logger.warning("Ignorando certificado extra %s: %s", path.name, exc)
    return out


def load_pkcs12_signer(
    pfx_bytes: bytes,
    password: bytes,
    certs_dir: Path | None = None,
) -> signers.SimpleSigner:
    """Carrega um `.pfx`/`.p12` e devolve um Signer do pyHanko.

    A senha só existe como parâmetro e como variável local: não é registrada em
    log, não vai para exceção e não encosta no disco.
    """
    try:
        pkcs12.load_key_and_certificates(pfx_bytes, password)
    except UnsupportedAlgorithm as exc:
        raise LegacyCryptoError() from exc
    except ValueError as exc:
        raise _classify_pkcs12_failure(exc) from exc

    signer = signers.SimpleSigner.load_pkcs12_data(
        pfx_bytes,
        other_certs=_load_extra_certs(certs_dir),
        passphrase=password,
    )
    if signer is None:  # pragma: no cover - defensivo
        raise InvalidCertificateError()

    _assert_certificate_is_current(signer.signing_cert)
    return signer


def _classify_pkcs12_failure(exc: ValueError):
    """Traduz o ValueError genérico do `cryptography` em erro específico."""
    text = str(exc).lower()
    if "unsupported" in text or "legacy" in text or "rc2" in text:
        return LegacyCryptoError()
    if "deserialize" in text or "parse" in text:
        return InvalidCertificateError()
    # "Invalid password or PKCS12 data": o arquivo abriu, a senha é que não bate.
    if "password" in text:
        return WrongPasswordError()
    return InvalidCertificateError()


def _assert_certificate_is_current(cert: asn1_x509.Certificate) -> None:
    validity = cert["tbs_certificate"]["validity"]
    not_before: datetime = validity["not_before"].native
    not_after: datetime = validity["not_after"].native
    now = datetime.now(timezone.utc)
    if now < not_before:
        raise CertificateNotYetValidError(not_before)
    if now > not_after:
        raise CertificateExpiredError(not_after)


def certificate_common_name(cert: asn1_x509.Certificate) -> str:
    """CN do titular. Em certificado ICP-Brasil vem como `NOME:CPF`."""
    cn = cert.subject.native.get("common_name") or ""
    return cn.split(":")[0].strip() or cn or "Assinante"


def certificate_document_id(cert: asn1_x509.Certificate) -> str | None:
    """CPF do titular, formatado, quando o certificado o traz.

    Certificado ICP-Brasil de pessoa física põe o CPF no próprio CN, depois do
    nome: `FULANA DE TAL:12345678901`. Pessoa jurídica traz o CNPJ (14
    dígitos), formatado aqui do mesmo jeito.
    """
    cn = cert.subject.native.get("common_name") or ""
    _, _, sufixo = cn.partition(":")
    digitos = "".join(c for c in sufixo if c.isdigit())
    if len(digitos) == 11:
        return f"CPF {digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}"
    if len(digitos) == 14:
        return f"CNPJ {digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"
    return None


# ---------------------------------------------------------------------------
# Documento
# ---------------------------------------------------------------------------


def open_pdf(pdf_bytes: bytes) -> tuple[IncrementalPdfFileWriter, int, bool]:
    """Abre o PDF para escrita incremental.

    Devolve (writer, número de páginas, já_assinado). Valida por magic bytes,
    não por extensão.
    """
    if not pdf_bytes.startswith(PDF_MAGIC):
        raise InvalidPdfError()
    try:
        reader = PdfFileReader(BytesIO(pdf_bytes), strict=False)
        if reader.encrypted:
            raise EncryptedPdfError()
        page_count = reader.root["/Pages"]["/Count"]
        already_signed = any(True for _ in enumerate_sig_fields(reader, filled_status=True))
        writer = IncrementalPdfFileWriter(BytesIO(pdf_bytes), strict=False)
    except (EncryptedPdfError, InvalidPdfError):
        raise
    except Exception as exc:
        if "encrypt" in str(exc).lower():
            raise EncryptedPdfError() from exc
        raise InvalidPdfError() from exc
    return writer, int(page_count), already_signed


def _unique_field_name(writer: IncrementalPdfFileWriter) -> str:
    taken = {name for name, *_ in enumerate_sig_fields(writer.prev)}
    n = 1
    while f"Assinatura{n}" in taken:
        n += 1
    return f"Assinatura{n}"


# ---------------------------------------------------------------------------
# Aparência
# ---------------------------------------------------------------------------


#: Corpo máximo do texto do carimbo, em pontos. O tamanho real é calculado
#: para caber na caixa que o usuário desenhou (ver `_fit_font_size`).
#:
#: Inteiros porque o pyHanko monta a caixa do texto com `fractions.Fraction`,
#: que recusa float.
STAMP_FONT_SIZE_MAX = 11
STAMP_FONT_SIZE_MIN = 5

#: A fonte do carimbo, escolhida entre as instaladas no sistema. Qual é e por
#: que o critério é o `unitsPerEm`: ver `platform_support.stamp_font`.
STAMP_FONT = stamp_font()

#: Largura média do caractere, em fração do corpo da fonte. Na Helvetica o
#: texto corrido fica perto de 0.5; no Courier, que é monoespaçado, é exatamente
#: 0.6. Serve para estimar se a linha mais longa cabe na caixa.
CHAR_WIDTH_SANS = 0.52
CHAR_WIDTH_COURIER = 0.6

#: Distância entre as linhas, somada ao corpo da fonte.
LEADING_EXTRA = 2

#: Folga interna da caixa, em pontos — valor *pedido* ao pyHanko.
#:
#: O que chega ao papel é menos: o stamp é composto numa caixa própria e
#: depois ajustado ao retângulo da anotação, e a margem encolhe junto. Este
#: número também é o que `_fit_font_size` usa para decidir se o texto cabe.
STAMP_PADDING_X = 10
STAMP_PADDING_Y = 6


def _stamp_font():
    """A fonte do carimbo e a largura média que ela implica."""
    if STAMP_FONT is not None and STAMP_FONT.exists():
        return GlyphAccumulatorFactory, CHAR_WIDTH_SANS
    return None, CHAR_WIDTH_COURIER


def _fit_font_size(lines: list[str], box: SignatureBox) -> int:
    """Maior corpo de fonte em que o carimbo inteiro cabe na caixa desenhada.

    Limitado pela largura (a linha mais longa) e pela altura (número de linhas,
    contando a entrelinha). Nunca passa de ``STAMP_FONT_SIZE_MAX`` nem desce
    abaixo de ``STAMP_FONT_SIZE_MIN`` — caixa pequena demais é recusada antes,
    em `SignatureBox.validate`.
    """
    _, char_width = _stamp_font()
    # o selo ocupa a direita da caixa; o texto não pode invadi-lo
    largura = (box.x2 - box.x1) - 2 * STAMP_PADDING_X - _seal_width(box)
    altura = (box.y2 - box.y1) - 2 * STAMP_PADDING_Y
    maior = max((len(linha) for linha in lines), default=1) or 1

    por_largura = largura / (maior * char_width)
    # Medido no papel: as linhas ficam a `size + LEADING_EXTRA` uma da outra, e
    # o bloco reserva mais uma entrelinha acima da primeira. Por isso o divisor
    # é `n + 1`: ignorar essa linha fantasma fazia o texto subir para fora da
    # moldura nas caixas baixas.
    por_altura = altura / (len(lines) + 1) - LEADING_EXTRA

    size = int(min(STAMP_FONT_SIZE_MAX, por_largura, por_altura))
    return max(STAMP_FONT_SIZE_MIN, size)


def _seal_width(box: SignatureBox) -> float:
    """Quanto o selo tira do texto: nada.

    O selo é marca d'água — fica *atrás* do texto, em amarelo quase
    transparente. Antes ele ocupava uma faixa à direita, e era ela que obrigava
    a caixa a ser larga. Mantido como função porque o cálculo do corpo da fonte
    pergunta por ele, e porque um dia pode voltar a ocupar espaço.
    """
    return 0.0


class _StampBackground(PdfContent):
    """A logo ao fundo do carimbo, como marca d'água.

    **Sem moldura.** O tracejado existe só na tela, para mostrar onde a
    assinatura vai cair enquanto se escolhe o lugar. No documento ele viraria
    uma caixa desenhada por cima do papel, e documento não tem caixa: tem
    assinatura. O que fica aqui é a logo, quase transparente, atrás do texto.
    """

    def __init__(self, box: SignatureBox):
        largura = box.x2 - box.x1
        altura = box.y2 - box.y1
        super().__init__(box=BoxConstraints(width=largura, height=altura))
        self._w = largura
        self._h = altura
        self._logo = _load_logo(altura)

    def set_writer(self, writer):
        """A logo precisa do mesmo writer para registrar o XObject da imagem."""
        super().set_writer(writer)
        if self._logo is not None:
            self._logo.set_writer(writer)

    def render(self) -> bytes:
        return self._render_logo() or b""

    def _render_logo(self) -> bytes | None:
        """A logo encostada à direita, centrada na altura."""
        if self._logo is None:
            return None
        lado = self._logo.box.width
        x = self._w - lado - 10
        y = (self._h - lado) / 2

        desenho = self._logo.render()
        # os recursos da imagem (XObject e o ExtGState da transparência)
        # precisam entrar no dicionário deste conteúdo
        self.import_resources(self._logo.resources)
        return b"q 1 0 0 1 %.2f %.2f cm\n%s\nQ" % (x, y, desenho)


def _load_logo(altura_caixa: float) -> PdfImage | None:
    """A logo dimensionada para a caixa, ou None se o arquivo não estiver lá."""
    if not STAMP_LOGO.exists():  # pragma: no cover - só falta em instalação torta
        return None
    try:
        from PIL import Image

        lado = min(altura_caixa * 0.58, 36.0)
        return PdfImage(
            Image.open(STAMP_LOGO),
            opacity=STAMP_LOGO_OPACITY,
            box=BoxConstraints(width=lado, height=lado),
        )
    except Exception as exc:  # pragma: no cover - Pillow ausente ou imagem ruim
        logger.warning("Sem marca d'água no carimbo: %s", exc)
        return None


#: Constante de Bézier para aproximar um quarto de círculo.
_K = 0.5523


def _circle(cx: float, cy: float, r: float) -> bytes:
    k = r * _K
    return b"\n".join([
        b"%.2f %.2f m" % (cx + r, cy),
        b"%.2f %.2f %.2f %.2f %.2f %.2f c" % (cx + r, cy + k, cx + k, cy + r, cx, cy + r),
        b"%.2f %.2f %.2f %.2f %.2f %.2f c" % (cx - k, cy + r, cx - r, cy + k, cx - r, cy),
        b"%.2f %.2f %.2f %.2f %.2f %.2f c" % (cx - r, cy - k, cx - k, cy - r, cx, cy - r),
        b"%.2f %.2f %.2f %.2f %.2f %.2f c" % (cx + k, cy - r, cx + r, cy - k, cx + r, cy),
    ])


def _stamp_lines(name: str, documento: str | None, req: SignatureRequest) -> list[str]:
    """O que o carimbo diz, uma linha por vez.

    `%(ts)s` é trocado pelo pyHanko na hora de assinar, no formato de
    `TextStampStyle.timestamp_format`.
    """
    linhas = ["Assinado digitalmente por", name]
    if documento:
        linhas.append(documento)
    linhas.append("%(ts)s")
    if req.reason:
        linhas.append(f"Motivo: {req.reason}")
    if req.location:
        linhas.append(f"Local: {req.location}")
    return linhas


#: O que sai primeiro quando a caixa é pequena demais, nesta ordem. Nome e
#: data nunca saem: sem eles o carimbo não diz quem assinou nem quando.
PODA = ("Local: ", "Motivo: ", "CPF ", "CNPJ ", "Assinado digitalmente por")


def _fit_lines(lines: list[str], box: SignatureBox) -> list[str]:
    """Corta o que não couber, do menos essencial para o mais.

    Melhor um carimbo curto e legível do que um carimbo completo derramando
    para fora da moldura.
    """
    altura = (box.y2 - box.y1) - 2 * STAMP_PADDING_Y
    minima = STAMP_FONT_SIZE_MIN + LEADING_EXTRA
    lines = list(lines)

    # (n + 1) pelo mesmo motivo de `_fit_font_size`: a entrelinha fantasma.
    while len(lines) > 2 and (len(lines) + 1) * minima > altura:
        for prefixo in PODA:
            achado = next((l for l in lines if l.startswith(prefixo)), None)
            if achado is not None:
                lines.remove(achado)
                break
        else:
            break  # só restou o essencial
    return lines


def _stamp_style(lines: list[str], box: SignatureBox) -> TextStampStyle:
    lines = _fit_lines(lines, box)
    size = _fit_font_size(lines, box)
    fabrica, _ = _stamp_font()
    font = (
        fabrica(str(STAMP_FONT), font_size=size)
        if fabrica
        else SimpleFontEngineFactory.default_factory()
    )
    return TextStampStyle(
        stamp_text="\n".join(lines),
        text_box_style=TextBoxStyle(font=font, font_size=size, leading=size + LEADING_EXTRA),
        timestamp_format="%d/%m/%Y às %H:%M",
        # NO_SCALING de propósito: quem garante que o texto cabe é
        # `_fit_font_size`, e é ele que responde pelo resultado. Com
        # SHRINK_TO_FIT o pyHanko reescala o bloco inteiro para caber — e leva
        # as margens junto, então a folga pedida em pontos virava uma fração
        # imprevisível dela (10 pt viravam 3 pt numa caixa, 6 pt em outra).
        inner_content_layout=SimpleBoxLayoutRule(
            x_align=AxisAlignment.ALIGN_MIN,
            y_align=AxisAlignment.ALIGN_MID,
            margins=Margins(
                left=STAMP_PADDING_X,
                right=int(STAMP_PADDING_X + _seal_width(box)),
                top=STAMP_PADDING_Y,
                bottom=STAMP_PADDING_Y,
            ),
            inner_content_scaling=InnerScaling.NO_SCALING,
        ),
        background=_StampBackground(box),
        background_opacity=1,
        border_width=0,
    )


# ---------------------------------------------------------------------------
# Contexto de validação (usado só quando há carimbo do tempo)
# ---------------------------------------------------------------------------


def _system_trust_roots() -> list[asn1_x509.Certificate]:
    """Âncoras do ca-certificates. É o que faz uma TSA pública validar."""
    if not SYSTEM_TRUST_ROOTS.exists():  # pragma: no cover - depende da distro
        return []
    try:
        from pyhanko.keys import load_certs_from_pemder_data

        return list(load_certs_from_pemder_data(SYSTEM_TRUST_ROOTS.read_bytes()))
    except Exception as exc:  # pragma: no cover - defensivo
        logger.warning("Não consegui ler as âncoras do sistema: %s", exc)
        return []


def icp_brasil_roots(certs_dir: Path | None) -> list[asn1_x509.Certificate]:
    """As raízes da ICP-Brasil que estiverem em ``certs/``.

    São os certificados autoassinados cujo titular é uma "Autoridade
    Certificadora Raiz Brasileira". Servem para responder uma pergunta que a
    validação comum não responde: este carimbo do tempo seria aceito no Brasil?
    """
    return [
        c
        for c in _load_extra_certs(certs_dir)
        if c.self_signed != "no"
        and "raiz brasileira" in str(c.subject.native.get("common_name", "")).lower()
    ]


def build_validation_context(certs_dir: Path | None) -> ValidationContext:
    """Contexto de confiança usado quando há carimbo do tempo.

    São validadas **duas** cadeias: a do assinante (ICP-Brasil) e a do
    certificado da própria TSA. Por isso as âncoras do sistema entram junto
    com o que estiver em ``certs/``: sem elas, TSA pública nenhuma valida;
    sem ``certs/``, certificado ICP-Brasil nenhum valida.

    Certificado autoassinado de ``certs/`` vira âncora (é o formato das raízes
    da ICP-Brasil); o resto entra como intermediário conhecido.
    """
    certs = _load_extra_certs(certs_dir)
    roots = [c for c in certs if c.self_signed != "no"]
    intermediates = [c for c in certs if c.self_signed == "no"]
    return ValidationContext(
        trust_roots=_system_trust_roots() + roots,
        other_certs=intermediates,
        allow_fetching=True,
        revocation_mode="soft-fail",
    )


def probe_timestamper(timestamper: timestamps.TimeStamper) -> None:
    """Bate na TSA antes de assinar.

    Sem isso, a TSA fora do ar aparece como erro de cadeia: o pyHanko valida a
    cadeia do assinante *antes* de pedir o carimbo, e o erro que chega ao
    usuário seria o errado.
    """
    import asyncio

    from pyhanko.sign.timestamps.common_utils import dummy_digest

    try:
        asyncio.run(timestamper.async_timestamp(dummy_digest("sha256"), "sha256"))
    except Exception as exc:
        logger.warning("TSA %s indisponível: %s", getattr(timestamper, "url", "?"), exc)
        # máquina sem rede e autoridade fora do ar pedem respostas diferentes:
        # uma é problema seu, a outra não
        if _looks_offline(exc):
            raise NoInternetError() from exc
        raise TimestampError() from exc


#: Como o sistema operacional diz "não tem rede aqui".
_OFFLINE_SIGNS = (
    "temporary failure in name resolution",
    "name or service not known",
    "nodename nor servname",
    "network is unreachable",
    "no route to host",
    "cannot connect to host",
    "connection refused",
    "getaddrinfo",
)


def _looks_offline(exc: Exception) -> bool:
    """A falha foi por falta de rede, e não por culpa do outro lado?"""
    texto = f"{type(exc).__name__}: {exc}".lower()
    causa = getattr(exc, "__cause__", None)
    if causa is not None:
        texto += f" {type(causa).__name__}: {causa}".lower()
    return any(sinal in texto for sinal in _OFFLINE_SIGNS)


# ---------------------------------------------------------------------------
# Assinatura
# ---------------------------------------------------------------------------


def sign_pdf(
    pdf_bytes: bytes,
    signer: signers.SimpleSigner,
    request: SignatureRequest,
    certs_dir: Path | None = None,
) -> SignResult:
    """Assina o PDF em PAdES e devolve os bytes do documento assinado."""
    box = request.box.validate()
    writer, page_count, already_signed = open_pdf(pdf_bytes)

    if request.page < 1 or request.page > page_count:
        raise PageOutOfRangeError(request.page)

    # Mexer no título é mexer no documento. Em documento que já tem assinatura,
    # qualquer alteração além da própria assinatura é munição para um validador
    # estrito classificar as assinaturas anteriores como indeterminadas — então
    # só se escreve o título quando somos os primeiros a assinar.
    if request.title and not already_signed:
        writer.document_meta.title = request.title

    name = certificate_common_name(signer.signing_cert)
    documento = certificate_document_id(signer.signing_cert)
    field_name = _unique_field_name(writer)

    field_spec = SigFieldSpec(
        sig_field_name=field_name,
        on_page=request.page - 1,  # pyHanko conta a partir de zero
        box=box.as_tuple(),
    )

    timestamper = None
    validation_context = None
    if request.timestamp:
        timestamper = timestamps.HTTPTimeStamper(url=request.tsa_url, timeout=15)
        probe_timestamper(timestamper)
        validation_context = build_validation_context(certs_dir)

    meta = signers.PdfSignatureMetadata(
        field_name=field_name,
        subfilter=SigSeedSubFilter.PADES,
        name=name,
        reason=request.reason or None,
        location=request.location or None,
        embed_validation_info=request.timestamp,
        use_pades_lta=request.timestamp,
        validation_context=validation_context,
    )

    pdf_signer = signers.PdfSigner(
        meta,
        signer=signer,
        timestamper=timestamper,
        stamp_style=_stamp_style(_stamp_lines(name, documento, request), box),
        new_field_spec=field_spec,
    )

    out = BytesIO()
    try:
        pdf_signer.sign_pdf(writer, output=out)
    except Exception as exc:
        raise _classify_signing_failure(exc, timestamped=request.timestamp) from exc

    return SignResult(
        pdf=out.getvalue(),
        signer_name=name,
        already_signed=already_signed,
        timestamped=request.timestamp,
    )


def _classify_signing_failure(exc: Exception, *, timestamped: bool):
    """Traduz a falha do pyHanko no erro que o usuário consegue agir sobre.

    A classificação é por **tipo** de exceção, não por texto: mensagem de
    biblioteca muda de versão para versão, hierarquia de exceção não.
    """
    import asyncio

    from pyhanko_certvalidator import errors as cv_errors

    from app.errors import SigningError

    if isinstance(exc, SigningError):
        return exc

    # Carimbo do tempo: erro do protocolo TSA ou simplesmente rede.
    if timestamped and _looks_offline(exc):
        return NoInternetError()
    if isinstance(exc, timestamps.TimestampRequestError):
        return TimestampError()
    if timestamped and isinstance(exc, (asyncio.TimeoutError, OSError)):
        return TimestampError()
    if timestamped and _is_network_error(exc):
        return TimestampError()

    # Cadeia de certificação incompleta ou não confiável.
    if isinstance(
        exc,
        (
            cv_errors.PathError,
            cv_errors.ValidationError,
            cv_errors.InvalidCertificateError,
            cv_errors.InsufficientRevinfoError,
        ),
    ):
        return ChainIncompleteError()

    logger.error("Falha não classificada ao assinar", exc_info=exc)
    return SigningError()


def _is_network_error(exc: Exception) -> bool:
    """aiohttp só é importado se estiver presente — ele é dependência do TSA."""
    try:
        import aiohttp
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, aiohttp.ClientError)
