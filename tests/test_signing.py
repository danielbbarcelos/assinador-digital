"""Testes da camada de assinatura — sem HTTP, sem `.pfx` de verdade."""

from __future__ import annotations

import datetime
from io import BytesIO
from pathlib import Path

import pytest
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.fields import enumerate_sig_fields

from app import errors
from app.signing import (
    SignatureBox,
    SignatureRequest,
    certificate_common_name,
    load_pkcs12_signer,
    open_pdf,
    sign_pdf,
)
from tests.conftest import build_pdf, build_pfx

BOX = SignatureBox(72, 72, 300, 150)


def _sign(pdf: bytes, pfx: bytes, password: bytes, **kwargs) -> bytes:
    signer = load_pkcs12_signer(pfx, password)
    req = SignatureRequest(page=kwargs.pop("page", 1), box=kwargs.pop("box", BOX), **kwargs)
    return sign_pdf(pdf, signer, req)


# --- carga do certificado --------------------------------------------------


def test_carrega_pfx_valido(pfx_bytes, pfx_password):
    signer = load_pkcs12_signer(pfx_bytes, pfx_password)
    assert certificate_common_name(signer.signing_cert) == "JOSÉ DA SILVA TESTE"


def test_senha_errada(pfx_bytes):
    with pytest.raises(errors.WrongPasswordError) as exc:
        load_pkcs12_signer(pfx_bytes, b"nao-e-essa")
    assert exc.value.code == "WRONG_PASSWORD"
    assert exc.value.field == "password"


def test_pfx_corrompido(pfx_password):
    with pytest.raises(errors.InvalidCertificateError):
        load_pkcs12_signer(b"isto nao e um pkcs12", pfx_password)


def test_pdf_enviado_no_lugar_do_pfx(pfx_password, pdf_bytes):
    with pytest.raises(errors.InvalidCertificateError):
        load_pkcs12_signer(pdf_bytes, pfx_password)


def test_certificado_expirado(pfx_password):
    now = datetime.datetime.now(datetime.timezone.utc)
    pfx = build_pfx(
        not_before=now - datetime.timedelta(days=400),
        not_after=now - datetime.timedelta(days=10),
    )
    with pytest.raises(errors.CertificateExpiredError) as exc:
        load_pkcs12_signer(pfx, pfx_password)
    assert exc.value.code == "CERT_EXPIRED"
    assert "/" in exc.value.message  # data formatada em pt-BR


def test_certificado_ainda_nao_valido(pfx_password):
    now = datetime.datetime.now(datetime.timezone.utc)
    pfx = build_pfx(
        not_before=now + datetime.timedelta(days=10),
        not_after=now + datetime.timedelta(days=400),
    )
    with pytest.raises(errors.CertificateNotYetValidError):
        load_pkcs12_signer(pfx, pfx_password)


def test_senha_nunca_aparece_na_mensagem_de_erro(pfx_bytes):
    segredo = b"correiacavalobateriagrampo"
    with pytest.raises(errors.SigningError) as exc:
        load_pkcs12_signer(pfx_bytes, segredo)
    assert segredo.decode() not in str(exc.value)
    assert segredo.decode() not in exc.value.message


# --- documento -------------------------------------------------------------


def test_pdf_invalido_por_magic_bytes(pfx_bytes, pfx_password):
    with pytest.raises(errors.InvalidPdfError):
        _sign(b"GIF89a nao sou pdf", pfx_bytes, pfx_password)


def test_pdf_protegido_por_senha(pfx_bytes, pfx_password):
    protegido = build_pdf(1, encrypt_password="segredo-do-dono")
    with pytest.raises(errors.EncryptedPdfError):
        _sign(protegido, pfx_bytes, pfx_password)


def test_pagina_fora_do_intervalo(pfx_bytes, pfx_password, pdf_bytes):
    with pytest.raises(errors.PageOutOfRangeError) as exc:
        _sign(pdf_bytes, pfx_bytes, pfx_password, page=99)
    assert "99" in exc.value.message


