// A fase da rodada, o calendario e os lembretes da rodada.
import { SS, estado, iLeilao } from './estado.js';
import { horaDeBrasilia } from './linha.js';
import { FUSO, agoraDaPagina, agoraMs, horaCurta, num } from './util.js';

/** A rodada do instantaneo ja fechou (assentando ou entre rodadas)? */
function rodadaFechada() {
  const fim = estado.dados && estado.dados.rodada && estado.dados.rodada.fim;
  const quando = fim ? new Date(fim) : null;
  return Boolean(quando && !isNaN(quando) && quando <= agoraDaPagina());
}

/** A lista da rodada ja saiu e as candidaturas ainda nao abriram? */
function antesDaAbertura() {
  const inicio = estado.dados && estado.dados.rodada && estado.dados.rodada.inicio;
  const quando = inicio ? new Date(inicio) : null;
  return Boolean(quando && !isNaN(quando) && agoraDaPagina() < quando);
}

/*
 * Nada conferido ainda na rodada desta lista: na virada o varrer.py esquece
 * as leituras da rodada anterior e, antes da abertura, nao consulta (o
 * Registro.br so diz algo util depois dela). Ate a primeira leitura chegar,
 * as contagens que dependem dela seriam "0" como se fosse fato.
 */
function aConferir() {
  if (!estado.dados || rodadaFechada()) return false;
  return antesDaAbertura() || !estado.dados.itens.length;
}

/**
 * Em que ponto do ciclo mensal estamos.
 *
 * O mesmo numero significa coisas diferentes conforme a fase: "0 competindo"
 * com a rodada aberta e uma oportunidade; com a rodada fechada e historia.
 *
 * Devolve a fracao decorrida da janela (para a regua), um texto completo
 * (para leitor de tela) e, so quando a informacao vira noticia, um texto
 * curto para aparecer na tela. Fora dessas horas a regua fala sozinha.
 */
const HORAS_DE_AVISO = 48;      // a partir daqui o prazo vira noticia
const DIA = 24;

function faseDaRodada(inicio, fim, agora = agoraDaPagina()) {
  if (!fim) return null;
  const abertura = inicio ? new Date(inicio) : null;
  const fechamento = new Date(fim);
  if (isNaN(fechamento)) return null;

  const horas = (fechamento - agora) / 3600000;
  const janela = abertura && !isNaN(abertura)
    ? (fechamento - abertura) / 3600000 : 7 * DIA;
  const decorrido = Math.min(1, Math.max(0, 1 - horas / janela));

  // sempre em horario de Brasilia: e o do Registro.br e o do resto da pagina
  const diaDe = (d) => d.toLocaleDateString('pt-BR',
    { day: '2-digit', month: '2-digit', timeZone: FUSO });
  const dia = diaDe(fechamento);
  const hora = fechamento.toLocaleTimeString('pt-BR',
    { hour: '2-digit', minute: '2-digit', timeZone: FUSO });

  // quanto falta, por extenso: "3 dias", "5 h", "menos de uma hora"
  const falta = horas > HORAS_DE_AVISO ? `${Math.round(horas / DIA)} dias`
    : horas < 1 ? 'menos de uma hora' : `${Math.round(horas)} h`;
  const quanto = { dia, hora, falta };

  // A lista nova saiu (dois dias antes) e a rodada ainda nao abriu: as datas
  // ja sao as dela, mas ninguem consegue se candidatar
  if (abertura && !isNaN(abertura) && agora < abertura) {
    const mes = MESES_EXTENSO[Number(abertura.toLocaleDateString('en-CA',
      { timeZone: FUSO }).slice(5, 7)) - 1];
    const abre = diaDe(abertura);
    return {
      ...quanto, fase: 'lista', decorrido: 0, mes,
      abre, horaAbre: horaCurta(abertura),
      curto: `abre em ${abre}`,
      completo: `A lista da rodada de ${mes} saiu: as candidaturas abrem em ${abre}, `
              + `às ${horaCurta(abertura)}, e vão até ${dia}, às ${horaCurta(fechamento)} `
              + '(horário de Brasília).',
    };
  }
  if (horas > HORAS_DE_AVISO) {
    return {
      ...quanto, fase: 'aberta', decorrido,
      completo: `Rodada aberta, fecha em ${falta}, `
              + `dia ${dia} às ${hora} (horário de Brasília). Candidaturas ainda valem.`,
    };
  }
  if (horas > 0) {
    return {
      ...quanto, fase: 'fim', decorrido,
      curto: `fecha em ${falta}`,
      completo: `Rodada aberta, mas fecha em ${falta}, dia ${dia} às ${hora} (horário de Brasília).`,
    };
  }
  // a atribuicao nao e instantanea: leva algumas horas para assentar
  if (horas > -HORAS_DE_AVISO) {
    return {
      ...quanto, fase: 'assentando', decorrido: 1,
      curto: 'rodada encerrada',
      completo: 'Rodada encerrada. Os resultados levam algumas horas para '
              + 'assentar; quem ficou sem concorrente fica livre para registrar.',
    };
  }
  return {
    ...quanto, fase: 'entre', decorrido: 1,
    curto: 'entre rodadas',
    completo: 'Entre rodadas. Não dá para se candidatar agora, mas o que ficou '
            + 'livre pode ser registrado na hora, e os leilões seguem.',
  };
}

