'use strict';
const $ = id => document.getElementById(id),
  state = {
    csrf: '',
    isAdmin: false,
    usersEpoch: 0,
    job: null,
    result: null,
    series: 0,
    side: 'right',
    stone: 0,
    view: 'studies',
    jobs: [],
    epoch: 0,
    modelEpoch: 0,
    imageEpoch: 0
  };
const titles = {
  studies: 'Исследования',
  images: 'Изображения и детекции',
  stones: 'Параметры и 3D',
  laser: 'Время разрушения',
  users: 'Пользователи'
};
const fmt = (v, n = 2) => Number(v).toLocaleString('ru-RU', {
  maximumFractionDigits: n
});
const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
};
const notice = text => {
  $('notice').textContent = text;
  $('notice').hidden = !text;
};
async function api(path, options = {}) {
  const response = await fetch('/api' + path, {
    ...options,
    headers: {
      'X-CSRF-Token': state.csrf,
      ...options.headers
    },
    credentials: 'same-origin'
  });
  let data;
  try {
    data = await response.json();
  } catch {
    data = {
      detail: 'Сервер вернул некорректный ответ.'
    };
  }
  if (!response.ok) {
    if (response.status === 401 && path !== '/login') loggedOut();
    throw Error(typeof data.detail === 'string' ? data.detail : 'Ошибка запроса');
  }
  return data;
}

function loggedOut() {
  state.uploadAbort?.abort();
  state.epoch++;
  state.modelEpoch++;
  state.imageEpoch++;
  state.csrf = '';
  state.isAdmin = false;
  state.usersEpoch++;
  $('users-nav').hidden = true;
  $('users-list').replaceChildren();
  $('users-count').textContent = '';
  $('create-user-form').reset();
  $('new-password').type = 'password';
  $('user-form-message').hidden = true;
  $('delete-user-dialog').close();
  $('password-dialog').close();
  $('change-password-form').reset();
  state.result = null;
  state.job = null;
  state.jobs = [];
  state.jobsSignature = '';
  state.jobLimit = 100;
  $('job-owner-filter').value = '';
  $('job-status-filter').value = 'all';
  image = null;
  records = [];
  for (const id of ['jobs', 'metrics', 'previews', 'laser-table', 'legend', 'series']) $(id).replaceChildren();
  clearModel();
  $('identity').textContent = '';
  $('account').textContent = '';
  $('study-bar').hidden = true;
  notice('');
  draw();
  view('studies');
  $('login').hidden = false;
  $('workspace').hidden = true;
  $('password').value = '';
  clearInterval(poll);
}
let poll;
let registrationInfo;
$('open-register').onclick = async () => {
  const message = $('register-message');
  $('register-form').reset();
  message.hidden = true;
  $('submit-register').disabled = true;
  $('register-consent-text').textContent = '';
  $('privacy-link').hidden = true;
  $('register-dialog').showModal();
  try {
    registrationInfo = await api('/registration');
    if (!registrationInfo.enabled) throw Error('Регистрация отключена. Обратитесь к администратору.');
    $('register-consent-text').textContent = registrationInfo.consent_text;
    if (registrationInfo.policy_url) {
      $('privacy-link').href = registrationInfo.policy_url;
      $('privacy-link').hidden = false;
    }
    $('submit-register').disabled = false;
  } catch (error) {
    message.textContent = error.message;
    message.hidden = false;
  }
};
$('cancel-register').onclick = () => $('register-dialog').close();
$('register-dialog').onclose = () => { $('register-form').reset(); registrationInfo = null; };
$('register-form').onsubmit = async event => {
  event.preventDefault();
  if (!registrationInfo?.enabled) return;
  const message = $('register-message');
  message.hidden = true;
  if ($('register-password').value !== $('register-password-repeat').value) {
    message.textContent = 'Пароли не совпадают.';
    message.hidden = false;
    return;
  }
  $('submit-register').disabled = true;
  $('cancel-register').disabled = true;
  try {
    const user = await api('/register', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:$('register-username').value, email:$('register-email').value,
        password:$('register-password').value, consent:$('register-consent').checked,
        consent_version:registrationInfo.consent_version})});
    $('register-dialog').close();
    $('username').value = user.username;
    $('login-error').textContent = 'Учётная запись создана. Введите заданный вами пароль для входа.';
    $('password').focus();
  } catch (error) {
    message.textContent = error.message;
    message.hidden = false;
  } finally {
    $('submit-register').disabled = !registrationInfo?.enabled;
    $('cancel-register').disabled = false;
  }
};
async function loggedIn(user) {
  state.csrf = user.csrf;
  state.isAdmin = user.is_admin === true;
  $('users-nav').hidden = !state.isAdmin;
  $('admin-study-filters').hidden = !state.isAdmin;
  $('jobs-heading').textContent = state.isAdmin ? 'Все исследования пользователей' : 'Мои исследования';
  $('study-policy').textContent = state.isAdmin
    ? 'Удаление результатов переносит исходный ZIP в архив. Архив можно скачать или отправить на повторный анализ; исходный владелец сохраняется в записи.'
    : 'Удаление скрывает исследование из вашего списка. Администратор сохраняет доступ к результатам и исходному архиву.';
  state.uploadLimit = user.upload_limit || 2147483648;
  $('account').textContent = user.username;
  $('login').hidden = true;
  $('workspace').hidden = false;
  $('upload-limit').textContent = 'ZIP до ' + fmt((user.upload_limit || 2147483648) / 1024 ** 3, 1) + ' ГиБ';
  await refresh();
  poll = setInterval(() => refresh().catch(e => notice(e.message)), 3000);
}
$('login-form').addEventListener('submit', async e => {
  e.preventDefault();
  const button = e.target.querySelector('button');
  button.disabled = true;
  try {
    const user = await api('/login', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        username: $('username').value,
        password: $('password').value
      })
    });
    $('login-error').textContent = '';
    $('password').value = '';
    await loggedIn(user);
  } catch (e) {
    $('login-error').textContent = e.message;
  } finally {
    button.disabled = false;
  }
});
$('logout').onclick = async () => {
  try {
    await api('/logout', {
      method: 'POST'
    });
    loggedOut();
  } catch (e) {
    notice(e.message);
  }
};

