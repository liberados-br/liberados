"""
"Quem aponta para este dominio?" -- os links que a web ainda faz para um
nome, lidos no grafo de dominios do CommonCrawl.

Camada de dominio: funcoes puras, sem rede e sem disco.

POR QUE ISTO IMPORTA

Quem garimpa nome com passado (o publico do SEO) abre um por um no Semrush ou
no Ahrefs para ver quantos dominios ainda apontam para ele e quem sao. Nome
com link de jornal ou de portal sai na frente no Google; nome com milhares
de links de sites desconhecidos pode ter historico de spam. Essas ferramentas
sao pagas e os termos delas proibem republicar o dado. O CommonCrawl publica
de graca, a cada trimestre, o grafo de links entre dominios da web inteira,
com duas medidas de centralidade para cada no (harmonic centrality e
PageRank): um download, zero consulta por nome.

Medido em 30/09/2026 com o grafo cc-main-2026-jun-jul-aug (119,7 milhoes de
dominios, 1,22 milhao deles .br): 5.480 dos 125.453 nomes da lista de
setembro aparecem no grafo. Entre os 17.523 conferidos, 12,5% dos que
aparecem foram disputados, contra 1,3% dos que nao aparecem, e a diferenca
se mantem em cada faixa de nota.

O QUE O GRAFO PROVA E O QUE NAO PROVA

Um no existe porque alguma pagina coletada fez link para ele, ou porque ele
foi coletado. A maioria dos nos nunca foi coletada e e so alvo de link (75%
no grafo de hosts de fev-abr/2026, pelo anuncio do CommonCrawl). Isso
e exatamente o que serve aqui (o dominio vencido que ainda recebe link), mas
o CommonCrawl AMOSTRA a web. Nome fora do grafo e "o CommonCrawl nao viu
link", nunca "nao tem link"; por isso so nome presente ganha linha, e a
ficha fica calada sobre o resto, como no indice do Internet Archive.

Nada aqui vira veredito de "spam": a linha guarda fatos (quantos apontam,
quantos deles sao .br, quantos estao entre os dominios mais centrais da web
e quais sao os principais), e quem le decide.
"""

from __future__ import annotations

from dataclasses import dataclass

from .passagens import fatia

# Um referente "forte" esta entre os FORTE primeiros dominios da web pela
# harmonic centrality do mesmo grafo. Com 133,2 milhoes de nos, o primeiro
# milhao e o 0,75% mais central: portais, jornais, universidades, orgaos
# publicos e os sites grandes de cada nicho.
FORTE = 1_000_000

# Quantos referentes a linha nomeia: os de melhor posicao, que sao o que
# o Semrush mostra primeiro ("quem fala desse site").
PRINCIPAIS = 5

