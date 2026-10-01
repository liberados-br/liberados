"""
A rodada inteira, pesquisavel no site: os ~125 mil nomes com a nota de cada um.

Caso de uso. O site mostra, por padrao, os ~17 mil que a varredura consulta.
Mas o corte de 45 e opiniao da ferramenta, e quem procura um nome especifico
(o da propria cidade, o do proprio ramo) quer procurar em tudo. Este arquivo
e isso: cada nome da lista oficial, com nota e motivos, SEM situacao. Quem
quer saber a situacao de um nome de fora do pool clica em "conferir", que
pergunta ao Registro.br do navegador de quem visita.

Formato compacto e DETERMINISTICO de proposito: sem data de geracao, sem
ordem instavel. O workflow comita o que mudou a cada execucao; com o mesmo
conteudo o git nao ve diferenca, e um arquivo de ~3 MB nao entra no historico
a cada execucao. So muda quando a lista ou a nota mudam.

    {"versao": 1, "rodada": {...}, "extensoes": [...], "motivos": [...],
     "marcas": ["OK", "ATENCAO", "RISCO"], "categorias": [...],
     "itens": [[rotulo, indice_extensao, nota, [indices_de_motivo],
                indice_de_risco, bits_de_categoria, [links]?, sinal?], ...]}

`[links]` e [referentes sem plataforma, muito citados] do indice "quem
aponta" e so existe nos nomes que tem linha nele (~5 mil de 125 mil).
`sinal` e o rotulo do sinal ruim mais recente do Cloudflare Intel ("Apostas"); com
ele e sem links, a setima posicao vem 0.

O risco de marca vai junto: busca na rodada inteira sem ele levaria gente
direto a nome de terceiro, que e justamente o que o projeto evita.

Os motivos sao so o tipo ("composto", nao "composto: loja + online"): o
detalhe multiplicaria o vocabulario por dezenas de milhares de variantes.
"""

from __future__ import annotations

import json
import os

from ..dominio import categorias
from ..dominio.marcas import Risco
from ..dominio.relevancia import separar
from .pool import marca_de, nota_de

VERSAO = 3   # 2: o setimo campo, links; 3: o oitavo, o sinal ruim do Cloudflare Intel
RISCOS = (Risco.OK, Risco.ATENCAO, Risco.RISCO)


def _tipo(motivo: str) -> str:
    return motivo.split(":", 1)[0].strip()


def montar(rodada, vocabularios, sinais: dict[str, str] | None = None) -> dict:
    extensoes: dict[str, int] = {}
    motivos: dict[str, int] = {}
    itens = []
    lexico = getattr(vocabularios, "lexico", None)
    pessoas = lexico.pessoas if lexico else frozenset()
    links = lexico.links if lexico else {}
    for dominio in sorted(set(rodada.liberacao)):
        rotulo, extensao = separar(dominio)
        if not rotulo:
            continue
        nota = nota_de(dominio, vocabularios, elegivel=dominio in rodada.elegiveis)
        tipos = sorted({motivos.setdefault(_tipo(m), len(motivos))
                        for m in nota.motivos})
        risco = marca_de(dominio, vocabularios).risco
        item = [rotulo, extensoes.setdefault(extensao, len(extensoes)),
                nota.valor, tipos, RISCOS.index(risco),
                categorias.mascara(rotulo, pessoas)]
        # [referentes sem plataforma, muito citados]: so nos ~5 mil que tem
        # linha no indice de links, para nao pesar 125 mil ",0" no arquivo
        sinal = (sinais or {}).get(dominio)
        if dominio in links or sinal:
            item.append(list(links[dominio]) if dominio in links else 0)
        if sinal:
            item.append(sinal)
        itens.append(item)
    return {
        "versao": VERSAO,
        "rodada": {"inicio": rodada.inicio, "fim": rodada.fim},
        "extensoes": sorted(extensoes, key=extensoes.get),
        "motivos": sorted(motivos, key=motivos.get),
        "marcas": [r.value for r in RISCOS],
        "categorias": categorias.tabela(),
        "itens": itens,
    }


def escrever(dados: dict, caminho: str) -> int:
    os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
    parcial = caminho + ".parcial"
    with open(parcial, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(parcial, caminho)
    return os.path.getsize(caminho)
