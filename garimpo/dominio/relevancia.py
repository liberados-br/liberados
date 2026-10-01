"""
Pontuacao de relevancia de um dominio.

Camada de dominio: funcao pura. Recebe o nome e os vocabularios, devolve uma
nota de 0 a 100 e os motivos que a compuseram.

A nota NAO e estimativa de preco. Ela existe para ordenar a atencao: com 15
mil nomes na fila e 3 candidaturas por rodada, o que importa e olhar primeiro
o que tem chance de valer algo.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property

from . import extensoes

# Pesos. Estao juntos e nomeados de proposito: e a regra de negocio mais
# ajustada do projeto, e o valor solto no meio do codigo escondia a intencao.
PESO_SIGLA = 30           # 3 letras: escasso, mas costuma colidir com marca
PESO_CURTO = 35           # 4 a 6 letras: o ponto doce
PESO_MEDIO = 20           # 7 a 9
PESO_LONGO = 8            # 10 a 14
PESO_MUITO_LONGO = 2

PESO_PALAVRA_PT = 30      # o brasileiro busca em portugues
PESO_PALAVRA_EN = 18
PESO_NICHO = 15           # composto comercial reconhecivel

PESO_ELEGIVEL = 25        # ja provou demanda ao travar rodadas seguidas

# Extensao. Medido em 30/09/2026 nos conferidos de setembro, fora dos
# elegiveis, com a mesma nota de nome (a nota sem a parcela da extensao, 70 a
# 100): disputados .com.br 58% (42 de 72), .ia.br e .app.br 11% (5 de 44),
# .net.br e .dev.br 2,6% (1 de 39), as outras genericas e as de cidade 1,4%
# (1 de 69), as restritas 0% (0 de 90). Com +10 para .com.br e +6 para as
# "boas", a lista de livres abria com cabelo.etc.br e vulcao.art.br.
PESO_COM_BR = 10          # fica: subir incharia o corte de 45 de .com.br
PESO_EXTENSAO = {"ia.br": -8, "app.br": -8, "net.br": -18, "dev.br": -18}
PESO_EXTENSAO_GENERICA = -28   # tec, art, eco, log... e as de cidade
PESO_EXTENSAO_RESTRITA = -45   # so CPF da profissao, so CNPJ do ramo

# Medidos nos 125 mil nomes da rodada de setembro de 2026. Cada peso existe
# porque, sem ele, a nota deixava de fora nomes que uma curadoria manual
# achava: "aprenda" e "confiar" (verbos), "petfriendly" e "lojaonline"
# (compostos de nicho), os de tres letras. Com eles, quase todos os nomes
# dessa curadoria (todos menos tres) passam do corte de 45.
PESO_FLEXAO = 22          # forma verbal ou flexao: aprenda, estude, confiar
PESO_POPULAR = 6          # entre as 20 mil palavras mais usadas do portugues
PESO_COMPOSTO_NICHO = 14  # nicho + palavra comum: imoveisnovos, lojaonline
PESO_CIDADE_NICHO = 12    # nicho + cidade de 200 mil habitantes: imoveisembelem
# LLL .com.br e escasso e foi a classe mais disputada de setembro. Com 5,
# somava exatamente o corte e ggg.com.br (pena de repeticao) ficava em 35,
# fora do pool. 15 e o minimo que poe os 124 da rodada de setembro no corte
# ou acima. Contra os disputados de setembro, precisao@100 sobe de 24 para
# 27 e @1000 fica igual (78).
# 25, medido em 30/09/2026: 18 dos 124 conferidos foram disputados (14,5%),
# a taxa das palavras populares de 7 a 9 letras, mas a nota mediana da classe
# era 55, abaixo das palavras obscuras de 4 a 6 letras (75, 2% disputadas).
PESO_TRES_COM_BR = 25

# Palavra que o dicionario tem e quase ninguem escreve (adau, japu, gete,
# cabrunco): entre os .com.br de 4 a 6 letras conferidos, 2,0% disputados
# (4 de 200) fora das 100 mil mais usadas do wordfreq, contra 37% (41 de 110)
# entre as 20 mil. O peso de vocabulario fica com um teto pela posicao.
RANKING_RARA = 100_000
PESO_PALAVRA_RARA = 18    # teto: fora das 100 mil, vale pouco mais que nicho
RANKING_MEDIA = 20_000
PESO_PALAVRA_MEDIA = 26   # teto: de 20 mil a 100 mil
# Palavra de verdade com 7 letras ou mais: as populares de 7 a 9 letras foram
# disputadas em 14,8% (17 de 115), acima das de 4 a 6 letras entre 20 mil e
# 100 mil (10,9%); o peso de tamanho sozinho as punha abaixo.
PESO_PALAVRA_LONGA = 8          # 7 a 9 letras
PESO_PALAVRA_MUITO_LONGA = 12   # 10 ou mais

# Demanda medida no cadastro aberto de CNPJ (26,8 milhoes de empresas ativas,
# agosto de 2026). Das 86 palavras da rodada de setembro usadas por 100+
# empresas no nome fantasia, umas 25 tambem eram escolhas da curadoria
# manual.
# Links que a web ainda faz para o nome (grafo de dominios do CommonCrawl,
# garimpo/dominio/links.py). Medido em 30/09/2026 contra os disputados de
# setembro, so conferidos fora dos elegiveis: na faixa de nota 40-49, nome
# com referente muito citado e 5+ referentes foi disputado em 6,7% (5 de
# 75), contra 3,7% dos nomes SEM link na faixa 70-79; com 2+ referentes,
# 3,0% (3 de 99), o nivel dos nomes sem link na faixa 60-69. Os pesos sao
# metade da distancia medida, porque as amostras sao pequenas. Um referente
# so nao conta: costuma ser site que republica a lista de vencidos.
PESO_LINKS_CITADO = 20        # referente muito citado e 5+ referentes
PESO_LINKS = 10               # 2+ referentes, ou algum muito citado
MINIMO_REFERENTES = 2
MINIMO_REFERENTES_CITADO = 5
# Links fortes: entram na conferencia com qualquer nota, como os elegiveis.
# Medido em 30/09/2026: 490 nomes da lista de setembro com links fortes
# ficavam abaixo do corte de 45 mesmo com o +20 (economyshopping.com.br, 626
# referentes e 27 muito citados, nota 32), e a lista mostrava "nao
# verificado" justo nos nomes com mais passado.
MINIMO_REFERENTES_FORTE = 20
# Um referente so, no .com.br: entre os .com.br conferidos em setembro que nao
# sao palavra, alguem quis o nome (registrou, travou ou pediu) em 20,7% dos
# que tinham um referente, contra 5,7% dos sem link (30/09/2026). Pouco,
# porque o referente unico costuma ser site que republica a lista de
# vencidos; fora do .com.br o link nao separa. Um reforco maior
# para o link muito citado no .com.br tambem foi medido e recusado: subia os
# travados no topo 3000 (418 para 464), mas enchia o topo da lista de siglas
# sem sentido (15 palavras de verdade entre as 40 primeiras livres, contra 36).
PESO_UM_REFERENTE_COM_BR = 4


# Visita forte: esteve entre os 100 mil sites mais abertos do Brasil no
# Chrome (CrUX) em algum mes guardado. Entra na conferencia com qualquer
# nota, como o link forte, mas NAO pesa na nota: medido em 30/09/2026, +20
# para esses 452 nomes subia a precisao@100 de 35 para 39 e derrubava a
# @3000 de 125 para 118 (docs/criterios-de-valor.md).
FAIXA_VISITAS_FORTES = 100_000


def visitas_fortes(dominio: str, lexico: "Lexico | None") -> bool:
    return bool(lexico) and lexico.trafego.get(dominio, 10 ** 9) <= FAIXA_VISITAS_FORTES


def links_fortes(dominio: str, lexico: "Lexico | None") -> bool:
    """5+ referentes com algum muito citado, ou 20+ referentes (sem plataforma)."""
    if not lexico:
        return False
    proprios, citados = lexico.links.get(dominio, (0, 0))
    return bool((citados and proprios >= MINIMO_REFERENTES_CITADO)
                or proprios >= MINIMO_REFERENTES_FORTE)

PESO_PALAVRA_DE_NEGOCIO = 8   # o nome e palavra comum em nome de empresa
PESO_NOME_DE_EMPRESAS = 8     # o nome e, colado, o nome fantasia de varias
MINIMO_EMPRESAS_PALAVRA = 100
MINIMO_EMPRESAS_NOME = 3      # 1 empresa e marca; 2 pode ser coincidencia

RANKING_POPULAR = 20_000

PENA_COMPRIDO = -10       # acima de 16 letras
PENA_REPETICAO = -10      # tres letras iguais seguidas
PENA_DIGITO = -12         # numero atrapalha o teste do radio
PENA_HIFEN = -15          # hifen atrapalha mais ainda


# Radicais de nicho comercial. Nome composto nao passa no teste do radio, mas
# "creditoimobiliario" tem comprador obvio e trafego de busca.
NICHOS = (
    "imovel", "imoveis", "credito", "seguro", "advogad", "advocacia",
    "medic", "saude", "odonto", "dental", "pet", "vet", "auto", "carro",
    "moto", "casa", "reforma", "obra", "solar", "energia", "curso",
    "concurso", "emprego", "vaga", "loja", "oferta", "cupom", "guia",
    "agencia", "consult", "contab", "fintech", "cripto", "banco",
    "franquia", "aluguel", "viagem", "turismo", "hotel", "restaurante",
    "delivery", "nutri", "fitness", "academia", "estetica", "beleza",
    "cuidar", "idoso", "escola", "faculdade", "juridic", "prompt",
    "ia", "ai", "dev", "cloud", "app", "data",
)

_REPETICAO = re.compile(r"(.)\1{2,}")

_RAIZES = frozenset(n for n in NICHOS if len(n) >= 3)
# palavra de ligacao dentro de composto: medica-EM-casa, loja-DA-moda
CONECTIVOS = ("para", "pra", "em", "de", "da", "do", "na", "no", "ja", "e")


@dataclass(frozen=True)
class Lexico:
    """
    O que a nota sabe alem das listas de portugues e ingles.

    Tudo opcional: com o Lexico vazio a nota e exatamente a antiga, o que
    mantem os testes antigos validos e deixa a nota degradar, em vez de
    quebrar, quando uma fonte nao baixa.
    """

    flexoes: frozenset[str] = frozenset()          # aprenda, estude, confiar
    popularidade: Mapping[str, int] = field(default_factory=dict)  # 0 = mais usada
    comuns: frozenset[str] = frozenset()           # palavras de uso comum, pt e en
    pessoas: frozenset[str] = frozenset()          # nomes e sobrenomes comuns
    cidades: tuple[str, ...] = ()                  # 200 mil habitantes ou mais
    # do cadastro de CNPJ: palavra -> empresas que a usam no nome fantasia,
    # e rotulo colado -> empresas com exatamente esse nome
    negocios: Mapping[str, int] = field(default_factory=dict)
    nomes_de_empresa: Mapping[str, int] = field(default_factory=dict)
    # do indice de links: dominio -> (referentes sem plataforma, muito citados)
    links: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    # do indice de trafego (CrUX Brasil): dominio -> melhor faixa (1000 ... 1000000)
    trafego: Mapping[str, int] = field(default_factory=dict)

    @cached_property
    def padrao_cidades(self) -> re.Pattern | None:
        if not self.cidades:
            return None
        # a mais comprida primeiro, para "saojosedoscampos" ganhar de "campos"
        nomes = sorted(set(self.cidades), key=len, reverse=True)
        return re.compile("|".join(map(re.escape, nomes)))


LEXICO_VAZIO = Lexico()


def e_nicho(parte: str) -> bool:
    """Parte de nome que e um radical de nicho, com ate 2 letras de sufixo."""
    if not parte.isalpha():
        return False
    return any(parte[:k] in _RAIZES
               for k in range(max(3, len(parte) - 2), len(parte) + 1))


def composto_de_nicho(rotulo: str, lexico: Lexico) -> tuple[str, ...] | None:
    """
    Nicho + palavra comum, com conectivo opcional: loja+online, medica+em+casa.

    Regra apertada de proposito. As versoes mais soltas, medidas na rodada de
    setembro, poriam 3.950 nomes no pool, quase todos loja/imoveis + nome de
    pessoa (lojacarolbraga, leandraimoveis). Por isso: exatamente duas partes
    de conteudo; a que nao e nicho precisa ter 4 letras e ser palavra comum;
    nome e sobrenome comuns nao contam.
    """
    if not lexico.comuns or not rotulo.isalpha():
        return None

    def conteudo(parte: str, outra_e_nicho: bool) -> bool:
        if parte in lexico.pessoas:
            return False
        if e_nicho(parte):
            return True
        return outra_e_nicho and len(parte) >= 4 and parte in lexico.comuns

    for corte in range(3, len(rotulo) - 2):
        a, resto = rotulo[:corte], rotulo[corte:]
        for liga in ("",) + CONECTIVOS:
            if liga and not resto.startswith(liga):
                continue
            b = resto[len(liga):]
            if len(b) < 3:
                continue
            if (conteudo(a, e_nicho(b)) and conteudo(b, e_nicho(a))
                    and (e_nicho(a) or e_nicho(b))):
                return tuple(p for p in (a, liga, b) if p)
    return None


@dataclass(frozen=True)
class Nota:
    valor: int
    motivos: tuple[str, ...] = ()

    def __str__(self) -> str:
        return f"{self.valor} ({'; '.join(self.motivos)})"


def desempate_da_nota(dominio: str) -> tuple[int, int, str]:
    """
    Chave de desempate de nota igual: .com.br primeiro, depois o rotulo mais
    curto, depois A-Z. A mesma de site_modelo/lista/util.js (desempateDaNota).
    """
    rotulo, _, extensao = dominio.partition(".")
    return (0 if extensao == "com.br" else 1, len(rotulo), dominio)


def separar(dominio: str) -> tuple[str | None, str | None]:
    """"exemplo.com.br" -> ("exemplo", "com.br"). Sem ponto, devolve (None, None)."""
    rotulo, ponto, extensao = dominio.partition(".")
    if not ponto:
        return None, None
    return rotulo, extensao


def _peso_do_tamanho(n: int) -> tuple[int, str | None]:
    if n <= 3:
        return PESO_SIGLA, "sigla de 3 letras"
    if n <= 6:
        return PESO_CURTO, "nome curto"
    if n <= 9:
        return PESO_MEDIO, None
    if n <= 14:
        return PESO_LONGO, None
    return PESO_MUITO_LONGO, None


def _peso_da_extensao(extensao: str) -> tuple[int, str | None]:
    if extensao == "com.br":
        return PESO_COM_BR, None
    if extensao in PESO_EXTENSAO:
        return PESO_EXTENSAO[extensao], f"extensão {extensao}"
    if extensoes.restrita(extensao):
        return PESO_EXTENSAO_RESTRITA, None   # o motivo sai em pontuar(), com quem registra
    return PESO_EXTENSAO_GENERICA, f"extensão {extensao}"


def _peso_da_palavra(rotulo: str, pt, en, lexico: Lexico) -> tuple[int, str | None]:
    """O peso de vocabulario com o teto pela posicao no wordfreq."""
    peso, motivo = _peso_do_vocabulario(rotulo, pt, en, lexico)
    # sem o wordfreq (fonte que nao baixou), fica o peso cheio
    if not lexico.popularidade or not e_palavra(rotulo, pt, en, lexico):
        return peso, motivo
    posicao = lexico.popularidade.get(rotulo, math.inf)
    if rotulo not in en and posicao >= RANKING_RARA:
        return min(peso, PESO_PALAVRA_RARA), "palavra de dicionário pouco usada"
    if posicao >= RANKING_MEDIA:
        peso = min(peso, PESO_PALAVRA_MEDIA)
    if 7 <= len(rotulo) <= 9:
        peso += PESO_PALAVRA_LONGA
    elif len(rotulo) >= 10:
        peso += PESO_PALAVRA_MUITO_LONGA
    return peso, motivo


def _peso_do_vocabulario(rotulo: str, pt, en,
                         lexico: Lexico) -> tuple[int, str | None]:
    if rotulo in pt:
        return PESO_PALAVRA_PT, "palavra em português"
    if rotulo in en:
        return PESO_PALAVRA_EN, "palavra em inglês"
    if rotulo in lexico.flexoes:
        return PESO_FLEXAO, "palavra em português (flexão)"
    achados = sorted({k for k in NICHOS if len(k) >= 3 and k in rotulo})
    if achados:
        return PESO_NICHO, f"nicho: {', '.join(achados[:3])}"
    return 0, None


def e_palavra(rotulo: str, pt, en, lexico: Lexico = LEXICO_VAZIO) -> bool:
    return rotulo in pt or rotulo in en or rotulo in lexico.flexoes


def pontuar(dominio: str, pt, en, *, elegivel: bool = False,
            lexico: Lexico | None = None) -> Nota:
    """Nota de 0 a 100. `pt` e `en` sao conjuntos de palavras sem acento."""
    lexico = lexico or LEXICO_VAZIO
    rotulo, extensao = separar(dominio)
    if not rotulo:
        return Nota(0)

    # Digito e hifen pioram o nome, mas nao o anulam: 2dev.com.br e
    # 1btc.com.br sao elegiveis ao leilao, ou seja, ja provaram demanda.
    # Antes qualquer nome nao-alfabetico caia para nota 0 e ia para o fim da
    # fila, o que deixou 41 elegiveis no fundo do ranking.
    if not rotulo.replace("-", "").isalnum():
        return Nota(0)

    total = 0
    motivos: list[str] = []

    for peso, motivo in (_peso_do_tamanho(len(rotulo)),
                         _peso_da_palavra(rotulo, pt, en, lexico)):
        total += peso
        if motivo:
            motivos.append(motivo)

    if e_palavra(rotulo, pt, en, lexico):
        if lexico.popularidade.get(rotulo, math.inf) < RANKING_POPULAR:
            total += PESO_POPULAR
            motivos.append("palavra popular")
    else:
        partes = composto_de_nicho(rotulo, lexico)
        if partes:
            total += PESO_COMPOSTO_NICHO
            motivos.append(f"composto: {' + '.join(partes)}")
        cidades = lexico.padrao_cidades
        if cidades and any(n in rotulo for n in _RAIZES if len(n) >= 4):
            achada = cidades.search(rotulo)
            if achada:
                total += PESO_CIDADE_NICHO
                motivos.append(f"nicho + cidade: {achada.group(0)}")

    if len(rotulo) == 3 and extensao == "com.br" and rotulo.isalpha():
        total += PESO_TRES_COM_BR
        motivos.append("três letras .com.br")

    # Os motivos NAO dizem quantas empresas: a contagem e derivada de dado
    # CC BY-ND 3.0 e nao deve ir para a pagina publica. O motivo diz o que o
    # sinal significa, a nota carrega o peso. So no .com.br: fora dele a
    # palavra de nome de empresa nao separou a disputa (30/09/2026, 535
    # conferidos: 1,1% com 2+ candidatos), e o bonus punha lima.app.br e
    # spot.ia.br no topo dos livres.
    com_br = extensao == "com.br"
    if com_br and lexico.negocios.get(rotulo, 0) >= MINIMO_EMPRESAS_PALAVRA:
        total += PESO_PALAVRA_DE_NEGOCIO
        motivos.append("palavra comum em nome de empresa")
    if com_br and lexico.nomes_de_empresa.get(rotulo, 0) >= MINIMO_EMPRESAS_NOME:
        total += PESO_NOME_DE_EMPRESAS
        motivos.append("nome usado por várias empresas")

    proprios, citados = lexico.links.get(dominio, (0, 0))
    if citados and proprios >= MINIMO_REFERENTES_CITADO:
        total += PESO_LINKS_CITADO
        motivos.append("links de sites muito citados")
    elif citados or proprios >= MINIMO_REFERENTES:
        total += PESO_LINKS
        motivos.append("outros sites apontam para ele")
    elif proprios and extensao == "com.br":
        total += PESO_UM_REFERENTE_COM_BR

    if elegivel:
        total += PESO_ELEGIVEL
        motivos.append("elegível ao leilão")

    peso, motivo = _peso_da_extensao(extensao)
    total += peso
    if motivo:
        motivos.append(motivo)
    # Quem pode registrar limita o mercado (adv.br so CPF, ind.br so CNPJ do
    # ramo): o motivo diz quem, e o peso ja caiu em _peso_da_extensao.
    # Fonte: dominio/extensoes.py.
    if extensoes.restrita(extensao):
        motivos.append(f"extensão restrita: {extensoes.quem_registra(extensao)}")

    if len(rotulo) > 16:
        total += PENA_COMPRIDO
    if _REPETICAO.search(rotulo):
        total += PENA_REPETICAO
    if any(c.isdigit() for c in rotulo):
        total += PENA_DIGITO
        motivos.append("tem número")
    if "-" in rotulo:
        total += PENA_HIFEN
        motivos.append("tem hífen")

    return Nota(max(0, min(100, total)), tuple(motivos))
