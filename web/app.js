const PROVIDERS = ["openai", "anthropic", "lmstudio"];

const providerLabel = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  lmstudio: "LM Studio",
};

const providerIcons = {
  openai: "/assets/icons/openai.png",
  anthropic: "/assets/icons/anthropic.png",
  lmstudio: "/assets/icons/lmstudio.png",
  user: "/assets/icons/user.png",
};

const state = {
  root: "",
  lmRoot: "",
  conversations: [],
  selectedId: null,
  currentConversation: null,
  search: "",
  sortPreset: "last_ts:desc",
  markdownEnabled: true,
  providerActive: {
    openai: true,
    anthropic: true,
    lmstudio: true,
  },
  providerCounts: {
    openai: 0,
    anthropic: 0,
    lmstudio: 0,
  },
};

const els = {
  controlPanel: document.querySelector(".control-panel"),
  sidebarPanel: document.querySelector(".sidebar"),
  splitterLeft: document.getElementById("splitterLeft"),
  splitterRight: document.getElementById("splitterRight"),
  rootInput: document.getElementById("rootInput"),
  lmRootInput: document.getElementById("lmRootInput"),
  pickRootBtn: document.getElementById("pickRootBtn"),
  pickLmRootBtn: document.getElementById("pickLmRootBtn"),
  saveSettingsBtn: document.getElementById("saveSettingsBtn"),
  settingsHint: document.getElementById("settingsHint"),
  settingsDock: document.getElementById("settingsDock"),
  searchInput: document.getElementById("searchInput"),
  sortPreset: document.getElementById("sortPreset"),
  markdownToggle: document.getElementById("markdownToggle"),
  rescanBtn: document.getElementById("rescanBtn"),
  providerFilters: document.getElementById("providerFilters"),
  chatList: document.getElementById("chatList"),
  sidebarStats: document.getElementById("sidebarStats"),
  chatHeader: document.getElementById("chatHeader"),
  chatScroll: document.getElementById("chatScroll"),
  chatStats: document.getElementById("chatStats"),
  chatItemTemplate: document.getElementById("chatItemTemplate"),
  messageTemplate: document.getElementById("messageTemplate"),
  dateSeparatorTemplate: document.getElementById("dateSeparatorTemplate"),
};

function providerIcon(provider) {
  return providerIcons[provider] || providerIcons.openai;
}

function userIcon() {
  return providerIcons.user;
}

function setHint(text, isError = false) {
  els.settingsHint.textContent = text || "";
  els.settingsHint.classList.toggle("error", isError);
}

function formatTs(ts) {
  if (typeof ts !== "number" || Number.isNaN(ts)) return "unknown date";
  return new Date(ts * 1000).toLocaleString();
}

function formatDateSeparator(ts) {
  if (typeof ts !== "number" || Number.isNaN(ts)) return null;
  const dt = new Date(ts * 1000);
  return new Intl.DateTimeFormat("de-DE", {
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  })
    .format(dt)
    .replace(",", "");
}

function dateKey(ts) {
  if (typeof ts !== "number" || Number.isNaN(ts)) return null;
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderInlineMarkdown(text) {
  const safe = escapeHtml(text);
  const linked = safe.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
  );

  return linked
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
}

