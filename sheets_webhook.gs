/**
 * Вебхук @uchetskup_bot → Касса и Продажи.
 * Развернуть → Веб-приложение, доступ: Все.
 */
var SHEET_ID = '1f1GSrlJp_MQGCMi6tBlRfEkvpfzQ7qyNLv-kYs0iP4E';
var WEBHOOK_SECRET = 'motya-kassa-7f3c9e';
var MONTHS = ['Января','Февраля','Марта','Апреля','Мая','Июня','Июля','Августа','Сентября','Октября','Ноября','Декабря'];
var COL_DELIVERY = 7;  // G Доставка
var COL_CONS = 8;      // H Расходник
var COL_ROLE = 11;     // K Роль Матвея
var COL_PCT = 12;      // L % Матвея
var COL_PLACE = 17;    // Q Где
var COL_BUYER = 18;    // R Контакт
var COL_CAT = 20;      // T Категория
var COL_NOTE = 21;     // U Примечание

function cache_() {
  return CacheService.getScriptCache();
}

function cacheGet_(key) {
  try {
    var hit = cache_().get(key);
    if (hit) return ContentService.createTextOutput(hit).setMimeType(ContentService.MimeType.JSON);
  } catch (e) {}
  return null;
}

function cachePut_(key, obj) {
  try { cache_().put(key, JSON.stringify(obj), 20); } catch (e) {}
  return json_(obj);
}

function cacheDrop_() {
  try {
    cache_().removeAll(['balance', 'setup', 'unsold', 'lots', 'inv_list']);
  } catch (e) {}
}

function doPost(e) {
  var body = {};
  try { body = JSON.parse((e && e.postData && e.postData.contents) || '{}'); }
  catch (err) { return json_({ok: false, error: 'bad json'}); }
  if (body.secret !== WEBHOOK_SECRET) return json_({ok: false, error: 'forbidden'});

  var action = body.action || 'write';
  if (action === 'ping') return json_({ok: true, pong: true});

  if (action === 'balance') {
    var c0 = cacheGet_('balance');
    if (c0) return c0;
  }
  if (action === 'setup') {
    var c1 = cacheGet_('setup');
    if (c1) return c1;
  }
  if (action === 'unsold') {
    var c2 = cacheGet_('unsold');
    if (c2) return c2;
  }
  if (action === 'inv_list') {
    var c3 = cacheGet_('inv_list');
    if (c3) return c3;
  }

  var ss = SpreadsheetApp.getActive() || SpreadsheetApp.openById(SHEET_ID);
  var sales = ss.getSheetByName('Продажи');
  var kassa = ss.getSheetByName('Касса');
  var writing = (
    action === 'update' || action === 'delete' || action === 'inv_start' ||
    action === 'inv_save' || action === 'inv_cancel' || action === 'inv_update' ||
    action === 'write' || body.cash_dir || body.type === 'buy' || body.type === 'sell'
  );
  if (sales && (writing || action === 'setup')) ensureExtraCols_(sales);

  if (action === 'balance') return cachePut_('balance', readBalance_(ss, kassa));
  if (action === 'setup') {
    maybeFillCats_(sales);
    var b = readBalance_(ss, kassa);
    b.setup = true;
    return cachePut_('setup', b);
  }
  if (action === 'unsold') {
    return cachePut_('unsold', {ok: true, items: listUnsold_(sales)});
  }
  if (action === 'lots') {
    if (sales) maybeFillCats_(sales);
    return json_({ok: true, items: listLots_(sales, body)});
  }
  if (action === 'lot') {
    return json_({ok: true, item: getLot_(sales, Number(body.sheet_row || body.row || 0))});
  }
  if (action === 'update') {
    updateLot_(sales, body);
    cacheDrop_();
    return json_({ok: true});
  }
  if (action === 'delete') {
    var rowDel = Number(body.sheet_row || body.row || 0);
    var item = getLot_(sales, rowDel);
    if (!item && body.product) {
      item = {product: body.product, cost: Number(body.cost || 0), sale: Number(body.sale || 0), sold: !!body.sale};
    }
    var rev = reverseKassa_(kassa, item);
    deleteLot_(sales, rowDel);
    cacheDrop_();
    return json_({ok: true, reversed: rev});
  }
  if (action === 'inv_start') { cacheDrop_(); return json_(invStart_(ss, body)); }
  if (action === 'inv_save') { cacheDrop_(); return json_(invSave_(ss, body)); }
  if (action === 'inv_cancel') { cacheDrop_(); return json_(invCancel_(ss, body)); }
  if (action === 'inv_list') return cachePut_('inv_list', {ok: true, entries: invList_(ss)});
  if (action === 'inv_get') return json_({ok: true, items: invGet_(ss, Number(body.session_id || 0))});
  if (action === 'inv_update') { cacheDrop_(); return json_(invUpdate_(ss, body)); }
  if (action === 'kassa_all') {
    return json_({ok: true, rows: kassaRows_(kassa)});
  }
  if (action === 'kassa_del') {
    var kd = Number(body.row || 0);
    if (!kd || kd < 9) return json_({ok: false, error: 'bad row'});
    kassa.deleteRow(kd);
    cacheDrop_();
    return json_({ok: true});
  }

  if (body.cash_dir === 'in' || body.cash_dir === 'out') {
    appendKassa_(kassa, body);
  }
  var written = 0;
  if (body.type === 'buy') {
    written = appendSales_(sales, body);
  }
  if (body.type === 'sell') {
    markSold_(sales, body);
  }
  cacheDrop_();
  return json_({ok: true, sheet_row: written});
}

