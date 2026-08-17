(() => {
  'use strict';

  const CANONICAL_FEED = 'scraper/data/denuncias.json';
  const MANUAL_FEED = 'scraper/data/fuentes-x.json';
  const MEDIA_INDEX = 'scraper/data/x-media-index.json';
  const PAGE_SIZE = 24;

  const state = {
    cases: [], filtered: [], visible: PAGE_SIZE,
    media: 'all', category: 'all', sort: 'newest', query: '', activeCase: null
  };

  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));

  const escapeHtml = (value) => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');

  const asArray = (value) => {
    if (value == null || value === '') return [];
    if (Array.isArray(value)) return value;
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (!trimmed) return [];
      try { const parsed = JSON.parse(trimmed); return Array.isArray(parsed) ? parsed : [parsed]; } catch (_) { return [trimmed]; }
    }
    return [value];
  };

  const factText = (value) => {
    if (value == null || value === '') return '';
    if (Array.isArray(value)) return value.map(factText).filter(Boolean).join(' · ');
    if (typeof value === 'object') return value.name || value.label || value.value || JSON.stringify(value);
    return String(value).trim();
  };

  function extractPost(url) {
    const match = String(url || '').match(/https?:\/\/(?:www\.)?(?:x|twitter)\.com\/([^/]+)\/status\/(\d+)/i);
    return match ? { username: match[1], tweetId: match[2], url: `https://x.com/${match[1]}/status/${match[2]}` } : null;
  }

  function cleanCategory(value) {
    return String(value || 'por-clasificar').trim().toLowerCase().replaceAll('_', '-');
  }

  function labelize(value) {
    const v = String(value || 'por-clasificar').replaceAll('_', '-');
    return v.split('-').filter(Boolean).map(p => p[0].toUpperCase() + p.slice(1)).join(' ');
  }

  function chooseImage(images) {
    const valid = asArray(images).map(x => typeof x === 'object' ? (x.url || x.src || x.image) : x)
      .filter(url => typeof url === 'string' && /^https?:\/\//i.test(url));
    return valid.find(url => /amplify_video_thumb|ext_tw_video_thumb|\/media\//i.test(url) && !/profile_images/i.test(url))
      || valid.find(url => !/profile_images|_normal\./i.test(url)) || '';
  }

  function guessType(obj, url = '') {
    const declared = String(obj?.type || obj?.media_type || '').toLowerCase();
    if (declared) return declared;
    if (obj?.has_video === true || obj?.video_url) return 'video';
    if (obj?.thumbnail_url || obj?.poster_url || obj?.image || chooseImage(obj?.images)) return 'image';
    if (extractPost(url)) return 'post';
    if (/\.pdf(?:$|\?)/i.test(url)) return 'document';
    return 'source';
  }

  function evidenceFrom(value, fallback = {}) {
    if (!value) return null;
    if (typeof value === 'string') {
      if (!/^https?:\/\//i.test(value)) return { id: `note-${Math.random()}`, type: 'note', text: value, url: '', thumbnail: '', videoUrl: '', author: '', createdAt: '', capturedAt: '', sourceStatus: 'recorded' };
      value = { url: value };
    }
    if (typeof value !== 'object') return null;

    const rawUrl = value.url || value.source_url || value.tweet_url || value.permalink || fallback.url || '';
    const post = extractPost(rawUrl);
    const images = value.images || value.image_urls || fallback.images || [];
    const thumbnail = value.thumbnail_url || value.poster_url || value.preview_url || value.thumbnail || value.image || chooseImage(images) || fallback.thumbnail || '';
    const videoUrl = value.video_url || fallback.videoUrl || '';
    const tweetId = String(value.tweet_id || post?.tweetId || fallback.tweetId || '');
    const author = value.author || value.username || value.display_name || post?.username || fallback.author || '';
    const type = guessType({ ...fallback, ...value }, rawUrl);
    return {
      id: String(value.evidence_id || value.id || tweetId || rawUrl || `ev-${Math.random()}`),
      tweetId,
      type,
      url: post?.url || rawUrl,
      thumbnail,
      videoUrl,
      author,
      text: String(value.text || value.resumen || value.description || value.note || fallback.text || '').trim(),
      createdAt: value.created_at || value.tweet_created_at || fallback.createdAt || '',
      capturedAt: value.captured_at || value.scraped_at || value.added_at || fallback.capturedAt || '',
      sourceStatus: value.source_status || value.status || fallback.sourceStatus || 'recorded',
      label: value.label || value.title || '',
      manual: value.manual === true || fallback.manual === true,
      hasVideo: value.has_video === true || type === 'video' || Boolean(videoUrl)
    };
  }

  function primaryDate(item) {
    return item?.tweet_created_at || item?.created_at || item?.published_at || item?.captured_at || item?.scraped_at || item?.added_at || '';
  }

  function caseFromRecord(item, index, manual = false) {
    if (!item || typeof item !== 'object') return null;
    const post = extractPost(item.url || item.source_url || '');
    const id = String(item.expediente_id || item.case_id || (post?.tweetId ? `X-${post.tweetId}` : `EV-SOURCE-${index + 1}`));
    const evidence = [];
    const primary = evidenceFrom(item, {
      url: item.url || item.source_url || '', tweetId: post?.tweetId || '', author: item.username || post?.username || '',
      text: item.text || item.resumen || '', images: item.images, videoUrl: item.video_url || '',
      createdAt: item.created_at || '', capturedAt: item.captured_at || item.scraped_at || item.added_at || '', manual
    });
    if (primary && (primary.url || primary.videoUrl || primary.thumbnail || primary.text)) evidence.push(primary);

    ['evidencias', 'fuentes', 'sources', 'source_tweets'].forEach(field => {
      asArray(item[field]).forEach((entry, i) => {
        const ev = evidenceFrom(entry, { manual, sourceStatus: item.status, capturedAt: item.captured_at || item.scraped_at || '', text: '' });
        if (ev) { if (!ev.id) ev.id = `${id}-E${i + 2}`; evidence.push(ev); }
      });
    });

    const summary = String(item.resumen || item.summary || item.text || item.contexto || '').trim();
    const titleSeed = String(item.titulo || item.title || '').trim();
    const title = titleSeed || (summary ? summary.replace(/\s+/g, ' ').slice(0, 118) + (summary.length > 118 ? '…' : '') : `Expediente ${id}`);
    const related = asArray(item.relacionados || item.related_expedientes || item.connections).map(x => typeof x === 'object' ? (x.expediente_id || x.id || x.target) : x).filter(Boolean).map(String);

    return {
      id, title, summary,
      category: cleanCategory(item.category), categoryLabel: item.category_label || '',
      status: String(item.status || item.editorial_status || item.source_status || (manual ? 'registrado' : 'published')),
      severity: String(item.severity || 'info'),
      context: typeof item.contexto === 'string' ? item.contexto : (item.context || ''),
      facts: {
        who: factText(item.quien ?? item.who ?? item.actores ?? item.personas),
        when: factText(item.cuando ?? item.when ?? item.fecha_hecho ?? item.event_date ?? item.created_at),
        where: factText(item.donde ?? item.where ?? item.ubicacion ?? item.location),
        what: factText(item.que ?? item.what ?? item.hecho ?? item.resumen),
        why: factText(item.por_que ?? item.why ?? item.motivo),
        how: factText(item.como ?? item.how ?? item.modalidad)
      },
      related, tags: asArray(item.tags).map(x => typeof x === 'object' ? (x.name || x.label || '') : String(x)).filter(Boolean),
      evidence, manual, explicitSourceCount: Number(item.source_count || 0),
      date: primaryDate(item), index
    };
  }

  function evidenceKey(ev) { return ev.url || ev.tweetId || ev.id; }
  function dedupeEvidence(list) {
    const map = new Map();
    list.forEach(ev => {
      const key = evidenceKey(ev);
      if (!key) return;
      const old = map.get(key);
      if (!old) map.set(key, ev);
      else map.set(key, { ...old, ...Object.fromEntries(Object.entries(ev).filter(([,v]) => v !== '' && v != null && v !== false)), hasVideo: old.hasVideo || ev.hasVideo, manual: old.manual || ev.manual });
    });
    return Array.from(map.values());
  }

  function mergeCase(target, incoming) {
    if (!target) return incoming;
    const facts = { ...target.facts };
    Object.keys(incoming.facts || {}).forEach(k => { if (!facts[k] && incoming.facts[k]) facts[k] = incoming.facts[k]; });
    return {
      ...target,
      title: target.title && !/^Expediente /.test(target.title) ? target.title : incoming.title,
      summary: target.summary || incoming.summary,
      category: target.category !== 'por-clasificar' ? target.category : incoming.category,
      categoryLabel: target.categoryLabel || incoming.categoryLabel,
      status: target.status || incoming.status,
      severity: target.severity !== 'info' ? target.severity : incoming.severity,
      context: target.context || incoming.context,
      facts,
      related: Array.from(new Set([...(target.related || []), ...(incoming.related || [])])),
      tags: Array.from(new Set([...(target.tags || []), ...(incoming.tags || [])])),
      evidence: dedupeEvidence([...(target.evidence || []), ...(incoming.evidence || [])]),
      manual: target.manual || incoming.manual,
      explicitSourceCount: Math.max(target.explicitSourceCount || 0, incoming.explicitSourceCount || 0),
      date: target.date || incoming.date
    };
  }

  function applyMediaMeta(cases, mediaPayload) {
    const byTweet = new Map();
    (mediaPayload.sources || []).forEach(meta => { if (meta?.tweet_id) byTweet.set(String(meta.tweet_id), meta); });
    cases.forEach(c => {
      c.evidence = c.evidence.map(ev => {
        const meta = ev.tweetId ? byTweet.get(String(ev.tweetId)) : null;
        if (!meta) return ev;
        return {
          ...ev,
          type: meta.media_type || (meta.has_video === true ? 'video' : ev.type),
          hasVideo: meta.has_video === true || ev.hasVideo,
          thumbnail: meta.thumbnail_url || meta.poster_url || meta.preview_url || ev.thumbnail,
          videoUrl: meta.video_url || ev.videoUrl,
          capturedAt: meta.captured_at || ev.capturedAt,
          createdAt: meta.tweet_created_at || ev.createdAt,
          sourceStatus: meta.source_status || ev.sourceStatus,
          author: meta.username || ev.author
        };
      });
    });

    const represented = new Set(cases.flatMap(c => c.evidence.map(ev => ev.tweetId).filter(Boolean)));
    (mediaPayload.sources || []).forEach((meta, i) => {
      const tid = String(meta?.tweet_id || '');
      if (!tid || represented.has(tid) || !meta.url) return;
      const ev = evidenceFrom(meta, { manual: true });
      cases.push({
        id: meta.expediente_id || `X-${tid}`, title: `Fuente registrada por @${meta.username || 'X'}`,
        summary: 'Fuente pública capturada y pendiente de enriquecimiento editorial.',
        category: cleanCategory(meta.category), categoryLabel: meta.category_label || '', status: meta.source_status || 'captured', severity: 'info', context: '',
        facts: { who: '', when: meta.tweet_created_at || '', where: '', what: '', why: '', how: '' }, related: [], tags: [], evidence: [ev], manual: true, explicitSourceCount: 1, date: meta.tweet_created_at || meta.captured_at || '', index: 100000 + i
      });
    });
  }

  function buildCases(canonicalPayload, manualPayload, mediaPayload) {
    const canonical = Array.isArray(canonicalPayload) ? canonicalPayload : (canonicalPayload.denuncias || canonicalPayload.items || []);
    const manual = Array.isArray(manualPayload) ? manualPayload : (manualPayload.sources || manualPayload.items || []);
    const map = new Map();
    canonical.forEach((item, i) => { const c = caseFromRecord(item, i, false); if (c) map.set(c.id, mergeCase(map.get(c.id), c)); });
    manual.forEach((item, i) => { const c = caseFromRecord(item, i, true); if (c) map.set(c.id, mergeCase(map.get(c.id), c)); });
    const cases = Array.from(map.values());
    applyMediaMeta(cases, mediaPayload || {});
    const regrouped = new Map();
    cases.forEach(c => { c.evidence = dedupeEvidence(c.evidence); regrouped.set(c.id, mergeCase(regrouped.get(c.id), c)); });
    return Array.from(regrouped.values());
  }

  function dateValue(value) { const n = Date.parse(value || ''); return Number.isFinite(n) ? n : 0; }
  function caseDate(c) {
    return Math.max(dateValue(c.date), ...c.evidence.map(ev => Math.max(dateValue(ev.createdAt), dateValue(ev.capturedAt))));
  }
  function formatDate(value) {
    if (!value) return 'FECHA EN REVISIÓN';
    const d = new Date(value); if (Number.isNaN(d.getTime())) return String(value).slice(0, 10).toUpperCase();
    return new Intl.DateTimeFormat('es-VE', { day:'2-digit', month:'short', year:'numeric' }).format(d).replaceAll('.','').toUpperCase();
  }
  function latestDate(c) {
    const candidates = [c.date, ...c.evidence.flatMap(ev => [ev.createdAt, ev.capturedAt])].filter(Boolean).sort((a,b) => dateValue(b)-dateValue(a));
    return candidates[0] || '';
  }
  function evidenceCount(c) { return Math.max(c.evidence.length, c.explicitSourceCount || 0); }
  function hasVideo(c) { return c.evidence.some(ev => ev.hasVideo || ev.type === 'video' || ev.videoUrl); }
  function hasImage(c) { return c.evidence.some(ev => ev.type === 'image' || Boolean(ev.thumbnail)); }
  function statusLabel(status) {
    const s = String(status || '').toLowerCase();
    if (/verific|corrobor/.test(s)) return 'Verificado';
    if (/investig|review|revision|revisi/.test(s)) return 'En investigación';
    if (/draft|borrador/.test(s)) return 'Borrador';
    if (/published|publicado/.test(s)) return 'Publicado';
    if (/captur|record|registr/.test(s)) return 'Fuente registrada';
    return labelize(s || 'registrado');
  }
  function statusClass(status) { const s = String(status || '').toLowerCase(); return /verific|corrobor/.test(s) ? 'verified' : (/investig|review|revision/.test(s) ? 'review' : (/draft/.test(s) ? 'draft' : '')); }

  function mediaPriority(ev) { return ev.thumbnail ? 0 : (ev.videoUrl ? 1 : (ev.hasVideo ? 2 : (ev.type === 'image' ? 3 : 4))); }
  function visualTemplate(ev, compact = false) {
    const type = ev.hasVideo || ev.type === 'video' ? 'VIDEO' : (ev.type === 'image' || ev.thumbnail ? 'IMAGEN' : labelize(ev.type || 'FUENTE').toUpperCase());
    if (ev.thumbnail) return `<img src="${escapeHtml(ev.thumbnail)}" alt="Miniatura de evidencia" loading="lazy" referrerpolicy="no-referrer"><span class="case-media-type">${type}</span>`;
    if (ev.videoUrl && !/^https?:\/\/(?:www\.)?(?:x|twitter)\.com/i.test(ev.videoUrl)) return `<video class="case-thumb-video" src="${escapeHtml(ev.videoUrl)}#t=0.12" muted playsinline preload="metadata"></video><span class="case-media-type">VIDEO</span>`;
    const author = ev.author ? `@${ev.author}` : type;
    return `<div class="case-poster"><span class="case-poster-icon">${ev.hasVideo || ev.type === 'video' ? '▶' : '▧'}</span><small>${type}</small><strong>${escapeHtml(author)}</strong></div>`;
  }

  function cardMedia(c) {
    const items = [...c.evidence].sort((a,b) => mediaPriority(a)-mediaPriority(b)).slice(0,3);
    if (!items.length) items.push({ type:'source', author:'archivo', text:'' });
    const cls = items.length === 1 ? 'one' : (items.length === 2 ? 'two' : 'three');
    return `<div class="case-card-media-grid ${cls}">${items.map(ev => `<div class="case-media-cell">${visualTemplate(ev, true)}</div>`).join('')}</div>`;
  }

  function miniFact(label, value) { return value ? `<span><b>${label}</b> ${escapeHtml(value)}</span>` : ''; }
  function cardTemplate(c) {
    const count = evidenceCount(c);
    return `<button type="button" class="case-card" data-case="${escapeHtml(c.id)}">
      <div class="case-card-media">${cardMedia(c)}<span class="case-card-status ${statusClass(c.status)}">${escapeHtml(statusLabel(c.status))}</span><span class="case-card-count">${count} ${count === 1 ? 'evidencia' : 'evidencias'}</span></div>
      <div class="case-card-body"><div class="case-card-top"><span class="case-exp-id">${escapeHtml(c.id)}</span><time class="case-date">${escapeHtml(formatDate(latestDate(c)))}</time></div>
      <h2>${escapeHtml(c.title)}</h2><p class="case-summary">${escapeHtml(c.summary || 'Expediente documental en proceso de enriquecimiento editorial.')}</p>
      <div class="case-facts-mini">${miniFact('Quién', c.facts.who)}${miniFact('Dónde', c.facts.where)}${miniFact('Cuándo', c.facts.when)}${miniFact('Conecta', c.related.length ? `${c.related.length} expediente${c.related.length === 1 ? '' : 's'}` : '')}</div>
      <div class="case-card-footer"><span class="case-category">${escapeHtml(c.categoryLabel || labelize(c.category))}</span><span class="case-open">Abrir expediente →</span></div></div></button>`;
  }

  function initVideoThumbs(root = document) {
    $$('.case-thumb-video', root).forEach(video => {
      video.addEventListener('loadedmetadata', () => { try { if (video.duration > .15) video.currentTime = Math.min(.15, video.duration / 10); } catch (_) {} }, { once:true });
      video.addEventListener('error', () => { const cell = video.parentElement; if (cell) cell.innerHTML = `<div class="case-poster"><span class="case-poster-icon">▶</span><small>VIDEO</small><strong>Media registrado</strong></div>`; }, { once:true });
    });
  }

  function updateMetrics() {
    $('#metricCases').textContent = state.cases.length.toLocaleString('es-VE');
    $('#metricEvidence').textContent = state.cases.reduce((n,c) => n + evidenceCount(c), 0).toLocaleString('es-VE');
    $('#metricVideo').textContent = state.cases.reduce((n,c) => n + c.evidence.filter(ev => ev.hasVideo || ev.type === 'video' || ev.videoUrl).length, 0).toLocaleString('es-VE');
    $('#metricMulti').textContent = state.cases.filter(c => evidenceCount(c) > 1).length.toLocaleString('es-VE');
  }

  function populateCategories() {
    const counts = new Map(); state.cases.forEach(c => counts.set(c.category, (counts.get(c.category) || 0) + 1));
    $('#categoryFilter').innerHTML = '<option value="all">Todas</option>' + Array.from(counts.entries()).sort((a,b)=>b[1]-a[1]).map(([cat,n]) => `<option value="${escapeHtml(cat)}">${escapeHtml(labelize(cat))} · ${n}</option>`).join('');
  }

  function applyFilters() {
    const q = state.query.toLocaleLowerCase('es');
    let rows = state.cases.filter(c => {
      if (state.media === 'video' && !hasVideo(c)) return false;
      if (state.media === 'image' && !hasImage(c)) return false;
      if (state.media === 'multi' && evidenceCount(c) < 2) return false;
      if (state.media === 'manual' && !c.manual) return false;
      if (state.category !== 'all' && c.category !== state.category) return false;
      if (q) {
        const haystack = [c.id,c.title,c.summary,c.category,c.categoryLabel,c.status,c.context,...Object.values(c.facts),...c.tags,...c.related,...c.evidence.flatMap(ev => [ev.author,ev.text,ev.url,ev.type])].join(' ').toLocaleLowerCase('es');
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
    rows.sort((a,b) => state.sort === 'oldest' ? caseDate(a)-caseDate(b) : state.sort === 'evidence' ? evidenceCount(b)-evidenceCount(a) || caseDate(b)-caseDate(a) : state.sort === 'id' ? a.id.localeCompare(b.id,'es',{numeric:true}) : caseDate(b)-caseDate(a));
    state.filtered = rows; render();
  }

  function render() {
    const visible = state.filtered.slice(0,state.visible); $('#archiveGrid').innerHTML = visible.map(cardTemplate).join(''); initVideoThumbs($('#archiveGrid'));
    $$('.case-card', $('#archiveGrid')).forEach(card => card.addEventListener('click', () => openCase(card.dataset.case)));
    $('#archiveCount').textContent = `${state.filtered.length.toLocaleString('es-VE')} expedientes · mostrando ${visible.length.toLocaleString('es-VE')}`;
    $('#loadMore').hidden = visible.length >= state.filtered.length; $('#archiveEmpty').hidden = state.filtered.length !== 0;
  }

  function factCell(label, value) { return `<div class="drawer-fact ${value ? '' : 'is-empty'}"><span>${label}</span><p>${escapeHtml(value || 'Por establecer / en revisión')}</p></div>`; }

  function stageTemplate(ev) {
    const visual = `<div class="drawer-stage-visual">${visualTemplate(ev)}</div>`;
    const xPost = extractPost(ev.url);
    const actions = `${xPost ? `<button type="button" data-load-x="${escapeHtml(ev.id)}">Cargar publicación</button>` : ''}${ev.url ? `<a href="${escapeHtml(ev.url)}" target="_blank" rel="noopener noreferrer">Fuente ↗</a>` : ''}`;
    return `<div class="drawer-evidence-stage" data-stage-evidence="${escapeHtml(ev.id)}">${visual}<div class="drawer-stage-info"><div><strong>${escapeHtml(ev.author ? '@'+ev.author : labelize(ev.type))}</strong><span>${escapeHtml(formatDate(ev.createdAt || ev.capturedAt))} · ${escapeHtml(statusLabel(ev.sourceStatus))}</span></div><div class="drawer-stage-actions">${actions}</div></div></div>`;
  }

  function evidenceRow(ev, index, activeId) {
    return `<article class="drawer-evidence ${ev.id === activeId ? 'is-active' : ''}" data-evidence-row="${escapeHtml(ev.id)}"><div class="drawer-evidence-visual">${visualTemplate(ev,true)}</div><div class="drawer-evidence-body"><div class="drawer-evidence-top"><strong>E${String(index+1).padStart(2,'0')} · ${escapeHtml(ev.author ? '@'+ev.author : labelize(ev.type))}</strong><span>${escapeHtml(labelize(ev.type))}</span></div><p class="drawer-evidence-copy">${escapeHtml(ev.text || 'Evidencia registrada; consulta la fuente original para su contenido completo.')}</p><div class="drawer-evidence-actions"><button type="button" data-preview-evidence="${escapeHtml(ev.id)}">Ver en ficha</button>${ev.url ? `<a href="${escapeHtml(ev.url)}" target="_blank" rel="noopener noreferrer">Original ↗</a>` : ''}</div></div></article>`;
  }

  function drawerHtml(c, selectedEvidence) {
    const ev = selectedEvidence || c.evidence[0] || { id:'none', type:'source', text:'', author:'' };
    const related = c.related.length ? c.related.map(id => `<button type="button" class="drawer-connection" data-related="${escapeHtml(id)}" ${state.cases.some(x=>x.id===id)?'':'disabled'}>${escapeHtml(id)}${state.cases.some(x=>x.id===id)?' →':''}</button>`).join('') : '<span class="drawer-disclaimer">Sin conexiones editoriales registradas.</span>';
    const tags = c.tags.length ? `<div class="drawer-tags">${c.tags.map(t=>`<span class="drawer-tag">${escapeHtml(t)}</span>`).join('')}</div>` : '';
    return `<div class="drawer-hero"><div class="drawer-state-row"><span class="drawer-state">${escapeHtml(statusLabel(c.status))}</span><time class="drawer-date">ACTUALIZADO ${escapeHtml(formatDate(latestDate(c)))}</time></div><h1>${escapeHtml(c.title)}</h1><p class="drawer-summary">${escapeHtml(c.summary || 'Expediente en proceso de enriquecimiento editorial.')}</p>${stageTemplate(ev)}${tags}</div>
      <section class="drawer-section"><div class="drawer-section-head"><div><span>Ficha del hecho</span><strong>Quién · cuándo · dónde · por qué</strong></div></div><div class="drawer-facts">${factCell('Qué ocurrió',c.facts.what)}${factCell('Quién',c.facts.who)}${factCell('Cuándo',c.facts.when)}${factCell('Dónde',c.facts.where)}${factCell('Por qué se investiga',c.facts.why)}${factCell('Cómo',c.facts.how)}</div></section>
      ${c.context ? `<section class="drawer-section"><div class="drawer-section-head"><div><span>Contexto</span><strong>Notas del expediente</strong></div></div><p class="drawer-context">${escapeHtml(c.context)}</p></section>` : ''}
      <section class="drawer-section"><div class="drawer-section-head"><div><span>Evidencias</span><strong>${evidenceCount(c)} ${evidenceCount(c)===1?'pieza registrada':'piezas registradas'}</strong></div></div><div class="drawer-evidence-list">${c.evidence.map((x,i)=>evidenceRow(x,i,ev.id)).join('') || '<p class="drawer-disclaimer">Todavía no hay evidencia material asociada.</p>'}</div></section>
      <section class="drawer-section"><div class="drawer-section-head"><div><span>Conexiones</span><strong>Expedientes relacionados</strong></div></div><div class="drawer-connections">${related}</div></section>
      <section class="drawer-section"><p class="drawer-disclaimer">Una evidencia registrada documenta que una fuente existe o fue capturada. No implica por sí sola que todas sus afirmaciones estén verificadas. El estado editorial del expediente indica el nivel de revisión.</p></section>`;
  }

  function wireDrawer(c, selectedId) {
    const content = $('#caseDrawerContent');
    $$('.case-thumb-video', content).forEach(() => {}); initVideoThumbs(content);
    $$('[data-preview-evidence]', content).forEach(btn => btn.addEventListener('click', () => renderDrawer(c, btn.dataset.previewEvidence)));
    $$('[data-related]', content).forEach(btn => btn.addEventListener('click', () => { if (!btn.disabled) openCase(btn.dataset.related); }));
    $$('[data-load-x]', content).forEach(btn => btn.addEventListener('click', () => {
      const ev = c.evidence.find(x => x.id === btn.dataset.loadX); if (ev) loadXIntoStage(ev);
    }));
  }

  function renderDrawer(c, evidenceId) {
    const ev = c.evidence.find(x => x.id === evidenceId) || c.evidence[0];
    $('#drawerCategory').textContent = c.categoryLabel || labelize(c.category); $('#drawerId').textContent = c.id;
    $('#caseDrawerContent').innerHTML = drawerHtml(c, ev); wireDrawer(c, ev?.id);
  }

  function openCase(id) {
    const c = state.cases.find(x => x.id === id); if (!c) return; state.activeCase = id; renderDrawer(c);
    $('#caseDrawerBackdrop').hidden = false; document.body.classList.add('case-drawer-open');
    requestAnimationFrame(() => { $('#caseDrawer').classList.add('is-open'); $('#caseDrawerBackdrop').classList.add('is-open'); $('#caseDrawer').setAttribute('aria-hidden','false'); });
    history.replaceState(null,'',`#expediente=${encodeURIComponent(id)}`);
  }

  function closeCase() {
    $('#caseDrawer').classList.remove('is-open'); $('#caseDrawerBackdrop').classList.remove('is-open'); $('#caseDrawer').setAttribute('aria-hidden','true'); document.body.classList.remove('case-drawer-open'); state.activeCase = null;
    setTimeout(() => { $('#caseDrawerBackdrop').hidden = true; }, 220);
    if (location.hash.startsWith('#expediente=')) history.replaceState(null,'',location.pathname + location.search + '#archivo');
  }

  function loadXWidgets() {
    if (window.twttr?.widgets) return Promise.resolve(window.twttr);
    if (window.__xWidgetsPromise) return window.__xWidgetsPromise;
    window.__xWidgetsPromise = new Promise((resolve,reject) => {
      const script = document.createElement('script'); script.src='https://platform.twitter.com/widgets.js'; script.async=true; script.charset='utf-8';
      const timeout = setTimeout(()=>reject(new Error('timeout')),7000);
      script.onload=()=>{ const started=Date.now(); const timer=setInterval(()=>{ if(window.twttr?.widgets){clearInterval(timer);clearTimeout(timeout);resolve(window.twttr);} else if(Date.now()-started>6500){clearInterval(timer);clearTimeout(timeout);reject(new Error('unavailable'));}},100);};
      script.onerror=()=>{clearTimeout(timeout);reject(new Error('failed'));}; document.head.appendChild(script);
    }); return window.__xWidgetsPromise;
  }

  async function loadXIntoStage(ev) {
    const stage = $('[data-stage-evidence]'); if (!stage) return;
    stage.innerHTML = '<div class="drawer-x-mount"><div class="drawer-x-loading">Cargando publicación original bajo demanda…</div></div>';
    const mount = $('.drawer-x-mount', stage); const post = extractPost(ev.url);
    if (!post) { stage.innerHTML = stageTemplate(ev); return; }
    try { const twttr = await loadXWidgets(); mount.innerHTML=''; const rendered = await twttr.widgets.createTweet(String(post.tweetId), mount,{theme:'dark',conversation:'none',cards:'visible',align:'center',dnt:true}); if(!rendered) throw new Error('unavailable'); }
    catch(_) { mount.innerHTML = `<div class="drawer-x-loading">El embed no está disponible.<br><br><a href="${escapeHtml(ev.url)}" target="_blank" rel="noopener noreferrer" style="color:var(--beige)">Abrir fuente original ↗</a></div>`; }
  }

  async function fetchJson(url, fallback) {
    const controller = new AbortController(); const timer = setTimeout(()=>controller.abort(),5000);
    try { const r = await fetch(`${url}?_=${Date.now()}`,{cache:'no-store',signal:controller.signal}); if(!r.ok) throw new Error(String(r.status)); return await r.json(); }
    catch(_) { return fallback; } finally { clearTimeout(timer); }
  }

  function wireControls() {
    $('#archiveSearch').addEventListener('input', e => { state.query=e.target.value.trim(); state.visible=PAGE_SIZE; applyFilters(); });
    $('#categoryFilter').addEventListener('change', e => { state.category=e.target.value; state.visible=PAGE_SIZE; applyFilters(); });
    $('#sortFilter').addEventListener('change', e => { state.sort=e.target.value; applyFilters(); });
    $$('#mediaFilters button').forEach(btn => btn.addEventListener('click',()=>{ $$('#mediaFilters button').forEach(x=>x.classList.remove('is-active')); btn.classList.add('is-active'); state.media=btn.dataset.media; state.visible=PAGE_SIZE; applyFilters(); }));
    $('#loadMore').addEventListener('click',()=>{ state.visible+=PAGE_SIZE; render(); });
    $('#clearFilters').addEventListener('click',()=>{ state.query='';state.category='all';state.media='all';state.visible=PAGE_SIZE;$('#archiveSearch').value='';$('#categoryFilter').value='all';$$('#mediaFilters button').forEach(x=>x.classList.toggle('is-active',x.dataset.media==='all'));applyFilters(); });
    $('#drawerClose').addEventListener('click',closeCase); $('#caseDrawerBackdrop').addEventListener('click',closeCase);
    document.addEventListener('keydown', e => { if(e.key==='Escape' && state.activeCase) closeCase(); else if(e.key==='/' && !/input|textarea|select/i.test(document.activeElement?.tagName||'')){e.preventDefault();$('#archiveSearch').focus();} });
  }

  async function init() {
    wireControls();
    const [canonical,manual,media] = await Promise.all([fetchJson(CANONICAL_FEED,{denuncias:[]}),fetchJson(MANUAL_FEED,{sources:[]}),fetchJson(MEDIA_INDEX,{sources:[]})]);
    state.cases = buildCases(canonical,manual,media); updateMetrics(); populateCategories(); applyFilters();
    const match = location.hash.match(/^#expediente=(.+)$/); if(match) setTimeout(()=>openCase(decodeURIComponent(match[1])),0);
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',init); else init();
})();