# PLATAFORMAS ONDE QUALQUER UM PUBLICA. O grafo e por dominio: todo blog em
# fulano.blogspot.com vira "blogspot.com aponta", e o blogspot esta entre os
# 50 dominios mais centrais da web. Medido no grafo jul-set/2026 contra os
# 5.478 nomes da lista de setembro: blogspot.com apontava para 534 deles,
# google.com para 21, linktr.ee para 40, wixsite.com para 14 -- e um nome de
# spam de aposta tinha 5 de 6 referentes "fortes", a comecar por google.com
# e myshopify.com (cada loja Shopify e um subdominio). Link de
# plataforma conta como referente (e um link de verdade), mas nao como
# forte nem entre os principais: nao diz que alguem conhecido citou o nome.
# Marca inequivoca casa pelo primeiro rotulo, para pegar as variantes por
# pais (google.com.br, blogspot.com.br, pinterest.co.uk). Palavra comum
# ("academia", "archive", "medium", "web", "x") casa so o dominio exato:
# academia.org.br e a Academia Brasileira de Letras, nao o academia.edu.
PLATAFORMAS = frozenset({
    # blogs e construtores de site
    "blogspot", "blogger", "wordpress", "wixsite", "wix", "weebly", "tumblr",
    "jimdo", "jimdofree", "webnode", "site123", "yolasite", "strikingly",
    "squarespace", "over-blog", "livejournal", "cocolog-nifty", "wikidot",
    "hatenablog", "substack", "pbworks", "liveinternet", "neocities",
    "webflow", "notion", "gitbook", "readthedocs", "telegra", "rentry",
    "prezi", "canva",
    # hospedagem e DNS com subdominio de usuario
    "amazonaws", "cloudfront", "github", "gitlab", "netlify", "vercel",
    "herokuapp", "appspot", "googleusercontent", "firebaseapp",
    "000webhostapp", "freemyip", "duckdns", "no-ip", "azurewebsites",
    "myshopify", "xsrv", "glitch", "replit",
    # Google (Sites, Grupos, Maps, Docs) e redes onde o usuario posta
    "google", "youtube", "facebook", "instagram", "twitter", "linkedin",
    "pinterest", "reddit", "tiktok", "quora", "whatsapp", "discord", "flickr",
    "vimeo", "soundcloud",
    # links e documentos postados por usuario
    "linktr", "tinyurl", "papaly", "scribd", "issuu", "slideshare", "yumpu",
    "calameo",
})
PLATAFORMAS_EXATAS = frozenset({
    "medium.com", "ghost.io", "tilda.ws", "carrd.co", "webs.com", "web.app",
    "ddns.net", "x.com", "t.co", "t.me", "wa.me", "ok.ru", "vk.com",
    "telegram.org", "bit.ly", "archive.org", "archive.ph", "archive.is",
    "academia.edu", "free.fr", "surge.sh",
    # pseudo-registros (CentralNic e parecidos): qualquer um compra
    # fulano.uk.com, e o grafo junta tudo em uk.com. Medido em 30/09/2026: o
    # trustguru.com.br tinha eu.com, co.com, uk.com e us.com entre os "muito
    # citados", ao lado de stanford.edu e nature.com.
    "br.com", "cn.com", "de.com", "eu.com", "gb.com", "gb.net", "gr.com",
    "hu.com", "hu.net", "jp.net", "jpn.com", "kr.com", "mex.com", "no.com",
    "qc.com", "ru.com", "sa.com", "se.com", "se.net", "uk.com", "uk.net",
    "us.com", "us.org", "uy.com", "za.com", "co.com", "in.net", "ae.org",
    "com.de", "africa.com", "co.nl", "com.se", "eu.org",
})


def plataforma(dominio: str) -> bool:
    """'fulano.blogspot.com' ja chega como 'blogspot.com'; 'google.com.br' tambem conta."""
    return dominio in PLATAFORMAS_EXATAS or dominio.split(".", 1)[0] in PLATAFORMAS


def invertido(dominio: str) -> str:
    """'bicharia.com.br' -> 'br.com.bicharia', a notacao do grafo."""
    return ".".join(reversed(dominio.strip().lower().split(".")))


def direto(chave: str) -> str:
    """'br.com.bicharia' -> 'bicharia.com.br'."""
    return ".".join(reversed(chave.split(".")))


@dataclass(frozen=True)
class Links:
    """Os links de um nome, no tamanho que cabe numa linha do indice."""

    dominio: str
    grafo: str                   # ex.: "2026-06/2026-08", meses do grafo
    total: int                   # nos no grafo inteiro (o denominador)
    harmonica: int               # posicao pela harmonic centrality (1 = a mais central)
    pagerank: int                # posicao pelo PageRank
    referentes: int = 0          # dominios distintos que apontam para ele
    referentes_br: int = 0       # deles, quantos terminam em .br
    fortes: int = 0              # deles, quantos estao entre os FORTE primeiros (sem plataforma)
    plataformas: int = 0         # deles, quantos sao plataforma onde qualquer um publica
    principais: tuple[str, ...] = ()   # os de melhor posicao sem plataforma, ate PRINCIPAIS


def ordenar(dominio: str, referentes) -> list[tuple[str, int]]:
    """
    (dominio, posicao harmonica) de quem aponta, um por dominio, do mais
    central para o menos. Autolink (o proprio nome) nao conta.
    """
    vistos: dict[str, int] = {}
    for nome, posicao in referentes:
        if nome == dominio:
            continue
        if nome not in vistos or posicao < vistos[nome]:
            vistos[nome] = posicao
    return sorted(vistos.items(), key=lambda p: (p[1], p[0]))