function currentSeries() {
  return state.result?.series[state.series];
}

function currentStone() {
  return currentSeries()?.stones[state.side]?.[state.stone];
}

function base() {
  return '/jobs/' + state.job + '/series/' + state.series;
}

function view(name) {
  if (name === 'users' && !state.isAdmin) name = 'studies';
  state.view = name;
  for (const key of Object.keys(titles)) $(key).hidden = key !== name || (!state.result && !['studies', 'users'].includes(key));
  $('empty').hidden = ['studies', 'users'].includes(name) || !!state.result;
  $('study-bar').hidden = name === 'users' || !state.result;
  $('page-title').textContent = titles[name];
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b.dataset.view === name));
  if (name === 'images') requestAnimationFrame(draw);
  if (name === 'stones' && window.Plotly && $('model').data) requestAnimationFrame(() => Plotly.Plots.resize($('model')));
  if (name === 'users') refreshUsers().catch(e => notice(e.message));
}
document.querySelectorAll('nav button').forEach(b => b.onclick = () => view(b.dataset.view));

function userMessage(text, error = false) {
  const node = $('user-form-message');
  node.textContent = text;
  node.classList.toggle('error', error);
  node.hidden = !text;
}

async function refreshUsers() {
  if (!state.isAdmin) return;
  const auth = state.csrf, epoch = ++state.usersEpoch;
  const users = await api('/admin/users');
  if (auth !== state.csrf || epoch !== state.usersEpoch || !state.isAdmin) return;
  $('users-list').replaceChildren();
  $('users-count').textContent = 'Учётных записей: ' + users.length;
  for (const user of users) {
    const row = el('tr'), name = el('td'), status = el('td'), actions = el('td');
    name.append(el('strong', user.username), el('small', user.is_admin ? 'Администратор' : 'Пользователь'));
    if (user.email) name.append(el('small', user.email));
    status.append(el('span', user.enabled ? 'Активен' : 'Отключён', 'user-status' + (user.enabled ? '' : ' inactive')));
    if (user.is_admin) {
      actions.append(el('span', 'Защищён', 'muted'));
    } else {
      const button = el('button', 'Удалить', 'delete-user');
      button.type = 'button';
      button.setAttribute('aria-label', 'Удалить пользователя ' + user.username);
      button.onclick = () => {
        deleteTarget = user;
        $('delete-user-description').textContent = 'Учётная запись «' + user.username + '». Исследований: ' + user.studies + '.';
        $('delete-user-dialog').showModal();
      };
      actions.append(button);
    }
    row.append(name, status, el('td', String(user.studies)), actions);
    $('users-list').append(row);
  }
}

