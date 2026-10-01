// Os filtros da lista e a lista que eles montam.
import { garantirLinha } from './acompanhados.js';
import { buscaNaRodada, casaBusca, modoDaBusca, pintarModoBusca } from './busca.js';
import { pintarContagensDosFiltros } from './contagem.js';
import { atualizarEndereco } from './endereco.js';
import { $, C, CT, D, FILTROS, LISTAS_MISTAS, N, NAO_VERIFICADO, SITUACOES, SS, estado } from './estado.js';
import { linksDe } from './linha.js';
import { passaMarca } from './painel.js';
import { contagemDaBusca, pintarResumoDaBusca } from './resumo.js';
import { pintarTabela } from './tabela.js';
import { desempateDaNota, extensaoDe } from './util.js';

// ------------------------------------------------------------------ filtros

/** Chave de SITUACOES de uma linha, para o filtro e a contagem. */
function situacaoDe(it) {
  if (it[SS] === NAO_VERIFICADO) return 'nao_verificado';
  return SITUACOES[estado.dados.status[it[SS]]] || 'registrado';
}

/**
 * `manterOrdem`: quem chama e a conferencia ao vivo, nao a pessoa. A linha
 * conferida muda o numero no lugar e so troca de posicao quando a pessoa
 * mexe na lista (filtro, ordem, busca) ou recarrega: com a ordem por
 * candidatos, o nome pularia para outra pagina logo depois do clique.
 */
function aplicar({ manterOrdem = false, aosPoucos = false } = {}) {
  const { dados } = estado;
  // antes de tudo: ao sair do modo busca o cartao de antes volta a mandar
  pintarModoBusca();
  const q = modoDaBusca(estado.busca);
  const naRodada = buscaNaRodada();
  const teste = naRodada ? FILTROS.rodada : (FILTROS[estado.filtro] || FILTROS.todos);
  // "toda a rodada" (ou a busca de 3 letras sem cartao escolhido) olha a
  // lista inteira; acompanhados e a lista compartilhada podem ter nome de
  // fora do instantaneo; o resto, so nele
  let base = dados.itens;
  if ((estado.filtro === 'rodada' || naRodada) && estado.rodada) base = estado.rodada;
  if (estado.filtro === 'acompanhados') {
    base = [...estado.acompanhados.keys()].map(garantirLinha);
  }
  if (estado.filtro === 'lista') base = [...estado.lista].map(garantirLinha);
  // quem marcou a estrela (ou mandou a lista) escolheu ver: marca nao esconde
  const filtrarMarca = estado.filtro !== 'acompanhados' && estado.filtro !== 'lista';

  const mista = naRodada || LISTAS_MISTAS.has(estado.filtro);
  $('#situacao').classList.toggle('escondido', !mista);
  // numa lista de uma situacao so, a situacao de cada linha repetiria o
  // titulo da lista: o extra.css esconde o selo por esta marca
  const tabela = $('#tabela');
  if (tabela) tabela.dataset.mista = mista ? '1' : '0';
  if (!mista && estado.situacao) {
    estado.situacao = '';
    $('#situacao').value = '';
  }

  // Uma passada so: a lista e as contagens de cada filtro. A contagem de um
  // filtro respeita todos os OUTROS (cartao, busca, marca e os demais
  // seletores), mas nao ele mesmo; senao escolher .com.br zeraria as outras
  // extensoes.
  const porExt = new Map();
  const porCat = [];
  const porSit = new Map();
  const cat = estado.categoria === '' ? -1 : Number(estado.categoria);
  const itens = [];
  // na busca pela rodada inteira, os achados antes da situacao: os chips do
  // resumo contam em cima deles. O total sem filtro (contagemDaBusca) faz a
  // passada propria, porque a base deste laco pode ser so o cartao
  const casados = naRodada && estado.rodada ? [] : null;
  for (const it of base) {
    // conferido nesta lista e mudou: fica, com o marcador, ate a proxima
    // troca de lista. Sumir em silencio pareceria bug (ver anotarMudanca).
    if (!teste(it) && !estado.retidos.has(it[D])) continue;
    if (q.termo && !casaBusca(it, q)) continue;
    if (filtrarMarca && !passaMarca(it)) continue;
    const ext = extensaoDe(it[D]);
    const bits = it[CT] || 0;
    const sit = situacaoDe(it);
    const okExt = !estado.extensao || ext === estado.extensao;
    const okCat = cat < 0 || Boolean(bits & (1 << cat));
    const okSit = !estado.situacao || sit === estado.situacao;
    if (okCat && okSit) porExt.set(ext, (porExt.get(ext) || 0) + 1);
    if (okExt && okSit) {
      for (let b = bits, i = 0; b; b >>>= 1, i++) if (b & 1) porCat[i] = (porCat[i] || 0) + 1;
    }
    if (okExt && okCat) porSit.set(sit, (porSit.get(sit) || 0) + 1);
    if (casados && okExt && okCat) casados.push(it);
    if (okExt && okCat && okSit) itens.push(it);
  }
  pintarContagensDosFiltros(porExt, porCat, porSit);

  const ordenadores = {
    nota: (a, b) => b[N] - a[N] || desempateDaNota(a[D], b[D]),
    // nao verificado (null) vai para o fim nas duas direcoes: null - numero
    // da NaN e embaralha a ordenacao inteira
    menos_candidatos: (a, b) => (a[C] ?? 1e9) - (b[C] ?? 1e9) || b[N] - a[N],
    candidatos: (a, b) => (b[C] ?? -1) - (a[C] ?? -1) || b[N] - a[N],
    tamanho: (a, b) => a[D].length - b[D].length || b[N] - a[N],
    alfabetica: (a, b) => a[D].localeCompare(b[D]),
    alfabetica_desc: (a, b) => b[D].localeCompare(a[D]),
    // primeiro os que tem link de site muito citado (a etiqueta de borda
    // grossa), depois os outros; em cada grupo, o numero que a etiqueta
    // mostra, do maior para o menor
    links: (a, b) => (Math.sign(linksDe(b)[1]) - Math.sign(linksDe(a)[1]))
                     || (linksDe(b)[0] - linksDe(a)[0]) || b[N] - a[N],
  };
  itens.sort(ordenadores[estado.ordem] || ordenadores.nota);
  if (manterOrdem && estado.atual.length) {
    // sort estavel: quem ja estava na tela fica onde estava; quem entrou
    // agora vai para o fim, na ordem pedida
    const pos = new Map(estado.atual.map((it, i) => [it, i]));
    itens.sort((a, b) => (pos.get(a) ?? Infinity) - (pos.get(b) ?? Infinity));
  }

  estado.atual = itens;
  // antes da tabela: a lista vazia tambem precisa saber se foi filtro
  estado.contagemBusca = contagemDaBusca(q);
  if (!manterOrdem) atualizarEndereco();
  estado.pagina = Math.min(estado.pagina,
                           Math.max(0, Math.ceil(itens.length / estado.porPagina) - 1));
  pintarTabela({ aosPoucos });
  pintarResumoDaBusca(q, casados);
}

export {
  situacaoDe,
  aplicar,
};