def resumir(dominio: str, grafo: str, total: int, harmonica: int, pagerank: int,
            referentes) -> Links:
    """
    `referentes`: pares (dominio, posicao harmonica) de quem aponta para o
    nome, um por dominio. Autolink (o proprio nome) nao conta.
    """
    ordem = ordenar(dominio, referentes)
    proprios = [(n, p) for n, p in ordem if not plataforma(n)]
    return Links(
        dominio=dominio, grafo=grafo, total=total,
        harmonica=harmonica, pagerank=pagerank,
        referentes=len(ordem),
        referentes_br=sum(1 for n, _ in ordem if n.endswith(".br")),
        fortes=sum(1 for _, p in proprios if p <= FORTE),
        plataformas=len(ordem) - len(proprios),
        principais=tuple(n for n, _ in proprios[:PRINCIPAIS]),
    )


# -------------------------------------------------------------- o indice

COLUNAS = 10


def linha(r: Links) -> str:
    """Uma linha da fatia: separada por TAB, na ordem de `Links`."""
    return "\t".join((r.dominio, r.grafo, str(r.total), str(r.harmonica),
                      str(r.pagerank), str(r.referentes), str(r.referentes_br),
                      str(r.fortes), str(r.plataformas), ",".join(r.principais)))


def ler_linha(texto: str) -> Links | None:
    partes = texto.rstrip("\n").split("\t")
    if len(partes) != COLUNAS or not partes[0]:
        return None
    try:
        return Links(dominio=partes[0], grafo=partes[1], total=int(partes[2]),
                     harmonica=int(partes[3]), pagerank=int(partes[4]),
                     referentes=int(partes[5]), referentes_br=int(partes[6]),
                     fortes=int(partes[7]), plataformas=int(partes[8]),
                     principais=tuple(p for p in partes[9].split(",") if p))
    except ValueError:
        return None


def agrupar(resumos) -> dict[str, list[str]]:
    """{fatia: linhas ordenadas}. Mesma fatia das passagens, de proposito."""
    fatias: dict[str, list[str]] = {}
    for r in resumos:
        fatias.setdefault(fatia(r.dominio), []).append(linha(r))
    return {f: sorted(ls) for f, ls in fatias.items()}


# -------------------------------------------------- todos os referentes

# A lista inteira de quem aponta, para a pessoa tocar em "N links" e ver
# todos. Um arquivo a parte (docs/historico/referentes/), nas mesmas fatias:
# o indice de links fica pequeno e so quem abre a lista baixa a fatia dela.
# Linha: "nome<TAB>c:globo.com,:blog.net,p:blogspot.com", do mais central
# para o menos; "c" e muito citado, "p" e plataforma, vazio e o resto.
CITADO, ABERTO = "c", "p"


def marca(nome: str, posicao: int) -> str:
    if plataforma(nome):
        return ABERTO
    return CITADO if posicao <= FORTE else ""


def linha_referentes(dominio: str, ordem: list[tuple[str, int]]) -> str:
    return dominio + "\t" + ",".join(f"{marca(n, p)}:{n}" for n, p in ordem)


def ler_referentes(texto: str) -> tuple[str, list[tuple[str, str]]] | None:
    """(dominio, [(marca, referente), ...]); None se a linha nao e do formato."""
    dominio, sep, resto = texto.rstrip("\n").partition("\t")
    if not sep or not dominio:
        return None
    itens = []
    for parte in filter(None, resto.split(",")):
        m, dois, nome = parte.partition(":")
        if not dois or m not in ("", CITADO, ABERTO) or not nome:
            return None
        itens.append((m, nome))
    return dominio, itens


def agrupar_referentes(ordens: dict[str, list[tuple[str, int]]]) -> dict[str, list[str]]:
    fatias: dict[str, list[str]] = {}
    for dominio, ordem in ordens.items():
        if ordem:
            fatias.setdefault(fatia(dominio), []).append(linha_referentes(dominio, ordem))
    return {f: sorted(ls) for f, ls in fatias.items()}
