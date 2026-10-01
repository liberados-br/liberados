# Segurança

## Reportar uma vulnerabilidade

Use o **Private vulnerability reporting** do GitHub neste repositório:
<https://github.com/liberados-br/liberados/security/advisories/new>.
Não abra issue pública para falha de segurança.

## O que este projeto trata como dado sensível

Este é um projeto sobre dados públicos de domínios. A varredura, o
exportador e o servidor local **nunca** consultam nem armazenam dado
pessoal. O navegador de quem visita o site faz, sob demanda, três tipos de
consulta, cada uma só ao abrir a página ou o botão que a usa, com a cota de
IP daquele visitante. O resultado fica só naquele aparelho: não volta para
o servidor, porque dado vindo de navegador alheio pode ser forjado.

- **Quem disputa** (`web/disputa.js`, ao tocar na contagem de "Competindo"
  na lista ou no botão "Ver quem disputa" da ficha de um nome):
  `rdap.registro.br/domain/<nome>?ticket=<n>` devolve nome e documento
  mascarado de quem se candidatou, o mesmo que a busca do Registro.br mostra
  a qualquer pessoa. Mostra nome, tipo e número mascarado do documento,
  instante do pedido e, para empresas, quantos domínios têm.
- **Titular de um domínio registrado** (`site_modelo/ficha.js`,
  `site_modelo/letra.js`): RDAP do próprio `.br`, mostra só o nome do
  titular (o `fn` do `registrant`).
- **Mostrar o .com** (`web/pontocom.js`): RDAP da Verisign e do
  registrador, mostra `fn`, organização e o código do país do titular do
  `.com`, como o WHOIS publica.

Em nenhuma delas saem documento (fora o mascarado de *quem disputa*),
endereço, e-mail, telefone nem `legalRepresentative`. O feed autenticado do
painel do Registro.br traz o documento do titular; nada aqui se conecta a
ele, e nada aqui envia oferta de leilão.

## Limites de consulta

O Registro.br limita por IP e o limite vale para a máquina inteira, não só
para o script. Pull request que acelere a varredura além de uma conexão a
cada 2 segundos não será aceito. Detalhes medidos em
[`docs/limitacoes-registrobr.md`](docs/limitacoes-registrobr.md).
