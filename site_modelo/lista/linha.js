// Uma linha da lista: selo, contagem, etiquetas e acoes.
import { agendarReativacaoConferir } from './conferencia.js';
import { C, CH1, CH2, D, E, LK, M, MK, N, NAO_VERIFICADO, SN, SS, estado, iLeilao, iLivre, iRegistrado } from './estado.js';
import { rodadaFechada } from './fase.js';
import { FUSO, agoraDaPagina, esc, frescorDe, idadeTexto, num } from './util.js';

// ------------------------------------------------------------------- tabela

const SELOS = {
  LIBERACAO_LIVRE: ['sem_competicao', 'sem competição'],
  LIBERACAO_DISPUTADA: ['disputado', 'disputado'],
  COMPETITIVO: ['leilao', 'leilão aberto'],
  LIVRE: ['livre', 'livre agora'],
  REGISTRADO: ['registrado', 'registrado'],
  // enumeracao oficial do ISAVAIL: status 5, 3 e 1
  AGUARDANDO_LIBERACAO: ['registrado', 'espera a próxima rodada'],
  INDISPONIVEL: ['registrado', 'indisponível'],
  LIVRE_COM_TICKET: ['disputado', 'pedido pendente'],
};

// Com a rodada fechada, as leituras da rodada sao historia: "sem competicao"
// ja nao convida a se candidatar, e "disputado" ja travou.
const SELOS_FECHADA = {
  LIBERACAO_LIVRE: ['sem_competicao', 'fechou sem candidato'],
  LIBERACAO_DISPUTADA: ['disputado', 'travou'],
  AGUARDANDO_LIBERACAO: ['disputado', 'volta na próxima rodada'],
};
// Nao existe selo de "leilao encerrado" por relogio. COMPETITIVO quer dizer
// que o nome estava na ultima lista-competicao.txt lida, ou seja, o leilao
// dele acontece AGORA, mesmo com a rodada fechada — leilao nao acaba junto
// com a rodada (L5, medido em 17/09/2026).
//
// A lista manda nos DOIS sentidos, e e o que sustenta este selo:
// aplicar_lista_de_leiloes() promove quem entrou e
// encerrar_leiloes_fora_da_lista() apaga a leitura de quem saiu
// (garimpo/adaptadores/repositorio.py), entao o nome volta para a fila e o
// proximo avail diz se foi registrado ou se travou. Sem a segunda metade,
// "leilao aberto" sobreviveria dias a um leilao ja resolvido.

function selo(it) {
  if (it[SS] === NAO_VERIFICADO) {
    return '<span class="selo nao-verificado">não verificado</span>';
  }
  const nome = estado.dados.status[it[SS]];
  // Rodada fechada, elegivel e registrado: o desfecho e a situacao, numa
  // etiqueta so, em vez de "registrado" mais uma "leilao encerrado" ao lado.
  if (it[E] && it[SS] === iRegistrado && rodadaFechada()) {
    return '<span class="selo registrado">leilão encerrado'
         + '<span class="sr-apenas">: foi a leilão nesta rodada e já tem dono</span></span>';
  }
  const fechada = rodadaFechada() && SELOS_FECHADA[nome];
  const [cls, rotulo] = fechada || SELOS[nome] || ['desconhecido', nome];
  return `<span class="selo ${cls}">${esc(rotulo)}</span>`;
}

/**
 * A cor nao pode carregar sozinha o significado (WCAG 1.4.1): cada nivel tem
 * forma propria no CSS e um texto so para leitor de tela.
 */