function doGet() {
  return json_({ok: true, service: 'uchetskup'});
}

function json_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function parseDate_(s) {
  var p = String(s || '').split('.');
  if (p.length === 3) return new Date(Number(p[2]), Number(p[1]) - 1, Number(p[0]));
  return s || '';
}

function formatDate_(v) {
  if (Object.prototype.toString.call(v) === '[object Date]' && !isNaN(v.getTime())) {
    var d = v.getDate(), m = v.getMonth() + 1, y = v.getFullYear();
    return ('0' + d).slice(-2) + '.' + ('0' + m).slice(-2) + '.' + y;
  }
  return v ? String(v) : '';
}

function monthName_(d) {
  if (Object.prototype.toString.call(d) !== '[object Date]' || isNaN(d.getTime())) return '';
  return MONTHS[d.getMonth()] || '';
}

function nextRow_(sh, col, start) {
  var max = sh.getMaxRows();
  var last = start - 1;
  var from = start;
  var chunk = 250;
  while (from <= max) {
    var n = Math.min(chunk, max - from + 1);
    var vals = sh.getRange(from, col, n, 1).getValues();
    var empty = true;
    for (var i = 0; i < vals.length; i++) {
      if (vals[i][0] !== '' && vals[i][0] != null) {
        last = from + i;
        empty = false;
      }
    }
    if (empty && from > start) break;
    from += chunk;
  }
  return last + 1;
}

function lastDataRow_(sh) {
  return Math.max(8, nextRow_(sh, 5, 9) - 1);
}

function ensureExtraCols_(sh) {
  if (!sh) return;
  try {
    if (cache_().get('colsOk') === '1') return;
  } catch (e0) {}
  var need = 21;
  if (sh.getMaxColumns() < need) {
    sh.insertColumnsAfter(sh.getMaxColumns(), need - sh.getMaxColumns());
  }
  var headCat = sh.getRange(8, COL_CAT).getValue();
  var headNote = sh.getRange(8, COL_NOTE).getValue();
  if (headCat !== 'Категория' || headNote !== 'Примечание') {
    try { sh.showColumns(COL_CAT, 2); } catch (e1) {}
    sh.setColumnWidth(COL_CAT, 140);
    sh.setColumnWidth(COL_NOTE, 220);
    sh.getRange(8, COL_CAT).setValue('Категория');
    sh.getRange(8, COL_NOTE).setValue('Примечание');
    try {
      sh.getRange(8, 17).copyFormatToRange(sh, COL_CAT, COL_NOTE, 8, 8);
    } catch (e2) {
      try { sh.getRange(8, 5).copyFormatToRange(sh, COL_CAT, COL_NOTE, 8, 8); } catch (e3) {}
    }
  }
  try { cache_().put('colsOk', '1', 21600); } catch (e4) {}
}

function maybeFillCats_(sh) {
  if (!sh) return;
  try {
    if (cache_().get('catsOk') === '1') return;
  } catch (e0) {}
  fillCats_(sh);
  try { cache_().put('catsOk', '1', 21600); } catch (e1) {}
}

