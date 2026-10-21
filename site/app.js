(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const els = {
    search: $('search'), sort: $('sort'), direction: $('sort-direction'),
    online: $('online-only'), priced: $('priced-only'), sources: $('source-tooltips'), favorites: $('favorites-only'),
    refresh: $('refresh-button'), export: $('export-button'), body: $('table-body'),
    head: $('table-head'), count: $('result-count'), bottomCount: $('bottom-count'),
    progressArea: $('progress-area'), progressLabel: $('progress-label'), progressCount: $('progress-count'),
    progressBar: $('progress-bar'), error: $('error-banner'), heroStatus: $('hero-status'),
    statTotal: $('stat-total'),
    tooltip: $('arcane-tooltip'),
  };

  const state = {
    items: [], prices: new Map(), favorites: readFavorites(), view: 'compare',
    favoritesOnly: false, sort: 'name', direction: 'asc',
    active: false, processed: 0, failed: 0, completedAt: 0, volumeUpdatedAt: 0,
    renderPending: false, loadingCatalog: true, fetchSequence: 0, initialLoad: true, catalog: new Map(), tooltipTrigger: null, tooltipHideTimer: null,
  };

  function readSourcePreference() {
    try { return localStorage.getItem('arcane-atlas-source-tooltips') !== 'off'; }
    catch { return true; }
  }
  els.sources.checked = readSourcePreference();

  function readFavorites() {
    try { return new Set(JSON.parse(localStorage.getItem('arcane-atlas-favorites') || '[]')); }
    catch { return new Set(); }
  }
  function persistFavorites() {
    try { localStorage.setItem('arcane-atlas-favorites', JSON.stringify([...state.favorites])); } catch {}
  }
  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  }
  function n(value) {
    return value == null || !Number.isFinite(Number(value)) ? null : Number(value);
  }
  function showPrice(value, cls) {
    const amount = n(value);
    return amount == null
      ? '<span class="amount empty">—</span>'
      : `<span class="amount ${cls}">${esc(amount.toLocaleString('en-US', { maximumFractionDigits: 2 }))}</span><span class="amount-pt">p</span>`;
  }
  function getSide(item, rank) {
    const quotes = state.prices.get(item.slug)?.quotes;
    return quotes?.[rank]?.[els.online.checked ? 'online' : 'recent'] || null;
  }
  function getSpread(side) {
    return side && side.ask != null && side.bid != null ? side.ask - side.bid : null;
  }
  function countSide(side) { return side ? side.sellers + side.buyers : 0; }
  function volume(item) { return countSide(getSide(item, 'rank0')) + countSide(getSide(item, 'rankMax')); }
  function salesVolume(item) {
    const data = state.prices.get(item.slug)?.sales48h;
    return data?.[state.view === 'compare' ? 'allRanks' : state.view] ?? null;
  }
  function showVolume(value) {
    return value == null ? '<span class="amount empty">—</span>'
      : `<span class="listing-count">${Number(value).toLocaleString('en-US')}</span>`;
  }
  function sortVal(item) {
    switch (state.sort) {
      case 'name': return item.name.toLocaleLowerCase();
      case 'rank0.ask': return getSide(item, 'rank0')?.ask ?? null;
      case 'rank0.bid': return getSide(item, 'rank0')?.bid ?? null;
      case 'rankMax.ask': return getSide(item, 'rankMax')?.ask ?? null;
      case 'rankMax.bid': return getSide(item, 'rankMax')?.bid ?? null;
      case 'spread': return getSpread(getSide(item, 'rankMax'));
      case 'rank0.spread': return getSpread(getSide(item, 'rank0'));
      case 'volume': return volume(item);
      case 'sales48h': return salesVolume(item);
      default: return item.name.toLocaleLowerCase();
    }
  }

  function visibleItems() {
    const q = els.search.value.trim().toLocaleLowerCase();
    return state.items.filter((item) => {
      if (q && !item.name.toLocaleLowerCase().includes(q)) return false;
      if (state.favoritesOnly && !state.favorites.has(item.slug)) return false;
      if (els.priced.checked) {
        const ranks = state.view === 'compare' ? ['rank0', 'rankMax'] : [state.view];
        const sides = ranks.map((rank) => getSide(item, rank));
        if (!sides.some((s) => s && (s.ask != null || s.bid != null))) return false;
      }
      return true;
    }).sort((a, b) => {
      const va = sortVal(a), vb = sortVal(b);
      if (va == null && vb != null) return 1;
      if (vb == null && va != null) return -1;
      if (va == null && vb == null) return a.name.localeCompare(b.name);
      let comparison = typeof va === 'string' ? va.localeCompare(vb) : va - vb;
      if (comparison === 0) comparison = a.name.localeCompare(b.name);
      return state.direction === 'desc' ? -comparison : comparison;
    });
  }

  function header(label, field, note) {
    const current = state.sort === field;
    return `<th scope="col" data-sort="${esc(field)}" aria-sort="${current ? (state.direction === 'asc' ? 'ascending' : 'descending') : 'none'}">${label}${current ? `<span class="th-sort">${state.direction === 'asc' ? '↑' : '↓'}</span>` : ''}${note ? `<span class="th-unit">${note}</span>` : ''}</th>`;
  }
  function renderHeader() {
    let columns;
    if (state.view === 'compare') {
      columns = [
        header('Arcane', 'name', 'Item / max rank'), header('Rank 0 · Sell', 'rank0.ask', 'Lowest asking price'),
        header('Rank 0 · Buy', 'rank0.bid', 'Highest buying offer'), header('Max · Sell', 'rankMax.ask', 'Lowest asking price'),
        header('Max · Buy', 'rankMax.bid', 'Highest buying offer'), header('Spread', 'spread', 'At max rank'),
        header('Listings', 'volume', 'Both displayed ranks'), header('48h volume', 'sales48h', 'Reported closed orders · all ranks'), '<th scope="col" data-sort="none">Actions</th>',
      ];
    } else {
      const rank = state.view, scope = rank === 'rank0' ? 'Rank 0' : 'Max rank';
      columns = [header('Arcane', 'name', 'Item / max rank'), header(`${scope} · Sell`, `${rank}.ask`, 'Lowest asking price'),
        header(`${scope} · Buy`, `${rank}.bid`, 'Highest buying offer'),
        header('Spread', rank === 'rankMax' ? 'spread' : 'rank0.spread', 'Ask minus bid'),
        header('Listings', 'volume', 'Selected rank'), header('48h volume', 'sales48h', 'Reported closed orders · selected rank'), '<th scope="col" data-sort="none">Actions</th>'];
    }
    els.head.innerHTML = `<tr>${columns.join('')}</tr>`;
  }
  function wikiUrl(item) {
    if (typeof item.wikiUrl === 'string' && /^https:\/\/wiki\.warframe\.com\/w\//.test(item.wikiUrl)) return item.wikiUrl;
    return `https://wiki.warframe.com/w/${encodeURIComponent(item.name.replaceAll(' ', '_'))}`;
  }
  function tooltipHtml(item) {
    const effect = item.effects || {};
    const effectKey = state.view === 'rank0' ? 'rank0' : 'rankMax';
    const effectText = typeof effect[effectKey] === 'string' && effect[effectKey].trim()
  ? effect[effectKey].replace(/\\r\\n|\\n|\\r/g, '\n').trim() : null;
    const shownRank = effectKey === 'rank0' ? 0
      : (Number.isInteger(effect.maxRank) ? effect.maxRank : item.maxRank);
    const rankLabel = shownRank > 0 ? `RANK ${shownRank}` : 'RANK 0';
    const description = effectText
      ? `<p class="effect-description">${esc(effectText)}</p>`
      : '<p class="effect-unavailable">Effect description not available in this snapshot. Check the Wiki for details.</p>';
    const sources = Array.isArray(item.acquisitionSources) ? item.acquisitionSources : [];
    const entries = sources.slice(0, 4).map((source) => {
      const name = esc(source.location || 'Unspecified location');
      const chance = typeof source.chance === 'number' && Number.isFinite(source.chance)
        ? `<span class="acquisition-chance">${esc(source.chance.toLocaleString('en-US', { maximumFractionDigits: 3 }))}%</span>` : '';
      return `<li><span>${name}</span>${chance}</li>`;
    }).join('');
    const summary = sources.length ? `<ul class="acquisition-list">${entries}</ul>`
      : `<p class="acquisition-empty">${item.acquisitionStatus === 'documented' ? 'No drop locations are listed for this arcane.' : 'Drop location data is not available in this snapshot.'}</p>`;
    const more = sources.length > 4 ? `<span>${sources.length - 4} more recorded source${sources.length - 4 === 1 ? '' : 's'} · Wiki for details</span>` : '<span>Vendors and special-event methods may differ</span>';
    return `<div class="acquisition-heading"><span>ARCANE DETAILS</span><strong>${esc(item.name)}</strong></div>
      <section class="effect-section"><div class="tooltip-section-heading">EFFECT <span class="effect-rank">${rankLabel}</span></div>${description}</section>
      <section class="sources-section"><div class="tooltip-section-heading">ACQUISITION · RECORDED DROPS</div>${summary}</section>
      <div class="acquisition-footer">${more}<span>Wiki ↗ in actions</span></div>`;
  }
  function cancelTooltipHide() {
    clearTimeout(state.tooltipHideTimer);
    state.tooltipHideTimer = null;
  }
  function scheduleTooltipHide() {
    cancelTooltipHide();
    state.tooltipHideTimer = setTimeout(hideTooltip, 160);
  }
  function hideTooltip() {
    cancelTooltipHide();
    if (state.tooltipTrigger) {
      state.tooltipTrigger.querySelector('a.item-name')?.removeAttribute('aria-describedby');
      state.tooltipTrigger = null;
    }
    els.tooltip.hidden = true;
  }
  function showTooltip(trigger) {
    cancelTooltipHide();
    if (!els.sources.checked || state.tooltipTrigger === trigger) return;
    const item = state.catalog.get(trigger.dataset.acquisition);
    if (!item) return;
    hideTooltip();
    state.tooltipTrigger = trigger;
    els.tooltip.innerHTML = tooltipHtml(item);
    els.tooltip.hidden = false;
    trigger.querySelector('a.item-name')?.setAttribute('aria-describedby', 'arcane-tooltip');
    positionTooltip(trigger);
  }
  function positionTooltip(trigger) {
    const rect = trigger.getBoundingClientRect();
    if (!trigger.isConnected || rect.bottom < 0 || rect.top > innerHeight) {
      hideTooltip();
      return;
    }
    els.tooltip.style.maxHeight = '';
    const bounds = els.tooltip.getBoundingClientRect();
    const margin = 12;
    const right = rect.right + margin;
    const left = rect.left - bounds.width - margin;
    let x, y;
    if (right + bounds.width <= innerWidth - margin || left >= margin) {
      x = right + bounds.width <= innerWidth - margin ? right : left;
      y = Math.max(margin, Math.min(rect.top, innerHeight - bounds.height - margin));
    } else {
      const below = innerHeight - rect.bottom - margin * 2;
      const above = rect.top - margin * 2;
      const placeBelow = below >= above;
      const space = Math.max(100, placeBelow ? below : above);
      els.tooltip.style.maxHeight = `${Math.min(450, space)}px`;
      const height = els.tooltip.getBoundingClientRect().height;
      x = Math.max(margin, Math.min(rect.left, innerWidth - bounds.width - margin));
      y = placeBelow ? rect.bottom + margin : rect.top - height - margin;
      y = Math.max(margin, Math.min(y, innerHeight - height - margin));
    }
    els.tooltip.style.left = `${x}px`;
    els.tooltip.style.top = `${y}px`;
  }

  function itemCell(item) {
    const icon = item.icon
      ? `<img class="item-icon" src="${esc(item.icon)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
      : '<span class="item-icon item-fallback" aria-hidden="true">✧</span>';
    const marketUrl = `https://warframe.market/items/${encodeURIComponent(item.slug)}`;
    return `<div class="item-cell" data-acquisition="${esc(item.slug)}">${icon}<div class="item-details"><a class="item-name" target="_blank" rel="noopener noreferrer" href="${marketUrl}">${esc(item.name)}</a><span class="item-sub">${item.maxRank > 0 ? `MAX RANK ${item.maxRank}` : 'UNRANKED'}</span></div></div>`;
  }
  function spreadCell(side) {
    const value = getSpread(side);
    if (value == null) return '<span class="amount empty">—</span>';
    return `<span class="spread${value < 0 ? ' negative' : ''}">${esc(value.toLocaleString('en-US', { maximumFractionDigits: 2 }))} p</span>`;
  }
  function row(item) {
    const rank0 = getSide(item, 'rank0'), rankMax = getSide(item, 'rankMax');
    const fav = state.favorites.has(item.slug);
    const marketUrl = `https://warframe.market/items/${encodeURIComponent(item.slug)}`;
    const action = `<td class="action-cell"><div class="action-group"><button type="button" class="favorite-btn${fav ? ' active' : ''}" aria-label="${fav ? 'Remove from' : 'Add to'} watchlist: ${esc(item.name)}" aria-pressed="${fav}" data-favorite="${esc(item.slug)}">${fav ? '★' : '☆'}</button><a class="outbound-link" href="${marketUrl}" target="_blank" rel="noopener noreferrer" title="View on Warframe.market" aria-label="Open ${esc(item.name)} on Warframe.market">Market ↗</a><a class="wiki-link" href="${esc(wikiUrl(item))}" target="_blank" rel="noopener noreferrer" title="Acquisition details on Warframe Wiki" aria-label="Open ${esc(item.name)} on Warframe Wiki">Wiki ↗</a></div></td>`;
    if (state.view === 'compare') {
      return `<tr><td>${itemCell(item)}</td><td>${showPrice(rank0?.ask, 'ask')}</td><td>${showPrice(rank0?.bid, 'bid')}</td><td>${showPrice(rankMax?.ask, 'ask')}</td><td>${showPrice(rankMax?.bid, 'bid')}</td><td>${spreadCell(rankMax)}</td><td><span class="listing-count">${volume(item)}</span></td><td>${showVolume(salesVolume(item))}</td>${action}</tr>`;
    }
    const side = state.view === 'rank0' ? rank0 : rankMax;
    return `<tr><td>${itemCell(item)}</td><td>${showPrice(side?.ask, 'ask')}</td><td>${showPrice(side?.bid, 'bid')}</td><td>${spreadCell(side)}</td><td><span class="listing-count">${countSide(side)}</span></td><td>${showVolume(salesVolume(item))}</td>${action}</tr>`;
  }

  function render() {
    hideTooltip();
    state.renderPending = false;
    renderHeader();
    const matches = visibleItems();
    const colSpan = state.view === 'compare' ? 9 : 7;
    if (matches.length === 0) {
      const message = state.loadingCatalog ? 'Loading the published arcane price snapshot…'
        : state.favoritesOnly && !state.favorites.size ? 'Your watchlist is empty. Star an arcane to save it here.'
        : 'No arcanes match your current filters.';
      els.body.innerHTML = `<tr><td colspan="${colSpan}" class="empty-state">${esc(message)}</td></tr>`;
    } else {
      els.body.innerHTML = matches.map(row).join('');
    }
    els.count.textContent = `${matches.length.toLocaleString()} arcane${matches.length === 1 ? '' : 's'} shown · ${state.items.length.toLocaleString()} tracked`;
    els.bottomCount.textContent = `Showing ${matches.length.toLocaleString()} of ${state.items.length.toLocaleString()} arcanes`;
    els.statTotal.textContent = state.items.length ? state.items.length.toLocaleString() : '—';
    const ready = !!state.items.length;
    els.export.disabled = !ready;
    els.refresh.disabled = state.active;
    els.refresh.classList.toggle('refreshing', state.active);
    updateProgress();
  }
  function scheduleRender() {
    if (!state.renderPending) {
      state.renderPending = true;
      requestAnimationFrame(render);
    }
  }
  function ageLabel(timestamp) {
    const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60000));
    if (minutes < 2) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.floor(minutes / 60);
    return hours < 48 ? `${hours} h ago` : `${Math.floor(hours / 24)} d ago`;
  }
  function updateProgress() {
    els.progressArea.hidden = !state.active;
    if (state.active) {
      els.progressCount.textContent = 'Downloading';
      els.progressLabel.textContent = 'Loading the published snapshot…';
      els.progressBar.style.width = '35%';
      els.heroStatus.textContent = state.items.length ? 'Checking for a newer snapshot…' : 'Loading market snapshot…';
    } else if (state.completedAt) {
      const age = Date.now() - state.completedAt;
      els.heroStatus.textContent = `${age > 3 * 3600000 ? 'Snapshot may be stale' : 'Latest published snapshot'} · ${ageLabel(state.completedAt)}`;
    } else {
      els.heroStatus.textContent = 'Awaiting initial scheduled price scan';
    }
  }
  function notice(message) { els.error.hidden = false; els.error.textContent = message; }
  function clearNotice() { els.error.hidden = true; els.error.textContent = ''; }

  async function connect() {
    const request = ++state.fetchSequence;
    const hadData = state.items.length > 0;
    state.active = true;
    if (!hadData) state.loadingCatalog = true;
    clearNotice();
    scheduleRender();
    try {
      const response = await fetch(`./data/prices.json?t=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`The published data file returned HTTP ${response.status}.`);
      const snapshot = await response.json();
      if (request !== state.fetchSequence) return;
      if (snapshot.schemaVersion !== 1 || !Array.isArray(snapshot.items) || !Array.isArray(snapshot.prices)) {
        throw new Error('The price snapshot has an unsupported format.');
      }
      if (!snapshot.items.length || !snapshot.completedAt) {
        if (!hadData) {
          state.items = [];
          state.catalog.clear();
          state.prices.clear();
          state.completedAt = 0;
          state.loadingCatalog = false;
        }
        notice('The site is published, but the first market scan has not completed yet. The site owner can start “Update prices & deploy” in GitHub Actions.');
        return;
      }
      state.items = snapshot.items;
      state.catalog = new Map(state.items.map((item) => [item.slug, item]));
      state.prices = new Map(snapshot.prices.map((p) => [p.slug, p]));
      state.completedAt = Number(snapshot.completedAt) || 0;
      state.volumeUpdatedAt = Number(snapshot.volumeUpdatedAt) || 0;
      state.failed = Number(snapshot.failed) || 0;
      state.processed = state.prices.size;
      state.loadingCatalog = false;
      if (state.failed) {
        notice(`${state.failed} arcane quotes were unavailable in this snapshot and are shown as missing.`);
      } else if (Date.now() - state.completedAt > 3 * 3600000) {
        notice('This snapshot is more than 3 hours old. Listed prices and trader online status may no longer be current.');
      }
    } catch (error) {
      if (request !== state.fetchSequence) return;
      state.loadingCatalog = false;
      notice(`Could not load the published price snapshot: ${error.message} Try reloading the page.`);
    } finally {
      if (request === state.fetchSequence) {
        state.active = false;
        scheduleRender();
      }
    }
  }

  function setSort(field) {
    if (field === 'none') return;
    if (state.sort === field) state.direction = state.direction === 'asc' ? 'desc' : 'asc';
    else {
      state.sort = field;
      state.direction = field === 'name' || field.endsWith('.ask') ? 'asc' : 'desc';
    }
    els.sort.value = state.sort;
    updateDirection();
    scheduleRender();
  }
  function updateDirection() {
    els.direction.textContent = state.direction === 'asc' ? '↑↓' : '↓↑';
    els.direction.title = `Current order: ${state.direction === 'asc' ? 'ascending' : 'descending'}. Click to reverse.`;
    els.direction.setAttribute('aria-label', els.direction.title);
  }
  function csvValue(value) { return `"${String(value ?? '').replaceAll('"', '""')}"`; }
  function exportCSV() {
    const headers = ['Arcane', 'Max Rank', 'Rank 0 Sell (pt)', 'Rank 0 Buy (pt)', 'Rank 0 Median Ask (pt)', 'Max Rank Sell (pt)', 'Max Rank Buy (pt)', 'Max Rank Median Ask (pt)', 'Max Rank Spread (pt)', 'Listings (both ranks)', '48h Volume (all ranks)', '48h Volume (rank 0)', '48h Volume (max rank)', 'Volume Updated At (UTC)', 'Market URL', 'Recorded Drop Sources', 'Wiki URL', 'Rank 0 Effect', 'Max Rank Effect'];
    const data = visibleItems().map((item) => {
      const r0 = getSide(item, 'rank0'), max = getSide(item, 'rankMax');
      return [item.name, item.maxRank, r0?.ask, r0?.bid, r0?.medianAsk, max?.ask, max?.bid, max?.medianAsk,
        getSpread(max), volume(item), state.prices.get(item.slug)?.sales48h?.allRanks,
        state.prices.get(item.slug)?.sales48h?.rank0, state.prices.get(item.slug)?.sales48h?.rankMax,
        state.volumeUpdatedAt ? new Date(state.volumeUpdatedAt).toISOString() : '',
        `https://warframe.market/items/${encodeURIComponent(item.slug)}`,
        (item.acquisitionSources || []).map((source) => source.location).join('; '), wikiUrl(item),
        item.effects?.rank0, item.effects?.rankMax].map(csvValue).join(',');
    });
    const csv = '\uFEFF' + [headers.map(csvValue).join(','), ...data].join('\r\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `arcane-atlas-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  els.search.addEventListener('input', scheduleRender);
  els.online.addEventListener('change', scheduleRender);
  els.priced.addEventListener('change', scheduleRender);
  els.sources.addEventListener('change', () => {
    if (!els.sources.checked) hideTooltip();
    try { localStorage.setItem('arcane-atlas-source-tooltips', els.sources.checked ? 'on' : 'off'); } catch {}
  });
  els.sort.addEventListener('change', () => setSort(els.sort.value));
  els.direction.addEventListener('click', () => { state.direction = state.direction === 'asc' ? 'desc' : 'asc'; updateDirection(); scheduleRender(); });
  els.refresh.addEventListener('click', connect);
  els.export.addEventListener('click', exportCSV);
  els.favorites.addEventListener('click', () => {
    state.favoritesOnly = !state.favoritesOnly;
    els.favorites.classList.toggle('active', state.favoritesOnly);
    els.favorites.setAttribute('aria-pressed', String(state.favoritesOnly));
    els.favorites.querySelector('span').textContent = state.favoritesOnly ? '★' : '☆';
    scheduleRender();
  });
  document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => {
    state.view = button.dataset.view;
    document.querySelectorAll('[data-view]').forEach((element) => {
      element.classList.toggle('active', element === button);
      element.setAttribute('aria-pressed', String(element === button));
    });
    scheduleRender();
  }));
  els.body.addEventListener('pointerover', (event) => {
    const trigger = event.target.closest('[data-acquisition]');
    if (trigger && !trigger.contains(event.relatedTarget)) showTooltip(trigger);
  });
  els.body.addEventListener('pointerout', (event) => {
    if (state.tooltipTrigger && !state.tooltipTrigger.contains(event.relatedTarget) &&
        !els.tooltip.contains(event.relatedTarget)) scheduleTooltipHide();
  });
  els.tooltip.addEventListener('pointerenter', cancelTooltipHide);
  els.tooltip.addEventListener('pointerleave', scheduleTooltipHide);
  els.body.addEventListener('focusin', (event) => {
    const trigger = event.target.closest('[data-acquisition]');
    if (trigger) showTooltip(trigger);
  });
  els.body.addEventListener('focusout', (event) => {
    if (state.tooltipTrigger && !state.tooltipTrigger.contains(event.relatedTarget)) hideTooltip();
  });
  window.addEventListener('scroll', () => { if (state.tooltipTrigger) positionTooltip(state.tooltipTrigger); }, true);
  window.addEventListener('resize', hideTooltip);
  els.head.addEventListener('click', (event) => { const th = event.target.closest('th[data-sort]'); if (th) setSort(th.dataset.sort); });
  els.body.addEventListener('click', (event) => {
    const button = event.target.closest('[data-favorite]');
    if (!button) return;
    const slug = button.dataset.favorite;
    if (state.favorites.has(slug)) state.favorites.delete(slug);
    else state.favorites.add(slug);
    persistFavorites();
    scheduleRender();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === '/' && !event.ctrlKey && !event.altKey && !event.metaKey &&
        !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) {
      event.preventDefault(); els.search.focus();
    }
  });
  updateDirection();
  connect();
  setInterval(() => { if (!document.hidden && !state.active) connect(); }, 5 * 60 * 1000);
})();