function competindo(it) {
  if (it[SS] === NAO_VERIFICADO) {
    return '<span class="competindo competindo-nulo">?'
         + '<span class="sr-apenas"> ainda não verificado; use conferir</span></span>';
  }
  if (it[SS] === iRegistrado || it[SS] === iLivre) {
    return '<span class="competindo competindo-nulo">&ndash;</span>';
  }
  const n = it[C];
  // Com a rodada fechada, "0" num nome que travou ou voltou nao e noticia:
  // os tickets somem do avail no fim da rodada, e o selo ja conta o desfecho
  // (um 0 verde ao lado de "volta na proxima rodada" confundiria).
  if (n === 0 && rodadaFechada()) {
    return '<span class="competindo competindo-nulo">&ndash;</span>';
  }
  // O numero e de uma leitura, nao de agora: um nome pode seguir com
  // "1 candidato" dias depois de o pedido virar registro. A idade visivel
  // mora ao lado do selo (carimboDaLinha), uma vez por linha; aqui fica so
  // no texto para leitor de tela.
  const lido = idadeDaContagem(it);
  const ao = lido ? `, lido ${lido}` : '';
  // leilao so abre com dois tickets; se a contagem guardada e menor, a
  // situacao veio da lista oficial e o numero exato ainda nao foi lido
  if (it[SS] === iLeilao && n < 2) {
    return '<span class="competindo competindo-pouco">≥2'
         + `<span class="sr-apenas"> candidatos, pelo menos dois${esc(ao)}</span></span>`;
  }
  if (n === 0) {
    return '<span class="competindo competindo-zero">0'
         + `<span class="sr-apenas"> candidatos visíveis, pode ser zero ou um${esc(ao)}</span>`
         + `</span>`;
  }
  const classe = n <= 2 ? 'competindo-pouco' : 'competindo-muito';
  const aviso = n <= 2 ? 'poucos candidatos' : 'muitos candidatos';
  const quantos = n === 1 ? '1 candidato' : `${n} candidatos`;
  const chegada = chegadaTexto(it);
  // a propria contagem abre quem disputa (web/disputa.js): consulta feita no
  // navegador do visitante, sob demanda
  if (window.Disputa) {
    return window.Disputa.contagem(it[D], n, classe,
      `${quantos}${ao}, ${aviso}` + (chegada ? `; ${chegada}` : ''), chegada, lido);
  }
  const title = chegada ? ` title="${esc(chegada)}"` : '';
  return `<span class="competindo ${classe}"${title}>${n}`
       + `<span class="sr-apenas"> ${esc(quantos + ao)}, ${aviso}`
       + (chegada ? `; ${esc(chegada)}` : '') + `</span></span>`;
}

/** A idade da leitura que deu a contagem: "agora" se conferida nesta aba. */
function idadeDaContagem(it) {
  const f = frescorDe(it);
  if (!f) return '';
  return f.aoVivo ? 'agora' : idadeTexto(f.idade);
}

/**
 * Quando os concorrentes chegaram, pelo numero do ticket (sequencial). E
 * estimativa por cima: o ticket pode ter sido emitido antes de a varredura
 * ve-lo. So aparece quando o instantaneo trouxe o dado.
 */
function chegadaTexto(it) {
  const primeiro = it[CH1], ultimo = it[CH2];
  if (!primeiro) return '';
  const fmt = (s) => new Date(s * 1000).toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: FUSO });
  // Ticket anterior ao primeiro ponto da curva so tem um limite: "ate" o
  // instante daquele ponto. Sem isto, dezenas de nomes apareceriam
  // "chegando" no mesmo minuto.
  const inicio = (estado.dados.ritmo || {}).desde;
  const quando = (s) => (inicio && s <= inicio ? `até ${fmt(s)}` : `por volta de ${fmt(s)}`);
  if (!ultimo || ultimo === primeiro || (it[C] || 0) < 2) {
    return `primeiro concorrente ${quando(primeiro)}`;
  }
  return `primeiro concorrente ${quando(primeiro)}; último visível ${quando(ultimo)}`;
}

function carimboDaLinha(it) {
  const f = frescorDe(it);
  if (!f) return '';
  if (f.aoVivo) {
    return '<span class="idade ao-vivo">conferido agora, no seu navegador</span>';
  }
  const texto = idadeTexto(f.idade)
    + (f.navegador ? ' · no seu navegador' : '')
    + (f.vencido ? ' · a confirmar' : '');
  const classe = f.vencido ? ' vencida' : (f.navegador ? ' navegador' : '');
  return `<span class="idade${classe}">${esc(texto)}</span>`;
}

/** O marcador de quem mudou ao ser conferido nesta lista (ver anotarMudanca). */
function marcadorDaMudanca(it) {
  const m = estado.retidos.get(it[D]);
  return m ? `<span class="mudou">${esc(m.texto)}</span>` : '';
}

