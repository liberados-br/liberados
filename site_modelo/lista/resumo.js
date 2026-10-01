// O resumo da busca e a escolha do filtro inicial.
import { provisorios } from './acompanhados.js';
import { buscaNaRodada, casaBusca, normalizarTermo } from './busca.js';
import { $, CT, D, FILTROS, MINIMO_BUSCA_RODADA, N, NAO_VERIFICADO, PREFERENCIA, PREFERENCIA_FECHADA, SS, estado } from './estado.js';
import { aConferir, proximaAberturaDoCalendario, rodadaFechada } from './fase.js';
import { aplicar } from './filtros.js';
import { passaMarca, visiveis } from './painel.js';
import { carregarRodada } from './rodada.js';
import { fichaDaBusca, filtrosLigados, rotulosDosFiltros } from './tabela.js';
import { FUSO, desempateDaNota, extensaoDe, num } from './util.js';

/** "09/09", no fuso do Registro.br. */
function diaMes(quando) {
  const d = new Date(quando);
  return isNaN(d) ? '' : d.toLocaleDateString('pt-BR', { timeZone: FUSO, day: '2-digit', month: '2-digit' });
}

/*
 * As situacoes do resumo da busca, na ordem do que da para fazer agora. Cada
 * uma e um cartao que ja existe: o toque leva ao cartao, com a busca, e o
 * numero do resumo e o que o cartao mostra (mesmo filtro, mesma marca).
 */
function situacoesDoResumo() {
  const proxima = proximaAberturaDoCalendario();
  const quando = proxima ? `em ${diaMes(proxima)}` : 'na próxima rodada';
  const volta = (n) => (n === 1 ? `travou e volta ${quando}` : `travaram e voltam ${quando}`);
  const livres = (n) => (n === 1 ? 'livre agora' : 'livres agora');
  const leilao = () => 'em leilão';
  if (rodadaFechada()) {
    return [['livres', livres], ['aguardando', volta],
      ['sem_competicao', (n) => (n === 1 ? 'fechou sem candidato visível' : 'fecharam sem candidato visível')],
      ['leilao', leilao]];
  }
  return [['livres', livres], ['sem_competicao', () => 'sem candidato visível'],
    ['disputados', (n) => (n === 1 ? 'já tem candidato' : 'já têm candidato')],
    ['leilao', leilao], ['aguardando', volta]];
}

function botaoDoResumo(texto, pressionado, dados) {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = texto;
  b.setAttribute('aria-pressed', String(pressionado));
  Object.assign(b.dataset, dados);
  return b;
}

/**
 * Quantos nomes tem o termo e quantos deles passam pelos filtros. O total
 * ignora marca, extensao e ramo (e a situacao, que os chips detalham): e a resposta a "quantos nomes da
 * rodada tem pet" (com a marca em "so possivel marca", o resumo diria
 * "0 nomes da rodada" quando sao 9).
 */
function contarBusca(linhas, q, passaFiltros) {
  let total = 0;
  let comFiltros = 0;
  for (const it of linhas) {
    if (!casaBusca(it, q)) continue;
    total++;
    if (passaFiltros(it)) comFiltros++;
  }
  return { total, comFiltros };
}

/** A contagem do resumo, na rodada inteira (ou nos consultados, sem ela); null sem termo. */
function contagemDaBusca(q) {
  if (q.termo.length < MINIMO_BUSCA_RODADA) return null;
  const cat = estado.categoria === '' ? -1 : Number(estado.categoria);
  const passa = (it) => passaMarca(it)
    && (!estado.extensao || extensaoDe(it[D]) === estado.extensao)
    && (cat < 0 || Boolean((it[CT] || 0) & (1 << cat)));
  return contarBusca(estado.rodada || estado.dados.itens, q, passa);
}

/**
 * O resumo da busca: com 3 letras ou mais, quantos nomes da
 * rodada tem o termo e em que situacao estao, um toque por situacao. Nome
 * exato que nao esta na rodada ganha a ficha e ate 5 nomes da rodada com o
 * termo e sem concorrente a vista. Tudo por textContent (sem risco de XSS).
 */
