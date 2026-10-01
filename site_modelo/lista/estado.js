// Os indices do JSON compacto, o estado da pagina e as tabelas de filtro e situacao.
import { rodadaFechada } from './fase.js';

const $ = (s) => document.querySelector(s);
// Quantos nomes por pagina. Sem "todos": sem competicao passa
// de 15 mil linhas e a rodada inteira de 125 mil, o que trava o celular.
const TAMANHOS_PAGINA = [25, 50, 100, 250, 500];
const CHAVE_POR_PAGINA = 'por_pagina';

// indices do json compacto (ver garimpo/casos/instantaneo.py): [dominio,
// status, candidatos, nota, elegivel, motivos[], marca, em_leilao,
// verificado_em em segundos UTC, classe de frescor]
const D = 0, SS = 1, C = 2, N = 3, E = 4, M = 5, MK = 6, EL = 7, VF = 8, CL = 9;
// quando o primeiro concorrente chegou e quando o ultimo visivel
// chegou (epoch, 0 = desconhecido), estimados pelo ritmo da rodada
const CH1 = 11, CH2 = 12;
const CT = 10;   // categorias de negocio, em bits (dominio/categorias.py)
// [referentes, muito citados] do indice de links (grafo do CommonCrawl), ou 0
const LK = 13;
// o sinal ruim mais recente do Cloudflare Intel ("Apostas"), ou 0
const SN = 14;
// o rotulo normalizado para a busca, calculado uma vez por linha
// (fica so na memoria; nada serializa a linha inteira)
const RN = 15;
// A partir de quantas letras a busca olha a rodada inteira
const MINIMO_BUSCA_RODADA = 3;

const estado = {
  dados: null,
  filtro: null,           // escolhido por filtroInicial(): o primeiro que tem o que mostrar
  busca: '',
  ordem: 'nota',
  extensao: '',
  categoria: '',          // indice do bit, como texto; '' = todos os ramos
  marca: '',              // '' todas; 'sem' esconde risco; 'so' apenas possivel marca
  situacao: '',           // '' todas; chave de SITUACOES (so em lista que mistura)
  lista: new Set(),       // lista compartilhada por link (?lista=); nunca gravada
  pagina: 0,
  porPagina: 100,
  atual: [],
  porDominio: new Map(),
  aoVivo: new Set(),      // conferidos agora, neste navegador
  conferidos: new Map(),  // dominio -> {s, c, t}: conferido neste aparelho, guardado
  doNavegador: new Set(), // linhas cuja leitura veio do guardado, nao do servidor
  retidos: new Map(),     // dominio -> {texto, fora}: mudou ao conferir, fica na lista
  conferindo: null,       // o que esta sendo consultado neste instante
  acompanhados: new Map(),  // dominio -> ultima situacao vista, so neste aparelho
  mudancas: new Map(),    // dominio -> {de, texto}: uma linha por nome
  lembretesServidos: new Set(),  // leiloes com .ics gerado pelo exportador
  modoBusca: false,       // body.modo-busca: so caixa, resumo, filtros e lista
  antesDaBusca: null,     // {filtro, cartaoEscolhido} de quando o modo ligou
};

// os indices de status dependem da ordem em exportar_site.py
let iSemComp, iDisputado, iLeilao, iLivre, iRegistrado, iAguardando;

/** Os indices de situacao, pela lista `status` do dados.json. */
function definirIndices(s) {
  iSemComp = s.indexOf('LIBERACAO_LIVRE');
  iDisputado = s.indexOf('LIBERACAO_DISPUTADA');
  iLeilao = s.indexOf('COMPETITIVO');
  iLivre = s.indexOf('LIVRE');
  iRegistrado = s.indexOf('REGISTRADO');
  iAguardando = s.indexOf('AGUARDANDO_LIBERACAO');
}

