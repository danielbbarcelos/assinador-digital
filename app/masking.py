"""Máscaras de dados pessoais.

O app lida com nome completo e CPF o tempo todo — é o que o certificado traz.
Na tela, nem sempre isso precisa aparecer inteiro: o cofre lista certificados e
o validador mostra quem assinou, e em nenhum dos dois o CPF completo ajuda em
alguma coisa. Aqui ficam as duas máscaras usadas nesses lugares.
"""

from __future__ import annotations


def mask_name(nome: str) -> str:
    """Primeiro nome inteiro; o resto vira asteriscos.

    O comprimento de cada sobrenome também é pista, então o mínimo é três.
    """
    partes = [p for p in nome.split() if p]
    if not partes:
        return "—"
    return " ".join([partes[0]] + ["*" * max(len(p), 3) for p in partes[1:]])


def mask_document(documento: str | None) -> str | None:
    """Três primeiros dígitos; o resto escondido. `CPF 123.***.***-**`."""
    if not documento:
        return None
    rotulo, _, numero = documento.partition(" ")
    digitos = "".join(c for c in numero if c.isdigit())
    if len(digitos) == 11:
        return f"{rotulo} {digitos[:3]}.***.***-**"
    if len(digitos) == 14:
        return f"{rotulo} {digitos[:3]}.***.***/****-**"
    return f"{rotulo} {digitos[:3]}..."
