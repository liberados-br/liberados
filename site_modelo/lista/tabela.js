// A tabela, a lista vazia e a limpeza dos filtros.
import { buscaNaRodada, filtroEfetivo, modoDaBusca } from './busca.js';
import { pintarBotoesDaLista } from './endereco.js';
import { $, D, EXPLICA_FILTRO, FILTROS, NOMES_FILTRO, estado } from './estado.js';
import { aplicar } from './filtros.js';
import { linha } from './linha.js';
import { pintarContagens, visiveis } from './painel.js';
import { escolherFiltro } from './resumo.js';
import { carregarRodada } from './rodada.js';
import { sincronizarSeletores } from './seletores.js';
import { num } from './util.js';

// Enquanto a pessoa digita, a tabela sai em duas vezes: as
// primeiras linhas no quadro seguinte, o resto no outro. Com a CPU 4x mais
// lenta, pintar 100 linhas de uma vez custava ~100 ms por tecla.
const PRIMEIRAS_LINHAS = 25;
let restoDaTabela = 0;

function pintarTabela({ aosPoucos = false } = {}) {
  const itens = estado.atual;
  const inicio = estado.pagina * estado.porPagina;
  const pagina = itens.slice(inicio, inicio + estado.porPagina);

  cancelAnimationFrame(restoDaTabela);
  const corpo = $('#corpo-tabela');
  if (aosPoucos && pagina.length > PRIMEIRAS_LINHAS) {
    corpo.innerHTML = pagina.slice(0, PRIMEIRAS_LINHAS).map(linha).join('');
    restoDaTabela = requestAnimationFrame(() => {
      restoDaTabela = requestAnimationFrame(() => {
        corpo.insertAdjacentHTML('beforeend', pagina.slice(PRIMEIRAS_LINHAS).map(linha).join(''));
      });
    });
  } else {
    corpo.innerHTML = pagina.map(linha).join('');
  }
  $('#tabela').classList.toggle('escondido', pagina.length === 0);

  const vazio = $('#vazio');
  vazio.classList.toggle('escondido', pagina.length !== 0);
  if (pagina.length === 0) pintarVazio();

  const paginas = Math.max(1, Math.ceil(itens.length / estado.porPagina));
  // quem mudou e ja nao pertence a lista continua na tela, mas nao na conta
  const fora = itens.filter((it) => (estado.retidos.get(it[D]) || {}).fora).length;
  // Com mais de uma pagina, o titulo diz que a lista continua. So o
  // "pagina 1 de 2" embaixo da tabela faria 160 em leilao parecerem 100.
  const faixa = paginas > 1
    ? ` · mostrando ${num(inicio + 1)} a ${num(inicio + pagina.length)}` : '';
  const efetivo = filtroEfetivo();
  $('#titulo-lista').textContent =
    `${efetivo !== estado.filtro ? 'Busca na rodada inteira' : (NOMES_FILTRO[efetivo] || efetivo)}: ${num(itens.length - fora)}`
    + faixa
    + (fora ? ` · ${fora} ${fora === 1 ? 'saiu' : 'saíram'} ao conferir` : '');
  const cartao = document.querySelector(`.cartao[data-filtro="${efetivo}"] .ajuda`);
  $('#explica-filtro').textContent = cartao ? cartao.textContent : (EXPLICA_FILTRO[efetivo] || '');
  pintarBotoesDaLista();

  $('#paginacao').classList.toggle('escondido', paginas === 1);
  $('#info-pagina').textContent = `página ${estado.pagina + 1} de ${num(paginas)}`;
  $('#btn-anterior').disabled = estado.pagina === 0;
  $('#btn-proxima').disabled = estado.pagina + 1 >= paginas;

  document.querySelectorAll('.cartao').forEach((c) => {
    c.setAttribute('aria-pressed', String(c.dataset.filtro === efetivo));
  });
}

/**
 * Lista vazia e convite, nao beco: diz por que esta vazia e oferece o
 * proximo passo num botao.
 */