$('show-new-password').onchange = () => {
  $('new-password').type = $('show-new-password').checked ? 'text' : 'password';
};
$('create-user-form').onsubmit = async event => {
  event.preventDefault();
  if (!state.isAdmin) return;
  const auth = state.csrf;
  $('create-user').disabled = true;
  userMessage('');
  try {
    const user = await api('/admin/users', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: $('new-username').value, password: $('new-password').value})
    });
    if (auth !== state.csrf) return;
    $('create-user-form').reset();
    $('new-password').type = 'password';
    userMessage('Пользователь «' + user.username + '» создан. Можно входить в систему.');
    await refreshUsers();
  } catch (error) {
    if (auth === state.csrf) userMessage(error.message, true);
  } finally {
    $('create-user').disabled = false;
  }
};
$('refresh-users').onclick = () => refreshUsers().catch(error => notice(error.message));
$('password-settings').onclick = () => {
  $('change-password-form').reset();
  $('password-message').hidden = true;
  $('password-dialog').showModal();
};
$('cancel-password').onclick = () => $('password-dialog').close();
$('password-dialog').onclose = () => $('change-password-form').reset();
$('change-password-form').onsubmit = async event => {
  event.preventDefault();
  const auth = state.csrf;
  const errorMessage = text => {
    $('password-message').textContent = text;
    $('password-message').hidden = !text;
  };
  errorMessage('');
  if ($('changed-password').value !== $('confirm-password').value) {
    errorMessage('Новые пароли не совпадают. Проверьте оба поля.');
    return;
  }
  $('save-password').disabled = true;
  try {
    const user = await api('/me/password', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({current_password: $('current-password').value,
                            new_password: $('changed-password').value})
    });
    if (auth !== state.csrf) return;
    state.csrf = user.csrf;
    $('password-dialog').close();
    notice('Пароль изменён и сохранён. Остальные сеансы завершены.');
  } catch (error) {
    if (auth === state.csrf) errorMessage(error.message);
  } finally {
    $('save-password').disabled = false;
  }
};
let deleteTarget = null;
$('cancel-delete-user').onclick = () => $('delete-user-dialog').close();
$('delete-user-dialog').onclose = () => { deleteTarget = null; };
$('confirm-delete-user').onclick = async () => {
  if (!deleteTarget || !state.isAdmin) return;
  const target = deleteTarget, auth = state.csrf;
  $('confirm-delete-user').disabled = true;
  try {
    await api('/admin/users/' + encodeURIComponent(target.id), {method: 'DELETE'});
    if (auth !== state.csrf) return;
    $('delete-user-dialog').close();
    userMessage('Пользователь «' + target.username + '» удалён. Исследования сохранены.');
    await refreshUsers();
  } catch (error) {
    if (auth === state.csrf) {
      $('delete-user-dialog').close();
      userMessage(error.message, true);
    }
  } finally {
    $('confirm-delete-user').disabled = false;
  }
};

