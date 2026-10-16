(() => {
  "use strict";

  const telegram = window.Telegram && window.Telegram.WebApp;
  const initData = telegram ? telegram.initData : "";
  const $ = (id) => document.getElementById(id);
  const paletteColors = {
    teal: ["#68e2cf", "#138eaa"], violet: ["#bd9cff", "#7653d6"],
    amber: ["#ffd17e", "#c07938"], rose: ["#ff9dc4", "#b94f83"],
  };
  const state = {themes: [], preferences: {}, current: null, tasks: [], controller: null, frame: 0, lastFrame: 0, dots: []};
  const canvas = $("scene");
  const context = canvas.getContext("2d", {alpha: true});
  const list = $("theme-list");
  const messages = $("messages");
  const promptInput = $("prompt");

  if (telegram) {
    telegram.ready();
    telegram.expand();
    if (telegram.themeParams && telegram.themeParams.bg_color) {
      document.documentElement.style.setProperty("--tg-bg", telegram.themeParams.bg_color);
    }
    telegram.onEvent("themeChanged", () => {
      if (state.preferences.appearance_mode === "system") applyAppearance("system");
    });
  }

  async function api(path, options = {}) {
    if (!initData) throw new Error("এই workspace Telegram app থেকে খুলুন।");
    const response = await fetch(path, {
      ...options,
      credentials: "omit",
      headers: {Authorization: `tma ${initData}`, ...(options.headers || {})},
    });
    if (response.status === 401) throw new Error("Telegram session শেষ হয়েছে। Mini App বন্ধ করে আবার খুলুন।");
    const data = response.headers.get("content-type")?.includes("application/json") ? await response.json() : {};
    if (!response.ok) throw new Error(data.detail || "অনুরোধটি সম্পন্ন করা যায়নি।");
    return data;
  }

  function setNotice(message = "") {
    const notice = $("notice");
    notice.textContent = message;
    notice.hidden = !message;
  }

  function localLabel(theme, language) {
    return (theme.label && (theme.label.bn || theme.label.en)) || theme.id;
  }

  function setStatus(value, ok = false) {
    const element = $("connection");
    element.textContent = value;
    element.dataset.state = ok ? "ok" : "";
  }

  function renderTasks() {
    const container = $("task-list");
    container.replaceChildren();
    const openTasks = state.tasks.filter((task) => task.status === "open").length;
    $("task-count").textContent = `${openTasks} খোলা`;
    if (!state.tasks.length) {
      const empty = document.createElement("p");
      empty.className = "task-empty";
      empty.textContent = "এখনো কোনো কাজ যোগ করা হয়নি।";
      container.append(empty);
      return;
    }
    for (const task of state.tasks) {
      const row = document.createElement("article");
      row.className = `task-row${task.status === "done" ? " is-done" : ""}`;
      row.setAttribute("role", "listitem");
      const content = document.createElement("div");
      content.className = "task-content";
      const title = document.createElement("strong");
      title.textContent = `#${task.id} · ${task.task_text}`;
      const details = document.createElement("span");
      const parts = [task.priority];
      if (task.due_at) parts.push(`শেষ তারিখ ${task.due_at}`);
      if (task.blocked_by?.length) parts.push(`আগে শেষ করো: ${task.blocked_by.map((id) => `#${id}`).join(", ")}`);
      details.textContent = parts.join(" · ");
      content.append(title, details);
      row.append(content);
      if (task.status === "open") {
        const actions = document.createElement("div");
        actions.className = "task-actions";
        const done = document.createElement("button");
        done.type = "button";
        done.className = "task-action";
        done.dataset.taskAction = "done";
        done.dataset.taskId = String(task.id);
        done.textContent = task.blocked_by?.length ? "অপেক্ষমাণ" : "সম্পন্ন";
        done.disabled = Boolean(task.blocked_by?.length);
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "task-action delete";
        remove.dataset.taskAction = "delete";
        remove.dataset.taskId = String(task.id);
        remove.textContent = "মুছো";
        actions.append(done, remove);
        row.append(actions);
      }
      container.append(row);
    }
  }

  async function loadTasks() {
    const data = await api("/api/v1/tasks");
    state.tasks = data.tasks;
    renderTasks();
  }

  async function updateTask(payload) {
    const data = await api("/api/v1/tasks", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
    });
    state.tasks = data.tasks;
    renderTasks();
    setNotice("");
  }

  function renderThemeList() {
    list.replaceChildren();
    for (const theme of state.themes) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "theme-option";
      button.setAttribute("role", "option");
      button.dataset.theme = theme.id;
      button.setAttribute("aria-selected", String(state.current?.theme_id === theme.id));
      const icon = document.createElement("span");
      icon.className = "theme-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = theme.icon;
      const name = document.createElement("span");
      name.className = "theme-name";
      name.textContent = localLabel(theme);
      button.append(icon, name);
      if (state.preferences.favorites?.[theme.id] === true) {
        const star = document.createElement("span");
        star.className = "theme-star";
        star.textContent = "★";
        star.setAttribute("aria-label", "পছন্দের theme");
        button.append(star);
      }
      button.addEventListener("click", () => updateTheme({action: "mode", mode: "manual", theme_id: theme.id}));
      list.append(button);
    }
    $("theme-count").textContent = String(state.themes.length).padStart(2, "0");
    updateModeButtons();
  }

  function updateModeButtons() {
    for (const button of document.querySelectorAll("[data-mode]")) {
      button.setAttribute("aria-pressed", String(state.current?.mode === button.dataset.mode));
    }
  }

  function applyAppearance(mode) {
    const actual = mode === "system" ? (telegram?.colorScheme || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")) : mode;
    document.body.dataset.appearance = actual;
    const theme = state.current?.theme || state.themes.find((item) => item.id === state.current?.theme_id);
    document.documentElement.style.setProperty("--text", actual === "light" ? "#152537" : (theme?.colors?.text || "#f2f8ff"));
    document.documentElement.style.setProperty("--muted", actual === "light" ? "#50657a" : "#9ab0c4");
  }

  function applyTheme(theme, selectedState = state.current) {
    if (!theme) return;
    state.current = {...(selectedState || {}), theme_id: theme.id, theme};
    const colors = theme.colors || {};
    const palette = paletteColors[state.preferences.theme_palette];
    document.documentElement.style.setProperty("--bg", colors.background || "#091421");
    document.documentElement.style.setProperty("--surface", colors.surface || "#142435");
    document.documentElement.style.setProperty("--accent", palette?.[0] || colors.accent || "#74e4dc");
    document.documentElement.style.setProperty("--glow", palette?.[1] || colors.glow || "#507bf3");
    document.documentElement.style.setProperty("--text", colors.text || "#f2f8ff");
    document.body.dataset.scene = theme.animation || "aurora";
    $("active-icon").textContent = theme.icon || "✨";
    $("welcome-icon").textContent = theme.icon || "✨";
    $("active-theme").textContent = localLabel(theme);
    $("footer-theme").textContent = `${theme.icon || "✨"} ${localLabel(theme)}`;
    const confidence = Number(selectedState?.confidence || 0);
    const label = selectedState?.mode === "manual" ? "Manual theme" : selectedState?.mode === "off" ? "Theme বন্ধ" : "Auto mode";
    $("topic-status").textContent = confidence ? `${label} · ${Math.round(confidence * 100)}% confidence` : label;
    $("favorite").setAttribute("aria-pressed", String(selectedState?.favorite === true));
    $("favorite").firstChild.textContent = selectedState?.favorite === true ? "♥ " : "♡ ";
    applyAppearance(state.preferences.appearance_mode || "system");
    applyMotion(theme);
    for (const button of list.querySelectorAll("[data-theme]")) {
      button.setAttribute("aria-selected", String(button.dataset.theme === theme.id));
    }
    drawScene(true);
  }

  function applyMotion(theme) {
    const setting = state.preferences.theme_animation || "auto";
    const lowPower = (navigator.deviceMemory && navigator.deviceMemory <= 2)
      || matchMedia("(prefers-reduced-motion: reduce)").matches;
    const level = setting === "auto" ? (lowPower || theme.motion === "low" ? "reduced" : "full") : setting;
    document.body.dataset.animation = level;
    if (level === "off") {
      cancelAnimationFrame(state.frame);
      context.clearRect(0, 0, canvas.width, canvas.height);
    } else {
      drawScene(true);
    }
  }

  async function updateTheme(payload) {
    try {
      const data = await api("/api/v1/theme", {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
      });
      state.current = data.state;
      await refreshStateOnly();
      setNotice("");
      setStatus("PREFERENCES SAVED", true);
    } catch (error) {
      setNotice(error.message);
      setStatus("SESSION ISSUE");
    }
  }

  async function refreshStateOnly() {
    const data = await api("/api/v1/bootstrap");
    state.current = data.state;
    state.preferences = data.state;
    state.themes = data.themes;
    renderThemeList();
    syncSettings();
    const theme = state.themes.find((item) => item.id === state.current.theme_id);
    applyTheme(theme, state.current);
  }

  function syncSettings() {
    $("appearance").value = state.preferences.appearance_mode || "system";
    $("animation").value = state.preferences.theme_animation || "auto";
    $("palette").value = state.preferences.theme_palette || "default";
    $("learning").checked = (state.preferences.theme_learning || "on") === "on";
  }

  function addMessage(role, content = "") {
    $("welcome-icon").closest(".welcome-card")?.remove();
    const bubble = document.createElement("article");
    bubble.className = `message ${role}`;
    if (role === "assistant") {
      const label = document.createElement("span");
      label.className = "message-label";
      label.textContent = `${state.current?.theme?.icon || "✦"} OMEGA AI`;
      bubble.append(label);
    }
    const body = document.createElement("div");
    body.className = "message-body";
    body.textContent = content;
    bubble.append(body);
    messages.append(bubble);
    messages.scrollTop = messages.scrollHeight;
    return {bubble, body};
  }

  function restoreHistory(history) {
    messages.replaceChildren();
    if (!history.length) {
      const welcome = document.createElement("div");
      welcome.className = "welcome-card";
      welcome.innerHTML = '<span id="welcome-icon" class="welcome-orb" aria-hidden="true">✦</span><div><strong>স্বাগতম</strong><p>যে বিষয় নিয়ে কথা বলতে চাও, লিখে শুরু করো। বিষয় বদলালে পরিবেশও বদলাবে।</p></div>';
      messages.append(welcome);
      return;
    }
    for (const item of history) addMessage(item.role === "user" ? "user" : "assistant", item.content || "");
    messages.scrollTop = messages.scrollHeight;
  }

  function parseEvents(buffer) {
    const blocks = buffer.split("\n\n");
    return {events: blocks.slice(0, -1), remainder: blocks.at(-1) || ""};
  }

  async function sendMessage(text) {
    if (state.controller) return;
    const prompt = text.trim();
    if (!prompt) return;
    setNotice("");
    addMessage("user", prompt);
    promptInput.value = "";
    promptInput.style.height = "auto";
    const answer = addMessage("assistant", "");
    answer.bubble.classList.add("typing");
    state.controller = new AbortController();
    $("send").disabled = true;
    $("stop").hidden = false;
    setStatus("GENERATING");
    try {
      const response = await fetch("/api/v1/chat", {
        method: "POST", credentials: "omit", signal: state.controller.signal,
        headers: {Authorization: `tma ${initData}`, "Content-Type": "application/json"},
        body: JSON.stringify({text: prompt}),
      });
      if (!response.ok) {
        const issue = await response.json().catch(() => ({}));
        throw new Error(issue.detail || "উত্তর তৈরি করা যায়নি।");
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream: true});
        const parsed = parseEvents(buffer);
        buffer = parsed.remainder;
        for (const block of parsed.events) {
          const eventName = block.match(/^event: (.+)$/m)?.[1] || "message";
          const dataLine = block.match(/^data: (.+)$/m)?.[1];
          if (!dataLine) continue;
          const data = JSON.parse(dataLine);
          if (eventName === "theme") {
            state.current = {...state.current, theme_id: data.theme_id, topic_id: data.topic_id, confidence: data.confidence};
            applyTheme(data.theme, state.current);
            const label = answer.bubble.querySelector(".message-label");
            if (label) label.textContent = `${data.theme.icon || "✦"} OMEGA AI · ${localLabel(data.theme)}`;
          } else if (eventName === "token") {
            answer.body.textContent += data.text;
            messages.scrollTop = messages.scrollHeight;
          } else if (eventName === "done") {
            answer.body.textContent = data.text || answer.body.textContent;
            answer.bubble.classList.remove("typing");
            setStatus("CONNECTED", true);
          } else if (eventName === "error") {
            setNotice(data.message);
            answer.bubble.classList.remove("typing");
          }
        }
      }
    } catch (error) {
      answer.bubble.classList.remove("typing");
      if (error.name === "AbortError") {
        setNotice("উত্তর থামানো হয়েছে। দেখা অংশটি এই screen-এ রাখা আছে।");
        setStatus("STOPPED");
      } else {
        if (!answer.body.textContent) answer.bubble.remove();
        setNotice(error.message);
        setStatus("SESSION ISSUE");
      }
    } finally {
      state.controller = null;
      $("send").disabled = false;
      $("stop").hidden = true;
    }
  }

  function drawScene(restart = false) {
    if (!context || document.hidden || document.body.dataset.animation === "off") return;
    if (restart) {
      cancelAnimationFrame(state.frame);
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      canvas.width = Math.max(1, Math.floor(innerWidth * dpr));
      canvas.height = Math.max(1, Math.floor(innerHeight * dpr));
      canvas.style.width = `${innerWidth}px`;
      canvas.style.height = `${innerHeight}px`;
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      const amount = document.body.dataset.animation === "full" ? 62 : 26;
      state.dots = Array.from({length: amount}, () => ({
        x: Math.random() * innerWidth, y: Math.random() * innerHeight,
        vx: (Math.random() - .5) * .19, vy: (Math.random() - .5) * .19,
        radius: Math.random() * 1.7 + .5, phase: Math.random() * 6,
      }));
      state.lastFrame = 0;
    }
    const animate = (stamp) => {
      if (document.hidden || document.body.dataset.animation === "off") return;
      const interval = document.body.dataset.animation === "full" ? 38 : 110;
      if (stamp - state.lastFrame > interval) {
        state.lastFrame = stamp;
        renderScene(stamp);
      }
      state.frame = requestAnimationFrame(animate);
    };
    if (document.body.dataset.animation === "reduced") renderScene(performance.now());
    else state.frame = requestAnimationFrame(animate);
  }

  function renderScene(stamp) {
    const width = innerWidth, height = innerHeight;
    context.clearRect(0, 0, width, height);
    const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#74e4dc";
    const animation = document.body.dataset.scene || "aurora";
    const dots = state.dots;
    if (animation === "aurora") {
      const gradient = context.createRadialGradient(width * .75, height * .25, 10, width * .75, height * .25, width * .55);
      gradient.addColorStop(0, `${accent}18`); gradient.addColorStop(1, "transparent");
      context.fillStyle = gradient; context.fillRect(0, 0, width, height);
    }
    for (let i = 0; i < dots.length; i++) {
      const dot = dots[i];
      if (document.body.dataset.animation === "full") {
        dot.x += dot.vx * 9; dot.y += (animation === "code-rain" ? Math.abs(dot.vy) + .55 : dot.vy * 9);
        if (dot.x < -10 || dot.x > width + 10) dot.vx *= -1;
        if (dot.y > height + 8) dot.y = -8;
        if (dot.y < -10 && animation !== "code-rain") dot.y = height + 8;
      }
      const alpha = animation === "starfield" ? .2 + .5 * (Math.sin(stamp / 900 + dot.phase) + 1) / 2 : .34;
      context.globalAlpha = alpha;
      context.fillStyle = accent;
      context.strokeStyle = `${accent}88`;
      context.lineWidth = 1;
      if (animation === "code-rain") {
        context.font = "10px monospace"; context.fillText(Math.random() > .5 ? "0" : "1", dot.x, dot.y);
      } else if (animation === "molecules" || animation === "neural-network") {
        for (let j = i + 1; j < dots.length; j++) {
          const other = dots[j], dx = other.x - dot.x, dy = other.y - dot.y;
          if (dx * dx + dy * dy < 7600) { context.globalAlpha = .08; context.beginPath(); context.moveTo(dot.x, dot.y); context.lineTo(other.x, other.y); context.stroke(); }
        }
        context.globalAlpha = alpha; context.beginPath(); context.arc(dot.x, dot.y, dot.radius + 1, 0, Math.PI * 2); context.fill();
      } else if (animation === "cells") {
        context.globalAlpha = .13; context.beginPath(); context.arc(dot.x, dot.y, 7 + dot.radius * 3, 0, Math.PI * 2); context.stroke();
        context.globalAlpha = alpha; context.beginPath(); context.arc(dot.x, dot.y, dot.radius, 0, Math.PI * 2); context.fill();
      } else if (animation === "math-grid") {
        context.globalAlpha = .18; context.beginPath(); context.moveTo(dot.x - 4, dot.y); context.lineTo(dot.x + 4, dot.y); context.moveTo(dot.x, dot.y - 4); context.lineTo(dot.x, dot.y + 4); context.stroke();
      } else if (animation === "chart-lines" || animation === "paper-lines") {
        context.globalAlpha = .18; context.beginPath(); context.moveTo(dot.x, dot.y); context.lineTo(dot.x + 24, dot.y + dot.vy * 60); context.stroke();
      } else {
        context.beginPath(); context.arc(dot.x, dot.y, dot.radius, 0, Math.PI * 2); context.fill();
        if (animation === "starfield") { context.beginPath(); context.moveTo(dot.x - 4, dot.y); context.lineTo(dot.x + 4, dot.y); context.moveTo(dot.x, dot.y - 4); context.lineTo(dot.x, dot.y + 4); context.stroke(); }
      }
    }
    context.globalAlpha = 1;
    if (animation === "math-grid") {
      context.globalAlpha = .1; context.strokeStyle = accent; context.lineWidth = 1;
      for (let x = 30; x < width; x += 54) { context.beginPath(); context.moveTo(x, 0); context.lineTo(x, height); context.stroke(); }
      for (let y = 30; y < height; y += 54) { context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke(); }
      context.globalAlpha = 1;
    }
  }

  function bindUI() {
    $("chat-form").addEventListener("submit", (event) => {event.preventDefault(); sendMessage(promptInput.value);});
    promptInput.addEventListener("input", () => {promptInput.style.height = "auto"; promptInput.style.height = `${Math.min(promptInput.scrollHeight, 160)}px`;});
    promptInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {event.preventDefault(); $("chat-form").requestSubmit();}
    });
    $("stop").addEventListener("click", () => state.controller?.abort());
    $("task-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const text = $("task-text").value.trim();
      if (!text) return;
      const dueAt = $("task-due").value || null;
      try {
        await updateTask({
          action: "add", text, priority: $("task-priority").value, due_at: dueAt,
        });
        $("task-text").value = "";
        $("task-due").value = "";
      } catch (error) {
        setNotice(error.message);
      }
    });
    $("task-list").addEventListener("click", async (event) => {
      const button = event.target.closest("[data-task-action]");
      if (!button || button.disabled) return;
      button.disabled = true;
      try {
        await updateTask({action: button.dataset.taskAction, task_id: Number(button.dataset.taskId)});
      } catch (error) {
        button.disabled = false;
        setNotice(error.message);
      }
    });
    for (const button of document.querySelectorAll("[data-mode]")) {
      button.addEventListener("click", () => updateTheme({action: "mode", mode: button.dataset.mode}));
    }
    $("favorite").addEventListener("click", () => updateTheme({action: "favorite", theme_id: state.current?.theme_id || "general"}));
    $("settings-toggle").addEventListener("click", () => {
      const panel = $("settings"); panel.hidden = !panel.hidden;
      $("settings-toggle").setAttribute("aria-expanded", String(!panel.hidden));
      if (!panel.hidden) panel.scrollIntoView({behavior: "smooth", block: "nearest"});
    });
    $("settings-close").addEventListener("click", () => {$("settings").hidden = true; $("settings-toggle").setAttribute("aria-expanded", "false");});
    for (const [id, key] of [["appearance", "appearance_mode"], ["animation", "theme_animation"], ["palette", "theme_palette"]]) {
      $(id).addEventListener("change", () => updateTheme({action: "setting", key, value: $(id).value}));
    }
    $("learning").addEventListener("change", () => updateTheme({action: "setting", key: "theme_learning", value: $("learning").checked ? "on" : "off"}));
    $("reset-theme").addEventListener("click", async () => {
      if (!confirm("সব visual preference মুছে Auto mode চালু করবে?")) return;
      await updateTheme({action: "reset"});
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) cancelAnimationFrame(state.frame); else drawScene(true);
    });
    window.addEventListener("resize", () => drawScene(true));
    if (telegram?.onEvent) telegram.onEvent("viewportChanged", () => drawScene(true));
  }

  async function start() {
    bindUI();
    if (!initData) {
      setStatus("OPEN IN TELEGRAM");
      setNotice("নিরাপত্তার জন্য এই workspace Telegram Mini App থেকেই খুলতে হবে।");
      $("send").disabled = true;
      return;
    }
    try {
      const data = await api("/api/v1/bootstrap");
      state.themes = data.themes;
      state.current = data.state;
      state.preferences = data.state;
      renderThemeList();
      syncSettings();
      restoreHistory(data.history || []);
      applyTheme(state.themes.find((item) => item.id === state.current.theme_id), state.current);
      try {
        await loadTasks();
      } catch (error) {
        $("task-list").textContent = error.message;
      }
      setStatus("CONNECTED", true);
    } catch (error) {
      setStatus("SESSION ISSUE");
      setNotice(error.message);
      $("send").disabled = true;
    }
  }

  start();
})();
