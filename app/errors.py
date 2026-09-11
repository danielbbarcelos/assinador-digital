"""Exceções tipadas do assinador.

Cada erro carrega um ``code`` estável (consumido pelo frontend para destacar o
campo certo) e uma ``message`` em português, já pronta para exibição. Nenhuma
mensagem pode conter a senha do PKCS#12 nem trechos do documento.

Este módulo não importa FastAPI: o mapeamento para HTTP vive em ``main.py``.
"""

from __future__ import annotations

from datetime import datetime


class SigningError(Exception):
    """Erro de negócio do assinador, com código estável e texto exibível."""

    code = "SIGNING_ERROR"
    message = "Não foi possível assinar o documento."
    #: status HTTP sugerido; 4xx = culpa do input, 5xx = culpa do ambiente.
    http_status = 400
    #: campo do formulário que o frontend deve destacar (ou None).
    field: str | None = None

    def __init__(self, message: str | None = None):
        if message is not None:
            self.message = message
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str | None]:
        return {"error": self.message, "code": self.code, "field": self.field}


# --- certificado -----------------------------------------------------------


class WrongPasswordError(SigningError):
    code = "WRONG_PASSWORD"
    message = "Senha do certificado incorreta."
    field = "password"


class InvalidCertificateError(SigningError):
    code = "INVALID_CERTIFICATE"
    message = "Arquivo de certificado inválido ou corrompido."
    field = "pfx"


class LegacyCryptoError(SigningError):
    code = "LEGACY_CRYPTO"
    message = (
        "Certificado usa criptografia legada. "
        "Reempacote com `openssl pkcs12 -legacy` (ver README)."
    )
    field = "pfx"


class NoCertificateError(SigningError):
    code = "NO_CERTIFICATE"
    message = "Escolha um certificado para assinar."
    field = "pfx"


class CertificateExpiredError(SigningError):
    code = "CERT_EXPIRED"
    field = "pfx"

    def __init__(self, expired_at: datetime):
        self.expired_at = expired_at
        super().__init__(f"Certificado expirado em {expired_at:%d/%m/%Y}.")


class CertificateNotYetValidError(SigningError):
    code = "CERT_NOT_YET_VALID"
    field = "pfx"

    def __init__(self, valid_from: datetime):
        self.valid_from = valid_from
        super().__init__(
            f"Certificado só é válido a partir de {valid_from:%d/%m/%Y}."
        )


class ChainIncompleteError(SigningError):
    code = "CHAIN_INCOMPLETE"
    message = (
        "Não foi possível montar a cadeia de certificação até uma AC confiável. "
        "Coloque os certificados das ACs intermediárias em certs/ (ver README)."
    )
    field = "pfx"


# --- documento -------------------------------------------------------------


class InvalidPdfError(SigningError):
    code = "INVALID_PDF"
    message = "Arquivo enviado não é um PDF válido."
    field = "pdf"


class EncryptedPdfError(SigningError):
    code = "PDF_ENCRYPTED"
    message = "PDF protegido por senha — remova a proteção antes de assinar."
    field = "pdf"


class PageOutOfRangeError(SigningError):
    code = "PAGE_OUT_OF_RANGE"
    field = "page"

    def __init__(self, page: int):
        self.page = page
        super().__init__(f"Página {page} não existe neste documento.")


class NoMarksError(SigningError):
    code = "NO_MARKS"
    message = "Marque na página onde a assinatura deve entrar."
    field = "box"


class TooManyMarksError(SigningError):
    code = "TOO_MANY_MARKS"
    field = "box"

    def __init__(self, limite: int):
        super().__init__(f"No máximo {limite} assinaturas por vez.")


class InvalidBoxError(SigningError):
    code = "INVALID_BOX"
    message = "Área da assinatura inválida — desenhe o retângulo novamente."
    field = "box"


class VazioError(SigningError):
    code = "EMPTY_FILE"
    message = "Arquivo vazio."

    def __init__(self, field: str):
        self.field = field
        super().__init__()


class FileTooLargeError(SigningError):
    code = "FILE_TOO_LARGE"
    field = "pdf"

    def __init__(self, limit_mb: int):
        super().__init__(f"Arquivo maior que o limite de {limit_mb} MB.")


# --- carimbo do tempo ------------------------------------------------------


class TimestampError(SigningError):
    code = "TSA_UNAVAILABLE"
    message = (
        "Servidor de carimbo do tempo indisponível. "
        "Tente sem carimbo ou mais tarde."
    )
    http_status = 502
    field = "timestamp"