function pintarFase(rodada) {
  const regua = document.querySelector('#regua');
  const prazo = document.querySelector('#prazo');
  const info = faseDaRodada(rodada && rodada.inicio, rodada && rodada.fim);

  if (!info) {
    if (regua) regua.classList.add('escondido');
    return;
  }
  if (regua) {
    regua.style.setProperty('--decorrido', (info.decorrido * 100).toFixed(1) + '%');
    regua.dataset.fase = info.fase;
    regua.setAttribute('aria-label', info.completo);
  }
  if (prazo) {
    prazo.textContent = info.curto || '';
    prazo.classList.toggle('escondido', !info.curto);
    prazo.dataset.fase = info.fase;
  }
  pintarFaseInicio(info);
}

/**
 * A linha da apresentacao que diz se da para se candidatar agora. O build
 * grava as datas (e a proxima rodada, pela regra); aqui ela vira frase de
 * quem vai agir, e muda com o relogio de quem olha.
 */
let proximaRodada = null;   // lida do HTML uma vez: a frase apaga o span

function pintarFaseInicio(info) {
  const el = document.querySelector('#fase-inicio');
  if (!el) return;
  if (proximaRodada === null) {
    proximaRodada = (document.querySelector('#p-proxima-rodada') || {}).textContent || '';
  }
  const proxima = proximaRodada;
  const { falta, dia, hora } = info;
  // Entre rodadas a frase conta os dias ate a lista sair (2 dias antes da
  // abertura), que e quando da para escolher o nome de novo
  const abre = proximaAberturaDoCalendario();
  const lista = abre ? listaDaRodada(abre) : null;
  const curta = (d) => d.toLocaleDateString('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit' });
  // Quantos leiloes da rodada ainda estao abertos, pela lista oficial. Nao
  // da para calcular pelo relogio: leilao nao acaba junto com a rodada (L5).
  const aindaEmLeilao = estado.dados.itens
    .filter((it) => it[SS] === iLeilao).length;
  // a contagem regressiva (#relogio) diz quanto falta; a frase
  // fica com o que aconteceu e as datas
  const frases = {
    lista: info.completo,
    aberta: `Rodada aberta: candidaturas até ${dia} às ${hora}, horário de Brasília (faltam ${falta}).`,
    fim: `Últimas horas: a rodada fecha em ${falta}, dia ${dia} às ${hora} (horário de Brasília).`,
    assentando: aindaEmLeilao
      ? `A rodada fechou em ${dia}. Os livres e os que travaram já aparecem abaixo, e ${aindaEmLeilao === 1 ? 'um leilão continua aberto' : `${num(aindaEmLeilao)} leilões continuam abertos`}.`
      : `A rodada fechou em ${dia}. Os livres e os que travaram já aparecem abaixo.`,
    entre: abre && lista > agoraMs()
      ? `Entre rodadas: a lista da próxima sai em ${curta(lista)} e a rodada abre em ${curta(abre)}, às 15h.`
      : abre
        ? `A lista da próxima rodada já deve ter saído: a rodada abre em ${curta(abre)}, às 15h.`
        : /\d/.test(proxima)
          ? `Entre rodadas: a próxima começa em ${proxima.trim()}, pela regra da segunda quarta-feira.`
          : 'Entre rodadas: a próxima começa na segunda quarta-feira do mês.',
  };
  el.textContent = frases[info.fase] || el.textContent;
  el.dataset.fase = info.fase;
  // So a primeira frase de "entre" repete as datas da contagem (#relogio):
  // o extra.css esconde a pilula por esta marca. "Ja deve ter saido" e
  // noticia e fica a vista.
  el.toggleAttribute('data-repete-relogio', info.fase === 'entre' && Boolean(abre) && lista > agoraMs());
  pintarBotaoDaRodada(info, abre);
  // a frase de apresentacao fala da rodada aberta; fechada, fala do que sobrou
  // (a mesma frase sai do build em paginas.subtitulo_da_pagina: mude as duas)
  const subtitulo = document.querySelector('.apresentacao .subtitulo');
  if (subtitulo && (info.fase === 'assentando' || info.fase === 'entre')) {
    const total = document.createElement('strong');
    total.textContent = num(estado.dados.total_rodada || 0);
    subtitulo.replaceChildren('Todo mês o Registro.br devolve ao mercado os domínios que não foram '
      + 'renovados. Na última rodada foram ', total, '; agora você vê os que ficaram livres para '
      + 'registrar na hora e os que travaram e voltam na próxima.');
  }
}

