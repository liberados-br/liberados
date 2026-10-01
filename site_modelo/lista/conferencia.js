// A conferencia ao vivo no RDAP e o conferido guardado no aparelho.
import { REVER_MINUTOS, fotografia, garantirLinha, notificar, registrarMudanca, rotuloSituacao, textoDaMudanca } from './acompanhados.js';
import { $, C, D, EL, FILTROS, N, SS, VF, estado } from './estado.js';
import { aplicar } from './filtros.js';
import { pintarContagens, visiveis } from './painel.js';
import { pintarTabela } from './tabela.js';
import { agoraSeg } from './util.js';

// ------------------------------------------------------------- conferencia

/*
 * Conferencia ao vivo, do navegador de quem visita, direto no RDAP do
 * Registro.br. O RDAP responde com CORS aberto (`*`); o endpoint de
 * disponibilidade que a varredura usa so aceita a origem do proprio
 * Registro.br, entao nao serve daqui.
 *
 * Tres escolhas de proposito:
 *
 * - Cada visitante confere para si. Nada volta para o servidor nem passa a
 *   valer para os outros: resultado vindo de navegador alheio poderia ser
 *   forjado, e o site mostraria a mentira para todo mundo.
 * - Os links `?ticket=` da resposta NUNCA sao seguidos. Cada um devolve nome
 *   e CPF parcial de quem se candidatou. A contagem sai de `publicIds`.
 * - Uma consulta por vez, com pausa, e a fila para no primeiro erro. O
 *   limite do Registro.br e por IP e nao tem numero oficial; o que circula
 *   em fonte de terceiros e ~30 consultas a cada 5 minutos.
 *
 * O cabecalho `Nicbr-Resource` (exposto pelo CORS) separa liberacao,
 * leilao e nome travado. Mapeamento conferido em 10/09/2026 contra o endpoint de
 * disponibilidade, nome a nome.
 *
 * A consulta em si e window.Disputa.consultar (disputa.js, carregado antes
 * deste arquivo): um 429 ali bloqueia consultas novas por 5 min, cota
 * compartilhada com "quem disputa" e a ficha. Sem isto, o botao
 * "conferir" deixaria tentar de novo na hora, e cada toque so empurraria
 * o bloqueio do Registro.br para mais tarde.
 */
const PAUSA_RDAP = 2500;          // ms entre consultas
// Teto da conferencia que roda sozinha ao abrir, SOMANDO acompanhados e
// joias: o limite do Registro.br e por IP, e vale para o total.
const AUTOMATICAS = 20;

const fila = { itens: [], rodando: false, parada: false };

function esperar(ms) {
  return new Promise((pronto) => setTimeout(pronto, ms));
}

let reativarConferirAgendado = false;
/**
 * Sem isto, o botao "conferir" ficaria desabilitado para sempre depois do
 * bloqueio de 5 min: nada mais repinta a tabela enquanto a pessoa nao troca
 * de filtro ou de pagina. Chamado do proprio render do botao (botaoConferir),
 * deduplicado por `reativarConferirAgendado` para nao empilhar um setTimeout
 * por linha da tabela.
 */
function agendarReativacaoConferir() {
  if (reativarConferirAgendado || !window.Disputa || !window.Disputa.bloqueado || !window.Disputa.bloqueado()) return;
  reativarConferirAgendado = true;
  setTimeout(() => {
    reativarConferirAgendado = false;
    pintarTabela();
  }, window.Disputa.restanteBloqueio() + 200);
}

async function consultarRdap(dominio) {
  const r = await window.Disputa.consultar('domain/' + encodeURIComponent(dominio));
  // 404 no RDAP e resposta, nao erro: o nome nao existe no cadastro
  if (r.status === 404) return { situacao: 'LIVRE', candidatos: 0 };

  const recurso = r.recurso;
  const corpo = r.json;
  const tickets = (corpo.publicIds || [])
    .filter((p) => p && p.type === 'ticket').length;

  // Leilao aberto (`-running`) ou com a rodada ja fechada e ofertas ate o dia
  // seguinte (`-closed;date=<fim da rodada>`, visto em 16/09/2026).
  if (recurso.startsWith('competitive-release-process-')) {
    return { situacao: 'COMPETITIVO', candidatos: tickets };
  }
  // Nome que travou: status 5 do ISAVAIL. O RDAP responde 200 com um objeto
  // `domain` vazio (sem status, eventos nem tickets), que sem este teste
  // cairia em REGISTRADO (medido em 16/09/2026, limitacao S12).
  if (recurso.startsWith('release-process-waiting')) {
    return { situacao: 'AGUARDANDO_LIBERACAO', candidatos: 0 };
  }
  if (recurso.startsWith('release-process-running')) {
    return {
      situacao: tickets ? 'LIBERACAO_DISPUTADA' : 'LIBERACAO_LIVRE',
      candidatos: tickets,
    };
  }
  if (corpo.objectClassName === 'domain') {
    return { situacao: 'REGISTRADO', candidatos: 0 };
  }
  throw new Error('resposta em formato inesperado');
}

