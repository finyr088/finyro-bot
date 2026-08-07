/* Финуро — мини-приложение (vanilla JS). Работает внутри Telegram WebApp. */
(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  const state = { token: null, user: null, tab: "cabinet" };

  // --- Утилиты ---
  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) =>
    String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

  let toastTimer = null;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
  }

  async function api(path, { method = "GET", body } = {}) {
    const headers = { "Content-Type": "application/json" };
    if (state.token) headers["Authorization"] = "Bearer " + state.token;
    const res = await fetch("/api" + path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) {
      const err = new Error((data && data.detail) || "Ошибка запроса");
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // --- Загрузка / авторизация ---
  async function boot() {
    if (tg) { try { tg.ready(); tg.expand(); } catch (_) {} }
    try {
      const initData = tg ? tg.initData : "";
      const auth = await api("/auth/telegram", { method: "POST", body: { init_data: initData } });
      state.token = auth.token;
      state.user = auth.user;
      renderShell();
      if (state.user.has_access) {
        showTab("home");
      } else {
        renderLocked();
      }
    } catch (e) {
      renderFatal(e);
    }
  }

  function renderFatal(e) {
    $("#app").innerHTML = `
      <div class="center">
        <div class="big">🔌</div>
        <h1 class="title">Не удалось запустить</h1>
        <p class="muted">${esc(e.message || "Ошибка")}</p>
        <p class="muted" style="margin-top:10px">Откройте приложение через кнопку в боте Финуро.</p>
      </div>`;
  }

  function renderLocked() {
    $("#app").innerHTML = `
      <div class="center">
        <div class="big">🔒</div>
        <h1 class="title">Доступ не активирован</h1>
        <p class="muted">Оплатите курс в боте и дождитесь подтверждения — приложение откроется автоматически.</p>
        <button class="btn" style="margin-top:20px;max-width:260px" id="closeBtn">Понятно</button>
      </div>`;
    const b = $("#closeBtn");
    if (b) b.onclick = () => { if (tg) tg.close(); };
  }

  // --- Каркас интерфейса ---
  function renderShell() {
    const expires = state.user.access_expires_at
      ? "до " + new Date(state.user.access_expires_at).toLocaleDateString("ru-RU")
      : "бессрочно";
    const initials = (firstName(state.user.name)[0] || "?").toUpperCase();
    $("#app").innerHTML = `
      <div class="appbar">
        <img src="logo.svg" alt="Финуро" />
        <div>
          <div class="brand">Финуро</div>
          <div class="sub">Финансовая грамотность</div>
        </div>
        <div class="spacer"></div>
        <button class="avatar" id="profileBtn" title="Личный кабинет">${initials}</button>
      </div>
      <div id="screens">
        <div class="screen" id="screen-home"></div>
        <div class="screen" id="screen-videos"></div>
        <div class="screen" id="screen-tests"></div>
        <div class="screen" id="screen-theory"></div>
        <div class="screen" id="screen-profile"></div>
        <div class="screen" id="screen-detail"></div>
      </div>
      <nav class="nav">
        ${navBtn("home", "🏠", "Главная")}
        ${navBtn("videos", "🎬", "Вебинары")}
        ${navBtn("tests", "📝", "Тесты")}
        ${navBtn("theory", "📚", "Теория")}
      </nav>`;
    document.querySelectorAll(".nav button").forEach((b) => {
      b.onclick = () => showTab(b.dataset.tab);
    });
    $("#profileBtn").onclick = showProfile;
    state._expires = expires;
  }

  const navBtn = (tab, ic, label) =>
    `<button data-tab="${tab}"><span class="ic">${ic}</span><span>${label}</span></button>`;

  function showTab(name) {
    state.tab = name;
    document.querySelectorAll(".screen").forEach((s) => s.classList.remove("active"));
    const el = $("#screen-" + name);
    el.classList.add("active");
    document.querySelectorAll(".nav button").forEach((b) =>
      b.classList.toggle("active", b.dataset.tab === name)
    );
    const loaders = { home: loadHome, videos: loadVideos, tests: loadTests, theory: loadTheory };
    if (loaders[name]) loaders[name](el);
  }

  function loadingInto(el) {
    el.innerHTML = `<div class="center" style="min-height:50vh"><div class="spinner"></div></div>`;
  }

  // --- Главная ---
  async function loadHome(el) {
    loadingInto(el);
    try {
      const [me, videos] = await Promise.all([api("/me"), api("/videos")]);
      const cont = me.continue;
      const newItems = videos.videos.filter((v) => !v.watched).slice(0, 3);
      let html = `<h1 class="title">Привет, ${esc(firstName(me.user.name))}! 👋</h1>`;

      if (cont) {
        const label = cont.watched ? "Пересмотреть" : "Досмотреть";
        const tag = cont.watched ? "просмотрено" : "продолжить";
        html += `
          <h2 class="section">Продолжить просмотр</h2>
          <div class="card continue-card" id="cont">
            <div class="thumb">🎬</div>
            <div class="grow">
              <h3>${esc(cont.title)}</h3>
              <p>${esc(cont.description || "Вебинар курса")}</p>
              <span class="badge ${cont.watched ? "done" : "new"}">${tag}</span>
            </div>
            <div class="play">▶</div>
          </div>
          <div style="margin-top:-4px">
            <button class="btn" id="contBtn">${label}</button>
          </div>`;
      }

      html += `<h2 class="section">Новые материалы</h2><div id="home-new"></div>`;
      el.innerHTML = html;

      if (cont) {
        const go = () => openVideo(cont.id);
        $("#cont", el).onclick = go;
        $("#contBtn", el).onclick = go;
      }

      const box = $("#home-new", el);
      if (!newItems.length) {
        box.innerHTML = `<div class="card"><p>Всё изучено 🎉 Загляните позже — контент выходит постепенно.</p></div>`;
      } else {
        newItems.forEach((v) => {
          const c = document.createElement("div");
          c.className = "card tap";
          c.innerHTML = `<div class="row"><div class="grow"><h3>${esc(v.title)}</h3><p>${esc(v.description || "Вебинар")}</p></div><span class="badge new">новое</span></div>`;
          c.onclick = () => openVideo(v.id);
          box.appendChild(c);
        });
      }
    } catch (e) { errCard(el, e); }
  }

  // --- Личный кабинет (профиль) ---
  async function showProfile() {
    const el = $("#screen-profile");
    loadingInto(el);
    showDetail(el);
    try {
      const me = await api("/me");
      const p = me.progress;
      el.innerHTML = `
        <button class="back" id="pback">◀️ Назад</button>
        <h1 class="title" style="margin-top:12px">Личный кабинет</h1>
        <div class="card status-card">
          <h3>${me.user.has_access ? '<span class="dot green"></span>Подписка активна' : '<span class="dot red"></span>Подписка неактивна'}</h3>
          <p style="color:rgba(255,255,255,.85)">${esc(me.user.name)}</p>
          <p style="color:rgba(255,255,255,.85)">Доступ: ${esc(state._expires || "")}</p>
        </div>
        <div class="stats">
          <div class="stat"><div class="num">${p.videos_watched}/${p.videos_total}</div><div class="lbl">Вебинаров</div></div>
          <div class="stat"><div class="num">${p.tests_passed}</div><div class="lbl">Тестов пройдено</div></div>
          <div class="stat"><div class="num">${p.avg_percent}%</div><div class="lbl">Средний балл</div></div>
        </div>
        <h2 class="section">Поддержка</h2>
        <p class="muted" style="margin-bottom:12px">Напишите администратору — ответ придёт в бот Финуро.</p>
        <textarea id="msg" placeholder="Ваш вопрос…"></textarea>
        <button class="btn" id="send" style="margin-top:12px">Отправить</button>`;
      $("#pback", el).onclick = () => showTab(state.tab || "home");
      $("#send", el).onclick = async () => {
        const text = $("#msg", el).value.trim();
        if (!text) { toast("Введите сообщение"); return; }
        $("#send", el).disabled = true;
        try {
          await api("/support", { method: "POST", body: { text } });
          $("#msg", el).value = "";
          toast("Отправлено ✅ Ответ придёт в бот");
        } catch (e) { toast(e.message); }
        finally { $("#send", el).disabled = false; }
      };
    } catch (e) { errCard(el, e); }
  }

  // --- Вебинары ---
  async function loadVideos(el) {
    loadingInto(el);
    try {
      const data = await api("/videos");
      let html = `<h1 class="title">Вебинары</h1>`;
      if (!data.videos.length && !data.upcoming.length) {
        html += emptyCard("Вебинары скоро появятся.");
      }
      data.videos.forEach((v) => {
        const badge = v.watched ? '<span class="badge done">просмотрено</span>' : '<span class="badge todo">не начато</span>';
        html += `<div class="card tap" data-vid="${v.id}"><div class="row"><div class="grow"><h3>${esc(v.title)}</h3><p>${esc(v.description || "")}</p></div>${badge}</div></div>`;
      });
      data.upcoming.forEach((v) => {
        const when = v.publish_at ? new Date(v.publish_at).toLocaleDateString("ru-RU") : "";
        html += `<div class="card"><div class="row"><div class="grow"><h3 style="opacity:.7">${esc(v.title)}</h3><p>Скоро${when ? " · " + when : ""}</p></div><span class="badge soon">скоро</span></div></div>`;
      });
      el.innerHTML = html;
      el.querySelectorAll("[data-vid]").forEach((c) => (c.onclick = () => openVideo(+c.dataset.vid)));
    } catch (e) { errCard(el, e); }
  }

  async function openVideo(id) {
    const el = $("#screen-detail");
    loadingInto(el);
    showDetail();
    try {
      const v = await api("/videos/" + id);
      el.innerHTML = `
        <button class="back" id="back">◀️ Назад</button>
        <h1 class="title" style="margin-top:12px">${esc(v.title)}</h1>
        <div class="player-wrap" id="pw">
          <div class="watermark" id="wm">${esc(v.watermark)}</div>
          <video id="vid" controls playsinline preload="metadata"
            controlslist="nodownload noremoteplayback" disablepictureinpicture></video>
        </div>
        <p class="muted" style="margin:12px 2px">${esc(v.description || "")}</p>
        <button class="btn" id="watchBtn">${v.watched ? "✓ Просмотрено" : "Отметить просмотренным"}</button>`;
      $("#back", el).onclick = () => showTab("videos");
      setupPlayer($("#vid", el), $("#wm", el), $("#pw", el), v.stream_url);
      const wb = $("#watchBtn", el);
      const mark = async () => {
        try { await api("/videos/" + id + "/watch", { method: "POST" }); wb.textContent = "✓ Просмотрено"; toast("Отмечено как просмотренное"); }
        catch (e) { toast(e.message); }
      };
      wb.onclick = mark;
      $("#vid", el).addEventListener("ended", mark, { once: true });
    } catch (e) { errCard(el, e); }
  }

  function setupPlayer(video, wm, wrap, url) {
    video.oncontextmenu = () => false;
    if (!url) { toast("Ссылка на видео не задана"); return; }
    const isHls = /\.m3u8(\?|$)/i.test(url);
    if (isHls && window.Hls && window.Hls.isSupported()) {
      const hls = new window.Hls({ maxBufferLength: 30 });
      hls.loadSource(url);
      hls.attachMedia(video);
      video._hls = hls;
    } else {
      // Safari/iOS проигрывает HLS нативно; mp4 — тоже напрямую.
      video.src = url;
    }
    // Плавающий водяной знак — усложняет обрезку записи экрана.
    const move = () => {
      const w = wrap.clientWidth, h = wrap.clientHeight || 200;
      wm.style.left = Math.max(8, Math.random() * (w - wm.offsetWidth - 16)) + "px";
      wm.style.top = Math.max(8, Math.random() * (h - wm.offsetHeight - 16)) + "px";
    };
    move();
    wrap._wmTimer = setInterval(move, 4000);
  }

  // --- Тесты ---
  async function loadTests(el) {
    loadingInto(el);
    try {
      const data = await api("/tests");
      let html = `<h1 class="title">Тесты</h1>`;
      if (!data.tests.length) html += emptyCard("Тесты скоро появятся.");
      data.tests.forEach((t) => {
        html += `<div class="card tap" data-tid="${t.id}"><h3>${esc(t.title)}</h3><p>${esc(t.description || "")} · ${t.questions} вопр.</p></div>`;
      });
      el.innerHTML = html;
      el.querySelectorAll("[data-tid]").forEach((c) => (c.onclick = () => openTest(+c.dataset.tid)));
    } catch (e) { errCard(el, e); }
  }

  async function openTest(id) {
    const el = $("#screen-detail");
    loadingInto(el);
    showDetail();
    try {
      const t = await api("/tests/" + id);
      const answers = new Array(t.questions.length).fill(-1);
      let html = `<button class="back" id="back">◀️ Назад</button>
        <h1 class="title" style="margin-top:12px">${esc(t.title)}</h1>`;
      t.questions.forEach((q, qi) => {
        html += `<div class="q" data-qi="${qi}"><div class="qtext">${qi + 1}. ${esc(q.text)}</div>`;
        q.options.forEach((opt, oi) => {
          html += `<button class="opt" data-qi="${qi}" data-oi="${oi}">${esc(opt)}</button>`;
        });
        html += `</div>`;
      });
      html += `<button class="btn" id="submit" disabled>Завершить тест</button>
        <div id="result" style="margin-top:16px"></div>`;
      el.innerHTML = html;
      $("#back", el).onclick = () => showTab("tests");

      el.querySelectorAll(".opt").forEach((b) => {
        b.onclick = () => {
          const qi = +b.dataset.qi, oi = +b.dataset.oi;
          answers[qi] = oi;
          el.querySelectorAll(`.opt[data-qi="${qi}"]`).forEach((x) => x.classList.remove("sel"));
          b.classList.add("sel");
          $("#submit", el).disabled = answers.includes(-1);
        };
      });

      $("#submit", el).onclick = async () => {
        try {
          const r = await api("/tests/" + id + "/submit", { method: "POST", body: { answers } });
          el.querySelectorAll(".opt").forEach((b) => (b.style.pointerEvents = "none"));
          t.questions.forEach((q, qi) => {
            const right = r.correct[qi], your = r.your[qi];
            const rb = el.querySelector(`.opt[data-qi="${qi}"][data-oi="${right}"]`);
            if (rb) rb.classList.add("correct");
            if (your !== right) {
              const wb = el.querySelector(`.opt[data-qi="${qi}"][data-oi="${your}"]`);
              if (wb) wb.classList.add("wrong");
            }
          });
          $("#submit", el).style.display = "none";
          $("#result", el).innerHTML =
            `<div class="card" style="text-align:center"><div class="stat"><div class="num">${r.score}/${r.total}</div><div class="lbl">Результат · ${r.percent}%</div></div></div>`;
          $("#result", el).scrollIntoView({ behavior: "smooth" });
        } catch (e) { toast(e.message); }
      };
    } catch (e) { errCard(el, e); }
  }

  // --- Теория ---
  async function loadTheory(el) {
    loadingInto(el);
    try {
      const data = await api("/theory");
      let html = `<h1 class="title">Изучение тем</h1>`;
      if (!data.topics.length) html += emptyCard("Темы скоро появятся.");
      data.topics.forEach((t) => {
        html += `<div class="card tap" data-th="${t.id}"><div class="row"><div class="grow"><h3>${esc(t.title)}</h3></div><span class="badge todo">читать</span></div></div>`;
      });
      el.innerHTML = html;
      el.querySelectorAll("[data-th]").forEach((c) => (c.onclick = () => openTheory(+c.dataset.th)));
    } catch (e) { errCard(el, e); }
  }

  async function openTheory(id) {
    const el = $("#screen-detail");
    loadingInto(el);
    showDetail();
    try {
      const t = await api("/theory/" + id);
      const body = esc(t.content).replace(/\n/g, "<br>");
      el.innerHTML = `
        <button class="back" id="back">◀️ Назад</button>
        <h1 class="title" style="margin-top:12px">${esc(t.title)}</h1>
        <div class="card"><p style="color:var(--text);font-size:15px;line-height:1.6">${body}</p></div>`;
      $("#back", el).onclick = () => showTab("theory");
    } catch (e) { errCard(el, e); }
  }

  // --- Детальный экран ---
  function showDetail(target) {
    document.querySelectorAll(".screen").forEach((s) => s.classList.remove("active"));
    (target || $("#screen-detail")).classList.add("active");
    // очистка таймера водяного знака при уходе
    document.querySelectorAll(".player-wrap").forEach((pw) => pw._wmTimer && clearInterval(pw._wmTimer));
  }

  // --- Вспомогательное ---
  const firstName = (name) => (name || "").split(" ")[0] || "друг";
  const emptyCard = (msg) => `<div class="card"><p>${esc(msg)}</p></div>`;
  function errCard(el, e) {
    if (e.status === 403) { renderLocked(); return; }
    el.innerHTML = `<div class="card"><h3>Ошибка</h3><p>${esc(e.message || "Не удалось загрузить")}</p></div>`;
  }

  boot();
})();
