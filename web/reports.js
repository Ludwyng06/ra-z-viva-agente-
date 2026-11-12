/* Reportes aislados del compositor y del manejo de caché. */
window.renderSalesReport = function (bubble, data) {
  let report = data.reporteVentas;
  for (const call of data.toolCalls || []) {
    if (call.name !== 'reporte_ventas' || !call.ok) continue;
    try { report = JSON.parse(call.summary); } catch { /* Sin datos estructurados. */ }
  }
  if (!report || report.tipo !== 'reporte_ventas' || !Array.isArray(report.serie_diaria)) return;
  const money = n => new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 }).format(n || 0);
  const make = (tag, text) => { const e = document.createElement(tag); if (text != null) e.textContent = text; return e; };
  const card = make('section');
  card.className = 'sales-report';
  card.setAttribute('aria-label', 'Reporte de ventas');
  card.addEventListener('click', e => e.stopPropagation());
  card.addEventListener('keydown', e => e.stopPropagation());
  card.append(make('h3', `Ventas · ${report.desde} a ${report.hasta}`));
  const totals = report.totales;
  card.append(make('p', `${totals.pedidos} pedidos · ${money(totals.ventas_cop)} COP`));
  card.append(make('p', `Ticket promedio: ${money(totals.ticket_promedio_cop)} COP`));
  card.append(make('p', `${totals.pagados} pagados por anticipado · ${totals.contra_entrega} contra entrega`));
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 480 180');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Ventas diarias en COP; detalle en la tabla siguiente');
  const title = document.createElementNS(ns, 'title'); title.textContent = 'Ventas diarias (COP)'; svg.append(title);
  const series = report.serie_diaria;
  const maximum = Math.max(1, ...series.map(r => r.ventas_cop));
  const step = 460 / series.length;
  series.forEach((r, i) => {
    const height = 125 * r.ventas_cop / maximum;
    const rect = document.createElementNS(ns, 'rect');
    for (const [key, val] of Object.entries({ x: 10 + i * step, y: 145 - height, width: Math.max(2, step - 3), height, rx: 2, fill: '#39724b' })) rect.setAttribute(key, val);
    const tooltip = document.createElementNS(ns, 'title'); tooltip.textContent = `${r.fecha}: ${r.pedidos} pedidos, ${money(r.ventas_cop)} COP`; rect.append(tooltip); svg.append(rect);
    if (series.length <= 7 || i % 5 === 0 || i === series.length - 1) {
      const label = document.createElementNS(ns, 'text'); label.setAttribute('x', 10 + i * step); label.setAttribute('y', 165); label.setAttribute('font-size', 11); label.setAttribute('fill', '#28452f'); label.textContent = r.fecha.slice(8); svg.append(label);
    }
  });
  card.append(make('p', `Ventas diarias · máximo ${money(maximum > 1 ? maximum : 0)} COP`), svg);
  const details = make('details'); details.append(make('summary', 'Ver tabla diaria y top productos'));
  const table = make('table'); const caption = make('caption', 'Ventas por día (COP)'); table.append(caption);
  const head = make('thead'); const hr = make('tr'); ['Día', 'Pedidos', 'Ventas COP'].forEach(t => hr.append(make('th', t))); head.append(hr); table.append(head);
  const body = make('tbody');
  series.forEach(r => { const row = make('tr'); [r.fecha.slice(5), r.pedidos, money(r.ventas_cop)].forEach(t => row.append(make('td', t))); body.append(row); });
  table.append(body); details.append(table, make('h4', 'Top productos'));
  const products = make('ul');
  for (const p of report.top_productos || []) products.append(make('li', `${p.nombre}: ${p.unidades} unidades · ${money(p.ventas_cop)} COP`));
  if (!products.children.length) products.append(make('li', 'Sin ventas en este periodo.'));
  details.append(products, make('p', report.criterio)); card.append(details);
  const button = make('button', 'Descargar PDF'); button.type = 'button';
  button.addEventListener('click', async () => {
    button.disabled = true; button.textContent = 'Preparando PDF…';
    try {
      const res = await fetch(`/api/reportes/ventas/${encodeURIComponent(report.periodo)}.pdf`);
      if (!res.ok) throw new Error('No se pudo descargar');
      const url = URL.createObjectURL(await res.blob());
      const a = make('a'); a.href = url; a.download = `ventas-${report.periodo}.pdf`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
      button.textContent = 'Descargar PDF';
    } catch { button.textContent = 'Reintentar descarga PDF'; }
    finally { button.disabled = false; }
  });
  card.append(button, make('small', 'El PDF consulta los datos actuales del periodo.'));
  bubble.append(card);
};
