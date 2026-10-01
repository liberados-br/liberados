// O arranque: le o dados.json e liga tudo.
import { garantirLinha, iniciarRevisao, lerAcompanhados, mudancasDesdeAUltimaVisita, podeNotificar, registrarServico } from './acompanhados.js';
import { buscaNaRodada, buscaPedeModo } from './busca.js';
import { conferirAoAbrir, gravarConferidos, lerConferidos, restaurarConferido } from './conferencia.js';
import { abrirFiltrosSeAtivos, ligarDobras, rotularFiltros } from './dobras.js';
import { lerParametros } from './endereco.js';
import { $, D, EL, SS, definirIndices, estado, iDisputado, iLeilao, iSemComp } from './estado.js';
import { ligarEventos } from './eventos.js';
import { aplicar } from './filtros.js';
import { pintarCarimbo, pintarPainel, preencherCategorias, preencherExtensoes } from './painel.js';
import { lerPorPagina } from './porpagina.js';
import { iniciarRelogio } from './relogio.js';
import { filtroInicial } from './resumo.js';
import { carregarRodada } from './rodada.js';
import { sincronizarSeletores, tornarSeletor } from './seletores.js';

// -------------------------------------------------------------------- inicio

async function iniciar() {
  const r = await fetch('dados.json');
  estado.dados = await r.json();

  definirIndices(estado.dados.status);

  // Defesa em profundidade: o exportador ja aplica a lista oficial de
  // leiloes, mas um instantaneo antigo nao aplicava. Nome na lista nunca e
  // "sem competicao", seja la o que diga a ultima consulta.
  for (const it of estado.dados.itens) {
    if (it[EL] === 1 && (it[SS] === iSemComp || it[SS] === iDisputado)) {
      it[SS] = iLeilao;
    }
  }

  estado.porDominio = new Map(estado.dados.itens.map((it) => [it[D], it]));

  // os .ics que o exportador gerou: leilao segundo o instantaneo, antes de
  // qualquer conferencia ao vivo mexer na situacao
  estado.lembretesServidos = new Set(
    estado.dados.itens.filter((it) => it[SS] === iLeilao).map((it) => it[D]));
  // Depois da defesa acima e dos lembretes: a leitura guardada e mais nova
  // que o instantaneo e manda sobre ele, mas o .ics so existe para quem o
  // exportador viu em leilao.
  estado.conferidos = lerConferidos();
  for (const it of estado.dados.itens) restaurarConferido(it);
  gravarConferidos();          // sem o que venceu ou o servidor ja cobriu

  estado.acompanhados = lerAcompanhados();
  for (const dominio of estado.acompanhados.keys()) garantirLinha(dominio);

  ligarEventos();
  pintarPainel();
  preencherExtensoes();
  preencherCategorias();
  tornarSeletor($('#ordem'));
  tornarSeletor($('#extensao'), { busca: true, nomeCurto: 'extensão' });
  tornarSeletor($('#categoria'), { busca: true, nomeCurto: 'ramo' });
  tornarSeletor($('#marca'));
  tornarSeletor($('#situacao'));
  tornarSeletor($('#por-pagina'));
  lerParametros();
  lerPorPagina();
  sincronizarSeletores();
  rotularFiltros();   // o endereco pode ter trazido filtro: a dobra fechada conta
  mudancasDesdeAUltimaVisita();

  // Rodada nova: quando a lista muda, o instantaneo recomeca do zero e todo
  // cartao mostra 0 ate as consultas acumularem. filtroInicial() cai entao
  // na lista completa, a unica com o que mostrar.
  estado.filtroInicial = filtroInicial();
  estado.filtro = estado.filtroPedido || estado.filtroInicial;
  // ?filtro= no link e cartao escolhido: a busca fica nele
  estado.cartaoEscolhido = Boolean(estado.filtroPedido);
  // link com ?busca= de 3 letras ou mais reabre a busca na rodada inteira
  if (estado.filtro === 'rodada' || buscaNaRodada()) await carregarRodada();
  aplicar();
  rotularFiltros();   // aplicar() pode ter limpado a situacao sem avisar
  // Link com ordem, filtro, ramo ou extensao (e sem busca, que tem o modo
  // proprio) veio para ver a lista: no celular ela fica duas telas abaixo
  // do relogio. Depois de desenhar, para a rolagem cair no lugar certo.
  const pedido = new URLSearchParams(location.search);
  if (!pedido.get('busca') && ['ordem', 'filtro', 'ramo', 'ext', 'marca', 'situacao'].some((k) => pedido.has(k))) {
    // no celular o botao dos filtros vem antes de #controles: com filtro no
    // link a dobra abre (quem chega precisa ver qual), e a rolagem para nele
    abrirFiltrosSeAtivos();
    requestAnimationFrame(() => {
      const c = [$('.dobra-filtros'), $('#controles')].find((e) => e && e.offsetParent !== null);
      if (c) c.scrollIntoView({ block: 'start' });
    });
  }
  conferirAoAbrir();

  if (estado.acompanhados.size) {
    if (podeNotificar()) registrarServico();
    iniciarRevisao();
  }
  setInterval(pintarCarimbo, 60 * 1000);
}

// Link com ?busca= de 3 letras ou mais: o modo busca entra antes de o JSON
// chegar, para o relogio e os cartoes nao aparecerem e sumirem (sem script
// inline por causa da CSP, este e o primeiro ponto possivel)
try {
  const q = new URLSearchParams(location.search);
  if (!q.has('lista') && buscaPedeModo(q.get('busca') || '', q.get('filtro'))) {
    // sem teclado aberto a caixa pode subir: no celular o subtitulo sai
    document.body.classList.add('modo-busca', 'modo-busca-link');
  }
} catch (e) { /* sem o modo, a pagina abre como sempre */ }
iniciarRelogio();
ligarDobras();
iniciar().catch((e) => {
  console.error('falha ao iniciar', e);
  const vazio = document.querySelector('#vazio');
  if (vazio) {
    vazio.classList.remove('escondido');
    vazio.textContent = 'Não consegui carregar os dados desta página.';
  }
});
