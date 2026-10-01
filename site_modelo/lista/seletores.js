// Os seletores da lista com painel proprio.
import { esc } from './util.js';

// ------------------------------------------------------- seletores com painel

/**
 * Todo seletor da lista abre o mesmo painel: ordem, extensao, ramo, marca,
 * situacao e nomes por pagina. Com <select> nativo, no celular a lista do
 * sistema tem outra cara, outra fonte e outro jeito de marcar a escolhida.
 *
 * Extensao (116 opcoes na rodada inteira) e ramo (19) tem busca; os de ate
 * 8 opcoes nao, porque uma caixa de busca sobre tres opcoes so atrapalha.
 *
 * Um campo de texto no lugar do seletor nao serve: no celular, tocar nele
 * abre o teclado e seleciona o texto antes de a pessoa ver a lista. O
 * desenho e o de bibliotecas como
 * shadcn/ui (painel inferior no celular, painel ancorado no computador) e o
 * que a Baymard recomenda para lista longa no celular:
 *
 * - o gatilho e um <button>: tocar nao abre teclado;
 * - o painel e um <dialog> modal (prende o foco, Esc fecha, o resto da
 *   pagina fica inerte para leitor de tela);
 * - no celular o foco vai para a opcao escolhida e a busca so abre o teclado
 *   se for tocada; no computador vai direto para a busca, quando ha busca;
 * - cada opcao e um <button> com aria-pressed: nativo, sem
 *   aria-activedescendant.
 *
 * O <select> continua no DOM, escondido, como fonte da verdade: quem le ou
 * escreve .value e ouve 'change' nao muda. Quem escreve .value por codigo
 * chama sincronizarSeletores() depois (aplicar() ja chama).
 */
const seletores = [];
const celular = () => window.matchMedia('(max-width: 40rem)').matches;

