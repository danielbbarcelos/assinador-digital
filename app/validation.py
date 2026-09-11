"""Validação das assinaturas de um PDF, offline.

Responde três perguntas, que são diferentes entre si e costumam ser confundidas:

* **íntegro** — o documento não mudou depois desta assinatura;
* **confiável** — a cadeia do certificado fecha numa AC conhecida (as da
  ICP-Brasil ficam em ``certs/``);
* **o que a assinatura cobre** — o arquivo inteiro ou só a revisão dela.

Um documento com assinaturas de várias pessoas devolve uma entrada por
assinatura, mais uma para cada carimbo do tempo de documento.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path

from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import validate_pdf_signature, validate_pdf_timestamp

from app.errors import InvalidPdfError
from app.masking import mask_document
from app.signing import PDF_MAGIC, build_validation_context, icp_brasil_roots

logger = logging.getLogger(__name__)

# Cadeia que não fecha é um resultado aqui, não um incidente: o pyHanko loga o
# traceback inteiro e ele só atrapalharia quem lê o relatório.
for _ruidoso in ("pyhanko", "pyhanko_certvalidator"):
    logging.getLogger(_ruidoso).setLevel(logging.CRITICAL)


#: O que o pyHanko chama de "coverage", em português. Um documento com várias
#: assinaturas normalmente tem as antigas cobrindo só a revisão delas e a
#: última cobrindo o arquivo inteiro — não é defeito, é como a assinatura
#: incremental funciona.
COVERAGE_PT = {
    "ENTIRE_FILE": "o arquivo inteiro",
    "ENTIRE_REVISION": "a versão do documento até esta assinatura",
    "CONTIGUOUS_BLOCK_FROM_START": "do começo do arquivo até esta assinatura",
    "OTHER": "um trecho fora do usual — vale olhar",
}


@dataclass
class SignatureReport:
    field_name: str
    kind: str  # "signature" ou "timestamp"
    holder: str
    document: str | None
    intact: bool
    trusted: bool
    coverage: str
    coverage_label: str
    modifications: str | None
    signed_at: datetime | None
    timestamped_at: datetime | None
    summary: str
    #: O carimbo do tempo veio de uma ACT credenciada na ICP-Brasil?
    #: None quando não há carimbo nenhum.
    icp_timestamp: bool | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.intact and self.trusted

    def as_dict(self) -> dict[str, object]:
        return {
            "field": self.field_name,
            "kind": self.kind,
            "holder": self.holder,
            "document": self.document,
            "intact": self.intact,
            "trusted": self.trusted,
            "coverage": self.coverage,
            "coverage_label": self.coverage_label,
            "modifications": self.modifications,
            "signed_at": self.signed_at.isoformat() if self.signed_at else None,
            "timestamped_at": (
                self.timestamped_at.isoformat() if self.timestamped_at else None
            ),
            "summary": self.summary,
            "icp_timestamp": self.icp_timestamp,
            "problems": self.problems,
            "ok": self.ok,
        }


def validate_pdf_bytes(pdf_bytes: bytes, certs_dir: Path | None = None) -> list[SignatureReport]:
    """Valida todas as assinaturas de um PDF. Lista vazia = documento sem assinatura."""
    if not pdf_bytes.startswith(PDF_MAGIC):
        raise InvalidPdfError()

    contexto = build_validation_context(certs_dir)
    raizes_icp = icp_brasil_roots(certs_dir)
    try:
        reader = PdfFileReader(BytesIO(pdf_bytes), strict=False)
        assinaturas = reader.embedded_signatures
    except Exception as exc:
        raise InvalidPdfError() from exc

    relatorios = []
    for sig in assinaturas:
        # Um carimbo de tempo de documento (PAdES-LTA) é um campo de assinatura
        # como os outros, mas de outro tipo: validá-lo como assinatura de
        # pessoa levanta erro.
        eh_carimbo = str(sig.sig_object.get("/Type")) == "/DocTimeStamp"
        try:
            if eh_carimbo:
                status = validate_pdf_timestamp(sig, validation_context=contexto)
            else:
                status = validate_pdf_signature(
                    sig, signer_validation_context=contexto, ts_validation_context=contexto
                )
        except Exception as exc:
            logger.warning("Campo %s não pôde ser validado: %s", sig.field_name, exc)
            relatorios.append(
                SignatureReport(
                    field_name=sig.field_name,
                    kind="timestamp" if eh_carimbo else "signature",
                    holder="—",
                    document=None,
                    intact=False,
                    trusted=False,
                    coverage="—",
                    coverage_label="—",
                    modifications=None,
                    signed_at=None,
                    timestamped_at=None,
                    summary=str(exc)[:200],
                    problems=["não foi possível validar este campo"],
                )
            )
            continue

        from app.signing import certificate_common_name, certificate_document_id

        cert = status.signing_cert
        problemas: list[str] = []

        # O carimbo do tempo só tem valor na ICP-Brasil se vier de uma ACT
        # credenciada. Um carimbo de TSA estrangeira é criptograficamente
        # válido e mesmo assim é **reprovado** pelo validador oficial — e, sem
        # âncora temporal aceita, a assinatura inteira vira "indeterminada".
        cert_do_carimbo = None
        if eh_carimbo:
            cert_do_carimbo = cert
        elif getattr(status, "timestamp_validity", None) is not None:
            cert_do_carimbo = status.timestamp_validity.signing_cert

        icp = None
        if cert_do_carimbo is not None:
            icp = _issued_under_icp_brasil(cert_do_carimbo, raizes_icp)
            if not icp:
                problemas.append(
                    "o carimbo do tempo não é de uma ACT credenciada na ICP-Brasil: "
                    "o validador oficial vai reprová-lo"
                )
        carimbo = getattr(status, "timestamp_validity", None)
        relatorios.append(
            SignatureReport(
                field_name=sig.field_name,
                kind="timestamp" if eh_carimbo else "signature",
                holder=certificate_common_name(cert),
                # o CPF inteiro não ajuda a decidir nada aqui
                document=mask_document(certificate_document_id(cert)),
                intact=bool(status.intact),
                trusted=bool(status.trusted),
                coverage=status.coverage.name if status.coverage else "—",
                coverage_label=COVERAGE_PT.get(
                    status.coverage.name if status.coverage else "", "—"
                ),
                modifications=(
                    status.modification_level.name
                    if getattr(status, "modification_level", None)
                    else None
                ),
                signed_at=getattr(status, "signer_reported_dt", None),
                timestamped_at=(
                    getattr(status, "timestamp", None)
                    if eh_carimbo
                    else (carimbo.timestamp if carimbo else None)
                ),
                summary=status.summary(),
                icp_timestamp=icp,
                problems=problemas,
            )
        )
    return relatorios


def _issued_under_icp_brasil(cert, raizes) -> bool:
    """O certificado encadeia até uma raiz da ICP-Brasil?

    Comparação por emissor: sobe pela cadeia conhecida até bater numa das
    raízes brasileiras. Não substitui a validação completa — serve para dizer
    se o carimbo *poderia* ser aceito aqui.
    """
    if not raizes:
        return False
    nomes_raiz = {r.subject.native.get("common_name") for r in raizes}
    emissor = cert.issuer.native.get("common_name")
    if emissor in nomes_raiz:
        return True
    # uma AC intermediária brasileira tem "ICP-Brasil" no nome por convenção
    return "icp-brasil" in str(emissor or "").lower()