function aplicarAoVivo(it, resposta) {
  const indice = estado.dados.status.indexOf(resposta.situacao);
  if (indice >= 0) it[SS] = indice;
  it[C] = resposta.candidatos;
  it[VF] = agoraSeg();
  estado.aoVivo.add(it[D]);
  estado.doNavegador.delete(it[D]);
  estado.conferidos.set(it[D], { s: resposta.situacao, c: resposta.candidatos, t: it[VF] });
  gravarConferidos();
}

/**
 * O que a conferencia mudou, dito em texto.
 *
 * A linha nao some: se a conferencia diz "leilao", o filtro "sem
 * competicao" a tiraria da tela no mesmo instante, e quem clicou ficaria
 * procurando o nome na pagina. A linha fica onde estava, com um
 * marcador, ate a pessoa trocar de lista; a linha de status conta a
 * mudanca; e o titulo separa quem ja nao pertence a lista.
 *
 * `antes` e a fotografia tirada antes da consulta (aplicarAoVivo muda a
 * linha no lugar). Devolve o texto para a linha de status, ou null.
 */
function anotarMudanca(it, antes, naLista) {
  const depois = fotografia(it);
  const texto = antes.s ? textoDaMudanca(it[D], antes, depois) : null;
  if (!naLista) return texto;
  const teste = FILTROS[estado.filtro] || FILTROS.todos;
  const fora = !teste(it);
  if (!texto && !fora) return null;
  let marcador;
  if (antes.s && antes.s !== depois.s) marcador = `mudou: era ${rotuloSituacao(antes.s)}`;
  else if (texto) marcador = `mudou: candidatos de ${antes.c} para ${depois.c}`;
  else marcador = 'mudou';
  if (fora) marcador += ' · já não pertence a esta lista';
  estado.retidos.set(it[D], { texto: marcador, fora });
  return texto;
}

function avisar(texto) {
  const el = $('#ao-vivo');
  if (el) el.textContent = texto;
}

/** `frente`: pedido de quem clicou passa na frente da conferencia automatica. */
function enfileirar(itens, frente = false) {
  const novos = itens.filter((it) => !estado.aoVivo.has(it[D]) &&
                                     !fila.itens.includes(it));
  if (frente) fila.itens.unshift(...novos);
  else fila.itens.push(...novos);
  processarFila();
}

async function processarFila() {
  if (fila.rodando) return;
  fila.rodando = true;
  let feitos = 0;
  let comparados = 0;        // ja tinham situacao antes: da para dizer se mudou
  const mudados = [];

  while (fila.itens.length && !fila.parada) {
    const it = fila.itens.shift();
    estado.conferindo = it[D];
    pintarTabela();
    const faltam = fila.itens.length ? `, faltam ${fila.itens.length}` : '';
    avisar(`Conferindo ${it[D]} direto no Registro.br${faltam}…`);

    try {
      const antes = fotografia(it);
      const naLista = estado.atual.includes(it);
      aplicarAoVivo(it, await consultarRdap(it[D]));
      feitos++;
      if (antes.s) comparados++;
      const texto = anotarMudanca(it, antes, naLista);
      if (texto) mudados.push(texto);
      if (estado.acompanhados.has(it[D])) {
        const mudou = registrarMudanca(it);
        // com a pagina na frente, o quadro de mudancas ja basta
        if (mudou && document.visibilityState === 'hidden') notificar(it[D], mudou);
      }
    } catch (e) {
      fila.parada = true;
      fila.itens = [];
      // depois de um 429, e.message ja e a mensagem padrao do bloqueio
      // ("tente de novo em N min."); o prefixo generico so duplicaria
      avisar(window.Disputa && window.Disputa.bloqueado && window.Disputa.bloqueado()
        ? `${e.message} O resto da tabela continua com a idade que cada linha mostra.`
        : `A conferência parou: ${e.message}. O resto da tabela continua `
          + 'com a idade que cada linha mostra.');
    }
    estado.conferindo = null;
    pintarContagens();
    aplicar({ manterOrdem: true });

    if (fila.itens.length && !fila.parada) await esperar(PAUSA_RDAP);
  }

  if (!fila.parada && feitos) {
    const quantos = feitos === 1 ? '1 nome conferido' : `${feitos} nomes conferidos`;
    let msg = `${quantos} agora, direto no Registro.br, do seu navegador.`;
    if (mudados.length) {
      const mais = mudados.length > 3 ? `; e mais ${mudados.length - 3}` : '';
      msg += ` Mudou: ${mudados.slice(0, 3).join('; ')}${mais}. Quem saiu da lista `
           + 'fica marcado nela até você trocar de filtro.';
    } else if (comparados === feitos) {
      msg += ' Nada mudou.';
    }
    avisar(msg);
  }
  fila.rodando = false;
}