function guessCategory_(name) {
  var n = String(name || '').toLowerCase();
  if (/переходник|riser|райзер|12-2x6|12vhpwr/.test(n)) return 'Переходник';
  if (/комплект|\bпк\b|сборк/.test(n)) return 'ПК/комплект';
  if (/клав|наушн|колон|мышь|перифер/.test(n)) return 'Периферия';
  if (/rtx|gtx|rx\s|r9\s|hd\d|видео|gpu|1050|1660|2060|3060|3070|4060|470|580|5500|6700|отвал/.test(n)) return 'Видеокарта';
  if (/\bi[3579]\b|xeon|ryzen|athlon|процессор|\bcpu\b|12400|10100|9900|2650|2670|3570|3440/.test(n)) return 'Процессор';
  if (/ddr|озу|память|hynix|hyperx/.test(n)) return 'ОЗУ';
  if (/ssd|hdd|nvme|накоп|apacer|toshiba|barracuda|kingston 240|wd blue/.test(n)) return 'Накопитель';
  if (/плат|материн|\bb760\b|\bh81\b|\bh410|\bb250\b|\bx99\b|fintech|ds3h/.test(n)) return 'Материнка';
  if (/бп\b|блок пит|\bpsu\b|ginzu|accord|aerocool vp|\d+w/.test(n) && /пит|бп|psu|ginzu|accord|w\b/.test(n)) return 'Блок питания';
  if (/корпус|case|cougar|deepcool|panzer/.test(n)) return 'Корпус';
  if (/кулер|охлажд|вертуш|вентилятор|argb|башен/.test(n)) return 'Охлаждение';
  if (/монитор|display/.test(n)) return 'Монитор';
  if (/блок пит/.test(n)) return 'Блок питания';
  return 'Другое';
}

function fillCats_(sh) {
  if (!sh) return;
  var last = lastDataRow_(sh);
  if (last < 9) return;
  var n = last - 8;
  var names = sh.getRange(9, 5, n, 1).getValues();
  var cats = sh.getRange(9, COL_CAT, n, 1).getValues();
  var changed = false;
  for (var i = 0; i < n; i++) {
    if (names[i][0] && (cats[i][0] === '' || cats[i][0] == null)) {
      cats[i][0] = guessCategory_(names[i][0]);
      changed = true;
    }
  }
  if (changed) sh.getRange(9, COL_CAT, n, 1).setValues(cats);
}

function num_(v) {
  if (v === '' || v == null) return 0;
  var n = Number(v);
  return isNaN(n) ? 0 : n;
}

function readBalance_(ss, kassa) {
  var start = 14400;
  var handCell = null;
  try {
    var sales = ss && ss.getSheetByName('Продажи');
    if (sales) {
      var q5 = num_(sales.getRange('Q5').getValue());
      var n5 = sales.getRange('N5').getValue();
      if (q5) start = q5;
      if (n5 !== '' && n5 != null && !isNaN(Number(n5))) handCell = Number(n5);
    }
  } catch (e0) {}
  var inn = 0, out = 0, recent = [];
  if (kassa) {
    var last = nextRow_(kassa, 2, 9) - 1;
    if (last >= 9) {
      var vals = kassa.getRange(9, 1, last - 8, 5).getValues();
      for (var i = 0; i < vals.length; i++) {
        inn += num_(vals[i][2]);
        out += num_(vals[i][3]);
      }
      for (var j = vals.length - 1; j >= 0 && recent.length < 8; j--) {
        var cin = num_(vals[j][2]);
        var cout = num_(vals[j][3]);
        if (!cin && !cout && !vals[j][1]) continue;
        recent.push({
          date: formatDate_(vals[j][0]),
          what: String(vals[j][1] || ''),
          inn: cin,
          out: cout,
          comment: String(vals[j][4] || '')
        });
      }
    }
  }
  var computed = start + inn - out;
  return {
    ok: true,
    start: start,
    inn: inn,
    out: out,
    hand: (handCell != null ? handCell : computed),
    computed: computed,
    recent: recent
  };
}

function rowToItem_(row, vals) {
  var product = vals[4];
  if (!product) return null;
  var cat = vals[19] || guessCategory_(product);
  return {
    row: row,
    lot: String(vals[0] || ''),
    month: String(vals[1] || ''),
    buy: formatDate_(vals[2]),
    sell: formatDate_(vals[3]),
    product: String(product),
    cost: num_(vals[5]),
    sale: num_(vals[8]),
    category: String(cat || 'Другое'),
    delivery: num_(vals[6]),
    consumable: num_(vals[7]),
    role: String(vals[10] || ''),
    place: String(vals[16] || ''),
    buyer: String(vals[17] || ''),
    note: String(vals[20] || ''),
    sold: !(vals[3] === '' || vals[3] == null)
  };
}

