// A busca: normalizar o termo, decidir o modo e casar um nome.
import { $, D, MINIMO_BUSCA_RODADA, RN, estado } from './estado.js';
import { aplicar } from './filtros.js';
import { andarRelogio } from './relogio.js';

// -------------------------------------------------------------------- busca

/*
 * A busca. Casa so no rotulo, sem a extensao: casar no dominio inteiro
 * faria "dev" achar apolo.dev.br. Sem acento, caixa, hifen
 * nem espaco, dos dois lados: "Café" acha cafe, "pet-shop" acha petshop.
 * O modo vai no proprio texto, e por isso no ?busca= do link: "pet*" comeca
 * com, "*pet" termina com, "pet" entre aspas e o nome exato. Com ponto
 * ("pizza.com.br", "*.dev.br") compara o dominio inteiro.
 */
function normalizarTermo(texto) {
  return String(texto || '').toLowerCase().normalize('NFD')
    .replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9.]/g, '');
}

function modoDaBusca(texto) {
  let t = String(texto || '').trim().replace(/^www\./i, '');
  let modo = 'contem';
  const aspas = /^["“”](.*)["“”]$/.exec(t);
  if (aspas) {
    modo = 'exato';
    t = aspas[1];
  } else if (t.endsWith('*') && !t.startsWith('*')) {
    modo = 'comeca';
  } else if (t.startsWith('*') && !t.endsWith('*')) {
    modo = 'termina';
  }
  // o que sobra do texto, para a ficha: sem aspas nem asterisco
  const bruto = t.replace(/["“”*]/g, '').trim().toLowerCase().replace(/^www\./, '');
  const termo = normalizarTermo(bruto);
  return { termo, modo, bruto, dominio: termo.includes('.') };
}

function rotuloDaLinha(it) {
  if (it[RN] === undefined) {
    const p = it[D].indexOf('.');
    it[RN] = normalizarTermo(p === -1 ? it[D] : it[D].slice(0, p));
  }
  return it[RN];
}

function casaBusca(it, q) {
  const alvo = q.dominio ? rotuloDaLinha(it) + it[D].slice(it[D].indexOf('.')) : rotuloDaLinha(it);
  if (q.modo === 'exato') return alvo === q.termo;
  if (q.modo === 'comeca') return alvo.startsWith(q.termo);
  if (q.modo === 'termina') return alvo.endsWith(q.termo);
  return alvo.includes(q.termo);
}

/**
 * A busca olha a rodada inteira? Com 3 letras ou mais, enquanto a pessoa
 * nao escolheu um cartao: quem digita "pizza" quer os nomes da rodada com a
 * palavra, nao so os do cartao que abriu sozinho.
 */
function buscaNaRodada() {
  if (estado.cartaoEscolhido || ['acompanhados', 'lista'].includes(estado.filtro)) return false;
  return modoDaBusca(estado.busca).termo.length >= MINIMO_BUSCA_RODADA;
}

/*
 * O modo busca. Com 3 letras ou mais, a fase, o relogio, os
 * passos e os cartoes se recolhem (extra.css, body.modo-busca) e resumo,
 * filtros e lista ficam logo abaixo da caixa: sem isso, a 390 px a primeira
 * linha fica 1.640 px abaixo do campo. Liga nas 3 letras e so desliga com a
 * caixa vazia: apagar ate 2 letras nao traz o painel de volta a cada tecla.
 * O limite e o da busca na rodada, mas nao buscaNaRodada(): o toque num chip
 * escolhe um cartao e o painel nao pode voltar no meio da busca. Fica de
 * fora em Acompanhando e na lista compartilhada, que nao tem resumo.
 */
function buscaPedeModo(texto, filtro) {
  return modoDaBusca(texto).termo.length >= MINIMO_BUSCA_RODADA
    && !['acompanhados', 'lista'].includes(filtro);
}

/** Liga ou desliga o modo; ao sair, volta o cartao de antes da busca. */
function pintarModoBusca() {
  const antes = estado.modoBusca;
  if (!estado.busca.trim() || ['acompanhados', 'lista'].includes(estado.filtro)) {
    estado.modoBusca = false;
  } else if (buscaPedeModo(estado.busca, estado.filtro)) {
    estado.modoBusca = true;
  }
  // sempre: a classe pode ter entrado antes do JSON, pelo endereco
  document.body.classList.toggle('modo-busca', estado.modoBusca);
  if (!estado.modoBusca) document.body.classList.remove('modo-busca-link');
  if (antes === estado.modoBusca) return;
  // fora do modo, a proxima entrada e anunciada de novo: o resumo sai cedo
  // com a caixa vazia e nao chegaria a zerar isso
  if (!estado.modoBusca) estado.modoBuscaAnunciado = false;
  if (estado.modoBusca) {
    estado.antesDaBusca = { filtro: estado.filtro, cartaoEscolhido: estado.cartaoEscolhido };
  } else if (estado.antesDaBusca && !['acompanhados', 'lista'].includes(estado.filtro)) {
    // o chip escolhido dentro da busca era filtro da busca, nao do painel
    estado.filtro = estado.antesDaBusca.filtro;
    estado.cartaoEscolhido = estado.antesDaBusca.cartaoEscolhido;
    estado.antesDaBusca = null;
  }
  andarRelogio();   // escondido, o relogio para; de volta, anda de novo
}

/** A caixa vazia: o painel volta, com o cartao de antes e os filtros da pessoa. */
function limparBusca() {
  estado.busca = '';
  $('#busca').value = '';
  estado.pagina = 0;
  aplicar();
  const st = $('#resumo-busca-status');
  if (st) st.textContent = 'Busca limpa: de volta ao início da página.';
  $('#busca').focus({ preventScroll: true });
}

/** O cartao que a lista mostra: "Toda a rodada" enquanto a busca olha a rodada. */
function filtroEfetivo() {
  return buscaNaRodada() ? 'rodada' : estado.filtro;
}

export {
  normalizarTermo,
  modoDaBusca,
  rotuloDaLinha,
  casaBusca,
  buscaNaRodada,
  buscaPedeModo,
  pintarModoBusca,
  limparBusca,
  filtroEfetivo,
};
