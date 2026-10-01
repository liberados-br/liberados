# Como contribuir

Obrigado pelo interesse. Este projeto é pequeno e tem opinião própria sobre
algumas coisas; este arquivo existe para você saber quais antes de gastar
tempo.

## O básico

```bash
git clone https://github.com/liberados-br/liberados.git
cd liberados
python3 -m unittest -v test_scripts.py
```

Python 3.10 ou mais novo. **Não há o que instalar** — e é assim de propósito.

## As quatro regras que não mudam

### 1. Só biblioteca padrão

Nada de `pip install`. O projeto inteiro roda com o Python que já veio na
máquina: quem quer procurar domínio não deveria precisar aprender ambiente
virtual antes.

Se algo parece exigir uma dependência, quase sempre dá para resolver com
`urllib`, `sqlite3`, `json`, `csv` ou `socket`. O cliente RDAP e o teste de
resolução de DNS em `garimpo/adaptadores/rdap.py`, e o cliente UDP do
ISAVAIL em `garimpo/adaptadores/isavail.py`, são exemplos disso.

### 2. Nenhum teste toca a rede

A suíte roda em segundos e funciona sem internet. Adaptadores entram nos
casos de uso **por parâmetro**, e os testes injetam falsos — veja
`ClienteFalso` e `ClienteRdapFalso` em `test_scripts.py`.

Isso é verificado no CI. Um teste que chama a rede de verdade reprova.

Precisa testar código que fala HTTP? Separe a parte que decide da parte que
busca: `rdap.interpretar()` recebe o JSON já baixado justamente para poder
ser testado sem rede.

### 3. O piso de 2 segundos entre consultas é rígido

Medido em 09/09/2026 contra o Registro.br:

| Ritmo | Taxa | Resultado |
|---|---|---|
| 2s, uma conexão | 0,46 req/s | limpo |
| 1s, uma conexão | 0,87 req/s | começam falhas |
| 1s, três conexões | 2,7 req/s | **bloqueia** |

A 2,7 req/s o navegador da mesma máquina também passou a ser bloqueado: o
limite é por IP e afeta terceiros, inclusive quando o código roda num
runner de CI com IP compartilhado.

**Não aceitamos PR que diminua a pausa, paralelize consultas ou contorne o
limite.** A lista muda uma vez por mês; não há motivo para apressar. O
catálogo do que já foi medido está em
[`docs/limitacoes-registrobr.md`](docs/limitacoes-registrobr.md).

### 4. Dado publicado é só dado de domínio

O site gerado (`site/`) não leva ticket de ninguém, oferta nem anotação
pessoal. Só o que já é público no Registro.br. A única coisa parecida com
ticket que atravessa é o **ritmo da rodada** (`ritmo` no JSON): uma série
de pares "instante, maior número de ticket já visto", que é o contador
global de tickets do Registro.br, não a candidatura de alguém. Por nome, o
site recebe só a estimativa de quando os concorrentes chegaram, nunca os
números.

Consulta que devolve dado de pessoa (o `?ticket=` do RDAP, o titular de um
domínio) só acontece no navegador de quem olha, sob demanda, e nunca na
varredura nem no exportador. Veja o [`SECURITY.md`](SECURITY.md).

## Arquitetura

Dependência sempre para dentro:

```
dominio/      regras puras, sem I/O — situacao, relevancia, marcas, frescor, calendario
adaptadores/  a fronteira — registrobr, rdap, isavail, wayback, repositorio, dicionarios
casos/        orquestração — pool, varredura, instantaneo, historias, lembretes
web/          páginas do site, consultas em SQL e o servidor HTTP local
contexto.py   ponto de composição: o único que sabe onde ficam os arquivos
```

Regra prática: se um arquivo em `dominio/` precisou importar algo de
`adaptadores/`, a modelagem está errada.

Os scripts na raiz são entradas finas — argparse e impressão, nada de lógica.

## Estilo

- Código, comentários e documentação em **português**.
- Comentário explica **por que**, não o que. Um bom exemplo: o bloqueio por
  excesso de consultas volta com HTTP 200 e `status: 8`, que na tabela
  oficial é "erro", mas que uma leitura do status como bitmask confunde com
  leilão — e transforma cada resposta bloqueada num falso "domínio em
  leilão". O comentário que diz isso evita que alguém "simplifique" a
  checagem.
- Quando corrigir um engano, registre o que provou o contrário (a fonte
  oficial, a medição), não a história de quem errou.

## Números precisam de procedência

Este projeto faz afirmações verificáveis sobre um serviço real. Se você
acrescentar um número:

- diga **de onde veio** e **quando foi medido**;
- prefira o que dá para reproduzir com um comando do próprio repositório;
- separe o que foi medido do que foi inferido.

Achado novo sobre o Registro.br entra no catálogo
[`docs/limitacoes-registrobr.md`](docs/limitacoes-registrobr.md), com o
código seguinte da tabela certa; o fim daquele arquivo diz como.

## Abrindo um PR

1. Rode `python3 -m unittest -v test_scripts.py`.
2. Descreva o **problema**, não só a mudança.
3. Mexeu em regra do Registro.br? Cite a página oficial e a data da consulta —
   as regras mudam e a documentação deles nem sempre acompanha.

Achou um erro nos dados ou na interpretação das regras? Abra uma issue.