function botaoConferir(it) {
  const nome = esc(it[D]);
  const ocupado = estado.conferindo === it[D];
  // depois de um 429, window.Disputa bloqueia consultas novas por 5 min
  // (compartilhado com "quem disputa" e a ficha); o botao some disabled
  // em vez de deixar tentar de novo na hora
  const bloqueado = !ocupado && window.Disputa && window.Disputa.bloqueado && window.Disputa.bloqueado();
  if (bloqueado) agendarReativacaoConferir();
  const verbo = ocupado ? 'conferindo' : 'conferir';
  const titulo = bloqueado ? ` title="${esc(window.Disputa.mensagemBloqueio())}"` : '';
  return `<button type="button" class="acao-linha" data-conferir="${nome}"`
       + ` aria-label="${verbo} ${nome} agora no Registro.br"${titulo}`
       + `${ocupado || bloqueado ? ' disabled' : ''}>${ocupado ? 'conferindo…' : 'conferir'}`
       + '</button>';
}

function botaoAcompanhar(it) {
  const nome = esc(it[D]);
  const sim = estado.acompanhados.has(it[D]);
  return `<button type="button" class="estrela" data-acompanhar="${nome}"`
       + ` aria-pressed="${sim}" aria-label="acompanhar ${nome}"`
       + ` title="${sim ? 'Acompanhando: toque para parar' : 'Acompanhar: avisa se mudar'}">`
       + `<span aria-hidden="true">${sim ? '★' : '☆'}</span></button>`;
}

/*
 * Lembrete do fim do leilao, direto no calendario de quem usa.
 *
 * A dor que isto resolve esta na regra: o Registro.br NAO avisa quando
 * alguem cobre o seu lance, e recomenda acompanhar os 10 minutos finais.
 *
 * O dialogo e as tres opcoes (Google, Outlook, Calendario da Apple) moram em
 * agenda.js, compartilhado com a ficha "quando esse dominio volta?" e com a
 * proxima rodada. Aqui fica so o evento.
 *
 * O QUE O EVENTO MARCA E UM PISO, NAO UMA PREVISAO. A regra garante
 * "pelo menos 24 h" de ofertas depois que os tickets fecham; nao existe
 * fonte publica da hora em que um leilao fecha (o `date=` do RDAP e o
 * `ends-at` do avail sao o fim da RODADA, conferidos em 17/09/2026).
 * Medido no mesmo dia: um leilao foi ate 16h com piso as 15h, e outro
 * seguia aberto com o piso vencido havia horas. Entao o
 * evento diz "nao termina antes de", e passado o piso o botao some, porque
 * nao ha data honesta para oferecer.
 */
const MINUTOS_ANTES = 30;
const PAINEL_LEILAO = 'https://registro.br/painel/dominios/processo-competitivo/';

function horaDeBrasilia(d) {
  return d.toLocaleString('pt-BR', {
    weekday: 'long', day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit', timeZone: 'America/Sao_Paulo',
  });
}

function abrirLembrete(dominio) {
  const fim = fimDoLeilao();
  if (!fim || !window.Agenda) return;
  window.Agenda.abrir({
    cabecalho: `Lembrar do fim do leilão de ${dominio}`,
    texto: `Não termina antes de ${horaDeBrasilia(fim)}, horário de Brasília. `
         + 'O evento ocupa a última meia hora antes disso.',
    nota: 'O Registro.br não avisa quando cobrem o seu lance, e não publica '
        + 'a hora do fim. Lance nos 10 minutos finais prorroga o prazo em mais 10.',
    titulo: `Leilão de ${dominio}: pode fechar a partir daqui`,
    detalhes: `Não termina antes de ${horaDeBrasilia(fim)} (horário de Brasília); `
            + 'pode ir muito além, e o Registro.br não publica a hora do fim. '
            + 'Lance nos 10 minutos finais prorroga por mais 10. '
            + 'O Registro.br não avisa quando cobrem o seu lance. '
            + 'Oferta é vinculante: pagar em até 15 dias.',
    inicio: new Date(fim.getTime() - MINUTOS_ANTES * 60 * 1000),
    fim,
    url: PAINEL_LEILAO,
    uid: `leilao-${dominio}`,
    // o .ics servido so existe para quem o exportador viu em leilao
    servido: estado.lembretesServidos.has(dominio)
      ? `lembretes/${encodeURIComponent(dominio)}.ics` : null,
    arquivo: `leilao-${dominio}.ics`,
  });
}

