/* ============================================================
   明辨 MINGBIAN · 实时研判工作台
   左 DAG 七节点 / 中 思维流与流式正文 / 右 证据流与信号
   纪律：新数据进侧栏，绝不顶走用户正在读的段落。
   ============================================================ */
(function (global) {
  'use strict';
  const { $, el, esc, md, toast, fmtDur } = global.MB;

  const STEP_OF_NODE = {
    intake: 'intake', plan: 'plan', boxue: 'collect', shenwen: 'verify',
    shensi: 'analyze', audit: 'audit', mingbian: 'debate', duxing: 'deliver',
  };

  // 这些后缀本身不是可注册域名，得多往前吃一段
  const MULTI_SUFFIX = ['com.cn', 'net.cn', 'org.cn', 'gov.cn', 'edu.cn', 'ac.cn',
                        'co.uk', 'co.jp', 'com.hk', 'com.tw'];

  /** 归一到主域再计数。m.163.com 和 c.m.163.com 是同一家，算两个来源就是自欺欺人。
   *  与后端 models.root_domain 保持同一套规则。 */
  function rootDomain(host) {
    host = String(host || '').toLowerCase().replace(/^https?:\/\//, '').split('/')[0].replace(/^\.|\.$/g, '');
    const parts = host.split('.');
    if (parts.length <= 2) return host;
    const tail2 = parts.slice(-2).join('.');
    return MULTI_SUFFIX.indexOf(tail2) >= 0 ? parts.slice(-3).join('.') : tail2;
  }

  /** 「预计 300 秒」读起来要在脑子里除一次 60，直接给分钟。 */
  function etaText(sec) {
    sec = Number(sec) || 0;
    return sec >= 120 ? `${Math.round(sec / 60)} 分钟` : `${sec} 秒`;
  }

  const KIND_META = {
    plan: ['tag-accent', '拆解'], dispatch: ['tag-purple', '派遣'],
    action: ['tag', '动作'], finding: ['tag-ok', '发现'],
    reflect: ['tag-warn', '反思'],
  };

  function Live(opts) {
    this.host = opts.host;
    this.onDone = opts.onDone || function () { };
    this.nodes = [];
    this.startAt = 0;
    this.timer = null;
    this.evidenceCount = 0;
    this.build();
  }

  Live.prototype.build = function () {
    this.host.innerHTML =
      `<div class="live-grid">
         <aside class="live-col" id="lvLeft">
           <div class="card card-tight">
             <div class="card-title">流水线</div>
             <div id="lvDag" class="col" style="gap:2px"></div>
           </div>
           <div class="card card-tight" id="lvPlanCard" style="display:none">
             <div class="card-title">研判计划</div>
             <div id="lvPlan"></div>
           </div>
           <div class="card card-tight" id="lvTeamCard" style="display:none">
             <div class="card-title">专家团</div>
             <div id="lvTeam" class="col" style="gap:5px"></div>
           </div>
           <div class="card card-tight" id="lvTrajCard" style="display:none">
             <div class="card-title">立场轨迹</div>
             <div id="lvTraj" class="traj"></div>
           </div>
         </aside>

         <main class="live-col">
           <div class="card card-tight">
             <div class="row-between">
               <div class="row" style="gap:9px">
                 <span class="tag tag-accent" id="lvStatus">准备中</span>
                 <span class="t4 fs12" id="lvStatusMsg">正在连接引擎…</span>
               </div>
               <span class="t4 fs12 mono" id="lvClock">0.0s</span>
             </div>
             <div class="bar mt8"><i id="lvBar" style="width:4%"></i></div>
             <div class="row wrapflex mt8" style="gap:12px" id="lvCounters">
               <span class="t4 fs11">证据 <b class="mono t2" id="lvEv">0</b></span>
               <span class="t4 fs11">独立域名 <b class="mono t2" id="lvDom">0</b></span>
               <span class="t4 fs11">步数 <b class="mono t2" id="lvSteps">0</b></span>
               <span class="t4 fs11" id="lvTask"></span>
             </div>
           </div>

           <div class="card" id="lvGateCard" style="display:none"></div>
          <div class="card" id="lvDebateCard" style="display:none"></div>

           <div class="card">
             <div class="card-title">思维流</div>
             <div id="lvStream" class="stream"></div>
           </div>

           <div class="card" id="lvTextCard" style="display:none">
             <div class="card-title row-between"><span>报告成文中</span>
               <span class="t4 fs11" style="text-transform:none;letter-spacing:0">流式输出</span></div>
             <div class="md" id="lvText"></div>
           </div>
         </main>

         <aside class="live-col" id="lvRight">
           <div class="card card-tight">
             <div class="card-title">行情信号</div>
             <div id="lvSignals" class="col" style="gap:5px">
               <div class="t4 fs12">等待取证计划…</div>
             </div>
           </div>
           <div class="card card-tight">
             <div class="card-title row-between"><span>证据流</span>
               <span class="t4" id="lvEvCount" style="text-transform:none;letter-spacing:0">0</span></div>
             <div id="lvEvidence" class="col" style="gap:6px">
               <div class="t4 fs12">尚未取到证据。</div>
             </div>
           </div>
         </aside>
       </div>`;
  };

  Live.prototype.start = function (question, mode) {
    const self = this;
    this.startAt = Date.now();
    this.evidence = [];
    this.domains = new Set();
    this.steps = 0;
    this.text = '';
    this.report = null;

    this.timer = setInterval(() => {
      const s = (Date.now() - self.startAt) / 1000;
      // 跑到几百秒时「412.3s」不好读，过 90 秒就切成分秒
      $('#lvClock').textContent = s < 90 ? s.toFixed(1) + 's'
        : Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0');
    }, 100);

    fetch('./api/analyze', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, mode }),
    }).then(res => {
      if (!res.ok || !res.body) throw new Error('HTTP ' + res.status);
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      (function pump() {
        reader.read().then(({ done, value }) => {
          if (done) { self.finish(); return; }
          buf += dec.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            const block = buf.slice(0, idx); buf = buf.slice(idx + 2);
            const line = block.split('\n').find(l => l.startsWith('data:'));
            if (!line) continue;
            try { self.handle(JSON.parse(line.slice(5).trim())); } catch (e) { }
          }
          pump();
        }).catch(err => { self.fail(err); });
      })();
    }).catch(err => self.fail(err));
  };

  Live.prototype.fail = function (err) {
    clearInterval(this.timer);
    $('#lvStatus').className = 'tag tag-bad';
    $('#lvStatus').textContent = '中断';
    $('#lvStatusMsg').textContent = String(err && err.message || err);
    this.onDone(null);
  };

  Live.prototype.finish = function () {
    clearInterval(this.timer);
    if (this.report) {
      $('#lvStatus').className = 'tag tag-ok';
      $('#lvStatus').textContent = '完成';
      $('#lvStatusMsg').textContent = '研判结束，报告已生成';
      $('#lvBar').style.width = '100%';
      this.onDone(this.report);
    }
  };

  Live.prototype.handle = function (msg) {
    const t = msg.event, d = msg.data || {};
    const fn = this['on_' + t];
    if (fn) fn.call(this, d);
  };

  /* ---------------- 各事件 ---------------- */

  Live.prototype.on_dag = function (d) {
    this.nodes = d.nodes || [];
    $('#lvDag').innerHTML = this.nodes.map(n =>
      `<div class="dagnode" data-node="${esc(n.key)}">` +
      `<span class="dot"></span><span class="grow">${esc(n.cn)}</span>` +
      `<span class="t4 fs11 nd-time"></span></div>`).join('');
    if (d.mode) {
      $('#lvStatusMsg').textContent = d.mode.desc;
    }
  };

  Live.prototype.on_run = function (d) {
    $('#lvTask').innerHTML = `run <b class="mono t3">${esc((d.run_id || '').slice(0, 8))}</b>`;
  };

  Live.prototype.on_status = function (d) {
    const step = d.step;
    $('#lvStatusMsg').textContent = d.message || '';
    const idx = this.nodes.findIndex(n => STEP_OF_NODE[n.key] === step);
    this.nodes.forEach((n, i) => {
      const node = $(`[data-node="${n.key}"]`);
      if (!node) return;
      node.classList.toggle('on', i === idx);
      node.classList.toggle('done', idx >= 0 && i < idx);
      if (i === idx && !node.dataset.t0) {
        node.dataset.t0 = Date.now();
      }
      if (idx >= 0 && i < idx && node.dataset.t0 && !node.dataset.tdone) {
        node.dataset.tdone = '1';
        const secs = ((Date.now() - Number(node.dataset.t0)) / 1000).toFixed(1);
        node.querySelector('.nd-time').textContent = secs + 's';
      }
    });
    const pctMap = { intake: 8, plan: 14, collect: 32, verify: 44, analyze: 68,
      audit: 80, debate: 89, deliver: 96 };
    $('#lvBar').style.width = (pctMap[step] || 10) + '%';
    const label = { intake: '拆解', plan: '计划', collect: '博学', verify: '审问',
      analyze: '慎思', audit: '质检', debate: '明辨', deliver: '笃行' }[step] || '运行';
    $('#lvStatus').textContent = label;
  };

  Live.prototype.on_plan = function (d) {
    $('#lvPlanCard').style.display = '';
    $('#lvPlan').innerHTML =
      `<div class="t3 fs12 mb8">${esc(d.reason || '')}</div>` +
      `<div class="t4 fs11 mb8">拆出 ${(d.sub_questions || []).length} 个子问题 · 预计 ${etaText(d.eta)}</div>` +
      `<ol class="fs12 t3" style="margin:0;padding-left:16px">` +
      (d.sub_questions || []).map(s => `<li>${esc(s)}</li>`).join('') + `</ol>`;
  };

  Live.prototype.on_experts = function (d) {
    if (!d.roster) return;
    $('#lvTeamCard').style.display = '';
    $('#lvTeam').innerHTML = d.roster.map(e =>
      `<div class="expert-row" data-expert="${esc(e.key)}">` +
      `<span class="dot"></span>` +
      `<span class="grow"><b class="t2 fs12">${esc(e.name)}</b>` +
      `<span class="t4 fs11"> · ${esc(e.layer)}</span></span></div>`).join('');
  };

  Live.prototype.on_thought = function (d) {
    this.steps++;
    $('#lvSteps').textContent = this.steps;
    const [cls, label] = KIND_META[d.kind] || ['tag', '记录'];
    const row = el('div', { class: 'thought' });
    row.innerHTML = `<span class="tag ${cls}">${label}</span>` +
      `<span class="grow fs13 t2">${esc(d.text)}</span>` +
      `<span class="t4 fs11 mono nowrap">${((Date.now() - this.startAt) / 1000).toFixed(1)}s</span>`;
    const stream = $('#lvStream');
    stream.appendChild(row);
    stream.scrollTop = stream.scrollHeight;
    if (d.expert) {
      const er = $(`[data-expert="${d.expert}"]`);
      if (er) { er.classList.add('active'); setTimeout(() => er.classList.remove('active'), 1600); }
    }
  };

  Live.prototype.on_fetch_plan = function (d) {
    $('#lvSignals').innerHTML = (d.sources || []).map(s =>
      `<div class="signal" data-sig="${esc(s.key)}">` +
      `<span class="grow fs12 t3">${esc(s.label)}</span>` +
      `<span class="skel" style="width:52px;height:9px;margin:0"></span></div>`).join('')
      || `<div class="t4 fs12">本次无需行情取证。</div>`;
  };

  Live.prototype.on_signal = function (d) {
    const row = $(`[data-sig="${d.key}"]`);
    const html = d.status === 'ok'
      ? `<span class="grow fs12 t3">${esc(d.label)}</span>` +
        `<span class="mono fs12 t1">${esc(d.value || '')}</span>` +
        (typeof d.chg === 'number'
          ? `<span class="mono fs11 ${d.chg >= 0 ? 'strength-strong' : 'strength-weak'}">${d.chg >= 0 ? '+' : ''}${d.chg.toFixed(2)}%</span>`
          : '')
      : `<span class="grow fs12 t4">${esc(d.label)}</span><span class="tag tag-warn">取证失败</span>`;
    if (row) row.innerHTML = html;
    else {
      const n = el('div', { class: 'signal' }); n.innerHTML = html;
      $('#lvSignals').appendChild(n);
    }
  };

  Live.prototype.on_evidence = function (d) {
    const e = d.item || {};
    this.evidence.push(e);
    if (e.domain) this.domains.add(rootDomain(e.domain));
    $('#lvEv').textContent = this.evidence.length;
    $('#lvDom').textContent = this.domains.size;
    $('#lvEvCount').textContent = this.evidence.length;
    const box = $('#lvEvidence');
    if (this.evidence.length === 1) box.innerHTML = '';
    const cred = e.credibility || 0;
    const cls = cred >= 75 ? 'tag-ok' : cred >= 55 ? 'tag-accent' : 'tag-warn';
    const card = el('div', { class: 'evcard' });
    card.innerHTML = `<div class="row-between" style="gap:8px">` +
      `<span class="mono fs11 t3">${esc(e.domain || '本地')}</span>` +
      `<span class="tag ${cls}">${cred}</span></div>` +
      `<div class="fs12 t2 mt4">${esc((e.title || '').slice(0, 46))}</div>`;
    card.addEventListener('mouseenter', () => global.MB.showProv(card, e));
    card.addEventListener('mouseleave', () => global.MB.hideProv());
    box.prepend(card);
  };

  Live.prototype.on_fetch_summary = function (d) {
    $('#lvEv').textContent = d.evidence || 0;
    $('#lvDom').textContent = d.domains || 0;
  };

  Live.prototype.on_credibility = function (d) {
    // 平均可信度并入计数条
    const c = $('#lvCounters');
    if (c && !$('#lvCred')) {
      c.appendChild(el('span', { class: 't4 fs11', id: 'lvCred',
        html: `平均可信度 <b class="mono t2">${d.avg}</b>` }));
    }
  };

  Live.prototype.on_task = function (d) {
    if (!d.taskId) return;
    $('#lvTask').innerHTML = `taskId <b class="mono t3" title="可在 InfiniSynapse 后台查验">` +
      `${esc(d.taskId.slice(0, 14))}</b> · ${esc(d.model || '')}`;
  };

  Live.prototype.on_text = function (d) {
    this.text = d.markdown || '';
    $('#lvTextCard').style.display = '';
    $('#lvText').innerHTML = md(this.text) + '<span class="caret"></span>';
  };

  Live.prototype.on_gate = function (d) {
    const card = $('#lvGateCard');
    card.style.display = '';
    const notes = d.verdict === 'pass_with_notes';
    const pass = d.verdict === 'pass' || notes;
    card.innerHTML = `<div class="gate ${pass ? 'pass' : 'rework'}">` +
      `<span class="tag ${notes ? 'tag-accent' : pass ? 'tag-ok' : 'tag-warn'}">` +
      `${notes ? '带保留通过' : pass ? '质检通过' : '质检未通过'}</span>` +
      `<span class="grow t2 fs13">${esc(d.headline || '')}</span></div>`;
  };

  Live.prototype.on_rework = function (d) {
    const card = $('#lvGateCard');
    card.style.display = '';
    card.innerHTML += `<div class="inset mt8" style="border-left:2px solid var(--warn)">` +
      `<div class="t2 fs13">第 ${d.round} 轮返工 → 打回${esc(d.to_cn)}</div>` +
      `<ul class="t3 fs12" style="margin:6px 0 0;padding-left:16px">` +
      (d.issues || []).slice(0, 4).map(i => `<li>${esc(i.reason || '')}</li>`).join('') +
      `</ul></div>`;
    toast(`第 ${d.round} 轮返工：打回${d.to_cn}`);
  };

  Live.prototype.on_degraded = function () {
    // 故意空实现：备用通道是内部细节，不在工作台里点名。
  };

  /** 立场轨迹：每打一个点就长一行，让「结论怎么变成现在这样」是看着发生的。 */
  Live.prototype.on_stance = function (d) {
    $('#lvTrajCard').style.display = '';
    const meta = {
      init: ['', '起点'], ground: ['ground', '落地'], firm: ['firm', '加固'],
      soften: ['soften', '削弱'], reverse: ['reverse', '掉头'], hold: ['', '维持'],
    }[d.shift_kind] || ['', ''];
    const row = el('div', { class: 'traj-row' });
    row.innerHTML = `<div class="traj-dot ${meta[0]}"></div><div class="grow">` +
      `<div class="row" style="gap:6px;align-items:baseline">` +
      `<b class="t2 fs12">${esc(d.stage_cn)}</b>` +
      `<span class="t4 fs11">${esc(meta[1])}</span><span class="spacer"></span>` +
      (d.probability != null
        ? `<b class="mono t1 fs12">${Math.round(d.probability * 100)}%</b>`
        : `<span class="t4 fs11">未表态</span>`) + `</div>` +
      `<div class="t4 fs11 mt4">${esc(d.shift)}</div></div>`;
    $('#lvTraj').appendChild(row);
    if (d.shift_kind === 'reverse') toast(`立场掉头：${d.stance}`);
  };

  /** 辩论门控。关掉的时候更要展示——「为什么这次不用辩」也是结论。 */
  Live.prototype.on_debate_gate = function (d) {
    const card = $('#lvDebateCard');
    card.style.display = '';
    const [cls, label] = {
      open: ['tag-warn', '开辩'], closed: ['tag-ok', '无需辩论'],
      no_budget: ['tag-accent', '有分歧 · 本档无额度'],
    }[d.state] || ['tag', d.state || ''];
    const sigs = (d.signals || []).map(s =>
      `<span class="tag ${s.hit ? 'tag-warn' : ''}" title="${esc(s.detail)}" ` +
      `${s.hit ? '' : 'style="opacity:.5"'}>${s.hit ? '✓ ' : ''}${esc(s.name)}</span>`).join('');
    card.innerHTML = `<div class="card-title row-between"><span>辩论门控</span>` +
      `<span class="tag ${cls}">${esc(label)}</span></div>` +
      `<div class="row wrapflex" style="gap:6px">${sigs}</div>` +
      `<div class="t3 fs12 mt8">${esc(d.reason)}</div>` +
      `<div class="t4 fs11 mt4">门控分 ${d.score} / 阈值 ${d.threshold}</div>` +
      `<div id="lvDebateRounds"></div>`;
  };

  Live.prototype.on_debate_round = function (d) {
    const host = $('#lvDebateRounds');
    if (!host) return;
    const jg = d.judgement || {}, at = d.attack || {};
    const box = el('div', { class: 'inset mt12' });
    box.innerHTML = `<div class="row" style="gap:7px">` +
      `<span class="tag tag-purple">第 ${d.round} 轮</span>` +
      `<b class="t2 fs12 grow">${esc(d.headline || '')}</b></div>` +
      (at.strongest ? `<div class="t3 fs12 mt8">最强攻击：${esc(at.strongest)}</div>` : '') +
      (jg.summary ? `<div class="t4 fs11 mt4">裁定：${esc(jg.summary)}</div>` : '');
    host.appendChild(box);
  };

  Live.prototype.on_clique = function (d) {
    (d.groups || []).forEach(g => {
      this.on_thought({ kind: 'reflect', text: g.note, expert: 'contra' });
    });
  };

  /** 引擎自己的联网检索。让人看见它在查什么，而不是干等一个转圈。
   *  同一条检索是逐字流式长出来的，按 id 原地改写，不要每个 token 追加一行。 */
  Live.prototype.on_engine_probe = function (d) {
    const stream = $('#lvStream');
    if (!stream) return;
    const key = 'probe-' + (d.id || 0);
    this.probes = this.probes || {};
    let row = this.probes[key];
    if (!row) {
      this.steps++;
      $('#lvSteps').textContent = this.steps;
      row = el('div', { class: 'thought' });
      row.innerHTML = `<span class="tag tag-accent">${d.kind === 'web_fetch' ? '引擎取页' : '引擎检索'}</span>` +
        `<span class="grow fs13 t2"></span>` +
        `<span class="t4 fs11 mono nowrap">${((Date.now() - this.startAt) / 1000).toFixed(1)}s</span>`;
      this.probes[key] = row;
      stream.appendChild(row);
    }
    row.querySelector('.grow').textContent = d.text || '';
    stream.scrollTop = stream.scrollHeight;
  };

  Live.prototype.on_report = function (d) { this.report = d; };

  Live.prototype.on_result = function (d) {
    this.resultMeta = d;
  };

  Live.prototype.on_done = function (d) {
    if (this.report) this.report.id = this.report.id || d.id;
  };

  Live.prototype.on_error = function (d) {
    this.fail(new Error(d.message || '未知错误'));
  };

  global.MBLive = Live;
})(window);