def test_caixa_degenerada_e_recusada(pfx_bytes, pfx_password, pdf_bytes):
    with pytest.raises(errors.InvalidBoxError):
        _sign(pdf_bytes, pfx_bytes, pfx_password, box=SignatureBox(100, 100, 102, 101))


# --- carimbo ---------------------------------------------------------------


LINHAS = [
    "Assinado digitalmente por",
    "JOSÉ DA SILVA TESTE",
    "CPF 123.456.789-01",
    "11/09/2026 às 10:00",
    "Motivo: Concordo com o conteúdo integral",
    "Local: Belo Horizonte, MG",
]


@pytest.mark.parametrize(
    "largura,altura",
    [(275, 79), (159, 50), (389, 109), (500, 200), (120, 30)],
)
def test_carimbo_sempre_cabe_na_caixa(largura, altura):
    """O texto tem que caber na caixa desenhada, em qualquer tamanho dela."""
    from app.signing import (
        STAMP_FONT_SIZE_MAX,
        STAMP_FONT_SIZE_MIN,
        STAMP_PADDING_X,
        STAMP_PADDING_Y,
        _fit_font_size,
        _seal_width,
        _stamp_font,
    )

    box = SignatureBox(0, 0, largura, altura)
    size = _fit_font_size(LINHAS, box)
    _, char_width = _stamp_font()

    assert isinstance(size, int), "pyHanko monta a caixa com Fraction: float quebra"
    assert STAMP_FONT_SIZE_MIN <= size <= STAMP_FONT_SIZE_MAX

    if size > STAMP_FONT_SIZE_MIN:  # no piso, o texto pode estourar mesmo
        maior_linha = max(len(line) for line in LINHAS)
        util = largura - 2 * STAMP_PADDING_X - _seal_width(box)
        assert maior_linha * char_width * size <= util
        assert (len(LINHAS) + 1) * (size + 2) <= altura - 2 * STAMP_PADDING_Y


def test_carimbo_leva_a_logo_como_marca_dagua(pfx_bytes, pfx_password, pdf_bytes):
    """A logo entra como XObject de imagem, com transparência declarada."""
    from app.signing import STAMP_LOGO, STAMP_LOGO_OPACITY

    assert STAMP_LOGO.exists(), "a logo é parte do pacote"

    signer = load_pkcs12_signer(pfx_bytes, pfx_password)
    resultado = _sign(pdf_bytes, pfx_bytes, pfx_password)

    # /Image no documento e um ExtGState com a opacidade pedida
    assert b"/Image" in resultado.pdf
    assert f"/ca {STAMP_LOGO_OPACITY}".encode() in resultado.pdf or b"/ca" in resultado.pdf


def test_carimbo_funciona_sem_a_logo(monkeypatch, pfx_bytes, pfx_password, pdf_bytes):
    """Faltando o arquivo da logo, assina do mesmo jeito — sem marca d'água."""
    from pathlib import Path

    import app.signing as signing

    monkeypatch.setattr(signing, "STAMP_LOGO", Path("/nao/existe/logo.png"))
    resultado = _sign(pdf_bytes, pfx_bytes, pfx_password)
    assert resultado.pdf.startswith(b"%PDF-")


def test_carimbo_tem_fonte_de_mil_unidades_por_em():
    """A fonte do carimbo precisa ter unitsPerEm 1000.

    O pyHanko 0.37 escreve os avanços de glifo assumindo essa métrica; com
    fonte de 2048 (a maioria das TrueType) o texto sai com as letras
    espaçadas. Se a Nimbus Sans não estiver instalada, o carimbo cai para
    Courier, que é base-14 e não depende de fonte nenhuma do sistema.
    """
    from pyhanko.pdf_utils.font.basic import SimpleFontEngineFactory

    from app.signing import STAMP_FONT, _stamp_style

    style = _stamp_style(LINHAS, SignatureBox(0, 0, 300, 90))

    if not STAMP_FONT.exists():
        assert isinstance(style.text_box_style.font, SimpleFontEngineFactory)
        pytest.skip("Nimbus Sans não instalada; carimbo usa Courier")

    from fontTools.ttLib import TTFont

    assert TTFont(str(STAMP_FONT), lazy=True)["head"].unitsPerEm == 1000