function pintarVazio() {
  const filtrando = estado.busca.trim() || estado.extensao
    || estado.categoria !== '' || estado.marca !== '' || estado.situacao;
  let texto = 'Nenhum nome nesta lista agora.';
  let acao = ['Ver toda a rodada', () => escolherFiltro('rodada')];
  const busca = estado.busca.trim();
  const naRodada = buscaNaRodada();
  const cb = estado.contagemBusca;
  if (naRodada && cb && cb.total > 0) {
    // o termo existe, quem esconde e o filtro; nunca "nenhum
    // nome com pet" quando a marca ou a extensao tiram os 9
    const onde = estado.rodada ? 'da rodada' : (cb.total === 1 ? 'já consultado' : 'já consultados');
    texto = cb.total === 1
      ? `O único nome ${onde} com “${busca}” não passa pelos filtros atuais.`
      : `Nenhum dos ${num(cb.total)} nomes ${onde} com “${busca}” passa pelos filtros atuais.`;
    acao = ['Limpar filtros', () => {
      // o botao some com a lista refeita: o foco vai ao resumo
      limparSoFiltros();
      const resumo = $('#resumo-busca');
      resumo.tabIndex = -1;
      resumo.focus({ preventScroll: true });
    }];
  } else if (naRodada) {
    // o resumo em cima da lista ja oferece a ficha e os nomes parecidos
    texto = estado.rodada ? `Nenhum nome da rodada com “${busca}”.`
      : `Nenhum nome com “${busca}” entre os já consultados.`;
    acao = estado.rodada ? null
      : [`Buscar nos ${num(estado.dados.total_rodada || 0)} nomes da rodada`, buscarNaRodadaInteira];
  } else if (busca && !['rodada', 'acompanhados', 'lista'].includes(estado.filtro)) {
    // quem busca um nome quer saber se ele esta na rodada, nao so neste
    // cartao: a busca que nao acha oferece a lista inteira
    texto = `Nenhum nome com “${busca}” em ${NOMES_FILTRO[estado.filtro] || 'esta lista'}.`;
    acao = [`Buscar nos ${num(estado.dados.total_rodada || 0)} nomes da rodada`,
            () => escolherFiltro('rodada')];
  } else if (filtrando) {
    texto = 'Nada com essa busca e esses filtros.';
    acao = ['Limpar busca e filtros', limparFiltros];
  } else if (estado.filtro === 'joias') {
    texto = `Nenhuma joia agora: os ${num(estado.dados.total_elegiveis || 0)} `
          + 'elegíveis ao leilão já têm candidato ou já estão em leilão. É o '
          + 'normal passado o primeiro dia da rodada.';
    // "Ver os sem competicao" so quando ha algum: relida a lista, eles viram
    // livre, registrado ou aguardando, e o botao levaria a uma lista vazia
    const rotulos = {
      sem_competicao: 'Ver os sem competição',
      livres: 'Ver o que está livre agora',
      rodada: 'Ver toda a rodada',
    };
    const vis = visiveis();
    const destino = ['sem_competicao', 'livres'].find((f) => vis.some(FILTROS[f])) || 'rodada';
    acao = [rotulos[destino], () => escolherFiltro(destino)];
  } else if (estado.filtro === 'acompanhados') {
    texto = 'Você ainda não acompanha nenhum nome. Toque na estrela ao lado '
          + 'de um domínio e esta página avisa quando ele mudar.';
    acao = null;
  }
  const p = document.createElement('p');
  p.textContent = texto;
  const partes = [p];
  if (acao) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = acao[0];
    b.addEventListener('click', acao[1]);
    partes.push(b);
  }
  // Quem busca um nome que nao esta na lista quer saber dele mesmo assim: a
  // ficha diz se esta livre, registrado, e quando volta
  const nome = naRodada ? null : fichaDaBusca(modoDaBusca(busca).bruto);
  if (nome) {
    const a = document.createElement('a');
    a.className = 'ficha-da-busca';
    a.rel = 'nofollow';
    a.href = `/quando-volta/?d=${encodeURIComponent(nome)}`;
    a.textContent = `Ver se ${nome} está livre ou quando volta`;
    partes.push(a);
  }
  $('#vazio').replaceChildren(...partes);
}

/** O nome que a busca parece pedir, para a ficha; null se nao parece nome. */
function fichaDaBusca(busca) {
  const t = String(busca || '').trim().toLowerCase().replace(/^www\./, '');
  if (!/^[a-z0-9][a-z0-9-]{1,62}(\.[a-z0-9-]+)*$/.test(t)) return null;
  if (t.includes('.') && !t.endsWith('.br')) return null;
  return t.includes('.') ? t : `${t}.com.br`;
}

/**
 * Os filtros que estreitam o resumo (marca, extensao, ramo) estao ligados?
 * A situacao fica de fora: o resumo ja a detalha nos chips, e o "Todos"
 * a zera.
 */
function filtrosLigados() {
  return Boolean(estado.marca || estado.extensao || estado.categoria !== '');
}

/**
 * Os nomes dos filtros ligados, como o seletor mostra ("Só possível marca",
 * ".com.br"), para o resumo dizer qual deles esconde: no celular os
 * seletores ficam duas telas abaixo da busca.
 */
function rotulosDosFiltros() {
  return ['#marca', '#extensao', '#categoria'].map((id) => {
    const o = $(id)?.selectedOptions?.[0];
    return o && o.value ? (o.dataset.rotulo || o.textContent).trim() : '';
  }).filter(Boolean);
}

/** Zera marca, extensao, ramo e situacao; a busca fica. */
function limparSoFiltros() {
  estado.extensao = '';
  estado.categoria = '';
  estado.marca = '';
  estado.situacao = '';
  $('#extensao').value = '';
  $('#categoria').value = '';
  $('#marca').value = '';
  $('#situacao').value = '';
  sincronizarSeletores();
  estado.pagina = 0;
  pintarContagens();   // os numeros dos cartoes dependem da marca
  aplicar();
}

function limparFiltros() {
  estado.busca = '';
  $('#busca').value = '';
  limparSoFiltros();
}

/**
 * A busca volta a olhar a rodada inteira (o "todos" do resumo e o botao da
 * lista vazia): sem cartao escolhido e sem situacao.
 */
async function buscarNaRodadaInteira() {
  estado.cartaoEscolhido = false;
  estado.filtro = estado.filtroInicial || estado.filtro;
  estado.situacao = '';
  $('#situacao').value = '';
  estado.pagina = 0;
  estado.retidos.clear();
  aplicar();
  if (!estado.rodada) {
    await carregarRodada({ aviso: false });
    aplicar();
  }
}

export {
  PRIMEIRAS_LINHAS,
  restoDaTabela,
  pintarTabela,
  pintarVazio,
  fichaDaBusca,
  filtrosLigados,
  rotulosDosFiltros,
  limparSoFiltros,
  limparFiltros,
  buscarNaRodadaInteira,
};
