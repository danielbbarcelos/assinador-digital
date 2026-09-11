"""Fixtures dos testes.

Tudo que precisa de material criptográfico gera o seu próprio: nenhum `.pfx`
real entra neste repositório.
"""

from __future__ import annotations

import datetime
from io import BytesIO

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils import generic, writer
from pyhanko.pdf_utils.generic import pdf_name

PFX_PASSWORD = b"senha-de-teste"
TEST_CN = "JOSÉ DA SILVA TESTE:12345678901"


def build_pfx(
    *,
    password: bytes = PFX_PASSWORD,
    not_before: datetime.datetime | None = None,
    not_after: datetime.datetime | None = None,
    common_name: str = TEST_CN,
) -> bytes:
    """Gera um PKCS#12 autoassinado, com validade controlável."""
    now = datetime.datetime.now(datetime.timezone.utc)
    not_before = not_before or now - datetime.timedelta(days=1)
    not_after = not_after or now + datetime.timedelta(days=365)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Teste"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        name=b"teste",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password),
    )


def build_pdf(
    pages: int = 3,
    rotate: int = 0,
    size=(612, 792),
    encrypt_password: str | None = None,
) -> bytes:
    """PDF mínimo, com páginas, tamanho, /Rotate e criptografia controláveis."""
    w = writer.PdfFileWriter()
    width, height = size
    for i in range(pages):
        page = generic.DictionaryObject(
            {
                pdf_name("/Type"): pdf_name("/Page"),
                pdf_name("/MediaBox"): generic.ArrayObject(
                    [generic.NumberObject(v) for v in (0, 0, width, height)]
                ),
                pdf_name("/Rotate"): generic.NumberObject(rotate),
                pdf_name("/Resources"): generic.DictionaryObject(
                    {
                        pdf_name("/Font"): generic.DictionaryObject(
                            {
                                pdf_name("/F1"): generic.DictionaryObject(
                                    {
                                        pdf_name("/Type"): pdf_name("/Font"),
                                        pdf_name("/Subtype"): pdf_name("/Type1"),
                                        pdf_name("/BaseFont"): pdf_name("/Helvetica"),
                                    }
                                )
                            }
                        )
                    }
                ),
            }
        )
        stream = generic.StreamObject(
            stream_data=f"BT /F1 24 Tf 72 {height - 100} Td (Pagina {i + 1}) Tj ET".encode()
        )
        page[pdf_name("/Contents")] = w.add_object(stream)
        w.insert_page(page)
    if encrypt_password is not None:
        w.encrypt(encrypt_password)
    out = BytesIO()
    w.write(out)
    return out.getvalue()


@pytest.fixture(scope="session")
def pfx_bytes() -> bytes:
    return build_pfx()


@pytest.fixture(scope="session")
def pfx_password() -> bytes:
    return PFX_PASSWORD


@pytest.fixture()
def pdf_bytes() -> bytes:
    return build_pdf()
