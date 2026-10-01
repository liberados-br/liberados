// As dobras do celular: os tres passos e os filtros da lista atras de um botao
// que abre e fecha. Quem esconde e o extra.css, so abaixo de 40rem; no
// computador o botao nao aparece. Ele nasce com hidden, entao sem este script
// tudo fica aberto, como sempre foi.

/** Quantos seletores de filtro e ordem fogem da primeira opcao. */
function filtrosAtivos() {
  return [...document.querySelectorAll('#controles-filtros select')]
    .filter((s) => s.selectedIndex > 0).length;
}

/** Fechada, a dobra dos filtros diz quantos estao valendo. */
function rotularFiltros() {
  const texto = document.querySelector('.dobra-filtros .dobra-texto');
  if (!texto) return;
  const n = filtrosAtivos();
  texto.textContent = n ? `Filtros e ordem · ${n} ${n === 1 ? 'ativo' : 'ativos'}` : 'Filtros e ordem';
}

/** Link com filtro no endereco: a dobra abre, para quem chega ver qual vale. */
function abrirFiltrosSeAtivos() {
  const botao = document.querySelector('.dobra-filtros');
  if (botao && filtrosAtivos()) botao.setAttribute('aria-expanded', 'true');
}

/**
 * Leva a pessoa ate a lista depois de trocar o filtro (um numero da folha,
 * "Ver acompanhados"). A lista mora longe dos numeros: sem rolar, o toque
 * trocava a lista la embaixo e a tela parecia parada. So rola se o comeco
 * da lista nao esta a vista; com movimento reduzido, rola sem animacao.
 */
function levarALista() {
  requestAnimationFrame(() => {
    const alvo = [document.querySelector('.dobra-filtros'), document.querySelector('#controles')]
      .find((e) => e && e.offsetParent !== null);
    if (!alvo) return;
    const topo = alvo.getBoundingClientRect().top;
    if (topo >= 0 && topo < window.innerHeight * 0.5) return;
    const calmo = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    alvo.scrollIntoView({ block: 'start', behavior: calmo ? 'auto' : 'smooth' });
  });
}

function ligarDobras() {
  for (const botao of document.querySelectorAll('.dobra')) {
    botao.hidden = false;
    botao.dataset.pronta = '';
    botao.addEventListener('click', () => {
      botao.setAttribute('aria-expanded', String(botao.getAttribute('aria-expanded') !== 'true'));
    });
  }
  rotularFiltros();
  // os seletores avisam toda troca, inclusive a que vem do endereco
  // (lista/seletores.js), com um change que sobe ate #controles
  const controles = document.querySelector('#controles');
  if (controles) controles.addEventListener('change', rotularFiltros);
}

export {
  abrirFiltrosSeAtivos,
  levarALista,
  filtrosAtivos,
  rotularFiltros,
  ligarDobras,
};
