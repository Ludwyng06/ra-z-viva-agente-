"""Pruebas automáticas de geografía colombiana tolerante a dictado por voz (geo_co).

Evalúa los 9 casos requeridos por la especificación:
1. 'Horito' -> Orito / Putumayo
2. 'puto mayo' / 'Esta en puto mayo' -> Putumayo
3. 'bogota' -> Bogotá D.C.
4. 'medeyin' -> Medellín (Antioquia)
5. 'cali valle' -> Cali / Valle del Cauca
6. 'san andres' -> San Andrés / San Andrés y Providencia
7. 'santa rosa' -> opciones (existe en varios departamentos)
8. 'xyzabc' -> no_encontrado
9. Ciudad que no pertenece al departamento dado (ej. Cali, Antioquia) -> confirmar
"""

import sys
import unittest
import uuid
from pathlib import Path

# Agregar raíz de copiloto al path
COPILOTO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(COPILOTO_ROOT))

from app.geo_co import (
    resolve_ciudad_dpto,
    resolve_departamento,
    normalize_phonetic,
    normalize_clean,
    strip_accents,
    remove_prefixes,
    split_city_and_department,
    TODOS_LOS_DEPARTAMENTOS,
)
from app.tools_demo import actualizar_borrador_pedido, cotizar_envio


