const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const appPath = path.join(__dirname, "..", "src", "enan_ultra", "webapp", "static", "app.js");
const appSource = fs.readFileSync(appPath, "utf8");
const startup = /\n  start\(\);\n\}\)\(\);\s*$/;

function createThemeHarness({reduceMotion = false} = {}) {
  const css = new Map();
  const elements = new Map();
  const selectedTheme = {
    dataset: {theme: "programming"},
    attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
  };
  let cleared = false;

  for (const id of [
    "scene", "theme-list", "active-icon", "welcome-icon", "active-theme",
    "footer-theme", "topic-status", "favorite", "appearance", "animation",
    "palette", "learning", "tracking",
  ]) {
    elements.set(id, {textContent: "", dataset: {}, attributes: {}, setAttribute(name, value) {
      this.attributes[name] = value;
    }, value: "", checked: false, addEventListener() {}});
  }
  elements.get("scene").getContext = () => ({clearRect() { cleared = true; }});
  elements.get("theme-list").querySelectorAll = () => [selectedTheme];
  elements.get("favorite").firstChild = {textContent: ""};

  const document = {
    body: {dataset: {}},
    documentElement: {style: {setProperty(name, value) { css.set(name, value); }}},
    hidden: false,
    getElementById(id) { return elements.get(id); },
  };
  const sandbox = {
    document,
    window: {},
    navigator: {deviceMemory: 8},
    matchMedia(query) {
      return {matches: query.includes("prefers-reduced-motion") && reduceMotion};
    },
    cancelAnimationFrame() {},
    requestAnimationFrame() { return 1; },
  };
  const instrumented = appSource.replace(startup, `
  drawScene = () => {};
  globalThis.__themeHooks = {applyTheme, applyMotion, syncSettings, state};
})();
`);
  assert.notEqual(instrumented, appSource, "theme harness must replace the normal startup call");
  vm.runInNewContext(instrumented, sandbox, {filename: appPath});
  return {
    css,
    document,
    elements,
    hooks: sandbox.__themeHooks,
    selectedTheme,
    wasCanvasCleared: () => cleared,
  };
}

const theme = {
  id: "programming",
  icon: "💻",
  animation: "circuit",
  motion: "high",
  label: {bn: "প্রোগ্রামিং"},
  colors: {background: "#101820", surface: "#182838", accent: "#74e4dc", glow: "#507bf3", text: "#f2f8ff"},
};

test("selected themes update rendered DOM tokens, labels, selection, and appearance", () => {
  const harness = createThemeHarness();
  harness.hooks.state.preferences = {
    appearance_mode: "light",
    theme_animation: "off",
    theme_palette: "violet",
  };
  harness.hooks.applyTheme(theme, {
    mode: "manual", confidence: 0.9, favorite: true,
  });

  assert.equal(harness.css.get("--bg"), "#101820");
  assert.equal(harness.css.get("--surface"), "#182838");
  assert.equal(harness.css.get("--accent"), "#bd9cff");
  assert.equal(harness.css.get("--glow"), "#7653d6");
  assert.equal(harness.css.get("--text"), "#152537");
  assert.equal(harness.document.body.dataset.scene, "circuit");
  assert.equal(harness.document.body.dataset.appearance, "light");
  assert.equal(harness.document.body.dataset.animation, "off");
  assert.equal(harness.elements.get("active-theme").textContent, "প্রোগ্রামিং");
  assert.equal(harness.elements.get("footer-theme").textContent, "💻 প্রোগ্রামিং");
  assert.equal(harness.elements.get("favorite").attributes["aria-pressed"], "true");
  assert.equal(harness.selectedTheme.attributes["aria-selected"], "true");
  assert.equal(harness.wasCanvasCleared(), true);
});

test("automatic motion honors the reduced-motion system preference", () => {
  const harness = createThemeHarness({reduceMotion: true});
  harness.hooks.state.preferences = {
    appearance_mode: "dark",
    theme_animation: "auto",
    theme_palette: "default",
  };
  harness.hooks.applyTheme(theme, {mode: "auto"});

  assert.equal(harness.document.body.dataset.animation, "reduced");
  assert.equal(harness.document.body.dataset.appearance, "dark");
});

test("settings show favorite learning separately from topic tracking", () => {
  const harness = createThemeHarness();
  harness.hooks.state.preferences = {
    appearance_mode: "system",
    theme_animation: "auto",
    theme_palette: "default",
    theme_learning: "off",
    theme_tracking: "off",
  };
  harness.hooks.syncSettings();

  assert.equal(harness.elements.get("learning").checked, false);
  assert.equal(harness.elements.get("tracking").checked, false);
});
