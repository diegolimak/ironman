/**
 * garmin_push_via_browser.js  — v2.0
 * ─────────────────────────────────────────────────────────────────────────────
 * Cole este script no DevTools Console do Chrome em https://connect.garmin.com
 * para enviar os treinos da semana ao calendário Garmin.
 *
 * MELHORIAS v2.0:
 *  ✅ BRICK: 2 segmentos separados (bike + run), step orders globalmente únicos
 *  ✅ Blocos/Intervalos: "3x8min L3 c/ 4min L2" → RepeatGroupDTO real no Garmin
 *  ✅ Parser de detalhe inteligente: aquecimento / blocos / constante / resfriamento
 *  ✅ "Estímulos" / sprint / força em pé parseados como blocos também
 *
 * Uso:
 *   1. Abra https://connect.garmin.com (logado)
 *   2. F12 → Console
 *   3. Cole tudo e pressione Enter
 *   4. Chame:  pushWeek(2)           ← semana inteira
 *              pushWeek(2, [5,6,7])  ← só Qui-Sáb da S2
 */

(function() {
  'use strict';

  const BASE = '/gc-api';

  // ── Semanas do plano ──────────────────────────────────────────────────────
  const WEEK_STARTS = [
    '2026-05-25','2026-06-01','2026-06-08','2026-06-15','2026-06-22',
    '2026-06-29','2026-07-06','2026-07-13','2026-07-20','2026-07-27','2026-08-03'
  ];
  const DOW_PT = { 'Seg':0,'Ter':1,'Qua':2,'Qui':3,'Sex':4,'Sáb':5,'Dom':6 };

  // ── Sport types ───────────────────────────────────────────────────────────
  const SPORT = {
    run:      { sportTypeId:1,  sportTypeKey:'running'           },
    swim:     { sportTypeId:4,  sportTypeKey:'swimming'          },  // 4 = swim!
    bike:     { sportTypeId:2,  sportTypeKey:'cycling'           },
    brick:    { sportTypeId:15, sportTypeKey:'multi_sport'       },
    strength: { sportTypeId:5,  sportTypeKey:'strength_training' },
  };

  // ── HR Zone (suporta L1/L2/L3 do texto, Z1/Z2/Z3 do campo zone, A1/A2/A3 natação) ─
  const HR_ZONE = {
    // Zonas padrão
    'Z1':1,'Z1/Z2':2,'Z2':2,'Z2/Z3':3,'Z3':3,'Z3/Z4':4,'Z4':4,'REST':0,
    // Notação L (usada no detalhe do plano)
    'L1':1,'L1/L2':2,'L2':2,'L2/L3':3,'L3':3,'L3/L4':4,'L4':4,
    // Notação A (natação)
    'A1':2,'A2':3,'A3':3,'A4':4,'A5':4,
  };

  // ── Global step counter (reiniciado a cada treino) ─────────────────────
  let _gc = 0;
  function nso() { return ++_gc; }  // next step order

  // ── Helpers de duração ────────────────────────────────────────────────────
  function parseDur(s) {
    if (!s) return 3600;
    s = (s + '').toLowerCase().replace(/\s/g,'').replace('—','0');
    let t = 0;
    const hm = s.match(/^(\d+)h(\d*)/);
    const mm = s.match(/^(\d+)(?:min|m)$/);
    if (hm) {
      t += parseInt(hm[1]) * 3600;
      if (hm[2]) t += parseInt(hm[2]) * 60;
    } else if (mm) {
      t = parseInt(mm[1]) * 60;
    }
    return Math.max(t, 1800);
  }

  // Parse duração de um fragmento de texto (ex: "10min", "1h40", "2h", "45s")
  function fragSec(s) {
    s = (s + '').toLowerCase().replace(/\s/g,'');
    const hm = s.match(/(\d+)h(\d*)/);
    const mm = s.match(/(\d+)min/);
    const ss = s.match(/(\d+)s(?!\w)/);
    if (hm) {
      let t = parseInt(hm[1]) * 3600;
      if (hm[2]) t += parseInt(hm[2]) * 60;
      return t;
    }
    if (mm) return parseInt(mm[1]) * 60;
    if (ss) return parseInt(ss[1]);
    return 0;
  }

  // Extrai HR zone de um fragmento de texto
  // Suporta: "L2", "L3", "L2/L3", "Z2/Z3", "A2", "A3", "A3/A4"
  function zoneNum(s, fallback) {
    const str = s || '';
    // Notação A (natação): A1/A2/A3/A4/A5
    const aMatch = str.match(/\bA(\d)(?:\/A(\d))?\b/i);
    if (aMatch) {
      const key = aMatch[2] ? `A${aMatch[1]}` : `A${aMatch[1]}`;
      return HR_ZONE[key] || fallback || 2;
    }
    // Notação L ou Z
    const m = str.match(/[LZ](\d)(?:\/[LZ](\d))?/i);
    if (!m) return fallback || 2;
    const z1 = parseInt(m[1]);
    const z2 = m[2] ? parseInt(m[2]) : z1;
    const key = z1 < z2 ? `Z${z1}/Z${z2}` : `Z${z1}`;
    return HR_ZONE[key] || HR_ZONE[`Z${z2}`] || fallback || 2;
  }

  // ── Step builders ─────────────────────────────────────────────────────────
  function mkWarmup(secs, desc) {
    return {
      type:'ExecutableStepDTO', stepOrder:nso(),
      stepType:{stepTypeId:1, stepTypeKey:'warmup'},
      endCondition:{conditionTypeId:2, conditionTypeKey:'time'},
      endConditionValue: secs || 600,
      description: (desc || 'Aquecimento').substring(0,100)
    };
  }

  function mkCooldown(secs, desc) {
    return {
      type:'ExecutableStepDTO', stepOrder:nso(),
      stepType:{stepTypeId:2, stepTypeKey:'cooldown'},
      endCondition:{conditionTypeId:2, conditionTypeKey:'time'},
      endConditionValue: secs || 300,
      description: (desc || 'Resfriamento').substring(0,100)
    };
  }

  function mkInterval(secs, hrZ, desc) {
    const s = {
      type:'ExecutableStepDTO', stepOrder:nso(),
      stepType:{stepTypeId:3, stepTypeKey:'interval'},
      endCondition:{conditionTypeId:2, conditionTypeKey:'time'},
      endConditionValue: secs || 1800,
      description: (desc || 'Intervalo').substring(0,100)
    };
    if (hrZ && hrZ > 0) {
      s.targetType = {workoutTargetTypeId:4, workoutTargetTypeKey:'heart.rate.zone'};
      s.targetValueOne = hrZ;
      s.targetValueTwo = hrZ;
    }
    return s;
  }

  function mkRecovery(secs, hrZ) {
    const s = {
      type:'ExecutableStepDTO', stepOrder:nso(),
      stepType:{stepTypeId:6, stepTypeKey:'recovery'},
      endCondition:{conditionTypeId:2, conditionTypeKey:'time'},
      endConditionValue: secs || 120,
      description: 'Recuperação'
    };
    if (hrZ && hrZ > 0) {
      s.targetType = {workoutTargetTypeId:4, workoutTargetTypeKey:'heart.rate.zone'};
      s.targetValueOne = hrZ;
      s.targetValueTwo = hrZ;
    }
    return s;
  }

  // Intervalo baseado em distância (metros) — ex: "6.5km L3"
  function mkIntervalDist(meters, hrZ, desc) {
    const s = {
      type:'ExecutableStepDTO', stepOrder:nso(),
      stepType:{stepTypeId:3, stepTypeKey:'interval'},
      endCondition:{conditionTypeId:3, conditionTypeKey:'distance'},
      endConditionValue: meters,
      description: (desc || 'Intervalo').substring(0,100)
    };
    if (hrZ && hrZ > 0) {
      s.targetType = {workoutTargetTypeId:4, workoutTargetTypeKey:'heart.rate.zone'};
      s.targetValueOne = hrZ;
      s.targetValueTwo = hrZ;
    }
    return s;
  }

  // Recuperação baseada em tempo — ex: "3min recuperação"
  function mkRecoveryTime(secs, hrZ) {
    const s = {
      type:'ExecutableStepDTO', stepOrder:nso(),
      stepType:{stepTypeId:6, stepTypeKey:'recovery'},
      endCondition:{conditionTypeId:2, conditionTypeKey:'time'},
      endConditionValue: secs || 180,
      description: 'Recuperação'
    };
    if (hrZ && hrZ > 0) {
      s.targetType = {workoutTargetTypeId:4, workoutTargetTypeKey:'heart.rate.zone'};
      s.targetValueOne = hrZ;
      s.targetValueTwo = hrZ;
    }
    return s;
  }

  // RepeatGroupDTO — step orders: group primeiro, depois filhos (globalmente únicos)
  // Suporta: tempo (secs) ou distância (meters) para o intervalo
  function mkRepeatGroup(reps, intVal, intIsKm, intHrZ, recSec, recHrZ, intDesc) {
    const groupOrder = nso();
    const intStep    = intIsKm
      ? mkIntervalDist(intVal, intHrZ, intDesc)
      : mkInterval(intVal, intHrZ, intDesc);
    const recStep    = mkRecoveryTime(recSec, recHrZ);
    return {
      type:'RepeatGroupDTO',
      stepOrder: groupOrder,
      numberOfIterations: reps,
      smartRepeat: false,
      workoutSteps: [intStep, recStep]
    };
  }

  // Extrai distância em metros de um fragmento ("6.5km" → 6500, "1900m" → 1900)
  // ATENÇÃO: "1.900m" em PT tem ponto como milhar — usar "1900m" sem ponto nos detalhes
  function fragDist(s) {
    const km = (s+'').match(/(\d+(?:[.,]\d+)?)\s*km/i);
    if (km) return Math.round(parseFloat(km[1].replace(',','.')) * 1000);
    // \b garante que captura número inteiro (não "900" de "1.900")
    const mt = (s+'').match(/\b(\d+)\s*m(?:etros?)?\b/i);
    if (mt) return parseInt(mt[1]);
    return 0;
  }

  // ── Parser de detalhe → array de steps Garmin ─────────────────────────────
  // Lê strings como:
  //   "10min aquecimento | 40min L2 | 3x8min L3 c/ 4min L2 | 10min solto"
  //   "15min aquecimento | 6.5km L3 + 3min recuperação | 6.5km L3 | 5min trote"
  // Suporte a separadores | e + (BRICK usa ambos)
  function parseDetail(detail, defaultHrZ) {
    const steps  = [];
    const parts  = detail.split(/\s*[\|+]\s*/).map(s => s.trim()).filter(Boolean);

    for (const part of parts) {
      // ── Ignorar marcadores de seção e notas ──────────────────────────────
      if (/^(T2:|Foco:|Total:|Anote|Observ|Nutrição|Beba|Compare|Esta é|Você fez|Bônus|Se não|Piscina)/i.test(part)) continue;
      if (/^(BIKE|CORRIDA)\s+\d+km:/i.test(part)) {
        // Cabeçalho de seção do BRICK — remove o prefixo e reprocessa inline
        const inner = part.replace(/^(BIKE|CORRIDA)\s+\d+km:\s*/i, '');
        if (inner) steps.push(...parseDetail(inner, defaultHrZ));
        continue;
      }

      // ── BLOCO DE REPETIÇÃO ───────────────────────────────────────────────
      // Unifica: NxDIST[km|m] ZONA c/ DUR[min|s] ZONA
      //   Ex: "3x500m A2 c/ 45s intervalo"    (swim)
      //       "2x6.5km L3 c/ 3min L1"          (run)
      //       "3x8min L3 c/ 4min L2"            (bike)
      //       "5x1min força c/ 2min L1"
      const rptGeneral = part.match(
        /(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*(km|m\b|min|s\b)([^c]*)c\/\s*(\d+(?:[.,]\d+)?)\s*(min|s)\s*([^\|]*)/i
      );
      if (rptGeneral) {
        const reps    = parseInt(rptGeneral[1]);
        const intVal  = parseFloat(rptGeneral[2].replace(',','.'));
        const intUnit = rptGeneral[3].toLowerCase();
        const intZT   = rptGeneral[4];
        const recVal  = parseFloat(rptGeneral[5].replace(',','.'));
        const recUnit = rptGeneral[6].toLowerCase();
        const recZT   = rptGeneral[7];

        // Converter para metros ou segundos
        let intIsKm, intConverted;
        if (intUnit === 'km')        { intIsKm = true;  intConverted = Math.round(intVal * 1000); }
        else if (intUnit === 'm')    { intIsKm = true;  intConverted = Math.round(intVal);        }
        else if (intUnit === 'min')  { intIsKm = false; intConverted = intVal * 60;               }
        else /* s */                 { intIsKm = false; intConverted = intVal;                    }

        const recSec = recUnit === 'min' ? recVal * 60 : recVal;

        steps.push(mkRepeatGroup(
          reps, intConverted, intIsKm,
          zoneNum(intZT, defaultHrZ), recSec, zoneNum(recZT, 1),
          part.substring(0, 80)
        ));
        continue;
      }

      // ── AQUECIMENTO ──────────────────────────────────────────────────────
      if (/aquec|\baq\b/i.test(part)) {
        const secs = fragSec(part);
        const dist = fragDist(part);
        if (dist > 0 && !secs) {
          // "300m Br aquecimento" → distância
          const step = mkWarmup(600, 'Aquecimento');
          step.endCondition = {conditionTypeId:3, conditionTypeKey:'distance'};
          step.endConditionValue = dist;
          steps.push(step);
        } else {
          steps.push(mkWarmup(secs || 600, 'Aquecimento'));
        }
        continue;
      }

      // ── RECUPERAÇÃO EXPLÍCITA: "3min recuperação", "45s intervalo" ───────
      if (/recup|intervalo\b/i.test(part) && !rptGeneral) {
        const secs = fragSec(part) || 120;
        const hrZ  = zoneNum(part, 1);
        steps.push(mkRecoveryTime(secs, hrZ));
        continue;
      }

      // ── TROTE / SOLTO / RESFRIAMENTO ─────────────────────────────────────
      if (/\btrote\b|\bsolto\b|\bresfri|\bcool/i.test(part) && !(/[LA][34]/i.test(part))) {
        const secs = fragSec(part);
        const dist = fragDist(part);
        if (dist > 0 && !secs) {
          // "200m Br resfriamento" → distância
          const step = mkCooldown(300, 'Resfriamento');
          step.endCondition = {conditionTypeId:3, conditionTypeKey:'distance'};
          step.endConditionValue = dist;
          steps.push(step);
        } else {
          steps.push(mkCooldown(secs || 300, 'Resfriamento'));
        }
        continue;
      }

      // ── STEP BASEADO EM DISTÂNCIA: "6.5km L3", "500m A2 contínuo" ────────
      const dist = fragDist(part);
      if (dist > 0 && !fragSec(part)) {
        const hrZ = zoneNum(part, defaultHrZ);
        steps.push(mkIntervalDist(dist, hrZ, part.substring(0,80)));
        continue;
      }

      // ── STEP GENÉRICO BASEADO EM TEMPO (constante / progressivo / etc.) ──
      const secs = fragSec(part);
      if (secs > 0) {
        const hrZ = zoneNum(part, defaultHrZ);
        steps.push(mkInterval(secs, hrZ, part.substring(0,80)));
      }
    }

    return steps;
  }

  // Garante que o array de steps tenha warmup no início e cooldown no fim
  function ensureBookends(steps, defaultHrZ) {
    const hasWarmup   = steps.some(s => s.stepType?.stepTypeKey === 'warmup');
    const hasCooldown = steps.some(s => s.stepType?.stepTypeKey === 'cooldown');
    if (!hasWarmup)   steps.unshift(mkWarmup(600));
    if (!hasCooldown) steps.push(mkCooldown(300));
    return steps;
  }

  // Expande RepeatGroupDTO em steps planos — necessário para segmentos BRICK.
  // LIMITAÇÃO Garmin API: em workouts multi-sport (workoutSegments), a API descarta
  // silenciosamente os steps de recovery dentro de RepeatGroupDTO.
  // Solução: expandir N×(interval + recovery) como steps individuais.
  // Após expandir, renumera TODOS os steps e sincroniza _gc para o segmento seguinte.
  function expandForBrick(steps) {
    const flat = [];
    for (const s of steps) {
      if (s.type === 'RepeatGroupDTO') {
        const n = s.numberOfIterations || 1;
        for (let i = 0; i < n; i++) {
          for (const c of (s.workoutSteps || [])) {
            const suffix = n > 1 ? ` (${i+1}/${n})` : '';
            // Converter recovery → interval: Garmin descarta stepTypeId:6 em BRICK segments
            const stepType = (c.stepType?.stepTypeKey === 'recovery')
              ? {stepTypeId:3, stepTypeKey:'interval'}
              : c.stepType;
            flat.push(Object.assign({}, c, {
              stepType,
              description: ((c.description || '') + suffix).substring(0, 100)
            }));
          }
        }
      } else {
        flat.push(s);
      }
    }
    // Renumerar sequencialmente (corrige stepOrders errados de ensureBookends)
    flat.forEach((s, i) => { s.stepOrder = i + 1; });
    _gc = flat.length;  // sincronizar contador global para segmento seguinte
    return flat;
  }

  // ── Data a partir do dia da semana ────────────────────────────────────────
  function toDate(weekIdx, dowStr) {
    const prefix = dowStr.split(' ')[0];
    const offset = DOW_PT[prefix] ?? 0;
    const base   = new Date(WEEK_STARTS[weekIdx] + 'T12:00:00');
    base.setDate(base.getDate() + offset);
    return base.toISOString().slice(0,10);
  }

  // ── API helpers ───────────────────────────────────────────────────────────
  function csrf() {
    return document.querySelector('meta[name="csrf-token"]')?.content;
  }

  async function apiPost(path, body) {
    const r = await fetch(BASE + path, {
      method:'POST', credentials:'include',
      headers:{
        'connect-csrf-token': csrf(),
        'Content-Type':'application/json',
        'NK':'NT',
        'Accept':'application/json'
      },
      body: JSON.stringify(body)
    });
    const text = await r.text();
    const json = text ? JSON.parse(text) : {};
    if (!r.ok) throw new Error(`HTTP ${r.status}: ${text.substring(0,300)}`);
    return json;
  }

  // ── BUILD: Treino regular (swim / run / bike / strength) ──────────────────
  function buildRegular(day, weekIdx) {
    const sp = SPORT[day.disc];
    if (!sp) return null;
    _gc = 0;  // reset contador global de steps

    const date    = toDate(weekIdx, day.dow);
    const dur     = parseDur(day.dur);
    const defaultZ = HR_ZONE[day.zone] ?? 2;

    let steps;
    if (day.disc === 'strength') {
      // Força: steps básicos — os exercícios do detalhe são notas, não steps do Garmin
      steps = [
        mkWarmup(600, 'Mobilização'),
        mkInterval(dur, defaultZ, day.name.substring(0,80)),
        mkCooldown(300, 'Alongamento')
      ];
    } else if (day.detail && day.detail.includes('|')) {
      steps = parseDetail(day.detail, defaultZ);
    } else {
      // Sem detalhe estruturado → 3 steps básicos
      steps = [mkWarmup(600), mkInterval(dur, defaultZ, day.name), mkCooldown(300)];
    }

    steps = ensureBookends(steps, defaultZ);

    return {
      payload: {
        workoutName: `[IRONMAN] ${day.name.substring(0,50)}`,
        description: `S${weekIdx+1} IRONMAN 70.3 Rio | ${day.zone} | ${day.dur}`,
        sportType: sp,
        workoutSegments: [{
          segmentOrder: 1,
          sportType: sp,
          workoutSteps: steps
        }],
        estimatedDurationInSecs: dur + 900
      },
      date
    };
  }

  // ── BUILD: BRICK (multi_sport) — 2 segmentos com step orders globais ──────
  function buildBrick(day, weekIdx) {
    _gc = 0;  // reset contador global (único para todo o workout)

    const date    = toDate(weekIdx, day.dow);
    const dur     = parseDur(day.dur);
    const defaultZ = HR_ZONE[day.zone] ?? 3;

    const detail  = day.detail || '';

    // ── Separar seção BIKE da seção CORRIDA ─────────────────────────────────
    // Formatos esperados:
    //   "BIKE 100km: ... | T2: ... | CORRIDA 5km: ..."
    //   "BIKE 130km: ... | T2: ... | CORRIDA 5km: ..."
    let bikeText = detail;
    let runText  = '';

    const corrMatch = detail.match(/CORRIDA\s+\d+km:(.*?)(?:$|\|[^|]*Foco:|\|[^|]*Anote:)/is);
    const t2Match   = detail.search(/\|\s*T2:/i);

    if (corrMatch) {
      // Tem marcador explícito CORRIDA
      const corrIdx = detail.search(/CORRIDA\s+\d+km:/i);
      bikeText = detail.substring(0, corrIdx);
      runText  = corrMatch[1];
    } else if (t2Match > 0) {
      bikeText = detail.substring(0, t2Match);
    }

    // Limpar prefixo "BIKE Xkm:" e sufixo T2
    bikeText = bikeText.replace(/^BIKE\s+\d+km:\s*/i, '').replace(/\|\s*T2:.*$/is, '').trim();

    // ── Segmento 1: Bike ─────────────────────────────────────────────────────
    let bikeSteps;
    if (bikeText && (bikeText.includes('|') || bikeText.includes('+'))) {
      bikeSteps = parseDetail(bikeText, defaultZ);
    } else {
      // Fallback: aquecimento + bloco principal + desaceleração
      bikeSteps = [
        mkWarmup(600, 'Aquecimento bike'),
        mkInterval(Math.floor(dur * 0.80), defaultZ, 'Ciclismo principal'),
        mkCooldown(300, 'Desaceleração / T2')
      ];
    }
    bikeSteps = ensureBookends(bikeSteps, defaultZ);
    bikeSteps = expandForBrick(bikeSteps);  // expande RepeatGroups + renumera + atualiza _gc

    // ── Segmento 2: Run ──────────────────────────────────────────────────────
    // BRICK run: Garmin rejeita conditionTypeId:3 (distância) em steps planos de corrida.
    // Converter "Xkm" → tempo usando pace estimado por zona:
    //   Z2 ≈ 5:45/km (345s/km), Z3 ≈ 5:00/km (300s/km), Z4 ≈ 4:30/km (270s/km)
    const BRICK_RUN_PACE = {2:345, 3:300, 4:270};
    const runZ = HR_ZONE['Z2/Z3'] || 3;
    let runText2 = runText.trim();
    // Substituir "Xkm ZONA" → "Ymin ZONA" para evitar steps de distância
    runText2 = runText2.replace(/(\d+(?:[.,]\d+)?)\s*km\b([^+|]*)/ig, (_, km, rest) => {
      const z = zoneNum(rest, runZ);
      const pace = BRICK_RUN_PACE[z] || 330;
      const secs = Math.round(parseFloat(km.replace(',','.')) * pace);
      const mins = Math.round(secs/60);
      return `${mins}min${rest}`;
    });
    let runSteps;
    if (runText2) {
      runSteps = parseDetail(runText2, runZ);
    } else {
      runSteps = [];
    }

    // Se runSteps vazio ou só tem notas → steps padrão 5km (~25min)
    const runHasInterval = runSteps.some(s => s.stepType?.stepTypeKey === 'interval' || s.type === 'RepeatGroupDTO');
    if (!runHasInterval) {
      runSteps = [
        mkInterval(1500, runZ, 'Corrida pós-bike L2/L3'),
      ];
    }
    // Garantir cooldown no final da corrida
    if (!runSteps.some(s => s.stepType?.stepTypeKey === 'cooldown')) {
      runSteps.push(mkCooldown(300, 'Resfriamento final'));
    }

    return {
      payload: {
        workoutName: `[IRONMAN] ${day.name.substring(0,50)}`,
        description: `S${weekIdx+1} IRONMAN 70.3 Rio | BRICK | ${day.zone} | ${day.dur}`,
        sportType: SPORT.brick,
        workoutSegments: [
          {
            segmentOrder: 1,
            sportType: SPORT.bike,
            workoutSteps: bikeSteps
          },
          {
            segmentOrder: 2,
            sportType: SPORT.run,
            workoutSteps: runSteps
          }
        ],
        estimatedDurationInSecs: dur + 900
      },
      date
    };
  }

  // ── Despachante principal ─────────────────────────────────────────────────
  function buildPayload(day, weekIdx) {
    if (day.disc === 'brick') return buildBrick(day, weekIdx);
    return buildRegular(day, weekIdx);
  }

  // ── Lê os treinos do WEEKS global no dashboard ────────────────────────────
  function getDays(weekIdx) {
    if (typeof WEEKS !== 'undefined' && WEEKS[weekIdx]) {
      return WEEKS[weekIdx].days;
    }
    throw new Error('❌ WEEKS não encontrado. Cole este script na aba do dashboard (diegolimak.github.io/ironman/)');
  }

  // ── DEBUG: imprime a estrutura sem enviar ─────────────────────────────────
  window.previewWeek = function(weekNum, daysFilter) {
    const weekIdx = weekNum - 1;
    let days;
    try { days = getDays(weekIdx); } catch(e) { console.error(e.message); return; }
    const filtered = daysFilter ? days.filter((_,i) => daysFilter.includes(i+1)) : days;

    for (const day of filtered) {
      if (day.disc === 'rest') { console.log(`  ⏭  ${day.dow} — descanso`); continue; }
      const built = buildPayload(day, weekIdx);
      if (!built) continue;
      console.group(`${day.dow} — ${day.name}`);
      console.log('Segmentos:', built.payload.workoutSegments.length);
      built.payload.workoutSegments.forEach((seg, si) => {
        console.log(`  Seg ${si+1} (${seg.sportType.sportTypeKey}) — ${seg.workoutSteps.length} steps:`);
        seg.workoutSteps.forEach(s => {
          const ord   = s.stepOrder;
          const tipo  = s.type === 'RepeatGroupDTO' ? `🔁 REPEAT x${s.numberOfIterations}` : s.stepType.stepTypeKey;
          let   quan  = '';
          if (s.endConditionValue) {
            quan = s.endCondition?.conditionTypeKey === 'distance'
              ? `${(s.endConditionValue/1000).toFixed(2)}km`
              : `${(s.endConditionValue/60).toFixed(1)}min`;
          }
          const zone  = s.targetValueOne ? `Z${s.targetValueOne}` : '';
          const inner = s.workoutSteps ? ` [${s.workoutSteps.map(c => {
            const ct = c.endCondition?.conditionTypeKey === 'distance';
            const v  = ct ? `${(c.endConditionValue/1000).toFixed(2)}km` : `${(c.endConditionValue/60).toFixed(1)}min`;
            return c.stepType.stepTypeKey+' '+c.stepOrder+' '+v;
          }).join(', ')}]` : '';
          console.log(`    [${ord}] ${tipo} ${quan} ${zone}${inner}`);
        });
      });
      console.groupEnd();
    }
  };

  // ── Push principal ────────────────────────────────────────────────────────
  window.pushWeek = async function(weekNum, daysFilter) {
    const weekIdx = weekNum - 1;
    const start   = WEEK_STARTS[weekIdx];
    if (!start) { console.error('❌ Semana inválida (1–11)'); return; }

    let days;
    try { days = getDays(weekIdx); }
    catch(e) { console.error(e.message); return; }

    const filtered = daysFilter
      ? days.filter((_,i) => daysFilter.includes(i+1))
      : days;

    console.log(`\n🏊‍♂️🚴🏃 Enviando S${weekNum} (${start}) — ${filtered.length} dias`);
    console.log('   💡 Dica: use previewWeek('+weekNum+') para ver estrutura sem enviar\n');

    const results = [];
    for (const day of filtered) {
      if (day.disc === 'rest') {
        console.log(`  ⏭  ${day.dow} — descanso, pulando`);
        continue;
      }

      const built = buildPayload(day, weekIdx);
      if (!built) {
        console.log(`  ⚠  ${day.dow} — sport desconhecido: ${day.disc}`);
        continue;
      }

      // Log informativo sobre o tipo de estrutura
      const segs    = built.payload.workoutSegments;
      const isBrick = segs.length > 1;
      const totalSteps = segs.reduce((acc, seg) => acc + seg.workoutSteps.length, 0);
      const repeatCount = segs.reduce((acc, seg) =>
        acc + seg.workoutSteps.filter(s => s.type === 'RepeatGroupDTO').length, 0);

      const info = isBrick
        ? `BRICK [${segs[0].sportType.sportTypeKey}+${segs[1].sportType.sportTypeKey}]`
        : `${segs[0].sportType.sportTypeKey} — ${totalSteps} steps${repeatCount ? ', ' + repeatCount + ' blocos' : ''}`;

      try {
        const wo = await apiPost('/workout-service/workout', built.payload);
        const sc = await apiPost(`/workout-service/schedule/${wo.workoutId}`, { date: built.date });
        console.log(`  ✅ [${built.date}] ${day.name.substring(0,45)} — ${info} (id:${wo.workoutId})`);
        results.push({ date:built.date, name:day.name, workoutId:wo.workoutId,
                       scheduleId:sc.workoutScheduleId, ok:true });
      } catch(e) {
        console.error(`  ❌ [${day.dow}] ${day.name.substring(0,40)}: ${e.message}`);
        results.push({ date:toDate(weekIdx, day.dow), name:day.name, ok:false, error:e.message });
      }

      await new Promise(r => setTimeout(r, 900));
    }

    const ok = results.filter(r => r.ok).length;
    console.log(`\n📱 ${ok}/${results.length} treinos enviados. Sincronize o relógio!`);
    if (results.some(r => !r.ok)) {
      console.log('   ⚠ Para re-tentar falhas: pushWeek('+weekNum+', [dias])\n');
    }
    return results;
  };

  // ── Pull atividades da semana passada ─────────────────────────────────────
  // Uso: pullWeek(1)  → puxa atividades da S1 (25-31 Mai)
  window.pullWeek = async function(weekNum) {
    const weekIdx = weekNum - 1;
    const start   = new Date(WEEK_STARTS[weekIdx] + 'T00:00:00');
    const end     = new Date(start);
    end.setDate(start.getDate() + 6);
    end.setHours(23, 59, 59);

    const fmt = d => d.toISOString().slice(0,10);
    console.log(`\n📥 Puxando atividades S${weekNum}: ${fmt(start)} → ${fmt(end)}`);

    const r = await fetch(`${BASE}/activitylist-service/activities/search/activities?` +
      `startDate=${fmt(start)}&endDate=${fmt(end)}&limit=30&start=0`, {
      credentials:'include',
      headers:{'NK':'NT','Accept':'application/json','connect-csrf-token':csrf()}
    });
    const data = await r.json();
    const acts = data.activityList || data || [];

    console.log(`\n📊 ${acts.length} atividades encontradas na S${weekNum}:\n`);
    const rows = [];
    for (const a of acts) {
      const tipo = a.activityType?.typeKey || '?';
      const dist = a.distance ? (a.distance/1000).toFixed(1)+'km' : '—';
      const durf = a.duration  ? Math.round(a.duration/60)+'min' : '—';
      const fcM  = a.averageHR ? a.averageHR+'bpm' : '—';
      const fcX  = a.maxHR     ? a.maxHR+'bpm' : '—';
      const kcal = a.calories  ? a.calories+'kcal' : '—';
      const dia  = (a.startTimeLocal||'').substring(0,10);
      console.log(`  ${dia} | ${tipo.padEnd(20)} | ${dist.padEnd(8)} | ${durf.padEnd(7)} | FC ${fcM} (max ${fcX}) | ${kcal}`);
      rows.push({ dia, tipo, dist: a.distance, dur: a.duration, avgHR: a.averageHR, maxHR: a.maxHR, cal: a.calories, name: a.activityName, id: a.activityId });
    }
    console.log('\n📋 Resultado JSON (cole no garmin_data.json → activities_s'+weekNum+'):');
    console.log(JSON.stringify(rows, null, 2));
    return rows;
  };

  // ── Lista treinos agendados no calendário para uma semana ─────────────────
  // Uso: listScheduled(2)  → mostra workouts agendados na S2
  window.listScheduled = async function(weekNum) {
    const weekIdx = weekNum - 1;
    const start   = WEEK_STARTS[weekIdx];
    const end_d   = new Date(start + 'T12:00:00');
    end_d.setDate(end_d.getDate() + 6);
    const end = end_d.toISOString().slice(0,10);

    console.log(`\n📅 Treinos agendados S${weekNum}: ${start} → ${end}`);
    const r = await fetch(`${BASE}/workout-service/schedule/${start}/${end}`, {
      credentials:'include',
      headers:{'NK':'NT','Accept':'application/json','connect-csrf-token':csrf()}
    });
    const data = await r.json();
    const items = data.calendarItemList || data || [];
    const workouts = items.filter(x => x.itemType === 'workout' || x.workoutId);

    if (workouts.length === 0) {
      console.log('  Nenhum treino agendado nesta semana.');
      return [];
    }

    console.log(`  ${workouts.length} treino(s) encontrado(s):\n`);
    workouts.forEach(w => {
      const sport = w.activityType || w.sportTypeKey || '?';
      console.log(`  [scheduleId:${w.id}] [workoutId:${w.workoutId}] ${w.date} — ${w.title || w.workoutName || '?'} (${sport})`);
    });
    console.log('\n  Para deletar todos:  deleteScheduled('+weekNum+')');
    return workouts;
  };

  // ── Deleta todos os treinos agendados de uma semana (para re-push) ─────────
  // CUIDADO: irreversível. Use listScheduled(n) primeiro para confirmar.
  window.deleteScheduled = async function(weekNum) {
    const items = await listScheduled(weekNum);
    if (items.length === 0) { console.log('Nada para deletar.'); return; }

    console.log(`\n🗑 Deletando ${items.length} treino(s) agendados...`);
    let ok = 0;
    for (const w of items) {
      // Remove agendamento do calendário
      if (w.id) {
        const r1 = await fetch(`${BASE}/workout-service/schedule/${w.id}`, {
          method:'DELETE', credentials:'include',
          headers:{'NK':'NT','connect-csrf-token':csrf()}
        });
        if (r1.ok) { console.log(`  🗑 schedule ${w.id} deletado`); ok++; }
        else       { console.warn(`  ⚠ schedule ${w.id}: ${r1.status}`); }
      }
      // Remove o workout em si
      if (w.workoutId) {
        const r2 = await fetch(`${BASE}/workout-service/workout/${w.workoutId}`, {
          method:'DELETE', credentials:'include',
          headers:{'NK':'NT','connect-csrf-token':csrf()}
        });
        if (r2.ok) console.log(`  🗑 workout ${w.workoutId} deletado`);
        else       console.warn(`  ⚠ workout ${w.workoutId}: ${r2.status}`);
      }
      await new Promise(r => setTimeout(r, 400));
    }
    console.log(`\n✅ ${ok}/${items.length} removidos. Agora use pushWeek(${weekNum}) para reenviar.`);
  };

  console.log('✅ garmin_push_via_browser.js v2.0 carregado!');
  console.log('');
  console.log('   📤 ENVIAR:    pushWeek(2)             ← S2 inteira');
  console.log('   📤 PARCIAL:   pushWeek(2, [5,6,7,8])  ← só Qui-Dom da S2');
  console.log('   👁  PREVIEW:  previewWeek(2)           ← ver estrutura sem enviar');
  console.log('   📥 PUXAR:     pullWeek(1)              ← atividades da S1');
  console.log('   📅 LISTAR:    listScheduled(2)         ← ver o que está no calendário');
  console.log('   🗑  LIMPAR:    deleteScheduled(2)       ← deletar S2 para re-push');
  console.log('');

})();