function listUnsold_(sh) {
  if (!sh) return [];
  var last = lastDataRow_(sh);
  if (last < 9) return [];
  var vals = sh.getRange(9, 1, last - 8, 21).getValues();
  var items = [];
  for (var i = 0; i < vals.length; i++) {
    if (vals[i][3] !== '' && vals[i][3] != null) continue;
    var it = rowToItem_(9 + i, vals[i]);
    if (it) items.push(it);
  }
  return items;
}

function listLots_(sh, b) {
  if (!sh) return [];
  var last = lastDataRow_(sh);
  if (last < 9) return [];
  var q = String(b.query || b.q || '').toLowerCase();
  var mode = String(b.mode || 'all');
  var vals = sh.getRange(9, 1, last - 8, 21).getValues();
  var items = [];
  for (var i = 0; i < vals.length; i++) {
    var it = rowToItem_(9 + i, vals[i]);
    if (!it) continue;
    if (mode === 'unsold' && it.sold) continue;
    if (q) {
      var blob = (it.product + ' ' + it.category + ' ' + it.note + ' ' + it.buyer).toLowerCase();
      if (blob.indexOf(q) < 0) continue;
    }
    items.push(it);
  }
  items.reverse();
  if (items.length > 80) items = items.slice(0, 80);
  return items;
}

function getLot_(sh, row) {
  if (!sh || !row || row < 9) return null;
  var vals = sh.getRange(row, 1, 1, 21).getValues()[0];
  return rowToItem_(row, vals);
}

function appendKassa_(sh, b) {
  if (!sh) return;
  var row = nextRow_(sh, 2, 9);
  if (row < 9) row = 9;
  var d = parseDate_(b.date);
  sh.getRange(row, 1).setValue(d).setNumberFormat('dd.mm.yyyy');
  sh.getRange(row, 2).setValue(b.kassa_what || '');
  if (b.cash_dir === 'in') sh.getRange(row, 3).setValue(Number(b.amount || 0));
  if (b.cash_dir === 'out') sh.getRange(row, 4).setValue(Number(b.amount || 0));
  var bits = [];
  if (b.product) bits.push(b.product);
  if (b.place) bits.push(b.place);
  if (b.buyer) bits.push(b.buyer);
  if (b.note) bits.push(b.note);
  if (!bits.length && b.comment) bits.push(b.comment);
  sh.getRange(row, 5).setValue(bits.join(' · '));
}

function addNum_(sh, row, col, v) {
  if (v === '' || v == null) return;
  var n = Number(v);
  if (!n) return;
  var cur = num_(sh.getRange(row, col).getValue());
  sh.getRange(row, col).setValue(cur + n);
}

function writeRole_(sh, row, b) {
  if (!b.role) return;
  sh.getRange(row, COL_ROLE).setValue(b.role);
  if (b.role_pct !== '' && b.role_pct != null) {
    var p = Number(b.role_pct);
    if (p > 1) p = p / 100;
    sh.getRange(row, COL_PCT).setValue(p).setNumberFormat('0%');
  } else {
    // роль без процента: чистим L, чтобы не оставался % прошлой роли
    sh.getRange(row, COL_PCT).clearContent();
  }
}

function appendSales_(sh, b) {
  if (!sh) return 0;
  var row = nextRow_(sh, 5, 9);
  if (row < 9) row = 9;
  var d = parseDate_(b.date);
  sh.getRange(row, 1).setValue(b.lot || 'Данил');
  sh.getRange(row, 2).setValue(monthName_(d));
  sh.getRange(row, 3).setValue(d).setNumberFormat('dd.mm.yyyy');
  sh.getRange(row, 5).setValue(b.product || '');
  sh.getRange(row, 6).setValue(Number(b.amount || 0));
  addNum_(sh, row, COL_DELIVERY, b.delivery);
  addNum_(sh, row, COL_CONS, b.consumable);
  writeRole_(sh, row, b);
  var cat = b.category || guessCategory_(b.product);
  sh.getRange(row, COL_CAT).setValue(cat);
  if (b.note) sh.getRange(row, COL_NOTE).setValue(b.note);
  return row;
}