async function refresh() {
  const auth = state.csrf;
  const jobs = [];
  const limit = state.jobLimit || 100;
  while (jobs.length < limit) {
    const batch = await api('/jobs?offset=' + jobs.length + '&limit=' + Math.min(100, limit - jobs.length));
    jobs.push(...batch);
    if (batch.length < 100) break;
  }
  if (auth !== state.csrf) return;
  $('more-jobs').hidden = jobs.length < limit;
  const signature = JSON.stringify(jobs.map(({
    updated,
    ...rest
  }) => rest));
  if (signature === state.jobsSignature) return;
  state.jobsSignature = signature;
  state.jobs = jobs;
  renderJobs();
}
function renderJobs() {
  const list = $('jobs');
  list.replaceChildren();
  const owner = $('job-owner-filter').value.trim().toLowerCase();
  const filter = $('job-status-filter').value;
  const jobs = state.jobs.filter(j => !state.isAdmin || (
    (!owner || j.owner?.toLowerCase().includes(owner)) &&
    (filter === 'all' || (filter === 'deleted' && j.deleted_by_user_at && !j.archived_at) ||
     (filter === 'archived' && j.archived_at) || (filter === 'active' && !j.deleted_by_user_at && !j.archived_at))));
  if (!jobs.length) list.append(el('p', 'Исследования не найдены.', 'muted'));
  for (const job of jobs) {
    const row = el('article', undefined, 'card job'),
      name = el('div');
    name.append(el('strong', job.filename), el('small', new Date(job.created * 1000).toLocaleString('ru-RU')));
    if (state.isAdmin) name.append(el('small', 'Владелец: ' + job.owner + (job.owner_deleted ? ' · учётная запись удалена' : '')));
    if (job.source_job_id) name.append(el('small', 'Повторный анализ · источник: ' + job.source_job_id));
    const status = el('div');
    status.append(el('span', job.display_status));
    if (job.deleted_by_user_at && !job.archived_at) status.append(el('small', job.stage));
    if (job.archived_at && job.deleted_by_user_at) status.append(el('small', 'Ранее удалено пользователем'));
    if (['queued', 'running', 'uploading'].includes(job.state)) {
      const p = el('progress');
      p.max = 100;
      p.value = job.progress;
      status.append(p);
    }
    if (job.error) status.append(el('p', job.error, 'job-error'));
    const actions = el('div', undefined, 'job-actions');
    const button = (text, fn) => {
      const b = el('button', text);
      b.onclick = async () => {
        b.disabled = true;
        try {
          await fn();
        } catch (e) {
          notice(e.message);
        } finally {
          b.disabled = false;
        }
      };
      actions.append(b);
    };
    if (job.state === 'completed') button('Открыть →', () => openJob(job.id));
    if (['running', 'queued', 'uploading'].includes(job.state)) button('Отменить', async () => {
      await api('/jobs/' + job.id + '/cancel', {
        method: 'POST'
      });
      await refresh();
    });
    if (state.isAdmin && ['completed', 'failed', 'cancelled', 'archived'].includes(job.state) && job.archive_available) {
      const download = el('a', 'Скачать исходный ZIP', 'button');
      download.href = '/api/jobs/' + job.id + '/archive';
      actions.append(download);
      button('Повторить анализ', async () => {
        if (!confirm('Создать отдельное исследование администратора из этого ZIP?')) return;
        await api('/jobs/' + job.id + '/reprocess', {method: 'POST'});
        await refresh();
      });
    }
    if ((!state.isAdmin && !['archived', 'deleting'].includes(job.state)) ||
        (state.isAdmin && ['completed', 'failed', 'cancelled', 'deleting'].includes(job.state))) button(state.isAdmin ? 'Удалить результаты → в архив' : 'Удалить', async () => {
      if (!confirm(state.isAdmin ? 'Удалить результаты и распакованные DICOM? Исходный ZIP сохранится в архиве администратора.' : 'Скрыть исследование из вашего списка? Администратор сохранит доступ к нему.')) return;
      await api('/jobs/' + job.id, {
        method: 'DELETE'
      });
      if (state.job === job.id) {
        state.result = null;
        state.job = null;
        state.epoch++;
        state.modelEpoch++;
        state.imageEpoch++;
        $('study-bar').hidden = true;
        view('studies');
      }
      await refresh();
    });
    row.append(name, status, actions);
    list.append(row);
  }
}
$('job-owner-filter').oninput = renderJobs;
$('job-status-filter').onchange = renderJobs;
$('more-jobs').onclick = () => { state.jobLimit = (state.jobLimit || 100) + 100; refresh().catch(e => notice(e.message)); };
$('refresh').onclick = () => refresh().catch(e => notice(e.message));
async function upload(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.zip')) {
    notice('Выберите ZIP-архив.');
    return;
  }
  if (file.size > state.uploadLimit || file.name.length > 180) {
    notice('Превышен размер ZIP или длина имени файла (до 180 символов).');
    return;
  }
  const auth = state.csrf;
  const controller = new AbortController();
  state.uploadAbort = controller;
  const input = $('archive'),
    progress = $('upload-progress');
  input.disabled = true;
  progress.hidden = false;
  progress.value = 0;
  notice('');
  try {
    notice('Шифрование и защищённая передача архива…');
    await window.sudEncryptedUpload(file, {
      api: (path, options) => {
        if (auth !== state.csrf) throw Error('Сеанс изменён. Загрузка остановлена.');
        return api(path, options);
      },
      signal: controller.signal,
      progress: value => { progress.value = value; }
    });
    notice('Архив принят. Ход обработки отображается в списке исследований.');
    await refresh();
  } catch (e) {
    if (auth === state.csrf) notice(e.message);
  } finally {
    input.disabled = false;
    input.value = '';
    progress.hidden = true;
    if (state.uploadAbort === controller) state.uploadAbort = null;
  }
}
$('archive').onchange = e => upload(e.target.files[0]);
const drop = $('drop');
for (const event of ['dragenter', 'dragover']) drop.addEventListener(event, e => {
  e.preventDefault();
  drop.classList.add('drag');
});
for (const event of ['dragleave', 'drop']) drop.addEventListener(event, e => {
  e.preventDefault();
  drop.classList.remove('drag');
});
drop.addEventListener('drop', e => {
  if (!$('archive').disabled) upload(e.dataTransfer.files[0]);
});
async function openJob(id) {
  const epoch = ++state.epoch;
  notice('Загрузка результатов…');
  const result = await api('/jobs/' + id + '/result');
  if (epoch !== state.epoch) return;
  state.job = id;
  state.result = result;
  state.series = 0;
  const select = $('series');
  select.replaceChildren();
  result.series.forEach((s, i) => {
    const o = el('option', (i + 1) + '. ' + (s.identity.series_description || 'Серия') + ' · ' + s.identity.patient_id);
    o.value = i;
    select.append(o);
  });
  $('study-bar').hidden = false;
  selectSeries();
  view('images');
}

