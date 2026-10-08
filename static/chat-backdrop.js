/* Animated chat backdrops. chat-appearance.js owns the choice and strength;
   this file only draws. Every scene reads its palette from the active theme
   (the garden family keeps natural plant colours, pitched to it), renders on
   its own frame budget, pauses while the tab is hidden, and holds a
   single still frame under prefers-reduced-motion. Scenes run on their own and
   never follow the pointer; the picker cards render a real frame of each
   scene, animating while hovered. */

/* The drawing lives in ./backdrops/: kits (env/color/noise/kit/pixel/botany/space),
   one file per scene plus the ascii.rest adapter, the registry in scenes.js, and
   the runner, picker previews and sync in engine.js. Load this entry as a module:
   <script type="module" src="/static/chat-backdrop.js?v=N"></script>
   Chunk map and extraction record: backdrop-manifest.json in the snapshot parent. */

import { html } from "./backdrops/env.js";
import { SCENES } from "./backdrops/scenes.js";
import { sync, scanPreviews } from "./backdrops/engine.js";

window.FuseBackdrop = { sync, previews: scanPreviews, animated: (kind) => Boolean(SCENES[kind]) };
scanPreviews();
// chat-appearance.js may have applied a backdrop before this file loaded.
const scene = document.querySelector("[data-chat-scene]");
if (scene) sync(scene, html.dataset.chatBackdrop);