function semAcento(texto) {
  return String(texto).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

function tornarSeletor(sel, { busca: comBusca = false, nomeCurto = '' } = {}) {
  if (!sel) return;
  const rotulo = document.querySelector(`label[for="${sel.id}"]`);

  const botao = document.createElement('button');
  botao.type = 'button';
  botao.id = `${sel.id}-botao`;
  botao.className = 'combo-botao';
  botao.setAttribute('aria-haspopup', 'dialog');
  botao.setAttribute('aria-expanded', 'false');
  const valor = document.createElement('span');
  valor.id = `${sel.id}-valor`;
  valor.className = 'combo-valor';
  botao.appendChild(valor);
  if (rotulo) {
    rotulo.id = rotulo.id || `${sel.id}-rotulo`;
    rotulo.htmlFor = botao.id;
    // "Filtrar por extensao, .com.br (14.658)": o rotulo sozinho apagaria o valor
    botao.setAttribute('aria-labelledby', `${rotulo.id} ${valor.id}`);
  }

  const painel = document.createElement('dialog');
  painel.className = comBusca ? 'combo-painel' : 'combo-painel sem-busca';
  painel.setAttribute('aria-labelledby', `${sel.id}-titulo`);
  painel.innerHTML = `
    <div class="combo-topo">
      <h2 class="combo-titulo" id="${sel.id}-titulo">${esc(rotulo ? rotulo.textContent : nomeCurto)}</h2>
      <button type="button" class="combo-fechar" aria-label="Fechar">&times;</button>
    </div>
    ${comBusca ? `<label class="sr-apenas" for="${sel.id}-filtro">Buscar ${nomeCurto}</label>
    <input type="search" class="combo-busca" id="${sel.id}-filtro"
           placeholder="Buscar ${nomeCurto}" autocomplete="off" spellcheck="false"
           enterkeyhint="done">
    <p class="sr-apenas" aria-live="polite" id="${sel.id}-contagem"></p>` : ''}
    <div class="combo-opcoes" role="group" aria-labelledby="${sel.id}-titulo"></div>`;

  const caixa = document.createElement('div');
  caixa.className = `combo combo-${sel.id}`;
  sel.parentNode.insertBefore(caixa, sel);
  caixa.append(botao, sel);
  document.body.appendChild(painel);
  // hidden, nao a classe escondido: preencherCategorias() e aplicar() usam a
  // classe para dizer "este seletor nao vale agora", e sincronizarSeletores() a le
  sel.hidden = true;
  sel.tabIndex = -1;

  const busca = painel.querySelector('.combo-busca');
  const lista = painel.querySelector('.combo-opcoes');
  const contagem = painel.querySelector(`#${sel.id}-contagem`);
  const b = { sel, caixa, botao, valor, painel };
  seletores.push(b);

  function pintar() {
    const q = busca ? semAcento(busca.value).trim().replace(/^\./, '') : '';
    let vis = [...sel.options].map((o, i) => ({ o, i, t: semAcento(o.textContent).replace(/^\./, '') }));
    // opcao que daria lista vazia agora fica fora do painel, salvo a escolhida
    vis = vis.filter((x) => x.o.selected || (!x.o.hidden && (!x.o.value || x.o.dataset.n !== '0')));
    if (q) {
      vis = vis.filter((x) => x.o.value && x.t.includes(q));
      // quem digita "rio" quer .rio.br antes de .floripa.br
      vis.sort((a, c) => (a.t.startsWith(q) ? 0 : 1) - (c.t.startsWith(q) ? 0 : 1) || a.i - c.i);
    }
    lista.replaceChildren(...vis.map((x) => {
      const op = document.createElement('button');
      op.type = 'button';
      op.className = 'combo-opcao';
      op.dataset.i = String(x.i);
      op.setAttribute('aria-pressed', String(x.o.selected));
      op.textContent = x.o.textContent;
      return op;
    }));
    if (!vis.length) {
      const p = document.createElement('p');
      p.className = 'combo-vazio';
      p.textContent = `Nenhuma ${nomeCurto} com esse nome.`;
      lista.appendChild(p);
    }
    if (contagem) contagem.textContent = q ? `${vis.length} ${vis.length === 1 ? 'opção' : 'opções'}` : '';
  }

  function posicionar() {
    if (celular()) {
      painel.style.removeProperty('--combo-topo');
      painel.style.removeProperty('--combo-esquerda');
      painel.style.removeProperty('--combo-max');
      return;
    }
    const r = botao.getBoundingClientRect();
    const largura = Math.max(r.width, comBusca ? 280 : 200);
    const esquerda = Math.min(r.left, window.innerWidth - largura - 16);
    // Abre embaixo do botao; sem espaco embaixo (o de nomes por pagina fica
    // perto do fim da tela) e com mais espaco em cima, abre para cima, como
    // o seletor nativo. A altura natural e medida sem o limite de altura.
    const embaixo = window.innerHeight - r.bottom - 20;
    const emCima = r.top - 20;
    painel.style.setProperty('--combo-max', 'min(24rem, calc(100dvh - 2rem))');
    const altura = painel.open ? painel.offsetHeight : 0;
    const paraCima = altura > embaixo && emCima > embaixo;
    const max = Math.min(altura || Infinity, paraCima ? emCima : embaixo);
    const topo = paraCima ? r.top - 4 - max : r.bottom + 4;
    if (altura) painel.style.setProperty('--combo-max', `${Math.round(max)}px`);
    else painel.style.removeProperty('--combo-max');
    painel.style.setProperty('--combo-topo', `${Math.round(topo)}px`);
    painel.style.setProperty('--combo-esquerda', `${Math.round(Math.max(16, esquerda))}px`);
    painel.style.setProperty('--combo-largura', `${Math.round(largura)}px`);
  }

  function abrir() {
    if (busca) busca.value = '';
    pintar();
    posicionar();
    painel.showModal();
    posicionar();                 // agora com a altura do painel aberto
    botao.setAttribute('aria-expanded', 'true');
    const escolhida = lista.querySelector('[aria-pressed="true"]');
    if (celular() || !busca) {
      // sem teclado: a pessoa ve a lista; a busca abre o teclado se tocada
      (escolhida || lista.querySelector('.combo-opcao'))?.focus({ preventScroll: true });
      escolhida?.scrollIntoView({ block: celular() ? 'center' : 'nearest' });
    } else {
      busca.focus();
      escolhida?.scrollIntoView({ block: 'nearest' });
    }
  }

  function fechar() {
    if (painel.open) painel.close();
  }

  function escolher(i) {
    const o = sel.options[i];
    if (o && sel.value !== o.value) {
      sel.value = o.value;
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    }
    fechar();
  }

  botao.addEventListener('click', abrir);
  painel.addEventListener('close', () => {
    botao.setAttribute('aria-expanded', 'false');
    sincronizarSeletores();
    botao.focus({ preventScroll: true });
  });
  painel.querySelector('.combo-fechar').addEventListener('click', fechar);
  // clique fora do quadro (no fundo do modal) fecha
  painel.addEventListener('click', (e) => {
    if (e.target === painel) fechar();
    const op = e.target.closest('.combo-opcao');
    if (op) escolher(Number(op.dataset.i));
  });
  busca?.addEventListener('input', pintar);
  busca?.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      lista.querySelector('.combo-opcao')?.focus();
    } else if (e.key === 'Enter') {
      // uma busca, um Enter: fica com a primeira da lista filtrada
      e.preventDefault();
      const primeira = lista.querySelector('.combo-opcao');
      if (busca.value.trim() && primeira) escolher(Number(primeira.dataset.i));
      else busca.blur();
    }
  });
  lista.addEventListener('keydown', (e) => {
    const ops = [...lista.querySelectorAll('.combo-opcao')];
    const n = ops.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (e.key === 'ArrowUp' && n <= 0 && busca) { busca.focus(); return; }
      ops[Math.max(0, Math.min(ops.length - 1, n + (e.key === 'ArrowDown' ? 1 : -1)))]?.focus();
    } else if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault();
      (e.key === 'Home' ? ops[0] : ops[ops.length - 1])?.focus();
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && e.key !== ' ') {
      if (busca) {
        // digitar com a lista em foco vai para a busca, como num seletor nativo
        busca.focus();
      } else {
        // sem busca, a letra pula para a opcao que comeca com ela
        const letra = semAcento(e.key);
        const depois = [...ops.slice(n + 1), ...ops.slice(0, n + 1)];
        depois.find((op) => semAcento(op.textContent).replace(/^\./, '').startsWith(letra))?.focus();
      }
    }
  });
  window.addEventListener('resize', () => { if (painel.open) posicionar(); });
  // no computador o painel e ancorado ao botao: rolar a pagina o soltaria
  window.addEventListener('scroll', () => { if (painel.open && !celular()) fechar(); }, { passive: true });
}

/** Mostra no botao o que o <select> tem agora (depois de .value por codigo). */
function sincronizarSeletores() {
  for (const b of seletores) {
    b.caixa.classList.toggle('escondido', b.sel.classList.contains('escondido'));
    b.valor.textContent = (b.sel.selectedOptions[0] || b.sel.options[0] || {}).textContent || '';
  }
}

export {
  seletores,
  celular,
  semAcento,
  tornarSeletor,
  sincronizarSeletores,
};