function selectSeries() {
  state.series = Number($('series').value);
  state.modelEpoch++;
  state.imageEpoch++;
  const s = currentSeries();
  $('identity').textContent = 'ID ' + s.identity.patient_id + ' · ' + s.identity.study_code + ' · серия ' + s.identity.series_number;
  $('pdf').hidden = !s.analysis_available;
  $('pdf').href = '/api/jobs/' + state.job + '/pdf/' + state.series;
  notice((s.warnings || []).join('\n'));
  selectImages(true);
  selectStones();
}
$('series').onchange = selectSeries;
let image = null,
  records = [],
  scale = 1,
  panX = 0,
  panY = 0,
  lastDrag = null;

function selectImages(reset = false) {
  const s = currentSeries();
  if (!s) return;
  records = s.images[$('projection').value].map((record, index) => ({
    ...record,
    index
  })).filter(r => !$('only-detected').checked || r.detected);
  $('slice').max = Math.max(0, records.length - 1);
  if (reset) $('slice').value = 0;
  if (Number($('slice').value) >= records.length) $('slice').value = 0;
  loadImage(reset);
}

function loadImage(reset = false) {
  const record = records[Number($('slice').value)];
  const epoch = ++state.imageEpoch;
  image = null;
  if (reset) {
    scale = 1;
    panX = panY = 0;
  }
  if (!record) {
    $('slice-info').textContent = 'Нет изображений';
    draw();
    return;
  }
  $('slice-info').textContent = 'Слой ' + record.layer + ' · ' + (Number($('slice').value) + 1) + ' / ' + records.length;
  const next = new Image();
  next.onload = () => {
    if (epoch === state.imageEpoch) {
      image = next;
      draw();
    }
  };
  next.onerror = () => {
    if (epoch === state.imageEpoch) notice('Не удалось загрузить изображение. Обновите страницу.');
  };
  next.src = '/api' + base() + '/images/' + $('projection').value + '/' + record.index + '?annotated=' + $('annotated').checked;
}
$('slice').oninput = () => loadImage();
$('projection').onchange = () => selectImages(true);
$('only-detected').onchange = () => selectImages(true);
$('annotated').onchange = () => loadImage();
$('grid').onchange = () => draw();

