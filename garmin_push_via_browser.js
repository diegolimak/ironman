/**
 * garmin_push_via_browser.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Cole este script no DevTools Console do Chrome em https://connect.garmin.com
 * para enviar os treinos da semana ao calendário Garmin.
 *
 * Uso:
 *   1. Abra https://connect.garmin.com (logado)
 *   2. F12 → Console
 *   3. Cole tudo e pressione Enter
 *   4. Chame:  pushWeek(1)   ← número da semana (1 a 11)
 *
 * Os treinos aparecem no relógio após a próxima sincronização.
 */

(function() {
  const BASE = '/gc-api';

  // ── Semanas do plano ──────────────────────────────────────────────────────
  const WEEK_STARTS = [
    '2026-05-25','2026-06-01','2026-06-08','2026-06-15','2026-06-22',
    '2026-06-29','2026-07-06','2026-07-13','2026-07-20','2026-07-27','2026-08-03'
  ];

  const DOW_PT = { 'Seg':0,'Ter':1,'Qua':2,'Qui':3,'Sex':4,'Sáb':5,'Dom':6 };

  const SPORT = {
    run:      { sportTypeId: 1,  sportTypeKey: 'running'          },
    swim:     { sportTypeId: 4,  sportTypeKey: 'swimming'         },  // 4 = swimming (NÃO 5!)
    bike:     { sportTypeId: 2,  sportTypeKey: 'cycling'          },
    brick:    { sportTypeId: 15, sportTypeKey: 'multi_sport'      },
    strength: { sportTypeId: 5,  sportTypeKey: 'strength_training' }, // 5 = strength (NÃO swim!)
  };

  const HR_ZONE = { 'Z1':1,'Z1/Z2':2,'Z2':2,'Z2/Z3':3,'Z3':3,'Z3/Z4':4,'Z4':4,'REST':0 };

  // ── Duração em segundos ───────────────────────────────────────────────────
  function parseDur(s) {
    s = s.toLowerCase().replace(/\s/g,'').replace('—','0');
    let t = 0;
    if (s.includes('h')) {
      const p = s.split('h');
      t += parseInt(p[0]||0) * 3600;
      t += parseInt((p[1]||'0').replace(/[^0-9]/g,'') || 0) * 60;
    } else {
      t = parseInt(s.replace(/[^0-9]/g,'') || 0) * 60;
    }
    return Math.max(t, 1800);
  }

  // ── Data a partir do dia da semana ────────────────────────────────────────
  function toDate(weekIdx, dowStr) {
    const prefix = dowStr.split(' ')[0];
    const offset = DOW_PT[prefix] ?? 0;
    const base = new Date(WEEK_STARTS[weekIdx] + 'T12:00:00');
    base.setDate(base.getDate() + offset);
    return base.toISOString().slice(0,10);
  }

  // ── Payload do treino ─────────────────────────────────────────────────────
  function buildPayload(day, weekIdx) {
    const sp = SPORT[day.disc];
    if (!sp) return null;
    const dur = parseDur(day.dur);
    const hrZ = HR_ZONE[day.zone] ?? 2;
    const date = toDate(weekIdx, day.dow);
    return {
      payload: {
        workoutName: `[IRONMAN] ${day.name.substring(0,50)}`,
        description: `S${weekIdx+1} IRONMAN 70.3 Rio | ${day.zone} | ${day.dur}`,
        sportType: sp,
        workoutSegments: [{
          segmentOrder: 1,
          sportType: sp,
          workoutSteps: [
            { type:'ExecutableStepDTO', stepOrder:1, stepType:{stepTypeId:1,stepTypeKey:'warmup'},
              endCondition:{conditionTypeId:2,conditionTypeKey:'time'}, endConditionValue:600,
              description:'Aquecimento' },
            { type:'ExecutableStepDTO', stepOrder:2, stepType:{stepTypeId:3,stepTypeKey:'interval'},
              endCondition:{conditionTypeId:2,conditionTypeKey:'time'}, endConditionValue:dur,
              targetType:{workoutTargetTypeId:4,workoutTargetTypeKey:'heart.rate.zone'},
              targetValueOne:hrZ, targetValueTwo:hrZ, description:day.name.substring(0,100) },
            { type:'ExecutableStepDTO', stepOrder:3, stepType:{stepTypeId:2,stepTypeKey:'cooldown'},
              endCondition:{conditionTypeId:2,conditionTypeKey:'time'}, endConditionValue:300,
              description:'Resfriamento' }
          ]
        }],
        estimatedDurationInSecs: dur + 900
      },
      date
    };
  }

  // ── API helpers ───────────────────────────────────────────────────────────
  function csrf() {
    return document.querySelector('meta[name="csrf-token"]')?.content;
  }

  async function apiPost(path, body) {
    const r = await fetch(BASE + path, {
      method: 'POST', credentials: 'include',
      headers: { 'connect-csrf-token': csrf(), 'Content-Type':'application/json',
                 'NK':'NT', 'Accept':'application/json' },
      body: body !== undefined ? JSON.stringify(body) : undefined
    });
    const text = await r.text();
    const json = text ? JSON.parse(text) : {};
    if (!r.ok) throw new Error(`HTTP ${r.status}: ${text.substring(0,200)}`);
    return json;
  }

  // ── Lê os treinos do WEEKS global no dashboard OU aceita lista manual ─────
  function getDays(weekIdx) {
    if (typeof WEEKS !== 'undefined' && WEEKS[weekIdx]) {
      return WEEKS[weekIdx].days;
    }
    throw new Error('WEEKS não encontrado. Cole este script na página do dashboard.');
  }

  // ── Push principal ────────────────────────────────────────────────────────
  window.pushWeek = async function(weekNum, daysFilter) {
    const weekIdx = weekNum - 1;
    const start = WEEK_STARTS[weekIdx];
    if (!start) { console.error('Semana inválida (1–11)'); return; }

    let days;
    try { days = getDays(weekIdx); }
    catch(e) { console.error(e.message); return; }

    // Opcional: filtrar dias específicos (ex: [5,6,7,8] para Qui-Dom)
    const filtered = daysFilter
      ? days.filter((_,i) => daysFilter.includes(i+1))
      : days;

    console.log(`\n🏊‍♂️🚴🏃 Enviando S${weekNum} (${start}) — ${filtered.length} dias`);

    const results = [];
    for (const day of filtered) {
      if (day.disc === 'rest') { console.log(`  ⏭  ${day.dow} — descanso, pulando`); continue; }
      const built = buildPayload(day, weekIdx);
      if (!built) { console.log(`  ⚠  ${day.dow} — sport desconhecido: ${day.disc}`); continue; }

      try {
        const wo = await apiPost('/workout-service/workout', built.payload);
        const sc = await apiPost(`/workout-service/schedule/${wo.workoutId}`, { date: built.date });
        const msg = `  ✅ [${built.date}] ${day.name.substring(0,45)} (id:${wo.workoutId})`;
        console.log(msg);
        results.push({ date: built.date, name: day.name, workoutId: wo.workoutId, scheduleId: sc.workoutScheduleId, ok: true });
      } catch(e) {
        console.error(`  ❌ [${day.dow}] ${day.name.substring(0,40)}: ${e.message}`);
        results.push({ date: toDate(weekIdx, day.dow), name: day.name, ok: false, error: e.message });
      }

      await new Promise(r => setTimeout(r, 800)); // rate limiting
    }

    const ok = results.filter(r=>r.ok).length;
    console.log(`\n📱 ${ok}/${results.length} treinos enviados. Sincronize o relógio!`);
    return results;
  };

  console.log('✅ garmin_push_via_browser.js carregado!');
  console.log('   Uso: pushWeek(2)         ← envia semana 2 inteira');
  console.log('   Uso: pushWeek(2, [5,6,7,8]) ← envia apenas Qui-Dom da S2');
})();