function markSold_(sh, b) {
  if (!sh) return;
  var row = Number(b.sheet_row || b.row || 0);
  if (!row || row < 9) return;
  var d = parseDate_(b.date);
  sh.getRange(row, 4).setValue(d).setNumberFormat('dd.mm.yyyy');
  sh.getRange(row, 9).setValue(Number(b.amount || 0));
  addNum_(sh, row, COL_DELIVERY, b.delivery);
  addNum_(sh, row, COL_CONS, b.consumable);
  writeRole_(sh, row, b);
  if (b.place) sh.getRange(row, COL_PLACE).setValue(b.place);
  if (b.buyer) sh.getRange(row, COL_BUYER).setValue(b.buyer);
  if (b.note) sh.getRange(row, COL_NOTE).setValue(b.note);
  if (b.category) sh.getRange(row, COL_CAT).setValue(b.category);
}

function updateLot_(sh, b) {
  if (!sh) return;
  var row = Number(b.sheet_row || b.row || 0);
  if (!row || row < 9) return;
  var f = b.fields || b;
  if (f.lot != null && f.lot !== '') sh.getRange(row, 1).setValue(f.lot);
  if (f.buy_date || f.date_buy) {
    var d = parseDate_(f.buy_date || f.date_buy);
    sh.getRange(row, 2).setValue(monthName_(d));
    sh.getRange(row, 3).setValue(d).setNumberFormat('dd.mm.yyyy');
  }
  if (f.sell_date || f.date_sell) {
    var ds = parseDate_(f.sell_date || f.date_sell);
    sh.getRange(row, 4).setValue(ds).setNumberFormat('dd.mm.yyyy');
  }
  if (f.clear_sell) {
    sh.getRange(row, 4).clearContent();
    sh.getRange(row, 9).clearContent();
  }
  if (f.product != null && f.product !== '') sh.getRange(row, 5).setValue(f.product);
  if (f.cost != null && f.cost !== '') sh.getRange(row, 6).setValue(Number(f.cost));
  if (f.sale != null && f.sale !== '') sh.getRange(row, 9).setValue(Number(f.sale));
  if (f.category != null && f.category !== '') sh.getRange(row, COL_CAT).setValue(f.category);
  if (f.place != null) sh.getRange(row, COL_PLACE).setValue(f.place);
  if (f.buyer != null) sh.getRange(row, COL_BUYER).setValue(f.buyer);
  if (f.note != null) sh.getRange(row, COL_NOTE).setValue(f.note);
  if (f.delivery != null && f.delivery !== '') sh.getRange(row, COL_DELIVERY).setValue(Number(f.delivery));
  if (f.consumable != null && f.consumable !== '') sh.getRange(row, COL_CONS).setValue(Number(f.consumable));
  if (f.role != null) {
    sh.getRange(row, COL_ROLE).setValue(f.role);
    if (f.role_pct != null && f.role_pct !== '') {
      var p = Number(f.role_pct);
      if (p > 1) p = p / 100;
      sh.getRange(row, COL_PCT).setValue(p).setNumberFormat('0%');
    } else {
      // роль поменяли на вариант без процента — % тоже убираем
      sh.getRange(row, COL_PCT).clearContent();
    }
  }
}

function reverseKassa_(kassa, item) {
  var out = [];
  if (!kassa || !item || !item.product) return out;
  var product = String(item.product);
  var last = nextRow_(kassa, 2, 9) - 1;
  if (last < 9) return out;
  var vals = kassa.getRange(9, 1, last - 8, 5).getValues();
  var today = new Date();
  var plan = [];
  for (var i = 0; i < vals.length; i++) {
    var what = String(vals[i][1] || '');
    var comment = String(vals[i][4] || '');
    if (what.indexOf('Отмена') >= 0) continue;
    if (comment.indexOf(product) < 0) continue;
    var cin = num_(vals[i][2]);
    var cout = num_(vals[i][3]);
    if (cout > 0) {
      plan.push({dir: 'in', amount: cout, what: 'Отмена закупа', product: product});
    }
    if (cin > 0) {
      plan.push({dir: 'out', amount: cin, what: 'Отмена продажи', product: product});
    }
  }
  for (var j = 0; j < plan.length; j++) {
    var p = plan[j];
    var nr = nextRow_(kassa, 2, 9);
    if (nr < 9) nr = 9;
    kassa.getRange(nr, 1).setValue(today).setNumberFormat('dd.mm.yyyy');
    kassa.getRange(nr, 2).setValue(p.what);
    if (p.dir === 'in') kassa.getRange(nr, 3).setValue(p.amount);
    if (p.dir === 'out') kassa.getRange(nr, 4).setValue(p.amount);
    kassa.getRange(nr, 5).setValue(p.product);
    out.push(p);
  }
  return out;
}

