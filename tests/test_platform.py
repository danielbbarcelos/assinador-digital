"""O que muda entre sistemas, e o que não pode mudar."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app import platform_support as ps


def test_um_sistema_de_cada_vez():
    assert sum([ps.IS_LINUX, ps.IS_MAC, ps.IS_WINDOWS]) == 1


def test_config_fica_onde_o_sistema_espera():
    caminho = ps.config_dir()
    assert caminho.is_absolute()
    assert caminho.name in {"assinador-digital", "Assinador"}

    if ps.IS_LINUX:
        assert ".config" in str(caminho) or "XDG" in str(caminho)
    if ps.IS_MAC:
        assert "Library/Application Support" in str(caminho)
    if ps.IS_WINDOWS:
        assert "AppData" in str(caminho) or "Roaming" in str(caminho)


def test_config_respeita_o_xdg_no_linux(monkeypatch, tmp_path):
    if not ps.IS_LINUX:
        pytest.skip("XDG_CONFIG_HOME só vale no Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert ps.config_dir() == tmp_path / "assinador-digital"


def test_downloads_existe_ou_ao_menos_faz_sentido():
    caminho = ps.downloads_dir()
    assert caminho.is_absolute()
    assert str(caminho).startswith(str(Path.home())) or caminho.is_dir()


@pytest.mark.skipif(sys.platform == "win32", reason="ACL, não modo de arquivo")
def test_protect_restringe_o_acesso(tmp_path):
    arquivo = tmp_path / "segredo"
    arquivo.write_text("x")
    ps.protect(arquivo)
    assert oct(arquivo.stat().st_mode)[-3:] == "600"

    pasta = tmp_path / "cofre"
    pasta.mkdir()
    ps.protect(pasta, directory=True)
    assert oct(pasta.stat().st_mode)[-3:] == "700"


def test_protect_nao_quebra_em_caminho_inexistente(tmp_path):
    ps.protect(tmp_path / "nao-existe")  # não levanta


def test_a_fonte_do_carimbo_tem_mil_unidades_por_em():
    """O critério da escolha, valendo em qualquer sistema."""
    caminho = ps.stamp_font()
    if caminho is None:
        pytest.skip("nenhuma candidata instalada; o carimbo usa Courier")

    from fontTools.ttLib import TTFont

    assert TTFont(str(caminho), lazy=True, fontNumber=0)["head"].unitsPerEm == 1000