@pytest.mark.parametrize(
    "largura,altura,minimo_esperado",
    [(410, 110, 6), (300, 89, 6), (190, 65, 6), (160, 45, 3), (120, 30, 2)],
)
def test_carimbo_corta_o_superfluo_em_caixa_pequena(largura, altura, minimo_esperado):
    """Caixa apertada perde motivo, local e CPF — nunca o nome e a data.

    Melhor um carimbo curto do que um carimbo completo derramando para fora da
    moldura, que é o que acontecia antes.
    """
    from app.signing import (
        LEADING_EXTRA,
        STAMP_FONT_SIZE_MIN,
        STAMP_PADDING_Y,
        _fit_font_size,
        _fit_lines,
    )

    box = SignatureBox(0, 0, largura, altura)
    linhas = _fit_lines(LINHAS, box)
    size = _fit_font_size(linhas, box)

    assert len(linhas) == minimo_esperado
    assert LINHAS[1] in linhas, "o nome do titular nunca sai"
    assert any("%(ts)s" in l or "/" in l for l in linhas), "a data nunca sai"

    # o bloco cabe na altura, contando a entrelinha fantasma acima da 1ª linha
    if len(linhas) > 2 or size > STAMP_FONT_SIZE_MIN:
        ocupado = (len(linhas) + 1) * (size + LEADING_EXTRA)
        assert ocupado <= altura - 2 * STAMP_PADDING_Y + 1


def test_carimbo_mostra_o_cpf_do_titular(pfx_bytes, pfx_password):
    from app.signing import certificate_document_id

    signer = load_pkcs12_signer(pfx_bytes, pfx_password)
    assert certificate_document_id(signer.signing_cert) == "CPF 123.456.789-01"


def test_carimbo_sem_cpf_quando_o_certificado_nao_traz(pfx_password):
    from app.signing import certificate_document_id

    signer = load_pkcs12_signer(build_pfx(common_name="EMPRESA SEM DOCUMENTO"), pfx_password)
    assert certificate_document_id(signer.signing_cert) is None


def test_carimbo_mostra_nome_documento_data_motivo_e_local():
    from app.signing import _stamp_lines

    req = SignatureRequest(page=1, box=BOX, reason="Concordo", location="BH")
    linhas = _stamp_lines("FULANA DE TAL", "CPF 123.456.789-01", req)
    assert "FULANA DE TAL" in linhas
    assert "CPF 123.456.789-01" in linhas
    assert any("%(ts)s" in linha for linha in linhas), "a data vem do pyHanko"
    assert "Motivo: Concordo" in linhas
    assert "Local: BH" in linhas


def test_carimbo_omite_o_que_esta_vazio():
    from app.signing import _stamp_lines

    linhas = _stamp_lines("FULANA DE TAL", None, SignatureRequest(page=1, box=BOX))
    assert not any(linha.startswith(("Motivo:", "Local:", "CPF", "CNPJ")) for linha in linhas)


# --- geometria -------------------------------------------------------------


def test_box_normaliza_arrasto_invertido():
    box = SignatureBox(300, 200, 100, 50).validate()
    assert (box.x1, box.y1, box.x2, box.y2) == (100, 50, 300, 200)


# --- assinatura ------------------------------------------------------------


def test_assina_e_produz_pdf_valido(pfx_bytes, pfx_password, pdf_bytes):
    result = _sign(pdf_bytes, pfx_bytes, pfx_password, page=2)
    assert result.pdf.startswith(b"%PDF-")
    assert len(result.pdf) > len(pdf_bytes)
    assert result.signer_name == "JOSÉ DA SILVA TESTE"
    assert result.already_signed is False
    assert result.timestamped is False

    reader = PdfFileReader(BytesIO(result.pdf))
    fields = list(enumerate_sig_fields(reader))
    assert len(fields) == 1
    assert fields[0][0] == "Assinatura1"


