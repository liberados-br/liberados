# Liberados

[![Testes](https://github.com/liberados-br/liberados/actions/workflows/testes.yml/badge.svg)](https://github.com/liberados-br/liberados/actions/workflows/testes.yml)
[![Licença MIT](https://img.shields.io/badge/licen%C3%A7a-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/imagens/inicio-escuro.png">
  <img alt="A página inicial do Liberados: a contagem regressiva para a próxima rodada no alto da folha, a busca, os números da rodada e os três passos" src="docs/imagens/inicio-claro.png" width="100%">
</picture>

Todo mês o Registro.br devolve ao mercado cerca de **125 mil domínios
`.br`** que não foram renovados. Eles passam por uma rodada de sete dias, o
*processo de liberação*, e a lista é pública — mas é um arquivo de texto em
ISO-8859-1 com cem mil linhas e nenhuma informação além do nome.

O **Liberados** torna essa lista legível:

- uma **nota de relevância** (0 a 100) para cada nome, que decide o que vale
  olhar primeiro;
- **quantos candidatos** cada nome já tem, lido do próprio Registro.br;
- quais nomes estão **em leilão** (o processo competitivo) e até quando;
- **quando um domínio registrado volta** à lista, se não for renovado.

Este repositório é o motor que forma o site
**[liberados.com.br](https://liberados.com.br)**: a varredura, a nota, os
adaptadores do Registro.br, o exportador que gera o site estático, uma
interface local e a suíte de testes. Python 3.10+ puro, sem dependências.

## Por que isso é difícil

O processo de liberação é público, mas mal documentado e cheio de detalhes
contraintuitivos:

- **não é ordem de chegada** — o primeiro e o último candidato têm o mesmo
  peso;
- com **um** candidato, ele leva o domínio pela anuidade normal (R$ 40);
- com **dois ou mais**, ninguém leva: o nome **trava** e volta no mês
  seguinte; depois de três travas seguidas, vai a leilão;
- cada titular tem um **limite de 3 a 200 candidaturas** simultâneas;
- o endpoint de disponibilidade **esconde o candidato único**: "0
  candidatos" quer dizer "zero ou um".

A pergunta útil, portanto, não é "qual o melhor nome", e sim "qual bom nome
ninguém mais notou". O endpoint de disponibilidade do Registro.br devolve
**quantas candidaturas um domínio já tem**, e isso permite saber que um nome
vai travar *antes* de gastar uma vaga. As regras, com as fontes oficiais,
estão em [`docs/processo-de-liberacao.md`](docs/processo-de-liberacao.md) e
[`docs/processo-competitivo.md`](docs/processo-competitivo.md).

## Começo rápido

```bash
git clone https://github.com/liberados-br/liberados.git
cd liberados
python3 app.py
```

A interface local abre em `http://localhost:8765`. Clique em **Baixar
listas oficiais** e depois em **Verificar**. Não há nada para instalar: só
Python 3.10 ou mais novo (e `curl` e `iconv` para o script de download).

## Linha de comando

O fluxo completo, do arquivo oficial aos nomes sem concorrente visível:

```bash
# 1. baixar as listas oficiais da rodada atual (converte de ISO-8859-1)
./baixar_listas.sh                      # gera liberacao.txt e competitivo.txt

# 2. reduzir ~125 mil nomes a algumas centenas
python3 filtrar_lista.py liberacao.txt \
    --tld com.br --min 4 --max 9 --sem-numero --sem-hifen \
    --out candidatos.txt

# 3. marcar o que parece marca de terceiro (OK / ATENCAO / RISCO)
python3 flag_marcas.py candidatos.txt --out risco.csv
grep -v RISCO risco.csv | cut -d, -f1 | tail -n +2 > limpos.txt

# 4. consultar a situação real, uma consulta a cada 2 s
python3 check_dominios.py limpos.txt --out resultado.csv --delay 2

# 5. em liberação e sem candidato visível
grep LIBERACAO_LIVRE resultado.csv
```

Para experimentar sem baixar a lista, há um arquivo pequeno em
[`exemplos/`](exemplos/):

```bash
python3 check_dominios.py exemplos/lista-exemplo.txt --out resultado.csv --delay 2
```

A saída tem o formato de
[`exemplos/resultado-exemplo.csv`](exemplos/resultado-exemplo.csv):

| Status | Significado |
|---|---|
| `LIVRE` | disponível para registro imediato |
| `LIBERACAO_LIVRE` | em liberação, **sem candidato visível** (zero ou um) |
| `LIBERACAO_DISPUTADA` | em liberação, já tem candidato; com dois ou mais, trava |
| `COMPETITIVO` | em leilão |
| `REGISTRADO` | já tem titular |
| `LIMITADO` | bloqueado por excesso de consultas: aumente o `--delay` e espere |
| `ERRO` | falha de rede ou resposta inesperada |

> **Leia "0" como "zero ou um".** O Registro.br documenta que o endpoint só
> mostra os tickets quando há mais de um candidato. Em 1.657 domínios
> verificados em 09/09/2026 não apareceu nenhum com exatamente 1 candidato,
> enquanto dezenas apareciam com 2. Um nome "sem competição" pode já ter um
> candidato; se você se candidatar, vira o segundo, e **ninguém leva**.

Outras ferramentas:

| Script | O que faz |
|---|---|
| [`varrer.py`](varrer.py) | varredura com orçamento de tempo (`--minutos 7`): baixa as três listas, monta a fila pela idade de cada leitura, consulta até o tempo acabar e regera o site. Pensada para rodar sem supervisão, em execuções curtas e repetidas |
| [`desfecho.py`](desfecho.py) | depois que a rodada fecha, compara um instantâneo anterior (`--antes site/dados.json`) com a situação atual e mede quantos "0 candidatos" eram, na verdade, 1 |
| [`sinais_do_desfecho.py`](sinais_do_desfecho.py) | cruza o desfecho com o que se sabia antes (nota, tamanho, elegibilidade, extensão) |
| [`historias.py`](historias.py) | o que aconteceu com domínios que já têm titular: RDAP (titular, prazos, conferência de DNS do próprio registro), DNS e Internet Archive. Entrada: CSV `dominio,valor,ano,fonte`; `--titular` diz quantos domínios o titular tem |
| [`isavail.py`](isavail.py) | consulta pontual ao ISAVAIL, o serviço oficial (UDP 43) por trás do endpoint web; `--bruto` mostra o pacote. Contingência, não varredura |
| [`demanda_cnpj.py`](demanda_cnpj.py) | conta, no cadastro aberto de CNPJ da Receita, as palavras usadas em nome fantasia; o resultado entra na nota e fica em `work/`, fora do git (o dado é CC BY-ND) |
| [`acentuar.py`](acentuar.py) | acentua o texto visível do site sem tocar em código (`--conferir` só mostra o que mudaria) |

Todos aceitam `--help`. Nenhum se candidata a nada nem faz login: a
candidatura é manual, no painel do Registro.br.

## O site

```bash
python3 exportar_site.py
```

Gera `site/`: uma versão **somente leitura** da interface, com os mesmos
filtros, lendo um instantâneo (`site/dados.json`) em vez de consultar o
Registro.br, mais as páginas de texto, `sitemap.xml`, `robots.txt`,
`llms.txt` e os lembretes de calendário (`.ics`). O exportador lê o banco
local (`dados.db`), alimentado pela interface, por
`check_dominios.py --banco` ou por `varrer.py`. É exatamente o que roda em
[liberados.com.br](https://liberados.com.br).

O site não guarda nada sobre quem visita e só publica dado de domínio. As
consultas que envolvem dado de pessoa (quem disputa um nome, o titular de um
domínio registrado) são feitas **pelo navegador de quem olha**, direto no
RDAP, sob demanda, e o resultado fica naquele aparelho. Detalhes em
[`SECURITY.md`](SECURITY.md).

Cada linha do instantâneo carrega quando foi consultada. O site mostra essa
idade, e o botão *conferir* pergunta ao RDAP do Registro.br (CORS aberto)
na hora, do navegador do visitante.

## Arquitetura

Camadas, com a dependência apontando sempre para dentro:

```
garimpo/
├── dominio/        regras puras: sem rede, sem banco, sem I/O
│   ├── situacao.py     enumeração do status (ISAVAIL) -> situação
│   ├── relevancia.py   nota de relevância, 0 a 100
│   ├── marcas.py       risco de marca registrada
│   ├── categorias.py   ramo de negócio de cada nome
│   ├── extensoes.py    as extensões do .br e quem pode registrar
│   ├── frescor.py      prazo de cada classe e a fila da varredura
│   ├── ritmo.py        o contador global de tickets, lido como relógio
│   ├── calendario.py   datas das rodadas e previsão de volta
│   ├── passagens.py    em que rodadas um nome passou pela lista
│   ├── historico.py    leitura das listas antigas
│   ├── arquivo.py      "este domínio já teve site?", no Internet Archive
│   ├── links.py        quem ainda aponta para o nome, no grafo do CommonCrawl
│   └── passado.py      visitas reais, citações e o que o site era, com data
├── adaptadores/    a fronteira com o mundo externo
│   ├── registrobr.py   cliente HTTP e as três listas oficiais
│   ├── rdap.py         RDAP do .br e o teste de DNS
│   ├── isavail.py      cliente UDP do ISAVAIL
│   ├── wayback.py      Internet Archive
│   ├── cnpj.py         cadastro aberto de CNPJ
│   ├── dicionarios.py  vocabulários da nota (hunspell.py, wordfreq.py)
│   └── repositorio.py  SQLite; o único lugar com SQL
├── casos/          orquestração; recebe adaptadores por parâmetro
│   ├── pool.py         monta o conjunto que vale consultar
│   ├── varredura.py    consulta respeitando pausa, orçamento e parada
│   ├── manutencao.py   a fila de cada execução
│   ├── instantaneo.py  serializa e restaura o estado em JSON
│   ├── todos.py        a rodada inteira, pesquisável
│   ├── demanda.py      demanda por nome, a partir do CNPJ
│   ├── historias.py    o que aconteceu com um nome depois
│   ├── arquivo_das_rodadas.py  as listas e as disputas de cada rodada, guardadas
│   └── lembretes.py    os .ics de fim de leilão e de cada rodada
├── web/            apresentação
│   ├── paginas.py      monta o site estático a partir de site_modelo/
│   ├── ramos.py        páginas por ramo de negócio
│   ├── letras.py       uma página por domínio .com.br de uma letra
│   ├── graficos.py     os gráficos dos textos, em SVG estático
│   ├── consultas.py    traduz os filtros da tela em SQL
│   └── servidor.py     transporte HTTP da interface local
└── contexto.py     ponto de composição: liga tudo, sabe onde ficam os arquivos
```

- `dominio/` não importa nada das outras camadas: a lógica é testável sem
  rede e sem banco.
- `casos/` recebe os adaptadores por parâmetro, e os testes injetam falsos.
- Os scripts na raiz são entradas finas (argparse e impressão).
- `web/` na raiz tem a interface local (`index.html`, `app.js`,
  `style.css`) e dois módulos que o site também usa: `disputa.js` (quem
  disputa um nome) e `pontocom.js` (o `.com` do mesmo nome).
- `site_modelo/` tem a casca do site (`layout.html`), o estilo, os scripts
  do navegador (`lista/*.js`, os módulos ES da página inicial; `ficha.js`,
  `agenda.js`...) e as páginas da ferramenta em `conteudo/`.

## Testes

```bash
python3 -m pytest test_scripts.py -q     # se tiver o pytest
python3 -m unittest test_scripts.py      # só biblioteca padrão
```

Nenhum teste toca a rede: os adaptadores entram por parâmetro e a suíte usa
clientes falsos e um SQLite temporário. As respostas reais do Registro.br
ficam gravadas nos testes, para que uma mudança de formato do endpoint
quebre a suíte em vez de passar despercebida. O CI roda em Python 3.10 e
3.13 a cada push e pull request.

## Respeito aos limites do Registro.br

O endpoint de disponibilidade é público, mas **não é uma API documentada** —
é o que a caixa de busca do site usa — e o Registro.br limita consultas por
IP. O motor consulta devagar, uma de cada vez:

- pausa mínima de **2 segundos** entre consultas, rígida na varredura;
- **uma conexão só**, sem paralelismo;
- em bloqueio, recua 120 s e tenta o mesmo nome uma vez; se continuar
  bloqueado, ou depois de 10 erros seguidos, para;
- **filtre antes**: nunca rode a lista inteira.

Medido em 09/09/2026: a 2 s numa conexão (0,46 req/s) a consulta passa
limpa; com três conexões a 1 s (2,7 req/s) o Registro.br bloqueia o IP
inteiro, inclusive o navegador da mesma máquina. E o bloqueio volta como
**HTTP 200 com `status: 8`**, que um mapeamento ingênuo confunde com leilão.
O catálogo completo, numerado e datado, está em
[`docs/limitacoes-registrobr.md`](docs/limitacoes-registrobr.md).

## Documentação

- [Processo de liberação](docs/processo-de-liberacao.md) — as regras da rodada mensal, o candidato invisível, o limite de candidaturas, o que a especificação EPP conta
- [Processo competitivo](docs/processo-competitivo.md) — como funciona o leilão, incrementos, prorrogações e quanto costuma custar
- [O endpoint do Registro.br](docs/api-registrobr.md) — o JSON, a enumeração de `status`, as três listas, o ISAVAIL, a extensão NIC.br do RDAP
- [Limitações do Registro.br](docs/limitacoes-registrobr.md) — catálogo numerado e datado de cada limitação medida da API, do RDAP, das listas e do painel
- [Fontes oficiais do Registro.br](docs/fontes-oficiais-registrobr.md) — o GitHub e o FTP do Registro.br: o que existe, o que serve e o que não serve

## Contribuindo

Contribuições são bem-vindas, em especial: ampliar o filtro de marcas,
corrigir o mapeamento de `status` se o Registro.br mudar o formato, e
qualquer medição nova do comportamento do Registro.br, com data e comando
para reproduzir. Leia o [CONTRIBUTING.md](CONTRIBUTING.md) antes. Em
resumo: só biblioteca padrão, nenhum teste toca a rede, e o piso de 2
segundos entre consultas não se negocia.

Falhas de segurança: veja o [SECURITY.md](SECURITY.md). A convivência
segue o [código de conduta](CODE_OF_CONDUCT.md).

## Aviso

Este projeto **não tem vínculo com o Registro.br, o NIC.br nem o CGI.br**. O
formato do endpoint pode mudar sem aviso. Nada aqui é aconselhamento
jurídico; antes de investir num nome, confira marcas registradas em
[busca.inpi.gov.br](https://busca.inpi.gov.br/pePI/).

## Licença

[MIT](LICENSE).
