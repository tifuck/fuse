/* Collection 05, revision 3. Shared live/preview artwork.
   The 25 retained designs; rejected concepts are excluded from both surfaces.
   All static placement groups keep the SVG origin (0,0). */
(() => {
  'use strict';
  const icon = (id, name, note, body) => ({ id, name, note,
    html: `<svg class="motion-art loader-${id}" xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="studio-icon">${body}</g></svg>` });
  const repeat = (n, body) => Array.from({length:n}, (_,i) => `<g transform="rotate(${i*360/n} 32 32)">${body(i)}</g>`).join('');
  const delay = (i, n, duration=2.4) => `style="--ss-delay:${-i*duration/n}s"`;
  const stroke = (d, cls='', attr='') => `<path class="${cls}" d="${d}" ${attr}/>`;
  const loop = (d, cls='ss-lap') => stroke(d, cls, 'pathLength="100"');
  const turn = (body, cls='ss-turn') => `<g class="${cls}">${body}</g>`;
  const square = 'M23 12H41Q52 12 52 23V41Q52 52 41 52H23Q12 52 12 41V23Q12 12 23 12Z';
  const triangle = 'M26 15Q32 5 38 15L51 38Q57 49 44 49H20Q7 49 13 38Z';
  window.FuseSpinnerFuture = [
    icon('halo-ribbon', 'Halo Ribbon', 'A broad circular ribbon smoothly lengthens and shortens while turning.',
      turn('<circle class="ss-halo" cx="32" cy="32" r="20" pathLength="100"/>')),
    icon('duplex-roll', 'Duplex Roll', 'Two balanced capsules roll around a large, empty center.',
      turn(repeat(2, () => '<rect class="ss-fill" x="21" y="10" width="22" height="9" rx="4.5"/>'), 'ss-roll')),
    icon('trefoil-ribbon', 'Trefoil Ribbon', 'A softly triangular, three-lobed ribbon turns as a single continuous form.',
      turn(loop('M32 14C49 1 62 27 45 34C48 55 17 58 18 38C-1 29 15 4 32 14Z', 'ss-trefoil'), 'ss-slow')),
    icon('soft-square', 'Soft Square', 'Four generous rounded corners form a square that rolls without losing its center.',
      turn(loop(square, 'ss-square'), 'ss-roll')),
    icon('ribbon-fold', 'Ribbon Fold', 'Two curved folded bands gently pass over and under each other.',
      '<path class="ss-fold-a" d="M12 24Q32 7 52 24L40 34"/><path class="ss-fold-b" d="M52 40Q32 57 12 40L24 30"/>'),
    icon('hex-crown', 'Hex Crown', 'A six-sided open crown carries one broad, unhurried highlight.',
      loop('M32 9L52 21V43L32 55L12 43V21Z', 'ss-crown-base') + loop('M32 9L52 21V43L32 55L12 43V21Z', 'ss-crown')),
    icon('comma-four', 'Comma Four', 'Four substantial curved commas rotate around a crisp central opening.',
      turn(repeat(4, () => '<path class="ss-fill" d="M28 10C40 8 48 15 46 24C42 18 36 17 31 21L28 10Z"/>'), 'ss-slow')),
    icon('kite-pair', 'Kite Pair', 'Two offset, hollow kites alternate depth with a gentle breathing motion.',
      '<g transform="translate(-7 0)"><path class="ss-kite" d="M32 14L44 32L32 50L20 32Z"/></g><g transform="translate(7 0)"><path class="ss-kite" style="--ss-delay:-1.4s" d="M32 14L44 32L32 50L20 32Z"/></g>'),
    icon('capsule-current', 'Capsule Current', 'Two smooth capsule halves shift in opposite directions, always remaining aligned.',
      '<path class="ss-capsule-a" d="M28 16H23A16 16 0 0 0 23 48H28"/><path class="ss-capsule-b" d="M36 16H41A16 16 0 0 1 41 48H36"/>'),
    icon('crescent-echo', 'Crescent Echo', 'Three nested crescents rock together in a slow, offset cadence.',
      [10,17,24].map((r,i)=>`<g class="ss-crescent" ${delay(i,3,3.6)}><path d="M32 ${32-r}A${r} ${r} 0 1 1 ${32-r} 32"/></g>`).join('')),
    icon('pixel-conveyor', 'Pixel Conveyor', 'Four retro square cells pulse in a soft, directional handoff.',
      [0,1,2,3].map(i=>`<rect class="ss-pixel ss-fill" ${delay(i,4,1.6)} x="${8+i*13}" y="26" width="9" height="12" rx="2"/>`).join('')),
    icon('lens-shift', 'Lens Shift', 'A solid focus point travels across an open optical outline.',
      '<path d="M8 32Q32 7 56 32Q32 57 8 32Z"/><circle class="ss-focus ss-fill" cx="32" cy="32" r="6"/>'),
    icon('triune', 'Triune', 'Three equal ribbon sections circulate on a soft triangular course.',
      loop(triangle, 'ss-triune')),
    icon('orbital-window', 'Orbital Window', 'A circular opening sits inside a rotating, softly squared outer silhouette.',
      turn('<path class="ss-fill ss-window" fill-rule="evenodd" d="M22 10H42Q54 10 54 22V42Q54 54 42 54H22Q10 54 10 42V22Q10 10 22 10ZM32 19A13 13 0 1 0 32 45A13 13 0 1 0 32 19Z"/>', 'ss-roll')),
    icon('facet-orbit', 'Facet Orbit', 'Four rounded diamond tiles gently expand and contract on a slowly turning cross.',
      turn(repeat(4,i=>`<path class="ss-facet ss-fill" ${delay(i,4,2.8)} d="M29 10Q32 7 35 10L42 17Q45 20 42 23L35 30Q32 33 29 30L22 23Q19 20 22 17Z"/>`), 'ss-slow')),
    icon('crosscurrent', 'Crosscurrent', 'Four smooth bent strokes turn around a steady square of negative space.',
      turn(repeat(4,()=>'<path d="M24 12Q40 10 40 24V27"/>'), 'ss-cross-turn')),
    icon('isometric-loop', 'Isometric Loop', 'A bright edge traverses a retro isometric tile, above a quiet lower edge.',
      '<path class="ss-faint" d="M10 38L32 51L54 38"/>' + loop('M10 26L32 13L54 26L32 39Z', 'ss-isometric')),
    icon('ribbon-six', 'Ribbon Six', 'Three solid folded bands create a restrained, hexagonal ribbon mark.',
      turn(repeat(3,()=>'<path class="ss-fill" d="M32 9L52 21V34L44 29V26L32 19L20 26L12 21Z"/>'), 'ss-slow')),
    icon('silk-knot', 'Silk Knot', 'A continuous rounded knot carries two soft moving highlights.',
      loop('M32 16C53 5 59 27 44 32C59 37 53 59 32 48C11 59 5 37 20 32C5 27 11 5 32 16Z', 'ss-knot-base') + loop('M32 16C53 5 59 27 44 32C59 37 53 59 32 48C11 59 5 37 20 32C5 27 11 5 32 16Z', 'ss-knot')),
    icon('dial-eight', 'Dial Eight', 'Eight thick dial segments pass a smooth highlight around an open center.',
      repeat(8,i=>`<path class="ss-dial" ${delay(i,8,2.4)} d="M28 11Q32 10 36 11"/>`)),
    icon('tessera', 'Tessera', 'Four rounded tiles breathe outward and return in a calm, interleaved sequence.',
      repeat(4,i=>`<rect class="ss-tile ss-fill" ${delay(i,4,3.2)} x="15" y="15" width="14" height="14" rx="4"/>`)),
    icon('vortex-shell', 'Vortex Shell', 'Three substantial nested open rings counter-turn at measured speeds.',
      '<g class="ss-shell-a"><circle class="ss-shell" cx="32" cy="32" r="23" pathLength="100"/></g><g class="ss-shell-b"><circle class="ss-shell" cx="32" cy="32" r="15" pathLength="100"/></g><g class="ss-shell-c"><circle class="ss-shell" cx="32" cy="32" r="7" pathLength="100"/></g>'),
    icon('chevron-weave', 'Chevron Weave', 'Two solid rounded chevron ribbons trade height in a gentle woven motion.',
      '<path class="ss-weave-a" d="M13 24L32 14L51 24"/><path class="ss-weave-b" d="M13 40L32 50L51 40"/>'),
    icon('warp-sail', 'Warp Sail', 'An asymmetric sail of light circles a hollow triangular course.',
      turn(loop('M32 10Q39 27 53 43Q35 40 13 45Q24 30 32 10Z', 'ss-sail'), 'ss-sail-turn')),
    icon('aperture-lozenge', 'Aperture Lozenge', 'A hollow rounded lozenge opens and closes while turning with a measured cadence.',
      turn('<path class="ss-lozenge" d="M24 13Q32 5 40 13L51 24Q59 32 51 40L40 51Q32 59 24 51L13 40Q5 32 13 24Z"/>', 'ss-lozenge-turn')),
  ];
})();