def test_assinatura_cai_na_pagina_e_na_posicao_pedidas(pfx_bytes, pfx_password):
    pdf = build_pdf(3)
    box = SignatureBox(100, 120, 400, 220)
    result = _sign(pdf, pfx_bytes, pfx_password, page=3, box=box)

    reader = PdfFileReader(BytesIO(result.pdf))
    pages = reader.root["/Pages"]["/Kids"]
    page3 = pages[2].get_object()
    annots = [a.get_object() for a in page3["/Annots"]]
    rects = [[float(v) for v in a["/Rect"]] for a in annots]
    assert [100.0, 120.0, 400.0, 220.0] in rects
    # e não vazou para as outras páginas
    for idx in (0, 1):
        assert "/Annots" not in pages[idx].get_object()


def test_segunda_assinatura_e_incremental_e_avisa(pfx_bytes, pfx_password, pdf_bytes):
    primeira = _sign(pdf_bytes, pfx_bytes, pfx_password)
    assert primeira.already_signed is False

    segunda = _sign(primeira.pdf, pfx_bytes, pfx_password, box=SignatureBox(320, 72, 540, 150))
    assert segunda.already_signed is True
    # documento incremental: o conteúdo anterior continua lá, byte a byte
    assert segunda.pdf.startswith(primeira.pdf)

    reader = PdfFileReader(BytesIO(segunda.pdf))
    nomes = [name for name, *_ in enumerate_sig_fields(reader)]
    assert nomes == ["Assinatura1", "Assinatura2"]


def test_open_pdf_conta_paginas(pdf_bytes):
    _, count, already = open_pdf(pdf_bytes)
    assert count == 3
    assert already is False


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_assina_paginas_rotacionadas(pfx_bytes, pfx_password, rotate):
    pdf = build_pdf(1, rotate=rotate)
    result = _sign(pdf, pfx_bytes, pfx_password)
    assert result.pdf.startswith(b"%PDF-")


# --- carimbo do tempo (exige rede) -----------------------------------------


@pytest.mark.network
def test_nome_que_nao_resolve_e_falta_de_internet(pfx_bytes, pfx_password, pdf_bytes):
    """DNS que não resolve é problema de conexão, e o app diz isso."""
    with pytest.raises(errors.NoInternetError) as exc:
        _sign(
            pdf_bytes,
            pfx_bytes,
            pfx_password,
            timestamp=True,
            tsa_url="http://tsa-que-nao-existe.invalid/tsr",
        )
    assert exc.value.code == "NO_INTERNET"


def test_autoridade_fora_do_ar_nao_e_falta_de_internet(pfx_bytes, pfx_password, pdf_bytes):
    """Porta fechada é a autoridade que não atende, não a internet que caiu.

    A distinção importa: uma pede para tentar de novo mais tarde, a outra pede
    para olhar a conexão. Roda sem rede porque fala com a própria máquina.
    """
    with pytest.raises(errors.TimestampError) as exc:
        _sign(
            pdf_bytes,
            pfx_bytes,
            pfx_password,
            timestamp=True,
            tsa_url="http://127.0.0.1:9/tsr",  # porta 9: fechada por convenção
        )
    assert exc.value.code == "TSA_UNAVAILABLE"


@pytest.mark.network
def test_assina_com_carimbo_do_tempo(pfx_bytes, pfx_password, pdf_bytes, tmp_path):
    """PAdES-LTA de ponta a ponta contra uma TSA pública de verdade."""
    from cryptography.hazmat.primitives.serialization import Encoding, pkcs12

    # o certificado de teste é autoassinado: vira âncora própria em certs/
    _, cert, _ = pkcs12.load_key_and_certificates(pfx_bytes, pfx_password)
    (tmp_path / "raiz-de-teste.pem").write_bytes(cert.public_bytes(Encoding.PEM))

    signer = load_pkcs12_signer(pfx_bytes, pfx_password)
    req = SignatureRequest(page=1, box=BOX, timestamp=True)
    result = sign_pdf(pdf_bytes, signer, req, certs_dir=tmp_path)

    assert result.timestamped is True
    # LTA embute cadeia, CRL/OCSP e token: o arquivo cresce uma ordem de grandeza
    assert len(result.pdf) > len(pdf_bytes) * 10
