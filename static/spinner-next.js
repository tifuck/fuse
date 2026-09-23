/* Collection 04: shared by live chat and the motion lab. No SVG IDs. */
(() => {
  'use strict';
  const icon = (id, name, note, body) => ({ id, name, note,
    html: `<svg class="motion-art loader-${id}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="next-icon">${body}</g></svg>` });
  window.FuseSpinnerNext = [
    icon('vesper', 'Vesper', 'Two luminous crescents counter-rotate with a quiet, offset cadence.',
      '<g class="nx-turn"><path d="M13 32A19 19 0 0 1 45.4 18.6"/></g><g class="nx-reverse"><path class="nx-accent" d="M44 32A12 12 0 0 1 23.5 40.5"/></g>'),
    icon('wavelet', 'Wavelet', 'Five rounded reeds carry a smooth wave from left to right.',
      Array.from({ length: 5 }, (_, i) => `<path class="nx-reed" style="--delay:${-i * .18}s" d="M${12 + i * 10} 15V49"/>`).join('')),
    icon('ripple-lens', 'Ripple Lens', 'Three expanding lenses radiate outward with a soft, continuous handoff.',
      [0, -.8, -1.6].map(d => `<circle class="nx-ripple" style="--delay:${d}s" cx="32" cy="32" r="23"/>`).join('')),
    icon('hex-weave', 'Hex Weave', 'Two separated strokes weave around a softly rounded hexagonal circuit.',
      '<g class="nx-slow"><path class="nx-weave" pathLength="100" d="M28 10Q32 8 36 10L49 18Q53 20 53 25V39Q53 44 49 46L36 54Q32 56 28 54L15 46Q11 44 11 39V25Q11 20 15 18Z"/></g>'),
    icon('cascade-dots', 'Cascade Dots', 'A nine-point constellation sends a diagonal swell through its grid.',
      Array.from({ length: 9 }, (_, i) => `<circle class="nx-grid-dot nx-fill" style="--delay:${(-(i % 3 + Math.floor(i / 3)) * .2).toFixed(2)}s" cx="${16 + i % 3 * 16}" cy="${16 + Math.floor(i / 3) * 16}" r="5"/>`).join('')),
    icon('ribbon-knot', 'Ribbon Knot', 'A single traveling ribbon threads a compact four-lobed knot.',
      '<path class="nx-knot" pathLength="100" d="M32 32C8 8 56 8 32 32C56 8 56 56 32 32C56 56 8 56 32 32C8 56 8 8 32 32Z"/>'),

    icon('halcyon', 'Halcyon', 'A broad comet trail circles a calm ring, its tail tapering to a thread.',
      '<circle class="nx-track" cx="32" cy="32" r="22"/><g class="nx-turn"><path class="nx-comet" pathLength="100" d="M32 10A22 22 0 0 1 54 32"/></g>'),
    icon('meridian-gate', 'Meridian Gate', 'Two perfect arcs sweep through each other as their crossing point glides around.',
      '<g class="nx-turn"><path class="nx-arc" pathLength="100" d="M12 32A20 20 0 0 1 52 32"/></g><g class="nx-reverse"><path class="nx-arc nx-accent" pathLength="100" d="M12 32A20 20 0 0 1 52 32"/></g>'),
    icon('echo-mandala', 'Echo Mandala', 'Nested rounded diamonds ripple outward from a quiet center.',
      [14, 20, 26].map((r, i) => `<rect class="nx-echo" style="--delay:${-i * .55}s" x="${32 - r}" y="${32 - r}" width="${r * 2}" height="${r * 2}" rx="${r * .45}"/>`).join('')),
  ];
})();
