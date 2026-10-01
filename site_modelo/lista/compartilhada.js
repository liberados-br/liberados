// A lista que alguem compartilhou por link.
import { $ } from './estado.js';

// ------------------------------------------------------ lista compartilhada

/*
 * "Manda a minha lista para um amigo". Sem servidor, o link
 * carrega os proprios nomes: /?lista=one,peca,grita.ia.br (.com.br sai
 * abreviado). Quem abre ve a lista com a situacao do instantaneo, a pagina
 * confere as primeiras ao vivo, e nada entra nos favoritos dele sem o botao
 * "Acompanhar todos".
 */
const MAXIMO_LISTA = 200;

function lerListaDoLink(bruto) {
  const nomes = new Set();
  for (const pedaco of String(bruto || '').toLowerCase().split(',')) {
    let d = pedaco.trim().replace(/^www\./, '');
    if (!d) continue;
    if (!d.includes('.')) d += '.com.br';
    if (!/^[a-z0-9][a-z0-9-]{0,62}(\.[a-z0-9-]{1,63})*\.br$/.test(d)) continue;
    nomes.add(d);
    if (nomes.size >= MAXIMO_LISTA) break;
  }
  return nomes;
}

function linkDaLista(nomes) {
  const curtos = [...nomes].slice(0, MAXIMO_LISTA)
    .map((d) => (d.endsWith('.com.br') && d.split('.').length === 3 ? d.slice(0, -7) : d));
  return `${location.origin}/?lista=${curtos.join(',')}`;
}

function avisarLista(texto) {
  const el = $('#lista-aviso');
  el.textContent = texto;
  clearTimeout(el.timer);
  el.timer = setTimeout(() => { el.textContent = ''; }, 3000);
}

export {
  MAXIMO_LISTA,
  lerListaDoLink,
  linkDaLista,
  avisarLista,
};