function shiftSlice(delta) {
  $('slice').value = Math.max(0, Math.min(records.length - 1, Number($('slice').value) + delta));
  loadImage();
}
$('previous').onclick = () => shiftSlice(-1);
$('next').onclick = () => shiftSlice(1);

function zoom(factor) {
  scale = Math.min(12, Math.max(1, scale * factor));
  if (scale === 1) panX = panY = 0;
  draw();
}
$('zoom-in').onclick = () => zoom(1.3);
$('zoom-out').onclick = () => zoom(1 / 1.3);
$('reset').onclick = () => {
  scale = 1;
  panX = panY = 0;
  draw();
};
const canvas = $('canvas');
canvas.addEventListener('wheel', e => {
  e.preventDefault();
  zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15);
}, {
  passive: false
});
canvas.onpointerdown = e => {
  lastDrag = {
    x: e.clientX,
    y: e.clientY
  };
  canvas.setPointerCapture(e.pointerId);
};
canvas.onpointermove = e => {
  if (lastDrag) {
    panX += e.clientX - lastDrag.x;
    panY += e.clientY - lastDrag.y;
    lastDrag = {
      x: e.clientX,
      y: e.clientY
    };
    draw();
  }
};
canvas.onpointerup = canvas.onpointercancel = () => lastDrag = null;

function draw() {
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.fillStyle = '#16232d';
  ctx.fillRect(0, 0, rect.width, rect.height);
  if (!image) return;
  const r = records[Number($('slice').value)],
    left = 48,
    bottom = 35,
    pad = 18,
    w = rect.width - left - pad,
    h = rect.height - bottom - pad;
  const mmw = r.width * r.sx_mm,
    mmh = r.height * r.sy_mm,
    fit = Math.min(w / mmw, h / mmh),
    k = fit * scale,
    iw = mmw * k,
    ih = mmh * k;
  panX = Math.max(-iw * .5, Math.min(iw * .5, panX));
  panY = Math.max(-ih * .5, Math.min(ih * .5, panY));
  const x = left + (w - iw) / 2 + panX,
    y = pad + (h - ih) / 2 + panY;
  ctx.save();
  ctx.beginPath();
  ctx.rect(left, pad, w, h);
  ctx.clip();
  ctx.drawImage(image, x, y, iw, ih);
  if ($('grid').checked) {
    const step = scale > 1.05 ? 1 : 5;
    ctx.lineWidth = .5;
    for (let axis = 0; axis < 2; axis++) {
      const max = axis ? mmh : mmw;
      for (let v = 0; v <= max; v += step) {
        const major = v % 5 === 0;
        ctx.strokeStyle = major ? '#b9e5e950' : '#b9e5e925';
        ctx.beginPath();
        if (axis) {
          const py = y + ih - v * k;
          ctx.moveTo(x, py);
          ctx.lineTo(x + iw, py);
        } else {
          ctx.moveTo(x + v * k, y);
          ctx.lineTo(x + v * k, y + ih);
        }
        ctx.stroke();
      }
    }
  }
  ctx.restore();
  ctx.font = '10px Segoe UI';
  ctx.fillStyle = '#d4e5e8';
  ctx.strokeStyle = '#a8bfc6';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(left, pad);
  ctx.lineTo(left, pad + h);
  ctx.lineTo(left + w, pad + h);
  ctx.stroke();
  const labelStep = 5 * Math.max(1, Math.ceil(28 / (5 * k)));
  for (let v = 0; v <= mmw; v++) {
    const px = x + v * k;
    if (px < left || px > left + w) continue;
    ctx.beginPath();
    ctx.moveTo(px, pad + h);
    ctx.lineTo(px, pad + h + (v % 5 === 0 ? 6 : 3));
    ctx.stroke();
    if (v % labelStep === 0) ctx.fillText(v, px - 5, pad + h + 18);
  }
  for (let v = 0; v <= mmh; v++) {
    const py = y + ih - v * k;
    if (py < pad || py > pad + h) continue;
    ctx.beginPath();
    ctx.moveTo(left, py);
    ctx.lineTo(left - (v % 5 === 0 ? 6 : 3), py);
    ctx.stroke();
    if (v % labelStep === 0) ctx.fillText(v, 4, py + 3);
  }
  const vw = Math.max(0, Math.min(x + iw, left + w) - Math.max(x, left)) / k,
    vh = Math.max(0, Math.min(y + ih, pad + h) - Math.max(y, pad)) / k;
  $('dimensions').textContent = 'Изображение: ' + fmt(mmw) + ' × ' + fmt(mmh) + ' мм · Видимый фрагмент: ' + fmt(vw) + ' × ' + fmt(vh) + ' мм · Сетка ' + (scale > 1.05 ? '1' : '5') + ' мм · X →, Y ↑';
}
new ResizeObserver(() => {
  draw();
  if (window.Plotly && $('model').data) Plotly.Plots.resize($('model'));
}).observe(document.querySelector('main'));

