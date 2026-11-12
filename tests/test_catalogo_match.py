"""Catálogo real leído de la demo: 010_tienda_demo.sql solo crea el esquema."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import catalogo_match as match


@pytest.fixture
def catalogo():
    rows = [
        ('RV-ACO-04', 'Aceite de Coco Prensado en Frío (300ml)'),
        ('RV-ALA-05', 'Aceite Esencial de Lavanda (15ml)'),
        ('RV-ARO-13', 'Aceite de Romero Puro (30ml)'),
        ('RV-CHI-14', 'Semillas de Chía Orgánica (250g)'),
        ('RV-COL-06', 'Colágeno Hidrolizado en Polvo (300g)'),
        ('RV-CUR-07', 'Cúrcuma en Polvo Pura (200g)'),
        ('RV-GRA-11', 'Granola Artesanal con Frutos Secos (400g)'),
        ('RV-JAB-08', 'Jabón Artesanal de Avena y Miel'),
        ('RV-MAN-12', 'Mantequilla de Maní Natural (350g)'),
        ('RV-MIE-03', 'Miel de Abejas Pura (500g)'),
        ('RV-MOR-09', 'Cápsulas de Moringa (60 unidades)'),
        ('RV-TEM-02', 'Té de Manzanilla Natural (20 bolsitas)'),
        ('RV-TEV-01', 'Té Verde Orgánico (20 bolsitas)'),
        ('RV-VIN-10', 'Vinagre de Manzana con Madre (500ml)'),
    ]
    return [dict(sku=sku, nombre=nombre) for sku, nombre in rows]


@pytest.mark.parametrize('index', range(14))
def test_exact_names_and_skus(catalogo, index):
    product = catalogo[index]
    for text in (product['nombre'], product['sku'], product['sku'].replace('-', ' ')):
        result = match.buscar_producto(text, catalogo)
        assert result['estado'] == 'ok', (text, result)
        assert result['producto'] is product
        assert result['opciones'] == []


@pytest.mark.parametrize('text,sku', [
    ('dos colagenos', 'RV-COL-06'),
    ('un col a geno', 'RV-COL-06'),
    ('colageno idrolisado en polbo', 'RV-COL-06'),
    ('aseite esensial de labanda', 'RV-ALA-05'),
    ('las semillas de chia organica', 'RV-CHI-14'),
    ('jabones artesanales de avena y miel', 'RV-JAB-08'),
    ('mantequiya de mani', 'RV-MAN-12'),
    ('te de mansaniya', 'RV-TEM-02'),
    ('binagre de mansana', 'RV-VIN-10'),
    ('haceite de coco', 'RV-ACO-04'),
    ('MIEL DE ABEJAS!!!', 'RV-MIE-03'),
    ('capsulas de moringa marca Raíz Viva Naturales', 'RV-MOR-09'),
    ('dos curcumas', 'RV-CUR-07'),
    ('granola artesanal', 'RV-GRA-11'),
    ('aceite de romero', 'RV-ARO-13'),
    ('te verde', 'RV-TEV-01'),
])
def test_dictation(catalogo, text, sku):
    result = match.buscar_producto(text, catalogo)
    assert result['estado'] == 'ok', result
    assert result['producto']['sku'] == sku


def test_ambiguous_oils(catalogo):
    result = match.buscar_producto('aceite', catalogo)
    assert result['estado'] == 'opciones'
    assert result['producto'] is None
    assert {p['sku'] for p in result['opciones']} == {'RV-ACO-04', 'RV-ALA-05', 'RV-ARO-13'}


@pytest.mark.parametrize('text', ['', None, 'un dos', 'a', 'zzqqxxx', 'computador portatil'])
def test_unknown(catalogo, text):
    assert match.buscar_producto(text, catalogo)['estado'] == 'no_encontrado'


def test_empty_catalog():
    assert match.buscar_producto('miel', [])['estado'] == 'no_encontrado'


@pytest.mark.parametrize('scores,state', [
    ([.85, .77], 'ok'), ([.85, .78], 'opciones'),
    ([.84, .60], 'opciones'), ([.60, .59], 'opciones'),
    ([.59, .1], 'no_encontrado'), ([1, 1], 'opciones'),
])
def test_thresholds(monkeypatch, scores, state):
    monkeypatch.setattr(match, '_score', lambda query, product: scores[product['id']])
    products = [dict(id=i, nombre=str(i)) for i in range(2)]
    assert match.buscar_producto('producto', products)['estado'] == state


def test_phonetic_g_j():
    assert match._phonetic(['jengibre']) == match._phonetic(['gengibre'])


def test_reader(monkeypatch):
    from contextlib import contextmanager
    from app import db

    rows = [{'sku': 'TEST', 'nombre': 'Prueba'}]

    class Connection:
        def execute(self, sql):
            assert sql == 'SELECT * FROM tienda.productos ORDER BY nombre, sku'
            return self

        def fetchall(self):
            return rows

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr(db, 'demo_db', connection)
    assert match.leer_catalogo_demo() is rows
