"use strict";
// Static integrity checks for the Telegram Mini App assets (no browser required).
// Run with: node --test tests/test_webapp_theme_ui.cjs

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const webapp = path.join(__dirname, "..", "src", "enan_ultra", "webapp");
const read = (...parts) => fs.readFileSync(path.join(webapp, ...parts), "utf8");
const html = read("static", "index.html");
const js = read("static", "app.js");
const css = read("static", "app.css");
const themeFiles = fs.readdirSync(path.join(webapp, "themes")).filter((name) => name.endsWith(".json")).sort();

const REQUIRED_COLORS = ["background", "surface", "accent", "glow", "text"];
const HEX = /^#[0-9a-fA-F]{6}$/;

function luminance(hex) {
  const channels = [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16) / 255);
  const [r, g, b] = channels.map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(first, second) {
  const [light, dark] = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (light + 0.05) / (dark + 0.05);
}

test("a safe fallback 'general' theme exists", () => {
  assert.ok(themeFiles.includes("general.json"));
});

for (const file of themeFiles) {
  test(`theme ${file} follows the server-side schema and accessibility rules`, () => {
    const theme = JSON.parse(read("themes", file));
    assert.equal(theme.id, path.basename(file, ".json"), "id must equal the file name");
    assert.match(theme.id, /^[a-z][a-z0-9_]{0,40}$/);
    assert.ok(theme.label && (typeof theme.label.bn === "string" || typeof theme.label.en === "string"));
    assert.equal(typeof theme.icon, "string");
    assert.ok(theme.icon.length >= 1 && theme.icon.length <= 16);
    for (const key of REQUIRED_COLORS) {
      assert.match(theme.colors[key], HEX, `colors.${key} must be a six-digit hex value`);
    }
    assert.ok(contrast(theme.colors.text, theme.colors.background) >= 4.5, "text/background contrast < 4.5");
    assert.ok(contrast(theme.colors.text, theme.colors.surface) >= 4.5, "text/surface contrast < 4.5");
    assert.ok(contrast(theme.colors.accent, theme.colors.background) >= 3, "accent/background contrast < 3");
    assert.ok(contrast(theme.colors.accent, theme.colors.surface) >= 3, "accent/surface contrast < 3");
    const detector = theme.detector || {};
    for (const key of ["keywords_en", "keywords_bn"]) {
      const words = detector[key] || [];
      assert.ok(Array.isArray(words) && words.length <= 200);
      assert.ok(words.every((word) => typeof word === "string" && word.trim() && word.length <= 80));
    }
  });
}

test("every element id the script looks up exists in index.html", () => {
  const htmlIds = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]));
  const wanted = new Set([
    ...[...js.matchAll(/\$\(\s*["']([A-Za-z0-9_-]+)["']\s*\)/g)].map((match) => match[1]),
    ...[...js.matchAll(/getElementById\(\s*["']([A-Za-z0-9_-]+)["']\s*\)/g)].map((match) => match[1]),
  ]);
  assert.ok(wanted.size > 5, "expected the script to look up several elements");
  const missing = [...wanted].filter((id) => !htmlIds.has(id));
  assert.deepEqual(missing, [], `ids used by app.js but absent from index.html: ${missing.join(", ")}`);
});

test("index.html loads the packaged stylesheet and script", () => {
  assert.match(html, /app\.css/);
  assert.match(html, /app\.js/);
});

test("every CSS custom property that is read is also defined", () => {
  const defined = new Set([
    ...[...css.matchAll(/(--[A-Za-z0-9-]+)\s*:/g)].map((match) => match[1]),
    ...[...js.matchAll(/setProperty\(\s*["'](--[A-Za-z0-9-]+)["']/g)].map((match) => match[1]),
    ...[...html.matchAll(/(--[A-Za-z0-9-]+)\s*:/g)].map((match) => match[1]),
  ]);
  const used = new Set([...css.matchAll(/var\(\s*(--[A-Za-z0-9-]+)/g)].map((match) => match[1]));
  const missing = [...used].filter((name) => !defined.has(name));
  assert.deepEqual(missing, [], `undefined CSS variables: ${missing.join(", ")}`);
});

test("the script only assigns static literals to innerHTML and uses no eval-like APIs", () => {
  const staticLiteral = /^(?:'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*")$/;
  for (const match of js.matchAll(/\.innerHTML\s*=\s*([^;]+);/g)) {
    assert.match(match[1].trim(), staticLiteral, `dynamic innerHTML assignment: ${match[0].slice(0, 80)}`);
  }
  assert.doesNotMatch(js, /\.(?:outerHTML|insertAdjacentHTML)\b/);
  assert.doesNotMatch(js, /\beval\s*\(/);
  assert.doesNotMatch(js, /\bnew Function\s*\(/);
  assert.doesNotMatch(js, /document\.write\s*\(/);
});
