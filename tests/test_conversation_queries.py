"""Regresiones: lecturas durante borradores, ediciones y períodos sin pedidos."""
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import Mock

import pytest

from app import agent, tools_demo as tools
from test_productos_integracion import entorno
from test_catalogo_match import catalogo


def test_zero_metrics_are_short(monkeypatch):
    counts = dict.fromkeys(('total', 'confirmados', 'nuevos', 'despachados', 'entregados',
                            'cancelados', 'devoluciones', 'novedades', 'pagados',
                            'contra_entrega', 'ventas_cop', 'fletes_cop'), 0)
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = counts

    @contextmanager
    def db():
        yield connection
    monkeypatch.setattr(tools, 'demo_db', db)
    result = tools.metricas_periodo('hoy')
    assert result['resumen_hablado'] == 'Hoy todavía no hay pedidos.'
    assert result['total_pedidos'] == 0
    # Los grupos vacíos tampoco se enumeran si hay un total positivo.
    counts.update(total=2, ventas_cop=50000)
    assert tools.metricas_periodo('hoy')['resumen_hablado'] == 'Hoy van 2 pedidos con ventas totales de 50.000 COP.'


@pytest.fixture
def draft(entorno, monkeypatch):
    product = next(p for p in entorno if p['sku'] == 'RV-MAN-12')
    draft = dict(conversation_id='00000000-0000-0000-0000-000000000001',
                 cliente_nombre='Laura', ciudad='Pasto', departamento='Nariño', direccion=None,
                 productos=[dict(producto_id=product['id'], sku=product['sku'], nombre=product['nombre'],
                                 cantidad=1, precio_unitario=25000, total=25000)],
                 metodo_pago=None, transportadora=None, estado='en_progreso')
    monkeypatch.setattr(tools, 'get_or_create_borrador', lambda cid: deepcopy(draft))
    monkeypatch.setattr(tools, 'cotizar_envio', lambda ciudad, departamento=None, productos=None: {
        'resumen': f'Envío a {ciudad}: 12.000 COP.', 'mas_barata': None, 'mas_rapida': None})
    monkeypatch.setitem(tools.FUNCTIONS_DEMO, 'cotizar_envio', tools.cotizar_envio)
    return draft


@pytest.mark.parametrize('text', [
    'Hay más versiones de la mantequilla de maní o solo 350 gramos?',
    'hay mas cantidades?', '¿Cuánto cuesta?', '¿Cuánto cuesta el envío?',
    '¿Qué otras presentaciones hay?', '¿Cuánto stock queda?',
])
def test_lateral_question_answers_then_resumes_without_llm(draft, monkeypatch, text):
    @contextmanager
    def db():
        yield conn

    def execute(sql, params=None):
        result = Mock()
        if 'FROM conversations' in sql:
            result.fetchone.return_value = {'id': draft['conversation_id']}
        elif 'RETURNING id' in sql:
            result.fetchone.return_value = {'id': 1}
        elif 'pending_actions' in sql:
            result.fetchone.return_value = None
        elif 'borradores_pedido' in sql:
            result.fetchone.return_value = draft
        result.fetchall.return_value = []
        return result

    conn = Mock()
    conn.execute.side_effect = execute
    monkeypatch.setattr(agent, 'agent_db', db)
    monkeypatch.setattr(agent, 'demo_db', db)
    monkeypatch.setattr(agent, 'COPILOT_MODE', 'demo')
    monkeypatch.setattr(agent.httpx, 'Client', Mock(side_effect=AssertionError('No debe invocar Ollama')))
    before = deepcopy(draft)
    result = agent.chat(text, draft['conversation_id'])
    assert result['answer'].endswith('¿Contra entrega o pagado por anticipado?')
    if 'envío' in text:
        assert result['toolCalls'][0]['name'] == 'cotizar_envio'
        assert 'Pasto' in result['answer'] and '12.000 COP' in result['answer']
    else:
        assert result['toolCalls'][0]['name'] == 'consultar_producto'
        assert '350g' in result['answer'] and '25.000 COP' in result['answer']
        assert '10 unidades disponibles' in result['answer']
    assert draft == before
    assert result['timings']['llmMs'] == 0
    assert result['timings']['totalMs'] < 3000
    tools.save_borrador.assert_not_called()


def test_query_shows_all_catalog_variants(entorno):
    original = next(p for p in entorno if p['sku'] == 'RV-MAN-12')
    entorno.append({**original, 'sku': 'RV-MAN-2', 'nombre': 'Mantequilla de Maní Natural (700g)', 'precio_venta': 45000})
    result = tools.consultar_producto('Hay más versiones de mantequilla de maní o solo 350 gramos?', consulta=True)
    assert len(result['opciones']) == 2
    assert '350g' in result['resumen_hablado'] and '700g' in result['resumen_hablado']


@pytest.mark.parametrize('text,expected', [
    ('agrega 2 miel de abejas', [('RV-MAN-12', 1), ('RV-MIE-03', 2)]),
    ('agrega 2 mantequillas de mani', [('RV-MAN-12', 3)]),
    ('pon 2 tes verdes', [('RV-MAN-12', 1), ('RV-TEV-01', 2)]),
    ('mejor que sean 3', [('RV-MAN-12', 3)]),
    ('3 unidades', [('RV-MAN-12', 3)]),
    ('cambia la mantequilla por 2 miel de abejas', [('RV-MIE-03', 2)]),
])
def test_products_can_change_while_waiting_for_payment(draft, text, expected):
    result = tools.actualizar_borrador_pedido(texto_usuario=text, conversation_id=draft['conversation_id'])
    assert [(p['sku'], p['cantidad']) for p in result['borrador']['productos']] == expected
    assert result['faltan'][0] == 'metodo_pago'


@pytest.mark.parametrize('text', ['contra entrega', 'pagado por anticipado', 'la más barata', 'agrega 2 mieles', 'mejor que sean 3'])
def test_slot_answers_and_changes_are_not_questions(text):
    assert not agent.is_order_question(text)