function deleteLot_(sh, row) {
  if (!sh || !row || row < 9) return;
  var clearCols = [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, COL_PCT, COL_CAT, COL_PLACE, COL_BUYER, COL_NOTE];
  for (var i = 0; i < clearCols.length; i++) {
    sh.getRange(row, clearCols[i]).clearContent();
  }
}

function kassaRows_(sh) {
  if (!sh) return [];
  var last = nextRow_(sh, 2, 9) - 1;
  if (last < 9) return [];
  var vals = sh.getRange(9, 1, last - 8, 5).getValues();
  var out = [];
  for (var i = 0; i < vals.length; i++) {
    if (!vals[i][1] && !vals[i][2] && !vals[i][3]) continue;
    out.push({
      row: 9 + i,
      date: formatDate_(vals[i][0]),
      what: String(vals[i][1] || ''),
      inn: num_(vals[i][2]),
      out: num_(vals[i][3]),
      comment: String(vals[i][4] || '')
    });
  }
  return out;
}

/* ===== Инвентаризация =====
   Журнал: A дата · B период MM.YYYY · C кто · D всего · E на месте · F отсутствует · G статус
   Результаты: J сессия(строка журнала) · K дата · L период · M товар · N дата закупа ·
               O закуп ₽ · P строка в Продажах · Q статус · R правка */

var INV_NAME = 'Инвентаризация';
var INV_C_GO = '#d9ead3';   // зелёный — на месте
var INV_C_MISS = '#f4cccc'; // красный — отсутствует
var INV_C_WAIT = '#fff2cc'; // жёлтый — идёт
var INV_C_OFF = '#efefef';  // серый — отменена

function ensureInvSheet_(ss) {
  if (!ss) return null;
  var sh = ss.getSheetByName(INV_NAME);
  var created = false;
  if (!sh) {
    sh = ss.insertSheet(INV_NAME);
    created = true;
  }
  try {
    if (!created && cache_().get('invHead') === '1') return sh;
  } catch (e0) {}
  if (sh.getMaxColumns() < 18) sh.insertColumnsAfter(sh.getMaxColumns(), 18 - sh.getMaxColumns());
  var head1 = ['Дата', 'Период', 'Кто', 'Всего', 'На месте', 'Отсутствует', 'Статус'];
  var head2 = ['Сессия', 'Дата', 'Месяц', 'Товар', 'Дата закупа', 'Закуп ₽', 'Строка', 'Статус', 'Правка'];
  sh.getRange(8, 1, 1, head1.length).setValues([head1]).setFontWeight('bold');
  sh.getRange(8, 10, 1, head2.length).setValues([head2]).setFontWeight('bold');
  sh.setColumnWidth(3, 120);
  sh.setColumnWidth(7, 110);
  sh.setColumnWidth(13, 240);
  sh.setColumnWidth(17, 150);
  sh.setColumnWidth(18, 140);
  try { cache_().put('invHead', '1', 21600); } catch (e1) {}
  return sh;
}

function invStart_(ss, b) {
  var sh = ensureInvSheet_(ss);
  if (!sh) return {ok: false, error: 'no sheet'};
  var row = nextRow_(sh, 1, 9);
  sh.getRange(row, 1).setValue(new Date()).setNumberFormat('dd.mm.yyyy');
  sh.getRange(row, 3).setValue(String(b.who || ''));
  sh.getRange(row, 7).setValue('🟡 идёт').setBackground(INV_C_WAIT);
  return {ok: true, session_id: row};
}

