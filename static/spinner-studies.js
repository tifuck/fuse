/* Preview-only artwork. No DOM writes, SVG IDs, timers, or application hooks. */
(() => {
  'use strict';

  const svg = (id, body) => `<svg class="motion-art motion-${id}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="1em" height="1em" aria-hidden="true" focusable="false">${body}</svg>`;
  const study = (id, name, note, body) => ({ id, name, note, html: svg(id, body) });

  window.FuseSpinnerStudies = [
    study('paperfold', 'Paperfold', 'Four scored leaves take turns folding into a small architectural jewel.', `
      <path class="motion-c" d="M32 7 57 32 32 57 7 32Z"/>
      <g class="motion-fold motion-fold-n"><path class="motion-fill-a" d="M32 8 32 32 10 30Z"/><path class="motion-b" d="M32 10 21 25 32 32"/></g>
      <g class="motion-fold motion-fold-e"><path class="motion-fill-b" d="M56 32 32 32 34 10Z"/><path class="motion-c" d="M54 32 39 21 32 32"/></g>
      <g class="motion-fold motion-fold-s"><path class="motion-fill-c" d="M32 56 32 32 54 34Z"/><path class="motion-a" d="M32 54 43 39 32 32"/></g>
      <g class="motion-fold motion-fold-w"><path class="motion-fill-a" d="M8 32 32 32 30 54Z"/><path class="motion-b" d="M10 32 25 43 32 32"/></g>
      <circle class="motion-fill-c" cx="32" cy="32" r="2.5"/>
    `),
    study('ribbon-turn', 'One-Sided Ribbon', 'A broad ribbon compresses, opens, and carries a glint across its half-twist.', `
      <g class="motion-ribbon">
        <path class="motion-b motion-ribbon-back" d="M10 38C5 17 26 10 36 24L47 40C57 54 62 28 49 22C37 16 28 47 17 45C9 44 8 38 10 32"/>
        <path class="motion-a motion-ribbon-front" d="M10 32C13 20 22 21 30 32L38 42C46 54 57 38 54 29"/>
        <path class="motion-c motion-ribbon-glint" pathLength="100" d="M10 32C13 20 22 21 30 32L38 42C46 54 57 38 54 29"/>
        <path class="motion-c" d="m27 24 8 4m-9 4 8 4"/>
      </g>
    `),
    study('voxel-build', 'Voxel Atelier', 'Three isometric faces lift off their scaffold, then settle into a solid.', `
      <path class="motion-c" d="m9 45 23 13 23-13M32 58V36"/>
      <path class="motion-b" d="m12 22 20-11 20 11-20 12ZM12 22v22l20 11 20-11V22M32 34v21"/>
      <g class="motion-voxel-left"><path class="motion-fill-a" d="m13 24 17 10v18L13 42Z"/><path class="motion-b" d="m18 33 7 4v7"/></g>
      <g class="motion-voxel-right"><path class="motion-fill-b" d="m34 34 17-10v18L34 52Z"/><path class="motion-c" d="m39 38 7-4v7"/></g>
      <g class="motion-voxel-top"><path class="motion-fill-c" d="m15 21 17-10 17 10-17 10Z"/><path class="motion-a" d="m25 21 7-4 7 4-7 4Z"/></g>
    `),
    study('magnet-grid', 'Magnetic Register', 'Three machined tiles exchange an empty slot in a four-cell magnetic puzzle.', `
      <path class="motion-c" d="M8 17V8h9m30 0h9v9m0 30v9h-9m-30 0H8v-9"/>
      <path class="motion-b" d="M13 32h38M32 13v38"/>
      <g class="motion-tile motion-tile-a"><rect class="motion-fill-a" x="12" y="12" width="18" height="18" rx="4"/><path class="motion-c" d="M18 21h6"/></g>
      <g class="motion-tile motion-tile-b"><rect class="motion-fill-b" x="34" y="12" width="18" height="18" rx="4"/><path class="motion-a" d="M43 18v6"/></g>
      <g class="motion-tile motion-tile-c"><rect class="motion-fill-c" x="34" y="34" width="18" height="18" rx="4"/><circle class="motion-fill-b" cx="43" cy="43" r="2"/></g>
    `),
    study('helix-loom', 'Helix Loom', 'Alternating rungs breathe in depth between two counter-flowing molecular strands.', `
      <path class="motion-a motion-helix-strand" pathLength="100" d="M18 7C54 15 10 25 18 32S54 49 18 57"/>
      <path class="motion-b motion-helix-strand motion-helix-reverse" pathLength="100" d="M46 7C10 15 54 25 46 32S10 49 46 57"/>
      <g class="motion-c motion-rungs">
        <path class="motion-rung" style="--phase:0s" d="M22 11h20"/>
        <path class="motion-rung" style="--phase:-.3s" d="M21 21h22"/>
        <path class="motion-rung" style="--phase:-.6s" d="M18 32h28"/>
        <path class="motion-rung" style="--phase:-.9s" d="M21 43h22"/>
        <path class="motion-rung" style="--phase:-1.2s" d="M22 53h20"/>
      </g>
      <g class="motion-fill-a"><circle cx="18" cy="7" r="2.5"/><circle cx="18" cy="57" r="2.5"/></g>
      <g class="motion-fill-b"><circle cx="46" cy="7" r="2.5"/><circle cx="46" cy="57" r="2.5"/></g>
    `),
    study('tidal-lines', 'Tidal Contours', 'A pressure wave passes through five stacked topographic ridges.', `
      <path class="motion-contour motion-a" style="--phase:0s" d="M8 18C18 18 19 9 29 9S42 18 56 18"/>
      <path class="motion-contour motion-b" style="--phase:-.24s" d="M8 26C19 26 20 17 30 17S43 26 56 26"/>
      <path class="motion-contour motion-c" style="--phase:-.48s" d="M8 34C20 34 21 25 31 25S44 34 56 34"/>
      <path class="motion-contour motion-a" style="--phase:-.72s" d="M8 42C21 42 22 33 32 33S45 42 56 42"/>
      <path class="motion-contour motion-b" style="--phase:-.96s" d="M8 50C22 50 23 41 33 41S46 50 56 50"/>
      <path class="motion-c" d="M8 56h48"/>
    `),
    study('endless-ink', 'Endless Ink', 'Two calligraphic strokes chase a continuous figure eight, without erasing its guide.', `
      <path class="motion-b motion-ink-guide" d="M32 32C18 9 7 17 7 32S18 55 32 32 57 9 57 32 46 55 32 32Z"/>
      <path class="motion-a motion-ink-run" pathLength="100" d="M32 32C18 9 7 17 7 32S18 55 32 32 57 9 57 32 46 55 32 32Z"/>
      <path class="motion-c motion-ink-run motion-ink-second" pathLength="100" d="M32 32C18 9 7 17 7 32S18 55 32 32 57 9 57 32 46 55 32 32Z"/>
    `),
    study('scan-lock', 'Survey Lock', 'A Cartesian scanner sweeps a reticle; its target contracts as the scan crosses.', `
      <path class="motion-b" d="M9 21V9h12m22 0h12v12m0 22v12H43m-22 0H9V43"/>
      <path class="motion-c" d="M32 8v7m0 34v7M8 32h7m34 0h7"/>
      <path class="motion-a motion-scan-line" d="M15 16h34"/>
      <g class="motion-scan-target"><path class="motion-c" d="m32 21 11 11-11 11-11-11Z"/><circle class="motion-fill-a" cx="32" cy="32" r="3"/></g>
      <path class="motion-b" d="M20 47h5m4 0h3m4 0h8"/>
    `),
    study('sand-transfer', 'Time Decanter', 'Grains feed a rising lower reservoir while the hourglass turns to refill itself.', `
      <g class="motion-hourglass">
        <path class="motion-b" d="M16 10h32v5c0 9-12 12-12 17s12 8 12 17v5H16v-5c0-9 12-12 12-17S16 24 16 15Z"/>
        <path class="motion-fill-c motion-sand-top" d="M21 16h22c-1 7-9 9-11 13-2-4-10-6-11-13Z"/>
        <path class="motion-fill-a motion-sand-bottom" d="m20 49 12-12 12 12Z"/>
        <path class="motion-c motion-grains" pathLength="12" d="M32 29v15"/>
        <path class="motion-a motion-hourglass-cap" d="M14 8h36M14 56h36"/>
      </g>
    `),
    study('facet-shift', 'Mutable Mineral', 'An eight-point silhouette reforms from cut square to diamond to crystal.', `
      <rect class="motion-fill-a motion-mineral" x="0" y="0" width="64" height="64"/>
      <path class="motion-b motion-mineral-lines" d="m32 12 20 20-20 20-20-20Zm0 0v40M12 32h40"/>
      <path class="motion-c motion-mineral-core" d="m32 24 8 8-8 8-8-8Z"/>
      <circle class="motion-fill-c" cx="32" cy="32" r="2.5"/>
    `),
    study('plait-flow', 'Three-Ply', 'Three offset filaments trade foreground and carry bright stitches through a braid.', `
      <path class="motion-a motion-plait motion-plait-a" pathLength="100" d="M20 8C52 16 12 24 32 32S52 48 20 56"/>
      <path class="motion-b motion-plait motion-plait-b" pathLength="100" d="M32 8C0 16 52 24 32 32S12 48 32 56"/>
      <path class="motion-c motion-plait motion-plait-c" pathLength="100" d="M44 8C12 16 52 24 32 32S12 48 44 56"/>
      <path class="motion-c motion-stitches" pathLength="100" d="M20 8C52 16 12 24 32 32S52 48 20 56"/>
      <path class="motion-a motion-stitches motion-stitches-b" pathLength="100" d="M44 8C12 16 52 24 32 32S12 48 44 56"/>
      <path class="motion-b" d="M16 7h32M16 57h32"/>
    `),
    study('spectral-split', 'Prism Engine', 'A traveling incident beam splits into three individually phased spectral rays.', `
      <path class="motion-b" d="m31 10 17 41H14Z"/>
      <path class="motion-c" d="m31 10 5 34 12 7M14 51l22-7"/>
      <path class="motion-a" d="M5 32h17l14 3"/>
      <path class="motion-b" d="m36 35 22-15"/>
      <path class="motion-a" d="M36 35h22"/>
      <path class="motion-c" d="m36 35 22 15"/>
      <path class="motion-c motion-light-in" pathLength="100" d="M5 32h17l14 3"/>
      <path class="motion-b motion-light-out" style="--phase:-.2s" pathLength="100" d="m36 35 22-15"/>
      <path class="motion-a motion-light-out" style="--phase:-.4s" pathLength="100" d="M36 35h22"/>
      <path class="motion-c motion-light-out" style="--phase:-.6s" pathLength="100" d="m36 35 22 15"/>
    `),
    study('tesseract-roll', 'Wireframe Satellite', 'A nested cube rocks in perspective while a brass tracer circumnavigates its edges.', `
      <g class="motion-cube">
        <path class="motion-b" d="m13 21 20-10 19 11v23L32 55 13 44Zm0 0 19 11 20-10M32 32v23"/>
        <path class="motion-a" d="m22 26 11-6 10 6v13l-11 6-10-6Zm0 0 10 6 11-6M32 32v13"/>
        <path class="motion-c motion-cube-struts" d="m13 21 9 5m11-15v9m19 2-9 4m9 19-9-6M32 55V45m-19-1 9-5"/>
        <path class="motion-c motion-cube-tracer" pathLength="100" d="m13 21 20-10 19 11v23L32 55 13 44V21l19 11 20-10"/>
      </g>
    `),
    study('gimbal-dance', 'Cardan Bloom', 'Three elliptical bearings precess around a fixed, luminous center.', `
      <g transform="rotate(-35 32 32)"><ellipse class="motion-a motion-gimbal motion-gimbal-a" cx="32" cy="32" rx="25" ry="12"/></g>
      <g transform="rotate(35 32 32)"><ellipse class="motion-b motion-gimbal motion-gimbal-b" cx="32" cy="32" rx="25" ry="12"/></g>
      <g transform="rotate(90 32 32)"><ellipse class="motion-c motion-gimbal motion-gimbal-c" cx="32" cy="32" rx="25" ry="12"/></g>
      <circle class="motion-fill-a" cx="32" cy="32" r="4"/>
      <circle class="motion-c" cx="32" cy="32" r="8"/>
    `),
    study('escapement', 'Brass Escapement', 'A weighted arm swings against a toothed timing scale and a grounded frame.', `
      <path class="motion-b" d="m17 54 6-42h18l6 42ZM13 56h38"/>
      <path class="motion-c" d="m14 22 3 2m2-11 3 3M32 7v5m13 1-3 3m8 6-3 2"/>
      <path class="motion-a" d="M23 46h18"/>
      <g class="motion-pendulum"><path class="motion-c" d="M32 43V13"/><path class="motion-fill-c" d="M27 19h10v9H27Z"/><circle class="motion-b" cx="32" cy="15" r="3"/></g>
      <circle class="motion-fill-a" cx="32" cy="43" r="4"/>
    `),
    study('pixel-cascade', 'Raster Sort', 'Five columns of data compact into a histogram, then redistribute in a stepped wave.', `
      <path class="motion-c" d="M7 53h50M7 11h6m38 0h6"/>
      <g class="motion-pixels motion-a" style="--phase:0s"><path d="M10 20h4m-4 8h4m-4 8h4m-4 8h4"/></g>
      <g class="motion-pixels motion-b" style="--phase:-.24s"><path d="M20 20h4m-4 8h4m-4 8h4m-4 8h4"/></g>
      <g class="motion-pixels motion-c" style="--phase:-.48s"><path d="M30 20h4m-4 8h4m-4 8h4m-4 8h4"/></g>
      <g class="motion-pixels motion-a" style="--phase:-.72s"><path d="M40 20h4m-4 8h4m-4 8h4m-4 8h4"/></g>
      <g class="motion-pixels motion-b" style="--phase:-.96s"><path d="M50 20h4m-4 8h4m-4 8h4m-4 8h4"/></g>
      <g class="motion-fill-c"><rect x="9" y="56" width="6" height="2"/><rect x="29" y="56" width="6" height="2"/><rect x="49" y="56" width="6" height="2"/></g>
    `),
    study('mercury-join', 'Liquid Accord', 'Two droplets stretch into a capillary bridge, merge, and pull gently apart.', `
      <path class="motion-b" d="M12 51c10 5 30 5 40 0"/>
      <ellipse class="motion-fill-a motion-liquid-bridge" cx="32" cy="31" rx="17" ry="7"/>
      <ellipse class="motion-fill-a motion-drop-left" cx="19" cy="31" rx="10" ry="12"/>
      <ellipse class="motion-fill-b motion-drop-right" cx="45" cy="31" rx="10" ry="12"/>
      <path class="motion-c motion-drop-shine" d="M15 28c0-3 2-5 5-5"/>
      <path class="motion-a motion-drop-shine-right" d="M42 24c3-1 6 2 6 5"/>
    `),
    study('signal-trace', 'Logic Courier', 'A signal negotiates right-angle traces, handing its charge to a central chip.', `
      <g class="motion-b"><path d="M8 15h15v10h9v14h9v10h15M8 49h15V39m18-14V15h15"/><rect x="24" y="25" width="16" height="14" rx="2"/></g>
      <path class="motion-c motion-signal" pathLength="100" d="M8 15h15v10h9v14h9v10h15"/>
      <path class="motion-a motion-signal motion-signal-return" pathLength="100" d="M8 49h15V32h18V15h15"/>
      <g class="motion-fill-c"><circle cx="8" cy="15" r="3"/><circle cx="56" cy="49" r="3"/></g>
      <g class="motion-fill-a"><circle cx="8" cy="49" r="3"/><circle cx="56" cy="15" r="3"/></g>
      <rect class="motion-fill-a motion-chip" x="29" y="29" width="6" height="6" rx="1"/>
    `),
    study('threshold-depth', 'Threshold', 'Nested architectural doorways exchange depth as a small vanishing point stays anchored.', `
      <path class="motion-c" d="M8 54V10h48v44ZM8 10l24 22m24-22L32 32M8 54l24-22m24 22L32 32"/>
      <path class="motion-a motion-door motion-door-a" d="M15 48V16h34v32Z"/>
      <path class="motion-b motion-door motion-door-b" d="M15 48V16h34v32Z"/>
      <path class="motion-c motion-door motion-door-c" d="M15 48V16h34v32Z"/>
      <path class="motion-fill-a" d="m32 28 4 4-4 4-4-4Z"/>
    `),
    study('corona-shutter', 'Solar Iris', 'Six overlapping shutter blades open a hexagonal sun, then close around its core.', `
      <path class="motion-c" d="m32 6 23 13v26L32 58 9 45V19Z"/>
      <g transform="rotate(0 32 32)"><path class="motion-fill-a motion-blade" d="m32 10 18 10-9 14-9-9Z"/></g>
      <g transform="rotate(60 32 32)"><path class="motion-fill-b motion-blade" d="m32 10 18 10-9 14-9-9Z"/></g>
      <g transform="rotate(120 32 32)"><path class="motion-fill-c motion-blade" d="m32 10 18 10-9 14-9-9Z"/></g>
      <g transform="rotate(180 32 32)"><path class="motion-fill-a motion-blade" d="m32 10 18 10-9 14-9-9Z"/></g>
      <g transform="rotate(240 32 32)"><path class="motion-fill-b motion-blade" d="m32 10 18 10-9 14-9-9Z"/></g>
      <g transform="rotate(300 32 32)"><path class="motion-fill-c motion-blade" d="m32 10 18 10-9 14-9-9Z"/></g>
      <circle class="motion-a motion-sun" cx="32" cy="32" r="5"/>
    `),
  ];
})();
