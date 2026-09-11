"""Conversão de coordenadas tela → PDF, inclusive com /Rotate.

Os testes rodam o **pdf.js de verdade** (o mesmo arquivo que o app serve) sob
Node, porque a rotação é justamente o que não dá para conferir replicando a
matriz à mão. Cada caso é checado duas vezes:

1. contra valores esperados escritos aqui, derivados da geometria do PDF;
2. contra o que `viewport.convertToPdfPoint` do próprio pdf.js responde.

Se o Node não estiver instalado, os testes são pulados — não são obrigatórios
para assinar, mas são a rede de segurança do posicionamento.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from pyhanko.pdf_utils.reader import PdfFileReader

from app.signing import SignatureBox, SignatureRequest, load_pkcs12_signer, sign_pdf
from tests.conftest import build_pdf

HARNESS = Path(__file__).parent / "js" / "dump_viewport.mjs"
PAGE_W, PAGE_H = 612, 792

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node não instalado — conversão de coordenadas não testada"
)

#: Para onde cada canto do canvas aponta no PDF, por rotação da página.
#: Origem do canvas: canto superior esquerdo. Origem do PDF: inferior esquerdo.
CANTOS_ESPERADOS = {
    0: {
        "topLeft": (0, PAGE_H),
        "topRight": (PAGE_W, PAGE_H),
        "bottomLeft": (0, 0),
        "bottomRight": (PAGE_W, 0),
    },
    90: {
        "topLeft": (0, 0),
        "topRight": (0, PAGE_H),
        "bottomLeft": (PAGE_W, 0),
        "bottomRight": (PAGE_W, PAGE_H),
    },
    180: {
        "topLeft": (PAGE_W, 0),
        "topRight": (0, 0),
        "bottomLeft": (PAGE_W, PAGE_H),
        "bottomRight": (0, PAGE_H),
    },
    270: {
        "topLeft": (PAGE_W, PAGE_H),
        "topRight": (PAGE_W, 0),
        "bottomLeft": (0, PAGE_H),
        "bottomRight": (0, 0),
    },
}


def run_harness(tmp_path: Path, rotate: int, scale: float, rect: str | None = None) -> dict:
    pdf = tmp_path / f"rot{rotate}.pdf"
    pdf.write_bytes(build_pdf(1, rotate=rotate))
    out = tmp_path / f"out{rotate}.json"
    cmd = ["node", str(HARNESS), str(pdf), str(scale), str(out)]
    if rect:
        cmd.append(rect)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not out.exists():
        pytest.fail(f"harness falhou:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(out.read_text())


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
@pytest.mark.parametrize("scale", [1.0, 1.5])
def test_cantos_do_canvas_viram_os_pontos_pdf_certos(tmp_path, rotate, scale):
    data = run_harness(tmp_path, rotate, scale)

    # rotação 90/270 troca largura e altura na tela
    if rotate in (90, 270):
        assert data["viewport"]["width"] == pytest.approx(PAGE_H * scale)
        assert data["viewport"]["height"] == pytest.approx(PAGE_W * scale)
    else:
        assert data["viewport"]["width"] == pytest.approx(PAGE_W * scale)
        assert data["viewport"]["height"] == pytest.approx(PAGE_H * scale)

    for canto, (esperado_x, esperado_y) in CANTOS_ESPERADOS[rotate].items():
        nosso = data["coords"][canto]
        do_pdfjs = data["pdfjs"][canto]
        assert nosso["x"] == pytest.approx(esperado_x, abs=1e-6), f"{canto}.x em /Rotate {rotate}"
        assert nosso["y"] == pytest.approx(esperado_y, abs=1e-6), f"{canto}.y em /Rotate {rotate}"
        # e a nossa conta é a mesma que o pdf.js faz internamente
        assert nosso["x"] == pytest.approx(do_pdfjs["x"], abs=1e-9)
        assert nosso["y"] == pytest.approx(do_pdfjs["y"], abs=1e-9)


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_arrasto_invertido_produz_caixa_normalizada(tmp_path, rotate):
    """Arrastar do canto inferior direito para o superior esquerdo dá a mesma caixa."""
    data = run_harness(tmp_path, rotate, 1.5)
    box = data["box"]
    assert box["x1"] < box["x2"] and box["y1"] < box["y2"]
    assert (box["x1"], box["y1"], box["x2"], box["y2"]) == pytest.approx((0, 0, PAGE_W, PAGE_H))


def test_canvas_escalado_por_css_nao_desloca_o_ponto(tmp_path):
    """Canvas com metade do tamanho CSS (HiDPI/zoom): o fator tem que ser absorvido."""
    data = run_harness(tmp_path, 0, 1.5)
    # rect com left=20, top=30 e metade do tamanho do viewport; clique em (120, 80)
    assert data["clientToViewport"]["x"] == pytest.approx((120 - 20) * 2)
    assert data["clientToViewport"]["y"] == pytest.approx((80 - 30) * 2)


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_retangulo_desenhado_cai_exatamente_ali_no_pdf_assinado(
    tmp_path, rotate, pfx_bytes, pfx_password
):
    """O teste que importa: da tela até o /Rect do documento assinado.

    Desenha um retângulo em coordenadas de tela, converte pelo pdf.js real,
    assina com o resultado e confere onde a anotação foi parar.
    """
    data = run_harness(tmp_path, rotate, 1.5, rect="150,100,450,260")
    caixa = data["sampleBox"]

    signer = load_pkcs12_signer(pfx_bytes, pfx_password)
    req = SignatureRequest(
        page=1,
        box=SignatureBox(caixa["x1"], caixa["y1"], caixa["x2"], caixa["y2"]),
    )
    result = sign_pdf(build_pdf(1, rotate=rotate), signer, req)

    reader = PdfFileReader(BytesIO(result.pdf))
    page = reader.root["/Pages"]["/Kids"][0].get_object()
    rects = [[float(v) for v in a.get_object()["/Rect"]] for a in page["/Annots"]]
    esperado = [round(caixa[k]) for k in ("x1", "y1", "x2", "y2")]
    assert esperado in rects, f"/Rotate {rotate}: esperava {esperado}, achei {rects}"

    # e a caixa está dentro da página, não pendurada fora dela
    assert 0 <= esperado[0] < esperado[2] <= PAGE_W
    assert 0 <= esperado[1] < esperado[3] <= PAGE_H
