// Quantos nomes por pagina, guardado no aparelho.
import { $, CHAVE_POR_PAGINA, TAMANHOS_PAGINA, estado } from './estado.js';
import { pintarTabela } from './tabela.js';

// ---------------------------------------------------------- por pagina

function lerPorPagina() {
  try {
    const n = Number(localStorage.getItem(CHAVE_POR_PAGINA));
    if (TAMANHOS_PAGINA.includes(n)) estado.porPagina = n;
  } catch (e) { /* navegacao anonima ou storage bloqueado */ }
  $('#por-pagina').value = String(estado.porPagina);
}

/** Troca o tamanho sem perder de vista o primeiro nome que estava na tela. */
function trocarPorPagina(valor) {
  const n = Number(valor);
  if (!TAMANHOS_PAGINA.includes(n)) return;
  const primeiro = estado.pagina * estado.porPagina;
  estado.porPagina = n;
  estado.pagina = Math.floor(primeiro / n);
  try {
    localStorage.setItem(CHAVE_POR_PAGINA, String(n));
  } catch (e) { /* vale so para esta aba */ }
  pintarTabela();
}

export {
  lerPorPagina,
  trocarPorPagina,
};