function selectStones() {
  state.side = $('side').value;
  const stones = currentSeries()?.stones[state.side] || [];
  const select = $('stone');
  select.replaceChildren();
  stones.forEach((s, i) => {
    const o = el('option', 'Камень ' + (i + 1) + ' · слой ' + s.layer);
    o.value = i;
    select.append(o);
  });
  state.stone = 0;
  $('stone-count').textContent = 'Найдено: ' + stones.length;
  showParameters();
}
$('side').onchange = selectStones;
$('stone').onchange = () => {
  state.stone = Number($('stone').value);
  showParameters();
};

function clearModel() {
  state.modelEpoch++;
  if (window.Plotly) Plotly.purge($('model'));
  $('model').replaceChildren(el('p', 'Выберите модель для интерактивного просмотра.', 'muted'));
  $('model-info').textContent = '';
}

function showParameters() {
  clearModel();
  const s = currentStone(),
    series = currentSeries();
  $('metrics').replaceChildren();
  $('previews').replaceChildren();
  $('laser-table').replaceChildren();
  $('legend').replaceChildren();
  $('mode').textContent = 'Наведите указатель на ячейку или выберите её, чтобы увидеть название режима.';
  $('show-stone').disabled = $('show-cut').disabled = !s;
  $('show-kidney').disabled = !series?.kidney_available?.[state.side];
  if (!s) {
    $('metrics').append(el('p', series?.analysis_available ? 'Для этой почки камни не определены.' : 'Расчёт недоступен. См. сообщение о детекциях.', 'muted'));
    $('laser-title').textContent = 'Камень не выбран';
    return;
  }
  const fields = [
    ['Размеры, мм', s.dimensions_mm.map(v => fmt(v)).join(' × ')],
    ['Объём, см³', fmt(s.volume_cm3, 3)],
    ['Масса, г', fmt(s.mass_g, 3)],
    ['Плотность, г/см³', fmt(s.density_g_cm3)],
    ['HU, среднее', fmt(s.hu_mean, 0)],
    ['HU, минимум / максимум', fmt(s.hu_min, 0) + ' / ' + fmt(s.hu_max, 0)],
    ['Вокселей', fmt(s.voxel_count, 0)],
    ['Слои', s.layers.join('–')]
  ];
  for (const [label, value] of fields) {
    const card = el('div', undefined, 'metric');
    card.append(el('small', label), el('strong', value));
    $('metrics').append(card);
  }
  for (const kind of ['slice', 'hu', 'density']) {
    const img = el('img');
    img.alt = {
      slice: 'Фрагмент КТ',
      hu: 'Воксельная модель HU',
      density: 'Воксельная модель плотности'
    } [kind];
    img.src = '/api' + base() + '/stones/' + state.side + '/' + state.stone + '/preview/' + kind;
    img.loading = 'lazy';
    $('previews').append(img);
  }
  renderLaser(s);
}