/** O piso: a primeira hora em que o leilao PODE fechar. Nunca o fim. */
function fimDoLeilao() {
  const fim = estado.dados.rodada && estado.dados.rodada.fim;
  if (!fim) return null;
  const quando = new Date(new Date(fim).getTime() + 24 * 3600 * 1000);
  return isNaN(quando) || quando < agoraDaPagina() ? null : quando;
}

/*
 * So o leilao ganha lembrete na linha: e o unico em que perder a hora custa
 * o nome, e o Registro.br nao avisa quando cobrem o lance. O lembrete da
 * proxima rodada mora uma vez so, no botao do topo da pagina (fase.js), e o
 * de um nome travado, na ficha completa.
 */
function botaoLembrar(it) {
  const nome = esc(it[D]);
  if (it[SS] !== iLeilao || !fimDoLeilao()) return '';
  return `<button type="button" class="acao-linha" data-lembrar="${nome}"`
       + ` aria-label="lembrar do fim do leilão de ${nome}">lembrar do fim</button>`;
}

/**
 * O botao "i" ao lado do nome abre o resumo da ficha num dialogo (ficha.js):
 * so o que e daquele nome (dono, datas, quando pode voltar, se ja teve
 * site...), sem sair da lista; o "Mais detalhes" de la leva a pagina
 * completa. O nome continua levando direto a busca do Registro.br.
 */
const ICONE_FICHA = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">'
  + '<circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.3"/>'
  + '<path d="M8 7.25v3.75" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>'
  + '<circle cx="8" cy="4.9" r="0.95" fill="currentColor"/></svg>';

function botaoFicha(it) {
  if (!(window.Ficha && window.Ficha.abrir)) return '';
  const nome = esc(it[D]);
  return `<button type="button" class="botao-ficha" data-ficha="${nome}" aria-haspopup="dialog"`
       + ` aria-label="Resumo de ${nome}" title="Dono, datas e histórico">${ICONE_FICHA}</button>`;
}

/**
 * A etiqueta "elegivel" ao lado do nome, e o que ela diz em cada fase.
 *
 * Aberta, e so fora do leilao: leilao so abre para elegivel, entao ao lado
 * de "leilao aberto" a etiqueta nao diz nada. Num "sem competicao" ela diz
 * o que nada mais na linha diz: o nome ja travou antes, ja provou demanda.
 *
 * Fechada: "elegivel" num nome ja "registrado" seria lido como "ainda em
 * leilao, da para entrar" por quem abre o site pela primeira vez (a lista
 * "Foram a leilao" e a porta de entrada comum). Fechada a
 * rodada, a etiqueta conta o desfecho, no passado e sem a cor de convite:
 * "leilao encerrado" no registrado, e nada nos outros -- o selo de situacao
 * ("volta na proxima rodada", "fechou sem candidato") ja diz o que houve.
 */
/** [referentes, muito citados] da linha; [0, 0] sem indice. */
function linksDe(it) {
  const lk = it[LK];
  return Array.isArray(lk) ? lk : [0, 0];
}

/**
 * A etiqueta "N links": quantos outros sites apontam para o nome, pelo grafo
 * de dominios do CommonCrawl. E botao: toca e abre a lista de todos
 * (lista/links.js); quem ve uma contagem quer tocar nela. Um referente so
 * nao ganha etiqueta: costuma ser site que republica a lista de vencidos.
 * Link de site muito citado muda a forma, nao so a cor, e o leitor de tela
 * ouve o que ela quer dizer.
 */
function etiquetaLinks(it) {
  const [referentes, citados] = linksDe(it);
  if (!citados && referentes < 2) return '';
  const quantos = `${num(referentes)} ${referentes === 1 ? 'link' : 'links'}`;
  let deles = '';
  if (citados && referentes === 1) deles = ', e ele é muito citado';
  else if (citados) deles = `, ${num(citados)} ${citados === 1 ? 'deles muito citado' : 'deles muito citados'}`;
  const explica = `${num(referentes)} ${referentes === 1 ? 'site aponta' : 'sites apontam'} para este nome`
    + deles + ' (grafo de links do CommonCrawl). Toque para ver quais';
  return `<button type="button" class="pilula-links${citados ? ' citado' : ''}" data-links="${esc(it[D])}"`
    + ` aria-haspopup="dialog" title="${esc(explica)}">`
    + `${quantos}<span class="sr-apenas">: ${esc(explica)}</span></button>`;
}

