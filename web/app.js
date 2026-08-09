/* Финуро — мини-приложение (vanilla JS). Работает внутри Telegram WebApp. */
(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  const state = { token: null, user: null, tab: "cabinet" };

  // Логотип «монета роста» — чистая марка без подложки (лайм на прозрачном).
  const LOGO_MARK = `
    <svg class="logo" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg" aria-label="Финуро">
      <circle cx="20" cy="20" r="16.5" stroke="#C6FF4E" stroke-width="2.5"/>
      <rect x="11.5" y="21" width="4.6" height="8" rx="1.6" fill="#C6FF4E"/>
      <rect x="17.7" y="16" width="4.6" height="13" rx="1.6" fill="#C6FF4E"/>
      <rect x="23.9" y="11" width="4.6" height="18" rx="1.6" fill="#C6FF4E"/>
    </svg>`;

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
      <header class="appbar">
        <div class="bar-inner">
          ${LOGO_MARK}
          <div class="brand-box">
            <div class="brand">Финуро</div>
            <div class="sub">Финансовая грамотность</div>
          </div>
          <div class="spacer"></div>
          ${state.user.is_admin ? '<button class="icon-btn admin-enter" id="adminBtn" title="Админ-панель">🛡️</button>' : ""}
          <button class="avatar" id="profileBtn" title="Личный кабинет">${initials}</button>
        </div>
      </header>
      <div id="screens">
        <div class="screen" id="screen-home"></div>
        <div class="screen" id="screen-videos"></div>
        <div class="screen" id="screen-tests"></div>
        <div class="screen" id="screen-theory"></div>
        <div class="screen" id="screen-profile"></div>
        <div class="screen" id="screen-detail"></div>
      </div>
      <nav class="nav">
        <div class="nav-inner">
          ${navBtn("home", "🏠", "Главная")}
          ${navBtn("videos", "🎬", "Вебинары")}
          ${navBtn("tests", "📝", "Тесты")}
          ${navBtn("theory", "📚", "Теория")}
        </div>
      </nav>`;
    document.querySelectorAll(".nav button").forEach((b) => {
      b.onclick = () => showTab(b.dataset.tab);
    });
    $("#profileBtn").onclick = showProfile;
    const ab = $("#adminBtn");
    if (ab) ab.onclick = openAdmin;
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

  // ==================== АДМИН-ПАНЕЛЬ ====================
  const A_TABS = [
    ["overview", "Обзор"], ["payments", "Заявки"], ["students", "Ученики"],
    ["content", "Контент"], ["tests", "Тесты"], ["support", "Поддержка"],
    ["broadcast", "Рассылка"], ["results", "Результаты"],
  ];

  async function authBlob(path) {
    const res = await fetch("/api" + path, { headers: { Authorization: "Bearer " + state.token } });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return URL.createObjectURL(await res.blob());
  }

  async function apiForm(path, formData) {
    const res = await fetch("/api" + path, {
      method: "POST",
      headers: { Authorization: "Bearer " + state.token }, // Content-Type ставит браузер сам
      body: formData,
    });
    let d = null;
    try { d = await res.json(); } catch (_) {}
    if (!res.ok) throw new Error((d && d.detail) || "Ошибка загрузки (" + res.status + ")");
    return d;
  }

  function openAdmin() {
    $("#app").className = "admin-app";
    $("#app").innerHTML = `
      <div class="admin-bar">
        <span class="abrand">Финуро</span>
        <span class="admin-badge">АДМИН</span>
        <div class="spacer"></div>
        <button class="admin-exit" id="aexit">← В приложение</button>
      </div>
      <div class="atabs" id="atabs">
        ${A_TABS.map(([k, l]) => `<button class="atab" data-a="${k}">${l}</button>`).join("")}
      </div>
      <div class="ascreen" id="ascreen"></div>`;
    $("#aexit").onclick = () => { $("#app").className = ""; renderShell(); showTab("home"); };
    document.querySelectorAll(".atab").forEach((b) => (b.onclick = () => adminTab(b.dataset.a)));
    adminTab("overview");
  }

  function adminTab(name) {
    document.querySelectorAll(".atab").forEach((b) => b.classList.toggle("active", b.dataset.a === name));
    const el = $("#ascreen");
    ({
      overview: aOverview, payments: aPayments, students: aStudents, content: aContent,
      tests: aTests, support: aSupport, broadcast: aBroadcast, results: aResults,
    })[name](el);
  }

  const aLoad = (el) => (el.innerHTML = `<div class="center" style="min-height:40vh"><div class="spinner"></div></div>`);
  const aErr = (el, e) => (el.innerHTML = `<div class="acard"><h4>Ошибка</h4><p>${esc(e.message)}</p></div>`);

  // --- Обзор ---
  async function aOverview(el) {
    aLoad(el);
    try {
      const d = await api("/admin/overview");
      el.innerHTML = `
        <div class="astats">
          <div class="astat"><div class="n">${d.total_users}</div><div class="l">Учеников</div></div>
          <div class="astat"><div class="n">${d.active_users}</div><div class="l">С доступом</div></div>
          <div class="astat"><div class="n">${d.pending}</div><div class="l">Заявок</div></div>
          <div class="astat"><div class="n">${d.materials}</div><div class="l">Материалов</div></div>
          <div class="astat"><div class="n">${d.tests}</div><div class="l">Тестов</div></div>
          <div class="astat"><div class="n">${d.attempts}</div><div class="l">Попыток</div></div>
        </div>
        ${d.pending > 0 ? `<div class="acard"><h4>🔔 Есть новые заявки на оплату</h4><p>Откройте вкладку «Заявки», чтобы подтвердить доступ.</p></div>` : ""}`;
    } catch (e) { aErr(el, e); }
  }

  // --- Заявки ---
  async function aPayments(el) {
    aLoad(el);
    try {
      const d = await api("/admin/payments");
      if (!d.payments.length) { el.innerHTML = `<div class="acard"><p>Заявок на проверке нет ✅</p></div>`; return; }
      el.innerHTML = d.payments.map((p) => `
        <div class="acard" data-pid="${p.id}">
          <h4>${esc(p.name)}</h4>
          <p>ID: ${p.telegram_id} · ${new Date(p.created_at).toLocaleString("ru-RU")}</p>
          <div class="arow">
            ${p.has_proof ? `<button class="abtn sec" data-proof="${p.id}">🖼 Скриншот</button>` : ""}
            <button class="abtn ok" data-ok="${p.id}">✅ Подтвердить</button>
            <button class="abtn no" data-no="${p.id}">❌ Отклонить</button>
          </div>
          <div class="proofbox"></div>
        </div>`).join("");
      el.querySelectorAll("[data-proof]").forEach((b) => (b.onclick = async () => {
        const box = b.closest(".acard").querySelector(".proofbox");
        box.innerHTML = `<p class="ahint">Загрузка…</p>`;
        try { box.innerHTML = `<img class="aproof" src="${await authBlob("/admin/payments/" + b.dataset.proof + "/proof")}">`; }
        catch (e) { box.innerHTML = `<p class="ahint">Не удалось загрузить: ${esc(e.message)}</p>`; }
      }));
      const act = (id, action) => async () => {
        try { await api("/admin/payments/" + id + "/" + action, { method: "POST" }); toast(action === "approve" ? "Доступ выдан ✅" : "Отклонено"); aPayments(el); }
        catch (e) { toast(e.message); }
      };
      el.querySelectorAll("[data-ok]").forEach((b) => (b.onclick = act(b.dataset.ok, "approve")));
      el.querySelectorAll("[data-no]").forEach((b) => (b.onclick = act(b.dataset.no, "reject")));
    } catch (e) { aErr(el, e); }
  }

  // --- Ученики ---
  async function aStudents(el) {
    el.innerHTML = `<input class="ainput" id="ssearch" placeholder="Поиск по имени или ID…"><div id="slist"></div>`;
    const box = $("#slist", el);
    const load = async (q) => {
      aLoad(box);
      try {
        const d = await api("/admin/students" + (q ? "?q=" + encodeURIComponent(q) : ""));
        if (!d.students.length) { box.innerHTML = `<div class="acard"><p>Ничего не найдено.</p></div>`; return; }
        box.innerHTML = d.students.map((u) => `
          <div class="acard">
            <div style="display:flex;align-items:center;gap:8px">
              <div style="flex:1"><h4>${esc(u.name)}</h4><p>ID: ${u.telegram_id}</p></div>
              <span class="apill ${u.has_access ? "on" : "off"}">${u.has_access ? "доступ" : "нет"}</span>
            </div>
            <div class="arow">
              ${u.has_access
                ? `<button class="abtn no" data-rev="${u.telegram_id}">Отозвать</button>`
                : `<button class="abtn ok" data-grant="${u.telegram_id}">Выдать доступ</button>`}
            </div>
          </div>`).join("");
        const act = (tg, action) => async () => {
          try { await api("/admin/students/" + tg + "/" + action, { method: "POST" }); toast("Готово"); load($("#ssearch", el).value.trim()); }
          catch (e) { toast(e.message); }
        };
        box.querySelectorAll("[data-grant]").forEach((b) => (b.onclick = act(b.dataset.grant, "grant")));
        box.querySelectorAll("[data-rev]").forEach((b) => (b.onclick = act(b.dataset.rev, "revoke")));
      } catch (e) { aErr(box, e); }
    };
    let t;
    $("#ssearch", el).oninput = (ev) => { clearTimeout(t); t = setTimeout(() => load(ev.target.value.trim()), 350); };
    load("");
  }

  // --- Контент ---
  async function aContent(el) {
    aLoad(el);
    try {
      const d = await api("/admin/materials");
      el.innerHTML = `
        <button class="abtn ok" id="addmat" style="margin-bottom:14px">➕ Добавить материал</button>
        <div id="matform"></div>
        <div id="matlist">${d.materials.map(matCard).join("") || '<div class="acard"><p>Материалов пока нет.</p></div>'}</div>`;
      $("#addmat", el).onclick = () => renderMatForm($("#matform", el), el);
      wireMatList(el);
    } catch (e) { aErr(el, e); }
  }

  const matCard = (m) => `
    <div class="acard" data-mid="${m.id}">
      <div style="display:flex;align-items:center;gap:8px">
        <div style="flex:1"><h4>${m.kind === "video" ? "🎬" : "📚"} ${esc(m.title)}</h4>
        <p>${m.kind === "video" ? "Вебинар" : "Теория"}</p></div>
        <span class="apill ${m.status === "published" ? "on" : "off"}">${m.status === "published" ? "опубл." : m.status}</span>
      </div>
      <div class="arow">
        ${m.status === "published"
          ? `<button class="abtn sec" data-unpub="${m.id}">Снять</button>`
          : `<button class="abtn ok" data-pub="${m.id}">Опубликовать</button>`}
        <button class="abtn del" data-delmat="${m.id}">Удалить</button>
      </div>
    </div>`;

  function wireMatList(el) {
    const act = (id, action) => async () => {
      try { await api("/admin/materials/" + id + "/" + action, { method: "POST" }); toast("Готово"); aContent(el); }
      catch (e) { toast(e.message); }
    };
    el.querySelectorAll("[data-pub]").forEach((b) => (b.onclick = act(b.dataset.pub, "publish")));
    el.querySelectorAll("[data-unpub]").forEach((b) => (b.onclick = act(b.dataset.unpub, "unpublish")));
    el.querySelectorAll("[data-delmat]").forEach((b) => (b.onclick = async () => {
      if (!confirm("Удалить материал?")) return;
      try { await api("/admin/materials/" + b.dataset.delmat, { method: "DELETE" }); toast("Удалено"); aContent(el); }
      catch (e) { toast(e.message); }
    }));
  }

  function renderMatForm(box, el) {
    box.innerHTML = `
      <div class="acard">
        <select class="asel" id="mkind">
          <option value="video">🎬 Вебинар (видео)</option>
          <option value="theory">📚 Тема теории</option>
        </select>
        <input class="ainput" id="mtitle" placeholder="Название">
        <div id="videofields">
          <p class="ahint" style="margin:0 0 6px">Загрузите файл MP4:</p>
          <input class="ainput" type="file" id="mfile" accept="video/mp4,video/quicktime,video/*">
          <p class="ahint" style="margin:-2px 0 6px">— или вставьте ссылку на поток —</p>
          <input class="ainput" id="murl" placeholder="Ссылка HLS .m3u8 / mp4 (необязательно)">
        </div>
        <textarea class="aarea" id="mdesc" placeholder="Описание (для видео) или текст темы (для теории)"></textarea>
        <div class="arow">
          <button class="abtn ok" id="msave">Создать (черновик)</button>
          <button class="abtn sec" id="mcancel">Отмена</button>
        </div>
        <p class="ahint" id="upstatus">Материал создаётся черновиком — потом нажмите «Опубликовать».</p>
      </div>`;
    const kind = $("#mkind", box);
    const vf = $("#videofields", box);
    const toggle = () => { vf.style.display = kind.value === "video" ? "block" : "none"; };
    kind.onchange = toggle; toggle();
    $("#mcancel", box).onclick = () => (box.innerHTML = "");
    $("#msave", box).onclick = async () => {
      const k = kind.value;
      const title = $("#mtitle", box).value.trim();
      const text = $("#mdesc", box).value.trim();
      if (!title) { toast("Введите название"); return; }

      if (k === "theory") {
        try { await api("/admin/materials", { method: "POST", body: { kind: "theory", title, content: text } }); toast("Создано ✅"); aContent(el); }
        catch (e) { toast(e.message); }
        return;
      }

      // Видео: приоритет — загруженный файл; иначе ссылка.
      const file = $("#mfile", box).files[0];
      const url = $("#murl", box).value.trim();
      const save = $("#msave", box), status = $("#upstatus", box);
      if (file) {
        const fd = new FormData();
        fd.append("title", title); fd.append("description", text); fd.append("file", file);
        save.disabled = true;
        status.textContent = "⏳ Загрузка видео… не закрывайте окно (может занять время)";
        try {
          const r = await apiForm("/admin/materials/upload", fd);
          toast(`Видео загружено (${r.size_mb} МБ) ✅`);
          aContent(el);
        } catch (e) { toast(e.message); save.disabled = false; status.textContent = ""; }
      } else if (url) {
        try { await api("/admin/materials", { method: "POST", body: { kind: "video", title, stream_url: url, description: text } }); toast("Создано ✅"); aContent(el); }
        catch (e) { toast(e.message); }
      } else {
        toast("Загрузите файл MP4 или вставьте ссылку");
      }
    };
  }

  // --- Тесты ---
  async function aTests(el) {
    aLoad(el);
    try {
      const d = await api("/admin/tests");
      el.innerHTML = `
        <button class="abtn ok" id="addtest" style="margin-bottom:14px">➕ Создать тест</button>
        <div id="testform"></div>
        <div id="testlist">${d.tests.map((t) => `
          <div class="acard">
            <div style="display:flex;align-items:center;gap:8px">
              <div style="flex:1"><h4>${esc(t.title)}</h4><p>${t.questions} вопр. · ${t.status}</p></div>
              <button class="abtn del" data-deltest="${t.id}">Удалить</button>
            </div>
          </div>`).join("") || '<div class="acard"><p>Тестов пока нет.</p></div>'}</div>`;
      $("#addtest", el).onclick = () => renderTestForm($("#testform", el), el);
      el.querySelectorAll("[data-deltest]").forEach((b) => (b.onclick = async () => {
        if (!confirm("Удалить тест?")) return;
        try { await api("/admin/tests/" + b.dataset.deltest, { method: "DELETE" }); toast("Удалено"); aTests(el); }
        catch (e) { toast(e.message); }
      }));
    } catch (e) { aErr(el, e); }
  }

  function renderTestForm(box, el) {
    const qs = [];
    box.innerHTML = `
      <div class="acard">
        <input class="ainput" id="ttitle" placeholder="Название теста">
        <input class="ainput" id="tdesc" placeholder="Описание (необязательно)">
        <div id="qbox"></div>
        <div class="arow">
          <button class="abtn sec" id="addq">＋ Вопрос</button>
          <button class="abtn ok" id="tsave">Сохранить тест</button>
          <button class="abtn sec" id="tcancel">Отмена</button>
        </div>
        <p class="ahint">Отметьте кружком правильный вариант в каждом вопросе.</p>
      </div>`;
    const qbox = $("#qbox", box);
    const addQ = () => {
      const qi = qs.length;
      qs.push(true);
      const div = document.createElement("div");
      div.style.cssText = "border-top:1px solid #2a2a2a;padding-top:10px;margin-top:6px";
      div.innerHTML = `
        <input class="ainput q-text" placeholder="Вопрос ${qi + 1}">
        ${[0, 1, 2, 3].map((oi) => `
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
            <input type="radio" name="correct${qi}" value="${oi}" ${oi === 0 ? "checked" : ""}>
            <input class="ainput q-opt" data-oi="${oi}" placeholder="Вариант ${oi + 1}${oi > 1 ? " (необяз.)" : ""}" style="margin:0">
          </div>`).join("")}`;
      div.dataset.qi = qi;
      qbox.appendChild(div);
    };
    $("#addq", box).onclick = addQ; addQ();
    $("#tcancel", box).onclick = () => (box.innerHTML = "");
    $("#tsave", box).onclick = async () => {
      const title = $("#ttitle", box).value.trim();
      if (!title) { toast("Введите название теста"); return; }
      const questions = [];
      qbox.querySelectorAll("[data-qi]").forEach((div) => {
        const text = div.querySelector(".q-text").value.trim();
        const opts = [...div.querySelectorAll(".q-opt")].map((i) => i.value.trim()).filter(Boolean);
        const correctEl = div.querySelector("input[type=radio]:checked");
        const correct = correctEl ? +correctEl.value : 0;
        if (text && opts.length >= 2) questions.push({ text, options: opts, correct_index: Math.min(correct, opts.length - 1) });
      });
      if (!questions.length) { toast("Добавьте хотя бы один вопрос с 2+ вариантами"); return; }
      try {
        await api("/admin/tests", { method: "POST", body: { title, description: $("#tdesc", box).value.trim(), questions } });
        toast("Тест создан ✅"); aTests(el);
      } catch (e) { toast(e.message); }
    };
  }

  // --- Поддержка ---
  async function aSupport(el) {
    aLoad(el);
    try {
      const d = await api("/admin/support");
      if (!d.messages.length) { el.innerHTML = `<div class="acard"><p>Обращений пока нет.</p></div>`; return; }
      el.innerHTML = d.messages.map((m) => `
        <div class="acard">
          <h4>${m.from_admin ? "↩️ Вы → " : ""}${esc(m.name)} <span class="ahint">${new Date(m.created_at).toLocaleString("ru-RU")}</span></h4>
          <p style="color:#ddd">${esc(m.text)}</p>
          ${!m.from_admin && m.telegram_id ? `
            <div style="margin-top:10px">
              <input class="ainput rtext" placeholder="Ответить ученику…" style="margin-bottom:8px">
              <button class="abtn ok" data-reply="${m.telegram_id}">Отправить ответ</button>
            </div>` : ""}
        </div>`).join("");
      el.querySelectorAll("[data-reply]").forEach((b) => (b.onclick = async () => {
        const inp = b.closest(".acard").querySelector(".rtext");
        const text = inp.value.trim();
        if (!text) { toast("Введите ответ"); return; }
        try { await api("/admin/support/" + b.dataset.reply + "/reply", { method: "POST", body: { text } }); toast("Отправлено ✅"); inp.value = ""; }
        catch (e) { toast(e.message); }
      }));
    } catch (e) { aErr(el, e); }
  }

  // --- Рассылка ---
  function aBroadcast(el) {
    el.innerHTML = `
      <div class="acard">
        <h4>Рассылка</h4>
        <p class="ahint">Сообщение придёт ученикам в бот Финуро.</p>
        <textarea class="aarea" id="btext" placeholder="Текст рассылки…"></textarea>
        <label style="display:flex;align-items:center;gap:8px;color:#bbb;font-size:13px;margin-bottom:10px">
          <input type="checkbox" id="bactive" checked> только с активным доступом
        </label>
        <button class="abtn ok" id="bsend">Отправить</button>
      </div>`;
    $("#bsend", el).onclick = async () => {
      const text = $("#btext", el).value.trim();
      if (!text) { toast("Введите текст"); return; }
      if (!confirm("Отправить рассылку?")) return;
      $("#bsend", el).disabled = true;
      try {
        const r = await api("/admin/broadcast", { method: "POST", body: { text, only_active: $("#bactive", el).checked } });
        toast(`Доставлено ${r.sent} из ${r.total}`);
        $("#btext", el).value = "";
      } catch (e) { toast(e.message); }
      finally { $("#bsend", el).disabled = false; }
    };
  }

  // --- Результаты ---
  async function aResults(el) {
    aLoad(el);
    try {
      const d = await api("/admin/results");
      if (!d.results.length) { el.innerHTML = `<div class="acard"><p>Результатов пока нет.</p></div>`; return; }
      el.innerHTML = d.results.map((r) => `
        <div class="acard">
          <div style="display:flex;align-items:center;gap:8px">
            <div style="flex:1"><h4>${esc(r.name)}</h4><p>${esc(r.test)}</p></div>
            <span class="apill ${r.percent >= 60 ? "on" : "off"}">${r.score}/${r.total} · ${r.percent}%</span>
          </div>
        </div>`).join("");
    } catch (e) { aErr(el, e); }
  }

  boot();
})();
