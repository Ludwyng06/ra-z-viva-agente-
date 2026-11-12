/**
 * Raíz Viva Naturales — Animación Bio-Neuronal de Fondo
 * 
 * Efecto visual interactivo y bio-futurista:
 * - Esporas y semillas luminosas flotantes (tonos salvia y miel)
 * - Conexiones sinápticas/raíces que se entrelazan suavemente
 * - Brillo ambiental orgánico que respira a ritmo pausado
 * - Reacción sutil a los estados de voz (escuchando / hablando / procesando)
 * - 60 FPS con bajo consumo de CPU, soporte responsive y prefers-reduced-motion
 */
(function () {
  'use strict';

  // Configuración de paleta bio-futurista de Raíz Viva
  const PALETTE = {
    sage: [
      { r: 69, g: 120, b: 83 },   // Salvia vivo (#457853)
      { r: 99, g: 152, b: 114 },  // Salvia suave (#639872)
      { r: 42, g: 90, b: 56 }     // Hoja bosque (#2a5a38)
    ],
    honey: [
      { r: 217, g: 119, b: 6 },   // Ámbar miel (#d97706)
      { r: 245, g: 158, b: 11 },  // Miel dorada (#f59e0b)
      { r: 251, g: 191, b: 36 }   // Polen cálido (#fbbf24)
    ]
  };

  // Variables de control y estado
  let canvas = null;
  let ctx = null;
  let animationId = null;
  let isRunning = false;
  let lastTime = 0;

  let width = 0;
  let height = 0;
  let dpr = 1;

  let particles = [];
  let maxConnectionDistance = 120;
  let particleCount = 45;

  // Factores de animación dinámica
  let speedMultiplier = 1.0;
  let targetSpeedMultiplier = 1.0;
  let energyLevel = 0.0;
  let targetEnergyLevel = 0.0;

  // Detección de accesibilidad
  const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  let prefersReducedMotion = motionQuery.matches;

  motionQuery.addEventListener('change', (e) => {
    prefersReducedMotion = e.matches;
    if (prefersReducedMotion) {
      targetSpeedMultiplier = 0.1;
      speedMultiplier = 0.1;
    } else {
      targetSpeedMultiplier = 1.0;
    }
  });

  /**
   * Inicializa el canvas y los observadores cuando el DOM está listo
   */
  function init() {
    canvas = document.getElementById('bg-neural-canvas');
    if (!canvas) {
      canvas = document.createElement('canvas');
      canvas.id = 'bg-neural-canvas';
      canvas.setAttribute('aria-hidden', 'true');
      const app = document.querySelector('.app');
      if (app && app.parentNode) {
        app.parentNode.insertBefore(canvas, app);
      } else {
        document.body.prepend(canvas);
      }
    }

    try {
      ctx = canvas.getContext('2d', { alpha: true });
    } catch {
      return;
    }

    if (!ctx) return;

    handleResize();
    window.addEventListener('resize', debounce(handleResize, 150), { passive: true });

    setupStateObserver();
    setupVisibilityHandling();

    startAnimation();
  }

  /**
   * Ajusta el tamaño del canvas según la ventana física y el DPR
   */
  function handleResize() {
    if (!canvas || !ctx) return;

    width = window.innerWidth || document.documentElement.clientWidth || 800;
    height = window.innerHeight || document.documentElement.clientHeight || 600;
    dpr = Math.min(window.devicePixelRatio || 1, 2);

    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    canvas.style.width = width + 'px';
    canvas.style.height = height + 'px';

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);

    // Ajustar número de esporas según el tamaño de la pantalla
    if (width < 640) {
      particleCount = 30;
      maxConnectionDistance = 90;
    } else if (width < 1024) {
      particleCount = 45;
      maxConnectionDistance = 115;
    } else {
      particleCount = 65;
      maxConnectionDistance = 135;
    }

    createParticles();
  }

  /**
   * Genera las semillas / esporas iniciales
   */
  function createParticles() {
    particles = [];
    for (let i = 0; i < particleCount; i++) {
      const isHoney = Math.random() < 0.38;
      const paletteGroup = isHoney ? PALETTE.honey : PALETTE.sage;
      const color = paletteGroup[Math.floor(Math.random() * paletteGroup.length)];

      const speed = (0.12 + Math.random() * 0.22);
      const angle = Math.random() * Math.PI * 2;

      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        baseRadius: 1.3 + Math.random() * 1.8,
        radius: 1.3,
        color: color,
        isHoney: isHoney,
        pulseOffset: Math.random() * Math.PI * 2,
        pulseSpeed: 0.018 + Math.random() * 0.02,
        wobblePhase: Math.random() * Math.PI * 2,
        wobbleSpeed: 0.008 + Math.random() * 0.012,
        alpha: 0.4 + Math.random() * 0.45
      });
    }
  }

  /**
   * Observa cambios en el DOM para reaccionar a voz y estado del copiloto
   */
  function setupStateObserver() {
    const btnMic = document.getElementById('btn-hablar');
    const player = document.getElementById('player');
    const messages = document.getElementById('mensajes');
    const form = document.getElementById('form');

    let isListening = false;
    let isSpeaking = false;
    let isThinking = false;

    function evaluateState() {
      // 1. Escuchando: micrófono activo
      if (btnMic) {
        const hasRecClass = btnMic.classList.contains('rec');
        const isPressed = btnMic.getAttribute('aria-pressed') === 'true';
        isListening = hasRecClass || isPressed;
      }

      // 2. Hablando: audio o síntesis de voz en reproducción
      let audioPlaying = false;
      if (player && !player.paused && !player.ended && player.currentTime > 0) {
        audioPlaying = true;
      }
      let synthSpeaking = false;
      if (window.speechSynthesis && window.speechSynthesis.speaking) {
        synthSpeaking = true;
      }
      isSpeaking = audioPlaying || synthSpeaking;

      // 3. Pensando / procesando respuesta
      let hasTyping = false;
      if (messages && messages.querySelector('.typing')) {
        hasTyping = true;
      }
      const btnSend = document.getElementById('btn-enviar');
      const isSendDisabled = btnSend && btnSend.disabled && !isListening;
      isThinking = hasTyping || isSendDisabled;

      // Determinar objetivo de velocidad y energía
      if (prefersReducedMotion) {
        targetSpeedMultiplier = 0.1;
        targetEnergyLevel = 0.05;
      } else if (isListening) {
        // Al escuchar la voz del usuario: aceleración sutil y ondas receptivas
        targetSpeedMultiplier = 2.1;
        targetEnergyLevel = 0.9;
      } else if (isSpeaking) {
        // Al hablar el asistente: flujo rítmico armónico
        targetSpeedMultiplier = 1.7;
        targetEnergyLevel = 0.75;
      } else if (isThinking) {
        // Al procesar la respuesta: leve impulso cognitivo
        targetSpeedMultiplier = 1.35;
        targetEnergyLevel = 0.45;
      } else {
        // Estado de reposo orgánico
        targetSpeedMultiplier = 1.0;
        targetEnergyLevel = 0.0;
      }
    }

    // Observar mutaciones en el botón de micrófono
    if (btnMic && window.MutationObserver) {
      const micObserver = new MutationObserver(evaluateState);
      micObserver.observe(btnMic, { attributes: true, attributeFilter: ['class', 'aria-pressed'] });
    }

    // Observar mutaciones en el chat para detectar indicador de escritura
    if (messages && window.MutationObserver) {
      const msgObserver = new MutationObserver(evaluateState);
      msgObserver.observe(messages, { childList: true, subtree: true });
    }

    // Escuchar eventos de audio nativo del player
    if (player) {
      const audioEvents = ['play', 'playing', 'pause', 'ended', 'timeupdate'];
      audioEvents.forEach(evt => {
        player.addEventListener(evt, evaluateState, { passive: true });
      });
    }

    // Intervalo ligero para sincronizar síntesis de voz y estados transitorios
    setInterval(evaluateState, 350);
  }

  /**
   * Pausa y reanuda la animación cuando la pestaña cambia de visibilidad
   */
  function setupVisibilityHandling() {
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        stopAnimation();
      } else {
        lastTime = performance.now();
        startAnimation();
      }
    }, { passive: true });
  }

  function startAnimation() {
    if (!isRunning) {
      isRunning = true;
      lastTime = performance.now();
      animationId = requestAnimationFrame(renderLoop);
    }
  }

  function stopAnimation() {
    if (isRunning) {
      isRunning = false;
      if (animationId) {
        cancelAnimationFrame(animationId);
        animationId = null;
      }
    }
  }

  /**
   * Bucle principal de renderizado a 60 FPS
   */
  function renderLoop(currentTime) {
    if (!isRunning) return;

    const delta = Math.min((currentTime - lastTime) / 16.667, 2.5);
    lastTime = currentTime;

    // Transición suave (lerp) de velocidad y energía
    speedMultiplier += (targetSpeedMultiplier - speedMultiplier) * 0.05 * delta;
    energyLevel += (targetEnergyLevel - energyLevel) * 0.05 * delta;

    // Limpiar canvas preservando el fondo translúcido
    ctx.clearRect(0, 0, width, height);

    // Dibujar brillo ambiental que respira en el fondo
    drawAmbientGlow(currentTime);

    // Conectar partículas cercanas con líneas bio-neuronales
    drawConnections();

    // Actualizar y dibujar esporas luminosas
    drawParticles(delta, currentTime);

    animationId = requestAnimationFrame(renderLoop);
  }

  /**
   * Dibuja un brillo suave que respira orgánicamente de fondo
   */
  function drawAmbientGlow(time) {
    // Oscilación lenta y profunda (periodo de ~7 segundos)
    const breath = (Math.sin(time * 0.0009) + 1) * 0.5;
    const baseAlpha = 0.035 + breath * 0.025 + energyLevel * 0.03;

    // Foco de brillo salvia en esquina superior derecha
    const grad1 = ctx.createRadialGradient(
      width * 0.75, height * 0.2, 10,
      width * 0.75, height * 0.2, width * 0.45
    );
    grad1.addColorStop(0, `rgba(69, 120, 83, ${baseAlpha * 1.2})`);
    grad1.addColorStop(1, 'rgba(69, 120, 83, 0)');
    ctx.fillStyle = grad1;
    ctx.fillRect(0, 0, width, height);

    // Foco de brillo miel en esquina inferior izquierda
    const grad2 = ctx.createRadialGradient(
      width * 0.25, height * 0.85, 10,
      width * 0.25, height * 0.85, width * 0.4
    );
    grad2.addColorStop(0, `rgba(217, 119, 6, ${baseAlpha * 0.95})`);
    grad2.addColorStop(1, 'rgba(217, 119, 6, 0)');
    ctx.fillStyle = grad2;
    ctx.fillRect(0, 0, width, height);
  }

  /**
   * Conecta esporas cercanas con raíces luminosas sutiles
   */
  function drawConnections() {
    const currentMaxDist = maxConnectionDistance * (1 + energyLevel * 0.15);
    const maxDistSq = currentMaxDist * currentMaxDist;
    const len = particles.length;

    for (let i = 0; i < len; i++) {
      const p1 = particles[i];
      for (let j = i + 1; j < len; j++) {
        const p2 = particles[j];
        const dx = p2.x - p1.x;
        const dy = p2.y - p1.y;

        // Comprobación rápida en cuadrilátero antes de calcular distancia
        if (Math.abs(dx) > currentMaxDist || Math.abs(dy) > currentMaxDist) continue;

        const distSq = dx * dx + dy * dy;
        if (distSq < maxDistSq) {
          const dist = Math.sqrt(distSq);
          const proximity = 1 - (dist / currentMaxDist);
          const lineAlpha = proximity * (0.16 + energyLevel * 0.12);

          ctx.beginPath();
          ctx.moveTo(p1.x, p1.y);
          ctx.lineTo(p2.x, p2.y);

          // Color intermedio según afinidad de las esporas conectadas
          if (p1.isHoney && p2.isHoney) {
            ctx.strokeStyle = `rgba(217, 119, 6, ${lineAlpha})`;
          } else if (!p1.isHoney && !p2.isHoney) {
            ctx.strokeStyle = `rgba(69, 120, 83, ${lineAlpha})`;
          } else {
            // Conexión híbrida salvia-miel
            ctx.strokeStyle = `rgba(135, 137, 65, ${lineAlpha * 0.9})`;
          }

          ctx.lineWidth = 0.75 + proximity * 0.6;
          ctx.stroke();
        }
      }
    }
  }

  /**
   * Actualiza posiciones y dibuja cada espora luminosa
   */
  function drawParticles(delta, currentTime) {
    const currentSpeed = speedMultiplier;
    const len = particles.length;

    for (let i = 0; i < len; i++) {
      const p = particles[i];

      if (!prefersReducedMotion) {
        // Movimiento con suave oscilación de viento botánico
        p.wobblePhase += p.wobbleSpeed * delta;
        const wobbleX = Math.sin(p.wobblePhase) * 0.15;
        const wobbleY = Math.cos(p.wobblePhase) * 0.12;

        p.x += (p.vx + wobbleX) * currentSpeed * delta;
        p.y += (p.vy + wobbleY) * currentSpeed * delta;

        // Salida suave y reaparición en bordes opuestos
        const padding = 24;
        if (p.x < -padding) p.x = width + padding;
        else if (p.x > width + padding) p.x = -padding;

        if (p.y < -padding) p.y = height + padding;
        else if (p.y > height + padding) p.y = -padding;

        // Respiración de brillo y tamaño
        p.pulseOffset += p.pulseSpeed * (1 + energyLevel * 0.8) * delta;
      }

      const pulseFactor = (Math.sin(p.pulseOffset) + 1) * 0.5;
      const currentRadius = p.baseRadius * (0.85 + pulseFactor * 0.35 + energyLevel * 0.25);
      const currentAlpha = Math.min(1, (p.alpha * (0.7 + pulseFactor * 0.4) + energyLevel * 0.25));

      const { r, g, b } = p.color;

      // Halo sutil de bioluminiscencia alrededor de la semilla
      const haloRadius = currentRadius * (2.8 + energyLevel * 1.2);
      const haloGrad = ctx.createRadialGradient(
        p.x, p.y, currentRadius * 0.5,
        p.x, p.y, haloRadius
      );
      haloGrad.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${currentAlpha * 0.4})`);
      haloGrad.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);

      ctx.beginPath();
      ctx.arc(p.x, p.y, haloRadius, 0, Math.PI * 2);
      ctx.fillStyle = haloGrad;
      ctx.fill();

      // Núcleo brillante de la espora
      ctx.beginPath();
      ctx.arc(p.x, p.y, currentRadius, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${currentAlpha})`;
      ctx.fill();
    }
  }

  /**
   * Utilidad de retardo (debounce) para eventos de ventana
   */
  function debounce(fn, delay) {
    let timer = null;
    return function () {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, arguments), delay);
    };
  }

  // Inicializar al cargar el documento
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
