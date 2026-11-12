"""Evaluación explícita contra la API local; no inicia servicios."""
import argparse
import json
import os
import sys
import time
from pathlib import Path
import httpx
import yaml
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import ROOT, COPILOT_MODE
from app.db import atlas_db, agent_db, demo_db
from app.tools import store_id


def extra_checks(spec, data):
    """Reglas optativas, compartidas por casos simples y turnos.

    herramienta_resultado compara campos JSON de summary en UNA llamada exitosa.
    Un summary inválido/truncado no cuenta como resultado verificado.
    """
    answer = data.get('answer', '').casefold()
    forbidden = spec.get('no_contiene', [])
    if isinstance(forbidden, str):
        forbidden = [forbidden]
    if any(word.casefold() in answer for word in forbidden):
        return False
    required = spec.get('contiene_todos', [])
    if any(word.casefold() not in answer for word in required):
        return False
    expected = spec.get('herramienta_resultado')
    if expected:
        for call in data.get('toolCalls', []):
            if not call.get('ok') or call.get('name') != expected['nombre']:
                continue
            try:
                result = json.loads(call.get('summary', ''))
            except (TypeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue
            if any(result.get(key) != value for key, value in expected.get('campos', {}).items()):
                continue
            if len(result.get('opciones') or []) < expected.get('min_opciones', 0):
                continue
            serialized = json.dumps(result, ensure_ascii=False).casefold()
            if any(word.casefold() not in serialized for word in expected.get('contiene', [])):
                continue
            return True
        return False
    return True


def existing_order():
    if COPILOT_MODE == 'demo':
        with demo_db() as connection:
            row = connection.execute(
                "SELECT numero FROM tienda.pedidos WHERE estado IN ('confirmado', 'despachado', 'nuevo') ORDER BY fecha DESC LIMIT 1"
            ).fetchone()
            if row:
                return row['numero']
        return 'RV-1065'

    if os.getenv('ATLAS_EVAL_ORDER'):
        return os.environ['ATLAS_EVAL_ORDER']
    with atlas_db() as connection:
        row = connection.execute('''SELECT p.pancake_order_id FROM pancake_order_snapshots p
            WHERE p.store_id=%s ORDER BY (p.pancake_order_id LIKE 'SIM-%%'), EXISTS(SELECT 1 FROM logistics_agent_decisions d
              WHERE d.store_id=p.store_id AND d.pancake_order_id=p.pancake_order_id) DESC,
              p.inserted_at DESC NULLS LAST LIMIT 1''', (store_id(connection),)).fetchone()
    if not row:
        raise RuntimeError('No hay pedidos locales; configura ATLAS_EVAL_ORDER con un pedido existente.')
    return row['pancake_order_id']


def run(base_url, casos_path=None):
    if casos_path:
        cases_file = Path(casos_path)
    elif COPILOT_MODE == 'demo':
        cases_file = ROOT / 'eval/casos_demo.yaml'
    else:
        cases_file = ROOT / 'eval/casos.yaml'

    cases = yaml.safe_load(cases_file.read_text(encoding='utf-8'))
    number = existing_order()
    with agent_db() as connection:
        run_id = connection.execute('INSERT INTO eval_runs(total) VALUES (%s) RETURNING id', (len(cases),)).fetchone()['id']
    results = []

    if base_url == 'app':
        from starlette.testclient import TestClient
        from app.main import app
        client = TestClient(app)
    else:
        client = httpx.Client(base_url=base_url, timeout=600)

    with client:
        for case in cases:
            tick = time.perf_counter()
            data, error = {}, None
            if 'turnos' in case:
                conv_id = None
                all_tools = []
                total_ms = 0
                correct = True
                for turno in case['turnos']:
                    t_tick = time.perf_counter()
                    payload = {'text': turno['text'].replace('{pedido_existente}', number)}
                    if conv_id:
                        payload['conversationId'] = conv_id
                    try:
                        response = client.post('/api/chat', json=payload)
                        response.raise_for_status()
                        data = response.json()
                        conv_id = data.get('conversationId')
                    except Exception as exc:
                        error = type(exc).__name__
                        correct = False
                        break
                    t_ms = round((time.perf_counter() - t_tick) * 1000)
                    total_ms += t_ms
                    t_tools = [t['name'] for t in data.get('toolCalls', []) if t.get('ok')]
                    all_tools.extend(t_tools)

                    t_expected = turno.get('expected_tool')
                    t_answer = data.get('answer', '')
                    correct = extra_checks(turno, data) and correct
                    t_biz_tools = [t for t in t_tools if t != 'guardia_seguridad']
                    if t_expected:
                        exp_list = t_expected if isinstance(t_expected, list) else [t_expected]
                        if not any(exp in t_tools for exp in exp_list):
                            correct = False
                    elif t_expected is None and 'expected_tool' in turno:
                        if t_biz_tools:
                            correct = False
                    if turno.get('keywords_any'):
                        if not any(word.casefold() in t_answer.casefold() for word in turno['keywords_any']):
                            correct = False
                    if turno.get('require_pending') is True and not data.get('pendingAction'):
                        correct = False
                    elif turno.get('require_pending') is False and data.get('pendingAction'):
                        correct = False

                    if turno.get('verify_pending_quantity'):
                        with agent_db() as aconn:
                            p_rows = aconn.execute("SELECT * FROM pending_actions WHERE conversation_id = %s AND status = 'pending'", (conv_id,)).fetchall()
                            if len(p_rows) != 1:
                                correct = False
                            else:
                                p_items = p_rows[0]['payload'].get('items', [])
                                tot_qty = sum(it.get('cantidad', 1) for it in p_items)
                                if tot_qty != turno['verify_pending_quantity']:
                                    correct = False

                    if turno.get('verify_db_order'):
                        v_db = turno['verify_db_order']
                        with demo_db() as dconn:
                            db_order = dconn.execute('''
                                SELECT p.id, p.numero, p.estado, pi.cantidad
                                FROM tienda.pedidos p
                                JOIN tienda.clientes c ON c.id = p.cliente_id
                                JOIN tienda.pedido_items pi ON pi.pedido_id = p.id
                                WHERE c.nombre ILIKE %s
                                ORDER BY p.id DESC LIMIT 1
                            ''', (f"%{v_db['cliente']}%",)).fetchone()
                            if not db_order or db_order['cantidad'] != v_db['cantidad'] or db_order['estado'] != 'confirmado':
                                correct = False

                elapsed = total_ms
                tools = all_tools
                expected = case.get('expected_tool') or (case['turnos'][-1].get('expected_tool') if case['turnos'] else None)
                if expected and isinstance(expected, list):
                    expected = expected[0]
                answer = data.get('answer', '')
                if error or not answer:
                    correct = False
            else:
                try:
                    response = client.post('/api/chat', json={'text': case['text'].replace('{pedido_existente}', number)})
                    response.raise_for_status()
                    data = response.json()
                except Exception as exc:
                    error = type(exc).__name__
                elapsed = round((time.perf_counter() - tick) * 1000)
                tools = [t['name'] for t in data.get('toolCalls', []) if t.get('ok')]
                business_tools = [t for t in tools if t != 'guardia_seguridad']
                expected = case.get('expected_tool')
                answer = data.get('answer', '')
                if expected:
                    exp_list = expected if isinstance(expected, list) else [expected]
                    correct = not error and bool(answer) and any(e in business_tools for e in exp_list)
                else:
                    correct = not error and bool(answer) and not business_tools
                correct = correct and any(word.casefold() in answer.casefold() for word in case['keywords_any'])
                if case.get('require_sources'):
                    correct = correct and bool(data.get('sources'))
                if case.get('require_pending'):
                    correct = correct and bool(data.get('pendingAction'))
                if case.get('require_refusal'):
                    correct = correct and not data.get('pendingAction') and any(t.get('name') == 'guardia_seguridad' for t in data.get('toolCalls', []))
                if case['id'] == 'pedido_existente':
                    correct = correct and any('"encontrado": true' in t.get('summary', '').lower() for t in data.get('toolCalls', []))
                if case['id'] == 'pedido_inexistente':
                    correct = correct and any('"encontrado": false' in t.get('summary', '').lower() for t in data.get('toolCalls', []))
            correct = extra_checks(case, data) and correct
            results.append((case['id'], tools, bool(correct), elapsed, error))
            with agent_db() as connection:
                connection.execute('''INSERT INTO eval_results(run_id,case_name,expected_tool,used_tools,correct,ms,answer)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)''', (run_id, case['id'], expected, Jsonb(tools), bool(correct), elapsed, answer or error))
            # No dejar propuestas pendientes por ejecutar la evaluación.
            if data.get('pendingAction'):
                try:
                    client.post('/api/actions/' + data['pendingAction']['id'] + '/cancel').raise_for_status()
                except httpx.HTTPError:
                    print('No se pudo cancelar la propuesta de evaluación; revisar historial.', flush=True)
            print(f'{case["id"]}: {"OK" if correct else "FALLO"} ({elapsed} ms)', flush=True)

    passed = sum(r[2] for r in results)
    with agent_db() as connection:
        connection.execute('UPDATE eval_runs SET passed=%s WHERE id=%s', (passed, run_id))

    title = '# Evaluación del copiloto de tienda (Raíz Viva Naturales)' if COPILOT_MODE == 'demo' else '# Evaluación del copiloto de Atlas'
    lines = [
        title, '', f'Ejecución: `{run_id}`. Archivo: `{cases_file.name}`. Resultado: {passed}/{len(cases)}.',
        '', 'Criterio automático: herramienta correcta y exitosa, palabras clave y condiciones de fuentes/confirmación/rechazo. No reemplaza revisión humana de exactitud. Casos por texto; calidad de voz requiere demo manual.',
        '', '| Caso | Herramienta usada | Correcto | ms |', '|---|---|---|---:|'
    ]
    lines += [f'| {name} | {", ".join(tools) or "—"} | {"Sí" if ok else "No"}{" ("+error+")" if error else ""} | {ms} |'
              for name, tools, ok, ms, error in results]
    report_file = ROOT / ('eval/reporte_demo.md' if COPILOT_MODE == 'demo' else 'eval/reporte.md')
    report_file.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return 0 if passed == len(cases) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8765')
    parser.add_argument('--casos', default=None, help='Ruta al archivo yaml de casos (ej. eval/casos_demo.yaml)')
    args = parser.parse_args()
    sys.exit(run(args.base_url, args.casos))
