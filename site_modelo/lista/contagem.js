// Quantos nomes cada filtro tem.
import { $ } from './estado.js';
import { sincronizarSeletores } from './seletores.js';
import { num } from './util.js';

// --------------------------------------------------- contagem dos filtros

/**
 * Escreve em cada opcao quantos nomes ela daria agora. A opcao nao some nem
 * muda de lugar (o valor escolhido e o link ?ramo= continuam valendo); no
 * painel de extensao e ramo, a de zero fica escondida, salvo a escolhida.
 */
function pintarContagensDosFiltros(porExt, porCat, porSit) {
  const escrever = (sel, contar) => {
    if (!sel) return;
    for (const o of sel.options) {
      if (!o.value) continue;
      o.dataset.rotulo = o.dataset.rotulo || o.textContent.replace(/ \([\d.]+\)$/, '');
      const n = contar(o.value);
      o.dataset.n = String(n);
      o.textContent = `${o.dataset.rotulo} (${num(n)})`;
    }
  };
  escrever($('#extensao'), (v) => porExt.get(v) || 0);
  escrever($('#categoria'), (v) => porCat[Number(v)] || 0);
  escrever($('#situacao'), (v) => porSit.get(v) || 0);
  for (const o of $('#situacao').options) o.hidden = Boolean(o.value) && o.dataset.n === '0' && !o.selected;
  sincronizarSeletores();
}

export {
  pintarContagensDosFiltros,
};
