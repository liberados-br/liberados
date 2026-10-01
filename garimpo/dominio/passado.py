"""
O passado de um nome alem do Internet Archive e dos links: se teve visita de
verdade (CrUX), se foi citado (noticia, Wikipedia, Wikidata) e o que o
Cloudflare Intel achou que ele era, com data.

Camada de dominio: funcoes puras, sem rede e sem disco. Cada sinal e um
indice permanente em docs/historico/<pasta>/XX.txt, nas 256 fatias do hash
das passagens, montado na maquina local por arquivar_passado.py e servido
estatico. Como nos outros indices, nome fora do indice deixa a secao da
ficha calada: "nao consultado" nunca vira "nao tem".

TRAFEGO (CrUX). O Chrome UX Report publica todo mes as origens com visita
suficiente no Chrome, por pais, com faixa de popularidade (1 mil, 5 mil,
10 mil, 50 mil, 100 mil, 500 mil, 1 milhao). Estar la e ter tido gente de
verdade, nao necessariamente trafego organico. Medido em 30/09/2026 com o
top 1 milhao do Brasil (23 retratos de 2021 a 2026): 6.520 nomes da lista
de setembro; entre os conferidos, quem esteve entre os 100 mil mais
abertos foi disputado em 25,3%, contra 1,6% sem CrUX. Na faixa de 1
milhao sozinha o sinal e fraco (4,9%).

CITACOES. O grafo de links de noticia do GDELT (abr/2016 a jan/2019), os
links externos da Wikipedia em portugues e o "site oficial" (P856) da
Wikidata. Medido em 30/09/2026: 282, 488 e 57 nomes da lista.

CLASSIFICACAO (Cloudflare Intel, domain-history). Categoria de conteudo e
de seguranca com inicio e fim, desde dez/2020. Medido em 30/09/2026 numa
amostra de 1.000 nomes da lista: 596 com historico, 33 com sinal ruim
(estacionado, apostas, adulto, phishing; estacionado ficou neutro, ver
CATEGORIAS). Ruido medido e descartado: "DGA
Domains" marca palavra portuguesa longa como nome gerado por malware,
"Newly Seen Domains", "New Domains" e "CIPA Filter" nao dizem o que o site
era.
"""

from __future__ import annotations

from dataclasses import dataclass

from .passagens import fatia


def agrupar(linhas_por_dominio: dict[str, str]) -> dict[str, list[str]]:
    """{fatia: linhas ordenadas} a partir de {dominio: linha}."""
    fatias: dict[str, list[str]] = {}
    for dominio, linha in linhas_por_dominio.items():
        fatias.setdefault(fatia(dominio), []).append(linha)
    return {f: sorted(ls) for f, ls in fatias.items()}


def dono_da_origem(host: str, nomes: set[str]) -> str | None:
    """
    O nome da lista a que um host pertence: ele mesmo ou o dominio do qual
    e subdominio ("www.bicharia.com.br" e "loja.bicharia.com.br" contam
    para bicharia.com.br). None se nenhum.
    """
    host = host.strip().lower().rstrip(".")
    partes = host.split(".")
    for i in range(len(partes) - 1):
        candidato = ".".join(partes[i:])
        if candidato in nomes:
            return candidato
    return None


# ---------------------------------------------------------------- trafego

@dataclass(frozen=True)
class Trafego:
    dominio: str
    melhor: int                 # a melhor faixa alcancada (1000 ... 1000000)
    meses: int                  # em quantos retratos apareceu
    retratos: int               # quantos retratos existem (o denominador)
    primeiro: str               # AAAAMM
    ultimo: str                 # AAAAMM
    serie: tuple[tuple[str, int], ...] = ()   # (AAAAMM, faixa), em ordem


def resumir_trafego(dominio: str, vistos: dict[str, int], retratos: int) -> Trafego:
    """`vistos`: {AAAAMM: faixa} (a melhor do nome naquele mes)."""
    serie = tuple(sorted(vistos.items()))
    return Trafego(dominio=dominio, melhor=min(vistos.values()), meses=len(serie),
                   retratos=retratos, primeiro=serie[0][0], ultimo=serie[-1][0], serie=serie)


def linha_trafego(t: Trafego) -> str:
    return "\t".join((t.dominio, str(t.melhor), str(t.meses), str(t.retratos), t.primeiro,
                      t.ultimo, ",".join(f"{m}:{f}" for m, f in t.serie)))


def ler_trafego(texto: str) -> Trafego | None:
    p = texto.rstrip("\n").split("\t")
    if len(p) != 7 or not p[0]:
        return None
    try:
        serie = tuple((m, int(f)) for m, f in (x.split(":") for x in p[6].split(",") if x))
        return Trafego(p[0], int(p[1]), int(p[2]), int(p[3]), p[4], p[5], serie)
    except ValueError:
        return None


