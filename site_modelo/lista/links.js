// Quem aponta para um nome: a etiqueta "N links" abre um dialogo com todos
// os dominios, lidos do indice docs/historico/referentes/ (arquivar_links.py,
// grafo de dominios do CommonCrawl), na fatia do proprio nome.
import { esc, num } from './util.js';

const REFERENTES = '/dados/historico/referentes/';
const MARCAS = {
  c: 'Muito citados',
  '': 'Outros sites',
  p: 'Plataformas onde qualquer um publica',
};
// Explicacao so onde o rotulo nao basta; "Outros sites" dispensa, salvo o
// convite a conferir spam, com a mesma regra da ficha (ficha.js, MUITOS).
const EXPLICA = {
  c: 'Entre o 1 milhão de domínios mais citados da web, como portais, jornais e universidades.',
  '': '',
  p: 'Blogspot, redes sociais e afins: qualquer um publica lá, e o link não mostra que alguém conhecido citou o nome.',
};
const MUITOS = 10;
const SPAM = 'Muitos links e nenhum de domínio muito citado: vale conferir se não vêm de spam.';

let dialogo = null;
let aberto = '';

/** O mesmo FNV-1a de 32 bits de garimpo/dominio/passagens.py (um teste compara). */
function fnv1a32(texto) {
  let h = 0x811c9dc5;
  for (const byte of new TextEncoder().encode(texto)) {
    h ^= byte;
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

function fatiaDe(dominio) {
  return (fnv1a32(dominio) % 256).toString(16).padStart(2, '0');
}

/** [[marca, dominio], ...] de quem aponta para `dominio`; [] sem linha no indice. */
async function referentesDe(dominio) {
  const r = await fetch(`${REFERENTES}${fatiaDe(dominio)}.txt`);
  if (!r.ok) return [];
  const linha = (await r.text()).split('\n').find((l) => l.startsWith(dominio + '\t'));
  if (!linha) return [];
  return linha.slice(dominio.length + 1).split(',').filter(Boolean).map((parte) => {
    const i = parte.indexOf(':');
    return [parte.slice(0, i), parte.slice(i + 1)];
  });
}

/**
 * O HTML dos grupos: muito citados, outros e plataformas, nessa ordem. A
 * classe da lista diz o peso (citados em destaque, plataformas apagadas);
 * `titulo` e o nivel do cabecalho, h3 no dialogo e h4 dentro da ficha.
 */
function grupos(itens, titulo = 'h3') {
  const por = { c: [], '': [], p: [] };
  for (const [marca, nome] of itens) (por[marca] || por['']).push(nome);
  const spam = !por.c.length && por[''].length >= MUITOS;
  const classe = { c: ' citados', '': '', p: ' plataformas' };
  return ['c', '', 'p'].filter((m) => por[m].length).map((m) => {
    const explica = m === '' && spam ? SPAM : EXPLICA[m];
    return `<section class="links-grupo"><${titulo} class="links-titulo">${esc(MARCAS[m])} `
      + `<span>(${num(por[m].length)})</span></${titulo}>`
      + (explica ? `<p class="links-explica">${esc(explica)}</p>` : '')
      + `<ul class="links-lista${classe[m]}">${por[m].map((n) => `<li>${esc(n)}</li>`).join('')}</ul></section>`;
  }).join('');
}

function montar() {
  if (dialogo) return dialogo;
  dialogo = document.createElement('dialog');
  dialogo.className = 'dialogo dialogo-disputa dialogo-links';
  dialogo.setAttribute('aria-labelledby', 'links-titulo');
  // tres faixas: o cabecalho e as acoes ficam, so a lista rola (pode ter
  // milhares). O foco inicial e o titulo (autofocus), nao o primeiro botao:
  // o leitor de tela comeca pelo nome, e o botao nao abre com anel de foco
  // como se fosse a acao sugerida. O Tab segue dali para as acoes.
  dialogo.innerHTML = `
    <div class="dialogo-corpo">
      <div class="links-cabeca">
        <h2 id="links-titulo" tabindex="-1" autofocus>Quem aponta</h2>
        <p id="links-resumo" class="disputa-resumo" aria-live="polite"></p>
      </div>
      <div class="links-rolagem">
        <div id="links-grupos"></div>
        <p class="disputa-nota links-fonte">Do grafo de links do CommonCrawl, que lê uma amostra
          da web: a lista real pode ser maior. Os nomes ficam sem link de propósito, para
          ninguém cair num site de spam sem querer.</p>
      </div>
      <div class="disputa-acoes links-acoes">
        <a id="links-ficha" class="botao-link" rel="nofollow" href="#">Ver a ficha completa</a>
        <button type="button" id="links-fechar" class="discreto">Fechar</button>
      </div>
    </div>`;
  document.body.appendChild(dialogo);
  dialogo.addEventListener('click', (e) => { if (e.target === dialogo) dialogo.close(); });
  dialogo.querySelector('#links-fechar').addEventListener('click', () => dialogo.close());
  return dialogo;
}

async function abrirLinks(dominio) {
  const d = montar();
  aberto = dominio;
  d.querySelector('#links-titulo').textContent = `Quem aponta para ${dominio}`;
  d.querySelector('#links-resumo').textContent = 'Carregando…';
  d.querySelector('#links-grupos').innerHTML = '';
  d.querySelector('.links-rolagem').scrollTop = 0;
  d.querySelector('#links-ficha').href = `/quando-volta/?d=${encodeURIComponent(dominio)}`;
  if (!d.open) d.showModal();
  let itens = [];
  try {
    itens = await referentesDe(dominio);
  } catch (e) {
    if (aberto === dominio) d.querySelector('#links-resumo').textContent = 'Não consegui carregar a lista agora.';
    return;
  }
  if (aberto !== dominio) return;       // outro nome foi aberto no meio
  // a etiqueta conta so quem nao e plataforma: o resumo abre com o mesmo numero
  const abertas = itens.filter(([m]) => m === 'p').length;
  const proprios = itens.length - abertas;
  const plataformas = abertas === 1 ? 'uma plataforma' : `${num(abertas)} plataformas`;
  let resumo = 'A lista deste nome ainda não foi publicada.';
  if (itens.length && !proprios) resumo = `Só ${plataformas} apontava${abertas === 1 ? '' : 'm'} para este endereço.`;
  else if (itens.length) {
    resumo = `${num(proprios)} ${proprios === 1 ? 'domínio apontava' : 'domínios apontavam'} para este endereço`
      + (abertas ? `, fora ${plataformas}.` : '.');
  }
  d.querySelector('#links-resumo').textContent = resumo;
  d.querySelector('#links-grupos').innerHTML = grupos(itens);
}

export {
  REFERENTES,
  fnv1a32,
  fatiaDe,
  referentesDe,
  grupos,
  abrirLinks,
};