function etiquetaElegivel(it) {
  if (!it[E] || it[SS] === iLeilao || rodadaFechada()) return '';
  return '<span class="pilula-elegivel">elegível<span class="sr-apenas"> ao processo competitivo</span></span>';
}

/**
 * A linha nao lista os motivos da nota: "nome curto, palavra em portugues,
 * palavra popular" em todo nome e obvio para quem le e polui a lista. O que cada criterio quer dizer mora em
 * /como-selecionamos/. Fica so o motivo que muda a decisao e nao esta a
 * vista no nome: extensao restrita (.adv.br so para advogado, por exemplo),
 * como aviso ao lado do nome, igual ao de marca.
 */
/**
 * O sinal ruim que o Cloudflare Intel deu ao nome em algum momento (apostas,
 * adulto, golpe, estacionado): aviso ao lado do nome, como o de marca,
 * porque muda a decisao e nao esta a vista no nome. O detalhe, com as
 * datas, fica na ficha.
 */
function avisoDeSinal(it) {
  const sinal = it[SN];
  if (!sinal || typeof sinal !== 'string') return '';
  const explica = `O Cloudflare Intel classificou este endereço como ${sinal.toLowerCase()} em algum `
    + 'momento desde dez/2020 (pode ter sido outro dono). As datas estão em “quando volta”.';
  return `<span class="aviso-marca aviso-sinal" title="${esc(explica)}">classificado como `
    + `${esc(sinal.toLowerCase())}<span class="sr-apenas">. ${esc(explica)}</span></span>`;
}

function avisoDeRestricao(it) {
  for (const i of it[M]) {
    const m = estado.dados.motivos[i] || '';
    if (m.startsWith('extensão restrita')) return `<span class="aviso-restrita">${esc(m)}</span>`;
  }
  return '';
}

function linha(it) {
  const dominio = esc(it[D]);
  const elegivel = etiquetaElegivel(it) + etiquetaLinks(it);
  const risco = it[MK] === 2
    ? '<span class="aviso-marca">possível marca de terceiro</span>' : '';
  const url = `https://registro.br/busca-dominio?fqdn=${encodeURIComponent(it[D])}`;
  // o .com do mesmo nome, conferido no navegador so quando tocado (web/pontocom.js)
  const com = window.PontoCom ? window.PontoCom.botao(it[D]) : '';

  return `<tr role="row">
    <td class="col-marca" role="cell">${botaoAcompanhar(it)}</td>
    <td class="dominio" role="cell">
      <a href="${url}" target="_blank" rel="noopener noreferrer" aria-describedby="nota-nome-registro">${dominio}</a>${botaoFicha(it)}${elegivel} ${com}
      ${risco}${avisoDeSinal(it)}${avisoDeRestricao(it)}
    </td>
    <td class="col-situacao" role="cell"><div class="situacao-linha">${selo(it)}<span class="leitura">${carimboDaLinha(it)}${marcadorDaMudanca(it)}${botaoConferir(it)}${botaoLembrar(it)}</span></div></td>
    <td class="num col-competindo" role="cell">${competindo(it)}</td>
    <td class="num col-nota" role="cell">${esc(it[N])}</td>
  </tr>`;
}

export {
  SELOS,
  SELOS_FECHADA,
  selo,
  competindo,
  idadeDaContagem,
  chegadaTexto,
  carimboDaLinha,
  marcadorDaMudanca,
  botaoConferir,
  botaoAcompanhar,
  MINUTOS_ANTES,
  PAINEL_LEILAO,
  horaDeBrasilia,
  abrirLembrete,
  fimDoLeilao,
  botaoLembrar,
  botaoFicha,
  linksDe,
  etiquetaLinks,
  etiquetaElegivel,
  avisoDeRestricao,
  linha,
};
