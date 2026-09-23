/* Preview-only loading icons. No DOM writes, SVG IDs, or application hooks. */
(() => {
  'use strict';

  const icon = (id, name, note, body) => ({
    id, name, note,
    html: `<svg class="motion-art loader-${id}" xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon">${body}</g></svg>`,
  });
  const infinity = 'M32 32C23 18 10 17 10 32S23 46 32 32 54 17 54 32 41 46 32 32Z';
  const blades = (count, path, className) => Array.from({ length: count }, (_, i) =>
    `<g transform="rotate(${i * 360 / count} 32 32)"><path class="loader-solid ${className}" style="--loader-phase:${-i / count}s" d="${path}"/></g>`
  ).join('');

  window.FuseSpinnerLoaders = [
    icon('ink-runner', 'Infinity Runner', 'One long ink stroke continuously threads both lobes of an open figure eight.', `
      <path class="loader-run" pathLength="100" d="${infinity}"/>
    `),
    icon('ink-relay', 'Infinity Relay', 'Four stout segments relay around the same endless course, with no guide underneath.', `
      <path class="loader-relay" pathLength="100" d="${infinity}"/>
    `),
    icon('ink-twist', 'Twisted Loop', 'Two broad ribbon ends chase through a compact, diagonally twisted loop.', `
      <g transform="rotate(-35 32 32)">
        <path class="loader-twist" pathLength="100" d="M32 32C23 10 11 13 11 29C11 46 24 51 32 32C40 13 53 18 53 35C53 51 41 54 32 32Z"/>
      </g>
    `),

    icon('time-turn', 'Half-Turn', 'A bare hourglass makes continuous half-turns while its leading stroke circulates.', `
      <g class="loader-rotate">
        <path class="loader-glass" pathLength="100" d="M18 14H46C46 23 36 26 36 32S46 41 46 50H18C18 41 28 38 28 32S18 23 18 14Z"/>
      </g>
    `),
    icon('time-exchange', 'Chamber Exchange', 'Two opposing triangular chambers continuously trade a solid fill.', `
      <path d="M17 13H47L32 29ZM17 51H47L32 35Z"/>
      <path class="loader-solid loader-upper" d="M18 14H46L32 28Z"/>
      <path class="loader-solid loader-lower" d="M18 50H46L32 36Z"/>
    `),
    icon('time-pinch', 'Pinch Cycle', 'Two solid hourglass halves fold in opposite phases as the waist turns.', `
      <g class="loader-pinch-turn">
        <path class="loader-solid loader-pinch-top" d="M17 13H47C47 22 37 25 32 31C27 25 17 22 17 13Z"/>
        <path class="loader-solid loader-pinch-bottom" d="M17 51H47C47 42 37 39 32 33C27 39 17 42 17 51Z"/>
      </g>
    `),

    icon('cardan-precession', 'Arc Precession', 'Two interleaved elliptical strokes circulate through a slowly precessing plane.', `
      <g class="loader-precess">
        <g transform="rotate(-40 32 32)"><ellipse class="loader-orbit" pathLength="100" cx="32" cy="32" rx="23" ry="14"/></g>
        <g transform="rotate(40 32 32)"><ellipse class="loader-orbit loader-orbit-second" pathLength="100" cx="32" cy="32" rx="23" ry="14"/></g>
      </g>
    `),
    icon('cardan-bands', 'Tilted Bands', 'Two broken, tilted bands counter-chase without a nucleus or orbiting dots.', `
      <g transform="rotate(-45 32 32)"><ellipse class="loader-band" pathLength="100" cx="32" cy="32" rx="23" ry="13"/></g>
      <g transform="rotate(45 32 32)"><ellipse class="loader-band loader-band-second" pathLength="100" cx="32" cy="32" rx="23" ry="13"/></g>
    `),
    icon('cardan-sphere', 'Sphere Sweep', 'A broad equatorial stroke and a bowed meridian turn as a hollow spherical spinner.', `
      <g class="loader-sphere-turn">
        <circle class="loader-equator" pathLength="100" cx="32" cy="32" r="23"/>
        <ellipse class="loader-meridian" pathLength="100" cx="32" cy="32" rx="11" ry="23"/>
      </g>
    `),

    icon('portal-chase', 'Square Chase', 'Nested broken squares rotate in opposite directions, carrying two continuous chases.', `
      <g class="loader-square-outer"><path class="loader-square-run" pathLength="100" d="M16 16H48V48H16Z"/></g>
      <g class="loader-square-inner"><path class="loader-square-run" pathLength="100" d="M24 24H40V40H24Z"/></g>
    `),
    icon('portal-tunnel', 'Tunnel Stroke', 'A single folded square course carries two long traveling strokes into and out of its center.', `
      <path class="loader-tunnel-run" pathLength="100" d="M11 11H53V53H11V23H41V41H23V11Z"/>
    `),
    icon('portal-corners', 'Corner Relay', 'Four substantial corners hand brightness clockwise around an empty square.', `
      <path class="loader-corner" style="--loader-phase:0s" d="M13 27V13H27"/>
      <path class="loader-corner" style="--loader-phase:-.9s" d="M37 13H51V27"/>
      <path class="loader-corner" style="--loader-phase:-.6s" d="M51 37V51H37"/>
      <path class="loader-corner" style="--loader-phase:-.3s" d="M27 51H13V37"/>
    `),

    icon('solar-iris', 'Rotary Iris', 'Six solid shutter blades rotate around an open hexagonal aperture.', `
      <g class="loader-iris-turn">${blades(6, 'M32 9A23 23 0 0 1 50 18L39 30L34 22Z', 'loader-iris-blade')}</g>
    `),
    icon('solar-wedges', 'Solar Relay', 'Six broad sun wedges pass a progressive brightness chase around the ring.', `
      ${blades(6, 'M35 9A23 23 0 0 1 50.4 18.2L40.2 25.9A10.2 10.2 0 0 0 35 22.2Z', 'loader-wedge')}
    `),
    icon('solar-turbine', 'Three-Vane', 'Three full, curved vanes turn steadily with a small open center and no surrounding machinery.', `
      <g class="loader-turbine-turn">${blades(3, 'M29 27C19 28 13 22 15 16C25 9 39 12 40 23L35 29Q32 25 29 27Z', 'loader-vane')}</g>
    `),

    icon('fluid-eight', 'Fluid Eight', 'A traveling stroke reshapes from a round loop into a pinched figure eight. One glyph, two states.', `
      <path class="loader-fluid-path" pathLength="100" d="M32 8C45.25 8 56 18.75 56 32C56 45.25 45.25 56 32 56C18.75 56 8 45.25 8 32C8 18.75 18.75 8 32 8Z"/>
    `),
    icon('elastic-orbit', 'Elastic Orbit', 'A broken squircle rolls through a soft diamond, stretching and recovering on every turn.', `
      <g class="loader-elastic-turn"><rect class="loader-elastic-path" x="13" y="13" width="38" height="38" rx="12" pathLength="100"/></g>
    `),
    icon('braided-current', 'Braided Current', 'Two alternating ribbons thread a rounded triangular circuit, with a moving gap keeping the knot open.', `
      <g class="loader-braid-turn">
        <path class="loader-braid-path" pathLength="100" d="M32 9C41 9 59 42 52 48C45 54 9 54 10 43C11 32 23 9 32 9Z"/>
        <path class="loader-braid-path loader-braid-second" pathLength="100" d="M32 9C41 9 59 42 52 48C45 54 9 54 10 43C11 32 23 9 32 9Z"/>
      </g>
    `),
    icon('shutter-drive', 'Shutter Drive', 'A three-part aperture opens and closes as the whole assembly turns. Solid, mechanical, continuous.', `
      <g class="loader-shutter-turn">${blades(3, 'M31 9C43 8 51 16 53 26L36 26L30 20Z', 'loader-shutter-blade')}</g>
    `),
    icon('folded-relay', 'Folded Relay', 'Four folded corners advance in a staggered sequence while the square rolls through a quarter-turn.', `
      <g class="loader-fold-turn">${blades(4, 'M14 14H31L24 21H21V28L14 35Z', 'loader-fold-blade')}</g>
    `),
  ];
})();
