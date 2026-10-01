// Utilidades sem estado: escape, numeros, datas e o relogio da pagina.
import { CL, D, VF, estado } from './estado.js';

// --------------------------------------------------------------- utilidades

/** Nada dos dados entra no DOM sem passar por aqui: e a defesa contra XSS. */
function esc(valor) {
  return String(valor ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function extensaoDe(dominio) {
  const p = dominio.indexOf('.');
  return p === -1 ? '' : dominio.slice(p + 1);
}

/**
 * Desempate de nota igual: .com.br primeiro, depois o rotulo mais curto, depois
 * A-Z. Pelo tamanho do dominio inteiro, lima.ia.br passava na frente de
 * lima.com.br so porque a extensao e mais curta.
 */
function desempateDaNota(a, b) {
  const comBr = (d) => (d.endsWith('.com.br') ? 0 : 1);
  const rotulo = (d) => { const p = d.indexOf('.'); return p === -1 ? d.length : p; };
  return comBr(a) - comBr(b) || rotulo(a) - rotulo(b) || a.localeCompare(b);
}

// As datas do site sao as do Registro.br, em horario de Brasilia, qualquer
// que seja o fuso de quem olha (senao, em Manaus, a rodada "fecharia as 14:00")
const FUSO = 'America/Sao_Paulo';

function formatarData(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit', timeZone: FUSO,
  });
}

function formatarDataCurta(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleDateString('pt-BR', { timeZone: FUSO }) + ' ' +
    d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: FUSO });
}

/** "15h" ou "15h30", em horario de Brasilia. */
function horaCurta(d) {
  const [h, m] = d.toLocaleTimeString('pt-BR',
    { hour: '2-digit', minute: '2-digit', timeZone: FUSO }).split(':');
  return m === '00' ? `${Number(h)}h` : `${Number(h)}h${m}`;
}

/*
 * O relogio da pagina. Na previa local, ?agora=<ISO> poe a pagina num
 * instante qualquer para ensaiar as fases da rodada; o relogio
 * continua andando a partir dali. No ar o parametro nao vale.
 */
const DESVIO_DO_RELOGIO = (() => {
  try {
    if (!['127.0.0.1', 'localhost'].includes(location.hostname)) return 0;
    const pedido = new URLSearchParams(location.search).get('agora');
    const quando = pedido ? new Date(pedido).getTime() : NaN;
    return isNaN(quando) ? 0 : quando - Date.now();
  } catch (e) {
    return 0;
  }
})();
const agoraMs = () => Date.now() + DESVIO_DO_RELOGIO;
const agoraDaPagina = () => new Date(agoraMs());

function num(n) {
  return n.toLocaleString('pt-BR');
}

const agoraSeg = () => Math.floor(Date.now() / 1000);

function idadeTexto(segundos) {
  if (segundos < 90) return 'agora';
  const min = Math.round(segundos / 60);
  if (min < 60) return `há ${min} min`;
  const horas = Math.round(segundos / 3600);
  if (horas < 48) return `há ${horas} h`;
  return `há ${Math.round(segundos / 86400)} d`;
}

/**
 * Idade da linha e se ela passou do prazo prometido para a classe dela.
 *
 * O prazo vem do JSON (dominio/frescor.py), nao daqui: a regra mora num
 * lugar so. A comparacao e estrita (idade > prazo), a mesma do alarme.
 */
function frescorDe(it) {
  if (estado.aoVivo.has(it[D])) return { aoVivo: true };
  if (!it[VF]) return null;
  const idade = Math.max(0, agoraSeg() - it[VF]);
  const classes = (estado.dados.frescor || {}).classes || [];
  const prazo = classes[it[CL]] && classes[it[CL]].prazo_horas;
  return {
    idade,
    vencido: Boolean(prazo) && idade > prazo * 3600,
    navegador: estado.doNavegador.has(it[D]),
  };
}

export {
  desempateDaNota,
  esc,
  extensaoDe,
  FUSO,
  formatarData,
  formatarDataCurta,
  horaCurta,
  DESVIO_DO_RELOGIO,
  agoraMs,
  agoraDaPagina,
  num,
  agoraSeg,
  idadeTexto,
  frescorDe,
};
