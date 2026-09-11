"""Validação: o que ela responde, e o que ela avisa sobre a ICP-Brasil."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, pkcs12

from app.signing import (
    SIGNATURE_HEIGHT,
    SIGNATURE_WIDTH,
    SignatureBox,
    SignatureRequest,
    icp_brasil_roots,
    load_pkcs12_signer,
    sign_pdf,
)
from app.validation import validate_pdf_bytes

BOX = SignatureBox(61, 91, 61 + SIGNATURE_WIDTH, 91 + SIGNATURE_HEIGHT)
CERTS = Path(__file__).resolve().parent.parent / "certs"


@pytest.fixture()
def certs_com_teste(tmp_path, pfx_bytes, pfx_password):
    """As ACs da ICP-Brasil que estiverem em certs/, mais a âncora de teste."""
    destino = tmp_path / "certs"
    destino.mkdir()
    for origem in CERTS.glob("*.crt"):
        (destino / origem.name).write_bytes(origem.read_bytes())
    _, cert, _ = pkcs12.load_key_and_certificates(pfx_bytes, pfx_password)
    (destino / "teste.pem").write_bytes(cert.public_bytes(Encoding.PEM))
    return destino


def test_documento_sem_assinatura(pdf_bytes):
    assert validate_pdf_bytes(pdf_bytes, CERTS) == []


def test_relata_integridade_e_confianca(pfx_bytes, pfx_password, pdf_bytes, certs_com_teste):
    signer = load_pkcs12_signer(pfx_bytes, pfx_password)
    assinado = sign_pdf(pdf_bytes, signer, SignatureRequest(page=1, box=BOX)).pdf

    (relatorio,) = validate_pdf_bytes(assinado, certs_com_teste)
    assert relatorio.kind == "signature"
    assert relatorio.intact is True
    assert relatorio.trusted is True
    assert relatorio.coverage_label == "o arquivo inteiro"
    assert relatorio.icp_timestamp is None, "sem carimbo, nada a dizer sobre ACT"
    assert relatorio.problems == []


def test_cpf_sai_mascarado(pfx_bytes, pfx_password, pdf_bytes, certs_com_teste):
    signer = load_pkcs12_signer(pfx_bytes, pfx_password)
    assinado = sign_pdf(pdf_bytes, signer, SignatureRequest(page=1, box=BOX)).pdf

    (relatorio,) = validate_pdf_bytes(assinado, certs_com_teste)
    assert relatorio.document == "CPF 123.***.***-**"


def test_documento_alterado_depois_perde_a_integridade(
    pfx_bytes, pfx_password, pdf_bytes, certs_com_teste
):
    signer = load_pkcs12_signer(pfx_bytes, pfx_password)
    assinado = sign_pdf(pdf_bytes, signer, SignatureRequest(page=1, box=BOX)).pdf

    # mexe num byte do meio do conteúdo assinado
    meio = len(assinado) // 3
    adulterado = assinado[:meio] + bytes([assinado[meio] ^ 0xFF]) + assinado[meio + 1 :]

    (relatorio,) = validate_pdf_bytes(adulterado, certs_com_teste)
    assert relatorio.intact is False
    assert relatorio.ok is False


def test_ha_raizes_da_icp_brasil_no_repositorio():
    """Sem as raízes em certs/, o app não sabe dizer o que é ACT brasileira."""
    raizes = icp_brasil_roots(CERTS)
    if not raizes:
        pytest.skip("certs/ ainda não foi populado (ver README)")
    assert all("raiz brasileira" in r.subject.native["common_name"].lower() for r in raizes)


@pytest.mark.network
def test_avisa_que_o_carimbo_nao_e_de_act_brasileira(
    pfx_bytes, pfx_password, pdf_bytes, certs_com_teste
):
    """O aviso que faltava.

    A TSA padrão é criptograficamente impecável e não é credenciada na
    ICP-Brasil — o validador oficial reprova o carimbo e, sem âncora temporal
    aceita, trata as assinaturas como indeterminadas. O app precisa dizer isso
    antes, não o usuário descobrir no site do ITI.
    """
    if not icp_brasil_roots(certs_com_teste):
        pytest.skip("sem as raízes da ICP-Brasil não dá para julgar a ACT")

    signer = load_pkcs12_signer(pfx_bytes, pfx_password)
    assinado = sign_pdf(
        pdf_bytes, signer, SignatureRequest(page=1, box=BOX, timestamp=True), certs_dir=certs_com_teste
    ).pdf

    relatorios = validate_pdf_bytes(assinado, certs_com_teste)
    assert any(r.icp_timestamp is False for r in relatorios)
    assert any("ACT credenciada na ICP-Brasil" in p for r in relatorios for p in r.problems)
