// A rodada inteira (todos.json), carregada so quando a busca pede.
import { provisorios } from './acompanhados.js';
import { normalizarTermo } from './busca.js';
import { avisar, restaurarConferido } from './conferencia.js';
import { CT, D, LK, M, MK, N, NAO_VERIFICADO, RN, SN, estado } from './estado.js';
import { preencherExtensoes } from './painel.js';

// ----------------------------------------------------------- rodada inteira

/**
 * Carrega todos.json e junta ao instantaneo.
 *
 * Nome que ja foi consultado aparece com a linha do instantaneo, que tem
 * situacao e idade. O resto vira linha NAO_VERIFICADO: nota, motivos e
 * risco de marca, sem situacao. "conferir" funciona igual nas duas.
 */
function carregarRodada({ aviso = true } = {}) {
  if (estado.rodada) return Promise.resolve();
  // quem chega com a carga em andamento espera a mesma (o foco na busca
  // comeca a baixar; o cartao ou o resumo tocados logo depois esperam ela)
  if (!estado.promessaRodada) {
    estado.promessaRodada = baixarRodada(aviso).finally(() => { estado.promessaRodada = null; });
  }
  return estado.promessaRodada;
}

async function baixarRodada(aviso) {
  estado.carregandoRodada = true;
  // a busca avisa no proprio resumo ("buscando nos N nomes"); o #ao-vivo e
  // da conferencia ao vivo
  if (aviso) avisar('Carregando a lista completa da rodada (cerca de 0,8 MB)…');
  try {
    const r = await fetch('todos.json');
    if (!r.ok) throw new Error(`http ${r.status}`);
    const t = await r.json();
    const d = estado.dados;
    // os motivos do todos.json entram no fim do vocabulario do instantaneo
    const deslocamento = d.motivos.length;
    d.motivos.push(...t.motivos);
    const riscos = (t.marcas || []).map((m) => d.marcas.indexOf(m));
    const juntos = [];
    const vistos = new Set();
    for (const [rotulo, ext, nota, tipos, risco, cats, lk, sn] of t.itens) {
      const dominio = `${rotulo}.${t.extensoes[ext]}`;
      const conhecido = estado.porDominio.get(dominio);
      if (conhecido && provisorios.has(conhecido)) {
        // acompanhado de fora do instantaneo: ganha nota, motivos e ramo
        conhecido[N] = nota;
        conhecido[M] = tipos.map((i) => i + deslocamento);
        conhecido[MK] = Math.max(0, riscos[risco] ?? 0);
        conhecido[CT] = cats || 0;
        conhecido[LK] = lk || 0;
        conhecido[SN] = sn || 0;
        conhecido[RN] = normalizarTermo(rotulo);
        provisorios.delete(conhecido);
        juntos.push(conhecido);
      } else if (conhecido) {
        conhecido[RN] = normalizarTermo(rotulo);
        juntos.push(conhecido);
      } else {
        // o rotulo normalizado uma vez, na carga: cada tecla so compara
        const linha = [dominio, NAO_VERIFICADO, null, nota, 0,
                       tipos.map((i) => i + deslocamento),
                       Math.max(0, riscos[risco] ?? 0), 0, 0, -1, cats || 0,
                       0, 0, lk || 0, sn || 0, normalizarTermo(rotulo)];
        estado.porDominio.set(dominio, linha);
        restaurarConferido(linha);
        juntos.push(linha);
      }
      vistos.add(dominio);
    }
    // o instantaneo pode ter nome que ja saiu da lista (historico): fica
    for (const it of d.itens) if (!vistos.has(it[D])) juntos.push(it);
    estado.rodada = juntos;
    preencherExtensoes(t.extensoes);
    if (aviso) avisar('');
  } catch (e) {
    avisar(`Não consegui carregar a lista completa (${e.message}).`);
  } finally {
    estado.carregandoRodada = false;
  }
}

export {
  carregarRodada,
  baixarRodada,
};
