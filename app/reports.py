"""Reportes agregados de la tienda demo; nunca consulta datos personales."""
from datetime import datetime, time, timedelta
from io import BytesIO
from typing import Literal
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .config import BOGOTA, COPILOT_MODE
from .db import demo_db

router = APIRouter(prefix='/api/reportes')


def reporte_ventas(periodo='mes'):
    if periodo not in ('hoy', 'semana', 'mes'):
        raise ValueError('El periodo debe ser hoy, semana o mes.')
    today = datetime.now(BOGOTA).date()
    start = today if periodo == 'hoy' else (today - timedelta(days=6) if periodo == 'semana' else today.replace(day=1))
    ini = datetime.combine(start, time.min, BOGOTA)
    fin = datetime.combine(today + timedelta(days=1), time.min, BOGOTA)
    with demo_db() as conn:
        daily = conn.execute("""
            SELECT (fecha AT TIME ZONE 'America/Bogota')::date AS fecha,
                   count(*)::int AS pedidos,
                   count(*) FILTER (WHERE estado != 'cancelado')::int AS activos,
                   coalesce(sum(total) FILTER (WHERE estado != 'cancelado'), 0)::bigint AS ventas_cop,
                   count(*) FILTER (WHERE metodo_pago = 'pagado')::int AS pagados,
                   count(*) FILTER (WHERE metodo_pago = 'contra_entrega')::int AS contra_entrega,
                   coalesce(sum(total) FILTER (WHERE metodo_pago = 'pagado' AND estado != 'cancelado'), 0)::bigint AS pagados_cop,
                   coalesce(sum(total) FILTER (WHERE metodo_pago = 'contra_entrega' AND estado != 'cancelado'), 0)::bigint AS contra_entrega_cop
            FROM tienda.pedidos WHERE fecha >= %s AND fecha < %s
            GROUP BY 1 ORDER BY 1
        """, (ini, fin)).fetchall()
        top = conn.execute("""
            SELECT pr.nombre, sum(i.cantidad)::int AS unidades, sum(i.total)::bigint AS ventas_cop
            FROM tienda.pedido_items i JOIN tienda.pedidos p ON p.id = i.pedido_id
            JOIN tienda.productos pr ON pr.id = i.producto_id
            WHERE p.fecha >= %s AND p.fecha < %s AND p.estado != 'cancelado'
            GROUP BY pr.id, pr.nombre ORDER BY unidades DESC, ventas_cop DESC, pr.nombre LIMIT 5
        """, (ini, fin)).fetchall()
    indexed = {r['fecha']: r for r in daily}
    series = []
    day = start
    keys = ('pedidos', 'activos', 'ventas_cop', 'pagados', 'contra_entrega', 'pagados_cop', 'contra_entrega_cop')
    while day <= today:
        row = indexed.get(day, {})
        series.append({'fecha': day.isoformat(), **{k: int(row.get(k, 0)) for k in keys}})
        day += timedelta(days=1)
    totals = {k: sum(r[k] for r in series) for k in keys}
    totals['cancelados'] = totals['pedidos'] - totals['activos']
    totals['ticket_promedio_cop'] = round(totals['ventas_cop'] / totals['activos']) if totals['activos'] else 0
    money = lambda n: f'{n:,.0f}'.replace(',', '.')
    return {'tipo': 'reporte_ventas', 'periodo': periodo, 'desde': start.isoformat(), 'hasta': today.isoformat(),
            'serie_diaria': series, 'totales': totals, 'top_productos': top,
            'criterio': 'Pedidos y métodos de pago incluyen cancelados; ventas y ticket excluyen cancelados e incluyen flete. Semana: últimos 7 días. Top productos sin flete.',
            'resumen_hablado': f"Reporte de ventas: {totals['pedidos']} pedidos y {money(totals['ventas_cop'])} COP; ticket promedio {money(totals['ticket_promedio_cop'])} COP. {totals['pagados']} pagados por anticipado y {totals['contra_entrega']} contra entrega."}