# ---------------------------------------------------------------- citacoes

@dataclass(frozen=True)
class Citacoes:
    dominio: str
    veiculos: int = 0                       # sites de noticia (GDELT) que linkaram
    principais: tuple[str, ...] = ()        # ate 5, dos que mais linkaram
    artigos: int = 0                        # paginas da Wikipedia em portugues
    oficial_de: tuple[str, ...] = ()        # entidades da Wikidata (rotulo), ate 3


VEICULOS_NA_LINHA = 5
ENTIDADES_NA_LINHA = 3


def linha_citacoes(c: Citacoes) -> str:
    # rotulo de entidade pode ter virgula; na linha elas vao separadas por " | "
    return "\t".join((c.dominio, str(c.veiculos), ",".join(c.principais), str(c.artigos),
                      " | ".join(e.replace("\t", " ").replace("|", "/") for e in c.oficial_de)))


def ler_citacoes(texto: str) -> Citacoes | None:
    p = texto.rstrip("\n").split("\t")
    if len(p) != 5 or not p[0]:
        return None
    try:
        return Citacoes(p[0], int(p[1]), tuple(x for x in p[2].split(",") if x), int(p[3]),
                        tuple(x for x in p[4].split(" | ") if x))
    except ValueError:
        return None


# ------------------------------------------------------------- classificacao

# id do Cloudflare Intel -> (rotulo em portugues, e sinal ruim?). A lista inteira
# vem de GET /accounts/{id}/gateway/categories (conferida em 30/09/2026).
CATEGORIAS: dict[int, tuple[str, bool]] = {
    # propaganda e rastreio
    66: ("Anúncios", False), 195: ("Rastreadores e métricas", False),
    # adulto
    67: ("Temas adultos", True), 125: ("Nudez", True), 133: ("Pornografia", True),
    170: ("Abuso infantil", True),
    # negocios
    186: ("Corretoras e investimentos", False), 75: ("Negócios", False),
    183: ("Criptomoedas", False), 89: ("Economia e finanças", False),
    185: ("Finanças pessoais", False),
    # educacao
    90: ("Educação", False), 91: ("Instituição de ensino", False), 189: ("Referência", False),
    144: ("Ciência", False), 150: ("Espaço e astronomia", False),
    # entretenimento
    70: ("Artes", False), 74: ("Áudio e streaming", False), 76: ("Desenho e anime", False),
    79: ("Quadrinhos", False), 92: ("Entretenimento", False), 96: ("Belas-artes", False),
    100: ("Jogos", False), 106: ("Vídeo doméstico", False), 107: ("Humor", False),
    116: ("Revistas", False), 120: ("Filmes", False), 121: ("Música", False),
    122: ("Notícias e mídia", False), 127: ("Paranormal", False), 139: ("Rádio", False),
    156: ("Televisão", False), 164: ("Vídeo e streaming", False),
    99: ("Apostas", True),
    # governo
    190: ("Beneficência e ONG", False), 101: ("Governo e jurídico", False),
    137: ("Política e governo", False),
    103: ("Saúde e boa forma", False), 146: ("Educação sexual", False),
    # comunicacao
    77: ("Chat", False), 98: ("Fórum", False), 108: ("Segurança da informação", False),
    110: ("Mensageiro", False), 111: ("Telefonia pela internet", False), 118: ("Mensagens", False),
    126: ("P2P", False), 129: ("Blog pessoal", False), 172: ("Fotos", False), 168: ("E-mail", False),
    113: ("Empregos e carreira", False),
    179: ("Militar", False), 166: ("Armas", False),
    115: ("Tela de login", False), 141: ("Redirecionamento", False),
    # conteudo questionavel
    85: ("Anúncios enganosos", True), 87: ("Drogas", True), 102: ("Hacking", True),
    157: ("Extremismo e ódio", True), 135: ("Palavrões", False),
    138: ("Atividade questionável", True), 180: ("Cola escolar", True),
    162: ("Informação não confiável", True),
    140: ("Imóveis", False), 142: ("Religião", False),
    # riscos e ameacas
    # Estacionado e o destino mais comum de nome vencido (a pagina do
    # registrador ou de quem revende): 24 dos 36 "ruins" da amostra de
    # 30/09/2026. Diz o que o nome foi, mas nao mancha como apostas ou
    # golpe: fica neutro, sem o aviso da lista.
    128: ("Estacionado ou à venda", False),
    68: ("Anonimizador", True), 178: ("Uso indevido de marca", True),
    80: ("Botnet", True), 187: ("Domínio comprometido", True), 83: ("Mineração escondida", True),
    175: ("Túnel de DNS", True), 117: ("Malware", True), 131: ("Phishing", True),
    188: ("Software indesejado", True), 191: ("Golpe", True), 151: ("Spam", True),
    153: ("Spyware", True),
    # compras
    73: ("Leilões e marketplaces", False), 82: ("Cupons", False), 88: ("Loja virtual", False),
    148: ("Compras", False),
    # sociedade
    65: ("Aborto", False), 181: ("Bebidas alcoólicas", False), 71: ("Artesanato", False),
    72: ("Astrologia", False), 173: ("Tatuagem e body art", False), 78: ("Roupas", False),
    84: ("Namoro", False), 86: ("Cartões digitais", False), 94: ("Moda", False),
    97: ("Comida e bebida", False), 104: ("Hobbies", False), 105: ("Casa e jardim", False),
    112: ("Joias", False), 171: ("LGBTQ", False), 114: ("Estilo de vida", False),
    174: ("Lingerie e biquíni", False), 93: ("Maternidade e família", False), 130: ("Pets", False),
    132: ("Fotografia", False), 136: ("Rede profissional", False), 147: ("Sexualidade", False),
    149: ("Rede social", False), 154: ("Moda praia", False), 158: ("Tabaco", False),
    152: ("Esportes", False),
    # tecnologia
    69: ("APIs", False), 184: ("Inteligência artificial", False), 81: ("Servidor de conteúdo", False),
    95: ("Compartilhamento de arquivos", False), 109: ("Tecnologia da informação", False),
    194: ("Software", False), 123: ("Portal e busca", False), 192: ("Acesso remoto", False),
    145: ("Buscador", False), 193: ("Software grátis", False), 155: ("Tecnologia", False),
    159: ("Tradutor", False),
    160: ("Viagem", False), 163: ("Veículos", False), 165: ("Violência", False),
    167: ("Clima", False),
}

