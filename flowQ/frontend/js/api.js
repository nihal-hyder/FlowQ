/* flowQ API client, shared by every page.
   Pages are served by the FastAPI backend, so the API lives on the same origin under /api.
   Opening a page straight from disk (file://) falls back to http://localhost:8000. */
(function () {
  const BASE = window.FQ_API || (location.protocol.startsWith('http') ? '' : 'http://localhost:8000');
  const TOKEN = 'fq-token', USER = 'fq-user';
  const store = {
    get(k) { try { return localStorage.getItem(k) } catch (e) { return null } },
    set(k, v) { try { localStorage.setItem(k, v) } catch (e) {} },
    del(k) { try { localStorage.removeItem(k) } catch (e) {} }
  };
  const never = () => new Promise(() => {});

  function message(data, status) {
    if (data && typeof data.detail === 'string') return data.detail;
    if (data && Array.isArray(data.detail)) return data.detail.map(x => String(x.msg || '').replace(/^Value error, /, '')).join('. ');
    return status === 0 ? 'Cannot reach the flowQ server. Is the backend running?' : `Something went wrong (${status}). Please try again.`;
  }

  async function api(path, { method = 'GET', body, raw } = {}) {
    const headers = {}, token = store.get(TOKEN);
    if (token) headers.Authorization = 'Bearer ' + token;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    let res;
    try {
      res = await fetch(BASE + '/api' + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch (e) {
      throw Object.assign(new Error(message(null, 0)), { status: 0 });
    }
    if (res.status === 401 && token) { store.del(TOKEN); store.del(USER) }
    if (raw && res.ok) return res;
    const isJson = (res.headers.get('content-type') || '').includes('json');
    const data = isJson ? await res.json().catch(() => null) : null;
    if (!res.ok) throw Object.assign(new Error(message(data, res.status)), { status: res.status, data });
    return data;
  }

  function keep(session) {
    store.set(TOKEN, session.access_token);
    store.set(USER, JSON.stringify(session.user));
    return session.user;
  }

  const FQ = {
    api,
    loggedIn: () => !!store.get(TOKEN),
    user() { try { return JSON.parse(store.get(USER) || 'null') } catch (e) { return null } },
    async login(email, password) { return keep(await api('/auth/login', { method: 'POST', body: { email, password } })) },
    async register(body) { return keep(await api('/auth/register', { method: 'POST', body })) },
    async me() { const u = await api('/auth/me'); store.set(USER, JSON.stringify(u)); return u },
    logout(to) { store.del(TOKEN); store.del(USER); location.href = to || 'index.html' },
    can(u, perm) { return !!u && (u.role === 'admin' || (u.permissions || []).includes(perm)) },
    /* Dashboards: make sure someone with one of `roles` is logged in, otherwise send them to the login form. */
    async guard(roles) {
      const page = location.pathname.split('/').pop() || 'index.html';
      if (!store.get(TOKEN)) { location.replace('index.html?login=1&next=' + encodeURIComponent(page)); return never() }
      try {
        const u = await FQ.me();
        if (roles && !roles.includes(u.role)) { location.replace(u.home); return never() }
        return u;
      } catch (e) {
        if (e.status === 401) { location.replace('index.html?login=1&next=' + encodeURIComponent(page)); return never() }
        throw e;
      }
    },
    esc: s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    time: iso => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    day: iso => new Date(iso.length === 10 ? iso + 'T12:00:00' : iso).toLocaleDateString('en', { weekday: 'short', day: 'numeric', month: 'short' }),
    /* Run fn every `ms` while the tab is visible. Returns a stop() function. */
    poll(fn, ms) {
      let timer, stopped = false;
      const run = async () => {
        if (stopped) return;
        if (!document.hidden) { try { await fn() } catch (e) {} }
        timer = setTimeout(run, ms);
      };
      timer = setTimeout(run, ms);
      return () => { stopped = true; clearTimeout(timer) };
    }
  };
  window.FQ = FQ;
})();
