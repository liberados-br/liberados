// O endereco da pagina: ler e escrever os parametros.
import { fotografia, garantirLinha, gravarAcompanhados, iniciarRevisao } from './acompanhados.js';
import { buscaNaRodada } from './busca.js';
import { MAXIMO_LISTA, avisarLista, lerListaDoLink, linkDaLista } from './compartilhada.js';
import { $, FILTROS, NOMES_FILTRO, estado } from './estado.js';
import { aplicar } from './filtros.js';
import { pintarContagens } from './painel.js';
import { num } from './util.js';

// ------------------------------------------------- endereco = o que esta na tela

/*
 * O endereco da pagina carrega o cartao e os filtros, como em
 * loja online e na busca do GitHub: /?filtro=leilao&ext=com.br&ramo=pet.
 * Quem recebe o link abre a mesma lista, conferida no navegador dele. So vai
 * no endereco o que difere do padrao. "Acompanhando" vira ?filtro=acompanhados
 * na barra (recarregar mostra os seus); ao compartilhar vira ?lista= com os
 * nomes, porque o amigo nao tem as suas estrelas.
 */
function parametrosDaTela(paraCompartilhar = false) {
  const q = new URLSearchParams();
  const f = estado.filtro;
  let lista = null;
  if (f === 'lista') lista = estado.lista;
  else if (f === 'acompanhados' && paraCompartilhar) lista = estado.acompanhados.keys();
  // ao compartilhar o cartao vai sempre: o cartao inicial de quem abre pode ser outro
  // a busca na rodada inteira nao leva cartao: quem abre o link cai nela
  // tambem; cartao escolhido com busca vai sempre, senao o link voltaria a
  // rodada inteira
  else if (f && !buscaNaRodada()
           && (paraCompartilhar || f !== estado.filtroInicial || estado.cartaoEscolhido)) {
    q.set('filtro', f);
  }
  const busca = estado.busca.trim();
  if (busca) q.set('busca', busca);
  if (estado.ordem !== 'nota') q.set('ordem', estado.ordem);
  if (estado.extensao) q.set('ext', estado.extensao);
  const cat = (estado.dados.categorias || [])[Number(estado.categoria)];
  if (estado.categoria !== '' && cat) q.set('ramo', cat.nome);
  if (estado.marca) q.set('marca', estado.marca);
  if (estado.situacao) q.set('situacao', estado.situacao);
  // a lista vai por ultimo e com virgula legivel (so [a-z0-9.-], sem escape)
  let texto = q.toString();
  if (lista) {
    const nomes = linkDaLista(lista).split('?lista=')[1];
    texto = `lista=${nomes}${texto ? `&${texto}` : ''}`;
  }
  return texto;
}

function atualizarEndereco() {
  if (!estado.dados) return;
  const busca = parametrosDaTela();
  const novo = location.pathname + (busca ? `?${busca}` : '') + location.hash;
  if (novo !== location.pathname + location.search + location.hash) {
    history.replaceState(null, '', novo);
  }
}

async function compartilharTela() {
  const busca = parametrosDaTela(true);
  const url = `${location.origin}/${busca ? `?${busca}` : ''}`;
  const n = estado.atual.length;
  const partes = [buscaNaRodada() ? 'Toda a rodada' : (NOMES_FILTRO[estado.filtro] || 'Domínios')];
  if (estado.extensao) partes.push(`.${estado.extensao}`);
  const cat = (estado.dados.categorias || [])[Number(estado.categoria)];
  if (estado.categoria !== '' && cat) partes.push(cat.rotulo);
  const termo = estado.busca.trim();
  if (termo) partes.push(/^["“”].*["“”]$/.test(termo) ? termo : `“${termo}”`);
  const texto = `${partes.join(' · ')}: ${num(n)} ${n === 1 ? 'domínio' : 'domínios'} no Liberados`;
  try {
    if (window.matchMedia('(pointer: coarse)').matches && navigator.share) {
      await navigator.share({ title: 'Liberados', text: texto, url });
      return;
    }
    await navigator.clipboard.writeText(url);
    const cortada = estado.filtro === 'acompanhados' && estado.acompanhados.size > MAXIMO_LISTA;
    avisarLista(cortada ? `Link copiado com os primeiros ${MAXIMO_LISTA} nomes` : 'Link copiado');
  } catch (e) {
    // fechar a folha de compartilhar cai aqui; sem area de transferencia,
    // mostra o link para copiar a mao
    if (e && e.name !== 'AbortError') window.prompt('Copie o link:', url);
  }
}

function acompanharLista() {
  let novos = 0;
  for (const d of estado.lista) {
    if (estado.acompanhados.has(d)) continue;
    estado.acompanhados.set(d, fotografia(garantirLinha(d)));
    novos++;
  }
  gravarAcompanhados();
  if (novos) iniciarRevisao();
  pintarContagens();
  aplicar();
  avisarLista(novos ? `${novos} ${novos === 1 ? 'nome adicionado' : 'nomes adicionados'} aos seus acompanhados`
                    : 'Você já acompanha todos');
}

function pintarBotoesDaLista() {
  $('#btn-lista-acompanhar').classList.toggle('escondido',
    !(estado.filtro === 'lista' && estado.lista.size));
}

/**
 * Link direto para um recorte: as paginas por ramo levam a
 * /?ramo=pet, e um link compartilhado pode trazer ?busca= ou ?filtro=.
 */
function lerParametros() {
  const q = new URLSearchParams(location.search);
  const ramo = q.get('ramo');
  const i = (estado.dados.categorias || []).findIndex((c) => c.nome === ramo);
  if (i >= 0) {
    estado.categoria = String(i);
    $('#categoria').value = String(i);
  }
  const busca = (q.get('busca') || '').trim();
  if (busca) {
    estado.busca = busca;
    $('#busca').value = busca;
  }
  const filtro = q.get('filtro');
  if (filtro && FILTROS[filtro] && filtro !== 'lista') estado.filtroPedido = filtro;
  const opcao = (sel, v) => [...$(sel).options].some((o) => o.value === v);
  const ordem = q.get('ordem');
  if (ordem && opcao('#ordem', ordem)) {
    estado.ordem = ordem;
    $('#ordem').value = ordem;
  }
  const marca = q.get('marca');
  if (marca && opcao('#marca', marca)) {
    estado.marca = marca;
    $('#marca').value = marca;
  }
  const situacao = q.get('situacao');
  if (situacao && opcao('#situacao', situacao)) {
    estado.situacao = situacao;
    $('#situacao').value = situacao;
  }
  // extensao que so existe na rodada inteira ainda nao tem opcao: cria
  const ext = (q.get('ext') || '').toLowerCase();
  if (/^([a-z0-9-]+\.)+br$/.test(ext)) {
    if (!opcao('#extensao', ext)) {
      const o = document.createElement('option');
      o.value = ext;
      o.textContent = `.${ext}`;
      o.dataset.rotulo = `.${ext}`;
      $('#extensao').appendChild(o);
    }
    estado.extensao = ext;
    $('#extensao').value = ext;
  }
  const lista = lerListaDoLink(q.get('lista'));
  if (lista.size) {
    estado.lista = lista;
    estado.filtroPedido = 'lista';
  }
}

export {
  parametrosDaTela,
  atualizarEndereco,
  compartilharTela,
  acompanharLista,
  pintarBotoesDaLista,
  lerParametros,
};
