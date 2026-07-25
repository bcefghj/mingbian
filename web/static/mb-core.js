/* ============================================================
   明辨 MINGBIAN · 前端内核
   顶栏、Markdown 渲染、provenance 悬浮卡、toast、格式化工具。
   所有页面共用同一份，保证分享页与首页完全同构。
   ============================================================ */
(function (global) {
  'use strict';

  const MB = {};

  /* -------------------------------------------------- DOM 工具 */

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        if (k === 'class') node.className = attrs[k];
        else if (k === 'html') node.innerHTML = attrs[k];
        else if (k === 'text') node.textContent = attrs[k];
        else if (k.startsWith('on') && typeof attrs[k] === 'function') {
          node.addEventListener(k.slice(2).toLowerCase(), attrs[k]);
        } else if (attrs[k] != null) node.setAttribute(k, attrs[k]);
      }
    }
    (children || []).forEach(c => {
      if (c == null) return;
      node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return node;
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* -------------------------------------------------- 格式化 */

  function fmtTime(ts) {
    if (!ts) return '—';
    const d = new Date(ts * (ts < 1e12 ? 1000 : 1));
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function fmtDur(ms) {
    if (!ms) return '—';
    const s = ms / 1000;
    if (s < 60) return s.toFixed(1) + ' 秒';
    return Math.floor(s / 60) + ' 分 ' + Math.round(s % 60) + ' 秒';
  }

  function pct(v) {
    if (v == null) return '—';
    return Math.round(v * 100) + '%';
  }

  /* -------------------------------------------------- Markdown */

  function inline(s) {
    let t = esc(s);
    t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
    t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    t = t.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');
    // 裸链接
    t = t.replace(/(^|[\s(（])(https?:\/\/[^\s)）<]+)/g,
      '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
    return t;
  }

  /** 极简 Markdown 渲染器。够用即可，不引入外部依赖。 */
  function md(src) {
    if (!src) return '';
    const lines = String(src).replace(/\r/g, '').split('\n');
    const out = [];
    let inCode = false, codeBuf = [], listType = null, listBuf = [];
    let tblBuf = [], inTbl = false, para = [];

    const flushPara = () => {
      if (para.length) { out.push('<p>' + inline(para.join(' ')) + '</p>'); para = []; }
    };
    const flushList = () => {
      if (!listBuf.length) return;
      out.push(`<${listType}>` + listBuf.map(x => '<li>' + inline(x) + '</li>').join('') + `</${listType}>`);
      listBuf = []; listType = null;
    };
    const flushTbl = () => {
      if (!tblBuf.length) { inTbl = false; return; }
      const rows = tblBuf.map(r => r.replace(/^\||\|$/g, '').split('|').map(c => c.trim()));
      const head = rows.shift() || [];
      const body = rows.filter(r => !r.every(c => /^:?-+:?$/.test(c)));
      out.push('<table><thead><tr>' + head.map(h => '<th>' + inline(h) + '</th>').join('') +
        '</tr></thead><tbody>' +
        body.map(r => '<tr>' + r.map(c => '<td>' + inline(c) + '</td>').join('') + '</tr>').join('') +
        '</tbody></table>');
      tblBuf = []; inTbl = false;
    };
    const flushAll = () => { flushPara(); flushList(); flushTbl(); };

    for (const raw of lines) {
      const line = raw.replace(/\s+$/, '');
      if (/^```/.test(line)) {
        if (inCode) { out.push('<pre><code>' + esc(codeBuf.join('\n')) + '</code></pre>'); codeBuf = []; inCode = false; }
        else { flushAll(); inCode = true; }
        continue;
      }
      if (inCode) { codeBuf.push(raw); continue; }

      if (/^\|.*\|/.test(line)) { flushPara(); flushList(); inTbl = true; tblBuf.push(line); continue; }
      if (inTbl) flushTbl();

      if (!line.trim()) { flushAll(); continue; }

      let m;
      if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
        flushAll();
        const lvl = m[1].length;
        out.push(`<h${lvl}>${inline(m[2])}</h${lvl}>`);
        continue;
      }
      if (/^\s*([-*—]{3,})\s*$/.test(line)) { flushAll(); out.push('<hr>'); continue; }
      if ((m = line.match(/^>\s?(.*)$/))) {
        flushAll(); out.push('<blockquote>' + inline(m[1]) + '</blockquote>'); continue;
      }
      if ((m = line.match(/^\s*[-*+]\s+(.*)$/))) {
        flushPara();
        if (listType && listType !== 'ul') flushList();
        listType = 'ul'; listBuf.push(m[1]); continue;
      }
      if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
        flushPara();
        if (listType && listType !== 'ol') flushList();
        listType = 'ol'; listBuf.push(m[1]); continue;
      }
      flushList();
      para.push(line.trim());
    }
    if (inCode && codeBuf.length) out.push('<pre><code>' + esc(codeBuf.join('\n')) + '</code></pre>');
    flushAll();
    return out.join('\n');
  }

  /* -------------------------------------------------- Toast */

  let toastEl = null, toastTimer = null;
  function toast(msg) {
    if (!toastEl) {
      toastEl = el('div', { class: 'toast' });
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove('show'), 2200);
  }

  /* -------------------------------------------------- provenance 卡 */

  let provEl = null, provHide = null;

  function provCard() {
    if (!provEl) {
      provEl = el('div', { class: 'prov' });
      document.body.appendChild(provEl);
      provEl.addEventListener('mouseenter', () => clearTimeout(provHide));
      provEl.addEventListener('mouseleave', () => hideProv());
    }
    return provEl;
  }

  function showProv(anchor, ev) {
    const node = provCard();
    clearTimeout(provHide);
    const cred = ev.credibility || 0;
    const tierCls = cred >= 75 ? 'tag-ok' : cred >= 55 ? 'tag-accent' : 'tag-warn';
    node.innerHTML =
      `<h5>${esc(ev.title || ev.domain || '未命名来源')}</h5>` +
      `<div class="meta">` +
      `<span class="mono">${esc(ev.domain || '本地')}</span>` +
      `<span>${esc(ev.source_label || '')}</span>` +
      (ev.published_at ? `<span>发布 ${esc(ev.published_at)}</span>` : '') +
      (ev.captured_at ? `<span>抓取 ${esc(ev.captured_at)}</span>` : '') +
      `</div>` +
      `<div class="quote">${esc((ev.excerpt || '（无摘录）').slice(0, 220))}</div>` +
      `<div class="score row-between"><span>可信度 <b class="mono t2">${cred}</b>/100 · ` +
      `<span class="tag ${tierCls}">${esc(ev.ground_label || '')}</span></span>` +
      (ev.url ? `<a href="${esc(ev.url)}" target="_blank" rel="noopener">打开原文 →</a>` : '') +
      `</div>`;
    const r = anchor.getBoundingClientRect();
    const w = 340;
    let left = Math.min(Math.max(8, r.left), window.innerWidth - w - 12);
    let top = r.bottom + 8;
    node.style.left = left + 'px';
    node.style.top = top + 'px';
    node.classList.add('show');
    node.style.pointerEvents = 'auto';
    // 超出下边界就翻到上方
    requestAnimationFrame(() => {
      const h = node.offsetHeight;
      if (top + h > window.innerHeight - 10) node.style.top = Math.max(8, r.top - h - 8) + 'px';
    });
  }

  function hideProv() {
    provHide = setTimeout(() => {
      if (provEl) { provEl.classList.remove('show'); provEl.style.pointerEvents = 'none'; }
    }, 140);
  }

  /* -------------------------------------------------- 顶栏 */

  const NAV = [
    { href: './', label: '研判台', match: /^\/$|\/index/ },
    { href: './dashboard', label: '指标', match: /dashboard/ },
    { href: './experts', label: '专家册', match: /experts/ },
    { href: './bench', label: 'Benchmark', match: /bench/ },
    { href: './ledger', label: '调用台账', match: /ledger/ },
    { href: './about', label: '方法论', match: /about/ },
  ];

  function mountTopbar(active) {
    const host = $('#topbar');
    if (!host) return;
    const path = location.pathname;
    host.className = 'topbar';
    host.innerHTML =
      `<div class="topbar-in">` +
      `<a class="brand" href="./"><span class="brand-mark">辨</span>` +
      `<span class="col" style="gap:0"><span class="brand-name">明辨</span>` +
      `<span class="brand-en">Mingbian</span></span></a>` +
      `<nav class="nav">` +
      NAV.map(n => {
        const on = active ? (n.label === active) : n.match.test(path);
        return `<a class="${on ? 'on' : ''}" href="${n.href}">${n.label}</a>`;
      }).join('') +
      `</nav><span class="spacer"></span>` +
      `<span class="engine-badge" id="engineBadge" title="点击查看调用台账">` +
      `<span class="dot warn"></span><span>引擎检测中</span></span>` +
      `</div>`;
    const badge = $('#engineBadge');
    if (badge) badge.addEventListener('click', () => { location.href = './ledger'; });
    badge.style.cursor = 'pointer';
    refreshEngineBadge();
  }

  async function refreshEngineBadge() {
    const badge = $('#engineBadge');
    if (!badge) return;
    try {
      const r = await fetch('./api/engine').then(x => x.json());
      const ok = !!r.ok;
      badge.innerHTML =
        `<span class="dot ${ok ? '' : 'off'}"></span>` +
        `<span>InfiniSynapse · <b>${esc(r.model || 'deepseek-v4-pro')}</b></span>`;
      badge.title = ok ? '引擎在线，点击查看调用台账'
        : ('引擎不可达：' + (r.reason || '未知') + '（点击查看台账）');
    } catch (e) {
      badge.innerHTML = `<span class="dot off"></span><span>引擎状态未知</span>`;
    }
  }

  function mountFooter(extra) {
    const host = $('#footer');
    if (!host) return;
    host.className = 'foot wrap-wide';
    host.innerHTML =
      `<div class="row-between wrapflex">` +
      `<span>明辨 MINGBIAN · 多智能体证据研判引擎 · 由 InfiniSynapse deepseek-v4-pro 驱动</span>` +
      `<span>${extra || '本工具输出仅供决策参考，不构成投资、法律或医疗建议。'}</span>` +
      `</div>`;
  }

  /* -------------------------------------------------- 网络 */

  async function getJSON(url) {
    const r = await fetch(url, { headers: { 'Accept': 'application/json' } });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }

  async function postJSON(url, body) {
    const r = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    return j;
  }

  function copy(text) {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(() => toast('已复制'), () => toast('复制失败'));
    } else {
      const ta = el('textarea', { style: 'position:fixed;opacity:0' });
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); toast('已复制'); } catch (e) { toast('复制失败'); }
      ta.remove();
    }
  }

  Object.assign(MB, {
    $, $$, el, esc, md, inline, toast, copy,
    fmtTime, fmtDur, pct, getJSON, postJSON,
    showProv, hideProv, mountTopbar, mountFooter, refreshEngineBadge,
  });
  global.MB = MB;
})(window);
