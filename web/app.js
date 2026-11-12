(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const appContainer = $('app-container');
  const msgs = $('mensajes'), form = $('form'), input = $('mensaje');
  const btnMic = $('btn-hablar'), btnSend = $('btn-enviar'), swVoz = $('sw-voz');
  const player = $('player'), estadoVoz = $('estado-voz');

  // Elementos de la barra lateral e historial
  const historialPanel = $('historial');
  const historialBackdrop = $('historial-backdrop');
  const trazaBackdrop = $('traza-backdrop');
  const historialLista = $('historial-lista');
  const historialBusqueda = $('historial-busqueda');
  const historialVacio = $('historial-vacio');
  const historialSinResultados = $('historial-sin-resultados');
  const btnNuevoChat = $('btn-nuevo-chat');
  const btnNueva = $('btn-nueva');
  const btnSidebarToggle = $('btn-sidebar-toggle');
  const btnSidebarCollapse = $('btn-sidebar-collapse');
  const btnTrazaToggle = $('btn-traza-toggle');
  const btnTrazaClose = $('btn-traza-close');
  const currentChatTitle = $('current-chat-title');

  const EJEMPLOS_DEMO = [
    '¿Cuántos pedidos van este mes?',
    '¿Qué producto se vende más?',
    'Cotiza un envío de 2 mieles a Pasto',
    'Crea un pedido para Laura Gómez en Medellín: 2 tés verdes, contra entrega',
    '¿Qué novedades tengo abiertas?',
    '¿Qué productos se están agotando?',
  ];

  let appConfig = {
    mode: 'demo',
    appName: 'Agente de Raíz Viva Naturales',
    storeName: 'Raíz Viva Naturales',
    examples: EJEMPLOS_DEMO,
  };

  let conversationId = null;
  let viewGeneration = 0;
  let busy = false;
  let loadingConversation = false;
  let microphoneStarting = false;
  let recordingContext = null;
  let resolvingActionId = null;
  let voicePlaybackGeneration = 0;
  let trazaFocusReturn = null;
  let recorder = null, chunks = [], stream = null;
  let selectedEl = null;
  const trazas = new WeakMap();
  let pendingAction = null;

  // Estado del historial
  let conversaciones = [];
  let filtroBusqueda = '';
  let menuAbiertoEl = null;
  // El contrato actual no distingue títulos automáticos de renombres manuales.
  const renamedTitles = new Map();
  try {
    const saved = JSON.parse(localStorage.getItem('copiloto.renamedTitles') || '{}');
    Object.entries(saved || {}).forEach(([id, title]) => {
      if (typeof title === 'string') renamedTitles.set(id, title);
    });
  } catch {}

  function guardarRenombres() {
    try { localStorage.setItem('copiloto.renamedTitles', JSON.stringify(Object.fromEntries(renamedTitles))); } catch {}
  }

  function tituloSuperior(id, title) {
    return id && renamedTitles.get(id) === title ? title : (appConfig.storeName || 'Raíz Viva Naturales');
  }

  // <dialog> mantiene el foco dentro del modal y vuelve inerte el fondo.
  function abrirModal({ title, text, value, accept = 'Aceptar', danger = false, notice = false, returnFocus }) {
    return new Promise((resolve) => {
      const previousFocus = returnFocus || document.activeElement;
      const dialog = el('dialog', 'app-dialog');
      dialog.setAttribute('aria-labelledby', 'app-dialog-title');
      dialog.setAttribute('aria-describedby', 'app-dialog-description');
      const box = el('form', 'app-dialog-form');
      const heading = el('h2', null, title);
      heading.id = 'app-dialog-title';
      const description = el('p', null, text);
      description.id = 'app-dialog-description';
      box.append(heading, description);
      let field;
      if (value !== undefined) {
        const label = el('label', null, 'Nombre de la conversación');
        label.htmlFor = 'app-dialog-input';
        field = el('input');
        field.id = 'app-dialog-input';
        field.value = value;
        field.maxLength = 150;
        field.required = true;
        box.append(label, field);
      }
      const actions = el('div', 'app-dialog-actions');
      const cancel = el('button', 'dialog-cancel', 'Cancelar');
      cancel.type = 'button';
      const submit = el('button', danger ? 'dialog-accept danger' : 'dialog-accept', accept);
      submit.type = 'submit';
      if (!notice) actions.append(cancel);
      actions.append(submit);
      box.append(actions);
      dialog.append(box);
      document.body.append(dialog);
      let result = null;
      cancel.addEventListener('click', () => dialog.close());
      dialog.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          dialog.close();
          return;
        }
        if (event.key !== 'Tab') return;
        const focusable = Array.from(dialog.querySelectorAll('input, button:not([disabled])'));
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      });
      box.addEventListener('submit', (event) => {
        event.preventDefault();
        if (field && !field.value.trim()) { field.focus(); return; }
        result = field ? field.value.trim() : true;
        dialog.close();
      });
      let backdropDown = false;
      const isOutside = (event) => {
        const rect = dialog.getBoundingClientRect();
        return event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
      };
      dialog.addEventListener('pointerdown', (event) => { backdropDown = isOutside(event); });
      dialog.addEventListener('click', (event) => {
        if (backdropDown && isOutside(event)) dialog.close();
        backdropDown = false;
      });
      dialog.addEventListener('close', () => {
        dialog.remove();
        const target = previousFocus?.isConnected ? previousFocus : input;
        target?.focus();
        resolve(result);
      }, { once: true });
      dialog.showModal();
      (field || (danger ? cancel : submit)).focus();
      field?.select();
    });
  }

  function mostrarAviso(title, text, returnFocus) {
    return abrirModal({ title, text, notice: true, returnFocus });
  }

  // ---------- util ----------
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const ms = (n) => (n == null ? '—' : n >= 1000 ? (n / 1000).toFixed(1).replace('.', ',') + ' s' : Math.round(n) + ' ms');
  const scrollDown = () => {
    const scrollCont = $('chat-scroll-container') || msgs;
    scrollCont.scrollTop = scrollCont.scrollHeight;
  };
  const setVoz = (t) => { if (estadoVoz) estadoVoz.textContent = t || ''; };
  const formatCOP = (n) => {
    if (n == null || isNaN(n)) return '';
    return new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 }).format(n);
  };

  async function errorApi(response) {
    try {
      const body = await response.json();
      if (typeof body.detail === 'string' && body.detail.trim()) return body.detail;
      if (Array.isArray(body.detail)) {
        const messages = body.detail.map((item) => {
          if (typeof item === 'string') return item;
          if (!item || typeof item.msg !== 'string') return '';
          const location = Array.isArray(item.loc) ? item.loc.filter((part) => part !== 'body').join('.') : '';
          return location ? `${location}: ${item.msg}` : item.msg;
        }).filter(Boolean);
        if (messages.length) return messages.join('; ');
      }
    } catch {}
    return `La solicitud falló (HTTP ${response.status}).`;
  }

  function mensajeDeError(error, operacion) {
    if (error instanceof TypeError && /fetch/i.test(error.message)) {
      return `No hay conexión con el servidor para ${operacion}.`;
    }
    return error && error.message ? error.message : `No se pudo ${operacion}.`;
  }

  function evaluarSeguridad(toolCalls) {
    const guard = (toolCalls || []).find((call) => call.name === 'guardia_seguridad');
    const summary = typeof guard?.summary === 'string' ? guard.summary.trim() : '';
    if (/^bloqueado\b/i.test(summary)) {
      return {
        state: 'blocked',
        label: 'Bloqueada',
        detail: summary.replace(/^bloqueado\s*:?\s*/i, '') || 'El backend no incluyó un motivo público.',
      };
    }
    if (/^(advertencia|advertid[oa]|precaución)\b/i.test(summary)) {
      return { state: 'warning', label: 'Advertencia', detail: summary };
    }
    if (/^permitid[oa]\s*:/i.test(summary)) {
      return {
        state: 'allowed',
        label: 'Permitida',
        detail: summary.replace(/^permitid[oa]\s*:\s*/i, '') || 'El backend reportó permiso explícito.',
      };
    }
    return {
      state: 'unknown',
      label: 'Desconocida',
      detail: guard
        ? 'El registro existe, pero no contiene un resultado explícito.'
        : 'El backend no reportó un resultado explícito de seguridad para este turno.',
    };
  }

  // Clasificación de fechas en grupos (Hoy, Ayer, Últimos 7 días, Anteriores)
  function categorizarFecha(dateStr) {
    if (!dateStr) return 'Anteriores';
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return 'Anteriores';
    const now = new Date();
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const startOfYesterday = startOfToday - 86400000;
    const startOf7Days = startOfToday - (6 * 86400000);
    const itemTime = d.getTime();

    if (itemTime >= startOfToday) return 'Hoy';
    if (itemTime >= startOfYesterday) return 'Ayer';
    if (itemTime >= startOf7Days) return 'Últimos 7 días';
    return 'Anteriores';
  }

  // Formato relativo de fecha
  function formatFechaRelativa(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    const diffSec = Math.floor((now.getTime() - d.getTime()) / 1000);
    if (diffSec < 45) return 'hace un momento';
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `hace ${diffMin} min`;
    const diffHours = Math.floor(diffMin / 60);
    if (diffHours < 24 && now.getDate() === d.getDate() && now.getMonth() === d.getMonth() && now.getFullYear() === d.getFullYear()) {
      return `hace ${diffHours} h`;
    }
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (yesterday.getDate() === d.getDate() && yesterday.getMonth() === d.getMonth() && yesterday.getFullYear() === d.getFullYear()) {
      return 'ayer';
    }
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 7) return `hace ${diffDays} días`;
    if (diffDays < 30) return `hace ${Math.max(1, Math.floor(diffDays / 7))} sem`;
    return d.toLocaleDateString('es-CO', { day: 'numeric', month: 'short' });
  }

  // ---------- configuración dinámica ----------
  async function cargarConfig() {
    try {
      const r = await fetch('/api/config');
      if (r.ok) {
        const d = await r.json();
        appConfig = {
          mode: d.mode || 'demo',
          appName: d.appName || (d.mode === 'demo' ? 'Raíz Viva Naturales' : 'Copiloto de Atlas'),
          storeName: d.storeName || (d.mode === 'demo' ? 'Raíz Viva Naturales' : 'Tienda Atlas'),
          examples: Array.isArray(d.examples) && d.examples.length ? d.examples : EJEMPLOS_DEMO,
        };
      }
    } catch {
      // Mantiene valores por defecto
    }
    aplicarConfig();
  }

  function aplicarConfig() {
    const isDemo = appConfig.mode === 'demo';
    document.title = isDemo ? appConfig.appName : `${appConfig.appName} — ${appConfig.storeName}`;
    const titleEl = $('app-title'); if (titleEl) titleEl.textContent = isDemo ? appConfig.storeName : appConfig.appName;
    const subEl = $('app-subtitle'); if (subEl) subEl.textContent = isDemo ? 'Asistente de pedidos' : `Asistente para ${appConfig.storeName}`;
    const labelDb = $('label-db'); if (labelDb) labelDb.textContent = isDemo ? 'Tienda' : 'Atlas';

    const emptyTitle = $('empty-title');
    if (emptyTitle) emptyTitle.textContent = isDemo ? 'Hola, ¿en qué te ayudo hoy?' : 'Hola, ¿en qué te ayudo hoy?';
    const emptySub = $('empty-subtitle');
    if (emptySub) emptySub.textContent = isDemo
      ? 'Pregúntame por pedidos, novedades, inventario, cotizaciones o envíos. Puedes escribir o hablar.'
      : 'Pregúntame por pedidos, novedades, stock o cómo usar Atlas. Puedes escribir o hablar.';

    renderEjemplos(appConfig.examples);
  }

  function renderEjemplos(list) {
    const grid = $('ejemplos');
    if (!grid) return;
    grid.replaceChildren();
    const items = (list && list.length) ? list.slice(0, 6) : EJEMPLOS_DEMO;
    items.forEach((t) => {
      const card = el('button', 'example-card');
      card.type = 'button';
      const textSpan = el('span', 'example-card-text', t);
      card.appendChild(textSpan);
      card.innerHTML += '<svg class="example-card-arrow" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M8.59 16.59L13.17 12 8.59 7.41 10 6l6 6-6 6-1.41-1.41z"/></svg>';
      card.addEventListener('click', () => enviarTexto(t));
      grid.appendChild(card);
    });
  }

  // ---------- estado de servicios ----------
  async function cargarEstado() {
    try {
      const r = await fetch('/api/health');
      const h = await r.json();
      document.querySelectorAll('#estado li').forEach((li) => {
        const ok = !!h[li.dataset.k];
        li.classList.toggle('ok', ok);
        li.classList.toggle('bad', !ok);
        li.title = ok ? 'Disponible' : 'No disponible';
      });
    } catch {
      document.querySelectorAll('#estado li').forEach((li) => {
        li.classList.remove('ok');
        li.classList.add('bad');
        li.title = 'Sin conexión con la API';
      });
    }
  }

  // ---------- historial de conversaciones ----------
  async function cargarHistorial() {
    try {
      const r = await fetch('/api/conversations');
      if (!r.ok) return;
      conversaciones = await r.json();
      renderHistorial();
    } catch (err) {
      console.error('Error al cargar historial:', err);
    }
  }

  function marcarChatActivo(id) {
    if (!historialLista) return;
    historialLista.querySelectorAll('.history-item').forEach((li) => {
      li.classList.toggle('active', li.dataset.id === id);
    });
    // Actualizar título en topbar
    if (currentChatTitle) {
      const chat = conversaciones.find((c) => c.id === id);
      currentChatTitle.textContent = tituloSuperior(id, chat?.title);
    }
  }

  function cerrarMenuAbierto() {
    if (menuAbiertoEl) {
      menuAbiertoEl.remove();
      menuAbiertoEl = null;
    }
    document.querySelectorAll('.btn-chat-menu.menu-open').forEach((b) => b.classList.remove('menu-open'));
  }

  document.addEventListener('click', (e) => {
    if (menuAbiertoEl && !menuAbiertoEl.contains(e.target)) {
      cerrarMenuAbierto();
    }
  });

  function renderHistorial() {
    if (!historialLista) return;
    cerrarMenuAbierto();
    const q = (filtroBusqueda || '').toLowerCase().trim();
    const filtradas = q
      ? conversaciones.filter((c) => (c.title || '').toLowerCase().includes(q))
      : conversaciones;

    if (!conversaciones.length) {
      historialLista.replaceChildren();
      if (historialVacio) historialVacio.hidden = false;
      if (historialSinResultados) historialSinResultados.hidden = true;
      return;
    }

    if (historialVacio) historialVacio.hidden = true;

    if (!filtradas.length) {
      historialLista.replaceChildren();
      if (historialSinResultados) historialSinResultados.hidden = false;
      return;
    }

    if (historialSinResultados) historialSinResultados.hidden = true;
    historialLista.replaceChildren();

    // Agrupar por fecha
    const ordenGrupos = ['Hoy', 'Ayer', 'Últimos 7 días', 'Anteriores'];
    const grupos = {
      'Hoy': [],
      'Ayer': [],
      'Últimos 7 días': [],
      'Anteriores': [],
    };

    filtradas.forEach((chat) => {
      const g = categorizarFecha(chat.updatedAt);
      if (grupos[g]) grupos[g].push(chat);
      else grupos['Anteriores'].push(chat);
    });

    ordenGrupos.forEach((nombreGrupo) => {
      const items = grupos[nombreGrupo];
      if (!items || !items.length) return;

      const headerLi = el('li', 'history-group-header', nombreGrupo);
      historialLista.appendChild(headerLi);

      items.forEach((chat) => {
        const li = el('li', 'history-item');
        li.dataset.id = chat.id;
        if (chat.id === conversationId) {
          li.classList.add('active');
        }

        // Contenedor principal de título y metadatos
        const main = el('div', 'history-item-main');
        main.setAttribute('role', 'button');
        main.setAttribute('tabindex', '0');

        const titleRow = el('div', 'history-item-title-row');
        const titleSpan = el('span', 'history-item-title', chat.title || 'Conversación');
        titleSpan.title = chat.title || 'Conversación';
        titleRow.appendChild(titleSpan);

        if (chat.messageCount != null && chat.messageCount > 0) {
          const countSpan = el('span', 'history-item-badge', String(chat.messageCount));
          countSpan.title = `${chat.messageCount} mensajes`;
          titleRow.appendChild(countSpan);
        }
        main.appendChild(titleRow);

        const metaRow = el('div', 'history-item-meta');
        const timeSpan = el('span', 'history-item-time', formatFechaRelativa(chat.updatedAt));
        metaRow.appendChild(timeSpan);
        main.appendChild(metaRow);

        // Clic para cargar conversación
        main.addEventListener('click', () => {
          cargarConversacion(chat.id);
        });
        main.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            cargarConversacion(chat.id);
          }
        });

        // Menú de opciones de tres puntos (•••)
        const menuWrap = el('div', 'history-item-menu-wrap');
        const btnMenu = el('button', 'btn-chat-menu');
        btnMenu.type = 'button';
        btnMenu.title = 'Opciones';
        btnMenu.setAttribute('aria-label', `Opciones para ${chat.title || 'conversación'}`);
        btnMenu.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z"/></svg>';

        btnMenu.addEventListener('click', (e) => {
          e.stopPropagation();
          const yaAbierto = menuAbiertoEl && menuAbiertoEl.parentElement === menuWrap;
          cerrarMenuAbierto();
          if (yaAbierto) return;

          btnMenu.classList.add('menu-open');
          const dropdown = el('div', 'history-dropdown-menu');

          // Botón Renombrar
          const itemRename = el('button', 'dropdown-item');
          itemRename.type = 'button';
          itemRename.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path fill="currentColor" d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a.996.996 0 0 0 0-1.41l-2.34-2.34a.996.996 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg><span>Renombrar</span>';
          itemRename.addEventListener('click', (ev) => {
            ev.stopPropagation();
            cerrarMenuAbierto();
            activarEdicionTitulo(li, chat);
          });

          // Botón Eliminar
          const itemDelete = el('button', 'dropdown-item delete');
          itemDelete.type = 'button';
          itemDelete.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path fill="currentColor" d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg><span>Eliminar</span>';
          itemDelete.addEventListener('click', async (ev) => {
            ev.stopPropagation();
            cerrarMenuAbierto();
            const conf = await abrirModal({
              title: 'Eliminar conversación',
              text: `¿Eliminar "${chat.title || 'Conversación'}"? No se puede deshacer.`,
              accept: 'Eliminar', danger: true, returnFocus: btnMenu,
            });
            if (conf) {
              eliminarConversacion(chat.id);
            }
          });

          dropdown.append(itemRename, itemDelete);
          menuWrap.appendChild(dropdown);
          menuAbiertoEl = dropdown;
        });

        menuWrap.appendChild(btnMenu);
        li.append(main, menuWrap);
        historialLista.appendChild(li);
      });
    });
  }

  async function activarEdicionTitulo(li, chat) {
    const returnFocus = li.querySelector('.btn-chat-menu');
    const nuevo = await abrirModal({
      title: 'Renombrar conversación', text: 'Elige un nombre para encontrarla fácilmente.',
      value: chat.title || '', accept: 'Guardar', returnFocus,
    });
    if (!nuevo) return;
    try {
      const r = await fetch('/api/conversations/' + encodeURIComponent(chat.id), {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: nuevo }),
      });
      if (!r.ok) throw new Error(await errorApi(r));
      const data = await r.json();
      chat.title = data.title;
      chat.updatedAt = data.updatedAt || chat.updatedAt;
      renamedTitles.set(chat.id, chat.title);
      guardarRenombres();
      if (conversationId === chat.id && currentChatTitle) {
        currentChatTitle.textContent = tituloSuperior(chat.id, chat.title);
      }
      renderHistorial();
      historialLista.querySelector(`[data-id="${CSS.escape(chat.id)}"] .btn-chat-menu`)?.focus();
    } catch (err) {
      await mostrarAviso('No se pudo renombrar', mensajeDeError(err, 'renombrar la conversación'), returnFocus);
    }
  }

  async function eliminarConversacion(id) {
    try {
      const r = await fetch('/api/conversations/' + encodeURIComponent(id), {
        method: 'DELETE',
      });
      if (!r.ok) throw new Error(await errorApi(r));
      renamedTitles.delete(id);
      guardarRenombres();
      conversaciones = conversaciones.filter((c) => c.id !== id);
      if (conversationId === id) {
        nuevaConversacion();
      }
      renderHistorial();
    } catch (err) {
      await mostrarAviso('No se pudo eliminar', mensajeDeError(err, 'eliminar la conversación'));
    }
  }

  // ---------- mensajes ----------
  function quitarVacio() {
    const v = $('vacio');
    if (v) v.remove();
  }

  function addUser(text, viaVoz) {
    quitarVacio();
    const m = el('div', 'msg user');
    if (viaVoz) {
      const voiceIcon = el('span', null, '🎤 ');
      m.appendChild(voiceIcon);
    }
    const textNode = document.createTextNode(text);
    m.appendChild(textNode);
    msgs.appendChild(m);
    scrollDown();
    return m;
  }

  function addError(text) {
    quitarVacio();
    const m = el('div', 'msg error', text);
    msgs.appendChild(m);
    scrollDown();
  }

  function addTyping() {
    quitarVacio();
    const m = el('div', 'msg assistant typing');
    m.append('Pensando', el('span', 'd', '.'), el('span', 'd', '.'), el('span', 'd', '.'));
    msgs.appendChild(m);
    scrollDown();
    return m;
  }

  function addAssistant(data) {
    const m = el('div', 'msg assistant');
    m.title = 'Toca para ver trazabilidad técnica';
    m.tabIndex = 0;
    m.setAttribute('role', 'button');
    m.setAttribute('aria-controls', 'traza');
    m.setAttribute('aria-label', 'Respuesta de Raiz Viva. Abrir trazabilidad');
    m.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        mostrarTraza(m);
        abrirTraza();
      }
    });

    // Marca tipográfica del asistente.
    const header = el('div', 'msg-assistant-header');
    header.appendChild(el('span', 'msg-assistant-name', 'Raiz Viva'));
    m.appendChild(header);

    // Texto de respuesta
    const content = el('div', null, data.answer || '(sin respuesta)');
    m.appendChild(content);

    if (data.draft && (data.draft.direccion || data.draft.telefono)) {
      const card = el('div', 'pending-details');
      card.appendChild(el('strong', null, 'Datos de entrega del borrador'));
      if (data.draft.direccion) card.appendChild(el('p', null, `Dirección: ${data.draft.direccion}`));
      if (data.draft.telefono) card.appendChild(el('p', null, `Teléfono: ${data.draft.telefono}`));
      m.appendChild(card);
    }
    window.renderSalesReport?.(m, data);
    trazas.set(m, data);
    m.addEventListener('click', () => {
      mostrarTraza(m);
      abrirTraza();
    });

    msgs.appendChild(m);
    scrollDown();
    mostrarTraza(m, false); // Carga datos en traza sin forzar apertura inmediata
    return m;
  }

  // ---------- trazabilidad ----------
  function abrirTraza() {
    const traza = $('traza');
    if (!traza || traza.classList.contains('open')) return;
    trazaFocusReturn = document.activeElement;
    if (traza.contains(trazaFocusReturn)) trazaFocusReturn = btnTrazaToggle;
    if (traza) traza.classList.add('open');
    traza.removeAttribute('inert');
    traza.setAttribute('aria-hidden', 'false');
    if (btnTrazaToggle) btnTrazaToggle.setAttribute('aria-expanded', 'true');
    if (trazaBackdrop) trazaBackdrop.hidden = false;
    requestAnimationFrame(() => {
      if (traza.classList.contains('open')) btnTrazaClose?.focus();
    });
  }

  function cerrarTraza() {
    const traza = $('traza');
    const estabaAbierta = !!traza && traza.classList.contains('open');
    if (traza) {
      traza.classList.remove('open');
      traza.setAttribute('aria-hidden', 'true');
      traza.setAttribute('inert', '');
    }
    if (btnTrazaToggle) btnTrazaToggle.setAttribute('aria-expanded', 'false');
    if (trazaBackdrop) trazaBackdrop.hidden = true;
    if (estabaAbierta) {
      const target = trazaFocusReturn;
      trazaFocusReturn = null;
      requestAnimationFrame(() => {
        const focusTarget = target && target.isConnected && !target.closest('[inert]') ? target : btnTrazaToggle;
        focusTarget?.focus();
      });
    }
  }

  function limpiarTraza() {
    selectedEl = null;
    $('traza-vacia').hidden = false;
    $('traza-body').hidden = true;
  }

  function mostrarTraza(m, abrir = false) {
    const d = trazas.get(m);
    if (!d) return;
    if (selectedEl) selectedEl.classList.remove('sel');
    selectedEl = m;
    m.classList.add('sel');
    $('traza-vacia').hidden = true;
    $('traza-body').hidden = false;

    const transcript = d.transcript || '';
    $('trace-transcript-section').hidden = !transcript;
    $('t-transcript').textContent = transcript;

    const securityEvaluation = evaluarSeguridad(d.toolCalls);
    const security = $('t-security');
    security.dataset.state = securityEvaluation.state;
    security.replaceChildren(el('strong', null, securityEvaluation.label), document.createTextNode(` — ${securityEvaluation.detail}`));

    const tools = $('t-tools');
    tools.replaceChildren();
    const regularTools = (d.toolCalls || []).filter((tool) => tool.name !== 'guardia_seguridad');
    $('trace-tools-section').hidden = !regularTools.length;
    regularTools.forEach((t) => {
      const li = el('li', 'tool');
      const h = el('div', 'h');
      const icon = t.ok ? '✔ ' : '✖ ';
      const statusClass = t.ok ? 'ok' : 'ko';
      h.append(el('b', null, t.name), el('span', statusClass, icon + ms(t.ms)));
      li.appendChild(h);
      if (t.args && Object.keys(t.args).length) li.appendChild(el('pre', null, JSON.stringify(t.args)));
      if (t.summary) li.appendChild(el('p', 'sum', t.summary));
      tools.appendChild(li);
    });

    const src = $('t-sources');
    src.replaceChildren();
    const sources = d.sources || [];
    $('trace-sources-section').hidden = !sources.length;
    sources.forEach((s) => {
      const li = el('li');
      li.append(s.title || s.slug, ' ', el('small', null, s.slug ? '(' + s.slug + ')' : ''));
      src.appendChild(li);
    });

    const t = d.timings || {};
    const times = $('t-times');
    times.replaceChildren();
    const timeEntries = [['Voz a texto (STT)', t.sttMs], ['Modelo (LLM)', t.llmMs], ['Herramientas', t.toolsMs], ['Texto a voz (TTS)', t.ttsMs], ['Total', t.totalMs]]
      .filter(([, value]) => typeof value === 'number' && Number.isFinite(value));
    $('trace-times-section').hidden = !timeEntries.length;
    timeEntries.forEach(([key, value]) => {
      const dt = el('dt', null, key), dd = el('dd', key === 'Total' ? 'total' : null, ms(value));
      if (key === 'Total') dt.className = 'total';
      times.append(dt, dd);
    });
    $('trace-no-details').hidden = !!(transcript || regularTools.length || sources.length || timeEntries.length);

    if (abrir) abrirTraza();
  }

  // ---------- acción pendiente de confirmación ----------
  function mostrarPendiente(p) {
    const box = $('pendiente');
    box.replaceChildren();
    pendingAction = p;
    if (!p) {
      box.hidden = true;
      return;
    }
    box.hidden = false;

    const isDemo = appConfig.mode === 'demo';

    box.appendChild(el('h3', null, '⚡ Acción pendiente de confirmación'));
    const desc = el('p', null, p.description || 'Se requiere confirmación para proceder.');
    if (p.orderNumber) {
      desc.textContent += ` (Pedido ${p.orderNumber})`;
    }
    box.appendChild(desc);
    if (p.payload?.direccion) box.appendChild(el('p', null, `Dirección: ${p.payload.direccion}`));
    if (p.payload?.telefono) {
      const digits = String(p.payload.telefono).replace(/\D/g, '');
      box.appendChild(el('p', null, `Teléfono: ***${digits.slice(-4)}`));
    }

    const hasItems = (Array.isArray(p.products) && p.products.length) || (Array.isArray(p.items) && p.items.length);
    const hasFinancials = p.subtotal != null || p.flete != null || p.shipping != null || p.total != null || p.summary;

    if (hasItems || hasFinancials) {
      const detailsBox = el('div', 'pending-details');
      if (typeof p.summary === 'string' && p.summary.trim()) {
        detailsBox.appendChild(el('p', null, p.summary));
      }

      const items = Array.isArray(p.products) ? p.products : Array.isArray(p.items) ? p.items : null;
      if (items && items.length) {
        const table = el('table');
        const thead = el('thead');
        const trh = el('tr');
        trh.append(el('th', null, 'Producto'), el('th', 'num', 'Cant.'), el('th', 'num', 'Valor'));
        thead.appendChild(trh);
        table.appendChild(thead);

        const tbody = el('tbody');
        items.forEach((it) => {
          const tr = el('tr');
          const name = it.nombre || it.name || it.sku || 'Producto';
          const cant = it.cantidad || it.qty || 1;
          const precio = it.precio != null ? it.precio : it.precio_venta != null ? it.precio_venta : it.unit_price;
          tr.append(
            el('td', null, name),
            el('td', 'num', String(cant)),
            el('td', 'num', precio != null ? (typeof precio === 'number' ? formatCOP(precio) : String(precio)) : '—')
          );
          tbody.appendChild(tr);
        });

        const sub = p.subtotal != null ? p.subtotal : null;
        const fle = p.flete != null ? p.flete : p.shipping != null ? p.shipping : null;
        const tot = p.total != null ? p.total : null;

        if (sub != null) {
          const trSub = el('tr');
          trSub.append(el('td', null, 'Subtotal:'), el('td', null, ''), el('td', 'num', typeof sub === 'number' ? formatCOP(sub) : String(sub)));
          tbody.appendChild(trSub);
        }
        if (fle != null) {
          const trFle = el('tr');
          trFle.append(el('td', null, 'Flete de envío:'), el('td', null, ''), el('td', 'num', typeof fle === 'number' ? formatCOP(fle) : String(fle)));
          tbody.appendChild(trFle);
        }
        if (tot != null) {
          const trTot = el('tr', 'total-row');
          trTot.append(el('td', null, 'Total a recaudar:'), el('td', null, ''), el('td', 'num', typeof tot === 'number' ? formatCOP(tot) : String(tot)));
          tbody.appendChild(trTot);
        }

        table.appendChild(tbody);
        detailsBox.appendChild(table);
      } else if (p.subtotal != null || p.flete != null || p.shipping != null || p.total != null) {
        const table = el('table');
        const tbody = el('tbody');
        const sub = p.subtotal != null ? p.subtotal : null;
        const fle = p.flete != null ? p.flete : p.shipping != null ? p.shipping : null;
        const tot = p.total != null ? p.total : null;
        if (sub != null) {
          const trSub = el('tr');
          trSub.append(el('td', null, 'Subtotal:'), el('td', 'num', typeof sub === 'number' ? formatCOP(sub) : String(sub)));
          tbody.appendChild(trSub);
        }
        if (fle != null) {
          const trFle = el('tr');
          trFle.append(el('td', null, 'Flete:'), el('td', 'num', typeof fle === 'number' ? formatCOP(fle) : String(fle)));
          tbody.appendChild(trFle);
        }
        if (tot != null) {
          const trTot = el('tr', 'total-row');
          trTot.append(el('td', null, 'Total en destino:'), el('td', 'num', typeof tot === 'number' ? formatCOP(tot) : String(tot)));
          tbody.appendChild(trTot);
        }
        table.appendChild(tbody);
        detailsBox.appendChild(table);
      }
      box.appendChild(detailsBox);
    }

    const row = el('div', 'row');
    const okText = isDemo ? 'Confirmar' : 'Confirmar (simulado)';
    const ok = el('button', 'btn-ok', okText); ok.type = 'button';
    const no = el('button', 'btn-no', 'Cancelar'); no.type = 'button';
    const hintText = isDemo ? 'Acción real en la base de la tienda.' : 'Simulación: no modifica Atlas.';
    row.append(ok, no, el('span', 'sim', hintText));
    box.appendChild(row);

    ok.addEventListener('click', () => resolver(p.id, 'confirm', row));
    no.addEventListener('click', () => resolver(p.id, 'cancel', row));
    box.scrollIntoView({ block: 'nearest' });
  }

  async function resolver(id, op, row) {
    if (resolvingActionId) return;
    resolvingActionId = id;
    const requestGeneration = viewGeneration;
    const requestConversationId = conversationId;
    row.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    try {
      const r = await fetch('/api/actions/' + encodeURIComponent(id) + '/' + op, { method: 'POST' });
      if (!r.ok) throw new Error(await errorApi(r));
      const d = await r.json();
      if (requestGeneration !== viewGeneration || requestConversationId !== conversationId || pendingAction?.id !== id) {
        cargarHistorial();
        return;
      }
      mostrarPendiente(null);
      addAssistant({ answer: d.message || d.status, toolCalls: [], timings: {} });
      cargarHistorial();
    } catch (e) {
      if (requestGeneration === viewGeneration && requestConversationId === conversationId) {
        row.querySelectorAll('button').forEach((b) => { b.disabled = false; });
        addError('No se pudo completar la acción: ' + mensajeDeError(e, 'completar la acción'));
      }
    } finally {
      resolvingActionId = null;
    }
  }

  // ---------- audio de respuesta ----------
  function hablarNavegador(texto) {
    if (!('speechSynthesis' in window) || !texto) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(texto);
    const voces = speechSynthesis.getVoices();
    const v = voces.find((x) => /es-CO/i.test(x.lang)) || voces.find((x) => /^es/i.test(x.lang));
    if (v) { u.voice = v; u.lang = v.lang; } else u.lang = 'es-CO';
    speechSynthesis.speak(u);
  }

  function invalidarSalidaVoz() {
    voicePlaybackGeneration += 1;
    if ('speechSynthesis' in window) speechSynthesis.cancel();
    player.pause();
    player.currentTime = 0;
    player.removeAttribute('src');
    player.load();
  }

  function iniciarSalidaVoz() {
    invalidarSalidaVoz();
    return voicePlaybackGeneration;
  }

  function salidaVozVigente(generation) {
    return swVoz.checked && generation === voicePlaybackGeneration;
  }

  async function reproducir(data, generation) {
    if (!salidaVozVigente(generation)) return;
    if (data.audioUrl) {
      try {
        player.src = data.audioUrl;
        await player.play();
        if (!salidaVozVigente(generation)) player.pause();
        return;
      } catch {
        if (!salidaVozVigente(generation)) return;
      }
    }
    if (salidaVozVigente(generation)) hablarNavegador(data.answer);
  }

  async function pedirTts(data) {
    if (!swVoz.checked || !data.answer) return;
    const generation = iniciarSalidaVoz();
    try {
      const r = await fetch('/api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: data.answer }) });
      const d = r.ok ? await r.json() : {};
      if (salidaVozVigente(generation)) {
        await reproducir({ audioUrl: d.audioUrl, answer: data.answer }, generation);
      }
    } catch {
      if (salidaVozVigente(generation)) hablarNavegador(data.answer);
    }
  }

  // ---------- carga y cambio de conversación ----------
  async function cargarConversacion(id) {
    if (busy || resolvingActionId) return;
    const requestGeneration = ++viewGeneration;
    loadingConversation = true;
    input.disabled = true;
    btnSend.disabled = true;
    btnMic.disabled = !recorder;
    mostrarPendiente(null);
    cerrarTraza();
    limpiarTraza();
    conversationId = id;
    try {
      localStorage.setItem('copiloto.conversationId', id);
    } catch {}
    marcarChatActivo(id);
    cerrarHistorialMobile();

    quitarVacio();
    msgs.replaceChildren();
    const loading = el('div', 'msg assistant typing');
    loading.append('Cargando conversación', el('span', 'd', '.'), el('span', 'd', '.'), el('span', 'd', '.'));
    msgs.appendChild(loading);

    try {
      const r = await fetch('/api/conversations/' + encodeURIComponent(id));
      if (requestGeneration !== viewGeneration) return;
      if (!r.ok) {
        if (r.status === 404) {
          try { localStorage.removeItem('copiloto.conversationId'); } catch {}
          nuevaConversacion();
          addError('La conversación ya no existe.');
          await cargarHistorial();
          return;
        }
        throw new Error(await errorApi(r));
      }
      const data = await r.json();
      if (requestGeneration !== viewGeneration) return;
      if (data.conversationId !== id) {
        throw new Error('El servidor devolvió una conversación distinta a la solicitada.');
      }
      loading.remove();
      msgs.replaceChildren();
      selectedEl = null;

      if (currentChatTitle) {
        currentChatTitle.textContent = tituloSuperior(id, data.title);
      }

      const messages = data.messages || [];
      const toolCalls = data.toolCalls || [];

      if (!messages.length) {
        crearPantallaBienvenida(data.title || 'Conversación vacía', 'Escribe tu consulta o habla para continuar esta conversación.');
        $('traza-vacia').hidden = false;
        $('traza-body').hidden = true;
        mostrarPendiente(null);
        return;
      }

      let currentTurnUser = null;
      let lastAssistantEl = null;

      messages.forEach((m) => {
        if (m.role === 'user') {
          currentTurnUser = m;
          addUser(m.content, false);
        } else if (m.role === 'assistant') {
          const userMsgId = currentTurnUser ? currentTurnUser.id : null;
          const turnTools = toolCalls.filter((tc) => tc.message_id && userMsgId && String(tc.message_id) === String(userMsgId));
          const callsForMessage = turnTools.length ? turnTools : (messages.filter((x) => x.role === 'assistant').length === 1 ? toolCalls : []);

          const assistantData = {
            answer: m.content,
            toolCalls: callsForMessage,
            transcript: currentTurnUser ? currentTurnUser.content : 'Mensaje escrito',
            sources: [],
            timings: {
              toolsMs: callsForMessage.reduce((acc, c) => acc + (c.ms || 0), 0),
              totalMs: callsForMessage.reduce((acc, c) => acc + (c.ms || 0), 0),
            },
          };
          lastAssistantEl = addAssistant(assistantData);
        }
      });

      if (lastAssistantEl) {
        mostrarTraza(lastAssistantEl, false);
      } else {
        $('traza-vacia').hidden = false;
        $('traza-body').hidden = true;
      }

      if (data.pendingAction) {
        mostrarPendiente(data.pendingAction);
      } else {
        mostrarPendiente(null);
      }
    } catch (err) {
      if (requestGeneration === viewGeneration) {
        loading.remove();
        addError('No pude cargar la conversación: ' + mensajeDeError(err, 'cargar la conversación'));
      }
    } finally {
      if (requestGeneration === viewGeneration) {
        loadingConversation = false;
        input.disabled = busy;
        btnSend.disabled = busy;
        btnMic.disabled = busy && !recorder;
        scrollDown();
        input.focus();
      }
    }
  }

  function crearPantallaBienvenida(titulo, subtitulo) {
    quitarVacio();
    const v = el('div', 'empty');
    v.id = 'vacio';

    const wordmark = el('div', 'welcome-wordmark', appConfig.storeName);
    wordmark.append(el('span', 'welcome-brand-subtitle', 'Asistente de pedidos'));

    const titleH2 = el('h2', null, titulo || 'Hola, ¿en qué te ayudo hoy?');
    titleH2.id = 'empty-title';

    const subP = el('p', null, subtitulo || 'Pregúntame por pedidos, novedades, inventario, cotizaciones o envíos. Puedes escribir o hablar.');
    subP.id = 'empty-subtitle';

    const gridContainer = el('div', 'examples-grid-container');
    gridContainer.setAttribute('aria-label', 'Preguntas sugeridas');
    const gridTitle = el('div', 'examples-grid-title', 'Preguntas sugeridas:');
    const grid = el('div', 'examples-grid');
    grid.id = 'ejemplos';
    gridContainer.append(gridTitle, grid);

    v.append(wordmark, titleH2, subP, gridContainer);
    msgs.appendChild(v);
    renderEjemplos(appConfig.examples);
  }

  function nuevaConversacion() {
    viewGeneration += 1;
    loadingConversation = false;
    if (!busy) {
      input.disabled = false;
      btnSend.disabled = false;
      btnMic.disabled = false;
    }
    conversationId = null;
    try {
      localStorage.removeItem('copiloto.conversationId');
    } catch {}
    marcarChatActivo(null);
    mostrarPendiente(null);
    selectedEl = null;
    cerrarHistorialMobile();
    cerrarTraza();
    limpiarTraza();

    if (currentChatTitle) {
      currentChatTitle.textContent = appConfig.storeName || 'Raíz Viva Naturales';
    }

    msgs.replaceChildren();
    crearPantallaBienvenida();

    $('traza-vacia').hidden = false;
    $('traza-body').hidden = true;
    input.value = '';
    autosize();
    input.focus();
  }

  // ---------- envío de mensajes ----------
  function ocupado(b) {
    busy = b;
    btnSend.disabled = b || loadingConversation;
    btnMic.disabled = loadingConversation ? !recorder : b && !recorder;
    input.disabled = b || loadingConversation;
  }

  function manejarRespuesta(data, viaVoz, requestedConversationId, requestGeneration) {
    if (requestGeneration !== viewGeneration) return false;
    if (typeof data.conversationId !== 'string' || !data.conversationId) {
      throw new Error('El servidor no devolvió el identificador de esta conversación.');
    }
    if (requestedConversationId && data.conversationId !== requestedConversationId) {
      throw new Error('El servidor respondió con otra conversación. No se mostró esa respuesta.');
    }
    conversationId = data.conversationId;
    try {
      localStorage.setItem('copiloto.conversationId', conversationId);
    } catch {}
    addAssistant(data);
    mostrarPendiente(data.pendingAction || null);
    if (viaVoz) reproducir(data, iniciarSalidaVoz());
    else pedirTts(data);

    // Actualizar historial en segundo plano
    cargarHistorial().then(() => {
      marcarChatActivo(conversationId);
    });
    return true;
  }

  async function enviarTexto(texto) {
    texto = (texto || '').trim();
    if (!texto || busy || loadingConversation || resolvingActionId) return;
    const requestGeneration = viewGeneration;
    const requestedConversationId = conversationId;
    if ('speechSynthesis' in window) speechSynthesis.cancel();
    player.pause();
    addUser(texto, false);
    input.value = '';
    autosize();
    ocupado(true);
    const typing = addTyping();
    try {
      const r = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversationId: requestedConversationId || undefined, text: texto }),
      });
      if (!r.ok) throw new Error(await errorApi(r));
      const data = await r.json();
      typing.remove();
      if (requestGeneration !== viewGeneration) {
        await cargarHistorial();
        return;
      }
      if (!data.transcript) data.transcript = texto;
      manejarRespuesta(data, false, requestedConversationId, requestGeneration);
    } catch (e) {
      typing.remove();
      if (requestGeneration === viewGeneration) {
        addError('No se pudo enviar el mensaje: ' + mensajeDeError(e, 'enviar el mensaje'));
      }
    } finally {
      ocupado(false);
      if (requestGeneration === viewGeneration) input.focus();
    }
  }

  async function enviarAudio(blob, requestedConversationId, requestGeneration) {
    ocupado(true);
    const typing = requestGeneration === viewGeneration ? addTyping() : null;
    if (requestGeneration === viewGeneration) setVoz('Transcribiendo…');
    try {
      const fd = new FormData();
      const ext = blob.type.includes('ogg') ? 'ogg' : blob.type.includes('wav') ? 'wav' : 'webm';
      fd.append('audio', blob, 'voz.' + ext);
      if (requestedConversationId) fd.append('conversationId', requestedConversationId);
      const r = await fetch('/api/voice', { method: 'POST', body: fd });
      if (!r.ok) throw new Error(await errorApi(r));
      const data = await r.json();
      if (typing) typing.remove();
      if (requestGeneration !== viewGeneration) {
        await cargarHistorial();
        return;
      }
      if (typeof data.conversationId !== 'string' || !data.conversationId) {
        throw new Error('El servidor no devolvió el identificador de esta conversación.');
      }
      if (requestedConversationId && data.conversationId !== requestedConversationId) {
        throw new Error('El servidor respondió con otra conversación. No se mostró esa respuesta.');
      }
      addUser(data.transcript || '(no se entendió el audio)', true);
      manejarRespuesta(data, true, requestedConversationId, requestGeneration);
    } catch (e) {
      if (typing) typing.remove();
      if (requestGeneration === viewGeneration) {
        addError('No se pudo procesar el audio: ' + mensajeDeError(e, 'procesar el audio'));
      }
    } finally {
      if (requestGeneration === viewGeneration) setVoz('');
      ocupado(false);
    }
  }

  // ---------- grabación de audio ----------
  async function iniciarGrabacion() {
    if (recorder || busy || loadingConversation || microphoneStarting || resolvingActionId) return;
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      addError('Tu navegador no permite grabar audio. Usa Chrome o Edge en localhost.');
      return;
    }
    microphoneStarting = true;
    const context = { conversationId, viewGeneration };
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      microphoneStarting = false;
      if (context.viewGeneration === viewGeneration) addError('No hay permiso para usar el micrófono.');
      return;
    }
    microphoneStarting = false;
    recordingContext = context;
    if ('speechSynthesis' in window) speechSynthesis.cancel();
    player.pause();
    chunks = [];
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'].find((t) => MediaRecorder.isTypeSupported(t));
    recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = () => {
      const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
      stream.getTracks().forEach((t) => t.stop());
      recorder = null;
      stream = null;
      btnMic.classList.remove('rec');
      btnMic.setAttribute('aria-pressed', 'false');
      const micText = $('mic-texto');
      if (micText) micText.textContent = 'Hablar';
      const requestContext = recordingContext;
      recordingContext = null;
      if (blob.size > 1500 && requestContext) {
        enviarAudio(blob, requestContext.conversationId, requestContext.viewGeneration);
      }
      else {
        if (requestContext && requestContext.viewGeneration === viewGeneration) {
          setVoz('Audio muy corto, intenta de nuevo.');
        }
        ocupado(false);
      }
    };
    recorder.start();
    btnMic.classList.add('rec');
    btnMic.setAttribute('aria-pressed', 'true');
    const micText = $('mic-texto');
    if (micText) micText.textContent = 'Escuchando…';
    setVoz('Grabando: suelta o toca de nuevo para enviar.');
  }

  function detenerGrabacion() {
    if (recorder && recorder.state !== 'inactive') recorder.stop();
  }

  let downAt = 0;
  btnMic.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    if (recorder) {
      detenerGrabacion();
      downAt = 0;
      return;
    }
    downAt = Date.now();
    iniciarGrabacion();
  });
  btnMic.addEventListener('pointerup', () => {
    if (recorder && downAt && Date.now() - downAt > 600) detenerGrabacion();
    downAt = 0;
  });
  btnMic.addEventListener('keydown', (e) => {
    if (e.key === ' ' || e.key === 'Enter') {
      e.preventDefault();
      if (e.repeat) return;
      recorder ? detenerGrabacion() : iniciarGrabacion();
    }
  });

  // ---------- eventos UI ----------
  function actualizarPlaceholder() {
    if (window.innerWidth <= 480) {
      input.placeholder = 'Escribe un mensaje o habla…';
    } else {
      input.placeholder = 'Escribe tu mensaje o pulsa el micrófono…';
    }
  }
  window.addEventListener('resize', actualizarPlaceholder);
  actualizarPlaceholder();

  function autosize() {
    input.style.height = 'auto';
    const h = Math.min(Math.max(input.scrollHeight, 24), 150);
    input.style.height = h + 'px';
  }
  input.addEventListener('input', autosize);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    enviarTexto(input.value);
  });

  // Botones nuevo chat
  if (btnNueva) btnNueva.addEventListener('click', nuevaConversacion);
  if (btnNuevoChat) btnNuevoChat.addEventListener('click', nuevaConversacion);

  // Búsqueda en historial
  if (historialBusqueda) {
    historialBusqueda.addEventListener('input', (e) => {
      filtroBusqueda = e.target.value || '';
      renderHistorial();
    });
  }

  // Barra lateral: Plegar y desplegar
  function abrirSidebar() {
    if (window.innerWidth <= 768) {
      if (historialPanel) historialPanel.classList.add('open');
      if (historialBackdrop) historialBackdrop.hidden = false;
      if (btnSidebarToggle) btnSidebarToggle.setAttribute('aria-expanded', 'true');
    } else {
      if (appContainer) appContainer.classList.remove('sidebar-collapsed');
      try { localStorage.setItem('copiloto.sidebar', 'open'); } catch {}
    }
  }

  function cerrarSidebar() {
    if (window.innerWidth <= 768) {
      if (historialPanel) historialPanel.classList.remove('open');
      if (historialBackdrop) historialBackdrop.hidden = true;
      if (btnSidebarToggle) btnSidebarToggle.setAttribute('aria-expanded', 'false');
    } else {
      if (appContainer) appContainer.classList.add('sidebar-collapsed');
      try { localStorage.setItem('copiloto.sidebar', 'collapsed'); } catch {}
    }
  }

  function cerrarHistorialMobile() {
    if (window.innerWidth <= 768) {
      if (historialPanel) historialPanel.classList.remove('open');
      if (historialBackdrop) historialBackdrop.hidden = true;
      if (btnSidebarToggle) btnSidebarToggle.setAttribute('aria-expanded', 'false');
    }
  }

  if (btnSidebarCollapse) {
    btnSidebarCollapse.addEventListener('click', cerrarSidebar);
  }
  if (btnSidebarToggle) {
    btnSidebarToggle.addEventListener('click', () => {
      if (window.innerWidth <= 768) {
        const isOpen = historialPanel && historialPanel.classList.contains('open');
        if (isOpen) cerrarSidebar(); else abrirSidebar();
      } else {
        const isCollapsed = appContainer && appContainer.classList.contains('sidebar-collapsed');
        if (isCollapsed) abrirSidebar(); else cerrarSidebar();
      }
    });
  }
  if (historialBackdrop) {
    historialBackdrop.addEventListener('click', cerrarHistorialMobile);
  }

  // Trazabilidad
  if (btnTrazaToggle) {
    btnTrazaToggle.addEventListener('click', () => {
      const traza = $('traza');
      const isOpen = traza && traza.classList.contains('open');
      if (isOpen) cerrarTraza(); else abrirTraza();
    });
  }
  if (btnTrazaClose) {
    btnTrazaClose.addEventListener('click', cerrarTraza);
  }
  if (trazaBackdrop) {
    trazaBackdrop.addEventListener('click', cerrarTraza);
  }
  document.addEventListener('keydown', (event) => {
    const traza = $('traza');
    if (!traza || !traza.classList.contains('open')) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      cerrarTraza();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = Array.from(traza.querySelectorAll('button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])'));
    if (!focusable.length) {
      event.preventDefault();
      btnTrazaClose?.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!traza.contains(document.activeElement)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    } else if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  // Interruptor de respuesta en voz (icono altavoz en composer)
  const btnVoz = $('btn-voz');
  function sincronizarBotonVoz() {
    if (!btnVoz || !swVoz) return;
    const activo = !!swVoz.checked;
    btnVoz.classList.toggle('active', activo);
    btnVoz.setAttribute('aria-pressed', String(activo));
    const iconOn = btnVoz.querySelector('.icon-speaker-on');
    const iconOff = btnVoz.querySelector('.icon-speaker-off');
    if (iconOn) iconOn.hidden = !activo;
    if (iconOff) iconOff.hidden = activo;
    btnVoz.title = activo
      ? 'Respuesta en voz activada (toca para silenciar)'
      : 'Respuesta en voz silenciada (toca para activar)';
  }

  if (btnVoz) {
    btnVoz.addEventListener('click', () => {
      swVoz.checked = !swVoz.checked;
      swVoz.dispatchEvent(new Event('change'));
      sincronizarBotonVoz();
    });
  }

  swVoz.addEventListener('change', () => {
    try { localStorage.setItem('copiloto.voz', swVoz.checked ? '1' : '0'); } catch {}
    sincronizarBotonVoz();
    if (!swVoz.checked) {
      invalidarSalidaVoz();
    }
  });
  try {
    if (localStorage.getItem('copiloto.voz') === '0') swVoz.checked = false;
  } catch {}
  sincronizarBotonVoz();

  // Preferencia de barra lateral en escritorio
  try {
    if (window.innerWidth > 768 && localStorage.getItem('copiloto.sidebar') === 'collapsed') {
      if (appContainer) appContainer.classList.add('sidebar-collapsed');
    }
  } catch {}

  if ('speechSynthesis' in window) speechSynthesis.getVoices();

  // Inicialización
  (async () => {
    await cargarConfig();
    await cargarEstado();
    setInterval(cargarEstado, 20000);

    await cargarHistorial();

    const savedId = (() => {
      try { return localStorage.getItem('copiloto.conversationId'); } catch { return null; }
    })();

    if (savedId) {
      await cargarConversacion(savedId);
    } else {
      nuevaConversacion();
    }
  })();
})();
