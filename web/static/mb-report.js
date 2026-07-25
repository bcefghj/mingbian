/* ============================================================
   明辨 MINGBIAN · 报告渲染器
   首页实时结果与 /report/{id} 分享页共用这一份，保证完全同构。
   ============================================================ */
(function (global) {
  'use strict';
  const { el, esc, md, toast, copy, fmtDur, pct, postJSON } = global.MB;

  const STRENGTH_CLASS = {
    strong: 'strength-strong', moderate: 'strength-moderate',
    weak: 'strength-weak', contested: 'strength-contested',
    unsupported: 'strength-unsupported',
  };

  /* -------------------------------------------------- 证据 chip */

  function chipHTML(ev) {
    if (!ev) return '';
    const label = ev.domain || (ev.title || '').slice(0, 10) || '来源';
    return `<span class="ev-chip" data-ev="${esc(ev.ev_id)}">` +
      `${esc(label)}<span class="cred">${ev.credibility || 0}</span></span>`;
  }

  /** 把正文里的 [E1] / [ev_xxxx] 标记换成可悬浮的域名 chip。
      显示域名而不是编号——「统计局」比「[3]」有信息量得多。 */
  function injectChips(html, byRef) {
    return html.replace(/\[((?:E\d+|ev_[a-z0-9]+)(?:\s*[,、]\s*(?:E\d+|ev_[a-z0-9]+))*)\]/gi,
      (whole, inner) => {
        const parts = inner.split(/[,、]/).map(s => s.trim());
        const chips = parts.map(p => byRef[p] || byRef[p.toLowerCase()])
          .filter(Boolean).map(chipHTML);
        return chips.length ? chips.join('') : whole;
      });
  }

  function bindChips(root, evMap) {
    global.MB.$$('.ev-chip', root).forEach(chip => {
      const ev = evMap[chip.dataset.ev];
      if (!ev) return;
      chip.addEventListener('mouseenter', () => {
        global.MB.showProv(chip, ev);
        lightEvidenceRow(root, ev.ev_id, true);
      });
      chip.addEventListener('mouseleave', () => {
        global.MB.hideProv();
        lightEvidenceRow(root, ev.ev_id, false);
      });
      chip.addEventListener('click', () => {
        const row = root.querySelector(`[data-evrow="${ev.ev_id}"]`);
        if (row) { row.scrollIntoView({ behavior: 'smooth', block: 'center' }); flash(row); }
      });
    });
  }

  function lightEvidenceRow(root, evId, on) {
    const row = root.querySelector(`[data-evrow="${evId}"]`);
    if (row) row.style.background = on ? 'rgba(91,156,248,.10)' : '';
  }

  function flash(node) {
    node.style.transition = 'background 160ms';
    node.style.background = 'rgba(91,156,248,.22)';
    setTimeout(() => { node.style.background = ''; }, 900);
  }

  /* -------------------------------------------------- 各区块 */

  function verdictCard(r) {
    const conf = r.confidence || {};
    const stanceCls = /高风险|不可行|看空/.test(r.stance || '') ? 'tag-bad'
      : /可行|看多/.test(r.stance || '') ? 'tag-ok' : 'tag-accent';
    const p = conf.probability;
    const iv = conf.interval;

    const bits = [];
    bits.push(`<div class="row wrapflex mb12">` +
      (r.stance ? `<span class="tag ${stanceCls}">${esc(r.stance)}</span>` : '') +
      (conf.ipcc ? `<span class="tag tag-purple" title="对照 IPCC 可能性量表">${esc(conf.ipcc)}</span>` : '') +
      (r.as_of ? `<span class="tag">数据截至 ${esc(r.as_of)}</span>` : '') +
      (r.mode_config ? `<span class="tag">${esc(r.mode_config.name)}档</span>` : '') +
      `</div>`);
    bits.push(`<div class="fs20 t1" style="line-height:1.55;letter-spacing:-.015em">${esc(r.verdict || '（本次未生成一句话结论）')}</div>`);

    // 摊开本次实际覆盖的分析角度。质检里的「维度完整性」打的就是这一项，
    // 只给分数不给清单，那个分数就没法被检验。
    const dims = (r.dimensions || []).filter(Boolean);
    if (dims.length) {
      bits.push(`<div class="row wrapflex mt12" style="gap:6px">` +
        `<span class="t4 fs11" style="margin-right:2px">覆盖角度</span>` +
        dims.map(d => `<span class="tag">${esc(d)}</span>`).join('') + `</div>`);
    }

    if (p != null) {
      bits.push(`<div class="mt16">${confidenceLadder(conf)}</div>`);
    }
    return `<section class="card">${bits.join('')}</section>`;
  }

  /** 置信度三段式：基准率 → 调整项 → 最终值。
      裸百分比是不可审计的，摊开推演过程才有意义。 */
  function confidenceLadder(conf) {
    const base = conf.base_rate || {};
    const adjs = conf.adjustments || [];
    const p = conf.probability, iv = conf.interval || [];
    const rows = [];

    rows.push(`<div class="row" style="align-items:flex-start;gap:14px">` +
      `<span class="t4 fs12 nowrap" style="width:52px">基准率</span>` +
      `<span class="grow fs13"><b class="mono t1">${pct(base.value)}</b> ${esc(base.basis || '')}` +
      (base.source ? `<div class="t4 fs11 mt4">└ 依据：${esc(base.source)}</div>` : '') +
      `</span></div>`);

    adjs.forEach(a => {
      const d = Number(a.delta || 0);
      const cls = d >= 0 ? 'strength-strong' : 'strength-weak';
      rows.push(`<div class="row mt8" style="align-items:flex-start;gap:14px">` +
        `<span class="t4 fs12 nowrap" style="width:52px">调整项</span>` +
        `<span class="grow fs13"><b class="mono ${cls}">${d >= 0 ? '+' : ''}${Math.round(d * 100)}%</b> ` +
        `${esc(a.reason || '')}</span></div>`);
    });

    const w = Math.round((p || 0) * 100);
    const lo = iv[0] != null ? Math.round(iv[0] * 100) : w;
    const hi = iv[1] != null ? Math.round(iv[1] * 100) : w;
    rows.push(`<div class="row mt12" style="align-items:flex-start;gap:14px">` +
      `<span class="t4 fs12 nowrap" style="width:52px">最终</span>` +
      `<span class="grow">` +
      `<span class="fs20 mono t1">${w}%</span>` +
      `<span class="t4 fs12"> 区间 ${lo}–${hi}%</span>` +
      `<div style="position:relative;height:6px;background:var(--s2);border-radius:3px;margin-top:8px">` +
      `<i style="position:absolute;left:${lo}%;width:${Math.max(2, hi - lo)}%;top:0;bottom:0;` +
      `background:rgba(91,156,248,.32);border-radius:3px"></i>` +
      `<i style="position:absolute;left:calc(${w}% - 1px);width:2px;top:-2px;bottom:-2px;background:var(--accent)"></i>` +
      `</div></span></div>`);

    return `<div class="inset">${rows.join('')}` +
      `<div class="t4 fs11 mt12">这个数字是算出来的，不是估出来的：基准率打底，每项调整都写明理由。</div></div>`;
  }

  /** 质检门禁条。返工过就展示前后对比——这是编排真实发生的最强证据。 */
  function gateBar(r) {
    const q = r.quality || {};
    const hist = r.audit_history || [];
    const pass = q.verdict === 'pass';
    const last = hist[hist.length - 1] || {};
    const scores = q.scores || {};

    let inner = `<div class="gate ${pass ? 'pass' : 'rework'}">` +
      `<span class="tag ${pass ? 'tag-ok' : 'tag-warn'}">${pass ? '质检通过' : '质检未达标'}</span>` +
      `<span class="grow t2">${esc(q.headline || '')}</span>` +
      `<span class="t4 fs12 nowrap">${(q.rounds || 0)} 轮返工</span></div>`;

    const dims = [
      ['evidence_sufficiency', '证据充分性'], ['dimension_completeness', '维度完整性'],
      ['conclusion_confidence', '结论置信度'], ['structure_integrity', '结构完整度'],
      ['cross_validation', '交叉验证'],
    ];
    inner += `<div class="grid grid-4 mt12" style="gap:10px">` +
      dims.map(([k, n]) => {
        const v = scores[k] || 0;
        const cls = v >= 75 ? 'var(--ok)' : v >= 60 ? 'var(--accent)' : 'var(--warn)';
        return `<div class="inset" style="padding:9px 11px">` +
          `<div class="t4 fs11">${n}</div>` +
          `<div class="row" style="gap:8px;margin-top:5px">` +
          `<b class="mono t1" style="font-size:15px">${v}</b>` +
          `<span class="bar grow"><i style="width:${Math.min(100, v)}%;background:${cls}"></i></span>` +
          `</div></div>`;
      }).join('') + `</div>`;

    // 返工前后对比
    const reworked = hist.filter(h => h.before);
    if (reworked.length) {
      inner += reworked.map((h, i) => {
        const d = h.delta || {}, b = h.before || {};
        const arrow = (v, better) => {
          const good = better === 'up' ? v > 0 : v < 0;
          const cls = v === 0 ? 't4' : (good ? 'strength-strong' : 'strength-weak');
          return `<b class="mono ${cls}">${v > 0 ? '+' : ''}${v}</b>`;
        };
        return `<div class="inset mt12">` +
          `<div class="t4 fs11 mb8">第 ${i + 1} 轮返工前后对比</div>` +
          `<div class="fs12 t3" style="line-height:1.9">` +
          `<div>${esc(b.headline || '')}</div>` +
          `<div class="row wrapflex mt4" style="gap:14px">` +
          `<span>证据数 ${b.evidence_count} → ${b.evidence_count + (d.evidence_count || 0)} ${arrow(d.evidence_count || 0, 'up')}</span>` +
          `<span>独立来源 ${b.independent_domains} → ${b.independent_domains + (d.independent_domains || 0)} ${arrow(d.independent_domains || 0, 'up')}</span>` +
          `<span>无证据论点 ${b.unsupported_claims} → ${b.unsupported_claims + (d.unsupported_claims || 0)} ${arrow(d.unsupported_claims || 0, 'down')}</span>` +
          `</div></div></div>`;
      }).join('');
    }

    const issues = (q.issues || []).slice(0, 5);
    if (issues.length) {
      inner += `<details class="mt12"><summary class="t4 fs12 click">质检记录的 ${q.issues.length} 条问题</summary>` +
        `<div class="mt8">` + issues.map(i =>
          `<div class="fs12 t3" style="padding:5px 0;border-bottom:1px solid var(--line-soft)">` +
          `<span class="tag ${i.severity === 'high' ? 'tag-bad' : 'tag-warn'}">${esc(i.severity)}</span> ` +
          `<span class="mono t4">${esc(i.target)}</span> ${esc(i.reason)}</div>`).join('') +
        `</div></details>`;
    }
    return `<section class="card">${inner}</section>`;
  }

  function claimsSection(r, evMap, reviews) {
    const claims = r.claims || [];
    if (!claims.length) return '';
    const bySection = {};
    claims.forEach(c => { (bySection[c.section || '未分组'] = bySection[c.section || '未分组'] || []).push(c); });

    const body = Object.keys(bySection).map(sec => {
      const rows = bySection[sec].map(c => {
        const chips = (c.evidence_ids || []).map(id => chipHTML(evMap[id])).join('');
        const counter = (c.counter_evidence_ids || []).map(id => chipHTML(evMap[id])).join('');
        const rv = reviews[c.claim_id];
        const rvTag = rv ? `<span class="tag ${rv.verdict === 'confirmed' ? 'tag-ok' :
          rv.verdict === 'rejected' ? 'tag-bad' : 'tag-warn'}">` +
          `${rv.verdict === 'confirmed' ? '已核实' : rv.verdict === 'rejected' ? '已驳回' : '存疑'}</span>` : '';
        return `<div class="inset mt8" data-claim="${esc(c.claim_id)}">` +
          `<div class="row-between" style="align-items:flex-start;gap:12px">` +
          `<div class="grow fs13 t2">${esc(c.text)}</div>` +
          `<span class="strength ${STRENGTH_CLASS[c.strength] || ''}" title="强度由独立来源数判定，非模型自评">` +
          `<span class="glyph">${esc(c.strength_glyph || '')}</span>${esc(c.strength_label || '')}</span>` +
          `</div>` +
          `<div class="row wrapflex mt8" style="gap:6px">` +
          (chips || `<span class="tag tag-bad">未绑定证据</span>`) +
          (counter ? `<span class="t4 fs11" style="margin-left:6px">反向：</span>${counter}` : '') +
          (c.independent_domains ? `<span class="t4 fs11" style="margin-left:auto">${c.independent_domains} 个独立域名</span>` : '') +
          `</div>` +
          `<div class="row mt8" style="gap:5px">${rvTag}` +
          `<span class="spacer"></span>` +
          `<button class="btn btn-sm btn-ghost" data-review="confirmed" data-cid="${esc(c.claim_id)}">已核实</button>` +
          `<button class="btn btn-sm btn-ghost" data-review="doubted" data-cid="${esc(c.claim_id)}">存疑</button>` +
          `<button class="btn btn-sm btn-ghost" data-review="rejected" data-cid="${esc(c.claim_id)}">驳回</button>` +
          `</div></div>`;
      }).join('');
      return `<div class="mt16"><div class="t4 fs12">${esc(sec)}</div>${rows}</div>`;
    }).join('');

    const unsupported = claims.filter(c => !(c.evidence_ids || []).length).length;
    return `<section class="card"><div class="card-title row-between">` +
      `<span>论点与证据绑定</span>` +
      `<span class="t4" style="text-transform:none;letter-spacing:0">` +
      `${claims.length} 条论点 · ${unsupported} 条未绑定</span></div>` +
      body + `</section>`;
  }

  function redteamSection(r) {
    const rt = r.redteam || [], mi = r.minority || [];
    if (!rt.length && !mi.length) return '';
    let inner = `<div class="card-title">反方视角</div>`;
    if (rt.length) {
      inner += `<div class="t4 fs12 mb8">红队官的职责是挑毛病。一致同意也可能一致地错。</div>`;
      inner += rt.map((x, i) =>
        `<div class="inset mt8" style="border-left:2px solid var(--bad)">` +
        `<span class="mono t4 fs11">R${i + 1}</span> <span class="fs13">${esc(x)}</span></div>`).join('');
    }
    if (mi.length) {
      inner += `<div class="t4 fs12 mt16 mb8">少数派异议（被多数否决，但留痕）</div>`;
      inner += mi.map(x =>
        `<div class="inset mt8" style="border-left:2px solid var(--purple)">` +
        `<span class="fs13">${esc(x)}</span></div>`).join('');
    }
    return `<section class="card">${inner}</section>`;
  }

  function tensionsSection(r, evMap) {
    const ts = r.tensions || [];
    if (!ts.length) return '';
    const body = ts.map(t => {
      const side = (s, color) =>
        `<div class="inset" style="border-top:2px solid ${color}">` +
        `<div class="t1 fs13 mb8">${esc(s.stance || '')}</div>` +
        (s.quote ? `<div class="t3 fs12" style="border-left:2px solid var(--line-strong);padding-left:9px">「${esc(s.quote)}」</div>` : '') +
        `<div class="row wrapflex mt8" style="gap:5px">${(s.evidence_ids || []).map(id => chipHTML(evMap[id])).join('')}</div>` +
        `</div>`;
      return `<div class="mt12">` +
        `<div class="row" style="gap:8px"><span class="tag tag-purple">未解</span>` +
        `<b class="t1 fs13">${esc(t.topic)}</b></div>` +
        `<div class="grid grid-2 mt8">${side(t.side_a || {}, 'var(--ok)')}${side(t.side_b || {}, 'var(--bad)')}</div>` +
        (t.summary ? `<div class="t3 fs12 mt8">分歧要害：${esc(t.summary)}</div>` : '') +
        `</div>`;
    }).join('');
    return `<section class="card"><div class="card-title">未解张力</div>` +
      `<div class="t4 fs12">两派证据都站得住却互相矛盾。我们不替你抹平——这里恰恰是需要你自己判断的地方。</div>` +
      body + `</section>`;
  }

  function evidenceSection(r) {
    const evs = (r.evidence || []).slice().sort((a, b) => (b.credibility || 0) - (a.credibility || 0));
    if (!evs.length) return '';
    const sourced = evs.filter(e => e.fetch_status === 'sourced');
    const domains = new Set(sourced.map(e => e.domain).filter(Boolean));
    const rows = evs.map(e => {
      const cred = e.credibility || 0;
      const cls = cred >= 75 ? 'tag-ok' : cred >= 55 ? 'tag-accent' : 'tag-warn';
      return `<tr data-evrow="${esc(e.ev_id)}">` +
        `<td style="width:34%"><div class="t2">${esc((e.title || '无标题').slice(0, 60))}</div>` +
        `<div class="t4 fs11 mono mt4">${esc(e.domain || '本地')}</div></td>` +
        `<td class="t3 fs12">${esc((e.excerpt || '').slice(0, 150))}</td>` +
        `<td class="nowrap"><span class="tag">${esc(e.source_label || '')}</span></td>` +
        `<td class="nowrap t4 fs11">${esc(e.published_at || '未标注')}</td>` +
        `<td class="num"><span class="tag ${cls}">${cred}</span></td>` +
        `<td class="nowrap">` + (e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">原文 →</a>`
          : `<span class="t4 fs11">${esc(e.ground_label || '')}</span>`) + `</td></tr>`;
    }).join('');
    return `<section class="card"><div class="card-title row-between">` +
      `<span>证据表</span><span class="t4" style="text-transform:none;letter-spacing:0">` +
      `${sourced.length} 条已核验 · ${domains.size} 个独立域名</span></div>` +
      `<div style="overflow-x:auto"><table class="tbl"><thead><tr>` +
      `<th>来源</th><th>摘录</th><th>类型</th><th>发布</th><th class="num">可信度</th><th></th>` +
      `</tr></thead><tbody>${rows}</tbody></table></div>` +
      `<div class="t4 fs11 mt12">可信度为纯规则打分（来源类型基分 + 权威域名 / 时效 / 抓取完整度调整），不经模型评价，可复现。` +
      `<a href="./about">看打分规则 →</a></div></section>`;
  }

  function gapsSection(r) {
    const gs = r.gaps || [];
    if (!gs.length) return '';
    return `<section class="card"><div class="card-title">证据缺口</div>` +
      `<div class="t4 fs12 mb12">「没搜到」不等于「不存在」。下面是本次确实没拿到的东西，以及我们尝试过什么。</div>` +
      gs.map(g => `<div class="inset mt8">` +
        `<div class="row" style="gap:8px"><span class="tag tag-warn">${esc(g.kind_label || '缺口')}</span>` +
        `<b class="t2 fs13">${esc(g.topic || '')}</b></div>` +
        `<div class="t3 fs12 mt8">${esc(g.statement || '')}</div>` +
        ((g.queries_tried || []).length ?
          `<div class="row wrapflex mt8" style="gap:5px">` +
          g.queries_tried.map(q => `<span class="tag mono">${esc(q)}</span>`).join('') + `</div>` : '') +
        `</div>`).join('') +
      `</section>`;
  }

  function actionsSection(r) {
    const acts = r.actions || [], trg = r.triggers || [];
    if (!acts.length && !trg.length) return '';
    const kindTag = { do: ['tag-ok', '立即做'], verify: ['tag-accent', '去核实'], watch: ['tag-warn', '持续盯'] };
    let inner = `<div class="card-title">笃行 · 行动清单</div>`;
    if (acts.length) {
      inner += acts.map(a => {
        const [cls, label] = kindTag[a.kind] || ['tag', '建议'];
        return `<div class="row inset mt8" style="gap:10px;align-items:flex-start">` +
          `<span class="tag ${cls}">${label}</span>` +
          `<span class="grow fs13">${esc(a.text || '')}</span></div>`;
      }).join('');
    }
    if (trg.length) {
      inner += `<div class="t4 fs12 mt16 mb8">什么会让我改主意</div>`;
      inner += `<ul class="fs13 t3" style="margin:0;padding-left:18px">` +
        trg.map(t => `<li>${esc(t)}</li>`).join('') + `</ul>`;
    }
    return `<section class="card">${inner}</section>`;
  }

  function metricsStrip(r) {
    const m = r.metrics || {};
    const keys = ['efficiency', 'coverage', 'consistency', 'accuracy'];
    const cards = keys.filter(k => m[k]).map(k => {
      const x = m[k];
      const v = x.unit === '×' ? x.value + '×' : (x.value <= 1 ? pct(x.value) : x.value);
      return `<div class="card card-tight" title="${esc(x.formula)}｜${esc(x.why)}">` +
        `<div class="stat"><span class="v">${esc(v)}</span>` +
        `<span class="k">${esc(x.name)}</span>` +
        `<span class="d">${esc(x.detail || '')}</span></div></div>`;
    }).join('');
    if (!cards) return '';
    return `<div class="grid grid-4">${cards}</div>` +
      `<div class="t4 fs11 mt8">每个指标的口径都写在<a href="./about">方法论页</a>，包括为什么这么算。</div>`;
  }

  function expertStrip(r) {
    const es = r.experts || [];
    if (!es.length) return '';
    const plan = r.plan || {};
    return `<section class="card"><div class="card-title">本次编排</div>` +
      (plan.reason ? `<div class="t3 fs13 mb12">${esc(plan.reason)}</div>` : '') +
      `<div class="grid grid-3">` + es.map(e =>
        `<div class="inset"><div class="row-between">` +
        `<b class="t1 fs13">${esc(e.name)}</b>` +
        `<span class="tag">${esc(e.layer)}层</span></div>` +
        `<div class="t4 fs11 mt4">${esc((e.role || '').slice(0, 42))}</div>` +
        (e.finding ? `<div class="t3 fs12 mt8">「${esc(e.finding)}」</div>` : '') +
        `<div class="t4 fs11 mt8">调用 ${e.calls || 0} 次 · 产出证据 ${e.evidence || 0} 条</div>` +
        `</div>`).join('') + `</div></section>`;
  }

  function entityStrip(r) {
    const g = r.graph || {};
    const nodes = (g.nodes || []).slice(0, 14);
    if (!nodes.length) return '';
    const cliques = g.cliques || [];
    return `<section class="card"><div class="card-title row-between">` +
      `<span>关联实体</span>` +
      (r.id ? `<a class="t3" style="text-transform:none;letter-spacing:0" href="./graph/${esc(r.id)}">打开实体工作区 →</a>` : '') +
      `</div>` +
      `<div class="row wrapflex" style="gap:6px">` +
      nodes.map(n => `<span class="tag tag-accent click" data-entity="${esc(n.key)}" ` +
        `title="${esc(n.note || '')}｜${(n.evidence_ids || []).length} 条证据">` +
        `${esc(n.name)} <span class="t4">${(n.evidence_ids || []).length}</span></span>`).join('') +
      `</div>` +
      (cliques.length ? `<div class="inset mt12" style="border-left:2px solid var(--warn)">` +
        `<div class="t2 fs13 mb8">发现 ${cliques.length} 组一致性团伙</div>` +
        cliques.map(c => `<div class="t3 fs12">${esc(c.note)}（${esc(c.domains.join('、'))}）</div>`).join('') +
        `</div>` : '') +
      `</section>`;
  }

  function traceStrip(r) {
    const t = r.trace || {};
    const spans = t.spans || [];
    if (!spans.length && !r.taskId) return '';
    return `<section class="card"><div class="card-title row-between"><span>运行凭证</span>` +
      (r.id ? `<a class="t3" style="text-transform:none;letter-spacing:0" href="./trace/${esc(r.id)}">决策回放 →</a>` : '') +
      `</div>` +
      `<div class="grid grid-4">` +
      `<div><div class="t4 fs11">引擎</div><div class="t1 fs13 mt4">${esc(r.engine === 'infini' ? 'InfiniSynapse' : (r.engine || '—'))}</div></div>` +
      `<div><div class="t4 fs11">模型</div><div class="t1 fs13 mono mt4">${esc(r.model || '—')}</div></div>` +
      `<div><div class="t4 fs11">taskId</div><div class="t1 fs12 mono mt4 click" ` +
      `title="点击复制，可在 InfiniSynapse 后台查验" data-copy="${esc(r.taskId || '')}">${esc((r.taskId || '—').slice(0, 20))}</div></div>` +
      `<div><div class="t4 fs11">耗时 · 步数</div><div class="t1 fs13 mt4">${fmtDur(r.elapsed_ms)} · ${spans.length} 步</div></div>` +
      `</div></section>`;
  }

  /* -------------------------------------------------- 主渲染 */

  function render(container, r, opts) {
    opts = opts || {};
    const evMap = {};
    (r.evidence || []).forEach(e => { evMap[e.ev_id] = e; });
    const byRef = Object.assign({}, evMap);
    // 支持正文里的 E1/E2 写法
    const refMap = (r.confidence && r.confidence.ref_map) || r.ref_map || {};
    Object.keys(refMap).forEach(k => { byRef[k] = evMap[refMap[k]]; });
    (r.evidence || []).forEach((e, i) => { byRef['E' + (i + 1)] = byRef['E' + (i + 1)] || e; });

    const reviews = opts.reviews || {};
    const parts = [];

    parts.push(verdictCard(r));
    if (r.metrics) parts.push(metricsStrip(r));
    if (r.quality) parts.push(gateBar(r));
    parts.push(claimsSection(r, evMap, reviews));
    parts.push(tensionsSection(r, evMap));
    parts.push(redteamSection(r));

    const bodyHTML = injectChips(md(r.markdown || ''), byRef);
    parts.push(`<section class="card"><div class="card-title row-between"><span>完整报告</span>` +
      `<span class="t4 fs11" style="text-transform:none;letter-spacing:0">选中任意段落可追问深化</span></div>` +
      `<div class="md" id="mbBody">${bodyHTML}</div></section>`);

    (r.deepenings || []).forEach(d => {
      parts.push(`<section class="card" style="border-left:2px solid var(--purple)">` +
        `<div class="card-title">深化 · ${esc(d.section)}</div>` +
        `<div class="md">${injectChips(md(d.markdown), byRef)}</div></section>`);
    });

    parts.push(evidenceSection(r));
    parts.push(gapsSection(r));
    parts.push(actionsSection(r));
    parts.push(entityStrip(r));
    parts.push(expertStrip(r));
    parts.push(traceStrip(r));

    container.innerHTML = `<div class="col" style="gap:14px">${parts.filter(Boolean).join('')}</div>`;

    bindChips(container, evMap);
    bindReview(container, r);
    bindCopy(container);
    bindDeepen(container, r, opts);
    bindHighlight(container, r, evMap);
    return container;
  }

  function bindCopy(root) {
    global.MB.$$('[data-copy]', root).forEach(n => {
      n.addEventListener('click', () => { if (n.dataset.copy) copy(n.dataset.copy); });
    });
  }

  function bindReview(root, r) {
    global.MB.$$('[data-review]', root).forEach(btn => {
      btn.addEventListener('click', async () => {
        try {
          await postJSON('./api/review', {
            report_id: r.id, claim_id: btn.dataset.cid, verdict: btn.dataset.review,
          });
          const box = btn.closest('[data-claim]');
          const label = { confirmed: '已核实', doubted: '存疑', rejected: '已驳回' }[btn.dataset.review];
          const cls = { confirmed: 'tag-ok', doubted: 'tag-warn', rejected: 'tag-bad' }[btn.dataset.review];
          const strip = box.querySelector('.row.mt8:last-child');
          let tag = strip.querySelector('.tag');
          if (!tag) { tag = el('span', { class: 'tag' }); strip.prepend(tag); }
          tag.className = 'tag ' + cls; tag.textContent = label;
          toast('已记入复核队列');
        } catch (e) { toast('复核提交失败'); }
      });
    });
  }

  /** hover 论点 -> 正文里引用同一条证据的句子高亮，反向亦然。 */
  function bindHighlight(root, r, evMap) {
    const body = root.querySelector('#mbBody');
    if (!body) return;
    global.MB.$$('[data-claim]', root).forEach(card => {
      const text = (card.querySelector('.grow') || {}).textContent || '';
      const key = text.slice(0, 14).trim();
      if (key.length < 6) return;
      card.addEventListener('mouseenter', () => {
        global.MB.$$('p, li', body).forEach(p => {
          if (p.textContent.includes(key)) p.classList.add('lit-sentence');
        });
      });
      card.addEventListener('mouseleave', () => {
        global.MB.$$('.lit-sentence', body).forEach(p => p.classList.remove('lit-sentence'));
      });
    });
  }

  /** 划词深化：选中正文任意片段就浮出追问按钮。 */
  function bindDeepen(root, r, opts) {
    const body = root.querySelector('#mbBody');
    if (!body || !r.id) return;
    let bar = null;

    function hide() { if (bar) { bar.remove(); bar = null; } }

    body.addEventListener('mouseup', () => {
      setTimeout(() => {
        const sel = window.getSelection();
        const text = sel ? String(sel).trim() : '';
        hide();
        if (!text || text.length < 4 || text.length > 120) return;
        const rect = sel.getRangeAt(0).getBoundingClientRect();
        bar = el('div', {
          class: 'card card-tight',
          style: `position:fixed;z-index:180;left:${Math.min(rect.left, window.innerWidth - 210)}px;` +
            `top:${Math.max(8, rect.top - 46)}px;padding:6px 8px`,
        }, [
          el('button', {
            class: 'btn btn-sm btn-primary',
            onclick: async () => {
              const btn = bar.querySelector('button');
              btn.textContent = '深化中…'; btn.disabled = true;
              try {
                const res = await postJSON('./api/deepen', { report_id: r.id, section: text });
                hide();
                const sec = el('section', { class: 'card' });
                sec.style.borderLeft = '2px solid var(--purple)';
                sec.innerHTML = `<div class="card-title">深化 · ${esc(text.slice(0, 24))}</div>` +
                  `<div class="md">${md(res.markdown)}</div>`;
                root.firstElementChild.appendChild(sec);
                sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
                toast('深化结果已写回报告');
              } catch (e) { toast('深化失败：' + e.message); hide(); }
            },
          }, ['追问这一句']),
        ]);
        document.body.appendChild(bar);
      }, 10);
    });
    document.addEventListener('mousedown', e => {
      if (bar && !bar.contains(e.target)) hide();
    });
  }

  global.MBReport = { render, chipHTML, injectChips, confidenceLadder };
})(window);