/*
 * O calendario que o build grava na pagina (p-calendario, de
 * garimpo/dominio/calendario.py). A regra das datas mora la; aqui so se
 * escolhe a proxima abertura.
 */
let calendario = null;
function lerCalendario() {
  if (calendario === null) {
    try {
      calendario = JSON.parse((document.querySelector('#p-calendario') || {}).textContent || '{}');
    } catch (e) {
      calendario = {};
    }
  }
  return calendario;
}

function proximaAberturaDoCalendario() {
  const cal = lerCalendario();
  const hora = String(cal.hora_abertura || 15).padStart(2, '0');
  for (const iso of cal.aberturas || []) {
    const abre = new Date(`${iso}T${hora}:00:00-03:00`);
    if (abre > agoraDaPagina()) return abre;
  }
  return null;
}

function listaDaRodada(abre) {
  return new Date(abre.getTime() - (lerCalendario().dias_lista_antes || 2) * 86400000);
}

const MESES_EXTENSO = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho',
  'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];

/** O evento da proxima rodada: o dia em que a lista sai, as 9h. */
function eventoDaRodada(abre, dominio) {
  const lista = listaDaRodada(abre);
  const iso = abre.toLocaleDateString('en-CA', { timeZone: 'America/Sao_Paulo' });
  const diaLista = lista.toLocaleDateString('en-CA', { timeZone: 'America/Sao_Paulo' });
  const inicio = new Date(`${diaLista}T09:00:00-03:00`);
  const mes = MESES_EXTENSO[Number(iso.slice(5, 7)) - 1];
  const quando = (d) => d.toLocaleDateString('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit' });
  return {
    cabecalho: dominio ? `Lembrar da próxima rodada para ${dominio}` : 'Lembrar da próxima rodada',
    texto: `A lista da rodada de ${mes} sai em ${quando(lista)} e a rodada abre em ${quando(abre)}, `
         + 'às 15h. O lembrete fica no dia da lista, às 9h.',
    nota: 'Datas pela regra da segunda quarta-feira; o Registro.br pode mudar por feriado.'
        + (dominio ? ' No iPhone, o arquivo traz a rodada; o nome vai no Google e no Outlook.' : ''),
    titulo: dominio ? `Nova chance de pedir ${dominio}` : `Sai a lista da rodada de liberação de ${mes}`,
    detalhes: (dominio ? `${dominio} travou e volta na rodada de ${mes}. ` : '')
            + `A lista sai hoje; a rodada abre em ${quando(abre)} às 15h e candidatar-se é de graça.`,
    inicio,
    fim: new Date(inicio.getTime() + 30 * 60000),
    url: `${location.origin}/`,
    uid: `rodada-${iso}${dominio ? '-' + dominio : ''}`,
    servido: `/lembretes/rodadas/${iso}.ics`,
    arquivo: `rodada-${iso}.ics`,
  };
}

