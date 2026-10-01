// A estrela: acompanhar um nome e avisar quando ele muda.
import { AUTOMATICAS, avisar, enfileirar, fila, restaurarConferido } from './conferencia.js';
import { levarALista } from './dobras.js';
import { $, C, D, NAO_VERIFICADO, SS, VF, estado, iLeilao } from './estado.js';
import { aplicar } from './filtros.js';
import { SELOS } from './linha.js';
import { pintarContagens } from './painel.js';
import { escolherFiltro } from './resumo.js';

// ------------------------------------------------------------ acompanhados

/*
 * "Me avisa se isto mudar", sem servidor.
 *
 * A estrela guarda o nome e a ultima situacao vista NESTE aparelho
 * (localStorage). Na volta, a pagina compara com o instantaneo novo e
 * confere ao vivo no RDAP; o que mudou vira um quadro no topo. Com a pagina
 * aberta, a lista e reconferida a cada 20 minutos e, se a pessoa deixou,
 * sai um aviso do sistema.
 *
 * Aviso com a pagina FECHADA exigiria servidor guardando a inscricao de
 * cada aparelho (Web Push) ou um bot com a lista de quem segue o que. Nada disto
 * volta ao servidor: a mesma regra da conferencia ao vivo.
 */
const CHAVE_ACOMPANHADOS = 'acompanhados';
const REVER_MINUTOS = 20;
const provisorios = new WeakSet();   // linhas criadas so para um acompanhado

function lerAcompanhados() {
  try {
    const bruto = JSON.parse(localStorage.getItem(CHAVE_ACOMPANHADOS) || '{}');
    return new Map(Object.entries(bruto).filter(([d]) => /^[a-z0-9.-]+$/.test(d)));
  } catch (e) {
    return new Map();      // navegacao anonima ou storage bloqueado
  }
}

function gravarAcompanhados() {
  try {
    localStorage.setItem(CHAVE_ACOMPANHADOS,
                         JSON.stringify(Object.fromEntries(estado.acompanhados)));
  } catch (e) { /* vale so para esta aba */ }
}

/** Linha de um nome que nao esta no instantaneo: so o nome, a conferir. */
function garantirLinha(dominio) {
  let it = estado.porDominio.get(dominio);
  if (!it) {
    it = [dominio, NAO_VERIFICADO, null, 0, 0, [], 0, 0, 0, -1, 0];
    provisorios.add(it);
    estado.porDominio.set(dominio, it);
    restaurarConferido(it);
  }
  return it;
}

/** O que se guarda de cada acompanhado. Leilao mostra "2 ou mais". */
function fotografia(it) {
  if (it[SS] === NAO_VERIFICADO) return { s: null, c: null, t: 0 };
  const s = estado.dados.status[it[SS]];
  const c = it[SS] === iLeilao ? Math.max(it[C] || 0, 2) : (it[C] ?? null);
  return { s, c, t: it[VF] || 0 };
}

function rotuloSituacao(nome) {
  return (SELOS[nome] || [null, 'não verificado'])[1];
}

function textoDaMudanca(dominio, de, para) {
  if (de.s !== para.s) {
    let texto = `${dominio} passou de ${rotuloSituacao(de.s)} para ${rotuloSituacao(para.s)}`;
    if (para.c && para.s !== 'REGISTRADO' && para.s !== 'LIVRE') {
      texto += ` (${para.c} candidatos)`;
    }
    return texto;
  }
  if (de.c != null && para.c != null && de.c !== para.c) {
    return `${dominio}: candidatos visíveis foram de ${de.c} para ${para.c}`;
  }
  return null;
}

/**
 * Compara com o guardado, guarda o novo e devolve o texto se ESTA leitura
 * mudou algo. O quadro fica com uma linha por nome, do primeiro valor
 * visto na visita ao ultimo: o instantaneo diz 7 e a conferencia ao vivo
 * diz 9 vira "de 2 para 9", nao duas linhas.
 */
function registrarMudanca(it) {
  const antes = estado.acompanhados.get(it[D]);
  const agora = fotografia(it);
  if (!antes || !agora.s) return null;
  estado.acompanhados.set(it[D], agora);
  gravarAcompanhados();
  if (!antes.s) return null;          // primeira leitura nao e mudanca

  const de = (estado.mudancas.get(it[D]) || { de: antes }).de;
  const texto = textoDaMudanca(it[D], de, agora);
  if (texto) estado.mudancas.set(it[D], { de, texto });
  else estado.mudancas.delete(it[D]);   // mudou e voltou: nada a contar
  pintarMudancas();
  return textoDaMudanca(it[D], antes, agora) ? texto : null;
}

