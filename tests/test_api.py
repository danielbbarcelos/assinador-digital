"""Testes da API local — o contrato que o frontend consome."""

from __future__ import annotations

import pytest
import json

from fastapi.testclient import TestClient

from app import main
from tests.conftest import build_pdf

BOX = {"x1": 72, "y1": 72, "x2": 320, "y2": 160}
MARCA = json.dumps([{"page": 1, **BOX}])


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Cada teste com o seu cofre, nunca o do usuário."""
    from app.vault import CertificateVault

    monkeypatch.setattr(main, "vault", CertificateVault(tmp_path / "cofre"))
    return TestClient(main.app)


def sign_request(pdf: bytes, pfx: bytes, password: str, **overrides):
    data = {"marks": MARCA, "reason": "", "location": "", "timestamp": "false"}
    data.update(overrides)
    data["password"] = password
    return {
        "files": {
            "pdf": ("contrato.pdf", pdf, "application/pdf"),
            "pfx": ("cert.pfx", pfx, "application/x-pkcs12"),
        },
        "data": {k: str(v) for k, v in data.items()},
    }


def test_index_serve_a_pagina(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Assinador" in response.text


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["tsa"].startswith("http")


def test_assina_e_devolve_o_pdf_pronto_para_download(client, pfx_bytes, pfx_password, pdf_bytes):
    response = client.post(
        "/api/sign", **sign_request(pdf_bytes, pfx_bytes, pfx_password.decode())
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="contrato-assinado.pdf"'
    assert response.headers["X-Already-Signed"] == "0"
    assert response.headers["X-Timestamped"] == "0"
    assert response.content.startswith(b"%PDF-")
    assert len(response.content) > len(pdf_bytes)


def test_senha_errada_devolve_codigo_e_campo(client, pfx_bytes, pdf_bytes):
    response = client.post("/api/sign", **sign_request(pdf_bytes, pfx_bytes, "nao-e-essa"))
    assert response.status_code == 400
    body = response.json()
    assert body == {
        "error": "Senha do certificado incorreta.",
        "code": "WRONG_PASSWORD",
        "field": "password",
    }


def test_pdf_invalido(client, pfx_bytes, pfx_password):
    response = client.post(
        "/api/sign", **sign_request(b"nao sou um pdf", pfx_bytes, pfx_password.decode())
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PDF"


def test_pagina_fora_do_intervalo(client, pfx_bytes, pfx_password, pdf_bytes):
    response = client.post(
        "/api/sign",
        **sign_request(
            pdf_bytes,
            pfx_bytes,
            pfx_password.decode(),
            marks=json.dumps([{"page": 42, **BOX}]),
        ),
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "PAGE_OUT_OF_RANGE"
    assert "42" in body["error"]


def test_arquivo_vazio(client, pfx_bytes, pfx_password):
    response = client.post("/api/sign", **sign_request(b"", pfx_bytes, pfx_password.decode()))
    assert response.status_code == 400
    assert response.json()["code"] == "EMPTY_FILE"


def test_limite_de_tamanho(client, pfx_bytes, pfx_password, monkeypatch):
    monkeypatch.setattr(main, "MAX_PDF_MB", 1)
    grande = build_pdf(1) + b"\n%" + b"x" * (2 * 1024 * 1024)
    response = client.post("/api/sign", **sign_request(grande, pfx_bytes, pfx_password.decode()))
    assert response.status_code == 400
    assert response.json()["code"] == "FILE_TOO_LARGE"


def test_documento_ja_assinado_e_sinalizado(client, pfx_bytes, pfx_password, pdf_bytes):
    senha = pfx_password.decode()
    primeira = client.post("/api/sign", **sign_request(pdf_bytes, pfx_bytes, senha))
    assert primeira.headers["X-Already-Signed"] == "0"

    segunda = client.post(
        "/api/sign",
        **sign_request(
            primeira.content,
            pfx_bytes,
            senha,
            marks=json.dumps([{"page": 1, "x1": 340, "y1": 72, "x2": 540, "y2": 160}]),
        ),
    )
    assert segunda.status_code == 200
    assert segunda.headers["X-Already-Signed"] == "1"


def test_senha_nao_vaza_para_o_log(client, pfx_bytes, pdf_bytes, caplog):
    senha = "correiacavalobateriagrampo"
    with caplog.at_level("DEBUG"):
        response = client.post("/api/sign", **sign_request(pdf_bytes, pfx_bytes, senha))
    assert response.status_code == 400
    assert senha not in caplog.text
    assert senha not in response.text


def test_nome_com_acento_no_cabecalho_nao_quebra(client, pfx_bytes, pfx_password, pdf_bytes):
    """O CN do certificado de teste tem acento; cabeçalho HTTP é latin-1."""
    response = client.post(
        "/api/sign", **sign_request(pdf_bytes, pfx_bytes, pfx_password.decode())
    )
    assert response.status_code == 200
    assert response.headers["X-Signer-Name"] == "JOSE DA SILVA TESTE"


# --- certificados guardados -------------------------------------------------


def test_cofre_comeca_vazio(client):
    assert client.get("/api/certificates").json() == []


def test_guarda_certificado_e_devolve_mascarado(client, pfx_bytes, pfx_password):
    resposta = client.post(
        "/api/certificates",
        files={"pfx": ("cert.pfx", pfx_bytes, "application/x-pkcs12")},
        data={"password": pfx_password.decode()},
    )
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["holder"].startswith("JOSÉ ")
    assert corpo["document"] == "CPF 123.***.***-**"
    assert "pfx" not in corpo and "password" not in corpo

    assert len(client.get("/api/certificates").json()) == 1


def test_guardar_com_senha_errada_devolve_o_erro_certo(client, pfx_bytes):
    resposta = client.post(
        "/api/certificates",
        files={"pfx": ("cert.pfx", pfx_bytes, "application/x-pkcs12")},
        data={"password": "errada"},
    )
    assert resposta.status_code == 400
    assert resposta.json()["code"] == "WRONG_PASSWORD"


def test_assina_com_certificado_do_cofre(client, pfx_bytes, pfx_password, pdf_bytes):
    """Com a senha guardada, assinar não pede nada."""
    guardado = client.post(
        "/api/certificates",
        files={"pfx": ("cert.pfx", pfx_bytes, "application/x-pkcs12")},
        data={"password": pfx_password.decode(), "store_password": "true"},
    ).json()
    assert guardado["has_password"] is True

    # nem o arquivo nem a senha vão na requisição
    resposta = client.post(
        "/api/sign",
        files={"pdf": ("contrato.pdf", pdf_bytes, "application/pdf")},
        data={"marks": MARCA, "certificate_id": guardado["id"]},
    )
    assert resposta.status_code == 200
    assert resposta.content.startswith(b"%PDF-")


def test_certificado_do_cofre_sem_senha_pede_a_senha(client, pfx_bytes, pfx_password, pdf_bytes):
    guardado = client.post(
        "/api/certificates",
        files={"pfx": ("cert.pfx", pfx_bytes, "application/x-pkcs12")},
        data={"password": pfx_password.decode()},
    ).json()
    assert guardado["has_password"] is False, "não guardar a senha é o padrão"

    sem_senha = client.post(
        "/api/sign",
        files={"pdf": ("contrato.pdf", pdf_bytes, "application/pdf")},
        data={"marks": MARCA, "certificate_id": guardado["id"]},
    )
    assert sem_senha.status_code == 400
    assert sem_senha.json()["code"] == "PASSWORD_NEEDED"

    com_senha = client.post(
        "/api/sign",
        files={"pdf": ("contrato.pdf", pdf_bytes, "application/pdf")},
        data={
            "marks": MARCA,
            "certificate_id": guardado["id"],
            "password": pfx_password.decode(),
        },
    )
    assert com_senha.status_code == 200


def test_assinar_guardando_a_senha_do_certificado_do_cofre(
    client, pfx_bytes, pfx_password, pdf_bytes
):
    guardado = client.post(
        "/api/certificates",
        files={"pfx": ("cert.pfx", pfx_bytes, "application/x-pkcs12")},
        data={"password": pfx_password.decode()},
    ).json()

    client.post(
        "/api/sign",
        files={"pdf": ("contrato.pdf", pdf_bytes, "application/pdf")},
        data={
            "marks": MARCA,
            "certificate_id": guardado["id"],
            "password": pfx_password.decode(),
            "remember_password": "true",
        },
    )
    depois = client.get("/api/certificates").json()[0]
    assert depois["has_password"] is True


def test_apaga_a_senha_guardada(client, pfx_bytes, pfx_password):
    guardado = client.post(
        "/api/certificates",
        files={"pfx": ("cert.pfx", pfx_bytes, "application/x-pkcs12")},
        data={"password": pfx_password.decode(), "store_password": "true"},
    ).json()

    resposta = client.delete(f"/api/certificates/{guardado['id']}/password")
    assert resposta.status_code == 200
    assert resposta.json()["has_password"] is False

    # o certificado continua no cofre
    assert len(client.get("/api/certificates").json()) == 1


def test_guarda_a_senha_de_um_certificado_que_ja_estava_no_cofre(
    client, pfx_bytes, pfx_password
):
    guardado = client.post(
        "/api/certificates",
        files={"pfx": ("cert.pfx", pfx_bytes, "application/x-pkcs12")},
        data={"password": pfx_password.decode()},
    ).json()

    resposta = client.put(
        f"/api/certificates/{guardado['id']}/password",
        data={"password": pfx_password.decode()},
    )
    assert resposta.status_code == 200
    assert resposta.json()["has_password"] is True


def test_assinar_sem_certificado_nenhum(client, pdf_bytes):
    resposta = client.post(
        "/api/sign",
        files={"pdf": ("contrato.pdf", pdf_bytes, "application/pdf")},
        data={"marks": MARCA},
    )
    assert resposta.status_code == 400
    assert resposta.json()["code"] == "NO_CERTIFICATE"


def test_assinar_com_certificado_que_saiu_do_cofre(client, pdf_bytes):
    resposta = client.post(
        "/api/sign",
        files={"pdf": ("contrato.pdf", pdf_bytes, "application/pdf")},
        data={"marks": MARCA, "certificate_id": "nao-existe"},
    )
    assert resposta.status_code == 404
    assert resposta.json()["code"] == "CERT_NOT_FOUND"


def test_assinar_guardando_o_certificado(client, pfx_bytes, pfx_password, pdf_bytes):
    """Guarda o certificado, e a senha só se pedirem."""
    resposta = client.post(
        "/api/sign",
        **sign_request(pdf_bytes, pfx_bytes, pfx_password.decode(), remember="true"),
    )
    assert resposta.status_code == 200

    (guardado,) = client.get("/api/certificates").json()
    assert guardado["has_password"] is False


def test_assinar_guardando_certificado_e_senha(client, pfx_bytes, pfx_password, pdf_bytes):
    client.post(
        "/api/sign",
        **sign_request(
            pdf_bytes,
            pfx_bytes,
            pfx_password.decode(),
            remember="true",
            remember_password="true",
        ),
    )
    (guardado,) = client.get("/api/certificates").json()
    assert guardado["has_password"] is True


def test_assinar_sem_guardar_nao_deixa_rastro(client, pfx_bytes, pfx_password, pdf_bytes):
    client.post("/api/sign", **sign_request(pdf_bytes, pfx_bytes, pfx_password.decode()))
    assert client.get("/api/certificates").json() == []


def test_exclui_certificado(client, pfx_bytes, pfx_password):
    guardado = client.post(
        "/api/certificates",
        files={"pfx": ("cert.pfx", pfx_bytes, "application/x-pkcs12")},
        data={"password": pfx_password.decode()},
    ).json()

    assert client.delete(f"/api/certificates/{guardado['id']}").status_code == 200
    assert client.get("/api/certificates").json() == []
    assert client.delete(f"/api/certificates/{guardado['id']}").status_code == 404


# --- validação --------------------------------------------------------------


def test_valida_documento_assinado(client, pfx_bytes, pfx_password, pdf_bytes):
    assinado = client.post(
        "/api/sign", **sign_request(pdf_bytes, pfx_bytes, pfx_password.decode())
    ).content

    resposta = client.post(
        "/api/validate", files={"pdf": ("assinado.pdf", assinado, "application/pdf")}
    )
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["filename"] == "assinado.pdf"
    assert len(corpo["signatures"]) == 1

    sig = corpo["signatures"][0]
    assert sig["kind"] == "signature"
    assert sig["holder"] == "JOSÉ DA SILVA TESTE"
    assert sig["intact"] is True
    # certificado de teste é autoassinado: íntegro, mas não confiável
    assert sig["trusted"] is False
    assert sig["ok"] is False


def test_valida_documento_sem_assinatura(client, pdf_bytes):
    resposta = client.post(
        "/api/validate", files={"pdf": ("limpo.pdf", pdf_bytes, "application/pdf")}
    )
    assert resposta.status_code == 200
    assert resposta.json()["signatures"] == []


def test_valida_arquivo_que_nao_e_pdf(client):
    resposta = client.post(
        "/api/validate", files={"pdf": ("x.pdf", b"nao sou pdf", "application/pdf")}
    )
    assert resposta.status_code == 400
    assert resposta.json()["code"] == "INVALID_PDF"


# --- assinar em várias páginas ---------------------------------------------


def test_assina_tres_paginas_de_uma_vez(client, pfx_bytes, pfx_password, pdf_bytes):
    """Marcar três páginas produz três assinaturas, uma por página."""
    from io import BytesIO

    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.fields import enumerate_sig_fields

    marcas = json.dumps([{"page": n, **BOX} for n in (1, 2, 3)])
    resposta = client.post(
        "/api/sign", **sign_request(pdf_bytes, pfx_bytes, pfx_password.decode(), marks=marcas)
    )
    assert resposta.status_code == 200

    reader = PdfFileReader(BytesIO(resposta.content))
    campos = [nome for nome, *_ in enumerate_sig_fields(reader)]
    assert campos == ["Assinatura1", "Assinatura2", "Assinatura3"]

    paginas = reader.root["/Pages"]["/Kids"]
    for indice in (0, 1, 2):
        assert "/Annots" in paginas[indice].get_object(), f"página {indice + 1} sem carimbo"


def test_assinar_sem_marca_nenhuma(client, pfx_bytes, pfx_password, pdf_bytes):
    resposta = client.post(
        "/api/sign", **sign_request(pdf_bytes, pfx_bytes, pfx_password.decode(), marks="[]")
    )
    assert resposta.status_code == 400
    assert resposta.json()["code"] == "NO_MARKS"


def test_marca_malformada(client, pfx_bytes, pfx_password, pdf_bytes):
    resposta = client.post(
        "/api/sign",
        **sign_request(
            pdf_bytes, pfx_bytes, pfx_password.decode(), marks='[{"page": 1, "x1": 10}]'
        ),
    )
    assert resposta.status_code == 400
    assert resposta.json()["code"] == "NO_MARKS"


def test_limite_de_assinaturas_por_vez(client, pfx_bytes, pfx_password, pdf_bytes):
    marcas = json.dumps([{"page": 1, **BOX}] * 25)
    resposta = client.post(
        "/api/sign", **sign_request(pdf_bytes, pfx_bytes, pfx_password.decode(), marks=marcas)
    )
    assert resposta.status_code == 400
    assert resposta.json()["code"] == "TOO_MANY_MARKS"


def test_aviso_de_assinatura_anterior_nao_conta_as_proprias(
    client, pfx_bytes, pfx_password, pdf_bytes
):
    """Assinar três páginas de uma vez não é "documento já assinado".

    Da segunda volta em diante o documento realmente já tem assinatura — mas é
    a nossa, feita há um instante. O aviso existe para falar de assinatura de
    outra pessoa, e era isso que ele estava confundindo.
    """
    marcas = json.dumps([{"page": n, **BOX} for n in (1, 2, 3)])
    resposta = client.post(
        "/api/sign", **sign_request(pdf_bytes, pfx_bytes, pfx_password.decode(), marks=marcas)
    )
    assert resposta.headers["X-Already-Signed"] == "0"
    assert resposta.headers["X-Signatures-Added"] == "3"


def test_aviso_aparece_quando_havia_assinatura_mesmo(
    client, pfx_bytes, pfx_password, pdf_bytes
):
    senha = pfx_password.decode()
    primeira = client.post("/api/sign", **sign_request(pdf_bytes, pfx_bytes, senha))

    segunda = client.post(
        "/api/sign",
        **sign_request(
            primeira.content,
            pfx_bytes,
            senha,
            marks=json.dumps([{"page": 1, "x1": 340, "y1": 72, "x2": 540, "y2": 160}]),
        ),
    )
    assert segunda.headers["X-Already-Signed"] == "1"
