// Os eventos da pagina.
import { alternarAcompanhar } from './acompanhados.js';
import { limparBusca, modoDaBusca } from './busca.js';
import { avisar, enfileirar, fila } from './conferencia.js';
import { baixarCsv } from './csv.js';
import { levarALista } from './dobras.js';
import { acompanharLista, compartilharTela } from './endereco.js';
import { $, D, MINIMO_BUSCA_RODADA, estado } from './estado.js';
import { aplicar } from './filtros.js';
import { abrirLembrete } from './linha.js';
import { abrirLinks } from './links.js';
import { pintarContagens } from './painel.js';
import { trocarPorPagina } from './porpagina.js';
import { escolherFiltro } from './resumo.js';
import { carregarRodada } from './rodada.js';
import { buscarNaRodadaInteira, limparSoFiltros, pintarTabela } from './tabela.js';

// ------------------------------------------------------------------ eventos

function ligarEventos() {
  // um ouvinte so, na tabela: as linhas sao refeitas a cada pagina
  $('#corpo-tabela').addEventListener('click', (e) => {
    const links = e.target.closest('[data-links]');
    if (links) {
      abrirLinks(links.dataset.links);
      return;
    }
    const lembrar = e.target.closest('[data-lembrar]');
    if (lembrar) {
      abrirLembrete(lembrar.dataset.lembrar);
      return;
    }
    const estrela = e.target.closest('[data-acompanhar]');
    if (estrela) {
      alternarAcompanhar(estrela.dataset.acompanhar);
      return;
    }
    const botao = e.target.closest('[data-conferir]');
    if (!botao) return;
    const it = estado.porDominio.get(botao.dataset.conferir);
    if (!it) return;
    if (window.Disputa && window.Disputa.bloqueado && window.Disputa.bloqueado()) {
      avisar(window.Disputa.mensagemBloqueio());
      pintarTabela();               // desenha o botao ja desabilitado
      return;
    }
    estado.aoVivo.delete(it[D]);   // pedido explicito: confere de novo
    fila.parada = false;           // e retoma, se um erro tinha parado a fila
    enfileirar([it], true);
  });

  document.querySelectorAll('.cartao').forEach((c) => {
    c.addEventListener('click', () => { escolherFiltro(c.dataset.filtro); levarALista(); });
  });

  let timer;
  const buscar = () => {
    clearTimeout(timer);
    estado.busca = $('#busca').value;
    estado.pagina = 0;
    aplicar({ aosPoucos: true });
  };
  $('#busca').addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(buscar, 100);
  });
  // A rodada inteira (todos.json) comeca a baixar no primeiro foco, nao na
  // abertura: quem nao busca nao paga por ela
  $('#busca').addEventListener('focus', () => {
    carregarRodada({ aviso: false }).then(() => {
      if (estado.rodada && modoDaBusca(estado.busca).termo.length >= MINIMO_BUSCA_RODADA) aplicar();
    });
    // o resumo passa a dizer "buscando nos N nomes" enquanto baixa
    if (!estado.rodada && modoDaBusca(estado.busca).termo.length >= MINIMO_BUSCA_RODADA) aplicar();
  }, { once: true });
  // Enter leva a lista. No modo busca ela ja esta logo abaixo da caixa: no
  // celular o teclado fecha e a caixa vai ao topo, e o resumo e as primeiras
  // linhas cabem na tela; no computador o foco fica na caixa
  const confirmarBusca = () => {
    buscar();
    if (!estado.modoBusca) {
      $('#controles').scrollIntoView({ block: 'start' });
      return;
    }
    if (window.matchMedia && matchMedia('(pointer: coarse)').matches) $('#busca').blur();
    $('.busca-topo').scrollIntoView({ block: 'start' });
  };
  $('#busca').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') confirmarBusca();
  });
  // o botao Buscar e o Enter de quem nao sabe que a lista ja filtra sozinha;
  // com a caixa vazia ele so leva o cursor para ela
  $('#botao-buscar').addEventListener('click', () => {
    if (!$('#busca').value.trim()) {
      $('#busca').focus();
      return;
    }
    confirmarBusca();
  });
  $('#limpar-busca').addEventListener('click', limparBusca);
  $('#resumo-busca').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if ('limparFiltros' in b.dataset) {
      // o botao some com o resumo refeito: o foco fica no resumo, nao no vazio
      limparSoFiltros();
      const resumo = $('#resumo-busca');
      resumo.tabIndex = -1;
      resumo.focus({ preventScroll: true });
      return;
    }
    if (b.dataset.cartao && b.getAttribute('aria-pressed') !== 'true') {
      escolherFiltro(b.dataset.cartao);
    } else {
      buscarNaRodadaInteira();
    }
    if ('rolar' in b.dataset) $('#controles').scrollIntoView({ block: 'start' });
  });

  $('#ordem').addEventListener('change', (e) => {
    estado.ordem = e.target.value;
    estado.pagina = 0;
    aplicar();
  });

  $('#extensao').addEventListener('change', (e) => {
    estado.extensao = e.target.value;
    estado.pagina = 0;
    aplicar();
  });

  $('#categoria').addEventListener('change', (e) => {
    estado.categoria = e.target.value;
    estado.pagina = 0;
    aplicar();
  });

  $('#marca').addEventListener('change', (e) => {
    estado.marca = e.target.value;
    estado.pagina = 0;
    pintarContagens();
    aplicar();
  });

  $('#situacao').addEventListener('change', (e) => {
    estado.situacao = e.target.value;
    estado.pagina = 0;
    aplicar();
  });

  $('#btn-compartilhar').addEventListener('click', compartilharTela);
  $('#btn-lista-acompanhar').addEventListener('click', acompanharLista);

  $('#por-pagina').addEventListener('change', (e) => trocarPorPagina(e.target.value));

  $('#btn-anterior').addEventListener('click', () => {
    if (estado.pagina > 0) { estado.pagina--; pintarTabela(); window.scrollTo(0, 0); }
  });
  $('#btn-proxima').addEventListener('click', () => {
    estado.pagina++; pintarTabela(); window.scrollTo(0, 0);
  });

  $('#btn-csv').addEventListener('click', baixarCsv);
}

export {
  ligarEventos,
};
