// A contagem regressiva ate o proximo marco da rodada.
import { $, estado } from './estado.js';
import { pintarFase } from './fase.js';
import { agoraMs } from './util.js';

// ------------------------------------------------------------------ relogio

/*
 * A contagem regressiva do inicio. O build grava a caixa com as
 * tres datas da rodada que vem (garimpo/web/paginas.relogio_da_pagina); aqui
 * o relogio anda uma vez por segundo, so com a aba visivel, e a pessoa troca
 * o alvo tocando numa data. Os digitos ficam fora do leitor de tela (um
 * anuncio por segundo); ele le o rotulo e a data por extenso.
 */
const relogio = { alvo: null, timer: null };

function marcosDoRelogio() {
  return [...document.querySelectorAll('#relogio .relogio-marco')];
}

function escolherMarco(botao) {
  const caixa = document.querySelector('#relogio');
  for (const b of marcosDoRelogio()) b.setAttribute('aria-pressed', String(b === botao));
  relogio.alvo = botao;
  $('#relogio-rotulo').textContent = botao.dataset.rotulo;
  const quando = $('#relogio-quando');
  quando.textContent = botao.dataset.texto;
  quando.setAttribute('datetime', botao.dataset.alvo);
  // a barra vai do fim da rodada anterior ate a abertura; para o fechamento,
  // da abertura ate ele
  const abre = marcosDoRelogio().find((b) => b.dataset.marco === 'abre');
  caixa.dataset.desdeAlvo = botao.dataset.marco === 'fecha' && abre
    ? abre.dataset.alvo : caixa.dataset.desde;
  caixa.dataset.marco = botao.dataset.marco;
  tiqueDoRelogio();
}

/** O primeiro que ainda nao passou, preferindo a abertura (o que a pessoa quer saber). */
function marcoPadrao() {
  const agora = agoraMs();
  const futuros = marcosDoRelogio().filter((b) => new Date(b.dataset.alvo) > agora);
  return futuros.find((b) => b.dataset.marco !== 'lista') || futuros[0] || null;
}

function tiqueDoRelogio() {
  const caixa = document.querySelector('#relogio');
  if (!caixa || !relogio.alvo) return;
  const agora = agoraMs();
  for (const b of marcosDoRelogio()) {
    const passou = new Date(b.dataset.alvo) <= agora;
    b.classList.toggle('passou', passou);
    b.disabled = passou;
  }
  const alvo = new Date(relogio.alvo.dataset.alvo).getTime();
  let resta = Math.floor((alvo - agora) / 1000);
  if (resta < 0) {
    // chegou a hora: o alvo pula para a data seguinte, e a fase da pagina muda
    const seguinte = marcoPadrao();
    if (estado.dados) pintarFase(estado.dados.rodada);
    if (seguinte && seguinte !== relogio.alvo) {
      escolherMarco(seguinte);
    } else {
      // a pagina e de antes da rodada virar: o proximo build traz as datas
      // novas. Sem alvo, andarRelogio nao reagenda o tique (senao a caixa
      // sumiria e o relogio seguiria repintando a fase a cada segundo)
      caixa.classList.add('escondido');
      relogio.alvo = null;
      pararRelogio();
    }
    return;
  }
  const partes = { d: Math.floor(resta / 86400) };
  resta %= 86400;
  partes.h = Math.floor(resta / 3600);
  partes.m = Math.floor((resta % 3600) / 60);
  partes.s = resta % 60;
  for (const [u, n] of Object.entries(partes)) {
    const el = document.getElementById(`relogio-${u}`);
    const texto = u === 'd' ? String(n) : String(n).padStart(2, '0');
    if (el && el.textContent !== texto) el.textContent = texto;
  }
  caixa.classList.toggle('reta-final', alvo - agora < 24 * 3600000);
  const desde = new Date(caixa.dataset.desdeAlvo || caixa.dataset.desde).getTime();
  const barra = $('#relogio-barra');
  if (barra && alvo > desde) {
    const feito = Math.min(1, Math.max(0, (agora - desde) / (alvo - desde)));
    barra.style.setProperty('--feito', (feito * 100).toFixed(2) + '%');
  }
}

function pararRelogio() {
  clearTimeout(relogio.timer);
  relogio.timer = null;
}

function andarRelogio() {
  pararRelogio();
  // no modo busca o relogio esta recolhido (display: none): nao repinta
  if (document.hidden || document.body.classList.contains('modo-busca') || !relogio.alvo) return;
  tiqueDoRelogio();
  if (!relogio.alvo) return;       // o ultimo marco passou: o relogio para aqui
  // alinhado a virada do segundo, para os digitos nao pularem
  relogio.timer = setTimeout(andarRelogio, 1000 - (Date.now() % 1000) + 5);
}

function iniciarRelogio() {
  if (!document.querySelector('#relogio')) return;
  const inicial = marcoPadrao();
  if (!inicial) {
    document.querySelector('#relogio').classList.add('escondido');
    return;
  }
  for (const b of marcosDoRelogio()) {
    b.addEventListener('click', () => {
      escolherMarco(b);
      andarRelogio();
    });
  }
  escolherMarco(inicial);
  andarRelogio();
  document.addEventListener('visibilitychange', andarRelogio);
}

export {
  relogio,
  marcosDoRelogio,
  escolherMarco,
  marcoPadrao,
  tiqueDoRelogio,
  pararRelogio,
  andarRelogio,
  iniciarRelogio,
};