/** Na abertura: o instantaneo novo ja pode contar o que mudou, sem rede. */
function mudancasDesdeAUltimaVisita() {
  for (const [dominio, antes] of estado.acompanhados) {
    const it = estado.porDominio.get(dominio);
    if (it && it[SS] !== NAO_VERIFICADO && (it[VF] || 0) > (antes.t || 0)) {
      registrarMudanca(it);
    }
  }
}

function pintarMudancas() {
  const caixa = $('#mudancas');
  if (!caixa) return;
  if (!estado.mudancas.size) {
    caixa.classList.add('escondido');
    caixa.replaceChildren();
    return;
  }
  const titulo = document.createElement('p');
  titulo.className = 'mudancas-titulo';
  titulo.textContent = estado.mudancas.size === 1
    ? 'Um nome que você acompanha mudou'
    : `${estado.mudancas.size} nomes que você acompanha mudaram`;
  const lista = document.createElement('ul');
  for (const { texto } of estado.mudancas.values()) {
    const li = document.createElement('li');
    li.textContent = texto;
    lista.append(li);
  }
  const ver = document.createElement('button');
  ver.type = 'button';
  ver.textContent = 'Ver acompanhados';
  ver.addEventListener('click', () => { escolherFiltro('acompanhados'); levarALista(); });
  const ok = document.createElement('button');
  ok.type = 'button';
  ok.className = 'discreto';
  ok.textContent = 'Dispensar';
  ok.addEventListener('click', () => { estado.mudancas.clear(); pintarMudancas(); });
  const acoes = document.createElement('div');
  acoes.className = 'acoes';
  acoes.append(ver, ok);
  caixa.replaceChildren(titulo, lista, acoes);
  caixa.classList.remove('escondido');
}

function podeNotificar() {
  return 'Notification' in window && Notification.permission === 'granted';
}

/**
 * O Chrome do Android recusa `new Notification()`: la o aviso so sai pelo
 * service worker. Onde nao ha nenhum dos dois (Safari fora da tela de
 * inicio), fica o quadro de mudancas.
 */
async function notificar(dominio, texto) {
  if (!podeNotificar()) return;
  const opcoes = { body: texto, tag: `liberado-${dominio}`, lang: 'pt-BR' };
  try {
    const reg = navigator.serviceWorker && await navigator.serviceWorker.getRegistration();
    if (reg) {
      await reg.showNotification('Um nome que você acompanha mudou', opcoes);
      return;
    }
    new Notification('Um nome que você acompanha mudou', opcoes);
  } catch (e) { /* sem aviso do sistema; o quadro de mudancas continua */ }
}

function registrarServico() {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

/** So pede permissao depois de a pessoa tocar numa estrela, nunca ao abrir. */
async function pedirPermissao() {
  if (!('Notification' in window) || Notification.permission !== 'default') return;
  registrarServico();
  try { await Notification.requestPermission(); } catch (e) { /* recusou */ }
}

let revisao = null;

function iniciarRevisao() {
  if (revisao || !estado.acompanhados.size) return;
  revisao = setInterval(() => {
    if (!estado.acompanhados.size) return;
    const itens = [...estado.acompanhados.keys()].slice(0, AUTOMATICAS).map(garantirLinha);
    for (const it of itens) estado.aoVivo.delete(it[D]);   // confere de novo
    fila.parada = false;
    enfileirar(itens);
  }, REVER_MINUTOS * 60 * 1000);
}

function alternarAcompanhar(dominio) {
  const it = garantirLinha(dominio);
  if (estado.acompanhados.has(dominio)) {
    estado.acompanhados.delete(dominio);
    avisar(`Você deixou de acompanhar ${dominio}.`);
  } else {
    estado.acompanhados.set(dominio, fotografia(it));
    avisar(`Acompanhando ${dominio}, neste aparelho. Quando você voltar, a página `
         + 'confere e mostra o que mudou; com ela aberta, confere a cada '
         + `${REVER_MINUTOS} minutos.`);
    pedirPermissao();
    iniciarRevisao();
  }
  gravarAcompanhados();
  pintarContagens();
  aplicar();
}

export {
  CHAVE_ACOMPANHADOS,
  REVER_MINUTOS,
  provisorios,
  lerAcompanhados,
  gravarAcompanhados,
  garantirLinha,
  fotografia,
  rotuloSituacao,
  textoDaMudanca,
  registrarMudanca,
  mudancasDesdeAUltimaVisita,
  pintarMudancas,
  podeNotificar,
  notificar,
  registrarServico,
  pedirPermissao,
  revisao,
  iniciarRevisao,
  alternarAcompanhar,
};