function renderMarkdownPlainBlock(source) {
  const lines = (source || "").split("\n");
  const html = [];
  let paragraph = [];
  let inUl = false;
  let inOl = false;

  const closeParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${paragraph.join("<br>")}</p>`);
    paragraph = [];
  };

  const closeLists = () => {
    if (inUl) {
      html.push("</ul>");
      inUl = false;
    }
    if (inOl) {
      html.push("</ol>");
      inOl = false;
    }
  };

  lines.forEach((rawLine) => {
    const line = rawLine || "";
    const trimmed = line.trim();

    if (!trimmed) {
      closeParagraph();
      closeLists();
      return;
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      closeParagraph();
      closeLists();
      const level = heading[1].length;
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      return;
    }

    const ulItem = trimmed.match(/^[-*]\s+(.+)$/);
    if (ulItem) {
      closeParagraph();
      if (inOl) {
        html.push("</ol>");
        inOl = false;
      }
      if (!inUl) {
        html.push("<ul>");
        inUl = true;
      }
      html.push(`<li>${renderInlineMarkdown(ulItem[1])}</li>`);
      return;
    }

    const olItem = trimmed.match(/^\d+\.\s+(.+)$/);
    if (olItem) {
      closeParagraph();
      if (inUl) {
        html.push("</ul>");
        inUl = false;
      }
      if (!inOl) {
        html.push("<ol>");
        inOl = true;
      }
      html.push(`<li>${renderInlineMarkdown(olItem[1])}</li>`);
      return;
    }

    const quote = trimmed.match(/^>\s?(.+)$/);
    if (quote) {
      closeParagraph();
      closeLists();
      html.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
      return;
    }

    closeLists();
    paragraph.push(renderInlineMarkdown(line));
  });

  closeParagraph();
  closeLists();
  return html.join("");
}

function renderMarkdownToHtml(sourceText) {
  const source = sourceText || "";
  const chunks = [];
  const re = /```\s*([a-zA-Z0-9_+-]+)?\s*\n?([\s\S]*?)```/g;
  let last = 0;
  let match = re.exec(source);

  while (match) {
    const plain = source.slice(last, match.index);
    if (plain) chunks.push({ type: "plain", value: plain });
    chunks.push({ type: "code", language: (match[1] || "").toLowerCase(), value: match[2] || "" });
    last = re.lastIndex;
    match = re.exec(source);
  }

  if (last < source.length) {
    chunks.push({ type: "plain", value: source.slice(last) });
  }

  return chunks
    .map((chunk) => {
      if (chunk.type === "code") {
        const langClass = chunk.language ? ` class="language-${chunk.language}"` : "";
        return `<pre><code${langClass}>${escapeHtml(chunk.value.trim())}</code></pre>`;
      }
      return renderMarkdownPlainBlock(chunk.value);
    })
    .join("");
}

function applySyntaxHighlight(container) {
  if (!state.markdownEnabled || !container || !window.hljs) return;

  container.querySelectorAll("pre code").forEach((codeBlock) => {
    window.hljs.highlightElement(codeBlock);
  });
}

function compareConversations(a, b) {
  const [field, order] = state.sortPreset.split(":");
  const dir = order === "asc" ? 1 : -1;

  if (field === "title") {
    return a.title.localeCompare(b.title, undefined, { sensitivity: "base" }) * dir;
  }

  const av = typeof a[field] === "number" ? a[field] : Number.NEGATIVE_INFINITY;
  const bv = typeof b[field] === "number" ? b[field] : Number.NEGATIVE_INFINITY;
  return (av - bv) * dir;
}

function filteredConversations() {
  const q = state.search.trim().toLowerCase();
  return state.conversations
    .filter((c) => state.providerActive[c.provider])
    .filter((c) => {
      if (!q) return true;
      const hay = `${c.title} ${c.model_name || ""} ${c.source_file}`.toLowerCase();
      return hay.includes(q);
    })
    .sort(compareConversations);
}

function renderProviderChips() {
  els.providerFilters.innerHTML = "";
  PROVIDERS.forEach((provider) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `provider-chip ${state.providerActive[provider] ? "active" : "inactive"}`;
    chip.innerHTML = `
      <img src="${providerIcon(provider)}" alt="${providerLabel[provider]} icon">
      <span>${providerLabel[provider]} (${state.providerCounts[provider] || 0})</span>
    `;
    chip.addEventListener("click", () => {
      state.providerActive[provider] = !state.providerActive[provider];
      renderProviderChips();
      renderChatList();
    });
    els.providerFilters.appendChild(chip);
  });
}

function renderChatList() {
  const rows = filteredConversations();
  els.chatList.innerHTML = "";
  els.sidebarStats.textContent = `${rows.length} chats shown`;

  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "chat-empty";
    empty.textContent = "No chats match your current filters.";
    els.chatList.appendChild(empty);
    return;
  }

  const frag = document.createDocumentFragment();
  rows.forEach((conv) => {
    const node = els.chatItemTemplate.content.firstElementChild.cloneNode(true);
    node.dataset.id = conv.id;
    if (conv.id === state.selectedId) {
      node.classList.add("active");
    }

    node.querySelector(".provider-icon").src = providerIcon(conv.provider);
    node.querySelector(".provider-icon").alt = providerLabel[conv.provider];
    node.querySelector(".chat-item-title").textContent = conv.title;

    const metaParts = [providerLabel[conv.provider] || conv.provider, formatTs(conv.last_ts)];
    if (conv.model_name) metaParts.push(conv.model_name);
    node.querySelector(".chat-item-meta").textContent = metaParts.join(" | ");

    node.addEventListener("click", () => {
      loadConversation(conv.id);
    });
    frag.appendChild(node);
  });

  els.chatList.appendChild(frag);
}

function roleClass(role) {
  const r = (role || "").toLowerCase();
  if (r === "user" || r === "human") return "user";
  if (r === "system") return "system";
  return "assistant";
}

function messageAuthorIcon(provider, role) {
  return roleClass(role) === "user" ? userIcon() : providerIcon(provider);
}

function setMessageText(target, text) {
  if (state.markdownEnabled) {
    target.classList.remove("plain-text");
    target.innerHTML = renderMarkdownToHtml(text);
    applySyntaxHighlight(target);
  } else {
    target.classList.add("plain-text");
    target.textContent = text || "";
  }
}

function normalizedBlocks(msg) {
  if (Array.isArray(msg.blocks) && msg.blocks.length > 0) {
    return msg.blocks;
  }

  const blocks = [];
  if (msg.thinking_text) blocks.push({ type: "thinking", text: msg.thinking_text });
  if (msg.text) blocks.push({ type: "text", text: msg.text });
  return blocks;
}

function parseToolCallSegment(segment) {
  if (!segment || !segment.text) return null;

  const blockType = (segment.type || "").toLowerCase();
  if (blockType === "tool_call") {
    const name = String(segment.name || "tool").trim() || "tool";
    return {
      name,
      payload: String(segment.text || "").trim(),
    };
  }

  if (blockType && blockType !== "text") {
    return null;
  }

  const text = String(segment.text || "");
  const match = text.match(/^\[Tool call:\s*([^\]]+)\]\s*\n?([\s\S]*)$/i);
  if (!match) {
    return null;
  }

  return {
    name: (match[1] || "tool").trim() || "tool",
    payload: (match[2] || "").trim(),
  };
}

function parseToolResultSegment(segment) {
  if (!segment || !segment.text) return null;

  const blockType = (segment.type || "").toLowerCase();
  if (blockType === "tool_result") {
    return {
      payload: String(segment.text || "").trim(),
    };
  }

  if (blockType && blockType !== "text") {
    return null;
  }

  const text = String(segment.text || "");
  const match = text.match(/^\[Tool result\]\s*\n?([\s\S]*)$/i);
  if (!match) {
    return null;
  }

  return {
    payload: (match[1] || "").trim(),
  };
}

function appendMessageSegment(contentNode, segment) {
  if (!segment || !segment.text) return false;

  const toolCall = parseToolCallSegment(segment);
  if (toolCall) {
    const details = document.createElement("details");
    details.className = "tool-block message-segment tool";
    details.open = false;

    const summary = document.createElement("summary");
    summary.textContent = `Tool: ${toolCall.name}`;

    const pre = document.createElement("pre");
    pre.className = "tool-text";
    pre.textContent = toolCall.payload || "No payload.";

    details.appendChild(summary);
    details.appendChild(pre);
    contentNode.appendChild(details);
    return true;
  }

  const toolResult = parseToolResultSegment(segment);
  if (toolResult) {
    const lastEl = contentNode.lastElementChild;
    if (lastEl && lastEl.tagName === "DETAILS" && lastEl.classList.contains("tool-block") && !lastEl.classList.contains("tool-result-block")) {
      const hr = document.createElement("hr");
      hr.style.margin = "8px 0";
      hr.style.border = "none";
      hr.style.borderTop = "1px solid var(--line-soft)";
      
      const resLabel = document.createElement("div");
      resLabel.textContent = "Result:";
      resLabel.style.fontSize = "0.8rem";
      resLabel.style.color = "var(--muted)";
      resLabel.style.marginBottom = "4px";

      const pre = document.createElement("pre");
      pre.className = "tool-text";
      pre.textContent = toolResult.payload || "No result payload.";

      lastEl.appendChild(hr);
      lastEl.appendChild(resLabel);
      lastEl.appendChild(pre);
      return true;
    }

    const details = document.createElement("details");
    details.className = "tool-block tool-result-block message-segment tool";
    details.open = false;

    const summary = document.createElement("summary");
    summary.textContent = "Tool result";

    const pre = document.createElement("pre");
    pre.className = "tool-text";
    pre.textContent = toolResult.payload || "No result payload.";

    details.appendChild(summary);
    details.appendChild(pre);
    contentNode.appendChild(details);
    return true;
  }

  const type = (segment.type || "text").toLowerCase();
  if (type === "thinking") {
    const details = document.createElement("details");
    details.className = "thinking-block message-segment thinking";
    details.open = false;

    const summary = document.createElement("summary");
    summary.textContent = "Thinking";

    const body = document.createElement("div");
    body.className = "thinking-text";
    setMessageText(body, segment.text);

    details.appendChild(summary);
    details.appendChild(body);
    contentNode.appendChild(details);
    return true;
  }

  const textNode = document.createElement("div");
  textNode.className = "message-segment text";
  setMessageText(textNode, segment.text);
  contentNode.appendChild(textNode);
  return true;
}

function renderConversation(conv, resetScroll = false) {
  els.chatScroll.innerHTML = "";

  const frag = document.createDocumentFragment();
  let previousDate = null;

  conv.messages.forEach((msg) => {
    const currentDate = dateKey(msg.sent_ts);
    if (currentDate && currentDate !== previousDate) {
      const dateNode = els.dateSeparatorTemplate.content.firstElementChild.cloneNode(true);
      dateNode.textContent = formatDateSeparator(msg.sent_ts) || "";
      frag.appendChild(dateNode);
      previousDate = currentDate;
    }

    const row = els.messageTemplate.content.firstElementChild.cloneNode(true);
    row.classList.add(roleClass(msg.role));

    const icon = row.querySelector(".author-icon");
    icon.src = messageAuthorIcon(conv.provider, msg.role);

    const messageContent = row.querySelector(".message-content");
    const blocks = normalizedBlocks(msg);
    let appended = 0;

    blocks.forEach((segment) => {
      if (appendMessageSegment(messageContent, segment)) {
        appended += 1;
      }
    });

    if (appended === 0 && msg.text) {
      appendMessageSegment(messageContent, { type: "text", text: msg.text });
    }

    const timeNode = row.querySelector(".message-time");
    timeNode.textContent = typeof msg.sent_ts === "number" && !Number.isNaN(msg.sent_ts) ? formatTs(msg.sent_ts) : "";
    frag.appendChild(row);
  });

  els.chatScroll.appendChild(frag);
  els.chatStats.textContent = `Messages: ${conv.message_count || conv.messages.length} | Characters: ${conv.character_count || 0} | Words: ${conv.word_count || 0}`;

  if (resetScroll) {
    els.chatScroll.scrollTop = 0;
  }
}

async function loadConversation(id) {
  const resp = await fetch(`/api/conversation?id=${encodeURIComponent(id)}`);
  if (!resp.ok) return;

  const conv = await resp.json();
  state.selectedId = conv.id;
  state.currentConversation = conv;
  renderChatList();

  const titleProvider = `${conv.title} (${providerLabel[conv.provider] || conv.provider})`;
  const modelInfo = conv.model_name ? ` | ${conv.model_name}` : "";
  els.chatHeader.textContent = `${titleProvider}${modelInfo}`;
  renderConversation(conv, true);
}

async function loadSettings() {
  const resp = await fetch("/api/settings");
  if (!resp.ok) return;

  const payload = await resp.json();
  state.root = payload.transcript_root || "";
  state.lmRoot = payload.lmstudio_root || "";
  els.rootInput.value = state.root;
  els.lmRootInput.value = state.lmRoot;
}

async function saveSettings() {
  const transcriptRoot = els.rootInput.value.trim();
  const lmstudioRoot = els.lmRootInput.value.trim();

  const resp = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transcript_root: transcriptRoot, lmstudio_root: lmstudioRoot }),
  });

  if (!resp.ok) {
    const payload = await resp.json().catch(() => ({}));
    setHint(payload.error || "Could not save settings.", true);
    return false;
  }

  const payload = await resp.json();
  state.root = payload.transcript_root || transcriptRoot;
  state.lmRoot = payload.lmstudio_root || lmstudioRoot;
  els.rootInput.value = state.root;
  els.lmRootInput.value = state.lmRoot;
  setHint("Paths saved.");
  return true;
}

async function pickFolder(target) {
  setHint("Opening folder picker...");

  const resp = await fetch("/api/pick-folder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target }),
  });

  if (!resp.ok) {
    const payload = await resp.json().catch(() => ({}));
    const details = Array.isArray(payload.details) ? ` (${payload.details.join(" | ")})` : "";
    setHint(`${payload.error || "Could not open folder picker."}${details}`, true);
    return;
  }

  const payload = await resp.json();
  if (!payload.selected) {
    setHint("No folder selected.");
    return;
  }

  if (target === "transcript_root") {
    els.rootInput.value = payload.selected;
  } else {
    els.lmRootInput.value = payload.selected;
  }

  setHint("Folder selected. Click Save Paths to persist.");
}

async function loadConversations(force = false) {
  const query = new URLSearchParams();
  const root = els.rootInput.value.trim();
  const lmRoot = els.lmRootInput.value.trim();

  if (root) query.set("root", root);
  if (lmRoot) query.set("lm_root", lmRoot);
  if (force) query.set("force", "1");

  const resp = await fetch(`/api/conversations?${query.toString()}`);
  if (!resp.ok) {
    const payload = await resp.json().catch(() => ({}));
    alert(payload.error || "Failed to scan conversations");
    return;
  }

  const payload = await resp.json();
  state.root = payload.root || root;
  state.lmRoot = payload.lm_root || lmRoot;
  state.providerCounts = payload.provider_counts || state.providerCounts;
  state.conversations = payload.conversations || [];
  els.rootInput.value = state.root;
  els.lmRootInput.value = state.lmRoot;

  renderProviderChips();
  renderChatList();

  if (!state.selectedId) {
    const first = filteredConversations()[0];
    if (first) await loadConversation(first.id);
    return;
  }

  const stillPresent = state.conversations.some((c) => c.id === state.selectedId);
  if (!stillPresent) {
    state.selectedId = null;
    state.currentConversation = null;
    els.chatScroll.innerHTML = '<div class="chat-empty">Choose a chat from the sidebar.</div>';
    els.chatHeader.textContent = "Select a conversation";
    els.chatStats.textContent = "Messages: 0 | Characters: 0 | Words: 0";
  }
}

function installSplitters() {
  if (!els.splitterLeft || !els.splitterRight || !els.controlPanel || !els.sidebarPanel) {
    return;
  }

  const rootStyle = document.documentElement.style;
  let active = null;
  let startX = 0;
  let startWidth = 0;
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  const onMove = (evt) => {
    if (!active) return;
    const delta = evt.clientX - startX;

    if (active === "left") {
      rootStyle.setProperty("--w-controls", `${clamp(startWidth + delta, 190, 460)}px`);
      return;
    }

    rootStyle.setProperty("--w-sidebar", `${clamp(startWidth + delta, 220, 650)}px`);
  };

  const onUp = () => {
    if (active === "left") els.splitterLeft.classList.remove("dragging");
    if (active === "right") els.splitterRight.classList.remove("dragging");
    active = null;
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
  };

  els.splitterLeft.addEventListener("pointerdown", (evt) => {
    if (window.innerWidth <= 900) return;
    active = "left";
    startX = evt.clientX;
    startWidth = els.controlPanel.getBoundingClientRect().width;
    els.splitterLeft.classList.add("dragging");
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  });

  els.splitterRight.addEventListener("pointerdown", (evt) => {
    if (window.innerWidth <= 900) return;
    active = "right";
    startX = evt.clientX;
    startWidth = els.sidebarPanel.getBoundingClientRect().width;
    els.splitterRight.classList.add("dragging");
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  });
}

function installEvents() {
  els.searchInput.addEventListener("input", () => {
    state.search = els.searchInput.value;
    renderChatList();
  });

  els.sortPreset.addEventListener("change", () => {
    state.sortPreset = els.sortPreset.value;
    renderChatList();
  });

  els.markdownToggle.addEventListener("change", () => {
    state.markdownEnabled = els.markdownToggle.checked;
    if (state.currentConversation) renderConversation(state.currentConversation, false);
  });

  els.rescanBtn.addEventListener("click", async () => {
    setHint("");
    await loadConversations(true);
  });

  els.pickRootBtn.addEventListener("click", async () => {
    await pickFolder("transcript_root");
  });

  els.pickLmRootBtn.addEventListener("click", async () => {
    await pickFolder("lmstudio_root");
  });

  els.saveSettingsBtn.addEventListener("click", async () => {
    const saved = await saveSettings();
    if (saved) {
      await loadConversations(true);
      els.settingsDock.open = false;
    }
  });

  [els.rootInput, els.lmRootInput].forEach((input) => {
    input.addEventListener("keydown", async (evt) => {
      if (evt.key !== "Enter") return;
      const saved = await saveSettings();
      if (saved) await loadConversations(true);
    });
  });
}

async function boot() {
  installSplitters();
  installEvents();
  state.markdownEnabled = els.markdownToggle.checked;
  state.sortPreset = els.sortPreset.value;

  await loadSettings();
  await loadConversations(false);
}

boot();
