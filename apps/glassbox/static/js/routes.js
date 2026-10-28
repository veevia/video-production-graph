/* Routes of the Glass Box UI and the redirects that keep older links working. Pure functions, no DOM. */

const encode = (value) => encodeURIComponent(value ?? '');

function splitHash(hash) {
  const raw = String(hash || '').replace(/^#/, '');
  const qi = raw.indexOf('?');
  const path = qi >= 0 ? raw.slice(0, qi) : raw;
  const query = new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : '');
  const segs = path.split('/').filter(Boolean).map((seg) => { try { return decodeURIComponent(seg); } catch { return seg; } });
  return { path, query, segs };
}

function withQuery(base, params) {
  const text = params.toString();
  return text ? `${base}?${text}` : base;
}

/* Film links: filmId is "job-<pid>" or "lib-<film_id>". Keys with empty values are left out. */
export function filmHref(filmId, query = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== null && value !== undefined && value !== '') params.set(key, String(value));
  }
  return withQuery(`#/f/${encode(filmId)}`, params);
}

export const hrefs = {
  inbox: () => '#/',
  films: (query = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) if (value) params.set(key, String(value));
    return withQuery('#/films', params);
  },
  activity: () => '#/activity',
  film: filmHref,
  video: (id, query = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) if (value !== null && value !== undefined && value !== '') params.set(key, String(value));
    return withQuery(`#/v/${encode(id)}`, params);
  },
};

/* The new address for an old one, or the same hash when it is already current. */
export function redirectOf(hash) {
  const text = String(hash || '');
  if (!text || text === '#' || text === '#/') return '#/';
  const { query, segs } = splitHash(text);
  if (segs[0] === 'needs-you' && segs.length === 1) return '#/';
  if (segs[0] === 'library' && segs.length === 1) return '#/films';
  if (segs[0] === 'p' && segs[1]) {
    const film = `#/f/job-${encode(segs[1])}`;
    const params = new URLSearchParams();
    const copy = (skip) => { for (const [key, value] of query) if (!skip.includes(key)) params.append(key, value); };
    if (segs.length === 2) {
      copy([]);
      return withQuery(film, params);
    }
    if (segs[2] === 'n' && segs[3] && segs.length === 4) {
      if (query.get('v')) params.set('v', query.get('v'));
      params.set('tab', 'pipeline');
      params.set('n', segs[3]);
      return withQuery(film, params);
    }
    if (segs[2] === 'v' && segs[3] && segs.length === 4) {
      params.set('v', segs[3]);
      if (query.get('n')) {
        params.set('tab', 'pipeline');
        params.set('n', query.get('n'));
        return withQuery(film, params);
      }
      params.set('tab', 'watch');
      copy(['v', 'n']);
      return withQuery(film, params);
    }
    if (segs[2] === 'gates' && segs.length === 3) {
      if (query.get('v')) params.set('v', query.get('v'));
      params.set('tab', 'pipeline');
      if (query.get('n')) params.set('n', query.get('n'));
      return withQuery(film, params);
    }
    if (segs[2] === 'compare' && segs.length === 3) {
      params.set('tab', 'compare');
      copy(['tab']);
      return withQuery(film, params);
    }
    return text;
  }
  if (segs[0] === 'library' && segs[1] === 'f' && segs[2]) {
    const film = `#/f/lib-${encode(segs[2])}`;
    if (segs.length === 3) return withQuery(film, query);
    if (segs[3] === 'compare' && segs.length === 4) {
      const params = new URLSearchParams();
      params.set('tab', 'compare');
      for (const [key, value] of query) if (key !== 'tab') params.append(key, value);
      return withQuery(film, params);
    }
    return text;
  }
  if (segs[0] === 'library' && segs[1] === 'i' && segs[2] && segs.length === 3) return withQuery(`#/v/${encode(segs[2])}`, query);
  return text;
}

/* The route for a current hash: inbox, films, film, video, activity or notfound. */
export function parseRoute(hash) {
  const { path, query, segs } = splitHash(hash);
  const route = { path, query, name: 'notfound', filmId: null, itemId: null };
  if (!segs.length) route.name = 'inbox';
  else if (segs[0] === 'films' && segs.length === 1) route.name = 'films';
  else if (segs[0] === 'activity' && segs.length === 1) route.name = 'activity';
  else if (segs[0] === 'f' && segs[1] && segs.length === 2) {
    route.name = 'film';
    route.filmId = segs[1];
  } else if (segs[0] === 'v' && segs[1] && segs.length === 2) {
    route.name = 'video';
    route.itemId = segs[1];
  }
  if (route.name === 'film') {
    const match = /^(job|lib)-(.+)$/.exec(route.filmId);
    route.kind = match ? match[1] : null;
    route.key = match ? match[2] : null;
    route.version = query.get('v');
    route.tab = query.get('tab') || 'watch';
  }
  return route;
}
