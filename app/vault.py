"""Cofre local de certificados A1.

Guarda o `.pfx` e a senha em disco para não ter que reescolher o arquivo e
redigitar a senha a cada assinatura.

Como isso é protegido, sem rodeios:

* tudo (arquivo, senha, nome, CPF) vive num único blob **cifrado com Fernet**
  (AES-128-CBC + HMAC) em ``~/.config/sign-manager/vault.enc``;
* a chave fica ao lado, em ``vault.key``, com permissão ``0600``.

Ou seja: protege contra leitura casual, backup mal guardado, sincronização para
nuvem e olhar alheio no disco — **não** contra alguém que já esteja logado como
você. Para esse nível, o certificado não deveria estar em disco nenhum: é para
isso que existe o A3 em token, que é o próximo passo do app.

O que sai daqui para a interface é sempre mascarado (`as_public`): primeiro
nome, três primeiros dígitos do CPF, validade.
"""

from __future__ import annotations

import json
import os
import uuid
from base64 import b64decode, b64encode
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.errors import SigningError
from app.masking import mask_document as _mask_document
from app.masking import mask_name as _mask_name
from app.signing import (
    certificate_common_name,
    certificate_document_id,
    load_pkcs12_signer,
)


class VaultError(SigningError):
    code = "VAULT_ERROR"
    message = "Não consegui abrir o cofre de certificados."
    http_status = 500


class CertificateNotFound(SigningError):
    code = "CERT_NOT_FOUND"
    message = "Certificado não está mais no cofre."
    field = "pfx"
    http_status = 404


#: Como o app se chamava antes. Quem já tinha certificado guardado não pode
#: perdê-lo só porque o diretório mudou de nome.
LEGACY_DIR_NAME = "sign-manager"


def default_vault_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    atual = base / "assinador-digital"
    antigo = base / LEGACY_DIR_NAME
    if not atual.exists() and antigo.is_dir():
        atual.parent.mkdir(parents=True, exist_ok=True)
        antigo.rename(atual)
    return atual


@dataclass(frozen=True)
class StoredCertificate:
    """Um certificado guardado. `pfx` e `password` só saem para assinar."""

    id: str
    holder: str
    document: str | None
    not_after: datetime
    added_at: datetime
    pfx: bytes
    password: str

    @property
    def expired(self) -> bool:
        return datetime.now(timezone.utc) > self.not_after

    def as_public(self) -> dict[str, object]:
        """O que a interface pode ver: nada que identifique por inteiro."""
        return {
            "id": self.id,
            "holder": _mask_name(self.holder),
            "document": _mask_document(self.document),
            "valid_until": self.not_after.date().isoformat(),
            "expired": self.expired,
            "added_at": self.added_at.date().isoformat(),
        }


class CertificateVault:
    """Lista, guarda e devolve certificados. Tudo cifrado em repouso."""

    def __init__(self, directory: Path | None = None):
        self.dir = directory or default_vault_dir()
        self.blob = self.dir / "vault.enc"
        self.keyfile = self.dir / "vault.key"

    # --- chave -------------------------------------------------------------

    def _fernet(self) -> Fernet:
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.keyfile.exists():
            self.keyfile.write_bytes(Fernet.generate_key())
            self.keyfile.chmod(0o600)
        return Fernet(self.keyfile.read_bytes())

    # --- leitura e escrita do blob ----------------------------------------

    def _read(self) -> list[dict]:
        if not self.blob.exists():
            return []
        try:
            dados = self._fernet().decrypt(self.blob.read_bytes())
        except InvalidToken as exc:
            raise VaultError(
                "O cofre não abre com a chave atual. "
                f"Se a chave foi perdida, apague {self.blob} e cadastre de novo."
            ) from exc
        return json.loads(dados)

    def _write(self, registros: list[dict]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        cifrado = self._fernet().encrypt(json.dumps(registros).encode("utf-8"))
        # escreve e troca, para não deixar o cofre pela metade se faltar luz
        temp = self.blob.with_suffix(".tmp")
        temp.write_bytes(cifrado)
        temp.chmod(0o600)
        temp.replace(self.blob)

    # --- operações ---------------------------------------------------------

    def list(self) -> list[StoredCertificate]:
        return [_from_record(r) for r in self._read()]

    def get(self, cert_id: str) -> StoredCertificate:
        for registro in self._read():
            if registro["id"] == cert_id:
                return _from_record(registro)
        raise CertificateNotFound()

    def add(self, pfx_bytes: bytes, password: bytes) -> StoredCertificate:
        """Guarda um certificado — validando antes que ele abre com a senha."""
        signer = load_pkcs12_signer(pfx_bytes, password)
        cert = signer.signing_cert
        validade = cert["tbs_certificate"]["validity"]["not_after"].native

        registros = self._read()
        novo = {
            "id": uuid.uuid4().hex[:12],
            "holder": certificate_common_name(cert),
            "document": certificate_document_id(cert),
            "not_after": validade.isoformat(),
            "added_at": datetime.now(timezone.utc).isoformat(),
            "pfx": b64encode(pfx_bytes).decode("ascii"),
            "password": password.decode("utf-8"),
        }

        # mesmo titular e mesma validade: é o mesmo certificado, atualiza
        registros = [
            r
            for r in registros
            if not (r["holder"] == novo["holder"] and r["not_after"] == novo["not_after"])
        ]
        registros.append(novo)
        self._write(registros)
        return _from_record(novo)

    def delete(self, cert_id: str) -> None:
        registros = self._read()
        restantes = [r for r in registros if r["id"] != cert_id]
        if len(restantes) == len(registros):
            raise CertificateNotFound()
        self._write(restantes)


def _from_record(registro: dict) -> StoredCertificate:
    return StoredCertificate(
        id=registro["id"],
        holder=registro["holder"],
        document=registro.get("document"),
        not_after=datetime.fromisoformat(registro["not_after"]),
        added_at=datetime.fromisoformat(registro["added_at"]),
        pfx=b64decode(registro["pfx"]),
        password=registro["password"],
    )
