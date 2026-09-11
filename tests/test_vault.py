"""Cofre de certificados: o que ele guarda, o que ele mostra e o que ele esconde."""

from __future__ import annotations

import datetime

import pytest

from app import errors
from app.vault import CertificateVault, _mask_document, _mask_name
from tests.conftest import build_pfx


@pytest.fixture()
def vault(tmp_path):
    return CertificateVault(tmp_path / "cofre")


def test_guarda_e_lista(vault, pfx_bytes, pfx_password):
    guardado = vault.add(pfx_bytes, pfx_password)
    assert [c.id for c in vault.list()] == [guardado.id]

    de_volta = vault.get(guardado.id)
    assert de_volta.pfx == pfx_bytes
    assert de_volta.password == pfx_password.decode()


def test_o_que_vai_para_a_tela_e_mascarado(vault, pfx_bytes, pfx_password):
    publico = vault.add(pfx_bytes, pfx_password).as_public()

    assert publico["holder"].startswith("JOSÉ ")
    assert "SILVA" not in publico["holder"], "só o primeiro nome fica visível"
    assert publico["document"] == "CPF 123.***.***-**"
    assert "456" not in publico["document"]
    assert publico["expired"] is False
    assert set(publico) == {
        "id", "holder", "document", "valid_until", "expired", "added_at"
    }, "nem o .pfx nem a senha saem daqui"


def test_o_arquivo_em_disco_nao_tem_nada_legivel(vault, pfx_bytes, pfx_password):
    vault.add(pfx_bytes, pfx_password)
    bruto = vault.blob.read_bytes()

    assert b"JOS" not in bruto and b"SILVA" not in bruto
    assert pfx_password not in bruto
    assert pfx_bytes[:32] not in bruto
    assert bruto.startswith(b"gAAAAA"), "Fernet"


def test_permissoes_do_cofre(vault, pfx_bytes, pfx_password):
    vault.add(pfx_bytes, pfx_password)
    assert oct(vault.keyfile.stat().st_mode)[-3:] == "600"
    assert oct(vault.blob.stat().st_mode)[-3:] == "600"


def test_senha_errada_nao_entra_no_cofre(vault, pfx_bytes):
    with pytest.raises(errors.WrongPasswordError):
        vault.add(pfx_bytes, b"nao-e-essa")
    assert vault.list() == []


def test_certificado_expirado_nao_entra(vault, pfx_password):
    agora = datetime.datetime.now(datetime.timezone.utc)
    velho = build_pfx(
        not_before=agora - datetime.timedelta(days=400),
        not_after=agora - datetime.timedelta(days=10),
    )
    with pytest.raises(errors.CertificateExpiredError):
        vault.add(velho, pfx_password)


def test_guardar_o_mesmo_certificado_duas_vezes_nao_duplica(vault, pfx_bytes, pfx_password):
    vault.add(pfx_bytes, pfx_password)
    vault.add(pfx_bytes, pfx_password)
    assert len(vault.list()) == 1


def test_dois_titulares_convivem(vault, pfx_bytes, pfx_password):
    vault.add(pfx_bytes, pfx_password)
    vault.add(build_pfx(common_name="MARIA DE SOUZA:98765432100"), pfx_password)
    assert len(vault.list()) == 2


def test_excluir(vault, pfx_bytes, pfx_password):
    guardado = vault.add(pfx_bytes, pfx_password)
    vault.delete(guardado.id)
    assert vault.list() == []

    with pytest.raises(errors.SigningError) as exc:
        vault.delete(guardado.id)
    assert exc.value.code == "CERT_NOT_FOUND"


def test_cofre_vazio_nao_quebra(vault):
    assert vault.list() == []


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        # partícula curta ganha três asteriscos: o comprimento também é pista
        ("JOSÉ DA SILVA TESTE", "JOSÉ *** ***** *****"),
        ("MARIA", "MARIA"),
        ("ANA PAULA", "ANA *****"),
    ],
)
def test_mascara_do_nome(entrada, esperado):
    assert _mask_name(entrada) == esperado


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("CPF 123.456.789-01", "CPF 123.***.***-**"),
        ("CNPJ 12.345.678/0001-90", "CNPJ 123.***.***/****-**"),
        (None, None),
    ],
)
def test_mascara_do_documento(entrada, esperado):
    assert _mask_document(entrada) == esperado