# Nao dizem o que o site era (medido em 30/09/2026): DGA marca palavra
# portuguesa longa como nome de malware; os outros sao "visto ha pouco",
# filtro escolar dos EUA, "sem conteudo", "fora do ar", "diversos" e IP
# privado. Id desconhecido tambem fica de fora, ate ter rotulo.
RUIDO = frozenset({176, 177, 169, 182, 143, 124, 161, 119, 134})

RUIM, NEUTRO = "r", ""


@dataclass(frozen=True)
class Periodo:
    rotulo: str
    ruim: bool
    inicio: str        # AAAAMM
    fim: str = ""      # AAAAMM; "" = ate hoje


@dataclass(frozen=True)
class Classificacao:
    dominio: str
    consultado: str                         # AAAAMMDD
    periodos: tuple[Periodo, ...] = ()      # do mais antigo ao mais novo

    @property
    def ruim(self) -> bool:
        return any(p.ruim for p in self.periodos)


def _mes(iso: str | None) -> str:
    return (iso or "")[:7].replace("-", "")


def classificar(dominio: str, categorizacoes, consultado: str) -> Classificacao:
    """
    A partir de `categorizations` do domain-history: cada categoria que diz
    algo vira um periodo; a mesma categoria em periodos seguidos vira um so.
    """
    periodos: list[Periodo] = []
    for c in sorted(categorizacoes or [], key=lambda c: c.get("start") or ""):
        inicio, fim = _mes(c.get("start")), _mes(c.get("end"))
        for cat in c.get("categories") or []:
            cid = cat.get("id")
            if cid in RUIDO or cid not in CATEGORIAS:
                continue
            rotulo, ruim = CATEGORIAS[cid]
            anterior = next((i for i in range(len(periodos) - 1, -1, -1)
                             if periodos[i].rotulo == rotulo), None)
            if anterior is not None and periodos[anterior].fim and periodos[anterior].fim >= inicio:
                p = periodos[anterior]
                periodos[anterior] = Periodo(p.rotulo, p.ruim, p.inicio, fim)
            elif anterior is not None and not periodos[anterior].fim:
                continue                       # ja aberto ate hoje
            else:
                periodos.append(Periodo(rotulo, ruim, inicio, fim))
    return Classificacao(dominio, consultado, tuple(periodos))


def linha_classificacao(c: Classificacao) -> str:
    return "\t".join((c.dominio, c.consultado, ";".join(
        f"{RUIM if p.ruim else NEUTRO}:{p.rotulo}:{p.inicio}:{p.fim}" for p in c.periodos)))


def ler_classificacao(texto: str) -> Classificacao | None:
    p = texto.rstrip("\n").split("\t")
    if len(p) != 3 or not p[0]:
        return None
    periodos = []
    for parte in filter(None, p[2].split(";")):
        pedacos = parte.split(":")
        if len(pedacos) != 4 or pedacos[0] not in (RUIM, NEUTRO):
            return None
        periodos.append(Periodo(pedacos[1], pedacos[0] == RUIM, pedacos[2], pedacos[3]))
    return Classificacao(p[0], p[1], tuple(periodos))