/**
 * A abertura da rodada da lista que ja saiu. A data vem do instantaneo (o
 * cabecalho da lista oficial), nao da regra: feriado muda a regra.
 */
function eventoDaAbertura(abre) {
  const iso = abre.toLocaleDateString('en-CA', { timeZone: FUSO });
  const mes = MESES_EXTENSO[Number(iso.slice(5, 7)) - 1];
  const hora = horaCurta(abre);
  return {
    cabecalho: 'Lembrar da abertura da rodada',
    texto: `A rodada de ${mes} abre em ${horaDeBrasilia(abre)}, horário de Brasília. `
         + 'O evento ocupa a primeira meia hora.',
    nota: 'Candidatura não tem botão de cancelar: escolha antes.',
    titulo: `Rodada de liberação de ${mes} abre hoje às ${hora}`,
    detalhes: `A rodada de ${mes} abre hoje às ${hora} (horário de Brasília) e fica aberta por `
            + 'uma semana. Candidatar-se é de graça; se só você pedir um nome, ele é seu pela anuidade.',
    inicio: abre,
    fim: new Date(abre.getTime() + 30 * 60000),
    url: `${location.origin}/`,
    uid: `abertura-rodada-${iso}`,
    arquivo: 'abertura-da-rodada.ics',
  };
}

function pintarBotaoDaRodada(info, abre) {
  const botao = document.querySelector('#lembrar-rodada');
  if (!botao) return;
  const aberta = info.fase === 'aberta' || info.fase === 'fim';
  const rodada = estado.dados.rodada || {};
  const fim = new Date(rodada.fim);
  // lista saiu, rodada ainda fechada: o lembrete e o da abertura dela
  if (info.fase === 'lista') {
    const inicio = new Date(rodada.inicio);
    botao.classList.toggle('escondido', !window.Agenda || !(inicio > agoraDaPagina()));
    botao.textContent = 'Lembrar da abertura da rodada';
    botao.onclick = () => { if (window.Agenda) window.Agenda.abrir(eventoDaAbertura(inicio)); };
    return;
  }
  botao.classList.toggle('escondido', !window.Agenda || (aberta ? !(fim > agoraDaPagina()) : !abre));
  botao.textContent = aberta ? 'Lembrar do fim da rodada' : 'Me avise da próxima rodada';
  botao.onclick = () => {
    if (!window.Agenda) return;
    if (!aberta) {
      window.Agenda.abrir(eventoDaRodada(abre));
      return;
    }
    window.Agenda.abrir({
      cabecalho: 'Lembrar do fim da rodada',
      texto: `A rodada fecha em ${horaDeBrasilia(fim)}, horário de Brasília. `
           + 'O evento ocupa a última meia hora.',
      nota: 'Candidatura não tem botão de cancelar: escolha antes.',
      titulo: 'Rodada de liberação fecha hoje às 15h',
      detalhes: 'Último dia para se candidatar aos domínios .br da rodada. '
              + 'Se só você pedir um nome, ele é seu pela anuidade.',
      inicio: new Date(fim.getTime() - 30 * 60000),
      fim,
      url: `${location.origin}/`,
      uid: `fim-rodada-${fim.toISOString().slice(0, 10)}`,
      arquivo: 'fim-da-rodada.ics',
    });
  };
}

export {
  rodadaFechada,
  antesDaAbertura,
  aConferir,
  HORAS_DE_AVISO,
  DIA,
  faseDaRodada,
  pintarFase,
  proximaRodada,
  pintarFaseInicio,
  calendario,
  lerCalendario,
  proximaAberturaDoCalendario,
  listaDaRodada,
  MESES_EXTENSO,
  eventoDaRodada,
  eventoDaAbertura,
  pintarBotaoDaRodada,
};