/**
 * Foi conferido ha menos de REVER_MINUTOS (na propria aba ou salvo no
 * aparelho, tanto faz: os dois atualizam it[VF]). Usado para nao reconferir
 * de novo ao abrir a pagina.
 */
function recenteConferido(it) {
  return Boolean(it[VF]) && agoraSeg() - it[VF] < REVER_MINUTOS * 60;
}

/**
 * Conferido sozinho ao abrir: primeiro os acompanhados (o mais velho antes),
 * depois as joias, que sao o destaque e ja apareceram erradas. O teto e da
 * soma, e protege o limite por IP de quem visita.
 *
 * Quem ja foi conferido ha menos de REVER_MINUTOS fica fora: sem isto, ir e
 * voltar da inicial (Dominios -> Perguntas -> Dominios) reconferiria os mesmos
 * nomes a cada volta, porque estado.aoVivo (em memoria) zera a cada
 * carregamento da pagina e nao enxerga o que o localStorage 'conferidos' ja
 * sabe. A lista compartilhada fica fora do filtro: quem abriu um link quer
 * o numero fresco, mesmo que outra aba tenha conferido ha pouco.
 */
function conferirAoAbrir() {
  const acomp = [...estado.acompanhados.entries()]
    .sort((a, b) => (a[1].t || 0) - (b[1].t || 0))
    .map(([dominio]) => garantirLinha(dominio))
    .filter((it) => !recenteConferido(it));
  const joias = visiveis().filter(FILTROS.joias).filter((it) => !recenteConferido(it)).sort((a, b) => b[N] - a[N]);
  // quem abriu uma lista compartilhada quer os numeros dela, frescos
  const compartilhada = estado.filtro === 'lista' ? [...estado.lista].map(garantirLinha) : [];
  const lista = [...new Set([...compartilhada, ...acomp, ...joias])].slice(0, AUTOMATICAS);
  if (lista.length) enfileirar(lista);
}

// ----------------------------------------------------- conferido guardado

/*
 * O que voce conferiu fica no aparelho (localStorage), como a estrela.
 *
 * Sem isto, recarregar a pagina desfaria a conferencia: o nome que voce
 * acabou de ver em leilao voltaria como "sem competicao, ha 21 h". A leitura do navegador vale enquanto
 * for mais nova que a do servidor (comparada nome a nome, pelo carimbo de
 * cada linha, nunca pelo gerado_em) e por no maximo 7 dias, uma rodada.
 * Continua valendo so para quem conferiu: nada volta ao servidor.
 */
const CHAVE_CONFERIDOS = 'conferidos';
const VALIDADE_CONFERIDO = 7 * 24 * 3600;

function lerConferidos() {
  const agora = agoraSeg();
  const status = estado.dados.status;
  try {
    const bruto = JSON.parse(localStorage.getItem(CHAVE_CONFERIDOS) || '{}');
    return new Map(Object.entries(bruto).filter(([d, v]) =>
      /^[a-z0-9.-]+$/.test(d) && v && typeof v === 'object'
      && status.includes(v.s)
      && Number.isInteger(v.t) && v.t > agora - VALIDADE_CONFERIDO && v.t <= agora + 300
      && Number.isInteger(v.c) && v.c >= 0));
  } catch (e) {
    return new Map();      // navegacao anonima ou storage bloqueado
  }
}

function gravarConferidos() {
  try {
    localStorage.setItem(CHAVE_CONFERIDOS,
                         JSON.stringify(Object.fromEntries(estado.conferidos)));
  } catch (e) { /* vale so para esta aba */ }
}

/** Aplica a leitura guardada a uma linha, se for mais nova que a do servidor. */
function restaurarConferido(it) {
  const v = estado.conferidos.get(it[D]);
  if (!v) return;
  // O servidor ja passou na frente, ou a lista oficial de leiloes (baixada a
  // cada execucao, sem mexer no carimbo da linha) diz leilao e a leitura
  // guardada e de antes de o nome entrar nele. A lista oficial manda.
  const superada = v.t <= (it[VF] || 0)
    || (it[EL] === 1 && v.s !== 'COMPETITIVO');
  if (superada) {
    estado.conferidos.delete(it[D]);
    return;
  }
  it[SS] = estado.dados.status.indexOf(v.s);
  it[C] = v.c;
  it[VF] = v.t;
  estado.doNavegador.add(it[D]);
}

export {
  PAUSA_RDAP,
  AUTOMATICAS,
  fila,
  esperar,
  reativarConferirAgendado,
  agendarReativacaoConferir,
  consultarRdap,
  aplicarAoVivo,
  anotarMudanca,
  avisar,
  enfileirar,
  processarFila,
  recenteConferido,
  conferirAoAbrir,
  CHAVE_CONFERIDOS,
  VALIDADE_CONFERIDO,
  lerConferidos,
  gravarConferidos,
  restaurarConferido,
};
