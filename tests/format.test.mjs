import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import { test } from 'node:test';
const source = await readFile(new URL('../web/format.js', import.meta.url), 'utf8');
const { normalizeSearch, matchesSearch, optionText, valueText } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('exact search excludes longer titles while preserving normalization and empty queries', () => {
  const match = (title, query, mode) => matchesSearch(normalizeSearch(title), normalizeSearch(query.trim()), mode);
  assert.equal(match('A', 'A', 'exact'), true);
  assert.equal(match('AA', 'A', 'exact'), false);
  assert.equal(match('AA', 'A', 'partial'), true);
  assert.equal(match('ＣＡＦÉ', ' cafe ', 'exact'), true);
  assert.equal(match('ガラス', 'がらす', 'exact'), true);
  assert.equal(match('ガラス', 'からす', 'exact'), false);
  assert.equal(match('A', '', 'exact'), true);
  assert.equal(match('A', '  ', 'partial'), true);
});
test('case, width, kana and Latin accents are normalized, dakuten stays significant', () => {
  assert.equal(normalizeSearch('ＣＡＦÉ'), normalizeSearch('cafe'));
  assert.equal(normalizeSearch('ｶﾞラス'), normalizeSearch('がらす'));
  assert.notEqual(normalizeSearch('ガ'), normalizeSearch('カ'));
  assert.equal(normalizeSearch('Über'), 'uber');
});
test('unknown options and zero values are not inferred as normal or missing', () => {
  assert.equal(optionText(null), '不明');
  assert.equal(optionText({}), '不明');
  assert.equal(optionText({arrange:null, flip:null, assist:null, battle:false}), '正規');
  assert.match(optionText({arrange:'MIR/OFF', flip:'FLIP', battle:true}), /MIR\/OFF, FLIP, BATTLE/);
  assert.match(optionText({unknown:{future:true}}), /未対応/);
  assert.equal(valueText(0), '0');
  assert.equal(valueText(null), '不明');
});
