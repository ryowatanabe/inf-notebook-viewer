import { STYLES, DIFFICULTIES, normalizeSearch, matchesSearch, optionText, valueText, playDate, playTime, generatedTime } from './format.js';

const $ = id => document.getElementById(id);
const dataRoot = new URL('../data/', import.meta.url);
let songs = [], manifest, selected = null, cached = new Map(), request = 0;
let resultLimit = 50, historyLimit = 50;
function stored(key) { try { return localStorage.getItem(key); } catch { return null; } }
function persist(key, value) { try { localStorage.setItem(key, value); } catch { /* Storage may be disabled. */ } }
function el(tag, text, className) {
  const element = document.createElement(tag);
  if (text != null) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function setStatus(text, error = false) { $('status').textContent = text; $('status').classList.toggle('error', error); }
async function getJSON(path) {
  const response = await fetch(new URL(path, dataRoot), { cache: 'no-cache' });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  if (data.schemaVersion !== 1) throw new Error('未対応のデータ形式です');
  return data;
}
function readSelection() {
  const params = new URLSearchParams(location.search);
  const style = params.get('style') || stored('style');
  const difficulty = params.get('difficulty') || stored('difficulty');
  $('style').value = STYLES.includes(style) ? style : 'SP';
  $('difficulty').value = DIFFICULTIES.includes(difficulty) ? difficulty : 'ANOTHER';
  $('search').value = params.get('q') || '';
  const mode = params.get('match') || stored('search-mode');
  $('search-mode').value = mode === 'exact' ? 'exact' : 'partial';
  updateSearchPlaceholder();
  selected = params.get('song');
}
function updateSearchPlaceholder() {
  $('search').placeholder = $('search-mode').value === 'exact' ? '曲名をすべて入力' : '曲名の一部を入力';
}
function writeURL(push = true) {
  const url = new URL(location.href);
  for (const [key, value] of Object.entries({ song: selected, style: $('style').value, difficulty: $('difficulty').value, q: $('search').value, match: $('search-mode').value })) {
    if (value) url.searchParams.set(key, value); else url.searchParams.delete(key);
  }
  if (url.href !== location.href) history[push ? 'pushState' : 'replaceState']({}, '', url);
}
function renderResults() {
  const needle = normalizeSearch($('search').value.trim());
  const matching = songs.filter(song => matchesSearch(song.search, needle, $('search-mode').value));
  $('result-count').textContent = `${matching.length.toLocaleString()}曲`;
  $('results').replaceChildren();
  const fragment = document.createDocumentFragment();
  for (const song of matching.slice(0, resultLimit)) {
    const button = el('button', null, 'song-button');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(song.id === selected));
    button.append(el('span', song.title, 'song-title'));
    const chart = song.charts[$('style').value]?.[$('difficulty').value];
    button.append(el('span', chart ? `☆${chart.level ?? '?'}` : '記録なし', 'song-tag'));
    button.addEventListener('click', () => {
      selected = song.id; historyLimit = 50;
      writeURL(); renderResults(); showDetail(true);
    });
    const li = el('li'); li.append(button); fragment.append(li);
  }
  if (!matching.length) fragment.append(el('li', '一致する楽曲がありません。', 'empty'));
  $('results').append(fragment);
  $('more-results').hidden = matching.length <= resultLimit;
}
function showEmpty(message) { $('detail').replaceChildren(el('p', message, 'empty')); }
function drawChart(song) {
  const panel = $('detail'); panel.replaceChildren();
  const style = $('style').value, difficulty = $('difficulty').value;
  const chart = song.charts[style]?.[difficulty];
  panel.append(el('p', `${style} / ${difficulty} · ☆${chart?.level ?? '?'}`, 'chart-label'), el('h2', song.title, 'detail-title'));
  if (!chart) { panel.append(el('p', 'この譜面のプレイ記録はありません。', 'empty')); return; }
  const bests = el('div', null, 'bests');
  for (const [key, label] of [['score', 'ベストスコア'], ['missCount', '最少BP']]) {
    const card = el('div', null, 'best-card'), best = chart.best[key];
    card.append(el('p', label, 'best-label'), el('p', valueText(best.value), 'best-value'), el('p', optionText(best.options), 'option-text'));
    bests.append(card);
  }
  panel.append(bests);
  const heading = el('div', null, 'section-heading');
  heading.append(el('h2', 'プレイ履歴'), el('span', `${chart.history.length.toLocaleString()}件 · 新しい順`, 'muted')); panel.append(heading);
  if (!chart.history.length) { panel.append(el('p', '履歴はありません。ベスト記録のみ保存されています。', 'empty')); return; }
  const list = el('ol', null, 'history-list');
  for (const row of chart.history.slice(0, historyLimit)) {
    const li = el('li', null, 'history-row'), meta = el('div', null, 'row-meta');
    const date = el('time', playDate(row.timestamp));
    date.dateTime = `${playDate(row.timestamp)}T${row.timestamp.slice(9, 11)}:${row.timestamp.slice(11, 13)}:${row.timestamp.slice(13, 15)}`;
    date.title = playTime(row.timestamp);
    const lamp = el('span', null, 'lamp');
    lamp.dataset.lamp = row.clearType ?? '';
    lamp.title = valueText(row.clearType);
    lamp.setAttribute('role', 'img');
    lamp.setAttribute('aria-label', `クリアランプ: ${valueText(row.clearType)}`);
    meta.append(date, lamp, el('span', valueText(row.djLevel), 'rank'));
    const values = el('div', null, 'row-values');
    for (const [label, value] of [['SCORE', valueText(row.score)], ['BP', valueText(row.missCount)], ['SPEED', row.playSpeed == null ? '—' : `x${row.playSpeed}`]]) {
      const span = el('span');
      span.append(el('small', label), el('span', value, 'history-value'));
      values.append(span);
    }
    li.append(meta, values, el('p', optionText(row.options), 'option-text')); list.append(li);
  }
  panel.append(list);
  if (chart.history.length > historyLimit) {
    const more = el('button', `さらに表示（残り${chart.history.length - historyLimit}件）`, 'history-more');
    more.addEventListener('click', () => { historyLimit += 50; drawChart(song); }); panel.append(more);
  }
}
async function showDetail(scroll = false) {
  const token = ++request, id = selected;
  if (!id) { showEmpty('楽曲を選ぶと、ベスト記録と使用オプションを確認できます。'); return; }
  const entry = songs.find(song => song.id === id);
  if (!entry) { showEmpty('指定した楽曲は現在のデータにありません。'); return; }
  showEmpty('プレイ履歴を読み込んでいます…');
  if (scroll && matchMedia('(max-width: 700px)').matches) $('detail').scrollIntoView({ behavior: 'auto', block: 'start' });
  try {
    if (!cached.has(id)) cached.set(id, getJSON(entry.file).then(song => {
      if (song.id !== id || !song.charts) throw new Error('楽曲データが一致しません');
      return song;
    }));
    const song = await cached.get(id);
    if (token === request) drawChart(song);
  } catch {
    cached.delete(id);
    if (token !== request) return;
    showEmpty('履歴を取得できませんでした。通信状態を確認して再試行してください。');
    const retry = el('button', '再試行', 'retry'); retry.addEventListener('click', () => showDetail()); $('detail').append(retry);
  }
}
async function loadData() {
  ++request; cached = new Map(); $('refresh').disabled = true;
  setStatus('データを読み込んでいます…');
  try {
    manifest = await getJSON('manifest.json');
    const index = await getJSON(manifest.index);
    if (!Array.isArray(index.songs)) throw new Error('検索一覧が不正です');
    songs = index.songs.map(song => ({ ...song, search: normalizeSearch(song.title) }));
    const date = generatedTime(manifest.generatedAt);
    $('dataset-info').textContent = `データ生成: ${date}\n最新プレイ: ${playTime(manifest.latestPlay)}`;
    setStatus(''); renderResults(); await showDetail();
  } catch {
    songs = []; $('results').replaceChildren(); $('result-count').textContent = ''; $('dataset-info').textContent = '';
    setStatus('データを取得できません。JSONを生成し、HTTPサーバー経由で開いてください。', true);
    showEmpty('データを生成済みの場合は、通信状態を確認して「データを更新」を押してください。');
  } finally { $('refresh').disabled = false; }
}
$('search').addEventListener('input', () => { resultLimit = 50; writeURL(false); renderResults(); });
$('search-mode').addEventListener('change', () => {
  persist('search-mode', $('search-mode').value); updateSearchPlaceholder();
  resultLimit = 50; writeURL(); renderResults();
});
for (const key of ['style', 'difficulty']) $(key).addEventListener('change', () => {
  persist(key, $(key).value); historyLimit = 50; writeURL(); renderResults(); showDetail();
});
$('more-results').addEventListener('click', () => { resultLimit += 50; renderResults(); });
$('refresh').addEventListener('click', loadData);
window.addEventListener('popstate', () => { readSelection(); resultLimit = 50; historyLimit = 50; renderResults(); showDetail(); });
readSelection(); loadData();