class TestGeoCo(unittest.TestCase):
    """Batería de pruebas para geo_co y resolución tolerante a voz."""

    def test_catalogo_completo(self):
        """Verifica que existan los 32 departamentos + Bogotá D.C. (33 en total)."""
        self.assertEqual(len(TODOS_LOS_DEPARTAMENTOS), 33)
        self.assertIn("Bogotá D.C.", TODOS_LOS_DEPARTAMENTOS)
        self.assertIn("Putumayo", TODOS_LOS_DEPARTAMENTOS)
        self.assertIn("Antioquia", TODOS_LOS_DEPARTAMENTOS)
        self.assertIn("Valle del Cauca", TODOS_LOS_DEPARTAMENTOS)
        self.assertIn("San Andrés y Providencia", TODOS_LOS_DEPARTAMENTOS)

    def test_caso_1_horito_a_orito_putumayo(self):
        """1. 'Horito' con h muda dictada por voz debe resolver a Orito, Putumayo."""
        res = resolve_ciudad_dpto("Horito")
        self.assertEqual(res["estado"], "ok")
        self.assertEqual(res["ciudad"], "Orito")
        self.assertEqual(res["departamento"], "Putumayo")
        self.assertIn("Entendí Orito, Putumayo", res["mensaje"])

    def test_caso_2_puto_mayo_y_esta_en_puto_mayo(self):
        """2. 'puto mayo' y 'Esta en puto mayo' deben resolver a Putumayo."""
        # Como departamento explícito
        res_dep = resolve_departamento("Esta en puto mayo")
        self.assertEqual(res_dep["estado"], "ok")
        self.assertEqual(res_dep["departamento"], "Putumayo")

        # Con prefijo conversacional 'Esta en'
        res_a = resolve_ciudad_dpto("", "Esta en puto mayo")
        self.assertEqual(res_a["estado"], "ok")
        self.assertEqual(res_a["departamento"], "Putumayo")

        # Si el usuario lo dice en el campo ciudad por error
        res_b = resolve_ciudad_dpto("Esta en puto mayo")
        self.assertEqual(res_b["estado"], "ok")
        self.assertEqual(res_b["departamento"], "Putumayo")

        # Sin prefijo: 'puto mayo'
        res_c = resolve_ciudad_dpto("puto mayo")
        self.assertEqual(res_c["estado"], "ok")
        self.assertEqual(res_c["departamento"], "Putumayo")

    def test_caso_3_bogota(self):
        """3. 'bogota' sin tilde ni signos debe resolver a Bogotá D.C."""
        res = resolve_ciudad_dpto("bogota")
        self.assertEqual(res["estado"], "ok")
        self.assertEqual(res["ciudad"], "Bogotá D.C.")
        self.assertEqual(res["departamento"], "Bogotá D.C.")

    def test_caso_4_medeyin(self):
        """4. 'medeyin' con equivalencia fonética ll/y debe resolver a Medellín, Antioquia."""
        res = resolve_ciudad_dpto("medeyin")
        self.assertEqual(res["estado"], "ok")
        self.assertEqual(res["ciudad"], "Medellín")
        self.assertEqual(res["departamento"], "Antioquia")

    def test_caso_5_cali_valle(self):
        """5. 'cali valle' (ciudad y departamento juntos) debe resolver a Cali / Valle del Cauca."""
        res = resolve_ciudad_dpto("cali valle")
        self.assertEqual(res["estado"], "ok")
        self.assertEqual(res["ciudad"], "Cali")
        self.assertEqual(res["departamento"], "Valle del Cauca")

    def test_caso_6_san_andres(self):
        """6. 'san andres' debe resolver a San Andrés / San Andrés y Providencia."""
        res = resolve_ciudad_dpto("san andres")
        self.assertEqual(res["estado"], "ok")
        self.assertEqual(res["ciudad"], "San Andrés")
        self.assertEqual(res["departamento"], "San Andrés y Providencia")

    def test_caso_7_santa_rosa_opciones(self):
        """7. 'santa rosa' existe en varios departamentos (Bolívar, Cauca) -> opciones."""
        res = resolve_ciudad_dpto("santa rosa")
        self.assertEqual(res["estado"], "opciones")
        self.assertEqual(res["ciudad"], "Santa Rosa")
        self.assertIsNone(res["departamento"])
        self.assertTrue(len(res["opciones"]) >= 2)
        opciones_str = " ".join(res["opciones"])
        self.assertIn("Bolívar", opciones_str)
        self.assertIn("Cauca", opciones_str)

    def test_caso_8_xyzabc_no_encontrado(self):
        """8. 'xyzabc' texto inexistente debe retornar no_encontrado."""
        res = resolve_ciudad_dpto("xyzabc")
        self.assertEqual(res["estado"], "no_encontrado")
        self.assertIsNone(res["ciudad"])
        self.assertIsNone(res["departamento"])
        self.assertEqual(res["opciones"], [])

    def test_caso_9_ciudad_no_pertenece_al_departamento_dado(self):
        """9. Ciudad que no pertenece al departamento dado (Cali en Antioquia) -> confirmar."""
        res = resolve_ciudad_dpto("cali", "antioquia")
        self.assertEqual(res["estado"], "confirmar")
        self.assertEqual(res["ciudad"], "Cali")
        self.assertEqual(res["departamento"], "Valle del Cauca")
        self.assertIn("Antioquia", res["mensaje"])
        self.assertIn("Valle del Cauca", res["mensaje"])

    def test_combo_dictado_real_horito_y_puto_mayo(self):
        """Caso del problema real: dictado ciudad='Horito', departamento='Esta en puto mayo'."""
        res = resolve_ciudad_dpto("Horito", "Esta en puto mayo")
        self.assertEqual(res["estado"], "ok")
        self.assertEqual(res["ciudad"], "Orito")
        self.assertEqual(res["departamento"], "Putumayo")
        self.assertIn("Entendí Orito, Putumayo", res["mensaje"])

    def test_actualizar_borrador_pedido_con_horito(self):
        """Verifica que actualizar_borrador_pedido procese Horito y notifique la corrección."""
        cid = str(uuid.uuid4())
        res = actualizar_borrador_pedido(
            cliente_nombre="Carlos Mora",
            ciudad="Horito",
            departamento="Esta en puto mayo",
            conversation_id=cid,
        )
        self.assertEqual(res["estado"], "ok")
        self.assertEqual(res["ciudad"], "Orito")
        self.assertEqual(res["departamento"], "Putumayo")
        self.assertIn("Entendí Orito, Putumayo", res.get("mensaje", "") + res.get("instruccion", ""))

    def test_actualizar_borrador_pedido_santa_rosa_ambiguo(self):
        """Verifica que actualizar_borrador_pedido detenga el borrador y pida opciones con Santa Rosa."""
        cid = str(uuid.uuid4())
        res = actualizar_borrador_pedido(
            cliente_nombre="Laura Gómez",
            ciudad="Santa Rosa",
            conversation_id=cid,
        )
        self.assertEqual(res["estado"], "opciones")
        self.assertTrue(res.get("ambiguous", False))
        self.assertTrue(len(res["opciones"]) >= 2)


if __name__ == "__main__":
    unittest.main()