function invSave_(ss, b) {
  var sh = ensureInvSheet_(ss);
  if (!sh) return {ok: false, error: 'no sheet'};
  var id = Number(b.session_id || 0);
  if (!id || id < 9) id = nextRow_(sh, 1, 9);
  var c = b.counts || {};
  var d = parseDate_(b.date) || new Date();
  sh.getRange(id, 1).setValue(d).setNumberFormat('dd.mm.yyyy');
  sh.getRange(id, 2).setValue(String(b.period || ''));
  sh.getRange(id, 3).setValue(String(b.who || ''));
  sh.getRange(id, 4).setValue(Number(c.total || 0));
  sh.getRange(id, 5).setValue(Number(c.ok || 0));
  sh.getRange(id, 6).setValue(Number(c.miss || 0));
  sh.getRange(id, 7).setValue('🟢 завершена').setBackground(INV_C_GO);
  var items = b.items || [];
  var wrote = 0;
  if (items.length) {
    var out = [];
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var st = String(it.status || '');
      out.push([
        id,
        d,
        String(b.period || ''),
        String(it.product || ''),
        parseDate_(it.buy) || '',
        Number(it.cost || 0),
        Number(it.sheet_row || 0),
        st,
        ''
      ]);
    }
    var r0 = nextRow_(sh, 10, 9);
    sh.getRange(r0, 10, out.length, 9).setValues(out);
    sh.getRange(r0, 11, out.length, 1).setNumberFormat('dd.mm.yyyy');
    for (var j = 0; j < out.length; j++) {
      var isOk = out[j][7].indexOf('✅') === 0;
      sh.getRange(r0 + j, 17).setBackground(isOk ? INV_C_GO : INV_C_MISS);
    }
    wrote = out.length;
  }
  return {ok: true, session_id: id, wrote: wrote};
}

function invCancel_(ss, b) {
  var sh = ensureInvSheet_(ss);
  if (!sh) return {ok: false, error: 'no sheet'};
  var id = Number(b.session_id || 0);
  if (id >= 9) {
    sh.getRange(id, 7).setValue('⚪ отменена').setBackground(INV_C_OFF);
  }
  return {ok: true};
}

function invList_(ss) {
  var sh = ensureInvSheet_(ss);
  if (!sh) return [];
  var last = nextRow_(sh, 1, 9) - 1;
  if (last < 9) return [];
  var vals = sh.getRange(9, 1, last - 8, 7).getValues();
  var out = [];
  for (var i = 0; i < vals.length; i++) {
    if (vals[i][0] === '' && vals[i][0] == null && !vals[i][6]) continue;
    out.push({
      id: 9 + i,
      date: formatDate_(vals[i][0]),
      period: String(vals[i][1] || ''),
      who: String(vals[i][2] || ''),
      total: num_(vals[i][3]),
      ok: num_(vals[i][4]),
      miss: num_(vals[i][5]),
      status: String(vals[i][6] || '')
    });
  }
  return out.reverse();
}

function invGet_(ss, id) {
  var sh = ensureInvSheet_(ss);
  if (!sh || !id || id < 9) return [];
  var last = nextRow_(sh, 10, 9) - 1;
  if (last < 9) return [];
  var vals = sh.getRange(9, 10, last - 8, 9).getValues();
  var out = [];
  for (var i = 0; i < vals.length; i++) {
    if (Number(vals[i][0]) !== Number(id)) continue;
    out.push({
      product: String(vals[i][3] || ''),
      buy: formatDate_(vals[i][4]),
      cost: num_(vals[i][5]),
      sheet_row: num_(vals[i][6]),
      status: String(vals[i][7] || ''),
      fixed: String(vals[i][8] || '')
    });
  }
  return out;
}

function invUpdate_(ss, b) {
  var sh = ensureInvSheet_(ss);
  if (!sh) return {ok: false, error: 'no sheet'};
  var id = Number(b.session_id || 0);
  var rowLot = Number(b.sheet_row || 0);
  var last = nextRow_(sh, 10, 9) - 1;
  if (last < 9 || !id || !rowLot) return {ok: false, error: 'not found'};
  var vals = sh.getRange(9, 10, last - 8, 7).getValues();
  for (var i = 0; i < vals.length; i++) {
    if (Number(vals[i][0]) === id && Number(vals[i][6]) === rowLot) {
      var r = 9 + i;
      var st = String(b.status || '');
      sh.getRange(r, 17).setValue(st).setBackground(st.indexOf('✅') === 0 ? INV_C_GO : INV_C_MISS);
      sh.getRange(r, 18).setValue(String(b.fixed || ''));
      return {ok: true, row: r};
    }
  }
  return {ok: false, error: 'not found'};
}
