const dataUrl = "./data/snapshot.json";

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value ?? "";
}

function changeClass(value) {
  if (value > 0) return "positive";
  if (value < 0) return "negative";
  return "muted";
}

function formatChange(value, suffix = "%") {
  if (value === undefined || value === null) return "";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value}${suffix}`;
}

function shortDate(value) {
  const date = new Date(value);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
    hour12: false,
    timeZone: "UTC",
  }).format(date);
}

function tokenInitial(symbol) {
  if (symbol === "SPCX") return "X";
  if (symbol === "SNDK") return "S";
  if (symbol === "DRAM") return "R";
  return symbol.slice(0, 1);
}

function createLogo(token) {
  const logo = document.createElement("span");
  logo.className = "token-logo";
  logo.style.setProperty("--token", token.color);
  logo.textContent = tokenInitial(token.symbol);
  return logo;
}

function tokenTooltip(token) {
  return token.mint || "Unavailable";
}

async function copyMint(mint, button) {
  if (!mint) return;
  try {
    await navigator.clipboard.writeText(mint);
    button.textContent = "Copied";
    button.classList.add("copied");
    window.setTimeout(() => {
      button.textContent = "Copy";
      button.classList.remove("copied");
    }, 1400);
  } catch {
    button.textContent = "Select";
  }
}

function createMintPill(token) {
  const mint = tokenTooltip(token);
  const wrapper = document.createElement("div");
  wrapper.className = "mint-pill";

  const label = document.createElement("span");
  label.textContent = "CA";

  const code = document.createElement("code");
  code.textContent = mint;

  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Copy";
  button.setAttribute("aria-label", `Copy ${token.symbol} mint address`);
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    copyMint(token.mint, button);
  });

  wrapper.append(label, code, button);
  return wrapper;
}

function renderMarketShare(data) {
  const container = document.getElementById("marketShareChart");
  if (!container) return;

  const issuers = data.marketShare?.issuers || [];
  if (!issuers.length) {
    container.innerHTML = "";
    return;
  }

  const visible = issuers.filter((issuer) => issuer.share >= 0.05);
  container.innerHTML = "";

  const stack = document.createElement("div");
  stack.className = "issuer-stack";

  visible.forEach((issuer) => {
    const segment = document.createElement("span");
    segment.title = `${issuer.label}: ${issuer.display} (${issuer.share.toFixed(1)}%)`;
    segment.style.setProperty("--issuer", issuer.color);
    segment.style.setProperty("--share", `${Math.max(issuer.share, 1)}%`);
    stack.append(segment);
  });

  const history = document.createElement("div");
  history.className = "issuer-history";

  const colorByIssuer = new Map(issuers.map((issuer) => [issuer.id, issuer.color]));
  const labelByIssuer = new Map(issuers.map((issuer) => [issuer.id, issuer.label]));
  const historyDays = data.marketShare?.history || [];

  historyDays.forEach((day) => {
    const dayBar = document.createElement("div");
    dayBar.className = "issuer-day";
    dayBar.title = `${day.date}: Backpack ${day.backpackShare.toFixed(1)}%`;
    const dayStack = document.createElement("div");
    dayStack.className = "issuer-day-stack";

    Object.entries(day.issuers)
      .sort(([a], [b]) => {
        if (a === "backpack") return -1;
        if (b === "backpack") return 1;
        return (labelByIssuer.get(a) || a).localeCompare(labelByIssuer.get(b) || b);
      })
      .forEach(([issuerId, share]) => {
        if (share < 0.05) return;
        const segment = document.createElement("span");
        segment.style.setProperty("--issuer", colorByIssuer.get(issuerId) || "#94a3b8");
        segment.style.setProperty("--share", `${Math.max(share, 1)}%`);
        segment.title = `${labelByIssuer.get(issuerId) || issuerId}: ${share.toFixed(1)}%`;
        dayStack.append(segment);
      });

    const label = document.createElement("em");
    label.textContent = new Intl.DateTimeFormat("en-US", {
      month: "2-digit",
      day: "2-digit",
      timeZone: "UTC",
    }).format(new Date(`${day.date}T00:00:00Z`));
    dayBar.append(dayStack, label);
    history.append(dayBar);
  });

  const legend = document.createElement("div");
  legend.className = "issuer-legend";

  visible.slice(0, 6).forEach((issuer) => {
    const item = document.createElement("div");
    item.className = issuer.id === "backpack" ? "issuer-item primary" : "issuer-item";
    item.innerHTML = `
      <span style="--issuer: ${issuer.color}"></span>
      <strong>${issuer.label}</strong>
      <em>${issuer.share.toFixed(1)}%</em>
    `;
    legend.append(item);
  });

  container.append(stack, history, legend);
}

function renderMetrics(data) {
  const { dailyVolume, solanaShare, holders, cumulativeVolume } = data.metrics;

  setText("dailyVolumeLabel", dailyVolume.label);
  setText("dailyVolumeValue", dailyVolume.display);
  setText("dailyVolumeChange", formatChange(dailyVolume.change));
  document.getElementById("dailyVolumeChange").className = `metric-change ${changeClass(dailyVolume.change)}`;

  setText("solanaShareLabel", solanaShare.label);
  setText("solanaShareValue", solanaShare.display);
  setText("solanaShareDetail", solanaShare.detail);
  const shareChange = document.getElementById("solanaShareChange");
  if (shareChange) {
    const hasShareChange = solanaShare.change !== undefined && solanaShare.change !== null && solanaShare.change !== 0;
    shareChange.textContent = hasShareChange ? formatChange(solanaShare.change, solanaShare.changeSuffix || "%") : "";
    shareChange.className = `metric-change ${changeClass(solanaShare.change)}`;
  }
  document.getElementById("shareRing")?.style.setProperty("--ring", `${solanaShare.value * 3.6}deg`);

  setText("holdersLabel", holders.label);
  setText("holdersValue", holders.display);
  setText("holdersChange", formatChange(holders.change));

  setText("cumulativeVolumeLabel", cumulativeVolume.label);
  setText("cumulativeVolumeValue", cumulativeVolume.display);
  setText("cumulativeVolumeDetail", cumulativeVolume.detail);
  renderMarketShare(data);
}

function renderVolume(tokens) {
  const container = document.getElementById("volumeList");
  const max = Math.max(...tokens.map((token) => token.volumeRaw));
  container.innerHTML = "";

  tokens.forEach((token) => {
    const row = document.createElement("article");
    row.className = "volume-row";

    const identity = document.createElement("div");
    identity.className = "asset-id";
    identity.append(createLogo(token));
    const text = document.createElement("div");
    text.innerHTML = `<strong>${token.symbol}</strong><small>${token.name}</small>`;
    text.append(createMintPill(token));
    identity.append(text);

    const track = document.createElement("div");
    track.className = "bar-track";
    const fill = document.createElement("div");
    fill.className = "bar-fill";
    fill.style.setProperty("--token", token.color);
    fill.style.setProperty("--bar", `${Math.max((token.volumeRaw / max) * 100, 3)}%`);
    track.append(fill);

    const value = document.createElement("div");
    value.className = "volume-value";
    value.innerHTML = `<strong>${token.volume24h}</strong><span>${formatChange(token.change24h)}</span>`;

    row.append(identity, track, value);
    container.append(row);
  });
}

function renderTable(tokens) {
  const rows = document.getElementById("assetRows");
  rows.innerHTML = "";

  tokens.forEach((token) => {
    const row = document.createElement("article");
    row.className = "asset-row";
    row.setAttribute("role", "row");

    const identity = document.createElement("div");
    identity.className = "asset-id";
    identity.append(createLogo(token));
    const text = document.createElement("div");
    text.innerHTML = `<strong>${token.symbol}</strong><small>${token.name}</small>`;
    text.append(createMintPill(token));
    identity.append(text);

    const change = document.createElement("strong");
    change.className = token.change24h >= 0 ? "pos" : "neg";
    change.textContent = formatChange(token.change24h);

    const holders = document.createElement("div");
    holders.innerHTML = `<strong>${token.holders}</strong><small class="${token.holdersChange.startsWith("-") ? "neg" : "pos"}">${token.holdersChange}</small>`;

    row.append(
      identity,
      cell(token.price),
      change,
      cell(token.volume24h),
      cell(token.supply),
      cell(token.supplyChange),
      holders,
    );
    rows.append(row);
  });
}

function cell(value) {
  const element = document.createElement("strong");
  element.textContent = value;
  return element;
}

function renderNotes(data) {
  const notes = document.getElementById("notes");
  notes.innerHTML = "";
  data.notes.forEach((note) => {
    const item = document.createElement("p");
    item.textContent = note;
    notes.append(item);
  });
  const handle = document.getElementById("handle");
  if (handle) {
    handle.textContent = data.handle || "@coleta_cripto";
    handle.href = data.socialUrl || "https://x.com/Coleta_Cripto";
  }
  const cta = document.getElementById("backpackCta");
  if (cta) cta.href = data.backpackUrl || "https://backpack.exchange/join/coletacripto";
}

async function loadSnapshot() {
  const response = await fetch(dataUrl, { cache: "no-store" });
  if (!response.ok) throw new Error(`Could not load ${dataUrl}`);
  const data = await response.json();

  setText("title", data.title);
  setText("subtitle", data.subtitle);
  setText("description", data.description);
  setText("updatedAt", shortDate(data.updatedAt));
  document.getElementById("updatedAt").dateTime = data.updatedAt;

  renderMetrics(data);
  renderVolume(data.tokens);
  renderTable(data.tokens);
  renderNotes(data);
}

loadSnapshot().catch((error) => {
  document.body.innerHTML = `<main class="shell"><section class="panel"><h1>Tracker unavailable</h1><p>${error.message}</p></section></main>`;
});
