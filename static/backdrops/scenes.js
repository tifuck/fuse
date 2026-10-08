// The scene registry, in the folded source's order. Engine and entry read it by key.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

import { glyphs, life, rain, ripple } from "./scenes-glyph.js";
import { circuit, spiral, coral } from "./scenes-circuit.js";
import { riso, warp, mandala, aurora } from "./scenes-field.js";
import { garden } from "./scene-garden.js";
import { botanica } from "./scene-botanica.js";
import { ivy } from "./scene-nouveau.js";
import { meadow, pond } from "./scenes-meadow.js";
import { orbit, galaxy } from "./scenes-orbit-galaxy.js";
import { eclipse, belt, contact } from "./scenes-eclipse-belt-contact.js";
import { koi } from "./scene-koi.js";
import { signal } from "./scene-signal.js";

export const SCENES = { glyphs, life, rain, ripple, circuit, spiral, coral, riso, warp, mandala, aurora, garden, botanica, ivy, meadow, pond, orbit, galaxy, eclipse, belt, contact, koi, signal };
