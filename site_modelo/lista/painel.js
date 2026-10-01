// O painel do topo: cartoes, contagens e textos da fase.
import { $, D, E, FILTROS, MK, NOMES_FILTRO, SS, estado, iLeilao } from './estado.js';
import { aConferir, pintarFase, proximaAberturaDoCalendario, rodadaFechada } from './fase.js';
import { filtroInicial } from './resumo.js';
import { FUSO, agoraDaPagina, agoraMs, extensaoDe, formatarData, formatarDataCurta, horaCurta, idadeTexto, num } from './util.js';

// ------------------------------------------------------------------- painel

/**
 * O seletor de marca. E um filtro como os outros, e o padrao mostra tudo: a
 * etiqueta "possivel marca de terceiro" continua na linha, e marca famosa
 * a pessoa reconhece. "So marca" usa o mesmo criterio da etiqueta (RISCO);
 * ATENCAO nao tem etiqueta e por isso nao entra.
 */
function passaMarca(it) {
  if (estado.marca === 'sem') return it[MK] !== 2;
  if (estado.marca === 'so') return it[MK] === 2;
  return true;
}

function visiveis() {
  return estado.dados.itens.filter(passaMarca);
}

/** Os numeros dos cartoes. A parte, porque a conferencia ao vivo os muda. */
function pintarContagens() {
  const vis = visiveis();
  const contagem = {};
  for (const [chave, teste] of Object.entries(FILTROS)) {
    contagem[chave] = vis.filter(teste).length;
  }
  // a rodada inteira nao esta carregada ainda: o total vem do instantaneo
  contagem.rodada = estado.dados.total_rodada || 0;
  // acompanhado pode estar fora do instantaneo: conta a lista, nao o filtro
  contagem.acompanhados = estado.acompanhados.size;
  for (const [chave, n] of Object.entries(contagem)) {
    const el = document.getElementById('n-' + chave);
    if (el) el.textContent = num(n);
    const cartao = document.querySelector(`.cartao[data-filtro="${chave}"]`);
    if (cartao) cartao.classList.toggle('zerado', n === 0);
  }
  // "Elegiveis ao leilao" muda de papel quando a rodada fecha.
  //
  // Aberta: so diz algo enquanto ha elegivel FORA do leilao, nas primeiras
  // ~30 h (joias e disputados). Depois e a mesma lista de "Em leilao", nome
  // por nome, e dois cartoes iguais confundem.
  //
  // Fechada: vira a historia da rodada ("Foram a leilao"). E a unica lista
  // que nao encolhe quando a varredura reclassifica os nomes, porque o
  // elegivel vem de lista-processo-competitivo.txt e fica; ja "Em leilao"
  // esvazia sozinho conforme cada nome vira REGISTRADO ou AGUARDANDO.
  const fechada = rodadaFechada();
  const foraDoLeilao = vis.filter((it) => it[E] === 1 && it[SS] !== iLeilao).length;
  const cardEleg = document.querySelector('[data-filtro="elegiveis"]');
  if (cardEleg) {
    // A regra de esconder e a mesma nas duas fases, e e sobre duplicacao:
    // enquanto TODO elegivel ainda esta em leilao, este cartao seria copia
    // de "Em leilao", nome por nome. Ele reaparece, ja como historia, assim
    // que a varredura tira o primeiro nome do leilao.
    cardEleg.classList.toggle('escondido', foraDoLeilao === 0);
    if (fechada) {
      cardEleg.querySelector('.titulo').textContent = 'Foram a leilão';
      cardEleg.querySelector('.ajuda').textContent = 'Os nomes mais disputados desta '
        + 'rodada: leva quem deu o maior lance. O valor de cada leilão não é publicado';
      NOMES_FILTRO.elegiveis = 'Foram a leilão';
    }
  }
  if (!fechada && foraDoLeilao === 0 && contagem.leilao
      && estado.filtro === 'elegiveis') estado.filtro = 'leilao';
  if (contagem.joias === 0 && estado.filtro === 'joias') {
    estado.filtro = contagem.sem_competicao ? 'sem_competicao' : filtroInicial();
  }
  const ajudaLeilao = document.querySelector('[data-filtro="leilao"] .ajuda');
  if (ajudaLeilao) {
    // a frase simples fica; o detalhe dos elegiveis so enquanto diz algo
    ajudaLeilao.textContent = fechada
      ? 'Ainda abertos: o leilão de um nome não acaba junto com a rodada'
      : foraDoLeilao > 0 && contagem.elegiveis
        ? `Leva quem der o maior lance: ${num(contagem.leilao)} dos ${num(contagem.elegiveis)} elegíveis já abriram`
        : 'Nomes muito disputados: leva quem der o maior lance';
  }

  // Joias nasce escondido: "0 Joias" como primeira coisa da pagina parece
  // site quebrado. Aparece so quando ha o que mostrar, como Livre agora.
  const cardJoias = document.querySelector('[data-filtro="joias"]');
  if (cardJoias) cardJoias.classList.toggle('escondido', contagem.joias === 0);

  const cardAcomp = document.querySelector('[data-filtro="acompanhados"]');
  if (cardAcomp) cardAcomp.classList.toggle('escondido', contagem.acompanhados === 0);

  // o card de livres so aparece quando ha o que mostrar: durante a rodada
  // ninguem cai no pool livre, e um zero permanente e so ruido
  const livres = vis.filter(FILTROS.livres).length;
  const cardLivres = document.querySelector('[data-filtro="livres"]');
  if (cardLivres) cardLivres.classList.toggle('escondido', livres === 0);

  // Rodada fechada ("fechada" ja veio de cima): "Disputados" vira "Voltam na
  // proxima rodada" (os mesmos nomes, menos os elegiveis), e "Sem
  // competicao" vira historia.
  const cardAguard = document.querySelector('[data-filtro="aguardando"]');
  if (cardAguard) cardAguard.classList.toggle('escondido', contagem.aguardando === 0);
  const cardDisp = document.querySelector('[data-filtro="disputados"]');
  if (cardDisp) cardDisp.classList.toggle('escondido', fechada);
  if (fechada && estado.filtro === 'disputados') estado.filtro = 'aguardando';
  const semComp = document.querySelector('[data-filtro="sem_competicao"]');
  if (semComp && fechada) {
    semComp.querySelector('.titulo').textContent = 'Fecharam sem candidato';
    semComp.querySelector('.ajuda').textContent = 'Ninguém aparecia na consulta no fim da '
      + 'rodada: se era zero mesmo, o nome ficou livre para registro. Confira na hora';
    NOMES_FILTRO.sem_competicao = 'Fecharam sem candidato visível';
  }
  // Zerado entre rodadas, some. Relida a lista inteira, nenhum nome continua
  // "fechou sem candidato": cada um virou livre, registrado ou aguardando.
  // Sem isso, um 0 com um paragrafo de explicacao ficaria semanas no alto da
  // pagina, a primeira coisa que alguem de fora le. Durante a
  // rodada aberta ele nao some: ali o zero e momentaneo (todo nome lido tem
  // candidato) e um cartao que pisca parece defeito; so encurta a frase.
  if (semComp && contagem.sem_competicao === 0) {
    semComp.classList.toggle('escondido', fechada);
    if (!fechada) semComp.querySelector('.ajuda').textContent = 'Nenhum agora';
  } else if (semComp) {
    semComp.classList.remove('escondido');
  }
  if (fechada && contagem.sem_competicao === 0 && estado.filtro === 'sem_competicao') {
    estado.filtro = filtroInicial();
  }
  // o que da para fazer agora vem primeiro: livres, depois os que voltam
  const painelCartoes = document.querySelector('.cartoes');
  if (fechada && painelCartoes && cardLivres && cardAguard
      && painelCartoes.firstElementChild !== cardLivres) {
    painelCartoes.prepend(cardLivres, cardAguard);
  }
  // "Em leilao" e so o que a lista oficial (lista-competicao.txt) ainda
  // mostra, e so ela responde: LEILAO NAO ACABA JUNTO COM A RODADA. Medido
  // em 17/09/2026, uma hora depois do fim da rodada de setembro: a lista
  // ainda trazia um leilao aberto na rodada de julho, 25 da de agosto e 2
  // da de setembro (L5 em docs/limitacoes-registrobr.md). Supor que "todo
  // leilao acaba 24 h depois do fechamento" anunciaria "os leiloes desta
  // rodada terminaram" com leilao rodando. Por isso nao ha relogio nenhum neste cartao: ele some quando zera.
  const cardLeilao = document.querySelector('[data-filtro="leilao"]');
  if (cardLeilao) cardLeilao.classList.toggle('escondido', contagem.leilao === 0);
  if (contagem.leilao === 0 && estado.filtro === 'leilao') estado.filtro = filtroInicial();
  const proxima = proximaAberturaDoCalendario();
  const ajudaAguard = cardAguard && cardAguard.querySelector('.ajuda');
  if (ajudaAguard && proxima) {
    ajudaAguard.textContent = 'Dois ou mais pediram e ninguém levou: voltam na rodada que '
      + `abre em ${proxima.toLocaleDateString('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit' })}`;
  }
  // Lista nova ainda sem leitura: os cartoes que dependem da
  // consulta ao Registro.br somem em vez de dizer "0" como se fosse fato;
  // fica "Toda a rodada", pela nota, e o rodape diz quando a conferencia comeca
  if (aConferir()) {
    for (const f of ['joias', 'sem_competicao', 'disputados', 'elegiveis', 'livres',
      'aguardando', 'todos']) {
      const cartao = document.querySelector(`.cartao[data-filtro="${f}"]`);
      if (cartao) cartao.classList.add('escondido');
    }
  }
}

