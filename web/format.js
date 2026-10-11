export const STYLES = ['SP', 'DP', 'DP BATTLE'];
export const DIFFICULTIES = ['BEGINNER', 'NORMAL', 'HYPER', 'ANOTHER', 'LEGGENDARIA'];

export function normalizeSearch(text) {
  // Strip accents only on Latin letters; Japanese dakuten remains significant.
  return text.normalize('NFKC').toLowerCase().normalize('NFD')
    .replace(/([a-z])\p{M}+/gu, '$1').normalize('NFC')
    .replace(/[ァ-ヶ]/g, char => String.fromCharCode(char.charCodeAt(0) - 0x60));
}

export function optionText(options) {
  if (options == null) return '不明';
  const parts = ['arrange', 'flip'].map(key => options[key]).filter(Boolean);
  if (options.battle) parts.push('BATTLE');
  if (options.assist) parts.push(options.assist);
  if (options.allscratch) parts.push('ALL-SCR');
  if (options.regularspeed) parts.push('REGUL-SPEED');
  if (options.special) parts.push('SPECIAL（旧記録）');
  if (options.unknown && Object.keys(options.unknown).length) parts.push('未対応オプションあり');
  if (parts.length) return parts.join(', ');
  const known = ['arrange', 'flip', 'assist', 'battle'];
  return known.every(key => Object.hasOwn(options, key)) && options.battle === false
    ? '正規' : '不明';
}

export function valueText(value) { return value == null ? '不明' : String(value); }
export function playDate(ts) {
  return ts ? `${ts.slice(0, 4)}-${ts.slice(4, 6)}-${ts.slice(6, 8)}` : '不明';
}
export function playTime(ts) {
  return ts ? `${playDate(ts)} ${ts.slice(9, 11)}:${ts.slice(11, 13)}` : '不明';
}