function renderLaser(stone) {
  const table = el('table'),
    head = el('tr');
  head.append(el('th', 'Энергия, Дж / Частота, Гц'));
  stone.laser.frequencies.forEach(f => head.append(el('th', f)));
  const thead = el('thead');
  thead.append(head);
  table.append(thead);
  const body = el('tbody');
  stone.laser.cells.forEach((row, i) => {
    const tr = el('tr');
    tr.append(el('th', stone.laser.energies[i]));
    row.forEach((cell, j) => {
      const td = el('td'),
        b = el('button', fmt(cell.value));
      b.style.backgroundColor = 'rgb(' + cell.background.join(',') + ')';
      b.style.color = 'rgb(' + cell.color.join(',') + ')';
      b.title = cell.mode;
      b.setAttribute('aria-label', stone.laser.energies[i] + ' Дж, ' + stone.laser.frequencies[j] + ' Гц: ' + cell.value + '. ' + cell.mode);
      const show = () => {
        $('mode').textContent = stone.laser.energies[i] + ' Дж · ' + stone.laser.frequencies[j] + ' Гц · t = ' + fmt(cell.value) + ' — ' + cell.mode;
      };
      b.onpointerenter = b.onfocus = b.onclick = show;
      td.append(b);
      tr.append(td);
    });
    body.append(tr);
  });
  table.append(body);
  $('laser-table').append(table);
  $('laser-title').textContent = (state.side === 'right' ? 'Правая' : 'Левая') + ' почка · камень ' + (state.stone + 1) + ' · ' + fmt(stone.mass_g, 3) + ' г';
  stone.laser.legend.forEach(item => {
    const row = el('div', undefined, 'legend-item'),
      swatch = el('span', undefined, 'swatch');
    swatch.style.backgroundColor = 'rgb(' + item.color.join(',') + ')';
    row.append(swatch, el('span', item.mode));
    $('legend').append(row);
  });
}
async function showModel(kind, cut = false) {
  const epoch = ++state.modelEpoch;
  const path = '/api' + base() + '/models/' + state.side + '/' + kind + '/' + state.stone + '?cut=' + cut;
  $('model-info').textContent = 'Загрузка модели…';
  try {
    const response = await fetch(path, {
      credentials: 'same-origin'
    });
    if (!response.ok) throw Error('Модель недоступна.');
    const model = await response.json();
    if (epoch !== state.modelEpoch) return;
    Plotly.purge($('model'));
    $('model').replaceChildren();
    await Plotly.newPlot($('model'), model.data, model.layout, {
      responsive: true,
      displaylogo: false,
      modeBarButtonsToRemove: ['sendDataToCloud']
    });
    if (epoch === state.modelEpoch) $('model-info').textContent = kind === 'kidney' ? 'Почка и плотные участки HU >160' : cut ? 'Срез 15% по каждой оси' : 'Полный объём';
  } catch (e) {
    if (epoch === state.modelEpoch) $('model-info').textContent = e.message;
  }
}
$('show-stone').onclick = () => showModel('stone');
$('show-cut').onclick = () => showModel('stone', true);
$('show-kidney').onclick = () => showModel('kidney');
document.addEventListener('keydown', e => {
  if (state.view !== 'images' || ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName)) return;
  if (e.key === 'ArrowRight') shiftSlice(1);
  if (e.key === 'ArrowLeft') shiftSlice(-1);
});
api('/me').then(loggedIn).catch(() => loggedOut());
