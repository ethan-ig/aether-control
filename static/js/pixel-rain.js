(() => {
  const STYLE_ID = 'aether-pixel-rain-style';
  const CANVAS_ID = 'aetherPixelRain';

  if (!document.getElementById(STYLE_ID)) {
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      #${CANVAS_ID} {
        position: fixed;
        inset: 0;
        width: 100vw;
        height: 100vh;
        display: block;
        z-index: 4;
        opacity: 0;
        pointer-events: none;
        image-rendering: pixelated;
        transition: opacity .7s ease;
      }
      body.weather-rain #${CANVAS_ID} { opacity: .72; }
      body.weather-storm #${CANVAS_ID} { opacity: .88; }

      /* Canvas owns precipitation. Keep the existing storm lightning layer. */
      html body.pixel-rain-engine.weather-rain::before,
      html body.pixel-rain-engine.weather-rain::after,
      html body.pixel-rain-engine.weather-storm::before {
        opacity: 0 !important;
        animation: none !important;
      }
    `;
    document.head.appendChild(style);
  }

  let canvas = document.getElementById(CANVAS_ID);
  if (!canvas) {
    canvas = document.createElement('canvas');
    canvas.id = CANVAS_ID;
    canvas.setAttribute('aria-hidden', 'true');
    document.body.appendChild(canvas);
  }
  document.body.classList.add('pixel-rain-engine');

  const ctx = canvas.getContext('2d', {alpha: true});
  if (!ctx) return;

  let width = 0;
  let height = 0;
  let dpr = 1;
  let columns = [];
  let last = performance.now();
  let raf = 0;
  let lastDraw = 0;
  const FRAME_MS = 1000 / 45;

  function active() {
    return document.body.classList.contains('weather-rain') ||
           document.body.classList.contains('weather-storm');
  }

  function rebuild() {
    dpr = Math.min(window.devicePixelRatio || 1, 1.25);
    width = Math.max(1, window.innerWidth);
    height = Math.max(1, window.innerHeight);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.imageSmoothingEnabled = false;

    const gap = width <= 900 ? 10 : 11;
    const count = Math.ceil(width / gap) + 8;
    columns = Array.from({length: count}, (_, index) => ({
      x: (index - 4) * gap + (Math.random() * 4 - 2),
      y: Math.random() * height,
      speed: 300 + Math.random() * 390,
      trail: 13 + Math.floor(Math.random() * 30),
      width: Math.random() < .22 ? 2 : 1,
      alpha: .18 + Math.random() * .42,
      phase: Math.random() * Math.PI * 2,
      wobble: .4 + Math.random() * 1.1,
      hot: Math.random() < .15
    }));
  }

  function resetDrop(drop) {
    drop.y = -Math.random() * height * .45 - 16;
    drop.speed = 300 + Math.random() * 390;
    drop.trail = 13 + Math.floor(Math.random() * 30);
    drop.alpha = .18 + Math.random() * .42;
    drop.hot = Math.random() < .15;
  }

  function draw(now, dt) {
    ctx.clearRect(0, 0, width, height);
    if (!active()) return;

    const storm = document.body.classList.contains('weather-storm');
    const slant = storm ? .20 : .16;

    for (const drop of columns) {
      drop.y += drop.speed * dt * (storm ? 1.18 : 1);
      if (drop.y - drop.trail > height + 30) resetDrop(drop);

      const wave = Math.sin(now * .0014 + drop.phase) * drop.wobble;
      const x = Math.round(drop.x + wave);
      const y = Math.round(drop.y);
      const len = drop.trail;
      const dx = Math.round(len * slant);

      const grad = ctx.createLinearGradient(x - dx, y - len, x, y);
      grad.addColorStop(0, 'rgba(80,151,215,0)');
      grad.addColorStop(.48, `rgba(91,174,238,${drop.alpha * .45})`);
      grad.addColorStop(.84, `rgba(150,211,255,${drop.alpha})`);
      grad.addColorStop(
        1,
        drop.hot && storm
          ? `rgba(222,240,255,${Math.min(.9, drop.alpha + .22)})`
          : `rgba(183,224,255,${Math.min(.82, drop.alpha + .12)})`
      );
      ctx.strokeStyle = grad;
      ctx.lineWidth = drop.width;
      ctx.beginPath();
      ctx.moveTo(x - dx, y - len);
      ctx.lineTo(x, y);
      ctx.stroke();

      if (drop.hot) {
        ctx.fillStyle = storm ? 'rgba(221,240,255,.58)' : 'rgba(170,218,255,.42)';
        ctx.fillRect(x - drop.width, y - 2, drop.width + 1, 3);
      }
    }
  }

  function frame(now) {
    const elapsed = now - lastDraw;
    if (elapsed >= FRAME_MS) {
      const dt = Math.min((now - last) / 1000, .045);
      last = now;
      lastDraw = now;
      draw(now, dt);
    }
    raf = requestAnimationFrame(frame);
  }

  rebuild();
  addEventListener('resize', rebuild, {passive: true});
  document.addEventListener('visibilitychange', () => {
    last = performance.now();
    lastDraw = last;
  });
  raf = requestAnimationFrame(frame);
})();