const NOMES_FILTRO = {
  joias: 'Joias',
  sem_competicao: 'Sem competição',
  disputados: 'Disputados',
  leilao: 'Em leilão',
  elegiveis: 'Elegíveis ao leilão',
  livres: 'Livres para registro imediato',
  todos: 'Todos consultados',
  rodada: 'Toda a rodada',
  acompanhados: 'Acompanhando',
  aguardando: 'Voltam na próxima rodada',
  lista: 'Lista compartilhada',
};

// Cartoes sem .ajuda propria (a lista compartilhada nao tem cartao)
const EXPLICA_FILTRO = {
  lista: 'Domínios que alguém compartilhou com você por link. A estrela '
       + 'acompanha no seu aparelho; nada desta lista é guardado sem você pedir.',
};

// Listas em que as situacoes se misturam: so nelas aparece o filtro de
// situacao
const LISTAS_MISTAS = new Set(['acompanhados', 'todos', 'rodada', 'elegiveis', 'lista']);

// status do JSON -> chave do filtro de situacao
const SITUACOES = {
  LIBERACAO_LIVRE: 'sem_competicao',
  LIBERACAO_DISPUTADA: 'disputado',
  LIVRE_COM_TICKET: 'disputado',
  COMPETITIVO: 'leilao',
  LIVRE: 'livre',
  AGUARDANDO_LIBERACAO: 'aguardando',
  REGISTRADO: 'registrado',
  INDISPONIVEL: 'registrado',
};

// Ao abrir, o primeiro destes que tem o que mostrar. Joias e o melhor
// cartao, mas passa a maior parte da rodada vazio: todo elegivel entra em
// leilao em ~30 h. Abrir numa lista vazia da a impressao de site quebrado.
const PREFERENCIA = ['joias', 'sem_competicao', 'disputados', 'leilao', 'todos'];
// Com a rodada fechada o que interessa e o que da para fazer
// agora: registrar o que ficou livre, ou esperar o que travou.
// "elegiveis" no lugar de "leilao": fechada a rodada, "Em leilao" esvazia
// conforme a varredura reclassifica, e "Foram a leilao" (os elegiveis) e a
// lista estavel da rodada.
const PREFERENCIA_FECHADA = ['livres', 'aguardando', 'sem_competicao', 'elegiveis', 'todos'];

// Nome da rodada que nao esta no instantaneo: so nota, sem situacao
const NAO_VERIFICADO = -1;

const FILTROS = {
  joias: (it) => it[E] === 1 && it[SS] === iSemComp,
  sem_competicao: (it) => it[SS] === iSemComp,
  disputados: (it) => it[SS] === iDisputado,
  leilao: (it) => it[SS] === iLeilao,
  elegiveis: (it) => it[E] === 1,
  livres: (it) => it[SS] === iLivre,
  todos: (it) => it[SS] !== NAO_VERIFICADO,
  rodada: () => true,
  acompanhados: (it) => estado.acompanhados.has(it[D]),
  lista: (it) => estado.lista.has(it[D]),
  // Travou: status 5 do ISAVAIL, ou disputado (2+ tickets) lido antes do
  // fechamento de um nome que nao e elegivel. Ticket nao se cancela (S3) e
  // dois ou mais no fim travam o nome (regra oficial, S12), entao a leitura
  // antiga ja diz o desfecho; elegivel vai a leilao e fica de fora.
  aguardando: (it) => it[SS] === iAguardando
    || (rodadaFechada() && it[SS] === iDisputado && it[E] !== 1),
};

export {
  $,
  TAMANHOS_PAGINA,
  CHAVE_POR_PAGINA,
  D,
  SS,
  C,
  N,
  E,
  M,
  MK,
  EL,
  VF,
  CL,
  CH1,
  CH2,
  CT,
  LK,
  SN,
  RN,
  MINIMO_BUSCA_RODADA,
  estado,
  iSemComp,
  iDisputado,
  iLeilao,
  iLivre,
  iRegistrado,
  iAguardando,
  NOMES_FILTRO,
  EXPLICA_FILTRO,
  LISTAS_MISTAS,
  SITUACOES,
  PREFERENCIA,
  PREFERENCIA_FECHADA,
  NAO_VERIFICADO,
  FILTROS,
  definirIndices,
};