/** "a conferencia dos nomes comeca com a rodada, em 14/10, as 15h" */
function textoDaConferencia() {
  const inicio = new Date((estado.dados.rodada || {}).inicio);
  if (isNaN(inicio)) return 'A conferência dos nomes começa com a rodada.';
  const quando = `em ${inicio.toLocaleDateString('pt-BR',
    { timeZone: FUSO, day: '2-digit', month: '2-digit' })}, às ${horaCurta(inicio)}`;
  return inicio > agoraDaPagina()
    ? `A conferência dos nomes começa com a rodada, ${quando} (horário de Brasília).`
    : `A conferência dos nomes começou com a rodada, ${quando} (horário de Brasília); `
      + 'a primeira leitura chega nas próximas horas.';
}

function pintarPainel() {
  const d = estado.dados;
  pintarContagens();

  pintarCarimbo();
  pintarFase(d.rodada);

  // O denominador nao e a rodada inteira: 125 mil nomes vao para um filtro
  // de qualidade e so os selecionados sao consultados. Dizer so "X de 125
  // mil" daria a entender que o resto seria coberto um dia, e nao sera.
  // A frase e para quem procura dominio. A conta do pool (quantos
  // passam no filtro, quantos na fila) mora na aba Dados e em Como
  // selecionamos, onde quem quer o numero vai procurar.
  let texto = aConferir()
    ? `A lista traz ${num(d.total_rodada || 0)} nomes, aqui pela nota. ${textoDaConferencia()}`
    : `Dos ${num(d.total_rodada)} nomes da rodada, consultamos no `
      + `Registro.br os que têm melhor nota: ${num(d.itens.length)} até agora.`;
  if (d.em_leilao_em) {
    texto += ` Leilões conferidos na lista oficial de ${formatarDataCurta(d.em_leilao_em)}.`;
  }
  // no celular o title da estrela nao aparece: sem esta frase, ninguem sabe
  // para que ela serve ate tocar
  if (!estado.acompanhados.size) {
    texto += ' Toque na estrela de um nome para acompanhá-lo e ver o que mudou quando voltar.';
  }
  $('#rodape-painel').textContent = texto;

  // Os numeros das paginas de texto sao gravados no HTML pelo build
  // (garimpo/web/paginas.py); aqui so o rodape desta pagina.
  const rodape = document.querySelector('#rodape-gerado');
  if (rodape) rodape.textContent = formatarData(d.gerado_em);
}