function pintarResumoDaBusca(q, casados = null) {
  const el = $('#resumo-busca');
  if (!el) return;
  const ativo = q.termo.length >= MINIMO_BUSCA_RODADA
    && !['acompanhados', 'lista'].includes(estado.filtro);
  el.classList.toggle('escondido', !ativo);
  // so a frase vai ao leitor de tela, e so quando muda
  const falar = (texto) => {
    const st = $('#resumo-busca-status');
    if (st && st.textContent !== texto) st.textContent = texto;
  };
  if (!ativo) {
    el.replaceChildren();
    falar('');
    return;
  }
  const cat = estado.categoria === '' ? -1 : Number(estado.categoria);
  const passa = (it) => casaBusca(it, q) && passaMarca(it)
    && (!estado.extensao || extensaoDe(it[D]) === estado.extensao)
    && (cat < 0 || Boolean((it[CT] || 0) & (1 << cat)));
  const naRodada = buscaNaRodada();
  const total = estado.dados.total_rodada || 0;
  const r = estado.dados.rodada || {};
  const periodo = r.inicio && r.fim ? ` da rodada de ${diaMes(r.inicio)} a ${diaMes(r.fim)}` : ' da rodada';
  // entre aspas o termo ja vem com as dele
  const termo = /^["“”].*["“”]$/.test(estado.busca.trim()) ? estado.busca.trim() : `“${estado.busca.trim()}”`;
  const partes = [];
  const contagem = estado.contagemBusca || contagemDaBusca(q);
  // o filtro esconde parte dos achados: a segunda linha diz quantos sobram
  const escondendo = filtrosLigados() && contagem.comFiltros < contagem.total;

  const cabeca = document.createElement('p');
  cabeca.className = 'resumo-cabeca';
  let achados = null;
  if (estado.rodada) {
    // os achados com os filtros: e o que os chips e a lista mostram
    achados = casados || estado.rodada.filter(passa);
    cabeca.textContent = `${termo}: ${num(contagem.total)} `
      + `${contagem.total === 1 ? 'nome' : 'nomes'} dos ${num(total)}${periodo}.`;
  } else if (estado.promessaRodada) {
    cabeca.textContent = `Buscando ${termo} nos ${num(total)} nomes${periodo}…`;
  } else {
    cabeca.textContent = `${termo} nos nomes já consultados. `;
    cabeca.append(botaoDoResumo(`Buscar nos ${num(total)} nomes da rodada`, false, { buscaRodada: '' }));
  }
  partes.push(cabeca);
  // a linha da fase se recolhe no modo busca: a data que importa vem para ca
  const fase = ($('#fase-inicio') || {}).textContent || '';
  if (estado.modoBusca && fase.trim()) {
    const p = document.createElement('p');
    p.className = 'resumo-fase';
    p.textContent = fase.replace(/\s+/g, ' ').trim();
    partes.push(p);
  }
  let frase = cabeca.firstChild ? cabeca.firstChild.textContent : '';
  if (escondendo) {
    const p = document.createElement('p');
    p.className = 'resumo-filtros';
    const quais = rotulosDosFiltros();
    const linha = `Com os filtros atuais${quais.length ? ` (${quais.join(', ')})` : ''}: `
      + `${num(contagem.comFiltros)}.`;
    const b = document.createElement('button');
    b.type = 'button';
    b.dataset.limparFiltros = '';
    b.textContent = 'Limpar filtros';
    p.append(`${linha} `, b);
    partes.push(p);
    frase = `${frase.trim()} ${linha}`;
  }
  // uma vez, ao entrar: o leitor de tela sabe que a pagina mudou e como
  // voltar. Depois, a mesma frase sem o aviso nao e fala nova (a conferencia
  // ao vivo repinta o resumo segundos depois de abrir)
  const aviso = ' Mostrando só a busca; o botão Limpar busca volta ao início.';
  if (estado.modoBusca && !estado.modoBuscaAnunciado) {
    frase = frase.trim() + aviso;
    estado.modoBuscaAnunciado = true;
  }
  const dito = ($('#resumo-busca-status') || {}).textContent || '';
  if (dito !== frase.trim() + aviso) falar(frase);

  const chips = document.createElement('div');
  chips.className = 'resumo-situacoes';
  chips.setAttribute('role', 'group');
  chips.setAttribute('aria-label', 'Ver a busca por situação');
  if (achados && achados.length) {
    chips.append(botaoDoResumo(escondendo ? `Os ${num(achados.length)} filtrados` : `Todos os ${num(achados.length)}`, naRodada && !estado.situacao,
                               { buscaRodada: '', rolar: '' }));
  }
  // so nome consultado tem situacao, e toda linha consultada da rodada esta
  // nos achados: conta em cima deles (centenas), nao do instantaneo inteiro
  const consultados = (achados || estado.dados.itens.filter(passa)).filter((it) => it[SS] !== NAO_VERIFICADO);
  for (const [chave, rotulo] of situacoesDoResumo()) {
    let n = 0;
    for (const it of consultados) if (FILTROS[chave](it)) n++;
    if (!n) continue;
    chips.append(botaoDoResumo(`${num(n)} ${rotulo(n)}`, !naRodada && estado.filtro === chave,
                               { cartao: chave, rolar: '' }));
  }
  if (chips.childElementCount) partes.push(chips);

  // nome exato fora da rodada: a ficha diz se esta livre ou quando volta
  const nome = fichaDaBusca(q.bruto);
  const linha = nome && estado.porDominio.get(nome);
  const naLista = Boolean(linha) && !provisorios.has(linha);
  if (nome && achados && !naLista
      && (q.modo === 'exato' || q.dominio || contagem.total === 0)) {
    const p = document.createElement('p');
    p.className = 'resumo-ficha';
    const a = document.createElement('a');
    a.rel = 'nofollow';
    a.href = `/quando-volta/?d=${encodeURIComponent(nome)}`;
    a.textContent = 'veja se está livre ou quando volta';
    p.append(`${nome} não está nesta rodada: `, a);
    partes.push(p);
    const parecido = { termo: normalizarTermo(nome.split('.')[0]), modo: 'contem', dominio: false };
    const sugestoes = (estado.rodada || [])
      .filter((it) => it[D] !== nome && casaBusca(it, parecido) && passaMarca(it)
        && (FILTROS.livres(it) || FILTROS.sem_competicao(it)))
      .sort((x, y) => y[N] - x[N] || desempateDaNota(x[D], y[D]))
      .slice(0, 5);
    if (sugestoes.length) {
      const s = document.createElement('p');
      s.className = 'resumo-sugestoes';
      s.append('Na rodada, com o termo e sem concorrente à vista: ');
      sugestoes.forEach((it, i) => {
        const link = document.createElement('a');
        link.rel = 'nofollow';
        link.href = `/quando-volta/?d=${encodeURIComponent(it[D])}`;
        link.textContent = it[D];
        s.append(...(i ? [', ', link] : [link]));
      });
      partes.push(s);
    }
  }

  const dica = document.createElement('p');
  dica.className = 'resumo-dica';
  dica.textContent = 'pet* começa com · *pet termina com · "pet" só o nome exato';
  partes.push(dica);
  el.replaceChildren(...partes);
}

async function escolherFiltro(filtro) {
  estado.filtro = filtro;
  estado.cartaoEscolhido = true;   // a busca passa a olhar so este cartao
  estado.pagina = 0;
  estado.retidos.clear();       // quem mudou so fica ate a troca de lista
  if (filtro === 'rodada') await carregarRodada();
  aplicar();
}

function filtroInicial() {
  const vis = visiveis();
  // lista nova ainda sem leitura: a rodada inteira, pela nota
  if (aConferir()) return 'rodada';
  const ordem = rodadaFechada() ? PREFERENCIA_FECHADA : PREFERENCIA;
  return ordem.find((f) => vis.some(FILTROS[f])) || 'rodada';
}

export {
  diaMes,
  situacoesDoResumo,
  botaoDoResumo,
  contarBusca,
  contagemDaBusca,
  pintarResumoDaBusca,
  escolherFiltro,
  filtroInicial,
};
