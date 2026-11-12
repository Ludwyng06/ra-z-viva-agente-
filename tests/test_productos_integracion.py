"""Integración de resolución conservadora con herramientas, sin escrituras reales."""
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import tools_demo as tools
from test_catalogo_match import catalogo

@pytest.fixture
def entorno(monkeypatch, catalogo):
    for i, p in enumerate(catalogo):
        p.update(id=str(i), stock=10, precio_venta=25000, stock_minimo=2)
    conn = Mock()
    conn.execute.return_value.fetchall.return_value = catalogo
    @contextmanager
    def db():
        yield conn
    monkeypatch.setattr(tools, 'demo_db', db)
    monkeypatch.setattr(tools, 'leer_catalogo_demo', lambda: catalogo)
    monkeypatch.setattr(tools, 'top_productos', lambda **kw: {'top_productos': catalogo[:3]})
    monkeypatch.setattr(tools, 'get_or_create_borrador', lambda cid: dict(
        conversation_id=cid, cliente_nombre=None, ciudad=None, departamento=None,
        direccion=None, productos=[], transportadora=None, metodo_pago=None, estado='en_progreso'))
    monkeypatch.setattr(tools, 'save_borrador', Mock())
    return catalogo

@pytest.mark.parametrize('query', ['te berde', 'RV-TEV-01'])
def test_consulta_precio_stock(entorno, query):
    result = tools.consultar_producto(query)
    assert result['estado'] == 'ok'
    assert result['producto']['sku'] == 'RV-TEV-01'
    assert result['producto']['precio_venta'] == 25000
    assert result['producto']['stock'] == 10
    if query == 'te berde':
        assert 'Entendí' in result['mensaje']

@pytest.mark.parametrize('query,state', [('aceite', 'opciones'), ('xyzabc', 'no_encontrado')])
def test_borrador_no_escribe_ambiguos(entorno, query, state):
    result = tools.actualizar_borrador_pedido(productos=query, conversation_id='test')
    assert result['estado'] == state
    assert result['borrador']['productos'] == []
    tools.save_borrador.assert_not_called()
    assert result['opciones'] if state == 'opciones' else result['sugerencias']

@pytest.mark.parametrize('query', ['aceite', 'xyzabc'])
def test_propuesta_detiene_antes_de_escribir(entorno, monkeypatch, query):
    agent = Mock(side_effect=AssertionError('No debe escribir propuesta'))
    monkeypatch.setattr(tools, 'agent_db', agent)
    result = tools.proponer_pedido('Ana', 'Bogota', productos=query, conversation_id='test')
    assert result['estado'] in ('opciones', 'no_encontrado')
    agent.assert_not_called()

def test_borrador_corrige_y_conserva_cantidad(entorno):
    result = tools.actualizar_borrador_pedido(productos='dos tes berdes', conversation_id='test')
    assert result['borrador']['productos'][0]['sku'] == 'RV-TEV-01'
    assert result['borrador']['productos'][0]['cantidad'] == 2
    assert 'Entendí' in result['mensaje']

def test_texto_libre_no_separa_y_dentro_de_palabra(entorno):
    result = tools.parse_items_from_text('dos tes berdes y un colageno', entorno)
    assert len(result) == 2
    assert [r['producto']['sku'] for _, r in result] == ['RV-TEV-01', 'RV-COL-06']

def test_texto_libre_desconocido_cuantificado_pide_repetir(entorno):
    result = tools.actualizar_borrador_pedido(texto_usuario='dos xyzabc', conversation_id='test')
    assert result['estado'] == 'no_encontrado'
    assert result['sugerencias']