/**
 * A idade do instantaneo, no cabecalho. Relativa ("atualizado ha 2 h")
 * porque e isso que decide se vale conferir; a data completa fica no title
 * e no rodape. Repintada a cada minuto: a pagina costuma ficar aberta.
 */
function pintarCarimbo() {
  const d = estado.dados;
  const quando = new Date(d.gerado_em);
  const el = $('#carimbo');
  if (!el || isNaN(quando)) return;
  el.textContent = `atualizado ${idadeTexto(Math.max(0, (agoraMs() - quando) / 1000))}`;
  el.title = `Instantâneo de ${formatarData(d.gerado_em)}, `
           + `${num(d.itens.length)} domínios verificados`;
}

/**
 * Cria as opcoes do seletor de extensoes, uma vez so (chamada a cada
 * repintura do painel, duplicaria a lista inteira). Os numeros de cada opcao sao reescritos a cada aplicar(), em
 * pintarContagensDosFiltros.
 */
function preencherExtensoes(daRodada) {
  // chamada de novo com as extensoes da rodada inteira: so acrescenta as
  // que faltam, sem duplicar as que ja estao no seletor
  const sel = $('#extensao');
  if (daRodada) {
    const ja = new Set([...sel.options].map((o) => o.value));
    for (const ext of daRodada) {
      if (ja.has(ext)) continue;
      const o = document.createElement('option');
      o.value = ext;
      o.textContent = `.${ext}`;
      o.dataset.rotulo = `.${ext}`;
      sel.appendChild(o);
    }
    return;
  }
  const d = estado.dados;
  const contagem = new Map();
  for (const it of d.itens) {
    const e = extensaoDe(it[D]);
    contagem.set(e, (contagem.get(e) || 0) + 1);
  }
  [...contagem.entries()]
    .sort((a, b) => b[1] - a[1])
    .forEach(([ext, n]) => {
      const o = document.createElement('option');
      o.value = ext;
      o.textContent = `.${ext} (${num(n)})`;
      o.dataset.rotulo = `.${ext}`;
      sel.appendChild(o);
    });
}

/**
 * O seletor de ramo de negocio. As categorias vem do JSON, na ordem dos
 * bits de dominio/categorias.py: a regra mora num lugar so.
 */
function preencherCategorias() {
  const sel = $('#categoria');
  const lista = estado.dados.categorias || [];
  if (!sel) return;
  if (!lista.length) {
    sel.classList.add('escondido');     // instantaneo antigo, sem categoria
    return;
  }
  lista.forEach((c, i) => {
    const o = document.createElement('option');
    o.value = String(i);
    o.textContent = c.rotulo;
    o.dataset.rotulo = c.rotulo;
    sel.appendChild(o);
  });
}

export {
  passaMarca,
  visiveis,
  pintarContagens,
  textoDaConferencia,
  pintarPainel,
  pintarCarimbo,
  preencherExtensoes,
  preencherCategorias,
};