REPORT_SPEC = {'type': 'function', 'function': {
    'name': 'reporte_ventas', 'description': 'Reporte de ventas con gráfica diaria, totales, ticket, pagos y top productos.',
    'parameters': {'type': 'object', 'properties': {'periodo': {'type': 'string', 'enum': ['hoy', 'semana', 'mes']}},
                   'required': ['periodo'], 'additionalProperties': False}}}


def render_pdf(report):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.graphics.shapes import Drawing, Rect, String, Line
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    stream = BytesIO()
    styles = getSampleStyleSheet()
    green = colors.HexColor('#28563b')
    money = lambda n: f'{n:,.0f}'.replace(',', '.')
    story = [Paragraph('Raíz Viva Naturales', styles['Title']),
             Paragraph(f"Reporte de ventas · {report['desde']} a {report['hasta']}", styles['Heading2'])]
    t = report['totales']
    story += [Paragraph(f"{t['pedidos']} pedidos · Ventas: {money(t['ventas_cop'])} COP · Ticket promedio: {money(t['ticket_promedio_cop'])} COP", styles['Normal']),
              Paragraph(f"Pagados por anticipado: {t['pagados']} ({money(t['pagados_cop'])} COP). Contra entrega: {t['contra_entrega']} ({money(t['contra_entrega_cop'])} COP).", styles['Normal']), Spacer(1, 14)]
    series = report['serie_diaria']
    chart = Drawing(480, 160)
    chart.add(String(0, 146, 'Ventas diarias (COP)', fontSize=10, fillColor=green))
    maximum = max(1, *(r['ventas_cop'] for r in series))
    step = 450 / len(series)
    chart.add(Line(15, 22, 470, 22, strokeColor=green))
    for i, r in enumerate(series):
        height = 100 * r['ventas_cop'] / maximum
        chart.add(Rect(18 + i * step, 23, max(2, step - 4), height, fillColor=green, strokeColor=None))
        if len(series) <= 10 or i % 5 == 0 or i == len(series) - 1:
            chart.add(String(18 + i * step, 8, r['fecha'][8:], fontSize=8))
    chart.add(String(15, 128, f'Máximo diario: {money(maximum if maximum > 1 else 0)} COP', fontSize=8))
    story.append(chart)
    rows = [['Fecha', 'Pedidos', 'Ventas COP']] + [[r['fecha'], str(r['pedidos']), money(r['ventas_cop'])] for r in series]
    rows.append(['TOTAL', str(t['pedidos']), money(t['ventas_cop'])])
    def table(rows, widths):
        obj = Table(rows, colWidths=widths, repeatRows=1, hAlign='LEFT')
        obj.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), green), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                                 ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 9),
                                 ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f2f6ef'), colors.white]),
                                 ('BOTTOMPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, -1), 6)]))
        return obj
    story.extend([table(rows, [210, 100, 170]), Spacer(1, 16), Paragraph('Top productos', styles['Heading2'])])
    products = [[Paragraph(escape(r['nombre']), styles['Normal']), str(r['unidades']), money(r['ventas_cop'])] for r in report['top_productos']]
    story.append(table([['Producto', 'Unidades', 'Ventas COP']] + (products or [['Sin ventas', '0', '0']]), [290, 70, 120]))
    story.extend([Spacer(1, 12), Paragraph(report['criterio'], styles['Normal'])])
    SimpleDocTemplate(stream, title='Reporte de ventas - Raíz Viva Naturales', rightMargin=45, leftMargin=45).build(story)
    return stream.getvalue()


@router.get('/ventas/{periodo}.pdf')
def download_report(periodo: Literal['hoy', 'semana', 'mes']):
    if COPILOT_MODE != 'demo':
        raise HTTPException(404, 'Reporte disponible en la tienda demo.')
    return Response(render_pdf(reporte_ventas(periodo)), media_type='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="ventas-{periodo}.pdf"', 'Cache-Control': 'no-store'})
