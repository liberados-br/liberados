#!/usr/bin/env python3
"""
Testes das funcoes puras e dos casos de uso (sem rede, sem banco de verdade).

    python3 -m unittest -v test_scripts.py

Os payloads sao respostas reais do endpoint de disponibilidade, capturadas em
09/09/2026. Se o Registro.br mudar o formato, estes testes quebram e voce sabe
que precisa reajustar garimpo/dominio/situacao.py.

O teste mais importante do arquivo e TestBloqueioDisfarcado: o bloqueio por
excesso de consultas volta com HTTP 200 e status 8, que e o mesmo bit de
processo competitivo, e ja transformou 370 respostas bloqueadas em falsos
"dominios em leilao".
"""

import datetime
import re
import subprocess
import json
import os
import tempfile
import unittest
import unittest.mock

from filtrar_lista import separar as separar_antigo
from filtrar_lista import sem_acento
from garimpo.adaptadores.repositorio import Candidato, Repositorio
from garimpo.adaptadores import isavail, rdap, wayback
from garimpo.casos import historias, instantaneo, pool
from garimpo.dominio import extensoes
from garimpo.casos.varredura import Varredura
from garimpo.dominio.marcas import Risco, avaliar
from garimpo.dominio.relevancia import pontuar, separar
from garimpo.dominio.situacao import Leitura, Situacao, classificar


# --------------------------------------------------------------------------
# respostas reais, encurtadas
# --------------------------------------------------------------------------
LIVRE = {"status": 0, "fqdn": "zzzzqwertyx9.com.br"}
REGISTRADO = {"status": 2, "fqdn": "google.com.br",
              "publication-status": "published",
              "expires-at": "2027-05-18T00:00:00-03:00"}
LIBERACAO_SEM_CANDIDATO = {"status": 6, "fqdn": "anabolizantes.com.br",
                           "ends-at": "2026-09-16T15:00:00-03:00"}
LIBERACAO_DISPUTADA = {"status": 7, "fqdn": "medidas.com.br",
                       "tickets": [30001101, 30001102],
                       "ends-at": "2026-09-16T15:00:00-03:00"}
COMPETITIVO = {"status": 9, "fqdn": "combustiveis.com.br",
               "tickets": [30002201, 30002202],
               "ends-at": "2026-09-16T15:00:00-03:00",
               "accepting-new-tickets-until": "2026-09-16T15:00:00-03:00"}

# O bloqueio: HTTP 200, status 8, fqdn vazio.
LIMITE_EXCEDIDO = {"status": 8, "fqdn": "", "fqdnace": "", "exempt": False,
                   "reasons": ["Taxa máxima de consultas excedida"]}
LIMITE_SEM_MOTIVO = {"status": 8, "fqdn": "", "fqdnace": "", "exempt": False}


class TestClassificar(unittest.TestCase):
    def test_livre(self):
        self.assertIs(classificar(LIVRE).situacao, Situacao.LIVRE)

    def test_registrado(self):
        leitura = classificar(REGISTRADO)
        self.assertIs(leitura.situacao, Situacao.REGISTRADO)
        self.assertEqual(leitura.candidatos, 0)
        self.assertIn("expires-at", leitura.detalhe)

    def test_liberacao_sem_candidato(self):
        """O alvo do garimpo: pode sair pela anuidade normal."""
        leitura = classificar(LIBERACAO_SEM_CANDIDATO)
        self.assertIs(leitura.situacao, Situacao.LIBERACAO_LIVRE)
        self.assertEqual(leitura.candidatos, 0)

    def test_liberacao_disputada(self):
        leitura = classificar(LIBERACAO_DISPUTADA)
        self.assertIs(leitura.situacao, Situacao.LIBERACAO_DISPUTADA)
        self.assertEqual(leitura.candidatos, 2)

    def test_competitivo(self):
        leitura = classificar(COMPETITIVO)
        self.assertIs(leitura.situacao, Situacao.COMPETITIVO)
        self.assertEqual(leitura.candidatos, 2)
        self.assertIn("accepting-new-tickets-until", leitura.detalhe)

    def test_resposta_invalida(self):
        self.assertIs(classificar("nao e json").situacao, Situacao.ERRO)
        self.assertIs(classificar({"status": "x"}).situacao,
                      Situacao.INDESCONHECIDO)


class TestBloqueioDisfarcado(unittest.TestCase):
    """Regressao do bug que rotulou 370 dominios como leilao."""

    def test_limite_excedido_nao_e_leilao(self):
        leitura = classificar(LIMITE_EXCEDIDO)
        self.assertIs(leitura.situacao, Situacao.LIMITADO)
        self.assertIsNot(leitura.situacao, Situacao.COMPETITIVO)
        self.assertTrue(leitura.limitado)
        self.assertEqual(leitura.candidatos, 0)
        self.assertIn("Taxa", leitura.detalhe)

    def test_limite_sem_campo_reasons(self):
        """Rede de seguranca: sem fqdn e status 8 tambem e bloqueio."""
        self.assertIs(classificar(LIMITE_SEM_MOTIVO).situacao, Situacao.LIMITADO)

    def test_leilao_de_verdade_tem_fqdn_e_prazo(self):
        """O que separa leilao real de bloqueio."""
        leitura = classificar(COMPETITIVO)
        self.assertTrue(COMPETITIVO["fqdn"])
        self.assertIn("ends-at", leitura.detalhe)

    def test_bloqueio_nao_e_situacao_resolvida(self):
        """Bloqueio nao pode ser gravado como se fosse fato."""
        self.assertFalse(classificar(LIMITE_EXCEDIDO).situacao.resolvida)
        self.assertTrue(classificar(COMPETITIVO).situacao.resolvida)


class TestCandidatoOculto(unittest.TestCase):
    """
    O Registro.br documenta que so informa tickets com mais de um candidato,
    entao LIBERACAO_LIVRE quer dizer "zero ou um".
    """

    def test_um_candidato_e_indistinguivel_de_zero(self):
        # nao existe payload com exatamente 1 ticket: o endpoint omite
        um_candidato_como_o_endpoint_devolve = LIBERACAO_SEM_CANDIDATO
        leitura = classificar(um_candidato_como_o_endpoint_devolve)
        self.assertEqual(leitura.candidatos, 0)
        self.assertIs(leitura.situacao, Situacao.LIBERACAO_LIVRE)

    def test_dois_candidatos_ja_aparecem(self):
        self.assertEqual(classificar(LIBERACAO_DISPUTADA).candidatos, 2)


class TestRelevancia(unittest.TestCase):
    PT = {"vaca", "muro", "botica"}
    EN = {"farm", "hunt"}

    def test_curto_em_portugues_pontua_mais_que_longo(self):
        curto = pontuar("vaca.com.br", self.PT, self.EN)
        longo = pontuar("umnomemuitocompridoassim.com.br", self.PT, self.EN)
        self.assertGreater(curto.valor, longo.valor)

    def test_portugues_vale_mais_que_ingles(self):
        pt = pontuar("muro.com.br", self.PT, self.EN)
        en = pontuar("farm.com.br", self.PT, self.EN)
        self.assertGreater(pt.valor, en.valor)

    def test_elegivel_ao_leilao_soma(self):
        sem = pontuar("botica.com.br", self.PT, self.EN)
        com = pontuar("botica.com.br", self.PT, self.EN, elegivel=True)
        self.assertGreater(com.valor, sem.valor)
        self.assertIn("elegível ao leilão", com.motivos)

    def test_nicho_comercial_e_reconhecido(self):
        nota = pontuar("creditoimobiliario.com.br", self.PT, self.EN)
        self.assertTrue(any("nicho" in m for m in nota.motivos))

    def test_nota_fica_entre_0_e_100(self):
        for nome in ("a.com.br", "vaca.com.br", "xxx.com.br", "naoalfanumerico1.com.br"):
            self.assertTrue(0 <= pontuar(nome, self.PT, self.EN).valor <= 100)

    def test_separar(self):
        self.assertEqual(separar("combustiveis.com.br"), ("combustiveis", "com.br"))
        self.assertEqual(separar("doc.app.br"), ("doc", "app.br"))
        self.assertEqual(separar("semponto"), (None, None))


class TestMarcas(unittest.TestCase):
    def test_generico_e_ok(self):
        self.assertEqual(avaliar("remedio").risco, Risco.OK)
        self.assertEqual(avaliar("moradias").risco, Risco.OK)

    def test_marca_conhecida(self):
        self.assertEqual(avaliar("netflix").risco, Risco.RISCO)

    def test_typosquat(self):
        self.assertEqual(avaliar("googles").risco, Risco.RISCO)
        self.assertEqual(avaliar("airnb").risco, Risco.RISCO)

    def test_marca_embutida(self):
        avaliacao = avaliar("lojanike")
        self.assertEqual(avaliacao.risco, Risco.ATENCAO)
        self.assertIn("nike", avaliacao.motivo)

    def test_sigla_curta(self):
        self.assertEqual(avaliar("xyz").risco, Risco.ATENCAO)

    def test_desempacota_como_tupla(self):
        """O codigo antigo faz `nivel, motivo = avaliar(x)`."""
        nivel, motivo = avaliar("netflix")
        self.assertEqual(nivel, "RISCO")
        self.assertIn("netflix", motivo)

    def test_marcas_compostas_do_top10_de_setembro(self):
        """brasiltelecom e brfoods abriram o ranking de /dados/ como OK
        (S18): a lista fixa
        tinha "oi" e "brf", nao os nomes compostos que o Registro.br mostra."""
        for nome in ("brasiltelecom", "brfoods", "brasilfoods"):
            self.assertEqual(avaliar(nome).risco, Risco.RISCO, nome)

    def test_farm_e_marca_conhecida(self):
        """FARM Rio: ~130 lojas, grife da Azzas 2154 (maior grupo de moda
        da America Latina), estava no top 10 de setembro como OK."""
        self.assertEqual(avaliar("farm").risco, Risco.RISCO)

    def test_marca_curta_ainda_pega_substring_generica(self):
        """farm entrar na lista fixa tem o mesmo efeito colateral que azul
        (ja aceito: azulejo vira ATENCAO por causa da Azul aerea) -- nao e
        regressao nova, e o MINIMO_SUBSTRING de sempre."""
        self.assertEqual(avaliar("azulejo").risco, Risco.ATENCAO)
        self.assertEqual(avaliar("farmacia").risco, Risco.ATENCAO)

    def test_parece_marca_e_so_o_reconhecimento_por_nome(self):
        """parece_marca() reavalia na hora de montar a pagina (paginas.py),
        sem o instantaneo: por isso nao tem sigla curta (nao e sobre marca)
        nem site popular (pede o Tranco, que a pagina nao tem)."""
        from garimpo.dominio.marcas import parece_marca, parece_marca_dominio
        self.assertTrue(parece_marca("brasiltelecom"))
        self.assertTrue(parece_marca("lojanike"))          # marca embutida
        self.assertTrue(parece_marca("googles"))            # typosquat
        self.assertFalse(parece_marca("xyz"))               # sigla curta: nao conta aqui
        self.assertFalse(parece_marca("remedio"))
        self.assertTrue(parece_marca_dominio("brasiltelecom.com.br"))


class TestInstantaneo(unittest.TestCase):
    def _amostra(self):
        return [
            Candidato("muro.com.br", elegivel=True, em_leilao=True, nota=100,
                      motivos=("nome curto", "palavra em portugues"),
                      situacao=Situacao.COMPETITIVO, candidatos=4),
            Candidato("botica.com.br", nota=75, motivos=("nome curto",),
                      situacao=Situacao.LIBERACAO_LIVRE, candidatos=0),
            Candidato("bloqueado.com.br", nota=50,
                      situacao=Situacao.LIMITADO, candidatos=0),
        ]

    def test_ida_e_volta_preserva_o_essencial(self):
        meta = instantaneo.Metadados(gerado_em="2026-09-09T21:00:00-03:00")
        dados = instantaneo.exportar(self._amostra(), meta)
        voltou = {c.dominio: c for c in instantaneo.candidatos_de(dados)}

        self.assertIn("muro.com.br", voltou)
        muro = voltou["muro.com.br"]
        self.assertIs(muro.situacao, Situacao.COMPETITIVO)
        self.assertEqual(muro.candidatos, 4)
        self.assertEqual(muro.nota, 100)
        self.assertTrue(muro.elegivel)
        self.assertIn("nome curto", muro.motivos)

    def test_bloqueado_fica_de_fora(self):
        """LIMITADO nao e fato: sai do JSON para ser reconsultado depois."""
        dados = instantaneo.exportar(
            self._amostra(), instantaneo.Metadados(gerado_em="agora"))
        self.assertNotIn("bloqueado.com.br",
                         [i[0] for i in dados["itens"]])


class TestRepositorio(unittest.TestCase):
    def setUp(self):
        self.arquivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.arquivo.close()
        self.repo = Repositorio(self.arquivo.name)

    def tearDown(self):
        self.repo.fechar()
        os.unlink(self.arquivo.name)

    def test_gravar_pool_nao_apaga_resultado(self):
        c = Candidato("teste.com.br", nota=50)
        self.repo.gravar_pool([c])
        self.repo.gravar_leitura("teste.com.br",
                                 Leitura(Situacao.LIBERACAO_LIVRE, 0, "x", 6))

        # remontar o pool nao pode perder o que ja foi verificado
        self.repo.gravar_pool([Candidato("teste.com.br", nota=70)])
        guardado = self.repo.um("teste.com.br")
        self.assertIs(guardado.situacao, Situacao.LIBERACAO_LIVRE)
        self.assertEqual(guardado.nota, 70)

    def test_restaurar_insere_quem_saiu_do_pool(self):
        """
        Regressao: com UPDATE puro, uma execucao devolveu 818 tendo 859.
        Dominio que saiu da lista oficial nao pode sumir do historico.
        """
        historico = [Candidato("sumiu.com.br", nota=60,
                               situacao=Situacao.LIBERACAO_LIVRE, candidatos=0)]
        self.repo.gravar_pool([Candidato("outro.com.br", nota=10)])

        restaurados = self.repo.restaurar(historico)
        self.assertEqual(restaurados, 1)
        self.assertIsNotNone(self.repo.um("sumiu.com.br"))

    def test_restaurar_nao_sobrescreve_o_mais_novo(self):
        self.repo.gravar_pool([Candidato("x.com.br", nota=50)])
        self.repo.gravar_leitura("x.com.br",
                                 Leitura(Situacao.COMPETITIVO, 5, "novo", 9))
        self.repo.restaurar([Candidato("x.com.br", nota=50,
                                       situacao=Situacao.LIBERACAO_LIVRE,
                                       candidatos=0)])
        self.assertIs(self.repo.um("x.com.br").situacao, Situacao.COMPETITIVO)

    def _restaurar_duas(self, primeiro, segundo):
        self.repo.gravar_pool([Candidato("x.com.br", nota=50)])
        for quando, situacao in (primeiro, segundo):
            self.repo.restaurar([Candidato("x.com.br", nota=50,
                                           situacao=situacao, candidatos=1,
                                           verificado_em=quando)])
        return self.repo.um("x.com.br")

    def test_restaurar_fica_a_leitura_mais_nova(self):
        """Regressao: com COALESCE o banco vencia sempre."""
        t1 = ("2026-10-15T10:00:00-03:00", Situacao.LIBERACAO_LIVRE)
        t2 = ("2026-10-15T12:00:00-03:00", Situacao.LIBERACAO_DISPUTADA)
        self.assertIs(self._restaurar_duas(t1, t2).situacao,
                      Situacao.LIBERACAO_DISPUTADA)

    def test_restaurar_nao_troca_por_leitura_mais_velha(self):
        t1 = ("2026-10-15T12:00:00-03:00", Situacao.LIBERACAO_DISPUTADA)
        t0 = ("2026-10-15T10:00:00-03:00", Situacao.LIBERACAO_LIVRE)
        guardado = self._restaurar_duas(t1, t0)
        self.assertIs(guardado.situacao, Situacao.LIBERACAO_DISPUTADA)
        self.assertEqual(guardado.verificado_em, t1[0])

    def test_restaurar_compara_fusos_em_segundos(self):
        """22:00-03:00 e 01:00 UTC: vence 00:30+00:00, embora o texto diga o contrario."""
        casa = ("2026-10-14T22:00:00-03:00", Situacao.LIBERACAO_DISPUTADA)
        runner = ("2026-10-15T00:30:00+00:00", Situacao.LIBERACAO_LIVRE)
        for ordem in ((casa, runner), (runner, casa)):
            self.repo.esquecer_leituras()
            self.assertIs(self._restaurar_duas(*ordem).situacao,
                          Situacao.LIBERACAO_DISPUTADA, ordem)

    def test_restaurar_mais_novo_leva_junto_os_tickets_velhos(self):
        """Tickets da leitura vencida refariam a chegada por cima da do JSON."""
        self.repo.gravar_pool([Candidato("x.com.br", nota=50)])
        self.repo.gravar_leitura("x.com.br", Leitura(
            Situacao.LIBERACAO_DISPUTADA, 1, "velho", 6, tickets=(1000,)))
        self.repo.restaurar([Candidato("x.com.br", nota=50,
                                       situacao=Situacao.LIBERACAO_DISPUTADA,
                                       candidatos=2, chegada_min=1790000000,
                                       verificado_em="2099-01-01T00:00:00+00:00")])
        guardado = self.repo.um("x.com.br")
        self.assertEqual(guardado.candidatos, 2)
        self.assertIsNone(guardado.ticket_min)
        self.assertEqual(guardado.chegada_min, 1790000000)

    def test_limpar_contaminados(self):
        self.repo.gravar_pool([Candidato("falso.com.br"),
                               Candidato("real.com.br")])
        # leilao sem ends-at: era bloqueio classificado errado
        self.repo.gravar_leitura("falso.com.br",
                                 Leitura(Situacao.COMPETITIVO, 0, "", 8))
        self.repo.gravar_leitura("real.com.br",
                                 Leitura(Situacao.COMPETITIVO, 3,
                                         "ends-at=2026-09-16", 9))
        self.assertEqual(self.repo.limpar_contaminados(), 1)
        self.assertIsNone(self.repo.um("falso.com.br").situacao)
        self.assertIs(self.repo.um("real.com.br").situacao, Situacao.COMPETITIVO)


def _fonte_da_lista() -> str:
    """
    O texto dos modulos da pagina inicial (site_modelo/lista/*.js), juntos.
    Os testes acham cada funcao pelo nome, em qualquer modulo: o export fica
    numa lista no fim de cada arquivo, entao a declaracao continua comecando
    em "function nome(".
    """
    pasta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_modelo", "lista")
    # na ordem de leitura (a do antigo app.js unico): testes recortam trechos
    # entre duas funcoes vizinhas; modulo novo entra no fim
    ordem = ("estado util busca filtros linha tabela resumo fase relogio painel porpagina "
             "csv conferencia acompanhados contagem compartilhada endereco rodada "
             "seletores eventos app").split()
    nomes = [n[:-3] for n in os.listdir(pasta) if n.endswith(".js")]
    nomes.sort(key=lambda n: (ordem.index(n) if n in ordem else len(ordem), n))
    return "\n".join(open(os.path.join(pasta, f"{n}.js"), encoding="utf-8").read() for n in nomes)


class ClienteFalso:
    """Substitui o Registro.br nos testes de varredura."""

    def __init__(self, respostas):
        self.respostas = respostas
        self.consultados = []

    def verificar(self, dominio):
        self.consultados.append(dominio)
        resposta = self.respostas.get(dominio,
                                      Leitura(Situacao.LIBERACAO_LIVRE, 0, "", 6))
        # lista: uma resposta por consulta, e a ultima se repete
        if isinstance(resposta, list):
            return resposta.pop(0) if len(resposta) > 1 else resposta[0]
        return resposta


class TestVarredura(unittest.TestCase):
    def setUp(self):
        self.arquivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.arquivo.close()
        self.repo = Repositorio(self.arquivo.name)
        self.repo.gravar_pool([Candidato("a.com.br"), Candidato("b.com.br")])

    def tearDown(self):
        self.repo.fechar()
        os.unlink(self.arquivo.name)

    def test_grava_e_conta(self):
        cliente = ClienteFalso({})
        v = Varredura(cliente, self.repo)
        progresso = v.executar(["a.com.br", "b.com.br"], pausa=0)
        self.assertEqual(progresso.feitos, 2)
        self.assertEqual(progresso.erros, 0)
        self.assertIs(self.repo.um("a.com.br").situacao, Situacao.LIBERACAO_LIVRE)

    def test_erro_nao_vira_resultado(self):
        """Falha deixa o dominio NULL, para ser reconsultado depois."""
        cliente = ClienteFalso({"a.com.br": Leitura(Situacao.ERRO, 0, "rede")})
        v = Varredura(cliente, self.repo)
        progresso = v.executar(["a.com.br"], pausa=0)
        self.assertEqual(progresso.erros, 1)
        self.assertIsNone(self.repo.um("a.com.br").situacao)

    def test_detecta_mudanca(self):
        self.repo.gravar_leitura("a.com.br",
                                 Leitura(Situacao.LIBERACAO_LIVRE, 0, "", 6))
        cliente = ClienteFalso({
            "a.com.br": Leitura(Situacao.COMPETITIVO, 2, "ends-at=x", 9)})
        v = Varredura(cliente, self.repo)
        progresso = v.executar(["a.com.br"], pausa=0)
        self.assertEqual(len(progresso.mudancas), 1)
        self.assertIs(progresso.mudancas[0].para, Situacao.COMPETITIVO)

    def _pausas(self, v):
        pausas = []
        v._pausar = lambda s: pausas.append(s) or True
        return pausas

    def test_bloqueio_que_persiste_encerra_a_varredura(self):
        """LIMITADO depois do recuo para tudo, sem ir ao proximo."""
        from garimpo.adaptadores.registrobr import PAUSA_SEGURA
        from garimpo.casos import varredura as mod
        limitado = Leitura(Situacao.LIMITADO, 0, "", None)
        cliente = ClienteFalso({"a.com.br": [limitado, limitado]})
        v = Varredura(cliente, self.repo)
        pausas = self._pausas(v)
        p = v.executar(["a.com.br", "b.com.br"], pausa=0)
        self.assertEqual(cliente.consultados, ["a.com.br", "a.com.br"])
        self.assertTrue(p.barrada)
        self.assertEqual(p.motivo, "bloqueio")
        self.assertEqual(p.bloqueios, 1)
        self.assertEqual(mod.RECUO_APOS_BLOQUEIO, 120)
        self.assertEqual(pausas, [120])
        self.assertIn("barrada", p.mensagem)
        self.assertTrue(v.instantaneo()["barrada"])
        self.assertEqual(p.pausa, PAUSA_SEGURA)

    def test_bloqueio_que_passa_no_recuo_segue(self):
        from garimpo.adaptadores.registrobr import PAUSA_SEGURA
        limitado = Leitura(Situacao.LIMITADO, 0, "", None)
        boa = Leitura(Situacao.LIBERACAO_LIVRE, 0, "", 6)
        cliente = ClienteFalso({"a.com.br": [limitado, boa]})
        v = Varredura(cliente, self.repo)
        pausas = self._pausas(v)
        p = v.executar(["a.com.br", "b.com.br"], pausa=0)
        self.assertEqual(cliente.consultados, ["a.com.br", "a.com.br", "b.com.br"])
        self.assertFalse(p.barrada)
        self.assertIsNone(p.motivo)
        self.assertEqual(p.erros, 0)
        # nenhuma pausa abaixo do piso, mesmo pedindo 0
        self.assertEqual(min(pausas), PAUSA_SEGURA)

    def test_dez_erros_seguidos_encerram_com_motivo_rede(self):
        from garimpo.casos.varredura import ERROS_SEGUIDOS_PARA_PARAR
        self.assertEqual(ERROS_SEGUIDOS_PARA_PARAR, 10)
        nomes = [f"n{i:02d}.com.br" for i in range(12)]
        self.repo.gravar_pool([Candidato(n) for n in nomes])
        erro = Leitura(Situacao.ERRO, 0, "rede")
        cliente = ClienteFalso({n: erro for n in nomes})
        v = Varredura(cliente, self.repo)
        self._pausas(v)
        p = v.executar(nomes, pausa=0)
        self.assertEqual(len(cliente.consultados), 10)
        self.assertTrue(p.barrada)
        self.assertEqual(p.motivo, "rede")
        self.assertEqual(p.erros, 10)

    def test_nove_erros_e_uma_leitura_boa_seguem(self):
        nomes = [f"n{i:02d}.com.br" for i in range(20)]
        self.repo.gravar_pool([Candidato(n) for n in nomes])
        erro = Leitura(Situacao.ERRO, 0, "rede")
        # 9 erros, uma boa, mais 9 erros e uma boa: o contador zera
        respostas = {n: erro for i, n in enumerate(nomes) if i % 10 != 9}
        cliente = ClienteFalso(respostas)
        v = Varredura(cliente, self.repo)
        self._pausas(v)
        p = v.executar(nomes, pausa=0)
        self.assertEqual(cliente.consultados, nomes)
        self.assertFalse(p.barrada)
        self.assertEqual(p.erros, 18)

    def test_parada_interrompe(self):
        cliente = ClienteFalso({})
        v = Varredura(cliente, self.repo)
        v.pedir_parada()
        progresso = v.executar(["a.com.br", "b.com.br"], pausa=0)
        self.assertEqual(progresso.feitos, 0)
        self.assertIn("interrompido", progresso.mensagem)


class TestPool(unittest.TestCase):
    def test_elegivel_entra_mesmo_com_nota_baixa(self):
        from garimpo.adaptadores.registrobr import Rodada
        rodada = Rodada(liberacao=["xyzabcdefghij.com.br"],
                        elegiveis={"qq.com.br"}, em_leilao=set())
        candidatos, resumo = pool.montar(rodada, (set(), set()),
                                         nota_minima=90)
        nomes = {c.dominio for c in candidatos}
        self.assertIn("qq.com.br", nomes)
        self.assertEqual(resumo.elegiveis, 1)


class TestLeilaoAnunciado(unittest.TestCase):
    """
    Regressao: as 28 joias do site estavam todas em leilao.

    A lista oficial (lista-competicao.txt) ja dizia isso, e a coluna em_leilao
    do JSON tambem. Mas a situacao exibida era a da ultima consulta, de 30
    horas antes, e o filtro de joias nunca olhava a lista.
    """

    def setUp(self):
        self.arquivo = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.arquivo.close()
        self.repo = Repositorio(self.arquivo.name)

    def tearDown(self):
        self.repo.fechar()
        os.unlink(self.arquivo.name)

    def _joia_velha(self, dominio="exemplo.com.br", em_leilao=True):
        """Joia antiga: elegivel, lida sem candidato, ja na lista."""
        self.repo.gravar_pool([Candidato(dominio, elegivel=True,
                                         em_leilao=em_leilao, nota=43)])
        self.repo.gravar_leitura(dominio,
                                 Leitura(Situacao.LIBERACAO_LIVRE, 0, "", 6))

    def test_lista_manda_sobre_leitura_de_liberacao(self):
        from garimpo.dominio.situacao import com_leilao_anunciado
        for antes in (Situacao.LIBERACAO_LIVRE, Situacao.LIBERACAO_DISPUTADA):
            self.assertIs(com_leilao_anunciado(antes, True),
                          Situacao.COMPETITIVO)
            self.assertIs(com_leilao_anunciado(antes, False), antes)

    def test_lista_nao_mexe_em_quem_nao_esta_em_liberacao(self):
        """REGISTRADO e LIVRE ja sairam da rodada; a lista nao os ressuscita."""
        from garimpo.dominio.situacao import com_leilao_anunciado
        for s in (Situacao.REGISTRADO, Situacao.LIVRE, None):
            self.assertIs(com_leilao_anunciado(s, True), s)

    def test_nome_na_lista_nunca_e_joia(self):
        c = Candidato("exemplo.com.br", elegivel=True, em_leilao=True,
                      situacao=Situacao.LIBERACAO_LIVRE, candidatos=0)
        self.assertFalse(c.joia)
        self.assertIs(c.situacao_atual, Situacao.COMPETITIVO)

    def test_aplicar_grava_leilao_com_a_data_da_lista(self):
        self._joia_velha()
        self._joia_velha("custas.com.br", em_leilao=False)
        viraram = self.repo.aplicar_lista_de_leiloes("2026-09-10T22:30:00-03:00")

        self.assertEqual(viraram, 1)
        guardado = self.repo.um("exemplo.com.br")
        self.assertIs(guardado.situacao, Situacao.COMPETITIVO)
        self.assertEqual(guardado.verificado_em, "2026-09-10T22:30:00-03:00")
        # quem nao esta na lista continua como estava
        self.assertIs(self.repo.um("custas.com.br").situacao,
                      Situacao.LIBERACAO_LIVRE)

    def test_saiu_da_lista_volta_para_a_fila(self):
        """
        A lista oficial manda nos dois sentidos.

        Se so subisse, quem saia de lista-competicao.txt guardava
        "leilao aberto" ate ser reconsultado um a um. Com 16 mil nomes na
        fila isso leva dias, e a tela afirmaria leilao em nome ja resolvido
        — a armadilha das "joias falsas" pelo avesso.
        """
        self._joia_velha()
        self.repo.aplicar_lista_de_leiloes("2026-09-10T22:30:00-03:00")
        self.assertIs(self.repo.um("exemplo.com.br").situacao,
                      Situacao.COMPETITIVO)

        # a rodada acabou e o nome saiu da lista
        self.repo.con.execute("UPDATE dominios SET em_leilao=0")
        self.repo.con.commit()
        self.assertEqual(self.repo.encerrar_leiloes_fora_da_lista(), 1)

        guardado = self.repo.um("exemplo.com.br")
        self.assertIsNot(guardado.situacao, Situacao.COMPETITIVO)
        self.assertIsNone(guardado.verificado_em)
        self.assertIsNone(guardado.candidatos)
        # idempotente: nada sobrou para rebaixar
        self.assertEqual(self.repo.encerrar_leiloes_fora_da_lista(), 0)

    def test_quem_segue_na_lista_nao_e_rebaixado(self):
        """Leilao que atravessa a rodada continua aberto (groupon, 17/09)."""
        self._joia_velha()
        self.repo.aplicar_lista_de_leiloes("x")
        self.assertEqual(self.repo.encerrar_leiloes_fora_da_lista(), 0)
        self.assertIs(self.repo.um("exemplo.com.br").situacao,
                      Situacao.COMPETITIVO)

    def test_aplicar_nao_inventa_contagem(self):
        """A lista diz que ha leilao, nao quantos tickets."""
        self._joia_velha()
        self.repo.aplicar_lista_de_leiloes("x")
        self.assertEqual(self.repo.um("exemplo.com.br").candidatos, 0)

    def test_limpeza_de_contaminados_nao_apaga_o_que_veio_da_lista(self):
        """COMPETITIVO sem detalhe e tratado como bloqueio disfarcado."""
        self._joia_velha()
        self.repo.aplicar_lista_de_leiloes("x")
        self.assertEqual(self.repo.limpar_contaminados(), 0)
        self.assertIs(self.repo.um("exemplo.com.br").situacao,
                      Situacao.COMPETITIVO)

    def test_instantaneo_nao_publica_joia_que_esta_na_lista(self):
        c = Candidato("exemplo.com.br", elegivel=True, em_leilao=True,
                      situacao=Situacao.LIBERACAO_LIVRE, candidatos=0)
        dados = instantaneo.exportar([c], instantaneo.Metadados(gerado_em="x"))
        self.assertEqual(dados["status"][dados["itens"][0][1]], "COMPETITIVO")

    def test_filtros_da_tela_respeitam_a_lista(self):
        from garimpo.web.consultas import Consultas
        self._joia_velha()
        self._joia_velha("custas.com.br", em_leilao=False)
        consultas = Consultas(self.repo)

        joias = [c.dominio for c in consultas.listar(filtro="joias").itens]
        leilao = [c.dominio for c in consultas.listar(filtro="leilao").itens]
        self.assertEqual(joias, ["custas.com.br"])
        self.assertEqual(leilao, ["exemplo.com.br"])

    def test_le_a_data_do_cabecalho_da_lista(self):
        from garimpo.adaptadores.registrobr import gerado_em
        cabecalho = ("# Arquivo gerado em 2026-09-10T22:30:00-03:00\n"
                     "# Mais informações em https://registro.br/dominio/\n"
                     "\nexemplo.com.br\n")
        self.assertEqual(gerado_em(cabecalho), "2026-09-10T22:30:00-03:00")
        self.assertIsNone(gerado_em("exemplo.com.br\n"))


class _RespostaFalsa:
    """Imita o retorno de urllib.request.urlopen para testar baixar_rodada
    sem ir a rede: cabecalhos num dict e .read() devolvendo bytes."""

    def __init__(self, corpo: bytes, content_type="text/plain"):
        self._corpo = corpo
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._corpo


class TestValidarLista(unittest.TestCase):
    """
    Uma lista ausente no registro.br volta
    HTTP 200 com o HTML do site, nao um erro. Sem validar o corpo, isso
    viraria uma "lista" de dominios lida do HTML.
    """

    HTML = ("<!doctype html>\n<html><head><title>Registro.br</title></head>"
           "<body>pagina do site</body></html>")

    def _lista_boa(self, nomes=("a.com.br", "b.com.br")):
        linhas = ["# Processo de liberação no período de "
                 "2026-10-14T15:00:00-03:00 a 2026-10-21T15:00:00-03:00",
                 "# Mais informações em https://registro.br/dominio/"
                 "processo-de-liberacao/",
                 "# Arquivo gerado em 2026-10-12T10:00:00-03:00"]
        linhas.extend(nomes)
        linhas.append("# Fim do arquivo")
        return "\n".join(linhas)

    def test_aceita_lista_boa(self):
        from garimpo.adaptadores.registrobr import validar_lista
        validar_lista("https://registro.br/dominio/lista-x.txt",
                      self._lista_boa())  # nao pode levantar

    def test_aceita_cabecalho_fora_da_primeira_linha(self):
        # lista-competicao.txt nao comeca pelo periodo: comeca por
        # "# Arquivo gerado em ...", igual ao lista-competicao.txt real
        # conferido em 18/09/2026. O validador nao pode exigir que o
        # periodo seja a primeira linha.
        from garimpo.adaptadores.registrobr import validar_lista
        texto = ("# Arquivo gerado em 2026-09-14T20:30:00-03:00\n"
                 "# Mais informações em https://registro.br/dominio/"
                 "processo-de-liberacao/\n\n"
                 "# Processo de liberação no período de "
                 "2026-09-09T15:00:00-03:00 a 2026-09-16T15:00:00-03:00\n"
                 "a.com.br\nb.com.br\n# Fim do arquivo")
        validar_lista("https://registro.br/dominio/lista-competicao.txt",
                      texto)  # nao pode levantar

    def test_rejeita_corpo_html_servido_com_200(self):
        from garimpo.adaptadores.registrobr import ListaInvalida, validar_lista
        with self.assertRaises(ListaInvalida) as ctx:
            validar_lista("https://registro.br/dominio/lista-x.txt",
                          self.HTML, content_type="text/html")
        msg = str(ctx.exception)
        # a mensagem traz a URL, o Content-Type e os 80 primeiros caracteres
        self.assertIn("lista-x.txt", msg)
        self.assertIn("text/html", msg)
        self.assertIn(repr(self.HTML[:80]), msg)

    def test_rejeita_sem_cabecalho_ou_sem_rodape(self):
        from garimpo.adaptadores.registrobr import ListaInvalida, validar_lista
        sem_cabecalho = "a.com.br\nb.com.br\n# Fim do arquivo"
        sem_rodape = self._lista_boa().replace("# Fim do arquivo", "")
        for corpo in (sem_cabecalho, sem_rodape, ""):
            with self.assertRaises(ListaInvalida):
                validar_lista("https://registro.br/dominio/lista-x.txt", corpo)

    def test_baixar_rodada_para_a_execucao_se_a_liberacao_vier_html(self):
        # Elegiveis e leilao vem antes de liberacao no download; se a de
        # liberacao vier HTML, a excecao tem de propagar mesmo assim.
        from garimpo.adaptadores import registrobr as rb

        def abrir(url, timeout=None):
            if url == rb.LISTA_LIBERACAO:
                return _RespostaFalsa(self.HTML.encode(rb.CODIFICACAO_LISTAS),
                                      "text/html")
            return _RespostaFalsa(self._lista_boa().encode(rb.CODIFICACAO_LISTAS))

        with unittest.mock.patch.object(rb, "_abrir", side_effect=abrir):
            with self.assertRaises(rb.ListaInvalida):
                rb.baixar_rodada()

    def test_baixar_rodada_para_a_execucao_se_elegiveis_vier_html(self):
        # A primeira lista baixada: tem de parar antes de baixar as outras.
        from garimpo.adaptadores import registrobr as rb
        chamadas = []

        def abrir(url, timeout=None):
            chamadas.append(url)
            if url == rb.LISTA_ELEGIVEIS:
                return _RespostaFalsa(self.HTML.encode(rb.CODIFICACAO_LISTAS),
                                      "text/html")
            return _RespostaFalsa(self._lista_boa().encode(rb.CODIFICACAO_LISTAS))

        with unittest.mock.patch.object(rb, "_abrir", side_effect=abrir):
            with self.assertRaises(rb.ListaInvalida):
                rb.baixar_rodada()
        self.assertEqual(chamadas, [rb.LISTA_ELEGIVEIS])

    def test_baixar_rodada_so_avisa_se_a_lista_de_leiloes_vier_html(self):
        # A de leiloes e a unica das tres com um "ramo de queda" (a de rede
        # ja existia); lista invalida so gera aviso e segue sem ela.
        from garimpo.adaptadores import registrobr as rb
        avisos = []

        def abrir(url, timeout=None):
            if url == rb.LISTA_EM_LEILAO:
                return _RespostaFalsa(self.HTML.encode(rb.CODIFICACAO_LISTAS),
                                      "text/html")
            return _RespostaFalsa(self._lista_boa().encode(rb.CODIFICACAO_LISTAS))

        with unittest.mock.patch.object(rb, "_abrir", side_effect=abrir):
            rodada = rb.baixar_rodada(aviso=avisos.append)
        self.assertEqual(rodada.em_leilao, set())
        # o flag distingue "nao sei" de "nenhum leilao": sem ele,
        # varrer.preparar() nao teria como saber que precisa reaproveitar
        # o instantaneo anterior em vez de confiar no conjunto vazio
        self.assertFalse(rodada.em_leilao_lido)
        self.assertTrue(any("leiloes indisponivel" in a for a in avisos), avisos)
        # as outras duas listas nao sao afetadas
        self.assertEqual(rodada.total, 2)
        self.assertEqual(rodada.elegiveis, {"a.com.br", "b.com.br"})

    def test_lista_de_leiloes_boa_marca_lido(self):
        from garimpo.adaptadores import registrobr as rb

        def abrir(url, timeout=None):
            return _RespostaFalsa(self._lista_boa().encode(rb.CODIFICACAO_LISTAS))

        with unittest.mock.patch.object(rb, "_abrir", side_effect=abrir):
            rodada = rb.baixar_rodada()
        self.assertTrue(rodada.em_leilao_lido)


class TestReaproveitarLeilao(unittest.TestCase):
    """
    'Lista de leiloes invalida mantem a anterior' tem de valer de fato:
    `_reaproveitar_leilao`
    reconstroi `rodada.em_leilao` do instantaneo anterior ANTES do
    pool.montar, para o pool gravar a coluna certa (em vez de zerar todo
    mundo e o encerrar_leiloes_fora_da_lista() apagar o COMPETITIVO ja lido).
    """

    def _rodada(self, fim, em_leilao_lido, em_leilao=()):
        from garimpo.adaptadores.registrobr import Rodada
        return Rodada(liberacao=["leilao.com.br"], elegiveis={"leilao.com.br"},
                      em_leilao=set(em_leilao), fim=fim,
                      em_leilao_lido=em_leilao_lido)

    def _instantaneo(self, fim, em_leilao_em="2026-10-15T09:00:00-03:00"):
        meta = instantaneo.Metadados(
            gerado_em="2026-10-15T10:00:00-03:00", fim=fim,
            em_leilao_em=em_leilao_em)
        candidato = Candidato("leilao.com.br", elegivel=True, em_leilao=True,
                              nota=60, situacao=Situacao.COMPETITIVO,
                              candidatos=3)
        return instantaneo.exportar([candidato], meta)

    def test_lista_lida_nao_mexe_em_nada(self):
        from varrer import _reaproveitar_leilao
        rodada = self._rodada("2026-10-21T15:00:00-03:00", em_leilao_lido=True)
        nova, reaproveitado = _reaproveitar_leilao(rodada, self._instantaneo(
            "2026-10-21T15:00:00-03:00"))
        self.assertIs(nova, rodada)
        self.assertFalse(reaproveitado)

    def test_reaproveita_do_instantaneo_da_mesma_rodada(self):
        from varrer import _reaproveitar_leilao
        fim = "2026-10-21T15:00:00-03:00"
        rodada = self._rodada(fim, em_leilao_lido=False)
        nova, reaproveitado = _reaproveitar_leilao(rodada, self._instantaneo(fim))
        self.assertTrue(reaproveitado)
        self.assertEqual(nova.em_leilao, {"leilao.com.br"})
        self.assertEqual(nova.em_leilao_em, "2026-10-15T09:00:00-03:00")

    def test_nao_reaproveita_de_outra_rodada(self):
        from varrer import _reaproveitar_leilao
        rodada = self._rodada("2026-10-21T15:00:00-03:00", em_leilao_lido=False)
        anterior = self._instantaneo("2026-09-16T15:00:00-03:00")  # rodada velha
        nova, reaproveitado = _reaproveitar_leilao(rodada, anterior)
        self.assertFalse(reaproveitado)
        self.assertEqual(nova.em_leilao, set())

    def test_sem_instantaneo_nao_reaproveita(self):
        from varrer import _reaproveitar_leilao
        rodada = self._rodada("2026-10-21T15:00:00-03:00", em_leilao_lido=False)
        nova, reaproveitado = _reaproveitar_leilao(rodada, None)
        self.assertFalse(reaproveitado)
        self.assertEqual(nova.em_leilao, set())


class TestPrepararMantemLeilaoSemLista(unittest.TestCase):
    """
    Ponta a ponta: varrer.preparar() com a lista de leiloes invalida nao
    pode apagar um COMPETITIVO que o instantaneo anterior, da mesma rodada,
    ja tinha confirmado.
    """

    class _VocabFalso:
        def carregar(self, diga=None):
            return (set(), set())

    def setUp(self):
        import shutil
        import tempfile
        from garimpo.contexto import Contexto
        self.raiz = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.raiz, ignore_errors=True)
        self.ctx = Contexto(raiz=self.raiz)
        self.addCleanup(self.ctx.repo.fechar)

    def test_competitivo_do_instantaneo_sobrevive_a_lista_invalida(self):
        from garimpo.adaptadores.registrobr import Rodada
        import varrer

        fim = "2026-10-21T15:00:00-03:00"
        candidato_anterior = Candidato(
            "leilao.com.br", fonte="elegivel", elegivel=True, em_leilao=True,
            nota=60, situacao=Situacao.COMPETITIVO, candidatos=3,
            verificado_em="2026-10-15T10:00:00-03:00")
        meta = instantaneo.Metadados(
            gerado_em="2026-10-15T10:00:00-03:00",
            inicio="2026-10-14T15:00:00-03:00", fim=fim,
            em_leilao_em="2026-10-15T09:00:00-03:00")
        instantaneo.escrever(instantaneo.exportar([candidato_anterior], meta),
                             self.ctx.instantaneo)

        rodada = Rodada(liberacao=["leilao.com.br"],
                        elegiveis={"leilao.com.br"}, em_leilao=set(),
                        inicio="2026-10-14T15:00:00-03:00", fim=fim,
                        em_leilao_lido=False)
        self.ctx.baixar_rodada = lambda aviso=None: rodada
        self.ctx.vocabularios = self._VocabFalso()

        varrer.preparar(self.ctx, pool.NOTA_MINIMA, pool.TETO_LIBERACAO)

        guardado = self.ctx.repo.um("leilao.com.br")
        self.assertTrue(guardado.em_leilao)
        self.assertIs(guardado.situacao_atual, Situacao.COMPETITIVO)
        self.assertEqual(self.ctx.repo.meta("total_em_leilao"), "1")


class TestAntesDaAbertura(unittest.TestCase):
    """
    Entre a saida da lista e a abertura da rodada, o varrer.py
    aplica a lista nova e exporta, mas nao consulta o Registro.br; e depois
    da abertura as leituras da janela anterior voltam para a fila.
    """

    INICIO = "2026-10-14T15:00:00-03:00"
    FIM = "2026-10-21T15:00:00-03:00"

    class _VocabFalso:
        def carregar(self, diga=None):
            return (set(), set())

    def setUp(self):
        import shutil
        from garimpo.contexto import Contexto
        self.raiz = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.raiz, ignore_errors=True)
        self.ctx = Contexto(raiz=self.raiz)
        self.addCleanup(self.ctx.repo.fechar)

    def test_antes_da_abertura(self):
        from garimpo.dominio.frescor import epoch
        from varrer import antes_da_abertura
        self.assertTrue(antes_da_abertura(self.INICIO, epoch("2026-10-13T10:00:00-03:00")))
        self.assertFalse(antes_da_abertura(self.INICIO, epoch("2026-10-14T15:00:00-03:00")))
        # depois do fim a varredura segue (leiloes e desfecho)
        self.assertFalse(antes_da_abertura(self.INICIO, epoch("2026-10-22T10:00:00-03:00")))
        self.assertFalse(antes_da_abertura(None, 0))

    def test_esquecer_leituras_antes_compara_em_segundos(self):
        repo = self.ctx.repo
        repo.restaurar([
            # 14:30 em Brasilia: antes das 15h
            Candidato("antes.com.br", fonte="elegivel", nota=90,
                      situacao=Situacao.AGUARDANDO_LIBERACAO,
                      verificado_em="2026-10-14T14:30:00-03:00"),
            # 18:05 UTC = 15:05 em Brasilia: depois, embora o texto seja menor
            Candidato("depois.com.br", fonte="elegivel", nota=90,
                      situacao=Situacao.LIBERACAO_LIVRE,
                      verificado_em="2026-10-14T18:05:00+00:00"),
        ])
        self.assertEqual(repo.esquecer_leituras_antes(self.INICIO), 1)
        self.assertIsNone(repo.um("antes.com.br").verificado_em)
        self.assertIsNone(repo.um("antes.com.br").situacao)
        self.assertIs(repo.um("depois.com.br").situacao, Situacao.LIBERACAO_LIVRE)
        self.assertEqual(repo.esquecer_leituras_antes(None), 0)

    def _rodar_main(self, agora_iso, instantaneo_anterior=None):
        from garimpo.adaptadores.registrobr import Rodada
        from garimpo.dominio.frescor import epoch
        import varrer
        if instantaneo_anterior:
            instantaneo.escrever(instantaneo_anterior, self.ctx.instantaneo)
        rodada = Rodada(liberacao=["travado.com.br"],
                        elegiveis={"travado.com.br"}, em_leilao=set(),
                        inicio=self.INICIO, fim=self.FIM, em_leilao_lido=True)
        self.ctx.baixar_rodada = lambda aviso=None: rodada
        self.ctx.vocabularios = self._VocabFalso()
        varredura = unittest.mock.MagicMock()
        varredura.return_value.executar.return_value = unittest.mock.Mock(
            feitos=0, erros=0, bloqueios=0, mudancas=[], barrada=False,
            motivo=None)
        with unittest.mock.patch.object(varrer, "padrao", self.ctx), \
             unittest.mock.patch.object(varrer, "Varredura", varredura), \
             unittest.mock.patch.object(varrer.exportar_site, "main") as exportar, \
             unittest.mock.patch("garimpo.adaptadores.registrobr.consultar") as consultar, \
             unittest.mock.patch("time.time", return_value=epoch(agora_iso)), \
             unittest.mock.patch("sys.argv", ["varrer.py", "--minutos", "1"]), \
             unittest.mock.patch("builtins.print"):
            varrer.main()
        with open(os.path.join(self.ctx.trabalho, "varredura.json")) as f:
            resumo = json.load(f)
        return varredura, consultar, exportar, resumo

    def test_antes_da_abertura_nao_consulta_e_exporta(self):
        varredura, consultar, exportar, resumo = self._rodar_main(
            "2026-10-13T10:00:00-03:00")
        varredura.assert_not_called()
        consultar.assert_not_called()
        exportar.assert_called_once()
        self.assertEqual(resumo["pulada"], "antes_da_abertura")
        self.assertEqual(resumo["alvos"], 0)
        # a lista nova foi aplicada: o nome esta no pool, sem leitura
        self.assertIsNotNone(self.ctx.repo.um("travado.com.br"))

    def test_depois_da_abertura_le_de_novo_o_status5_de_antes(self):
        anterior = instantaneo.exportar(
            [Candidato("travado.com.br", fonte="elegivel", elegivel=True,
                       nota=90, situacao=Situacao.AGUARDANDO_LIBERACAO,
                       verificado_em="2026-10-13T10:00:00-03:00")],
            instantaneo.Metadados(gerado_em="2026-10-13T10:00:00-03:00",
                                  inicio=self.INICIO, fim=self.FIM))
        varredura, consultar, exportar, resumo = self._rodar_main(
            "2026-10-14T15:01:00-03:00", anterior)
        self.assertIsNone(resumo["pulada"])
        # o resumo sempre diz se a varredura foi barrada, e por que
        self.assertIs(resumo["barrada"], False)
        self.assertIsNone(resumo["motivo"])
        self.assertIsNone(self.ctx.repo.um("travado.com.br").situacao)
        alvos = varredura.return_value.executar.call_args[0][0]
        self.assertIn("travado.com.br", alvos)
        exportar.assert_called_once()

    # -- leituras/casa.json ----------------------------------------------

    def _casa(self, itens, fim=None):
        dados = instantaneo.exportar(
            itens, instantaneo.Metadados(gerado_em="2026-10-15T12:00:00-03:00",
                                         inicio=self.INICIO, fim=fim or self.FIM))
        dados.pop("ritmo", None)
        instantaneo.escrever(dados, os.path.join(self.raiz, "leituras", "casa.json"))
        return dados

    def _preparar(self, anterior, em_leilao=frozenset()):
        from garimpo.adaptadores.registrobr import Rodada
        from garimpo.dominio.frescor import epoch
        import varrer
        instantaneo.escrever(anterior, self.ctx.instantaneo)
        rodada = Rodada(liberacao=["casa.com.br", "leilao.com.br"],
                        elegiveis={"casa.com.br", "leilao.com.br"},
                        em_leilao=set(em_leilao), inicio=self.INICIO,
                        fim=self.FIM, em_leilao_lido=True,
                        em_leilao_em="2026-10-15T13:00:00-03:00")
        self.ctx.baixar_rodada = lambda aviso=None: rodada
        self.ctx.vocabularios = self._VocabFalso()
        falas = []
        with unittest.mock.patch("time.time",
                                 return_value=epoch("2026-10-15T14:00:00-03:00")), \
             unittest.mock.patch("builtins.print",
                                 side_effect=lambda *a, **k: falas.append(" ".join(map(str, a)))):
            varrer.preparar(self.ctx, pool.NOTA_MINIMA, pool.TETO_LIBERACAO)
        return falas

    def _leitura(self, nome, situacao, quando, candidatos=0):
        return Candidato(nome, fonte="elegivel", elegivel=True, nota=90,
                         situacao=situacao, candidatos=candidatos,
                         verificado_em=quando)

    def _anterior(self):
        return instantaneo.exportar(
            [self._leitura("casa.com.br", Situacao.LIBERACAO_LIVRE,
                           "2026-10-15T09:00:00+00:00"),
             self._leitura("leilao.com.br", Situacao.LIBERACAO_DISPUTADA,
                           "2026-10-15T09:00:00+00:00", 1)],
            instantaneo.Metadados(gerado_em="2026-10-15T09:00:00+00:00",
                                  inicio=self.INICIO, fim=self.FIM))

    def test_casa_da_mesma_rodada_vence_o_instantaneo_mais_velho(self):
        casa = self._casa([
            self._leitura("casa.com.br", Situacao.LIBERACAO_DISPUTADA,
                          "2026-10-15T12:00:00-03:00", 3),
            self._leitura("leilao.com.br", Situacao.LIBERACAO_LIVRE,
                          "2026-10-15T12:00:00-03:00")])
        # o arquivo segue o formato do dados.json, sem numero de ticket
        self.assertNotIn("ticket", json.dumps(casa))
        self._preparar(self._anterior(), em_leilao={"leilao.com.br"})
        guardado = self.ctx.repo.um("casa.com.br")
        self.assertIs(guardado.situacao, Situacao.LIBERACAO_DISPUTADA)
        self.assertEqual(guardado.candidatos, 3)
        # a lista oficial de leiloes continua mandando sobre a leitura de casa
        self.assertIs(self.ctx.repo.um("leilao.com.br").situacao_atual,
                      Situacao.COMPETITIVO)
        # e a leitura nova sai no dados.json
        saida = instantaneo.exportar(
            self.ctx.repo.buscar(),
            instantaneo.Metadados(gerado_em="agora", inicio=self.INICIO,
                                  fim=self.FIM))
        item = next(i for i in saida["itens"] if i[0] == "casa.com.br")
        self.assertEqual(saida["status"][item[instantaneo.I_SITUACAO]],
                         Situacao.LIBERACAO_DISPUTADA.value)

    def test_casa_mais_velha_nao_troca_o_instantaneo(self):
        self._casa([self._leitura("casa.com.br", Situacao.LIBERACAO_DISPUTADA,
                                  "2026-10-15T05:00:00-03:00", 3)])
        self._preparar(self._anterior())
        self.assertIs(self.ctx.repo.um("casa.com.br").situacao,
                      Situacao.LIBERACAO_LIVRE)

    def test_casa_de_outra_rodada_e_ignorada_com_aviso(self):
        self._casa([self._leitura("casa.com.br", Situacao.LIBERACAO_DISPUTADA,
                                  "2026-10-15T12:00:00-03:00", 3)],
                   fim="2026-09-16T15:00:00-03:00")
        falas = self._preparar(self._anterior())
        self.assertIs(self.ctx.repo.um("casa.com.br").situacao,
                      Situacao.LIBERACAO_LIVRE)
        self.assertTrue(any("casa.json" in f and "ignorado" in f for f in falas))

class TestFrescor(unittest.TestCase):
    """
    A promessa de idade maxima, e a regressao da fila que abandonava gente.

    Regressao: a vigia desempatava por nota com o carimbo sempre igual;
    com 195 vagas e 246 nomes, os 51 de nota mais baixa nunca voltavam.
    """

    HORA = 3600

    def _item(self, nome, nota=50, **campos):
        from garimpo.dominio import frescor
        campos.setdefault("situacao", Situacao.LIBERACAO_LIVRE)
        return frescor.Item(nome, nota=nota, **campos)

    def test_promessa_dos_quentes_so_com_a_rodada_aberta(self):
        """Fechada a rodada, o alarme nao cobra os quentes."""
        from garimpo.dominio.frescor import epoch, rodada_aberta
        ini, fim = "2026-09-09T15:00:00-03:00", "2026-09-16T15:00:00-03:00"
        self.assertTrue(rodada_aberta(ini, fim, epoch("2026-09-16T14:59:00-03:00")))
        self.assertFalse(rodada_aberta(ini, fim, epoch("2026-09-16T15:01:00-03:00")))
        # lista de outubro saiu, rodada ainda nao abriu
        self.assertFalse(rodada_aberta("2026-10-14T15:00:00-03:00", "2026-10-21T15:00:00-03:00",
                                       epoch("2026-10-12T10:00:00-03:00")))
        self.assertTrue(rodada_aberta(None, fim, 0))

    def test_epoch_compara_fusos_diferentes(self):
        """Como texto, 22:00-03:00 viria antes de 00:30+00:00. E depois."""
        from garimpo.dominio.frescor import epoch
        local = epoch("2026-09-10T22:00:00-03:00")
        runner = epoch("2026-09-11T00:30:00+00:00")
        self.assertGreater(local, runner)
        self.assertEqual(epoch("2026-09-11T01:00:00Z"), local)
        self.assertIsNone(epoch("nao e data"))
        self.assertIsNone(epoch(None))

    def test_classes(self):
        from garimpo.dominio.frescor import Classe, classificar
        itens = [
            self._item("joia.com.br", elegivel=True, nota=10),
            self._item("leilao.com.br", elegivel=True, em_leilao=True),
            self._item("dono.com.br", situacao=Situacao.REGISTRADO),
            self._item("topo.com.br", nota=90),
            self._item("fundo.com.br", nota=20),
        ]
        c = classificar(itens, limite_quente=2)
        self.assertIs(c["joia.com.br"], Classe.QUENTE)   # elegivel vem antes
        self.assertIs(c["topo.com.br"], Classe.QUENTE)
        self.assertIs(c["fundo.com.br"], Classe.FRIO)    # passou do limite
        self.assertIs(c["leilao.com.br"], Classe.LEILAO)
        self.assertIs(c["dono.com.br"], Classe.FIXO)

    def test_faixas_em_ordem_e_fixo_nunca(self):
        from garimpo.dominio.frescor import escolher
        agora = 100 * self.HORA
        itens = [
            self._item("frio.com.br", nota=1, verificado_em=agora - 50 * self.HORA),
            self._item("nunca.com.br", nota=60),
            self._item("quente.com.br", nota=99, verificado_em=agora - 9 * self.HORA),
            self._item("fresco.com.br", nota=98, verificado_em=agora - self.HORA),
            self._item("dono.com.br", situacao=Situacao.REGISTRADO,
                       verificado_em=agora - 99 * self.HORA),
        ]
        # limite 2: so "quente" e "fresco" sao quentes; "nunca" cai na fila
        from garimpo.dominio import frescor
        fila = escolher(itens, agora, 10,
                        intervalo=frescor.INTERVALO_HORAS * self.HORA,
                        limite_quente=2)
        self.assertEqual(fila[:3], ["quente.com.br", "nunca.com.br", "frio.com.br"])
        self.assertNotIn("fresco.com.br", fila)   # dentro do prazo
        self.assertNotIn("dono.com.br", fila)     # ja saiu da rodada

    def test_status5_lido_antes_da_abertura_volta_a_fila(self):
        """
        Um nome travado lido entre a lista (12/10) e a abertura
        (14/10, 15h) responde status 5, que e FIXO. Sem `rodada_inicio`, ele
        nunca mais seria relido: 0 de 2.500 na fila da rodada inteira.
        """
        from garimpo.dominio.frescor import Classe, classificar, epoch, escolher
        inicio = epoch("2026-10-14T15:00:00-03:00")
        agora = epoch("2026-10-14T15:01:00-03:00")
        antes = self._item("travado.com.br", nota=95, elegivel=True,
                           situacao=Situacao.AGUARDANDO_LIBERACAO,
                           verificado_em=epoch("2026-10-13T10:00:00-03:00"))
        depois = self._item("dono.com.br", situacao=Situacao.REGISTRADO,
                            verificado_em=epoch("2026-10-14T15:00:30-03:00"))
        fresco = self._item("fresco.com.br", nota=99, verificado_em=agora - 60)
        itens = [antes, depois, fresco]

        # sem a abertura, o comportamento antigo (e a prova do bug)
        self.assertIs(classificar(itens)["travado.com.br"], Classe.FIXO)
        self.assertNotIn("travado.com.br", escolher(itens, agora, 10))

        classes = classificar(itens, rodada_inicio=inicio)
        self.assertIsNot(classes["travado.com.br"], Classe.FIXO)
        self.assertIs(classes["dono.com.br"], Classe.FIXO)   # lido depois
        fila = escolher(itens, agora, 10, rodada_inicio=inicio)
        self.assertEqual(fila[0], "travado.com.br")
        self.assertNotIn("dono.com.br", fila)
        self.assertNotIn("fresco.com.br", fila)

        # capacidade infinita, 2.500 lidos em 13/10: todos voltam
        muitos = [self._item(f"top{n}.com.br", nota=100 - n % 50,
                             situacao=Situacao.AGUARDANDO_LIBERACAO,
                             verificado_em=epoch("2026-10-13T10:00:00-03:00"))
                  for n in range(2500)]
        fila = escolher(muitos, epoch("2026-10-18T09:00:00-03:00"), 10**6,
                        rodada_inicio=inicio)
        self.assertEqual(len(fila), 2500)

    def test_leilao_vencido_nao_espera_a_fila(self):
        """
        Regressao: rodada fechada, 200 quentes de leitura
        antiga, 1.474 nunca verificados e 190 vagas. Os 160 leiloes ficavam
        atras da fila e chegaram a 143 h com prazo de 48 h.
        """
        from garimpo.dominio import frescor
        agora = 200 * self.HORA
        antigo = agora - 143 * self.HORA
        itens = ([self._item(f"q{n:03}.com.br", nota=90, verificado_em=antigo)
                  for n in range(200)]
                 + [self._item(f"f{n:04}.com.br", nota=60) for n in range(1474)]
                 + [self._item(f"l{n:03}.com.br", situacao=Situacao.COMPETITIVO,
                               em_leilao=True, verificado_em=antigo)
                    for n in range(160)]
                 + [self._item("recente.com.br", situacao=Situacao.COMPETITIVO,
                               em_leilao=True, verificado_em=agora - self.HORA)])
        fila = frescor.escolher(itens, agora, 190, intervalo=4 * self.HORA,
                                limite_quente=200)
        leiloes = [d for d in fila if d.startswith("l")]
        # cota: 161 leiloes x 4 h / 48 h, sem os 160 de uma vez
        self.assertEqual(len(leiloes), 14)
        self.assertEqual(fila[0], "l000.com.br")
        self.assertNotIn("recente.com.br", fila)   # dentro das 48 h
        self.assertEqual(len(fila), 190)

    def test_leilao_na_frente_nao_estoura_o_prazo_dos_quentes(self):
        from garimpo.dominio import frescor
        itens = ([self._item(f"q{n:03}.com.br", nota=60 + n % 40, verificado_em=0)
                  for n in range(200)]
                 + [self._item(f"l{n:03}.com.br", situacao=Situacao.COMPETITIVO,
                               em_leilao=True) for n in range(160)]
                 + [self._item(f"f{n:03}.com.br", nota=1) for n in range(500)])
        antes = frescor.INTERVALO_HORAS
        frescor.configurar(4)          # cadencia de 4 h, prazo dos quentes 8 h
        try:
            piores, estado = self._simular(itens, capacidade=190, execucoes=16,
                                           intervalo=4, limite_quente=200)
        finally:
            frescor.configurar(antes)
        self.assertLessEqual(max(piores[2:]), 8)
        self.assertTrue(all(estado[f"l{n:03}.com.br"].verificado_em for n in range(160)))

    def _simular(self, itens, capacidade, execucoes, intervalo=None,
                 limite_quente=150, frios_por_execucao=None):
        """
        Roda a fila N vezes. Devolve a maior idade quente vista antes de cada
        execucao e o estado final; se `frios_por_execucao` for uma lista,
        anota nela quantas vagas de cada execucao foram para nao quentes.

        O intervalo padrao e o real (`frescor.INTERVALO_HORAS`), e o prazo
        cobrado tambem sai da constante: a promessa e a razao entre os dois,
        entao fixar um dos lados aqui faria o teste passar a medir uma
        politica que nao existe mais (como numa troca de cadencia de 4 h
        para 1 h).
        """
        from garimpo.dominio import frescor
        if intervalo is None:
            intervalo = frescor.INTERVALO_HORAS
        import dataclasses
        estado = {i.dominio: i for i in itens}
        piores = []
        for n in range(execucoes):
            agora = (n + 1) * intervalo * self.HORA
            classes = frescor.classificar(list(estado.values()), limite_quente)
            idades = [frescor.idade(i, agora) for i in estado.values()
                      if classes[i.dominio] is frescor.Classe.QUENTE]
            piores.append(max(idades) / self.HORA)
            escolhidos = frescor.escolher(list(estado.values()), agora,
                                          capacidade,
                                          intervalo=intervalo * self.HORA,
                                          limite_quente=limite_quente)
            if frios_por_execucao is not None:
                frios_por_execucao.append(sum(
                    1 for d in escolhidos
                    if classes[d] is not frescor.Classe.QUENTE))
            for nome in escolhidos:
                estado[nome] = dataclasses.replace(estado[nome],
                                                   verificado_em=agora)
        return piores, estado

    def test_quente_nao_come_todas_as_vagas(self):
        """
        Regressao de uma versao anterior da fila.

        200 quentes, 190 vagas, todos com o mesmo carimbo (como volta um
        instantaneo v3). Com `>=` e horizonte de uma execucao, todo quente
        vencia em toda execucao e a fila de nunca verificados ficava com
        zero vagas para sempre, com o alarme verde. A cota espalha: metade
        da faixa quente por execucao, o resto para a fila, e ainda assim
        ninguem passa do prazo.
        """
        from garimpo.dominio import frescor
        itens = ([self._item(f"q{n:03}.com.br", nota=60 + n % 40,
                             verificado_em=0) for n in range(200)]
                 + [self._item(f"f{n:03}.com.br", nota=1) for n in range(500)])
        frios = []
        piores, _ = self._simular(itens, capacidade=190, execucoes=8,
                                  limite_quente=200, frios_por_execucao=frios)
        self.assertTrue(all(n > 0 for n in frios), frios)
        self.assertLessEqual(max(piores),
                             frescor.PRAZOS[frescor.Classe.QUENTE])

    def test_ninguem_quente_passa_do_prazo(self):
        """
        A regressao. 150 quentes, 100 vagas, na cadencia e no prazo reais:
        cabe, desde que a fila nao abandone ninguem. O de nota mais baixa e
        justamente o que a vigia antiga esquecia.
        """
        from garimpo.dominio import frescor
        itens = ([self._item(f"q{n:03}.com.br", nota=100 - n % 90)
                  for n in range(150)]
                 + [self._item(f"f{n:04}.com.br", nota=1) for n in range(1000)])
        piores, _ = self._simular(itens, capacidade=100, execucoes=30)
        prazo = frescor.PRAZOS[frescor.Classe.QUENTE]
        # as duas primeiras execucoes ainda estao consumindo os nunca vistos
        self.assertLessEqual(max(piores[2:]), prazo)

    def test_sobra_de_vaga_esvazia_a_fila_de_nunca_verificados(self):
        itens = ([self._item(f"q{n:02}.com.br", nota=90) for n in range(50)]
                 + [self._item(f"f{n:03}.com.br", nota=1) for n in range(300)])
        _, estado = self._simular(itens, capacidade=100, execucoes=12,
                                  limite_quente=50)
        nunca = [i for i in estado.values() if i.verificado_em is None]
        self.assertEqual(nunca, [])

    def test_instantaneo_guarda_a_idade_de_cada_um(self):
        """v4: a idade volta do JSON. Antes todo mundo voltava com gerado_em."""
        velho = Candidato("velho.com.br", nota=50, situacao=Situacao.LIBERACAO_LIVRE,
                          candidatos=0, verificado_em="2026-09-09T12:00:00-03:00")
        novo = Candidato("novo.com.br", nota=50, situacao=Situacao.LIBERACAO_LIVRE,
                         candidatos=0, verificado_em="2026-09-10T20:00:00+00:00")
        meta = instantaneo.Metadados(gerado_em="2026-09-10T21:00:00-03:00")
        voltou = {c.dominio: c for c in instantaneo.candidatos_de(
            instantaneo.exportar([velho, novo], meta))}

        from garimpo.dominio.frescor import epoch
        self.assertEqual(epoch(voltou["velho.com.br"].verificado_em),
                         epoch(velho.verificado_em))
        self.assertEqual(epoch(voltou["novo.com.br"].verificado_em),
                         epoch(novo.verificado_em))

    def test_instantaneo_v3_cai_na_data_global(self):
        dados = {"status": ["LIBERACAO_LIVRE"], "marcas": ["OK"], "motivos": [],
                 "gerado_em": "2026-09-10T20:34:18-03:00",
                 "itens": [["x.com.br", 0, 0, 50, 0, [], 0, 0]]}
        c = instantaneo.candidatos_de(dados)[0]
        self.assertEqual(c.verificado_em, "2026-09-10T20:34:18-03:00")

    def test_relatorio_conta_os_vencidos(self):
        from garimpo.dominio.frescor import epoch
        agora = epoch("2026-09-11T12:00:00+00:00")
        velho = Candidato("velho.com.br", elegivel=True, nota=50,
                          situacao=Situacao.LIBERACAO_LIVRE, candidatos=0,
                          verificado_em="2026-09-10T12:00:00+00:00")
        novo = Candidato("novo.com.br", elegivel=True, nota=50,
                         situacao=Situacao.LIBERACAO_LIVRE, candidatos=0,
                         verificado_em="2026-09-11T11:00:00+00:00")
        dados = instantaneo.exportar([velho, novo],
                                     instantaneo.Metadados(gerado_em="x"))
        quente = [l for l in instantaneo.frescor_de(dados, agora)
                  if l["nome"] == "quente"][0]
        self.assertEqual(quente["nomes"], 2)
        self.assertEqual(quente["vencidos"], 1)
        self.assertEqual(quente["exemplos"], ["velho.com.br"])


class TestWordfreq(unittest.TestCase):
    """O leitor de msgpack feito a mao, contra bytes montados a mao."""

    def _arquivo(self, conteudo: bytes) -> str:
        import gzip
        f = tempfile.NamedTemporaryFile(suffix=".msgpack.gz", delete=False)
        f.write(gzip.compress(conteudo))
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_ranking_segue_a_ordem_dos_baldes(self):
        from garimpo.adaptadores import wordfreq
        # [ {}, ["a","b"], ["c"] ]: array de 3, mapa vazio, dois baldes
        dados = bytes([0x93, 0x80, 0x92, 0xA1]) + b"a" + bytes([0xA1]) + b"b" \
            + bytes([0x91, 0xA1]) + b"c"
        self.assertEqual(wordfreq.ranking(self._arquivo(dados)),
                         {"a": 0, "b": 1, "c": 2})

    def test_tipos_longos(self):
        from garimpo.adaptadores import wordfreq
        palavra = "x" * 40                       # str8: nao cabe no fixstr
        dados = bytes([0xDC, 0, 1, 0xD9, 40]) + palavra.encode()  # array16
        self.assertEqual(wordfreq.desempacotar(dados), [palavra])
        self.assertEqual(wordfreq.desempacotar(bytes([0xD0, 0xFF])), -1)


class TestHunspell(unittest.TestCase):
    AFF = "SET UTF-8\nSFX A Y 2\nSFX A ar a ar\nSFX A ar ou ar\n"

    def test_expande_sufixos(self):
        from garimpo.adaptadores import hunspell
        formas = hunspell.expandir("2\nplantar/A\nconfiar/A\n", self.AFF)
        for forma in ("plantar", "planta", "plantou", "confia"):
            self.assertIn(forma, formas)

    def test_nome_proprio_fica_de_fora(self):
        """Sobrenome no .dic viraria "palavra" e daria pontos a qualquer um."""
        from garimpo.adaptadores import hunspell
        self.assertNotIn("silva", hunspell.expandir("1\nSilva/A\n", self.AFF))

    def test_tira_acento(self):
        from garimpo.adaptadores import hunspell
        self.assertIn("cafe", hunspell.expandir("1\ncafé\n", self.AFF))


class TestNotaComLexico(unittest.TestCase):
    """
    Os sinais da nota, cada um com o caso que o motivou: um nome bom que a
    nota antiga deixava de fora.
    """

    def _nota(self, dominio, pt=(), en=(), **lexico):
        from garimpo.dominio.relevancia import Lexico
        return pontuar(dominio, set(pt), set(en), lexico=Lexico(**lexico))

    def test_lexico_vazio_e_a_nota_antiga(self):
        from garimpo.dominio.relevancia import Lexico
        antiga = pontuar("custas.com.br", {"custas"}, set())
        nova = pontuar("custas.com.br", {"custas"}, set(), lexico=Lexico())
        self.assertEqual(antiga, nova)

    def test_forma_verbal_conta_como_palavra(self):
        nota = self._nota("aprenda.com.br", flexoes=frozenset({"aprenda"}))
        self.assertGreaterEqual(nota.valor, 45)
        self.assertIn("palavra em português (flexão)", nota.motivos)

    def test_palavra_popular_ganha_um_pouco(self):
        com = self._nota("remedio.com.br", pt={"remedio"}, popularidade={"remedio": 6506})
        sem = self._nota("remedio.com.br", pt={"remedio"})
        self.assertGreater(com.valor, sem.valor)

    def test_composto_de_nicho(self):
        from garimpo.dominio.relevancia import Lexico, composto_de_nicho
        lex = Lexico(comuns=frozenset({"online", "casa", "novos", "friendly"}))
        self.assertEqual(composto_de_nicho("lojaonline", lex), ("loja", "online"))
        self.assertEqual(composto_de_nicho("medicaemcasa", lex), ("medica", "em", "casa"))
        nota = pontuar("imoveisnovos.com.br", set(), set(), lexico=lex)
        self.assertGreaterEqual(nota.valor, 45)

    def test_composto_nao_aceita_nome_de_pessoa(self):
        """A versao solta poria 3.950 nomes no pool, quase todos loja + nome."""
        from garimpo.dominio.relevancia import Lexico, composto_de_nicho
        lex = Lexico(comuns=frozenset({"carol", "valter"}),
                     pessoas=frozenset({"carol", "valter", "leandra"}))
        for nome in ("lojacarol", "casadovalter", "leandraimoveis"):
            self.assertIsNone(composto_de_nicho(nome, lex), nome)

    def test_composto_exige_parte_de_quatro_letras(self):
        from garimpo.dominio.relevancia import Lexico, composto_de_nicho
        lex = Lexico(comuns=frozenset({"ces", "ref"}))
        self.assertIsNone(composto_de_nicho("cesimoveis", lex))

    def test_nicho_com_cidade_grande(self):
        nota = self._nota("imoveisembelem.com.br", cidades=("belem",))
        self.assertIn("nicho + cidade: belem", nota.motivos)
        self.assertGreaterEqual(nota.valor, 45)

    def test_tres_letras_com_br_sempre_entra(self):
        """Recorde de LLL .com.br: R$ 80 mil. hyd saia do pool sem isto."""
        self.assertGreaterEqual(pontuar("hyd.com.br", set(), set()).valor, 45)
        # o pior caso: a pena de repeticao (-10) o deixava em 35 com peso 5
        self.assertGreaterEqual(pontuar("ggg.com.br", set(), set()).valor, 45)

    def test_pesos_de_base(self):
        """PESO_TRES_COM_BR subiu para 25 (30/09/2026); os de base ficam."""
        from garimpo.dominio import relevancia as r
        self.assertEqual(r.PESO_TRES_COM_BR, 25)
        self.assertEqual(
            (r.PESO_SIGLA, r.PESO_CURTO, r.PESO_COM_BR, r.PENA_REPETICAO,
             r.PESO_PALAVRA_PT, r.PESO_PALAVRA_EN, r.PESO_FLEXAO),
            (30, 35, 10, -10, 30, 18, 22))


class TestMarcaPeloSitePopular(unittest.TestCase):
    def test_site_popular_que_nao_e_palavra_e_risco(self):
        from garimpo.dominio.marcas import avaliar
        a = avaliar("jetbrains", sites_populares={"jetbrains": 862})
        self.assertIs(a.risco, Risco.RISCO)
        self.assertIn("862", a.motivo)

    def test_palavra_de_dicionario_vira_so_atencao(self):
        from garimpo.dominio.marcas import avaliar
        a = avaliar("boots", sites_populares={"boots": 9034},
                    e_palavra=lambda nome: nome == "boots")
        self.assertIs(a.risco, Risco.ATENCAO)

    def test_lista_fixa_continua_mandando(self):
        from garimpo.dominio.marcas import avaliar
        a = avaliar("wix", sites_populares={"wix": 625}, e_palavra=lambda n: True)
        self.assertIs(a.risco, Risco.RISCO)
        self.assertIn("marca conhecida", a.motivo)

    def test_sem_lista_nada_muda(self):
        from garimpo.dominio.marcas import avaliar
        self.assertIs(avaliar("jetbrains").risco, Risco.OK)


class TestPoolComVocabularios(unittest.TestCase):
    def test_par_simples_e_objeto_completo(self):
        from garimpo.adaptadores.dicionarios import Vocabularios
        from garimpo.dominio.relevancia import Lexico
        par = ({"custas"}, set())
        completo = Vocabularios(frozenset({"custas"}), frozenset(),
                                Lexico(), {"jetbrains": 862})
        self.assertEqual(pool.nota_de("custas.com.br", par),
                         pool.nota_de("custas.com.br", completo))
        self.assertIs(pool.marca_de("jetbrains.com.br", completo).risco, Risco.RISCO)
        self.assertIs(pool.marca_de("jetbrains.com.br", par).risco, Risco.OK)


class TestDemandaCnpj(unittest.TestCase):
    """O cadastro de CNPJ como sinal de demanda, com um zip de mentira."""

    # colunas: 0 cnpj_basico, 3 matriz(1)/filial(2), 4 fantasia, 5 situacao,
    # 10 inicio, 11 cnae; o resto vazio (inclusive e-mail e telefone)
    LINHAS = [
        ("11111111", "1", "Pizzaria Bella", "02", "20240105", "5611201"),
        ("11111111", "2", "Pizzaria Bella", "02", "20240105", "5611201"),  # filial
        ("22222222", "1", "PIZZARIA BELLA LTDA", "02", "20200101", "5611201"),
        ("33333333", "1", "Salão Beleza Pura", "02", "20250301", "9602501"),
        ("44444444", "1", "Pizzaria Fechada", "08", "20100101", "5611201"),  # inativa
        ("55555555", "1", "", "02", "20230101", "4781400"),                  # sem fantasia
    ]

    def _zip(self) -> str:
        import zipfile
        f = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        f.close()
        self.addCleanup(os.unlink, f.name)
        linhas = []
        for basico, mf, fantasia, sit, inicio, cnae in self.LINHAS:
            campos = [""] * 30
            campos[0], campos[3], campos[4] = basico, mf, fantasia
            campos[5], campos[10], campos[11] = sit, inicio, cnae
            linhas.append(";".join(f'"{c}"' for c in campos))
        with zipfile.ZipFile(f.name, "w") as z:
            z.writestr("K3241.ESTABELE", "\n".join(linhas).encode("latin-1"))
        return f.name

    def test_so_matriz_ativa(self):
        from garimpo.adaptadores import cnpj
        nomes = [m.nome_fantasia for m in cnpj.matrizes_ativas(self._zip())]
        self.assertEqual(nomes, ["Pizzaria Bella", "PIZZARIA BELLA LTDA",
                                 "Salão Beleza Pura", ""])

    def test_conta_por_empresa(self):
        """Filial nao conta de novo; LTDA e acento nao mudam o nome."""
        from garimpo.adaptadores import cnpj
        from garimpo.casos import demanda
        d = demanda.Demanda()
        d.somar(cnpj.matrizes_ativas(self._zip()))
        self.assertEqual(d.empresas, 4)
        self.assertEqual(d.palavras["pizzaria"], 2)
        self.assertEqual(d.palavras["salao"], 1)
        self.assertNotIn("ltda", d.palavras)
        self.assertEqual(d.nomes["pizzariabella"], 1)
        self.assertEqual(d.nomes["pizzariabellaltda"], 1)
        self.assertEqual(d.setores["561"]["pizzaria"], 2)

    def test_colado_vira_rotulo(self):
        from garimpo.casos.demanda import colado
        self.assertEqual(colado("Salão Beleza Pura"), "salaobelezapura")

    def test_ida_e_volta_e_cortes(self):
        from garimpo.casos import demanda
        d = demanda.Demanda(mes="2026-08")
        d.palavras.update({"pizzaria": 900, "rara": 1})
        d.nomes.update({"reidapizza": 3, "unica": 1})
        arquivo = os.path.join(tempfile.mkdtemp(), "demanda.json")
        demanda.gravar(d, arquivo)
        lido = demanda.ler(arquivo)
        self.assertEqual(lido["palavras"], {"pizzaria": 900})
        self.assertEqual(lido["nomes"], {"reidapizza": 3})
        self.assertEqual(lido["mes"], "2026-08")


class TestTodosDaRodada(unittest.TestCase):
    def _rodada(self):
        from garimpo.adaptadores.registrobr import Rodada
        return Rodada(liberacao=["custas.com.br", "lojaonline.com.br",
                                 "zzqx.net.br", "custas.com.br"],
                      elegiveis={"custas.com.br"}, em_leilao=set(),
                      inicio="2026-09-09T15:00:00-03:00",
                      fim="2026-09-16T15:00:00-03:00")

    def test_um_item_por_nome_com_nota(self):
        from garimpo.casos import todos
        dados = todos.montar(self._rodada(), ({"custas"}, set()))
        rotulos = [i[0] for i in dados["itens"]]
        self.assertEqual(rotulos, ["custas", "lojaonline", "zzqx"])  # sem repetido
        custas = dados["itens"][0]
        self.assertEqual(dados["extensoes"][custas[1]], "com.br")
        self.assertEqual(custas[2], pool.nota_de("custas.com.br", ({"custas"}, set()),
                                                 elegivel=True).valor)
        tipos = {dados["motivos"][i] for i in custas[3]}
        self.assertIn("elegível ao leilão", tipos)
        self.assertEqual(dados["marcas"][custas[4]], "OK")

    def test_risco_de_marca_vai_junto(self):
        """Busca na rodada inteira sem o risco levaria gente a nome de terceiro."""
        from garimpo.adaptadores.registrobr import Rodada
        from garimpo.casos import todos
        dados = todos.montar(Rodada(["netflix.com.br"], set(), set()), (set(), set()))
        self.assertEqual(dados["marcas"][dados["itens"][0][4]], "RISCO")

    def test_deterministico(self):
        """Mesmo conteudo, mesmo arquivo: o workflow nao comita a toa."""
        from garimpo.casos import todos
        a = json.dumps(todos.montar(self._rodada(), ({"custas"}, set())))
        b = json.dumps(todos.montar(self._rodada(), ({"custas"}, set())))
        self.assertEqual(a, b)
        self.assertNotIn("gerado_em", a)

    def test_motivo_so_o_tipo(self):
        from garimpo.casos import todos
        from garimpo.adaptadores.dicionarios import Vocabularios
        from garimpo.dominio.relevancia import Lexico
        vocab = Vocabularios(frozenset(), frozenset(),
                             Lexico(comuns=frozenset({"online"})), {})
        dados = todos.montar(self._rodada(), vocab)
        self.assertIn("composto", dados["motivos"])
        self.assertFalse(any(":" in m for m in dados["motivos"]))


class TestCategorias(unittest.TestCase):
    def test_casos_do_ramo(self):
        from garimpo.dominio.categorias import categorias_de
        self.assertIn("imoveis", categorias_de("imoveisembelem"))
        self.assertIn("alimentacao", categorias_de("pizzariabella"))
        self.assertIn("pet", categorias_de("petshopdobairro"))
        self.assertIn("pet", categorias_de("mundopet"))
        self.assertIn("beleza", categorias_de("barbeariadojoao"))
        self.assertIn("juridico", categorias_de("custas"))

    def test_raiz_curta_nao_casa_no_meio(self):
        """Senao "pet" acharia competencia, e "app" acharia happy."""
        from garimpo.dominio.categorias import categorias_de
        self.assertNotIn("pet", categorias_de("competencia"))
        self.assertNotIn("tecnologia", categorias_de("happyhour"))

    def test_palavra_que_engole_a_raiz(self):
        """Cada caso saiu errado numa amostra da rodada de setembro."""
        from garimpo.dominio.categorias import categorias_de
        casos = {
            "srpimoveis": "casa",              # movel dentro de imovel
            "mndvtransportes": "esporte",      # sport dentro de transport
            "investidorcientifico": "moda",    # vestido dentro de investidor
            "dasalesrefrigeracao": "pet",      # racao dentro de refrigeracao
            "paulocarneiro": "alimentacao",    # carne dentro de carneiro
            "mundobizarrobrasil": "construcao",  # obras dentro de robrasil
            "petruscavalcante": "pet",         # Petrus e nome, nao pet
        }
        for rotulo, errado in casos.items():
            self.assertNotIn(errado, categorias_de(rotulo), rotulo)
        # e o certo continua certo
        self.assertIn("imoveis", categorias_de("srpimoveis"))
        self.assertIn("transporte", categorias_de("mndvtransportes"))

    def test_raiz_engolida_hotfix_de_setembro(self):
        """Regressao: cada nome saiu no ramo errado na rodada de
        09/09/2026 (site/todos.json, 125.453 nomes)."""
        from garimpo.dominio.categorias import categorias_de
        casos = {
            "fernandacunha": "beleza",         # unha dentro de cunha
            "anapaula": "educacao",            # aula dentro de paula
            "eletrobras": "construcao",        # obras dentro de eletrobras
            "petrobrasp19": "construcao",      # obras dentro de petrobras
            "eletrobrasacre": "casa",          # eletro dentro de eletrobras
            "recargadecelular": "transporte",  # carga dentro de recarga
            "terrenosportoseguro": "esporte",  # sport em s + portoseguro
            "colchoesportoalegre": "esporte",  # esport em s + portoalegre
            "domusportoes": "esporte",         # sport em s + portoes
            "bordados": "tecnologia",          # dados dentro de bordados
            "cuidados": "tecnologia",          # dados dentro de cuidados
        }
        for rotulo, errado in casos.items():
            self.assertNotIn(errado, categorias_de(rotulo), rotulo)
        # e o certo continua certo
        self.assertIn("imoveis", categorias_de("terrenosportoseguro"))
        self.assertIn("casa", categorias_de("colchoesportoalegre"))
        self.assertIn("esporte", categorias_de("abcdesportos"))
        self.assertIn("construcao", categorias_de("jasolucoesobras"))

    def test_dados_e_digital_so_valem_sozinhos(self):
        """Modificador, nao ramo: com outro ramo no nome, tecnologia sai."""
        from garimpo.dominio.categorias import categorias_de
        # dados atravessando pousada|dosul; digital de marketing digital
        self.assertEqual(categorias_de("pousadadosul"), ("turismo",))
        self.assertEqual(categorias_de("marketingdigital"), ("marketing",))
        # sozinhos, continuam tecnologia
        self.assertEqual(categorias_de("lojadigital"), ("tecnologia",))
        self.assertEqual(categorias_de("centraldedados"), ("tecnologia",))
        # raiz forte de tecnologia ao lado de outra continua valendo
        self.assertIn("tecnologia", categorias_de("techdigitalimoveis"))

    def test_nome_sem_ramo(self):
        from garimpo.dominio.categorias import categorias_de
        self.assertEqual(categorias_de("xqmjml"), ())

    def test_mascara_e_tabela_andam_juntas(self):
        from garimpo.dominio import categorias
        bits = categorias.mascara("petshop")
        nomes = [c["nome"] for i, c in enumerate(categorias.tabela()) if bits & (1 << i)]
        self.assertEqual(nomes, ["pet"])

    def test_nomes_de_pessoas(self):
        """Nome exato ou dois nomes colados; cada metade com 3+ letras."""
        from garimpo.dominio.categorias import categorias_de, mascara, ORDEM
        gente = frozenset({"adriano", "souza", "aline", "bianca", "carlos", "ana"})
        for rotulo in ("carlos", "adrianosouza", "alinebianca", "souzacarlos"):
            self.assertIn("pessoas", categorias_de(rotulo, gente), rotulo)
        for rotulo in ("carloshop", "anaxsouza", "xyzcarlos", "banana"):
            self.assertNotIn("pessoas", categorias_de(rotulo, gente), rotulo)
        # sem a lista, a categoria some em vez de quebrar
        self.assertNotIn("pessoas", categorias_de("carlos"))
        # o bit novo foi no fim: os 18 ramos antigos mantem o numero
        self.assertEqual(ORDEM[-1], "pessoas")
        self.assertEqual(mascara("carlos", gente), 1 << (len(ORDEM) - 1))

    def test_pessoas_nos_dois_json(self):
        """Os dois exportadores recebem a mesma lista e marcam o mesmo bit."""
        from garimpo.casos import todos
        from garimpo.adaptadores.registrobr import Rodada
        from garimpo.dominio.relevancia import Lexico
        gente = frozenset({"adriano", "souza"})

        class Vocab:
            lexico = Lexico(pessoas=gente)

            def __iter__(self):
                return iter((set(), set()))

        t = todos.montar(Rodada(["adrianosouza.com.br"], set(), set()), Vocab())
        c = Candidato("adrianosouza.com.br", situacao=Situacao.LIBERACAO_LIVRE,
                      candidatos=0)
        d = instantaneo.exportar([c], instantaneo.Metadados(gerado_em="x"),
                                 pessoas=gente)
        self.assertTrue(t["itens"][0][5])
        self.assertEqual(d["itens"][0][instantaneo.I_CATEGORIAS], t["itens"][0][5])

    def test_vai_para_os_dois_json(self):
        from garimpo.casos import todos
        from garimpo.adaptadores.registrobr import Rodada
        t = todos.montar(Rodada(["petshop.com.br"], set(), set()), (set(), set()))
        self.assertEqual(t["categorias"][0]["nome"], "alimentacao")
        self.assertTrue(t["itens"][0][5])
        c = Candidato("petshop.com.br", situacao=Situacao.LIBERACAO_LIVRE, candidatos=0)
        d = instantaneo.exportar([c], instantaneo.Metadados(gerado_em="x"))
        self.assertEqual(d["itens"][0][instantaneo.I_CATEGORIAS], t["itens"][0][5])


class TestLembretes(unittest.TestCase):
    """O .ics servido pelo site, que o iPhone abre direto no Calendario."""

    # "agora" fixo: sem ele o teste dependeria do relogio e viraria vermelho
    # sozinho quando passasse o piso da rodada de setembro de 2026.
    ANTES = datetime.datetime(2026, 9, 17, 10, 0,
                              tzinfo=datetime.timezone(datetime.timedelta(hours=-3)))
    DEPOIS = datetime.datetime(2026, 9, 18, 10, 0,
                               tzinfo=datetime.timezone(datetime.timedelta(hours=-3)))

    def test_fim_e_rodada_mais_um_dia(self):
        from garimpo.casos import lembretes
        fim = lembretes.fim_do_leilao("2026-09-16T15:00:00-03:00", self.ANTES)
        self.assertEqual(fim.isoformat(), "2026-09-17T15:00:00-03:00")
        self.assertIsNone(lembretes.fim_do_leilao(None))
        self.assertIsNone(lembretes.fim_do_leilao("lixo"))

    def test_piso_vencido_nao_vira_evento(self):
        """
        O piso e a primeira hora em que o leilao PODE fechar, nao o fim: em
        17/09/2026 groupon.com.br seguia em leilao com o piso vencido havia
        horas. Passado o piso nao ha data honesta, e servir o convite velho
        marcaria no calendario de quem confia um fim que nao aconteceu.
        """
        from garimpo.casos import lembretes
        self.assertIsNone(
            lembretes.fim_do_leilao("2026-09-16T15:00:00-03:00", self.DEPOIS))

    def test_formato_do_calendario(self):
        from garimpo.casos import lembretes
        fim = lembretes.fim_do_leilao("2026-09-16T15:00:00-03:00", self.ANTES)
        texto = lembretes.ics("casa.com.br", fim)
        linhas = texto.split("\r\n")
        self.assertTrue(texto.endswith("\r\n"))
        self.assertIn("DTEND:20260917T180000Z", linhas)
        self.assertIn("DTSTART:20260917T173000Z", linhas)
        self.assertIn("BEGIN:VALARM", linhas)
        # RFC 5545: nenhuma linha passa de 75 bytes, acento incluso
        self.assertTrue(all(len(l.encode("utf-8")) <= 75 for l in linhas))
        # deterministico: mesma entrada, mesmo arquivo (o git nao ve diferenca)
        self.assertEqual(texto, lembretes.ics("casa.com.br", fim))

    def test_pasta_refeita(self):
        import tempfile
        from garimpo.casos import lembretes
        fim = lembretes.fim_do_leilao("2026-09-16T15:00:00-03:00", self.ANTES)
        with tempfile.TemporaryDirectory() as d:
            pasta = os.path.join(d, "lembretes")
            self.assertEqual(lembretes.escrever(pasta, ["a.com.br", "b.com.br"], fim), 2)
            self.assertEqual(lembretes.escrever(pasta, ["b.com.br"], fim), 1)
            self.assertEqual(os.listdir(pasta), ["b.com.br.ics"])
            self.assertEqual(lembretes.escrever(pasta, ["b.com.br"], None), 0)
            self.assertFalse(os.path.exists(pasta))


class TestCompatibilidade(unittest.TestCase):
    """filtrar_lista.py continua funcionando por conta propria."""

    def test_sem_acento(self):
        self.assertEqual(sem_acento("informação"), "informacao")

    def test_separar_antigo(self):
        self.assertEqual(separar_antigo("combustiveis.com.br"),
                         ("combustiveis", "com.br"))



# --------------------------------------------------------------------------
# RDAP e "o que aconteceu com esse nome depois"
# --------------------------------------------------------------------------

# resposta real do rdap.registro.br para pneus.com.br, capturada em
# 10/09/2026 e encurtada. E o .br mais caro ja vendido em leilao (R$ 220 mil
# em 2019) e, mesmo pago ate 2029, nao resolve para lugar nenhum.
RDAP_PNEUS = {
    "handle": "pneus.com.br",
    "status": ["active"],
    "events": [
        {"eventAction": "registration", "eventDate": "2019-02-21T21:22:26Z"},
        {"eventAction": "last changed", "eventDate": "2023-04-29T14:41:33Z"},
        {"eventAction": "expiration", "eventDate": "2029-02-21T21:22:26Z"},
    ],
    "nameservers": [{"ldhName": "a.auto.dns.br"}, {"ldhName": "b.auto.dns.br"}],
    "entities": [{
        "handle": "82534819000101",
        "roles": ["registrant"],
        "publicIds": [{"type": "cnpj", "identifier": "82.534.819/0001-01"}],
        "vcardArray": ["vcard", [
            ["version", {}, "text", "4.0"],
            ["kind", {}, "text", "org"],
            ["fn", {}, "text", "SUNSET PNEUS DO BRASIL LTDA"],
        ]],
    }],
}


class TestRdap(unittest.TestCase):
    def test_interpretar_extrai_titular_e_datas(self):
        f = rdap.interpretar("pneus.com.br", RDAP_PNEUS)
        self.assertTrue(f.existe)
        self.assertEqual(f.titular, "SUNSET PNEUS DO BRASIL LTDA")
        self.assertEqual(f.documento, "82.534.819/0001-01")
        self.assertEqual(f.registrado_em, "2019-02-21T21:22:26Z")
        self.assertEqual(f.expira_em, "2029-02-21T21:22:26Z")
        self.assertEqual(f.servidores, ("a.auto.dns.br", "b.auto.dns.br"))

    def test_sem_endereco_e_em_branco(self):
        """Pago, delegado, e ainda assim nao entrega nada."""
        f = rdap.interpretar("pneus.com.br", RDAP_PNEUS, enderecos=())
        self.assertFalse(f.resolve)
        self.assertTrue(f.em_branco)
        self.assertTrue(f.estacionado)      # a.auto.dns.br e do proprio registro

    def test_com_endereco_nao_e_em_branco(self):
        f = rdap.interpretar("x.com.br", RDAP_PNEUS, enderecos=("1.2.3.4",))
        self.assertTrue(f.resolve)
        self.assertFalse(f.em_branco)

    def test_dns_proprio_nao_e_estacionado(self):
        dados = dict(RDAP_PNEUS, nameservers=[{"ldhName": "ns1.locaweb.com.br"}])
        self.assertFalse(rdap.interpretar("x.com.br", dados).estacionado)

    def test_vcard_ausente_nao_explode(self):
        """Nem toda entidade traz vcard; faltar nao pode virar excecao."""
        dados = {"entities": [{"roles": ["registrant"]}], "events": []}
        f = rdap.interpretar("x.com.br", dados)
        self.assertIsNone(f.titular)
        self.assertTrue(f.existe)

    def test_resolver_devolve_vazio_em_vez_de_excecao(self):
        """
        Nome que nao resolve e resposta, nao falha.

        O getaddrinfo e trocado em vez de consultar um nome de verdade:
        nenhum teste deste arquivo pode depender de rede.
        """
        with unittest.mock.patch("socket.getaddrinfo",
                                 side_effect=OSError("sem resposta")):
            self.assertEqual(rdap.resolver("qualquer.com.br"), ())

    def test_resolver_ordena_e_remove_repetido(self):
        infos = [(0, 0, 0, "", ("1.2.3.4", 0)), (0, 0, 0, "", ("1.2.3.4", 0)),
                 (0, 0, 0, "", ("1.1.1.1", 0))]
        with unittest.mock.patch("socket.getaddrinfo", return_value=infos):
            self.assertEqual(rdap.resolver("qualquer.com.br"),
                             ("1.1.1.1", "1.2.3.4"))


# resposta real da CDX do Internet Archive para pneus.com.br, capturada em
# 10/09/2026 e encurtada. Mostra que, entre 2019 e 2023, o dominio NAO
# estava parado: estava redirecionando.
CDX_PNEUS = [
    ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
    ["br,com,pneus)/", "19990125", "http://pneus.com.br/", "text/html", "200", "X", "1"],
    ["br,com,pneus)/", "20031012", "http://pneus.com.br/", "text/html", "200", "X", "1"],
    ["br,com,pneus)/", "20040118", "http://pneus.com.br/", "text/html", "403", "X", "1"],
    ["br,com,pneus)/", "20081222", "http://pneus.com.br/", "text/html", "403", "X", "1"],
    ["br,com,pneus)/", "20190502", "http://pneus.com.br/", "text/html", "301", "X", "1"],
    ["br,com,pneus)/", "20230405", "http://pneus.com.br/", "text/html", "301", "X", "1"],
]


class TestWayback(unittest.TestCase):
    def test_interpretar_monta_a_linha_do_tempo(self):
        h = wayback.interpretar("pneus.com.br", CDX_PNEUS)
        self.assertTrue(h.existe)
        self.assertEqual(h.primeira, "199901")
        self.assertEqual(h.ultima, "202304")
        self.assertEqual(len(h.capturas), 5 + 1)

    def test_teve_site_e_diferente_de_respondeu(self):
        """
        301 nao e site, mas tambem nao e abandono.
        """
        h = wayback.interpretar("pneus.com.br", CDX_PNEUS)
        self.assertTrue(h.teve_site)               # houve 200 la atras
        self.assertEqual(h.ultima_viva, "202304")  # o ultimo 301 conta
        self.assertEqual(h.anos_de_site, 2)        # 1999 e 2003

    def test_sem_captura_nao_explode(self):
        h = wayback.interpretar("nunca-existiu.com.br", [])
        self.assertFalse(h.existe)
        self.assertFalse(h.teve_site)
        self.assertIsNone(h.ultima_viva)
        self.assertIn("nunca capturado", h.resumo())

    def test_le_colunas_pelo_cabecalho_e_nao_por_indice(self):
        """A CDX ja mudou de formato; ler por posicao fixa seria apostar."""
        trocado = [["statuscode", "timestamp"], ["200", "20200101"]]
        h = wayback.interpretar("x.com.br", trocado)
        self.assertEqual(h.primeira, "202001")
        self.assertTrue(h.teve_site)

    def test_cabecalho_sem_as_colunas_esperadas_vira_erro(self):
        h = wayback.interpretar("x.com.br", [["foo", "bar"], ["1", "2"]])
        self.assertIsNotNone(h.erro)


class ClienteRdapFalso:
    """Substitui o rdap.registro.br nos testes de historias."""

    def __init__(self, fichas):
        self.fichas = fichas
        self.consultados = []

    def ficha(self, dominio):
        self.consultados.append(dominio)
        return self.fichas.get(dominio, rdap.Ficha(dominio=dominio, existe=False))


class ArquivoFalso:
    """Substitui o Internet Archive nos testes de historias."""

    def __init__(self, historicos=None):
        self.historicos = historicos or {}
        self.consultados = []

    def historico(self, dominio):
        self.consultados.append(dominio)
        return self.historicos.get(dominio, wayback.Historico(dominio=dominio))


class TestHistorias(unittest.TestCase):
    def setUp(self):
        self.cliente = ClienteRdapFalso({
            "pneus.com.br": rdap.interpretar("pneus.com.br", RDAP_PNEUS),
            "ativo.com.br": rdap.Ficha(
                dominio="ativo.com.br", existe=True,
                servidores=("ns1.locaweb.com.br",), enderecos=("1.2.3.4",)),
            "parado.com.br": rdap.Ficha(
                dominio="parado.com.br", existe=True,
                servidores=("a.auto.dns.br",), enderecos=("200.160.2.95",)),
            "quebrado.com.br": rdap.Ficha(dominio="quebrado.com.br", erro="http 500"),
        })

    def investigar(self, dominios, arquivo=None):
        return historias.investigar(
            [{"dominio": d} for d in dominios], cliente=self.cliente,
            pausa=0, arquivo=arquivo)

    def test_categorias(self):
        achados = {h.dominio: h.categoria for h in self.investigar(
            ["pneus.com.br", "ativo.com.br", "parado.com.br",
             "livre.com.br", "quebrado.com.br"])}
        self.assertEqual(achados["pneus.com.br"], historias.EM_BRANCO)
        self.assertEqual(achados["ativo.com.br"], historias.ATIVO)
        self.assertEqual(achados["parado.com.br"], historias.ESTACIONADO)
        self.assertEqual(achados["livre.com.br"], historias.LIVRE)
        self.assertEqual(achados["quebrado.com.br"], historias.ERRO)

    def test_so_o_inesperado_e_notavel(self):
        """Dominio que funciona nao e historia: e o esperado."""
        por_nome = {h.dominio: h for h in self.investigar(
            ["pneus.com.br", "ativo.com.br", "parado.com.br"])}
        self.assertTrue(por_nome["pneus.com.br"].notavel)
        self.assertTrue(por_nome["parado.com.br"].notavel)
        self.assertFalse(por_nome["ativo.com.br"].notavel)

    def test_contexto_sobrevive_a_consulta(self):
        achados = historias.investigar(
            [{"dominio": "pneus.com.br", "valor": "R$ 220.000",
              "ano": "2019", "fonte": "NIC.br"}],
            cliente=self.cliente, pausa=0, arquivo=None)
        self.assertEqual(achados[0].valor, "R$ 220.000")
        self.assertIn("R$ 220.000", historias.resumir(achados))

    def test_sem_arquivo_nao_consulta_o_archive(self):
        """`arquivo=None` precisa mesmo pular a requisicao extra."""
        arquivo = ArquivoFalso()
        historias.investigar([{"dominio": "pneus.com.br"}],
                             cliente=self.cliente, pausa=0, arquivo=None)
        self.assertEqual(arquivo.consultados, [])

    def test_arquivo_distingue_morreu_de_nunca_foi_nada(self):
        arquivo = ArquivoFalso({
            "pneus.com.br": wayback.interpretar("pneus.com.br", CDX_PNEUS),
        })
        achados = self.investigar(["pneus.com.br", "ativo.com.br"],
                                  arquivo=arquivo)
        por_nome = {h.dominio: h for h in achados}
        self.assertTrue(por_nome["pneus.com.br"].ja_teve_site)
        self.assertFalse(por_nome["ativo.com.br"].ja_teve_site)
        self.assertEqual(arquivo.consultados,
                         ["pneus.com.br", "ativo.com.br"])
        # o resumo precisa contar isso, senao a descoberta nao chega a ninguem
        self.assertIn("arquivo:", historias.resumir(achados))

    def test_arquivo_fora_do_ar_nao_vira_nunca_teve_site(self):
        """Com o arquivo em 503, x.com.br nao pode sair como "sem capturas"."""
        arquivo = ArquivoFalso({
            "pneus.com.br": wayback.Historico(dominio="pneus.com.br", erro="http 503"),
        })
        achados = self.investigar(["pneus.com.br"], arquivo=arquivo)
        self.assertIn("nada a concluir", historias.resumir(achados))
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, "saida.csv")
            historias.escrever(achados, caminho)
            import csv
            linha = next(csv.DictReader(open(caminho, encoding="utf-8")))
        self.assertEqual(linha["arquivo_teve_site"], "erro")
        self.assertEqual(linha["arquivo_erro"], "http 503")

    def test_classificar_conta_tudo(self):
        contagem = historias.classificar(
            self.investigar(["pneus.com.br", "ativo.com.br"]))
        self.assertEqual(contagem[historias.EM_BRANCO], 1)
        self.assertEqual(contagem[historias.ATIVO], 1)
        self.assertEqual(contagem[historias.LIVRE], 0)

    def test_ida_e_volta_do_csv(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, "saida.csv")
            historias.escrever(self.investigar(["pneus.com.br"]), caminho)
            texto = open(caminho, encoding="utf-8").read()
            self.assertIn("SUNSET PNEUS DO BRASIL LTDA", texto)
            self.assertIn(historias.EM_BRANCO, texto)



if __name__ == "__main__":
    unittest.main()


class TestPaginas(unittest.TestCase):
    """Uma URL por assunto: montagem pura, sem ler o site_modelo."""

    LAYOUT = ("<title>{{titulo}}</title>"
              "<link rel=canonical href=\"{{canonical}}\">{{jsonld}}"
              "<nav>{{nav}}</nav>{{cabecalho_extra}}{{regua}}"
              "<main class={{classe_main}}>{{miolo}}</main><footer>{{rodape}}</footer>"
              "{{scripts}}<!-- {{nome}} {{descricao}} -->")

    def fragmento(self, **extra):
        meta = {"titulo": "As regras", "descricao": "O que vale.", **extra}
        cabeca = "<!--\n" + "".join(f"{k}: {v}\n" for k, v in meta.items()) + "-->\n"
        return cabeca + '<p id="p-total-rodada">-</p><table id="tab-bonus"></table>'

    def test_le_metadados_e_corpo(self):
        from garimpo.web import paginas
        p = paginas.ler_fragmento(self.fragmento(tipo="pagina", data="2026-09-11"),
                                  "regras-do-br")
        self.assertEqual(p.titulo, "As regras")
        self.assertEqual(p.caminho, "/regras-do-br/")
        self.assertTrue(p.corpo.startswith('<p id="p-total-rodada">'))
        self.assertEqual(p.data, "2026-09-11")

    def test_metadado_obrigatorio_e_descricao_curta(self):
        from garimpo.web import paginas
        with self.assertRaises(ValueError):
            paginas.ler_fragmento("<p>sem metadados</p>", "x")
        with self.assertRaises(ValueError):
            paginas.ler_fragmento(self.fragmento(descricao="x" * 161), "x")

    def test_slug_do_arquivo(self):
        from garimpo.web import paginas
        self.assertEqual(paginas.slug_do_arquivo("ferramenta.html"), "")
        self.assertEqual(paginas.slug_do_arquivo("insights/capsula.html"), "insights/capsula")

    def test_render_tem_um_h1_e_canonical(self):
        from garimpo.web import paginas
        p = paginas.ler_fragmento(self.fragmento(), "regras-do-br")
        html = paginas.render(p, self.LAYOUT, base="https://ex.br")
        self.assertEqual(html.count("<h1"), 1)
        self.assertIn('href="https://ex.br/regras-do-br/"', html)
        # sem regra de indexacao no HTML (nem nos cabecalhos HTTP)
        self.assertNotIn("robots", html)
        self.assertIn('<a class="aba" href="/regras-do-br/" aria-current="page">', html)
        self.assertNotIn('id="carimbo"', html)      # so a ferramenta tem
        self.assertIn("<title>As regras · Liberados</title>", html)

    def test_render_ferramenta(self):
        from garimpo.web import paginas
        p = paginas.ler_fragmento(self.fragmento(tipo="ferramenta", scripts="app.js"), "")
        html = paginas.render(p, self.LAYOUT, base="https://ex.br")
        self.assertIn('href="https://ex.br/"', html)
        # o h1 da raiz e a primeira coisa que se le: nunca so para leitor de tela
        self.assertIn('<h1 class="titulo-inicio">', html)
        self.assertNotIn('<h1 class="sr-apenas">', html)
        # o inicio de verdade abre com a contagem: o titulo vem logo depois
        # dela, antes da apresentacao, e a ordem de leitura e a da tela
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "site_modelo", "conteudo", "ferramenta.html"), encoding="utf-8") as f:
            real = paginas.ler_fragmento(f.read(), "")
        miolo = paginas._miolo(real)
        self.assertLess(miolo.index('id="p-relogio"'), miolo.index('<h1 class="titulo-inicio">'))
        self.assertLess(miolo.index('<h1 class="titulo-inicio">'), miolo.index('<div class="apresentacao">'))
        self.assertEqual(miolo.count('<h1'), 1)
        self.assertIn('id="carimbo"', html)
        self.assertIn('<script src="/app.js"></script>', html)
        # a pagina inicial e modulo ES: lista/app.js importa os outros
        modulo = paginas.ler_fragmento(self.fragmento(tipo="ferramenta", scripts="disputa.js, lista/app.js"), "")
        html_modulo = paginas.render(modulo, self.LAYOUT, base="https://ex.br")
        self.assertIn('<script src="/disputa.js"></script><script type="module" src="/lista/app.js"></script>',
                      html_modulo)
        self.assertIn('"@type": "WebSite"', html)

    def test_relogio_conta_ate_a_proxima_rodada(self):
        """Contagem regressiva do inicio: datas pela regra, prontas no HTML."""
        from garimpo.web import paginas
        rodada = {"inicio": "2026-09-09T15:00:00-03:00", "fim": "2026-09-16T15:00:00-03:00"}
        fechada = {"gerado_em": "2026-09-17T01:00:00+00:00", "rodada": rodada}
        caixa = paginas.relogio_da_pagina(fechada)
        self.assertIn('data-alvo="2026-10-12T00:00:00-03:00"', caixa)
        self.assertIn('data-alvo="2026-10-21T15:00:00-03:00"', caixa)
        self.assertIn('<time id="relogio-quando" datetime="2026-10-14T15:00:00-03:00">'
                      "quarta-feira, 14/10/2026, às 15h", caixa)
        self.assertIn('data-marco="abre" data-alvo="2026-10-14T15:00:00-03:00" '
                      'data-rotulo="A rodada de outubro abre em"', caixa)
        self.assertIn('data-desde="2026-09-16T15:00:00-03:00"', caixa)
        self.assertEqual(caixa.count('aria-pressed="true"'), 1)
        self.assertNotIn("style=", caixa)   # a CSP bloqueia
        passos_fechada = paginas.passos_da_pagina(fechada)
        self.assertIn("Candidate-se de 14/10 a 21/10", passos_fechada)
        self.assertIn('href="/dominios/"', passos_fechada)   # link nos passos
        # aberta: conta ate o fechamento dela e os passos ficam os do modelo
        aberta = {"gerado_em": "2026-09-12T12:00:00+00:00", "rodada": rodada}
        caixa = paginas.relogio_da_pagina(aberta)
        self.assertIn('datetime="2026-09-16T15:00:00-03:00">quarta-feira, 16/09/2026', caixa)
        self.assertIn("A rodada de setembro fecha em", caixa)
        self.assertEqual(paginas.passos_da_pagina(aberta), "")
        self.assertEqual(paginas.relogio_da_pagina({}), "")
        corpo = paginas.preencher_numeros('<div id="p-relogio"></div>', fechada)
        self.assertIn('<section class="relogio"', corpo)
        # a ressalva das datas mora junto dos passos, e so quando ha contagem
        self.assertNotIn("segunda quarta-feira", caixa)
        nota = '<p class="relogio-nota" id="p-relogio-nota"></p>'
        self.assertIn("pode mudar uma delas por feriado", paginas.preencher_numeros(nota, fechada))
        self.assertEqual(paginas.preencher_numeros(nota, {}), nota)
        # dias, horas, minutos e segundos, nessa ordem, que o relogio.js preenche
        unidades = re.findall(r'id="relogio-(\w)"', caixa)
        self.assertEqual(unidades, ["d", "h", "m", "s"])

    def test_dobras_do_celular_nascem_abertas_sem_script(self):
        """Passos e filtros dobram so no celular e so com o script: o botao
        nasce com hidden, e o CSS so esconde depois que o dobras.js o arma."""
        modelo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_modelo")
        with open(os.path.join(modelo, "conteudo", "ferramenta.html"), encoding="utf-8") as f:
            html = f.read()
        with open(os.path.join(modelo, "extra.css"), encoding="utf-8") as f:
            css = f.read()
        with open(os.path.join(modelo, "lista", "dobras.js"), encoding="utf-8") as f:
            js = f.read()
        for classe, alvo in (("dobra-passos", 'aria-controls="p-passos"'),
                             ("dobra-filtros", 'aria-controls="controles-filtros acoes-lista"')):
            botao = re.search(rf'<button type="button" class="dobra {classe}"[^>]*>', html).group(0)
            self.assertIn('hidden', botao)
            self.assertIn('aria-expanded="false"', botao)
            self.assertIn(alvo, botao)
        # o botao fica colado no que ele dobra (o CSS usa o seletor "+")
        self.assertRegex(html, r'class="dobra dobra-passos".*?</button>\s*<!--.*?-->\s*<ol class="passos"[^>]*id="p-passos"')
        self.assertRegex(html, r'class="dobra dobra-filtros".*?</button>\s*<div id="controles">')
        self.assertIn('id="controles-filtros"', html)
        self.assertIn('id="acoes-lista"', html)
        # esconder so abaixo de 40rem e so com data-pronta
        celular = css[css.index("dobras do celular"):]
        celular = celular[celular.index("@media (max-width: 40rem)"):]
        self.assertIn('.dobra-passos[data-pronta][aria-expanded="false"] + #p-passos { display: none; }', celular)
        self.assertIn("botao.hidden = false;", js)
        self.assertIn("botao.dataset.pronta = '';", js)

    def test_pilula_de_fase_so_some_quando_repete_a_contagem(self):
        """Entre rodadas a pilula some so na frase que repete as datas da
        contagem; "a lista ja deve ter saido" e noticia e fica."""
        modelo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_modelo")
        with open(os.path.join(modelo, "extra.css"), encoding="utf-8") as f:
            css = f.read()
        with open(os.path.join(modelo, "lista", "fase.js"), encoding="utf-8") as f:
            fase = f.read()
        self.assertIn(".fase-inicio[data-repete-relogio] { display: none; }", css)
        self.assertNotIn('.fase-inicio[data-fase="entre"] { display: none', css)
        marca = re.search(r"toggleAttribute\('data-repete-relogio', (.*?)\);", fase).group(1)
        self.assertIn("info.fase === 'entre'", marca)
        # a mesma condicao que escolhe a frase "a lista da proxima sai em"
        self.assertIn("lista > agoraMs()", marca)
        self.assertIn("entre: abre && lista > agoraMs()", fase)

    def test_rodape_leva_aos_caminhos_fixos(self):
        """O rodape e a porta fixa para /dominios/, o ciclo de vida e o
        glossario, e um alvo de toque de pelo menos 24px."""
        from garimpo.web import paginas
        modelo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_modelo")
        with open(os.path.join(modelo, "layout.html"), encoding="utf-8") as f:
            layout = f.read()
        paginas_carregadas = paginas.carregar(modelo)
        self.assertTrue(paginas_carregadas)
        for p in paginas_carregadas:
            with self.subTest(slug=p.slug or "/"):
                html = paginas.render(p, layout, base="https://ex.br")
                self.assertIn('<nav class="rodape-nav" aria-label="Rodapé">', html)
                for href, rotulo in paginas.CAMINHOS_RODAPE:
                    self.assertIn(f'<a href="{href}">{rotulo}</a>', html)
        css = open(os.path.join(modelo, "extra.css"), encoding="utf-8").read()
        self.assertIn(".rodape-nav a", css)
        self.assertIn("min-height: 1.5rem", css)   # 24px: alvo de toque minimo

    def test_inicio_explica_antes_dos_numeros(self):
        """A apresentacao vem antes dos cartoes, e Joias nao nasce visivel em zero."""
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "site_modelo", "conteudo", "ferramenta.html"),
                  encoding="utf-8") as f:
            corpo = f.read()
        self.assertLess(corpo.index('class="apresentacao"'), corpo.index('class="cartoes"'))
        self.assertIn('class="cartao destaque escondido" type="button" data-filtro="joias"', corpo)
        self.assertIn('id="fase-inicio"', corpo)

    def test_cartao_sem_competicao_zerado_some_entre_rodadas(self):
        """Relida a lista inteira, ninguem fica "fechou sem candidato": entre rodadas o cartao zerado some."""
        js = _fonte_da_lista()
        trecho = js[js.index("const semComp = document.querySelector"):]
        trecho = trecho[:trecho.index("const painelCartoes")]
        # entre rodadas o cartao zerado some; com a rodada aberta, so encurta
        self.assertIn("contagem.sem_competicao === 0", trecho)
        self.assertIn("semComp.classList.toggle('escondido', fechada)", trecho)
        self.assertIn("'Nenhum agora'", trecho)
        self.assertIn("semComp.classList.remove('escondido')", trecho)
        # a cadeia de Joias tambem nao pode parar num cartao vazio
        self.assertIn("contagem.sem_competicao ? 'sem_competicao' : filtroInicial()", js)

    def test_estilo_usa_os_tokens_de_fonte_e_raio(self):
        """Uma familia, um monoespacado e tres raios: nada de valor solto."""
        css = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "web", "style.css"), encoding="utf-8").read()
        for token in ("--fonte:", "--fonte-mono:", "--raio-p:", "--raio:", "--raio-g:", "--sombra:"):
            self.assertIn(token, css)
        corpo = css[css.index("body {"):]
        self.assertIn("font-family: var(--fonte);", corpo[:corpo.index("}")])
        # fora da declaracao dos tokens, ninguem mais escreve a pilha na mao
        depois = css[css.index("--fonte-mono:"):]
        depois = depois[depois.index("\n"):]
        self.assertNotIn("ui-monospace", depois)
        self.assertNotIn("-apple-system", depois)
        # raio solto so nas pilulas (999px)
        import re
        # 999px e a pilula; 0.25rem e a marca do texto selecionado; o par
        # "0 999px 999px 0" e a ponta arredondada da barra do relogio
        soltos = [v for v in re.findall(r"border-radius: ([^;]+);", css)
                  if "var(--raio" not in v
                  and v.strip() not in ("999px", "0.25rem", "0 999px 999px 0")]
        self.assertEqual([], soltos, soltos)

    def test_barra_de_filtros_e_painel_so_no_computador(self):
        """A barra vira painel no computador; no celular a altura nao muda (la ela empurraria a lista)."""
        css = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "site_modelo", "extra.css"), encoding="utf-8").read()
        bloco = css[css.index("barra de filtros"):]
        bloco = bloco[:bloco.index("}\n}") + 3] if "}\n}" in bloco else bloco
        self.assertIn("@media (min-width: 40.0625rem)", bloco)
        self.assertIn("#controles {", bloco)
        # nada de regra para tela pequena neste bloco
        self.assertNotIn("max-width: 40rem", bloco)

    def test_link_de_parametro_nao_gasta_rastreio(self):
        """?d= e ?ramo= tem canonical e nunca sao indexadas: os links por nome levam nofollow."""
        raiz = os.path.dirname(os.path.abspath(__file__))
        ramos = open(os.path.join(raiz, "garimpo", "web", "ramos.py"), encoding="utf-8").read()
        letras = open(os.path.join(raiz, "garimpo", "web", "letras.py"), encoding="utf-8").read()
        js = _fonte_da_lista()
        import re
        for arquivo, texto in (("ramos.py", ramos), ("letras.py", letras)):
            for linha in texto.split("\n"):
                if "quando-volta/?d=" in linha and "<a " in linha:
                    self.assertIn('rel="nofollow"', linha, f"{arquivo}: {linha.strip()[:80]}")
            for linha in texto.split("\n"):
                if 'href="/?ramo=' in linha:
                    self.assertIn('rel="nofollow"', linha, f"{arquivo}: {linha.strip()[:80]}")
        # no navegador, todo link montado para a ficha tambem marca nofollow
        criados = len(re.findall(r"quando-volta/\?d=\$\{encodeURIComponent", js))
        marcados = len(re.findall(r"rel = 'nofollow'|rel=\"nofollow\"", js))
        self.assertGreaterEqual(marcados, criados - 1, "link de ficha sem nofollow no app.js")

    def test_toda_ordem_do_seletor_tem_ordenador(self):
        """Opcao de ordem sem ordenador cai calada na relevancia (ex.: A a Z e Z a A)."""
        import re
        raiz = os.path.dirname(os.path.abspath(__file__))
        html = open(os.path.join(raiz, "site_modelo", "conteudo", "ferramenta.html"), encoding="utf-8").read()
        js = _fonte_da_lista()
        seletor = re.search(r'<select id="ordem">(.*?)</select>', html, re.S).group(1)
        valores = re.findall(r'value="([^"]+)"', seletor)
        self.assertIn("alfabetica_desc", valores)
        bloco = re.search(r"const ordenadores = \{(.*?)\n  \};", js, re.S).group(1)
        for v in valores:
            self.assertRegex(bloco, rf"\b{v}:", v)
        from garimpo.web import consultas
        local = open(os.path.join(raiz, "web", "index.html"), encoding="utf-8").read()
        seletor = re.search(r'<select id="ordem">(.*?)</select>', local, re.S).group(1)
        for v in re.findall(r'value="([^"]+)"', seletor):
            self.assertIn(v, consultas.ORDENS, v)

    def test_filtro_com_busca_abre_painel_sem_teclado(self):
        """Um campo de texto no lugar do seletor abriria o teclado do
        celular ao tocar. O gatilho e botao, o painel e dialog, e no celular o
        foco vai para a lista, nao para a busca."""
        import re
        js = _fonte_da_lista()
        corpo = js[js.index("function tornarSeletor"):js.index("function sincronizarSeletores")]
        self.assertIn("setAttribute('aria-haspopup', 'dialog')", corpo)
        self.assertIn("showModal()", corpo)
        self.assertNotIn("'combobox'", corpo)
        ramo_celular = re.search(r"if \(celular\(\)(?: \|\| !busca)?\) \{(.*?)\} else \{", corpo[corpo.index("function abrir"):], re.S).group(1)
        self.assertNotIn("busca.focus", ramo_celular)

    def test_painel_inferior_ancorado_em_bottom_sem_medir_viewport(self):
        """No Brave/Chrome do Android, arrastar para baixo e depois para cima
        deixava o painel dos filtros flutuando acima da borda quando a
        posicao vinha de visualViewport (JS), que atrasa enquanto as barras
        do navegador deslizam e nao conta a barra de baixo. O painel e
        `inset: auto 0 0 0` puro, a pagina nao rola com ele aberto, e o
        teclado e resolvido por interactive-widget=resizes-content."""
        raiz = os.path.dirname(os.path.abspath(__file__))
        js = self._app_js()
        self.assertNotIn("visualViewport", js)
        css = open(os.path.join(raiz, "site_modelo", "extra.css"), encoding="utf-8").read()
        celular = css[css.index("  .combo-painel {\n    inset: auto 0 0 0;"):]
        self.assertNotIn("combo-visivel", css)
        self.assertIn("html:has(.combo-painel[open]) { overflow: hidden; }", celular[:1500])
        layout = open(os.path.join(raiz, "site_modelo", "layout.html"), encoding="utf-8").read()
        self.assertIn("interactive-widget=resizes-content", layout)

    def test_todo_seletor_da_lista_abre_o_mesmo_painel(self):
        """Um <select> nativo (ordem, marca, situacao, por pagina) abriria,
        no celular, a lista do sistema, com outra cara que a de extensao e
        ramo. Todo <select> de #controles passa por tornarSeletor."""
        import re
        raiz = os.path.dirname(os.path.abspath(__file__))
        html = open(os.path.join(raiz, "site_modelo", "conteudo", "ferramenta.html"), encoding="utf-8").read()
        controles = html[html.index('<div id="controles">'):html.index('<div class="rolagem">')]
        ids = re.findall(r'<select id="([^"]+)"', controles)
        self.assertGreaterEqual(len(ids), 6)
        js = self._app_js()
        for i in ids:
            self.assertRegex(js, r"tornarSeletor\(\$\('#%s'\)" % re.escape(i), i)

    def _app_js(self):
        return _fonte_da_lista()

    def test_filtro_de_situacao_so_tem_chaves_conhecidas(self):
        """Toda opcao do seletor de situacao sai de SITUACOES (ou e nao verificado)."""
        import re
        raiz = os.path.dirname(os.path.abspath(__file__))
        html = open(os.path.join(raiz, "site_modelo", "conteudo", "ferramenta.html"), encoding="utf-8").read()
        seletor = re.search(r'<select id="situacao"[^>]*>(.*?)</select>', html, re.S).group(1)
        valores = [v for v in re.findall(r'value="([^"]*)"', seletor) if v]
        js = self._app_js()
        bloco = re.search(r"const SITUACOES = \{(.*?)\n\};", js, re.S).group(1)
        conhecidas = set(re.findall(r":\s*'([a-z_]+)'", bloco)) | {"nao_verificado"}
        self.assertEqual(set(valores), conhecidas)

    def test_conferir_nao_reordena_e_filtros_contam(self):
        """A conferencia ao vivo mantem a ordem da tela; aplicar reescreve as contagens."""
        js = self._app_js()
        fila = js[js.index("async function processarFila"):]
        self.assertIn("aplicar({ manterOrdem: true })", fila[:fila.index("\n}\n")])
        corpo = js[js.index("function aplicar("):js.index("// ------------------------------------------------------------------- tabela")]
        self.assertIn("pintarContagensDosFiltros(", corpo)

    def test_lista_do_link_valida_e_abrevia(self):
        """?lista= so aceita nome .br valido; .com.br vai abreviado e volta inteiro."""
        import re, shutil, json as _json
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = self._app_js()
        ler = re.search(r"function lerListaDoLink\(bruto\) \{.*?\n\}", js, re.S).group(0)
        link = re.search(r"function linkDaLista\(nomes\) \{.*?\n\}", js, re.S).group(0)
        roteiro = ("const MAXIMO_LISTA = 200; const location = {origin: 'https://ex.br'};"
                   + ler + link
                   + "const l = lerListaDoLink('cale, GRUTA,meuteste.ia.br,<script>,xx..br,a.b.com,www.sol,cale');"
                   + "process.stdout.write(JSON.stringify([[...l], linkDaLista(l)]));")
        saida = subprocess.run([node, "-e", roteiro], capture_output=True, text=True, check=True).stdout
        nomes, url = _json.loads(saida)
        self.assertEqual(nomes, ["cale.com.br", "gruta.com.br", "meuteste.ia.br", "sol.com.br"])
        self.assertEqual(url, "https://ex.br/?lista=cale,gruta,meuteste.ia.br,sol")
        # a lista de quem recebe o link nunca vai para o localStorage sozinha
        gravar = js[js.index("function gravarAcompanhados"):js.index("function garantirLinha")]
        self.assertNotIn("lista", gravar)

    def test_todo_parametro_do_endereco_e_lido_de_volta(self):
        """O link compartilhado so reproduz a tela se todo filtro gravado no endereco e lido ao abrir."""
        import re
        js = self._app_js()
        grava = js[js.index("function parametrosDaTela"):js.index("function atualizarEndereco")]
        le = js[js.index("function lerParametros"):js.index("// ----------------------------------------------------------- rodada inteira")]
        gravados = set(re.findall(r"q\.set\('([a-z]+)'", grava)) | {"lista"}
        lidos = set(re.findall(r"q\.get\('([a-z]+)'\)", le))
        self.assertTrue(gravados, "nenhum parametro achado")
        self.assertLessEqual(gravados, lidos)

    # --- busca na rodada inteira

    @staticmethod
    def _busca_py(rotulos, texto):
        """O oraculo da busca: a mesma regra de app.js, em Python."""
        import unicodedata
        def norm(s):
            s = unicodedata.normalize("NFD", s.lower())
            return re.sub(r"[^a-z0-9.]", "", "".join(c for c in s if not unicodedata.combining(c)))
        t = texto.strip()
        modo = "contem"
        if re.match(r'^["“”](.*)["“”]$', t):
            modo, t = "exato", t[1:-1]
        elif t.endswith("*") and not t.startswith("*"):
            modo = "comeca"
        elif t.startswith("*") and not t.endswith("*"):
            modo = "termina"
        termo = norm(t)
        casa = {"exato": lambda r: r == termo, "comeca": lambda r: r.startswith(termo),
                "termina": lambda r: r.endswith(termo), "contem": lambda r: termo in r}[modo]
        return sorted(r for r in rotulos if casa(norm(r)))

    def _busca_js(self, dominios, textos):
        """Roda normalizarTermo/modoDaBusca/casaBusca do app.js no node; None sem node."""
        import shutil, tempfile, json as _json
        node = shutil.which("node")
        if not node:
            return None
        js = self._app_js()
        funcoes = "".join(re.search(r"function %s\(.*?\n\}" % f, js, re.S).group(0) + "\n"
                          for f in ("normalizarTermo", "modoDaBusca", "rotuloDaLinha", "casaBusca"))
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            _json.dump({"dominios": dominios, "textos": textos}, f)
            caminho = f.name
        try:
            roteiro = ("const D = 0, RN = 13;" + funcoes
                       + "const e = JSON.parse(require('fs').readFileSync(%s, 'utf8'));" % _json.dumps(caminho)
                       + "const linhas = e.dominios.map((d) => [d]); const r = {};"
                       + "for (const t of e.textos) { const q = modoDaBusca(t);"
                       + "  r[t] = linhas.filter((it) => casaBusca(it, q)).map((it) => it[D].split('.')[0]).sort(); }"
                       + "process.stdout.write(JSON.stringify(r));")
            saida = subprocess.run([node, "-e", roteiro], capture_output=True, text=True, check=True).stdout
        finally:
            os.unlink(caminho)
        return _json.loads(saida)

    def test_busca_casa_so_no_rotulo_sem_acento_e_com_modo(self):
        """Rotulo sem extensao, sem acento/caixa/hifen; pet*, *pet e "pet"."""
        dominios = ["pizza.com.br", "pizzaria.com.br", "xpizza.com.br", "apolo.dev.br", "devops.com.br",
                    "cafe.com.br", "cafeteria.com.br", "pet.com.br", "petshop.com.br", "superpet.com.br",
                    "pet.floripa.br", "loja.com.br"]
        textos = ["pizza", "dev", "café", "cafe", "CAFÉ", "pet*", "*pet", '"pet"', "“pet”", "pet-shop", "PET"]
        r = self._busca_js(dominios, textos)
        if r is None:
            self.skipTest("sem node")
        rotulos = [d.split(".")[0] for d in dominios]
        for t in textos:
            self.assertEqual(r[t], self._busca_py(rotulos, t), t)
        self.assertEqual(r["dev"], ["devops"])          # apolo.dev.br nao casa pela extensao
        self.assertEqual(r["café"], r["cafe"])
        self.assertEqual(r["pet*"], ["pet", "pet", "petshop"])
        self.assertEqual(r["*pet"], ["pet", "pet", "superpet"])
        self.assertEqual(r['"pet"'], ["pet", "pet"])
        self.assertEqual(r["“pet”"], r['"pet"'])
        self.assertEqual(r["pet-shop"], ["petshop"])

    def test_busca_baixa_a_rodada_so_no_foco_e_fica_no_topo(self):
        """todos.json so no primeiro foco da busca; o campo sobe para baixo do subtitulo."""
        js = self._app_js()
        self.assertEqual(js.count("fetch('todos.json')"), 1)
        ligar = re.search(r"function ligarEventos\(\) \{.*?\n\}", js, re.S).group(0)
        foco = ligar[ligar.index("addEventListener('focus'"):]
        self.assertIn("carregarRodada(", foco[:foco.index("{ once: true }")])
        raiz = os.path.dirname(os.path.abspath(__file__))
        html = open(os.path.join(raiz, "site_modelo", "conteudo", "ferramenta.html"), encoding="utf-8").read()
        topo = html[html.index('class="busca-topo"'):html.index('id="fase-inicio"')]
        self.assertIn('id="busca"', topo)
        # botao visivel de confirmar, ligado ao mesmo caminho do Enter (29/09/2026)
        self.assertIn('id="botao-buscar"', topo)
        self.assertIn("$('#botao-buscar').addEventListener('click'", ligar)
        self.assertIn("confirmarBusca()", ligar)
        # sem linha fixa de atalhos sob a caixa: nome fora da rodada leva a
        # ficha pelo proprio resumo da busca
        self.assertNotIn('href="/quando-volta/"', topo)
        resumo = open(os.path.join(raiz, "site_modelo", "lista", "resumo.js"), encoding="utf-8").read()
        self.assertIn("veja se está livre ou quando volta", resumo)
        self.assertLess(html.index('id="p-subtitulo"'), html.index('class="busca-topo"'))
        self.assertEqual(html.count('id="busca"'), 1)

    def test_modo_busca_recolhe_o_painel_so_por_classe(self):
        """Com 3 letras a lista sobe para baixo da caixa; nada sai do DOM."""
        js = self._app_js()
        raiz = os.path.dirname(os.path.abspath(__file__))
        html = open(os.path.join(raiz, "site_modelo", "conteudo", "ferramenta.html"), encoding="utf-8").read()
        css = open(os.path.join(raiz, "site_modelo", "extra.css"), encoding="utf-8").read()
        # o que se recolhe existe no modelo, e so o que fica entre a caixa e a lista
        # o bloco que recolhe: da fase ate o primeiro "{ display: none; }"
        bloco = css[css.index("body.modo-busca .fase-linha"):]
        bloco = bloco[:bloco.index("{")]
        escondidos = re.findall(r"body\.modo-busca ([.#][\w-]+)(?=[,\s{])", bloco + " ")
        self.assertIn(".cartoes", escondidos)
        for sel in escondidos:
            marca = f'id="{sel[1:]}"' if sel[0] == "#" else sel[1:]
            self.assertIn(marca, html, sel)
        # a contagem fica: ela esta acima da caixa, e some-la faria a caixa pular
        for fica in ("#p-relogio", "#p-subtitulo", ".subtitulo", ".busca-topo", "#resumo-busca", "#controles",
                     ".rolagem", "#ao-vivo", "#mudancas", "#resumo-busca-status"):
            self.assertNotIn(fica, escondidos)
        regra = css[css.index("body.modo-busca .fase-linha"):]
        self.assertIn("display: none", regra[:regra.index("}")])
        # a saida do modo esta no modelo, e o CSP segue sem atributo style
        self.assertIn('id="limpar-busca"', html)
        self.assertIn("$('#limpar-busca').addEventListener('click', limparBusca)", js)
        self.assertNotIn("style=", html)
        # o modo se decide antes da lista (o cartao de antes volta ao sair) e
        # so desliga com a caixa vazia: apagar ate 2 letras nao traz o painel
        aplicar = re.search(r"function aplicar\(.*?\n\}", js, re.S).group(0)
        self.assertLess(aplicar.index("pintarModoBusca()"), aplicar.index("let base"))
        modo = re.search(r"function pintarModoBusca\(.*?\n\}", js, re.S).group(0)
        self.assertIn("!estado.busca.trim()", modo)
        self.assertIn("estado.antesDaBusca.filtro", modo)
        self.assertIn("andarRelogio()", modo)
        # escondido, o relogio nao repinta
        andar = re.search(r"function andarRelogio\(.*?\n\}", js, re.S).group(0)
        self.assertIn("modo-busca", andar)
        # o link com ?busca= ja abre no modo, antes do JSON
        self.assertLess(js.index("document.body.classList.add('modo-busca', 'modo-busca-link')"),
                        js.index("\niniciar().catch("))
        # a linha ao vivo vazia nao deixa vao no modo, mas segue no ar
        vao = re.search(r"body\.modo-busca #ao-vivo:empty \{([^}]*)\}", css).group(1)
        self.assertIn("min-height: 0", vao)
        self.assertNotIn("display", vao)
        # so quem chega pelo link perde o subtitulo, e so no celular
        link = css[css.index("body.modo-busca.modo-busca-link #p-subtitulo"):]
        self.assertIn("display: none", link[:link.index("}")])
        self.assertLess(css.rindex("@media (max-width: 40rem)", 0, css.index("body.modo-busca.modo-busca-link #p-subtitulo")),
                        css.index("body.modo-busca.modo-busca-link #p-subtitulo"))
        self.assertNotIn("modo-busca-link", html)
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        funcoes = "".join(re.search(r"function %s\(.*?\n\}" % f, js, re.S).group(0) + "\n"
                          for f in ("normalizarTermo", "modoDaBusca", "buscaPedeModo"))
        roteiro = ("const MINIMO_BUSCA_RODADA = 3;" + funcoes
                   + "process.stdout.write(JSON.stringify(['pizza', 'pi', '\"loja\"', 'piz*', ' pi ', 'pizza']"
                   + ".map((t, i) => buscaPedeModo(t, i === 5 ? 'acompanhados' : null))));")
        r = json.loads(subprocess.run([node, "-e", roteiro], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(r, [True, False, True, True, False, False])
        # entrar, limpar e entrar de novo; o aviso do modo volta a
        # ser dito na segunda busca, e a classe do link sai com o modo
        funcoes += re.search(r"function pintarModoBusca\(.*?\n\}", js, re.S).group(0) + "\n"
        roteiro = ("const MINIMO_BUSCA_RODADA = 3; const classes = new Set(['modo-busca', 'modo-busca-link']);"
                   "const document = { body: { classList: { toggle: (c, v) => (v ? classes.add(c) : classes.delete(c)),"
                   " remove: (c) => classes.delete(c) } } }; function andarRelogio() {}"
                   "const estado = { busca: 'pizza', filtro: 'sem_competicao', cartaoEscolhido: false,"
                   " modoBusca: false, antesDaBusca: null };" + funcoes
                   + "const passos = []; const olha = () => passos.push([estado.modoBusca, Boolean(estado.modoBuscaAnunciado),"
                   " classes.has('modo-busca'), classes.has('modo-busca-link')]);"
                   "pintarModoBusca(); estado.modoBuscaAnunciado = true; olha();"
                   "estado.busca = ''; pintarModoBusca(); olha();"
                   "estado.busca = 'loja'; pintarModoBusca(); olha();"
                   "process.stdout.write(JSON.stringify(passos));")
        r = json.loads(subprocess.run([node, "-e", roteiro], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(r, [[True, True, True, True], [False, False, False, False], [True, False, True, False]])

    def test_campo_sem_valor_no_layout_e_erro(self):
        from garimpo.web import paginas
        p = paginas.ler_fragmento(self.fragmento(), "x")
        with self.assertRaises(ValueError):
            paginas.render(p, "{{titulo}} {{inexistente}}")

    def test_artigo_vira_article_com_jsonld(self):
        from garimpo.web import paginas
        p = paginas.ler_fragmento(self.fragmento(tipo="artigo", secao="insights",
                                                 etiqueta="Descoberta", data="2026-09-10"),
                                  "insights/x")
        html = paginas.render(p, self.LAYOUT)
        self.assertIn('<article class="insight">', html)
        self.assertIn('<p class="etiqueta">Descoberta</p>', html)
        self.assertIn('"datePublished": "2026-09-10"', html)
        self.assertIn('<a class="aba" href="/insights/" aria-current="page">', html)

    def test_faq_extrai_perguntas(self):
        from garimpo.web import paginas
        corpo = ('<div class="pergunta"><h2>Posso?</h2><p class="resposta-curta">Não.</p>'
                 "<p>Porque <em>não</em>.</p></div>")
        p = paginas.Pagina(slug="perguntas", titulo="P", descricao="D", corpo=corpo, tipo="faq")
        faq = self.do_grafo(paginas.json_ld(p), "FAQPage")
        self.assertEqual(faq["mainEntity"][0]["name"], "Posso?")
        self.assertEqual(faq["mainEntity"][0]["acceptedAnswer"]["text"], "Não. Porque não.")

    @staticmethod
    def do_grafo(bloco, tipo):
        ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>',
                                  bloco, re.S).group(1))
        achados = [n for n in ld["@graph"] if n["@type"] == tipo]
        assert achados, f"sem {tipo} no @graph"
        return achados[0]

    def test_titulo_busca_vai_para_title_e_h1_fica_manchete(self):
        from garimpo.web import paginas
        p = paginas.ler_fragmento(self.fragmento(
            tipo="artigo", secao="insights", data="2026-09-10", modificado="2026-09-12",
            titulo_busca="Quais domínios .br são disputados"), "insights/x")
        html = paginas.render(p, self.LAYOUT + "{{og_tipo}}{{og_extra}}{{imagem}}",
                              base="https://ex.br")
        self.assertIn("<title>Quais domínios .br são disputados · Liberados</title>", html)
        self.assertIn("<h1>As regras</h1>", html)
        self.assertIn("article", html)
        self.assertIn('property="article:modified_time" content="2026-09-12"', html)
        self.assertIn("https://ex.br/og.png", html)
        artigo = self.do_grafo(paginas.json_ld(p, "https://ex.br"), "Article")
        self.assertEqual(artigo["dateModified"], "2026-09-12")
        self.assertEqual(artigo["datePublished"], "2026-09-10")
        migalhas = self.do_grafo(paginas.json_ld(p, "https://ex.br"), "BreadcrumbList")
        self.assertEqual([i["name"] for i in migalhas["itemListElement"]],
                         ["Início", "Insights", "Quais domínios .br são disputados"])

    def test_ferramenta_publica_dataset(self):
        from garimpo.web import paginas
        p = paginas.Pagina("", "Raiz", "d", "", tipo="ferramenta")
        dados = {"gerado_em": "2026-09-12T19:07:54+00:00",
                 "rodada": {"inicio": "2026-09-09T15:00:00-03:00",
                            "fim": "2026-09-16T15:00:00-03:00"}}
        bloco = paginas.json_ld(p, "https://ex.br", dados)
        conjunto = self.do_grafo(bloco, "Dataset")
        self.assertEqual(conjunto["distribution"][0]["contentUrl"], "https://ex.br/dados.json")
        self.assertEqual(conjunto["dateModified"], "2026-09-12T19:07:54+00:00")
        self.assertEqual(conjunto["temporalCoverage"],
                         "2026-09-09T15:00:00-03:00/2026-09-16T15:00:00-03:00")
        self.do_grafo(bloco, "WebSite")
        self.assertNotIn("BreadcrumbList", bloco)          # a raiz nao tem migalha

    def test_proxima_rodada_pela_segunda_quarta(self):
        from garimpo.web import paginas
        self.assertEqual(paginas.proxima_rodada("2026-09-09T15:00:00-03:00"), "14/10/2026")
        self.assertEqual(paginas.proxima_rodada("2026-10-14T15:00:00-03:00"), "11/11/2026")
        self.assertEqual(paginas.proxima_rodada("2026-12-09T15:00:00-03:00"), "13/01/2027")
        self.assertEqual(paginas.proxima_rodada(None), "")

    def test_faq_no_jsonld_leva_os_numeros_gravados(self):
        """O agente cita o JSON-LD: a data da proxima rodada nao pode sair como '-'."""
        from garimpo.web import paginas
        corpo = ('<div class="pergunta"><h2>Quando?</h2><p class="resposta-curta">'
                 'Em <span id="p-proxima-rodada">-</span>.</p></div>')
        p = paginas.Pagina("perguntas", "P", "D", corpo, tipo="faq")
        dados = {"rodada": {"inicio": "2026-09-09T15:00:00-03:00"}}
        faq = self.do_grafo(paginas.json_ld(p, "https://ex.br", dados), "FAQPage")
        self.assertEqual(faq["mainEntity"][0]["acceptedAnswer"]["text"], "Em 14/10/2026.")

    def test_glossario_vira_defined_term_set(self):
        from garimpo.web import paginas
        corpo = ('<dl><dt id="ticket">Ticket</dt><dd>O número do <b>pedido</b>.</dd>'
                 '<dt id="leilao">Processo competitivo</dt>\n<dd>O leilão.</dd></dl>')
        p = paginas.Pagina("glossario", "Glossário", "d", corpo, tipo="glossario")
        termos = self.do_grafo(paginas.json_ld(p, "https://ex.br"), "DefinedTermSet")
        self.assertEqual([(t["name"], t["description"]) for t in termos["hasDefinedTerm"]],
                         [("Ticket", "O número do pedido."), ("Processo competitivo", "O leilão.")])
        self.assertEqual(termos["hasDefinedTerm"][0]["url"], "https://ex.br/glossario/#ticket")

    def test_jsonld_nao_fecha_o_script(self):
        from garimpo.web import paginas
        p = paginas.Pagina("insights/x", "</script><b>", "d", "", tipo="artigo", secao="insights")
        bloco = paginas.json_ld(p, "https://ex.br")
        self.assertEqual(bloco.count("</script>"), 1)

    def test_llms_full_junta_o_texto(self):
        from garimpo.web import paginas
        ps = [paginas.Pagina("", "Raiz", "d1", "<p>app</p>", tipo="ferramenta"),
              paginas.Pagina("regras-do-br", "Regras", "d2", "<p>São <b id=\"p-total-rodada\">-</b>.</p>")]
        full = paginas.llms_full_txt(ps, {"total_rodada": 125453}, "https://ex.br")
        self.assertIn("# Regras", full)
        self.assertIn("**125.453**", full)
        self.assertNotIn("app", full.split("---", 1)[1])
        self.assertIn("https://ex.br/llms-full.txt", paginas.llms_txt(ps, "https://ex.br"))

    def test_preencher_numeros(self):
        from garimpo.web import paginas
        dados = {"total_rodada": 125453, "itens": [[1], [2]], "nao_verificados": 3,
                 "criterios": {"bonus": [{"peso": 25, "rotulo": "elegível"}],
                               "nota_minima": 45, "nichos": ["a"], "total_marcas": 160},
                 "gerado_em": "2026-09-11T23:09:33+00:00"}
        html = paginas.preencher_numeros(
            '<p id="p-total-rodada">-</p><b id="p-pool">x</b><table id="tab-bonus"></table>'
            '<span id="p-gerado">-</span>', dados)
        self.assertIn('<p id="p-total-rodada">125.453</p>', html)
        self.assertIn('<b id="p-pool">5</b>', html)
        self.assertIn("<td>elegível</td><td><strong>+25</strong></td>", html)
        self.assertIn('id="p-gerado">11/09/2026 20:09<', html)   # em Brasilia

    def test_sitemap_robots_e_llms(self):
        from garimpo.web import paginas
        ps = [paginas.Pagina("", "Raiz", "d1", "", tipo="ferramenta"),
              paginas.Pagina("insights/x", "X", "d2", "", tipo="artigo", data="2026-09-10")]
        mapa = paginas.sitemap(ps, "https://ex.br")
        self.assertIn("<loc>https://ex.br/</loc>", mapa)
        self.assertIn("<loc>https://ex.br/insights/x/</loc><lastmod>2026-09-10</lastmod>", mapa)
        self.assertIn("Sitemap: https://ex.br/sitemap.xml", paginas.robots_txt("https://ex.br"))
        llms = paginas.llms_txt(ps, "https://ex.br")
        self.assertIn("- [Raiz](https://ex.br/): d1", llms)             # a ferramenta: HTML
        self.assertIn("- [X](https://ex.br/insights/x/index.md): d2", llms)   # texto: o espelho

    def test_lastmod_usa_modificado_e_a_raiz_usa_o_build(self):
        """Pagina revista depois de `data` nao pode parecer mais velha do que e, e a
        raiz (sem `data`) usa o dia do instantaneo, em horario de Brasilia."""
        from garimpo.web import paginas
        ps = [paginas.Pagina("", "Raiz", "d", "", tipo="ferramenta"),
              paginas.Pagina("x", "X", "d", "", data="2026-09-11", modificado="2026-09-18")]
        # sem `dados`: a raiz fica sem lastmod, como antes
        self.assertNotIn("https://ex.br/</loc><lastmod>", paginas.sitemap(ps, "https://ex.br"))
        dados = {"gerado_em": "2026-09-18T01:30:00+00:00"}   # 17/09 22h30 em Brasilia
        mapa = paginas.sitemap(ps, "https://ex.br", dados)
        self.assertIn("<loc>https://ex.br/</loc><lastmod>2026-09-17</lastmod>", mapa)
        self.assertIn("<loc>https://ex.br/x/</loc><lastmod>2026-09-18</lastmod>", mapa)
        self.assertIn("Conferido em 18/09/2026",
                      paginas.render(ps[1], self.LAYOUT, base="https://ex.br"))

    def test_insights_pega_a_data_mais_recente_dos_artigos(self):
        """/insights/ nao tem `data` propria (sem ela, nao haveria lastmod no
        sitemap nem "Conferido em"). O "Conferido em" e o lastmod usam a
        revisao mais recente (`modificado`);
        `data` fica com a mais antiga, para o datePublished do JSON-LD nao
        dizer que o indice nasceu no dia em que um artigo so foi revisto."""
        from garimpo.web import paginas
        artigos = [paginas.Pagina("insights/a", "A", "d", "", tipo="artigo", data="2026-09-10"),
                  paginas.Pagina("insights/b", "B", "d", "", tipo="artigo",
                                 data="2026-09-05", modificado="2026-09-17"),
                  paginas.Pagina("insights/c", "C", "d", "", tipo="artigo", data="2026-09-03")]
        indice = paginas.indice_insights(artigos, "Insights", "d")
        self.assertEqual(indice.data, "2026-09-03")           # a mais antiga
        self.assertEqual(indice.modificado, "2026-09-17")     # a mais recente
        self.assertIn("<lastmod>2026-09-17</lastmod>",
                      paginas.sitemap([indice], "https://ex.br"))
        vazio = paginas.indice_insights([], "Insights", "d")
        self.assertEqual((vazio.data, vazio.modificado), ("", ""))

    def test_html_para_markdown(self):
        from garimpo.web import paginas
        html = ("<h2>Título</h2><p>Um <strong>forte</strong> e <code>x</code>, ver "
                '<a href="/regras-do-br/">regras</a>.</p>'
                "<ul><li>um<ul><li>dentro</li></ul></li><li>dois</li></ul>"
                '<table class="tabela-doc"><tbody><tr><td><strong>A</strong></td><td>b | c</td></tr></tbody></table>'
                "<table><thead><tr><th>H1</th><th>H2</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr></tbody></table>"
                "<pre><code>a &lt; b\nc</code></pre>"
                '<blockquote class="citacao">frase<cite>Fonte</cite></blockquote>'
                '<div class="aviso">cuidado</div>'
                '<span class="sr-apenas">oculto</span><button>botão</button>')
        md = paginas.html_para_markdown(html, "https://ex.br")
        self.assertIn("## Título", md)
        self.assertIn("Um **forte** e `x`, ver [regras](https://ex.br/regras-do-br/).", md)
        self.assertIn("- um\n\n  - dentro\n\n- dois", md)
        self.assertIn("|  |  |\n|---|---|\n| **A** | b \\| c |", md)
        self.assertIn("| H1 | H2 |\n|---|---|\n| 1 | 2 |", md)
        self.assertIn("```\na < b\nc\n```", md)
        self.assertIn("> frase — Fonte", md)
        self.assertIn("> cuidado", md)
        self.assertNotIn("oculto", md)
        self.assertNotIn("botão", md)

    def test_markdown_da_pagina_grava_numeros(self):
        from garimpo.web import paginas
        p = paginas.Pagina("regras-do-br", "Regras", "Desc.", '<p>São <b id="p-total-rodada">-</b> nomes.</p>',
                           data="2026-09-11")
        md = paginas.markdown_da_pagina(p, {"total_rodada": 125453}, "https://ex.br")
        self.assertTrue(md.startswith("# Regras\n\n> Desc.\n\nSão **125.453** nomes."))
        self.assertIn("Fonte: https://ex.br/regras-do-br/", md)
        self.assertIn("Conferido em 2026-09-11", md)

    def test_ancorar_da_id_estavel_a_cada_titulo(self):
        from garimpo.web import paginas
        corpo = ('<h2 id="curto">Já tem</h2><h2>Quanto custa um domínio?</h2>'
                 '<h3>Quanto custa um domínio?</h3>'
                 '<h2>Por que só <span id="p-pool">15 mil</span> nomes</h2>'
                 '<dt>sem id</dt><dt id="elegivel">Elegível</dt>')
        novo, titulos = paginas.ancorar(corpo)
        self.assertEqual([i for _, i, _ in titulos],
                         ["curto", "quanto-custa-um-dominio", "quanto-custa-um-dominio-2",
                          "por-que-so-nomes", "elegivel"])
        # o numero gravado pelo build nao entra no endereco: senao ele muda a cada rodada
        self.assertIn('<h2 id="por-que-so-nomes">', novo)
        self.assertIn("<dt>sem id</dt>", novo)
        self.assertLessEqual(len(paginas.slug_ancora("palavra " * 30)), 60)

    def test_faq_ganha_indice_links_e_url_por_pergunta(self):
        from garimpo.web import paginas
        corpo = ('<p>Intro.</p>'
                 '<div class="pergunta"><h2 id="custa">Quanto custa?</h2>'
                 '<p class="resposta-curta">Nada.</p></div>'
                 '<div class="pergunta"><h2>Chegar primeiro ajuda?</h2>'
                 '<p class="resposta-curta">Não.</p></div>')
        p = paginas.Pagina("perguntas", "P", "D", corpo, tipo="faq")
        html = paginas.render(p, self.LAYOUT, base="https://ex.br")
        self.assertLess(html.index('<nav class="indice"'), html.index('<div class="pergunta">'))
        self.assertIn('<li><a href="#chegar-primeiro-ajuda">Chegar primeiro ajuda?</a></li>', html)
        self.assertIn('<h2 id="custa"><a class="ancora" href="#custa">Quanto custa?</a></h2>', html)
        self.assertIn('<script src="/compartilhar.js"></script>', html)
        faq = self.do_grafo(paginas.json_ld(p, "https://ex.br"), "FAQPage")
        self.assertEqual([q["url"] for q in faq["mainEntity"]],
                         ["https://ex.br/perguntas/#custa",
                          "https://ex.br/perguntas/#chegar-primeiro-ajuda"])
        md = paginas.markdown_da_pagina(p, None, "https://ex.br")
        self.assertIn("## [Quanto custa?](https://ex.br/perguntas/#custa)", md)

    def test_artigo_tem_ancoras_mas_nao_indice(self):
        from garimpo.web import paginas
        corpo = "".join(f"<h2>Parte {n}</h2><p>{'texto ' * 300}</p>" for n in range(6))
        artigo = paginas.Pagina("insights/x", "X", "D", corpo, tipo="artigo", secao="insights")
        html = paginas.render(artigo, self.LAYOUT, base="https://ex.br")
        self.assertNotIn('class="indice"', html)
        self.assertIn('<a class="ancora" href="#parte-0">', html)
        pagina = paginas.Pagina("regras", "R", "D", corpo)
        self.assertIn('class="indice"', paginas.render(pagina, self.LAYOUT, base="https://ex.br"))
        self.assertNotIn("compartilhar.js", paginas.render(
            paginas.Pagina("", "Raiz", "D", "", tipo="ferramenta"), self.LAYOUT))

    def test_indice_de_insights_ordena(self):
        from garimpo.web import paginas
        a = paginas.Pagina("insights/b", "B", "db", "", tipo="artigo", ordem=2)
        b = paginas.Pagina("insights/a", "A", "da", "", tipo="artigo", ordem=1)
        idx = paginas.indice_insights([a, b], "Insights", "Sete achados.")
        self.assertLess(idx.corpo.index('href="/insights/a/"'), idx.corpo.index('href="/insights/b/"'))
        self.assertEqual(idx.caminho, "/insights/")

    def test_favicon_de_verdade(self):
        """
        O icone da marca: o selo .br desenhado em tracos (nao texto nem um
        emoji em data: URI, que dependem da fonte de quem renderiza e nunca
        viram arquivo rastreavel para o Google).
        """
        import exportar_site
        raiz = os.path.dirname(__file__)

        with open(os.path.join(raiz, "site_modelo", "favicon.svg"), encoding="utf-8") as f:
            svg = f.read()
        self.assertNotIn("<text", svg)
        self.assertIn("<path", svg)

        with open(os.path.join(raiz, "site_modelo", "layout.html"), encoding="utf-8") as f:
            layout = f.read()
        self.assertNotIn("data:image/svg+xml", layout)
        self.assertIn('<link rel="icon" href="/favicon.svg" type="image/svg+xml">', layout)
        self.assertIn('<link rel="icon" href="/favicon-48.png" sizes="48x48">', layout)
        self.assertIn('<link rel="apple-touch-icon" href="/apple-touch-icon.png">', layout)

        # os quatro arquivos de icone estao comitados e vao ao ar como
        # estaticos
        for nome in ("favicon.svg", "favicon-48.png", "favicon.ico", "apple-touch-icon.png"):
            self.assertIn(nome, exportar_site.ESTATICOS)
            self.assertTrue(os.path.exists(os.path.join(raiz, "site_modelo", nome)), nome)


# --------------------------------------------------------------------------
# Fontes oficiais do Registro.br (12/09/2026): a enumeracao do status, o
# ISAVAIL, a extensao NIC.br do RDAP e as categorias de extensao.
# Ver docs/fontes-oficiais-registrobr.md.
# --------------------------------------------------------------------------

# pacotes de avail.registro.br:43/udp, no formato capturado em 12/09/2026
# (nomes trocados por neutros);
# os tickets sao sinteticos (90000000 em diante): os reais sao de
# candidatos de verdade e o RDAP ainda responde a ?ticket= de ticket
# cancelado (docs/limitacoes-registrobr.md, S15)
ISAVAIL_COOKIE = "% Copyright Nic.br\nCK 8a6e52837fc2c68f7a4a 123\n"
ISAVAIL_LEILAO = (
    "% Copyright Nic.br\nST 9 727976103\ncarro.com.br\n"
    "2026-09-26 15:00:00|2026-09-26 15:00:00|2026-09-26 15:00:00\n"
    "90000001|90000002|90000003|90000004|90000005|90000006|90000007|"
    "90000008|90000009|90000010\n")
ISAVAIL_DISPUTADO = (
    "% Copyright Nic.br\nST 7 196086054\none.com.br\n"
    "2026-09-26 15:00:00|2026-09-26 15:00:00\n"
    "90001001|90001002|90001003|90001004|90001005|90001006|90001007|"
    "90001008|90001009|90001010\n")
ISAVAIL_EQUIVALENTE = (
    "% Copyright Nic.br\nST 3 544709500\ncafé.com.br|xn--caf-dma.com.br\n"
    "Domínio já registrado sob sintaxe similar\n")
ISAVAIL_LIVRE = "% Copyright Nic.br\nST 0 745255192\nexemplolivrezz.com.br\n"
ISAVAIL_INVALIDO = "% Copyright Nic.br\nST 4 785478214\ncarro.com.br\nconsulta inválida\n"
# os quatro abaixo sao os exemplos de Protocolo-ISAVAIL.txt
ISAVAIL_REGISTRADO = (
    "% Copyright registro.br\nST 2 12345\nexample.eng.br\n"
    "2007-03-15|published|fork.example.eng.br|example.eng.br\n"
    "blog|flog|sec3|vlog|wiki\n")
ISAVAIL_AGUARDANDO = "% Copyright registro.br\nST 5 12345\nexample.com.br\n"
ISAVAIL_LIMITE = "% Copyright registro.br\nST 8\nQuery rate limit exceeded\n"
ISAVAIL_COM_TICKET = "% Copyright registro.br\nST 1 12345\nexample.com.br\n2567849|2567856\n"


class TestStatusEnumeracao(unittest.TestCase):
    """
    O status e a enumeracao do ISAVAIL, nao um bitmask.

    Regressao: lido como bits (4|1), o 5 ("aguardando processo de
    liberacao") virava LIBERACAO_LIVRE, a joia falsa por excelencia.
    """

    def test_aguardando_nao_e_joia(self):
        leitura = classificar({"status": 5, "fqdn": "travado.com.br", "exempt": False})
        self.assertIs(leitura.situacao, Situacao.AGUARDANDO_LIBERACAO)
        self.assertIsNot(leitura.situacao, Situacao.LIBERACAO_LIVRE)
        self.assertTrue(leitura.situacao.resolvida)

    def test_com_ticket_fora_da_rodada(self):
        leitura = classificar({"status": 1, "fqdn": "x.com.br", "tickets": [1, 2]})
        self.assertIs(leitura.situacao, Situacao.LIVRE_COM_TICKET)
        self.assertEqual(leitura.candidatos, 2)

    def test_indisponivel_e_fato_com_motivo(self):
        leitura = classificar({"status": 3, "fqdn": "", "fqdnace": "xn--caf-dma.com.br",
                               "reasons": ["Domínio já registrado sob sintaxe similar"]})
        self.assertIs(leitura.situacao, Situacao.INDISPONIVEL)
        self.assertTrue(leitura.situacao.resolvida)
        self.assertIn("sintaxe similar", leitura.detalhe)

    def test_consulta_invalida_e_erro(self):
        com_motivo = classificar({"status": 4, "fqdn": "x", "reasons": ["consulta inválida"]})
        self.assertIs(com_motivo.situacao, Situacao.ERRO)
        self.assertIs(classificar({"status": 4, "fqdn": "x"}).situacao, Situacao.ERRO)

    def test_oito_com_fqdn_nao_e_leilao_nem_bloqueio(self):
        self.assertIs(classificar({"status": 8, "fqdn": "x.com.br"}).situacao,
                      Situacao.ERRO)

    def test_rate_limit_em_ingles_tambem_e_bloqueio(self):
        leitura = classificar({"status": 8, "fqdn": "",
                               "reasons": ["Query rate limit exceeded"]})
        self.assertIs(leitura.situacao, Situacao.LIMITADO)

    def test_valor_fora_da_tabela(self):
        self.assertIs(classificar({"status": 12, "fqdn": "x.com.br"}).situacao,
                      Situacao.INDESCONHECIDO)

    def test_situacoes_novas_ficam_no_fim_do_instantaneo(self):
        """Os indices gravados no JSON publicado nao podem mudar."""
        self.assertEqual(instantaneo.SITUACOES[:5], (
            Situacao.LIBERACAO_LIVRE, Situacao.LIBERACAO_DISPUTADA,
            Situacao.COMPETITIVO, Situacao.LIVRE, Situacao.REGISTRADO))
        self.assertIn(Situacao.AGUARDANDO_LIBERACAO, instantaneo.SITUACOES)


class TestIsavail(unittest.TestCase):
    """O leitor do protocolo oficial, contra pacotes gravados."""

    def test_cookie(self):
        r = isavail.interpretar(ISAVAIL_COOKIE)
        self.assertTrue(r.novo_cookie)
        self.assertEqual(r.cookie, "8a6e52837fc2c68f7a4a")
        self.assertIsNone(r.status)

    def test_leilao_com_dez_tickets(self):
        r = isavail.interpretar(ISAVAIL_LEILAO)
        self.assertEqual((r.status, r.fqdn, len(r.tickets)), (9, "carro.com.br", 10))
        self.assertEqual(r.tickets[0], 90000001)
        self.assertEqual(r.datas, ("2026-09-26 15:00:00",) * 3)
        leitura = classificar(r.payload())
        self.assertIs(leitura.situacao, Situacao.COMPETITIVO)
        self.assertEqual(leitura.candidatos, 10)

    def test_datas_nao_entram_no_payload(self):
        """12/09/2026: o ISAVAIL deu 26/09 para uma rodada que fecha 16/09."""
        p = isavail.interpretar(ISAVAIL_LEILAO).payload()
        self.assertNotIn("ends-at", p)
        self.assertNotIn("accepting-new-tickets-until", p)

    def test_disputado(self):
        leitura = classificar(isavail.interpretar(ISAVAIL_DISPUTADO).payload())
        self.assertIs(leitura.situacao, Situacao.LIBERACAO_DISPUTADA)
        self.assertEqual(leitura.candidatos, 10)

    def test_equivalente_reconhece_ace_pelo_prefixo(self):
        r = isavail.interpretar(ISAVAIL_EQUIVALENTE)
        self.assertEqual((r.status, r.fqdn, r.fqdnace),
                         (3, "café.com.br", "xn--caf-dma.com.br"))
        self.assertIn("sintaxe similar", r.mensagem)
        self.assertIs(classificar(r.payload()).situacao, Situacao.INDISPONIVEL)

    def test_livre(self):
        self.assertIs(classificar(isavail.interpretar(ISAVAIL_LIVRE).payload()).situacao,
                      Situacao.LIVRE)

    def test_registrado_exemplo_da_especificacao(self):
        r = isavail.interpretar(ISAVAIL_REGISTRADO)
        self.assertEqual(r.expira_em, "2007-03-15")
        self.assertEqual(r.publicacao, "published")
        self.assertEqual(r.servidores, ("fork.example.eng.br", "example.eng.br"))
        self.assertEqual(r.sugestoes[0], "blog.br")
        leitura = classificar(r.payload())
        self.assertIs(leitura.situacao, Situacao.REGISTRADO)
        self.assertIn("expires-at", leitura.detalhe)

    def test_aguardando_e_com_ticket(self):
        self.assertIs(classificar(isavail.interpretar(ISAVAIL_AGUARDANDO).payload()).situacao,
                      Situacao.AGUARDANDO_LIBERACAO)
        leitura = classificar(isavail.interpretar(ISAVAIL_COM_TICKET).payload())
        self.assertIs(leitura.situacao, Situacao.LIVRE_COM_TICKET)
        self.assertEqual(leitura.candidatos, 2)

    def test_limite_e_invalido(self):
        r = isavail.interpretar(ISAVAIL_LIMITE)
        self.assertEqual(r.status, 8)
        self.assertIs(classificar(r.payload()).situacao, Situacao.LIMITADO)
        self.assertIs(classificar(isavail.interpretar(ISAVAIL_INVALIDO).payload()).situacao,
                      Situacao.ERRO)

    def test_cliente_faz_a_danca_do_cookie(self):
        pacotes = [ISAVAIL_COOKIE, ISAVAIL_LIVRE]
        perguntas = []

        def enviar(p):
            perguntas.append(p)
            return pacotes.pop(0)

        cliente = isavail.Cliente(enviar=enviar)
        self.assertIs(cliente.verificar("exemplolivrezz.com.br").situacao, Situacao.LIVRE)
        self.assertEqual(len(perguntas), 2)
        self.assertTrue(perguntas[0].startswith("2 00000000000000000000 1 "))
        self.assertIn(" 8a6e52837fc2c68f7a4a ", perguntas[1])
        # a versao 1+ exige o campo "sugerir" no fim; sem ele e "consulta invalida"
        self.assertTrue(perguntas[1].endswith(" exemplolivrezz.com.br 0"))
        self.assertEqual(cliente.cookie, "8a6e52837fc2c68f7a4a")

    def test_cliente_sem_resposta_vira_erro(self):
        def enviar(p):
            raise OSError("timed out")

        leitura = isavail.Cliente(enviar=enviar).verificar("x.com.br")
        self.assertIs(leitura.situacao, Situacao.ERRO)
        self.assertIn("timed out", leitura.detalhe)


# resposta real do rdap.registro.br para pneus.com.br em 12/09/2026, a parte
# nova: a conferencia que o proprio registro faz de cada servidor de DNS
RDAP_PNEUS_NS = dict(
    RDAP_PNEUS,
    nicbr_arbitration=True,
    nameservers=[
        {"ldhName": "a.auto.dns.br", "events": [
            {"eventAction": "delegation check", "eventDate": "2026-09-04T08:10:12Z",
             "status": ["ns aa"]},
            {"eventAction": "last correct delegation check",
             "eventDate": "2026-09-04T08:10:12Z"}]},
        {"ldhName": "b.auto.dns.br", "events": [
            {"eventAction": "delegation check", "eventDate": "2026-09-04T08:10:12Z",
             "status": ["ns aa"]},
            {"eventAction": "last correct delegation check",
             "eventDate": "2026-09-04T08:10:12Z"}]},
    ],
)
# rdap.registro.br/entity/<cnpj>, 12/09/2026, sem o vcard e com o nome do
# representante trocado: esse campo nunca pode sair da resposta crua
RDAP_ENTIDADE = {
    "objectClassName": "entity", "handle": "82534819000101",
    "publicIds": [{"type": "cnpj", "identifier": "82.534.819/0001-01"}],
    "nicbr_domainCount": 166, "legalRepresentative": "Nome De Pessoa",
}


class TestRdapExtensaoNicbr(unittest.TestCase):
    def test_delegacao_conferida_pelo_registro(self):
        f = rdap.interpretar("pneus.com.br", RDAP_PNEUS_NS)
        self.assertEqual(len(f.delegacoes), 2)
        self.assertTrue(f.delegacoes[0].responde)
        self.assertFalse(f.delegacao_quebrada)
        self.assertEqual(f.delegacao_ok_em, "2026-09-04T08:10:12Z")
        self.assertTrue(f.arbitragem)
        self.assertEqual(f.servidores, ("a.auto.dns.br", "b.auto.dns.br"))
        self.assertEqual(historias.dns_segundo_o_registro(f), "ok")

    def test_delegacao_quebrada_sabe_desde_quando(self):
        dados = dict(RDAP_PNEUS_NS, nameservers=[
            {"ldhName": "ns1.morto.com.br", "events": [
                {"eventAction": "delegation check", "eventDate": "2026-09-10T00:00:00Z",
                 "status": "ns timeout"},           # a especificacao usa string
                {"eventAction": "last correct delegation check",
                 "eventDate": "2024-03-01T00:00:00Z"}]},
            {"ldhName": "ns2.morto.com.br", "events": [
                {"eventAction": "delegation check", "eventDate": "2026-09-10T00:00:00Z",
                 "status": ["ns udn"]}]},
        ])
        f = rdap.interpretar("morto.com.br", dados)
        self.assertTrue(f.delegacao_quebrada)
        self.assertEqual(f.delegacao_ok_em, "2024-03-01T00:00:00Z")
        self.assertEqual(historias.dns_segundo_o_registro(f), "quebrado")

    def test_um_servidor_bom_nao_e_quebrado(self):
        dados = dict(RDAP_PNEUS_NS)
        dados["nameservers"] = [RDAP_PNEUS_NS["nameservers"][0], {
            "ldhName": "ns2.x.com.br", "events": [
                {"eventAction": "delegation check", "eventDate": "2026-09-10T00:00:00Z",
                 "status": ["ns timeout"]}]}]
        f = rdap.interpretar("x.com.br", dados)
        self.assertFalse(f.delegacao_quebrada)
        self.assertEqual(historias.dns_segundo_o_registro(f), "parcial")

    def test_sem_conferencia_nao_afirma_nada(self):
        f = rdap.interpretar("x.com.br", RDAP_PNEUS)      # fixture sem eventos
        self.assertFalse(f.delegacao_quebrada)
        self.assertIsNone(f.delegacao_ok_em)
        self.assertIsNone(f.arbitragem)
        self.assertEqual(historias.dns_segundo_o_registro(f), "")

    def test_ordem_judicial(self):
        f = rdap.interpretar("x.com.br", dict(RDAP_PNEUS, status=["nicbr inactive court order"]))
        self.assertTrue(f.fora_do_ar_por_decisao)
        self.assertFalse(rdap.interpretar("x.com.br", RDAP_PNEUS).fora_do_ar_por_decisao)

    def test_entidade_so_conta_dominios(self):
        self.assertEqual(rdap.interpretar_entidade(RDAP_ENTIDADE), 166)
        chamadas = []

        def consulta(handle):
            chamadas.append(handle)
            return RDAP_ENTIDADE

        f = rdap.com_titular(rdap.interpretar("pneus.com.br", RDAP_PNEUS_NS), consulta)
        self.assertEqual(f.dominios_do_titular, 166)
        self.assertEqual(chamadas, ["82534819000101"])    # so digitos
        self.assertNotIn("Nome De Pessoa", repr(f))

    def test_entidade_nunca_para_cpf(self):
        chamadas = []
        # sem pontuacao de proposito: nenhum texto do repositorio carrega
        # algo com cara de CPF formatado
        f = rdap.Ficha("x.com.br", existe=True, documento="cpf 11 digitos")
        rdap.com_titular(f, lambda h: chamadas.append(h) or {})
        self.assertEqual(chamadas, [])

    def test_entidade_com_falha_nao_derruba(self):
        def quebra(handle):
            raise OSError("rede")

        f = rdap.com_titular(rdap.interpretar("pneus.com.br", RDAP_PNEUS_NS), quebra)
        self.assertIsNone(f.dominios_do_titular)
        self.assertTrue(f.existe)

    def test_resumo_conta_o_que_o_registro_sabe(self):
        f = rdap.Ficha("morto.com.br", existe=True, dominios_do_titular=166,
                       delegacoes=(rdap.Delegacao("ns1", "ns timeout",
                                                  "2026-09-10T00:00:00Z",
                                                  "2024-03-01T00:00:00Z"),))
        texto = historias.resumir([historias.Historia(ficha=f)])
        self.assertIn("desde 2024-03-01", texto)
        self.assertIn("166", texto)


# forma PROVAVEL de /ticket/<n>: um dominio em pendingCreate com a entidade
# do candidato. Nao conferida ao vivo em 12/09/2026 (a consulta foi barrada
# como dado pessoal); o leitor e tolerante, para aceitar variacoes do JSON
# real.
RDAP_TICKET = {
    "objectClassName": "domain", "ldhName": "exemplo.com.br",
    "status": ["pending create"],
    "events": [{"eventAction": "registration", "eventDate": "2026-09-09T15:01:02Z"}],
    "publicIds": [{"type": "ticket", "identifier": "90000001"}],
    "entities": [{
        "objectClassName": "entity", "handle": "12345678000199",
        "roles": ["registrant"],
        "publicIds": [{"type": "cnpj", "identifier": "12.345.678/0001-99"}],
        "vcardArray": ["vcard", [["version", {}, "text", "4.0"],
                                 ["fn", {}, "text", "EMPRESA EXEMPLO LTDA"]]],
    }],
}


class TestTickets(unittest.TestCase):
    def test_interpretar_ticket(self):
        c = rdap.interpretar_ticket(90000001, RDAP_TICKET)
        self.assertEqual(c.dominio, "exemplo.com.br")
        self.assertEqual(c.nome, "EMPRESA EXEMPLO LTDA")
        self.assertEqual((c.tipo_documento, c.documento), ("cnpj", "12.345.678/0001-99"))
        self.assertEqual(c.papel, "registrant")
        self.assertEqual(c.pedido_em, "2026-09-09T15:01:02Z")
        self.assertFalse(c.pessoa_fisica)

    def test_ticket_vazio_nao_explode(self):
        c = rdap.interpretar_ticket(1, {})
        self.assertIsNone(c.nome)
        self.assertIsNone(c.dominio)

    def test_cpf_e_pessoa_fisica(self):
        dados = dict(RDAP_TICKET, entities=[{"roles": ["registrant"],
                                             "publicIds": [{"type": "cpf", "identifier": "***.456.789-**"}]}])
        self.assertTrue(rdap.interpretar_ticket(2, dados).pessoa_fisica)


class TestCadenciaConfiguravel(unittest.TestCase):
    """O intervalo vem do workflow; o prazo dos quentes segue a razao."""

    def setUp(self):
        from garimpo.dominio import frescor
        self.frescor = frescor
        self.antes = (frescor.INTERVALO_HORAS, frescor.PRAZOS[frescor.Classe.QUENTE])

    def tearDown(self):
        self.frescor.configurar(self.antes[0])
        self.frescor.PRAZOS[self.frescor.Classe.QUENTE] = self.antes[1]

    def test_configurar_muda_intervalo_e_prazo(self):
        self.frescor.configurar(4)
        self.assertEqual(self.frescor.INTERVALO_HORAS, 4)
        self.assertEqual(self.frescor.PRAZOS[self.frescor.Classe.QUENTE], 8)
        self.assertEqual(self.frescor.tabela()["intervalo_horas"], 4)
        quente = next(c for c in self.frescor.tabela()["classes"] if c["nome"] == "quente")
        self.assertEqual(quente["prazo_horas"], 8)


class TestRitmo(unittest.TestCase):
    """Os tickets sao um contador global: a serie vira curva e estimativa."""

    H = 3600
    SERIE = [(0, 1000), (4 * 3600, 1400), (8 * 3600, 2200)]

    def test_registrar_so_avanca(self):
        from garimpo.dominio import ritmo
        s = ritmo.registrar([], 10, 100)
        s = ritmo.registrar(s, 20, 90)          # menor: nao acrescenta nada
        s = ritmo.registrar(s, 15, 150)
        s = ritmo.registrar(s, 12, 200)         # instante que nao avanca e corrigido
        self.assertEqual(s, [(10, 100), (15, 150), (15, 200)])

    def test_estimar_interpola(self):
        from garimpo.dominio import ritmo
        self.assertEqual(ritmo.estimar(self.SERIE, 1200), 2 * self.H)
        self.assertEqual(ritmo.estimar(self.SERIE, 1000), 0)
        self.assertEqual(ritmo.estimar(self.SERIE, 500), 0)     # antes: o limite
        self.assertIsNone(ritmo.estimar(self.SERIE, 9999))      # ainda nao visto
        self.assertIsNone(ritmo.estimar([], 1))

    def test_emitidos_e_por_hora(self):
        from garimpo.dominio import ritmo
        self.assertEqual(ritmo.emitidos(self.SERIE, 1000), 1201)
        self.assertIsNone(ritmo.emitidos(self.SERIE, None))
        # ultimas 4 h: de 1400 a 2200 em 4 h
        self.assertAlmostEqual(ritmo.por_hora(self.SERIE, 8 * self.H, 4 * self.H), 200)
        self.assertIsNone(ritmo.por_hora(self.SERIE[:1], 8 * self.H))
        # cinco pontos em sete minutos (a primeira execucao real): a subida e
        # descoberta de nomes, nao emissao; taxa nenhuma e melhor que 872 mil/h
        mesma_execucao = [(0, 1000), (100, 1500), (200, 2200), (300, 3000), (420, 3100)]
        self.assertIsNone(ritmo.por_hora(mesma_execucao, 420))
        r = ritmo.resumo(self.SERIE, 1000, 8 * self.H)
        self.assertEqual((r["emitidos"], r["pontos"]), (1201, 3))

    def test_por_hora_ignora_descoberta_dentro_da_execucao(self):
        # regressao: a serie saia com 2.289/h, o certo e ~267/h
        from datetime import datetime, timezone
        from garimpo.dominio import ritmo

        def t(txt):
            return int(datetime.strptime(txt, "%d %H:%M:%S").replace(
                year=2026, month=9, tzinfo=timezone.utc).timestamp())
        serie = [(t("12 17:44:51"), 32163208), (t("12 17:45:07"), 32164997),
                 (t("12 17:45:24"), 32179031), (t("12 17:45:43"), 32180685),
                 (t("12 17:46:08"), 32181871), (t("12 19:02:39"), 32181952),
                 (t("12 19:39:15"), 32182245), (t("12 19:39:26"), 32182353),
                 (t("12 19:57:32"), 32182458), (t("13 02:56:21"), 32183938),
                 (t("13 02:58:25"), 32184329)]
        self.assertEqual(len(ritmo.por_execucao(serie)), 3)
        self.assertAlmostEqual(ritmo.por_hora(serie, t("13 02:58:25")), 267, delta=1)

    def test_leitura_carrega_tickets_e_corte(self):
        leitura = classificar(LIBERACAO_DISPUTADA)
        self.assertEqual(leitura.tickets, (30001101, 30001102))
        self.assertFalse(leitura.cortado)
        dez = dict(LIBERACAO_DISPUTADA, tickets=list(range(1, 11)))
        self.assertTrue(classificar(dez).cortado)

    def test_repositorio_anota_ritmo_e_exporta_chegada(self):
        from garimpo.dominio import ritmo
        repo = Repositorio(":memory:")
        repo.gravar_pool([Candidato("a.com.br", nota=70), Candidato("b.com.br", nota=60)])
        repo.gravar_leitura("a.com.br", Leitura(Situacao.LIBERACAO_DISPUTADA, 2, "",
                                                 7, (1000, 1200)), epoch=100)
        repo.gravar_leitura("b.com.br", Leitura(Situacao.LIBERACAO_DISPUTADA, 2, "",
                                                 7, (1100, 2000)), epoch=200)
        # ticket menor visto depois nao acrescenta ponto
        repo.gravar_leitura("a.com.br", Leitura(Situacao.LIBERACAO_DISPUTADA, 2, "",
                                                 7, (1000, 1200)), epoch=300)
        self.assertEqual(repo.serie_ritmo(), [(100, 1200), (200, 2000)])
        self.assertEqual(repo.ticket_primeiro(), 1000)
        a = repo.um("a.com.br")
        self.assertEqual((a.ticket_min, a.ticket_max), (1000, 1200))

        meta = instantaneo.Metadados(gerado_em="2026-09-12T00:00:00+00:00")
        dados = instantaneo.exportar(repo.verificados(), meta,
                                     serie=repo.serie_ritmo(),
                                     primeiro=repo.ticket_primeiro(), agora_epoch=400)
        item = next(i for i in dados["itens"] if i[0] == "b.com.br")
        # 1100 esta entre 1200 (t=100)... nao: 1100 < 1200, o primeiro ponto
        self.assertEqual(item[instantaneo.I_CHEGADA_MIN], 100)
        self.assertEqual(item[instantaneo.I_CHEGADA_MAX], 200)
        self.assertEqual(dados["ritmo"]["emitidos"], 1001)
        self.assertEqual(dados["ritmo"]["serie"], [[100, 1200], [200, 2000]])
        # os numeros de ticket nao saem no item
        self.assertNotIn(1200, item)
        self.assertNotIn(2000, item)

        # restaurar num banco novo repoe a serie e a chegada estimada
        novo = Repositorio(":memory:")
        novo.gravar_pool([Candidato("b.com.br", nota=60)])
        novo.restaurar(instantaneo.candidatos_de(dados))
        novo.restaurar_ritmo(*instantaneo.ritmo_de(dados))
        self.assertEqual(novo.serie_ritmo(), [(100, 1200), (200, 2000)])
        self.assertEqual(novo.ticket_primeiro(), 1000)
        self.assertEqual(novo.um("b.com.br").chegada_min, 100)
        # e uma rodada nova zera tudo
        novo.esquecer_leituras()
        self.assertEqual(novo.serie_ritmo(), [])
        self.assertIsNone(novo.ticket_primeiro())

    def test_instantaneo_antigo_sem_chegada(self):
        """Item com 11 posicoes (formato v5 antigo) continua legivel."""
        dados = {"status": ["LIBERACAO_LIVRE"], "marcas": ["OK"], "motivos": [],
                 "gerado_em": "2026-09-11T00:00:00+00:00",
                 "itens": [["x.com.br", 0, 0, 50, 1, [], 0, 0, 0, 0, 0]]}
        c = instantaneo.candidatos_de(dados)[0]
        self.assertIsNone(c.chegada_min)
        self.assertEqual(instantaneo.ritmo_de(dados), ([], None))


class TestRecontagem(unittest.TestCase):
    """Com 10 tickets no avail, a varredura pergunta ao RDAP uma vez."""

    def _repo(self):
        repo = Repositorio(":memory:")
        repo.gravar_pool([Candidato("dez.com.br", nota=70), Candidato("dois.com.br", nota=70)])
        return repo

    def test_reconta_so_quem_bateu_no_corte(self):
        repo = self._repo()
        cliente = ClienteFalso({
            "dez.com.br": Leitura(Situacao.LIBERACAO_DISPUTADA, 10, "ends-at=x", 7,
                                  tuple(range(1, 11))),
            "dois.com.br": Leitura(Situacao.LIBERACAO_DISPUTADA, 2, "", 7, (1, 2)),
        })
        contados = []

        def contador(dominio):
            contados.append(dominio)
            return 67

        v = Varredura(cliente, repo, contador=contador)
        v.PAUSA_RDAP = 0
        v.executar(["dez.com.br", "dois.com.br"], pausa=0)
        self.assertEqual(contados, ["dez.com.br"])
        self.assertEqual(repo.um("dez.com.br").candidatos, 67)
        self.assertIn("rdap=67", repo.um("dez.com.br").detalhe)
        self.assertEqual(repo.um("dois.com.br").candidatos, 2)

    def test_erro_no_rdap_desliga_ate_a_proxima_execucao(self):
        repo = self._repo()
        repo.gravar_pool([Candidato("outro.com.br", nota=70)])
        dez = Leitura(Situacao.LIBERACAO_DISPUTADA, 10, "", 7, tuple(range(1, 11)))
        cliente = ClienteFalso({"dez.com.br": dez, "outro.com.br": dez})
        chamadas = []

        def contador(dominio):
            chamadas.append(dominio)
            raise OSError("429")

        v = Varredura(cliente, repo, contador=contador)
        v.PAUSA_RDAP = 0
        v.executar(["dez.com.br", "outro.com.br"], pausa=0)
        self.assertEqual(chamadas, ["dez.com.br"])      # parou no primeiro erro
        self.assertEqual(repo.um("dez.com.br").candidatos, 10)

    def test_sem_contador_nada_muda(self):
        repo = self._repo()
        dez = Leitura(Situacao.LIBERACAO_DISPUTADA, 10, "", 7, tuple(range(1, 11)))
        Varredura(ClienteFalso({"dez.com.br": dez}), repo).executar(["dez.com.br"], pausa=0)
        self.assertEqual(repo.um("dez.com.br").candidatos, 10)


class TestSinaisDoDesfecho(unittest.TestCase):
    def test_cruza_veredito_com_faixas(self):
        import sinais_do_desfecho as sd
        desfecho = [
            {"dominio": "curto.com.br", "leitura": sd.OCULTO},
            {"dominio": "outrocurto.com.br", "leitura": sd.ZERO},
            {"dominio": "aindanarodada.com.br", "leitura": "ainda na rodada ou travado"},
        ]
        antes = {
            "curto.com.br": {"nota": 85, "elegivel": True, "motivos": ["palavra em português"]},
            "outrocurto.com.br": {"nota": 55, "elegivel": False, "motivos": ["nicho: x"]},
        }
        t = sd.cruzar(desfecho, antes)
        self.assertEqual({k: t["todos"][k] for k in ("n", "ocultos", "taxa")},
                         {"n": 2, "ocultos": 1, "taxa": 0.5})
        self.assertEqual(t["nota 80+"]["taxa"], 1.0)
        self.assertEqual(t["não elegível"]["ocultos"], 0)
        self.assertIn("extensão genérica", t)
        self.assertIn("nicho comercial", t)
        rel = sd.relatorio(t)
        self.assertIn("2 nomes", rel)
        self.assertNotIn("| nota 80+", rel)     # menos de 5: nao entra na tabela

    def test_taxa_ponderada_pela_amostra(self):
        """A faixa sorteada em 1 de 10 pesa 10 na taxa geral."""
        import sinais_do_desfecho as sd
        desfecho = [
            {"dominio": "alto.com.br", "leitura": sd.OCULTO, "peso": "1"},
            {"dominio": "baixo.com.br", "leitura": sd.ZERO, "peso": "10"},
        ]
        antes = {"alto.com.br": {"nota": 85, "elegivel": False, "motivos": []},
                 "baixo.com.br": {"nota": 30, "elegivel": False, "motivos": []}}
        t = sd.cruzar(desfecho, antes)
        self.assertEqual((t["todos"]["n"], t["todos"]["ocultos"]), (2, 1))
        self.assertAlmostEqual(t["todos"]["taxa"], 1 / 11)

    def test_amostra_por_faixa_do_desfecho(self):
        """15.736 nomes nao cabem em 90 min; sorteio por faixa com peso."""
        import desfecho
        from garimpo.adaptadores.repositorio import Candidato
        alvos = ([Candidato(f"b{i:04d}.com.br", nota=30) for i in range(1000)]
                 + [Candidato(f"a{i}.com.br", nota=85) for i in range(3)])
        escolhidos, pesos = desfecho.amostrar(alvos, 50)
        self.assertEqual(len(escolhidos), 53)
        self.assertEqual({c.dominio for c in escolhidos if c.nota == 85},
                         {"a0.com.br", "a1.com.br", "a2.com.br"})
        self.assertEqual(pesos["a0.com.br"], 1.0)
        self.assertEqual({pesos[c.dominio] for c in escolhidos if c.nota == 30}, {20.0})
        # semente fixa: a mesma amostra em toda execucao
        self.assertEqual([c.dominio for c in escolhidos],
                         [c.dominio for c in desfecho.amostrar(alvos, 50)[0]])
        todos, pesos = desfecho.amostrar(alvos, 0)
        self.assertEqual((len(todos), set(pesos.values())), (1003, {1.0}))

    def test_itens_do_instantaneo(self):
        import sinais_do_desfecho as sd
        dados = {"motivos": ["a", "b"], "itens": [["x.com.br", 0, 0, 70, 1, [1], 0]]}
        self.assertEqual(sd.itens_do_instantaneo(dados)["x.com.br"],
                         {"nota": 70, "elegivel": True, "motivos": ["b"]})


class TestExtensoes(unittest.TestCase):
    """Quem pode registrar em cada extensao, pelo TLDs.php oficial."""

    def test_categorias(self):
        self.assertEqual(extensoes.categoria("com.br"), extensoes.GENERICA)
        self.assertEqual(extensoes.categoria("adv.br"), extensoes.PROFISSIONAL)
        self.assertEqual(extensoes.categoria("ind.br"), extensoes.EMPRESA)
        self.assertEqual(extensoes.categoria("rio.br"), extensoes.CIDADE)
        self.assertEqual(extensoes.categoria("blog.br"), extensoes.PESSOA)
        self.assertEqual(extensoes.categoria("org.br"), extensoes.RESTRITA)
        self.assertEqual(extensoes.categoria("br"), extensoes.RESTRITA)
        self.assertEqual(extensoes.categoria("naoexiste.br"), extensoes.DESCONHECIDA)

    def test_restrita(self):
        self.assertFalse(extensoes.restrita("app.br"))
        self.assertFalse(extensoes.restrita("sampa.br"))
        self.assertTrue(extensoes.restrita("med.br"))
        self.assertTrue(extensoes.restrita("tur.br"))
        # sem comprovacao da profissao (ajuda 2.6 do Registro.br, 18/09/2026)
        self.assertIn("só CPF", extensoes.quem_registra("med.br"))
        self.assertNotIn("conselho", extensoes.quem_registra("med.br"))

    def test_toda_extensao_frequente_da_rodada_esta_catalogada(self):
        """As 50 mais comuns na lista de setembro de 2026."""
        vistas = """com net app adv org ia tec dev ind art blog eng eco med agr
            tur srv tv log ong imb api inf seg arq pro social rio adm etc eti
            psc esp xyz cnt vet bsb vlog mus far wiki sorocaba floripa cim rec
            poa bio curitiba flog campinas""".split()
        faltam = [e for e in vistas if extensoes.categoria(e + ".br") == extensoes.DESCONHECIDA]
        self.assertEqual(faltam, [])

    def test_nota_rotula_e_pesa_extensao_restrita(self):
        restrita = pontuar("advogado.adv.br", {"advogado"}, set())
        livre = pontuar("advogado.art.br", {"advogado"}, set())   # generica
        self.assertTrue(any(m.startswith("extensão restrita") for m in restrita.motivos))
        self.assertFalse(any(m.startswith("extensão restrita") for m in livre.motivos))
        self.assertLess(restrita.valor, livre.valor)

    def test_mesmo_nome_vale_mais_no_com_br(self):
        """A ordem medida na disputa de setembro: com > ia/app > net/dev > outras > restritas."""
        nota = lambda d: pontuar(d, {"cabelo"}, set()).valor
        ordem = ["cabelo.com.br", "cabelo.ia.br", "cabelo.net.br", "cabelo.art.br", "cabelo.etc.br"]
        self.assertEqual(sorted(ordem, key=nota, reverse=True), ordem)
        self.assertEqual(len({nota(d) for d in ordem}), len(ordem))

    def test_nome_reposto_fora_do_pool_ganha_a_nota_de_hoje(self):
        """O instantaneo guarda a nota de quando o nome foi lido; a regra pode ter mudado."""
        from garimpo.adaptadores.repositorio import Repositorio
        from garimpo.casos import pool
        repo = Repositorio(":memory:")
        velho = Candidato(dominio="cabelo.etc.br", fonte="liberacao", nota=91,
                          motivos=("regra velha",), situacao=Situacao.LIVRE,
                          verificado_em="2026-09-30T10:00:00-03:00")
        repo.restaurar([velho])
        self.assertEqual(repo.renotar_historico(lambda d: pool.nota_de(d, ({"cabelo"}, set()))), 1)
        linha = repo.um("cabelo.etc.br")
        self.assertEqual(linha.nota, pontuar("cabelo.etc.br", {"cabelo"}, set()).valor)
        self.assertLess(linha.nota, 91)
        self.assertIs(linha.situacao, Situacao.LIVRE)   # a leitura fica

    def test_cnpj_so_no_com_br(self):
        from garimpo.dominio.relevancia import Lexico
        lexico = Lexico(negocios={"cabelo": 500}, nomes_de_empresa={"cabelo": 10})
        com = pontuar("cabelo.com.br", {"cabelo"}, set(), lexico=lexico)
        ia = pontuar("cabelo.ia.br", {"cabelo"}, set(), lexico=lexico)
        self.assertIn("palavra comum em nome de empresa", com.motivos)
        self.assertNotIn("palavra comum em nome de empresa", ia.motivos)

    def test_palavra_pouco_usada_vale_menos(self):
        """adau esta no dicionario, mas fora das 100 mil mais usadas: teto de 18."""
        from garimpo.dominio.relevancia import (Lexico, PESO_PALAVRA_PT, PESO_PALAVRA_RARA,
                                                PESO_POPULAR)
        lexico = Lexico(popularidade={"casa": 10, "adau": 150_000})
        casa = pontuar("casa.com.br", {"casa", "adau"}, set(), lexico=lexico)
        adau = pontuar("adau.com.br", {"casa", "adau"}, set(), lexico=lexico)
        self.assertEqual(casa.valor - adau.valor,
                         PESO_PALAVRA_PT - PESO_PALAVRA_RARA + PESO_POPULAR)
        self.assertIn("palavra de dicionário pouco usada", adau.motivos)
        # sem o wordfreq, fica o peso cheio
        self.assertEqual(pontuar("adau.com.br", {"adau"}, set()).valor,
                         pontuar("casa.com.br", {"casa"}, set()).valor)

    def test_desempate_poe_com_br_na_frente(self):
        from garimpo.dominio.relevancia import desempate_da_nota
        nomes = ["lima.ia.br", "lima.com.br", "limao.com.br", "ab.app.br"]
        self.assertEqual(sorted(nomes, key=desempate_da_nota),
                         ["lima.com.br", "limao.com.br", "ab.app.br", "lima.ia.br"])
        raiz = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(raiz, "site_modelo", "lista", "filtros.js")) as f:
            self.assertIn("desempateDaNota(a[D], b[D])", f.read())


# --------------------------------------------------------------------------
# serie historica das rodadas (Internet Archive)
# --------------------------------------------------------------------------
from datetime import date as _date
from garimpo.dominio import historico
from garimpo.web import graficos


def _lista(inicio, gerado, nomes, fim=True, cabecalho_latin1=False):
    linhas = [f"# Processo de liberação no período de {inicio}T15:00:00-03:00 a {inicio}T15:00:00-03:00",
              "# Mais informações em https://registro.br/dominio/processo-de-liberacao/",
              f"# Arquivo gerado em {gerado}"] + list(nomes)
    if fim:
        linhas.append("# Fim do arquivo")
    return "\n".join(linhas)


def _rodadas(por_mes: dict) -> dict:
    """{'2020-01': ['a.com.br', ...]} -> rodadas agrupadas, dia 8 de cada mes."""
    copias = [historico.ler_lista(_lista(f"{m}-08", f"{m}-06T10:00:00", n)) for m, n in por_mes.items()]
    return historico.agrupar_por_rodada(copias)


class TestHistoricoLeitura(unittest.TestCase):
    def test_latin1_e_cabecalho(self):
        bruto = _lista("2019-03-13", "2019-03-11T10:00:00", ["café.com.br", "Loja.com.br"]).encode("latin-1")
        c = historico.ler_lista(historico.decodificar(bruto))
        self.assertEqual(c.inicio, _date(2019, 3, 13))
        self.assertEqual(c.nomes, ("café.com.br", "loja.com.br"))
        self.assertTrue(c.completa)

    def test_mesma_rodada_em_dois_dias_vira_uma_so(self):
        """digest diferente nao e rodada diferente: vale a copia gerada por ultimo"""
        a = historico.ler_lista(_lista("2020-01-08", "2020-01-06T10:00:00", ["a.com.br"]))
        b = historico.ler_lista(_lista("2020-01-08", "2020-01-07T10:00:00", ["a.com.br", "b.com.br"]))
        rodadas = historico.agrupar_por_rodada([b, a])
        self.assertEqual(len(rodadas), 1)
        self.assertEqual(rodadas[_date(2020, 1, 8)].total_linhas, 2)

    def test_copia_truncada_e_descartada(self):
        truncada = historico.ler_lista(_lista("2020-01-08", "2020-01-06T10:00:00", ["a.com.br"], fim=False))
        self.assertEqual(historico.agrupar_por_rodada([truncada]), {})


class TestHistoricoSeries(unittest.TestCase):
    def setUp(self):
        self.r = _rodadas({
            "2020-01": ["trava.com.br", "x.com.br"],
            "2020-02": ["trava.com.br", "volta.com.br"],
            "2020-03": ["trava.com.br"],
            "2020-04": ["trava.com.br", "volta.com.br"],
            # 2020-05 sem copia
            "2020-06": ["volta.com.br"],
        })

    def test_intervalo_so_entre_meses_seguidos(self):
        s = historico.serie_de_tamanho(self.r)
        self.assertIsNone(s[0]["intervalo_dias"])
        self.assertEqual(s[1]["intervalo_dias"], 31)
        self.assertIsNone(s[-1]["intervalo_dias"], "junho vem depois de um mes sem copia")

    def test_sequencia_de_quatro_rodadas(self):
        seqs = {(n, k) for n, _, k in historico.sequencias(self.r)}
        self.assertIn(("trava.com.br", 4), seqs)
        self.assertIn(("x.com.br", 1), seqs)

    def test_episodios_nao_contam_mes_sem_copia_como_volta(self):
        ep = historico.episodios(self.r)
        self.assertEqual(ep["trava.com.br"], 1)
        # volta: jan nao, fev sim, mar nao, abr sim, jun sim (a rodada capturada anterior, abr, tinha o nome)
        self.assertEqual(ep["volta.com.br"], 2)

    def test_origem_dos_elegiveis_nas_tres_anteriores(self):
        lib = _rodadas({"2020-01": ["a.com.br", "b.com.br"], "2020-02": ["a.com.br", "b.com.br"],
                        "2020-03": ["a.com.br", "b.com.br"], "2020-04": ["a.com.br", "b.com.br"],
                        "2020-05": ["a.com.br", "b.com.br", "novo.com.br"]})
        ele = _rodadas({"2020-05": ["a.com.br", "novo.com.br"]})
        (linha,) = historico.origem_dos_elegiveis(lib, ele)
        self.assertEqual((linha["elegiveis"], linha["nas_3_anteriores"], linha["nas_4_anteriores"]), (2, 1, 1))


class TestHistoricoTermos(unittest.TestCase):
    def test_casamento_ancorado_evita_colisao(self):
        regras = historico.compilar_termos()
        self.assertTrue(historico.casa("vacinacovid19", regras["covid"]))
        self.assertFalse(historico.casa("ecovida", regras["covid"]), "covid no meio de ecovida")
        self.assertTrue(historico.casa("bitcoinbrasil", regras["cripto"]))
        self.assertFalse(historico.casa("criptografia", regras["cripto"]))
        self.assertFalse(historico.casa("alphabet", regras["apostas"]))
        self.assertFalse(historico.casa("zukunft", regras["NFT"]))


class TestGraficos(unittest.TestCase):
    def test_svg_respeita_a_csp_e_tem_dica(self):
        svg = graficos.colunas_no_tempo(
            [(_date(2020, 1, 8), 100, "g-s1", "jan: 100"), (_date(2020, 3, 8), 50, "g-s0", "mar <50>")], "t")
        self.assertNotIn("style=", svg, "CSP style-src 'self' bloqueia atributo style")
        self.assertIn("<title>jan: 100</title>", svg)
        self.assertIn("mar &lt;50&gt;", svg)
        self.assertEqual(svg.count("<path"), 2)

    def test_figura_leva_tabela_e_fonte(self):
        fig = graficos.figura("<svg></svg>", "Legenda.", "arquivo", graficos.tabela(["a", "b"], [[1, 2]], {1}))
        self.assertIn("<figcaption>Legenda.", fig)
        self.assertIn('<td class="num">2</td>', fig)
        self.assertIn("<details>", fig)

    def test_numeros_em_portugues(self):
        self.assertEqual(graficos.num(1234567), "1.234.567")
        self.assertEqual(graficos.compacto(163544), "164 mil")
        self.assertEqual(graficos.compacto(1500000), "1,5 mi")


class TestPaginaComGraficoEImagem(unittest.TestCase):
    def test_markdown_ignora_svg_e_mantem_tabela(self):
        from garimpo.web import paginas
        fig = graficos.figura(graficos.colunas([("a", 1, "g-s1", "a: 1")], "t"), "Legenda.", "fonte",
                              graficos.tabela(["x", "y"], [["a", 1]]),
                              [("g-s1", "5 semanas"), ("g-s0", "4 semanas")])
        md = paginas.html_para_markdown("<p>Antes.</p>" + fig)
        self.assertIn("5 semanas · 4 semanas", md, "itens da chave de cores colados")
        self.assertNotIn("<path", md)
        self.assertNotIn("a: 1", md, "texto de dica do SVG vazou para o Markdown")
        self.assertIn("| x | y |", md)
        self.assertIn("Legenda.", md)

    def test_imagem_propria_vai_para_og_e_json_ld(self):
        from garimpo.web import paginas
        texto = ("<!--\ntitulo: T\ndescricao: D\ntipo: artigo\nsecao: insights\n"
                 "data: 2026-09-13\nimagem: og/x.png\n-->\n<p>c</p>")
        p = paginas.ler_fragmento(texto, "insights/x")
        layout = "{{titulo}}{{descricao}}{{canonical}}{{nome}}{{nav}}{{classe_main}}{{cabecalho_extra}}{{regua}}{{miolo}}{{rodape}}{{jsonld}}{{og_tipo}}{{og_extra}}{{imagem}}{{scripts}}{{alternativo}}"
        html = paginas.render(p, layout, {}, "https://ex.br")
        self.assertIn("https://ex.br/og/x.png", html)
        self.assertNotIn("https://ex.br/og.png", html)


class TestPaginaDados(unittest.TestCase):
    """A aba Dados: numeros vivos do instantaneo e nenhum digito escrito a mao."""

    DADOS = {
        "status": ["LIBERACAO_LIVRE", "LIBERACAO_DISPUTADA", "COMPETITIVO"],
        "marcas": ["OK", "ATENCAO", "RISCO"],
        "itens": [
            ["a.com.br", 1, 2, 90, 0, [], 0, 0, 0, 0, 0, 0, 0],
            ["b.com.br", 1, 3, 90, 0, [], 0, 0, 0, 0, 0, 0, 0],
            ["marca.com.br", 1, 40, 90, 0, [], 2, 0, 0, 0, 0, 0, 0],
            ["c.com.br", 1, 12, 90, 0, [], 0, 0, 0, 0, 0, 0, 0],
            ["d.com.br", 0, 0, 90, 0, [], 0, 0, 0, 0, 0, 0, 0],
            ["e.com.br", 2, 3, 90, 1, [], 0, 1, 0, 0, 0, 0, 0],
        ],
    }

    def test_navegacao_tem_dados(self):
        from garimpo.web import paginas
        self.assertIn(("dados", "Dados"), paginas.NAVEGACAO)

    def test_grafico_de_candidatos(self):
        fig = graficos.figura_candidatos(self.DADOS)
        self.assertIn("Dos 4 nomes disputados, 50% têm só 2 ou 3", fig)
        self.assertIn("<td>10+</td><td class=\"num\">2</td>", fig)
        self.assertEqual(graficos.figura_candidatos({"status": ["LIVRE"], "itens": []}), "")

    def test_numeros_vivos_e_ranking_sem_marca(self):
        from garimpo.web import paginas
        molde = ('<strong id="p-disputados">-</strong><span id="p-verificados">-</span>'
                 '<div id="p-fig-candidatos"><p>espera</p></div><ol id="p-mais-disputados"><li>x</li></ol>')
        html = paginas.preencher_numeros(molde, self.DADOS)
        self.assertIn('<strong id="p-disputados">4</strong>', html)
        self.assertIn('<span id="p-verificados">6</span>', html)
        self.assertIn("<figure", html)
        self.assertNotIn("marca.com.br", html)
        self.assertLess(html.index("c.com.br"), html.index("b.com.br"))

    DISPUTAS = {"rodadas": {"2026-09-09": {
        "fim": "2026-09-16T15:00:00-03:00", "na_lista": 1000, "conferidos": 200,
        "nomes": {"a.com.br": [2, 0], "b.com.br": [3, 2], "c.com.br": [12, 0],
                  "marca.com.br": [40, 2], "fora.com.br": [50, 0]}}}}
    MOLDE = ('<section id="p-rodada"><h2 id="este-mes">A lista deste mês</h2>'
             '<p>A lista aberta. Estes números se atualizam.</p>'
             '<strong id="p-disputados">-</strong></section>')

    def _com(self, gerado_em: str, inicio: str = "2026-09-09T15:00:00-03:00",
             fim: str = "2026-09-16T15:00:00-03:00") -> dict:
        return dict(self.DADOS, gerado_em=gerado_em, rodada={"inicio": inicio, "fim": fim})

    def test_rodada_fechada_vem_da_base_e_nao_do_instantaneo(self):
        # depois do fechamento o instantaneo so ve os disputados que
        # sobraram (4 na rodada de setembro); a base guarda todos (311)
        from garimpo.web import paginas
        html = paginas.preencher_numeros(self.MOLDE, self._com("2026-09-18T01:00:00+00:00"),
                                         self.DISPUTAS)
        self.assertIn("Como terminou a rodada de setembro de 2026", html)
        self.assertNotIn("aberta.", html)
        self.assertNotIn("se atualizam", html)
        self.assertIn(">5</strong> nomes tiveram duas ou mais", html)
        self.assertIn("2,5% dos 200", html)
        self.assertIn(">2</strong> foram a leilão", html)
        self.assertIn(">3</strong> travaram", html)
        self.assertIn("abre em 14/10/2026", html)
        # ranking: do mais pedido, sem marca, e sem nome que o instantaneo nao confere
        self.assertLess(html.index("c.com.br</code>: 12 pedidos (travou, volta em outubro)"),
                        html.index("b.com.br</code>: 3 pedidos (foi a leilão)"))
        self.assertNotIn("marca.com.br</code>", html)
        self.assertNotIn("fora.com.br</code>", html)
        # marca.com.br (40) e fora.com.br (50) passam do ultimo da lista (b, 3)
        self.assertIn("fora os que parecem marca", html)
        self.assertIn("2 nomes com mais pedidos que o último", html)
        self.assertIn("<figure", html)

    def test_rodada_aberta_fica_com_o_bloco_vivo(self):
        from garimpo.web import paginas
        html = paginas.preencher_numeros(self.MOLDE, self._com("2026-09-12T12:00:00+00:00"),
                                         self.DISPUTAS)
        self.assertIn("A lista deste mês", html)
        self.assertIn('<strong id="p-disputados">4</strong>', html)

    def test_lista_nova_antes_de_abrir_mostra_a_rodada_anterior(self):
        # 12/10 a 14/10: o instantaneo ja e de outubro, a base ainda nao
        from garimpo.web import paginas
        dados = self._com("2026-10-13T12:00:00+00:00", "2026-10-14T15:00:00-03:00",
                          "2026-10-21T15:00:00-03:00")
        html = paginas.preencher_numeros(self.MOLDE, dados, self.DISPUTAS)
        self.assertIn("Como terminou a rodada de setembro de 2026", html)

    def test_rodada_fechada_sem_base_nao_inventa_numero(self):
        from garimpo.web import paginas
        html = paginas.preencher_numeros(self.MOLDE, self._com("2026-09-18T01:00:00+00:00"), {})
        self.assertIn("A rodada está fechada", html)
        self.assertNotIn("se atualizam", html)

    def test_ranking_reavalia_marca_mesmo_com_risco_gravado_desatualizado(self):
        """S18: a varredura que
        gravou o instantaneo e anterior a lista fixa de hoje, e o h3 promete
        "fora os que parecem marca" sem depender de uma nova varredura."""
        from garimpo.web import paginas
        dados = dict(self.DADOS, itens=self.DADOS["itens"] + [
            ["brasiltelecom.com.br", 1, 35, 90, 0, [], 0, 0, 0, 0, 0, 0, 0]])  # marca[0]="OK", gravado antes do fix
        disputas = {"rodadas": {"2026-09-09": dict(
            self.DISPUTAS["rodadas"]["2026-09-09"],
            nomes=dict(self.DISPUTAS["rodadas"]["2026-09-09"]["nomes"],
                      **{"brasiltelecom.com.br": [35, 0]}))}}
        html = paginas.rodada_da_pagina(
            dict(dados, gerado_em="2026-09-18T01:00:00+00:00",
                rodada={"inicio": "2026-09-09T15:00:00-03:00", "fim": "2026-09-16T15:00:00-03:00"}),
            disputas)
        self.assertNotIn("brasiltelecom.com.br</code>", html)
        # brasiltelecom (35) some da lista; o "ficaram de fora" sobe em 1
        self.assertIn("3 nomes com mais pedidos que o último", html)

    def test_json_ld_da_pagina_dados_tem_dataset(self):
        from garimpo.web import paginas
        p = paginas.ler_fragmento("<!--\ntitulo: T\ndescricao: D\ntipo: pagina\n-->\n<p>c</p>", "dados")
        ld = paginas.json_ld(p, "https://ex.br")
        self.assertIn("https://ex.br/dados/historico/rodadas.json", ld)
        self.assertIn("https://ex.br/dados/historico/disputas.json", ld)
        self.assertIn('"Dataset"', ld)


class TestCalendario(unittest.TestCase):
    """As datas da rodada pela regra, e a previsao de volta (S14)."""

    def test_segunda_quarta_as_15h(self):
        from garimpo.dominio import calendario as c
        self.assertEqual(c.abertura(2026, 9).isoformat(), "2026-09-09T15:00:00-03:00")
        self.assertEqual(c.abertura(2026, 10).isoformat(), "2026-10-14T15:00:00-03:00")
        self.assertEqual(c.abertura(2026, 11).isoformat(), "2026-11-11T15:00:00-03:00")
        # mes que comeca numa quarta: a segunda quarta e o dia 8
        self.assertEqual(c.abertura(2025, 1).day, 8)
        abre = c.abertura(2026, 10)
        self.assertEqual(c.saida_da_lista(abre).isoformat(), "2026-10-12")
        self.assertEqual(c.fechamento(abre).isoformat(), "2026-10-21T15:00:00-03:00")

    def test_proxima_abertura_e_estrita(self):
        from garimpo.dominio import calendario as c
        abre = c.abertura(2026, 9)
        self.assertEqual(c.proxima_abertura(abre).month, 10)
        um_minuto_antes = abre - __import__("datetime").timedelta(minutes=1)
        self.assertEqual(c.proxima_abertura(um_minuto_antes), abre)
        self.assertEqual(c.mes_seguinte(c.abertura(2026, 12)).isoformat(), "2027-01-13T15:00:00-03:00")

    def test_previsao_de_volta_bate_com_a_publicada(self):
        # um nome que vence em 15/07/2026 deve voltar na rodada de
        # 09/12/2026
        from garimpo.dominio import calendario as c
        provavel, seguinte = c.previsao_de_volta(_date(2026, 7, 15))
        self.assertEqual(provavel.date().isoformat(), "2026-12-09")
        self.assertEqual(seguinte.date().isoformat(), "2027-01-13")

    def test_aberturas_para_o_navegador(self):
        from garimpo.dominio import calendario as c
        datas = c.aberturas(_date(2026, 9, 14), 1, 2)
        self.assertEqual(datas, ["2026-08-12", "2026-09-09", "2026-10-14", "2026-11-11"])

    def test_pagina_recebe_o_calendario_do_instantaneo(self):
        from garimpo.web import paginas
        dados = {"gerado_em": "2026-09-14T03:00:00+00:00", "itens": [],
                 "rodada": {"inicio": "2026-09-09T15:00:00-03:00", "fim": "2026-09-16T15:00:00-03:00"}}
        html = paginas.preencher_numeros('<script type="application/json" id="p-calendario">{}</script>', dados)
        corpo = re.search(r">(.*)</script>", html).group(1)
        cal = json.loads(corpo)
        self.assertIn("2026-10-14", cal["aberturas"])
        self.assertEqual(cal["meses_ate_a_lista"], 5)
        self.assertEqual(cal["dias_lista_antes"], 2)
        self.assertEqual([d.date().isoformat() for d in paginas.rodadas_para_lembrete(dados, 2)],
                         ["2026-10-14", "2026-11-11"])
        self.assertEqual(paginas.proxima_rodada("2026-09-09T15:00:00-03:00"), "14/10/2026")


class TestPassagens(unittest.TestCase):
    """O indice da ficha: fatia por hash, igual no Python e no navegador."""

    def test_hash_conhecido(self):
        from garimpo.dominio import passagens
        # vetores de referencia do FNV-1a de 32 bits
        self.assertEqual(passagens.fnv1a32(""), 0x811C9DC5)
        self.assertEqual(passagens.fnv1a32("a"), 0xE40C292C)
        self.assertEqual(passagens.fatia("A.com.br "), passagens.fatia("a.com.br"))

    def test_mesmo_hash_no_ficha_js(self):
        import shutil
        from garimpo.dominio import passagens
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = open(os.path.join(os.path.dirname(__file__), "site_modelo", "ficha.js"), encoding="utf-8").read()
        funcao = re.search(r"function fnv1a32\(texto\) \{.*?\n  \}", js, re.S).group(0)
        nomes = ["galoegolo.com.br", "café.com.br", "xn--caf-dma.com.br", "a"]
        saida = subprocess.run([node, "-e", funcao + f"; for (const n of {json.dumps(nomes)}) process.stdout.write(String(fnv1a32(n)) + ' ')"],
                               capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual([int(x) for x in saida], [passagens.fnv1a32(n) for n in nomes])

    def test_montar_so_quem_passou_duas_vezes(self):
        from garimpo.dominio import passagens
        rod = {
            _date(2026, 7, 8): historico.Rodada(_date(2026, 7, 8), None, "", frozenset({"a.com.br", "b.com.br"})),
            _date(2026, 8, 12): historico.Rodada(_date(2026, 8, 12), None, "", frozenset({"a.com.br"})),
            _date(2026, 9, 9): historico.Rodada(_date(2026, 9, 9), None, "", frozenset({"a.com.br", "c.com.br"})),
        }
        datas, fatias = passagens.montar(rod)
        self.assertEqual(datas, ["2026-07-08", "2026-08-12", "2026-09-09"])
        linhas = [l for ls in fatias.values() for l in ls]
        self.assertEqual(linhas, ["a.com.br\t0,1,2"])
        self.assertIn(passagens.fatia("a.com.br"), fatias)

    def test_marca_a_rodada_em_que_era_elegivel(self):
        from garimpo.dominio import passagens
        d = [_date(2026, 7, 8), _date(2026, 8, 12), _date(2026, 9, 9)]
        rod = {x: historico.Rodada(x, None, "", frozenset({"a.com.br"})) for x in d}
        ele = {d[2]: historico.Rodada(d[2], None, "", frozenset({"a.com.br"}))}
        datas, fatias = passagens.montar(rod, elegiveis=ele)
        self.assertEqual([l for ls in fatias.values() for l in ls], ["a.com.br\t0,1,2e"])
        # sem copia da lista de elegiveis, "nao era" e "nao sabemos" se separam aqui
        self.assertEqual(passagens.com_elegiveis(datas, ele), [2])

    def test_ficha_le_o_rdap_depois_do_fechamento(self):
        """Nome travado e leilao com a rodada fechada nao sao 'registrado'."""
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = open(os.path.join(os.path.dirname(__file__), "site_modelo", "ficha.js"), encoding="utf-8").read()
        funcao = re.search(r"function classificar\(res, exibe\) \{.*?\n  \}", js, re.S).group(0)
        vazio = {"objectClassName": "domain", "handle": "exemplo.com.br", "ldhName": "exemplo.com.br"}
        leilao = {"objectClassName": "domain", "status": ["pending create"],
                  "publicIds": [{"type": "ticket", "identifier": "1"}, {"type": "ticket", "identifier": "2"}]}
        casos = [
            {"status": 200, "recurso": "release-process-waiting", "json": vazio},
            {"status": 200, "recurso": "competitive-release-process-closed;date=2026-09-16T18:00:00Z", "json": leilao},
            {"status": 200, "recurso": "competitive-release-process-running;date=2026-09-16T18:00:00Z", "json": leilao},
            {"status": 200, "recurso": "release-process-running;date=2026-09-16T18:00:00Z", "json": leilao},
            {"status": 200, "recurso": "release-process-closed;date=2026-09-16T18:00:00Z", "json": leilao},
        ]
        saida = subprocess.run([node, "-e", "const foraDaRegra = () => false;" + funcao
                                + f"; for (const c of {json.dumps(casos)}) console.log(JSON.stringify(classificar(c, '')))"],
                               capture_output=True, text=True, check=True).stdout.splitlines()
        r = [json.loads(l) for l in saida]
        self.assertEqual(r[0]["tipo"], "travado")
        self.assertEqual((r[1]["tipo"], r[1]["tickets"], r[1]["fim"]), ("leilao", 2, "2026-09-16T18:00:00.000Z"))
        self.assertEqual(r[2]["tipo"], "leilao")
        self.assertEqual(r[3]["tipo"], "rodada")
        # rodada normal fechada nao foi vista: nao vira "rodada aberta"
        self.assertEqual(r[4]["tipo"], "pendente")

    def test_conferencia_ao_vivo_le_o_rdap_depois_do_fechamento(self):
        """O app nao chama de REGISTRADO um nome travado nem um leilao fechado."""
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = _fonte_da_lista()
        funcao = re.search(r"async function consultarRdap\(dominio\) \{.*?\n\}", js, re.S).group(0)
        # consultarRdap delega a window.Disputa.consultar (disputa.js), que ja
        # devolve {status, recurso, json}; so essa fronteira precisa de mock aqui.
        roteiro = (funcao + """
const respostas = {
  'exemplo.com.br': [200, 'release-process-waiting', {objectClassName: 'domain'}],
  'carro.com.br': [200, 'competitive-release-process-closed;date=2026-09-16T18:00:00Z',
                    {objectClassName: 'domain', publicIds: [{type: 'ticket'}, {type: 'ticket'}]}],
  'liberados.com.br': [200, '', {objectClassName: 'domain', status: ['active']}],
};
globalThis.window = { Disputa: { consultar: async (caminho) => {
  const [status, recurso, json] = respostas[caminho.replace('domain/', '')];
  return { status, recurso, json };
} } };
(async () => { for (const d of Object.keys(respostas)) console.log(JSON.stringify(await consultarRdap(d))); })();
""")
        saida = subprocess.run([node, "-e", roteiro], capture_output=True, text=True, check=True).stdout.splitlines()
        r = [json.loads(l) for l in saida]
        self.assertEqual(r[0], {"situacao": "AGUARDANDO_LIBERACAO", "candidatos": 0})
        self.assertEqual(r[1], {"situacao": "COMPETITIVO", "candidatos": 2})
        self.assertEqual(r[2]["situacao"], "REGISTRADO")

    def test_sequencia_da_ficha(self):
        """Tres travas seguidas: leilao. Mes sem lista guardada nao prova ausencia."""
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = open(os.path.join(os.path.dirname(__file__), "site_modelo", "ficha.js"), encoding="utf-8").read()
        funcao = re.search(r"function sequencia\(rodadas, linha, mes, naLista\) \{.*?\n  \}", js, re.S).group(0)
        jul_a_set = ["2026-07-08", "2026-08-12", "2026-09-09"]
        sem_julho = ["2026-06-10", "2026-08-12", "2026-09-09"]
        casos = [
            (jul_a_set, "0,1,2", "2026-09", True),     # terceira trava: leilao
            (jul_a_set, "1,2", "2026-09", True),       # segunda: rodada normal, falta uma
            (jul_a_set, None, "2026-09", True),        # primeira vez (fora do indice, set indexado)
            (sem_julho, "1,2", "2026-09", True),       # julho sem copia: incerta
            (jul_a_set, None, "2026-10", True),        # outubro fora do indice: set nao prova nada
            (["2026-08-12", "2026-09-09"], "0e,1", "2026-09", True),   # elegivel em ago: +3
            (jul_a_set, None, "2026-09", False),       # nem na lista
            (["2026-08-12", "2026-09-09"], "0,1e", "2026-09", True),   # elegivel agora: jul provado
        ]
        saida = subprocess.run([node, "-e", funcao + f"; for (const c of {json.dumps(casos)}) "
                                "console.log(JSON.stringify(sequencia(...c)))"],
                               capture_output=True, text=True, check=True).stdout.splitlines()
        r = [json.loads(l) for l in saida]
        self.assertEqual((r[0]["seguidas"], r[0]["proxima"]), (3, "leilao"))
        self.assertEqual((r[1]["seguidas"], r[1]["proxima"], r[1]["faltam"], r[1]["piso"]), (2, "rodada", 1, False))
        self.assertEqual((r[2]["seguidas"], r[2]["proxima"], r[2]["faltam"]), (1, "rodada", 2))
        self.assertEqual((r[3]["proxima"], r[3]["falta"]), ("incerta", {"mes": "2026-07", "motivo": "sem-copia"}))
        # o que falta no indice e outubro, a rodada de agora, e nao setembro
        self.assertEqual((r[4]["proxima"], r[4]["falta"]), ("incerta", {"mes": "2026-10", "motivo": "indice"}))
        self.assertEqual((r[5]["seguidas"], r[5]["proxima"], r[5]["piso"]), (5, "leilao", True))
        self.assertEqual(r[6]["proxima"], "nenhuma")
        self.assertEqual((r[7]["seguidas"], r[7]["proxima"]), (4, "leilao"))


class TestArquivoDasRodadas(unittest.TestCase):
    """A base propria: listas guardadas uma vez e contagem que so cresce."""

    @staticmethod
    def _dados(itens, inicio="2026-09-09T15:00:00-03:00", gerado="2026-09-10T00:00:00+00:00"):
        return {"rodada": {"inicio": inicio, "fim": "2026-09-16T15:00:00-03:00"},
                "gerado_em": gerado, "total_rodada": 125453,
                "status": [s.value for s in instantaneo.SITUACOES], "itens": itens}

    @staticmethod
    def _item(nome, situacao, candidatos, elegivel=0, em_leilao=0):
        return [nome, instantaneo.SITUACOES.index(situacao), candidatos, 50, elegivel, [], 0,
                em_leilao, 0, 0, 0, 0, 0]

    def test_acumula_o_maior_e_a_fase_mais_adiantada(self):
        from garimpo.casos import arquivo_das_rodadas as arq
        s = Situacao
        base = arq.acumular(None, self._dados([
            self._item("a.com.br", s.LIBERACAO_DISPUTADA, 3),
            self._item("b.com.br", s.LIBERACAO_LIVRE, 0),            # sem disputa: fora
            self._item("c.com.br", s.COMPETITIVO, 2, elegivel=1, em_leilao=1),
        ]))
        # depois da rodada a varredura ve o nome sem candidato: a contagem nao cai
        base = arq.acumular(base, self._dados([
            self._item("a.com.br", s.AGUARDANDO_LIBERACAO, 0),
            self._item("c.com.br", s.COMPETITIVO, 5, elegivel=1, em_leilao=1),
        ], gerado="2026-09-17T00:00:00+00:00"))
        rodada = base["rodadas"]["2026-09-09"]
        self.assertEqual(rodada["nomes"], {"a.com.br": [3, 0], "c.com.br": [5, 2]})
        self.assertEqual((rodada["conferidos"], rodada["na_lista"]), (3, 125453))
        self.assertEqual(json.loads(arq.serializar(base)), base)
        texto = json.dumps(base)
        self.assertNotIn("ticket_", texto)

    def test_nada_mudou_nada_grava(self):
        from garimpo.casos import arquivo_das_rodadas as arq
        dados = self._dados([self._item("a.com.br", Situacao.LIBERACAO_DISPUTADA, 2)])
        with tempfile.TemporaryDirectory() as d:
            caminho = os.path.join(d, "disputas.json")
            self.assertTrue(arq.atualizar_disputas(caminho, dados))
            self.assertFalse(arq.atualizar_disputas(caminho, dict(dados, gerado_em="2026-09-11T00:00:00+00:00")))

    def test_lista_guardada_uma_vez_e_so_completa(self):
        import gzip
        from garimpo.casos import arquivo_das_rodadas as arq
        cabecalho = ("# Processo de liberação no período de 2026-10-14T15:00:00-03:00 a 2026-10-21T15:00:00-03:00\n"
                     "# Arquivo gerado em 2026-10-12T10:00:00-03:00\n")
        with tempfile.TemporaryDirectory() as d:
            trabalho, pasta = os.path.join(d, "work"), os.path.join(d, "listas")
            os.makedirs(trabalho)
            with open(os.path.join(trabalho, "liberacao.txt"), "w", encoding="utf-8") as f:
                f.write(cabecalho + "a.com.br\n")                    # truncada: sem fim
            with open(os.path.join(trabalho, "competitivo.txt"), "w", encoding="utf-8") as f:
                f.write(cabecalho + "b.com.br\n# Fim do arquivo\n")
            self.assertEqual([os.path.basename(c) for c in arq.guardar_listas(trabalho, pasta)],
                             ["2026-10-14-elegiveis.txt.gz"])
            with open(os.path.join(trabalho, "liberacao.txt"), "a", encoding="utf-8") as f:
                f.write("# Fim do arquivo\n")
            self.assertEqual([os.path.basename(c) for c in arq.guardar_listas(trabalho, pasta)],
                             ["2026-10-14-liberacao.txt.gz"])
            self.assertEqual(arq.guardar_listas(trabalho, pasta), [])
            with open(os.path.join(pasta, "2026-10-14-liberacao.txt.gz"), "rb") as f:
                bruto = f.read()
            self.assertEqual(gzip.decompress(bruto).decode("utf-8").splitlines()[2], "a.com.br")
            self.assertEqual(bruto, arq.gzip_estavel(cabecalho + "a.com.br\n# Fim do arquivo\n"))


class TestLembretesDeRodada(unittest.TestCase):
    """O .ics da proxima rodada, que sobrevive quando nao ha leilao."""

    def test_dois_eventos_deterministicos(self):
        from garimpo.casos import lembretes
        from garimpo.dominio import calendario
        texto = lembretes.ics_da_rodada(calendario.abertura(2026, 10))
        linhas = texto.split("\r\n")
        self.assertIn("DTSTART;VALUE=DATE:20261012", linhas)
        self.assertIn("DTSTART:20261014T180000Z", linhas)
        self.assertEqual(texto.count("BEGIN:VEVENT"), 2)
        self.assertTrue(all(len(l.encode("utf-8")) <= 75 for l in linhas))
        self.assertEqual(texto, lembretes.ics_da_rodada(calendario.abertura(2026, 10)))

    def test_rodadas_depois_da_pasta_refeita(self):
        import tempfile
        from garimpo.casos import lembretes
        from garimpo.dominio import calendario
        with tempfile.TemporaryDirectory() as d:
            pasta = os.path.join(d, "lembretes")
            lembretes.escrever(pasta, [], None)          # sem leilao: apaga a pasta
            n = lembretes.escrever_rodadas(pasta, [calendario.abertura(2026, 10),
                                                   calendario.abertura(2026, 11)])
            self.assertEqual(n, 2)
            self.assertEqual(sorted(os.listdir(os.path.join(pasta, "rodadas"))),
                             ["2026-10-14.ics", "2026-11-11.ics"])


class TestConferenciaAoVivo(unittest.TestCase):
    """app.js: nao reconferir ao vivo quem acabou de ser conferido."""

    def test_recente_conferido_respeita_rever_minutos(self):
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = _fonte_da_lista()
        # o indice de VF no array compacto e a janela de REVER_MINUTOS, exatamente como app.js declara
        vf = int(re.search(r"const D = 0, SS = 1, C = 2, N = 3, E = 4, M = 5, MK = 6, EL = 7, VF = (\d+)", js).group(1))
        rever = int(re.search(r"const REVER_MINUTOS = (\d+);", js).group(1))
        funcao = re.search(r"function recenteConferido\(it\) \{.*?\n\}", js, re.S).group(0)
        roteiro = (f"const VF = {vf}; const REVER_MINUTOS = {rever};"
                   + "const agoraSeg = () => 1000000;" + funcao + """
const it = (vf) => { const a = []; a[VF] = vf; return a; };
process.stdout.write(JSON.stringify([
  recenteConferido(it(1000000 - 60)),             // 1 min atras: recente
  recenteConferido(it(1000000 - REVER_MINUTOS * 60 + 1)),  // dentro da janela, por 1s
  recenteConferido(it(1000000 - REVER_MINUTOS * 60)),      // no limite: nao e mais recente
  recenteConferido(it(1000000 - REVER_MINUTOS * 60 - 1)),  // passou da janela
  recenteConferido(it(0)),                          // nunca conferido (VF ausente)
  recenteConferido(it(undefined)),
]));""")
        saida = subprocess.run([node, "-e", roteiro], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(saida), [True, True, False, False, False, False])

    def test_etiqueta_elegivel_conta_o_desfecho_com_a_rodada_fechada(self):
        """'elegivel' num nome ja registrado, rodada fechada, seria lido como
        'ainda em leilao, da para entrar'. O desfecho e o proprio selo
        ('leilao encerrado', cinza, sem a cor de convite), nao uma segunda
        etiqueta; fechada, a etiqueta 'elegivel' nao aparece em nome nenhum.
        Os motivos da nota nao ficam na linha; so 'extensao restrita' fica,
        como aviso (avisoDeRestricao)."""
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = _fonte_da_lista()
        pegar = lambda nome: re.search(r"function " + nome + r"\(.*?\n\}", js, re.S).group(0)
        roteiro = """
const SS = 1, E = 4, M = 5;
const iLeilao = 0, iRegistrado = 1, iLivre = 2;
const NAO_VERIFICADO = -1;
const SELOS = { REGISTRADO: ['registrado', 'registrado'], LIVRE: ['livre', 'livre agora'] };
const SELOS_FECHADA = {};
const esc = (x) => x;
const estado = { dados: { status: ['COMPETITIVO', 'REGISTRADO', 'LIVRE'],
  motivos: ['nome curto', 'elegível ao leilão', 'sigla de 3 letras', 'três letras .com.br',
            'extensão ia.br', 'extensão restrita: só pessoa física'] } };
let fechada = false;
const rodadaFechada = () => fechada;
""" + "\n".join(pegar(n) for n in ("etiquetaElegivel", "selo", "avisoDeRestricao")) + """
const linha = (ss, e, m) => { const it = []; it[SS] = ss; it[E] = e; it[M] = m || [0, 1]; return it; };
const r = [];
r.push(etiquetaElegivel(linha(iLivre, 1)));          // aberta: convite
r.push(etiquetaElegivel(linha(iLeilao, 1)));         // aberta, em leilao: nada
fechada = true;
r.push(etiquetaElegivel(linha(iRegistrado, 1)));     // fechada: nada ao lado do nome
r.push(selo(linha(iRegistrado, 1)));                 // fechada: o selo conta
r.push(selo(linha(iRegistrado, 0)));                 // nao elegivel: registrado
r.push(avisoDeRestricao(linha(iLivre, 0, [0, 4])));  // extensao comum: nada
r.push(avisoDeRestricao(linha(iLivre, 0, [2, 5])));  // restrita: o aviso
process.stdout.write(JSON.stringify(r));
"""
        saida = subprocess.run([node, "-e", roteiro], capture_output=True, text=True, check=True).stdout
        aberta, em_leilao, fechada, selo_el, selo_nao, sem_aviso, restrita = json.loads(saida)
        self.assertIn(">elegível<", aberta)
        self.assertEqual(em_leilao, "")
        self.assertEqual(fechada, "")
        self.assertIn('class="selo registrado">leilão encerrado', selo_el)
        self.assertIn("já tem dono", selo_el)
        self.assertIn(">registrado<", selo_nao)
        self.assertEqual(sem_aviso, "")
        self.assertIn('class="aviso-restrita">extensão restrita: só pessoa física<', restrita)

    def test_conferir_ao_abrir_pula_quem_e_recente_mas_nao_a_lista_compartilhada(self):
        js = _fonte_da_lista()
        corpo = re.search(r"function conferirAoAbrir\(\) \{.*?\n\}", js, re.S).group(0)
        self.assertIn(".filter((it) => !recenteConferido(it))", corpo)
        # a lista compartilhada (?lista=) fica fora do filtro: quem abriu o link quer o numero fresco
        linha_compartilhada = next(l for l in corpo.splitlines() if "compartilhada =" in l)
        self.assertNotIn("recenteConferido", linha_compartilhada)

    def test_localstorage_ate_recente_conferido_a_cadeia_inteira(self):
        # nao basta o predicado isolado. O bug real era a
        # cadeia localStorage -> lerConferidos -> restaurarConferido -> it[VF]
        # -> recenteConferido nunca se encontrarem numa pagina recem-aberta
        # (estado.aoVivo, em memoria, some a cada load; quem sobrevive e o
        # localStorage). Roda as 3 funcoes reais do app.js encadeadas, com um
        # localStorage falso: e o mais perto de "abrir a inicial de novo" que
        # da para fazer sem navegador e sem tocar o Registro.br.
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = _fonte_da_lista()
        d_idx, ss_idx, vf_idx, el_idx = 0, 1, 8, 7
        assert re.search(r"const D = 0, SS = 1, C = 2, N = 3, E = 4, M = 5, MK = 6, EL = 7, VF = 8, CL = 9", js)
        rever = int(re.search(r"const REVER_MINUTOS = (\d+);", js).group(1))
        chave = re.search(r"const CHAVE_CONFERIDOS = '([^']+)';", js).group(1)
        validade = re.search(r"const VALIDADE_CONFERIDO = ([^;]+);", js).group(1)
        ler_conferidos = re.search(r"function lerConferidos\(\) \{.*?\n\}", js, re.S).group(0)
        restaurar_conferido = re.search(r"function restaurarConferido\(it\) \{.*?\n\}", js, re.S).group(0)
        recente_conferido = re.search(r"function recenteConferido\(it\) \{.*?\n\}", js, re.S).group(0)
        roteiro = f"""
const D = {d_idx}, SS = {ss_idx}, C = 2, VF = {vf_idx}, EL = {el_idx};
const REVER_MINUTOS = {rever};
const CHAVE_CONFERIDOS = {json.dumps(chave)};
const VALIDADE_CONFERIDO = {validade};
const agoraSeg = () => 2000000;
const estado = {{ dados: {{ status: ['LIVRE', 'REGISTRADO'] }}, conferidos: new Map(), doNavegador: new Set() }};
const localStorage = {{
  _v: {{}},
  getItem(k) {{ return Object.prototype.hasOwnProperty.call(this._v, k) ? this._v[k] : null; }},
  setItem(k, v) {{ this._v[k] = v; }},
}};
// uma conferencia feita 3 min atras, guardada no aparelho na visita anterior
localStorage.setItem(CHAVE_CONFERIDOS, JSON.stringify({{ 'ex.com.br': {{ s: 'LIVRE', c: 0, t: 2000000 - 180 }} }}));
{ler_conferidos}
{restaurar_conferido}
{recente_conferido}
estado.conferidos = lerConferidos();          // como no boot: le o localStorage
const it = []; it[D] = 'ex.com.br'; it[SS] = 0; it[VF] = 0; it[EL] = 0;
restaurarConferido(it);                       // como no boot: aplica a cada linha
process.stdout.write(JSON.stringify({{ tamanho: estado.conferidos.size, vf: it[VF], recente: recenteConferido(it) }}));
"""
        saida = subprocess.run([node, "-e", roteiro], capture_output=True, text=True, check=True).stdout
        r = json.loads(saida)
        self.assertEqual(r["tamanho"], 1)             # lerConferidos aceitou o registro
        self.assertEqual(r["vf"], 2000000 - 180)       # restaurarConferido propagou o carimbo
        self.assertTrue(r["recente"])                  # e recenteConferido reconhece: nao reconfere

    def test_conferencia_nao_duplica_a_mensagem_do_bloqueio(self):
        # com window.Disputa bloqueado, e.message ja e "Não deu
        # para conferir agora...", e o prefixo generico ("A conferência
        # parou:") duplicaria a frase e deixaria ".." no meio.
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = _fonte_da_lista()
        processar = re.search(r"async function processarFila\(\) \{.*?\n\}", js, re.S).group(0)
        catch = re.search(r"\} catch \(e\) \{.*?\n    \}", processar, re.S).group(0)
        # bloqueado: e.message ja e a mensagem completa, sem o prefixo generico
        for bloqueado, tem_prefixo_generico in ((True, False), (False, True)):
            roteiro = f"""
let fila = {{}};
let avisado = null;
const avisar = (t) => {{ avisado = t; }};
const window = {{ Disputa: {{ bloqueado: () => {str(bloqueado).lower()} }} }};
const erroLancado = {{ message: 'Não deu para conferir agora; tente de novo em 5 min.' }};
try {{ throw erroLancado; {catch}
process.stdout.write(JSON.stringify(avisado));
"""
            saida = subprocess.run([node, "-e", roteiro], capture_output=True, text=True, check=True).stdout
            avisado = json.loads(saida)
            self.assertEqual(avisado.startswith("A conferência parou:"), tem_prefixo_generico)
            if not tem_prefixo_generico:      # bloqueado: a frase pronta ja termina em ".", sem duplicar
                self.assertNotIn("..", avisado)


class TestFicha(unittest.TestCase):
    """A pagina /quando-volta/: navegacao, espelho sem formulario, scripts."""

    def test_aba_e_espelho(self):
        from garimpo.web import paginas
        self.assertIn(("quando-volta", "Quando volta"), paginas.NAVEGACAO)
        modelo = os.path.join(os.path.dirname(__file__), "site_modelo")
        pagina = next(p for p in paginas.carregar(modelo) if p.slug == "quando-volta")
        self.assertEqual(pagina.scripts, ("disputa.js", "agenda.js", "ficha.js"))
        md = paginas.markdown_da_pagina(pagina, {})
        self.assertNotIn("Consultar", md)             # o formulario nao vira texto
        self.assertIn("/quando-volta/?d=nome.com.br", md)

    def test_ficha_nunca_le_dado_pessoal_alem_do_nome(self):
        js = open(os.path.join(os.path.dirname(__file__), "site_modelo", "ficha.js"), encoding="utf-8").read()
        codigo = "\n".join(l for l in js.splitlines() if not l.strip().startswith(("*", "//", "/*")))
        for proibido in ("legalRepresentative", "publicIds.find", "email", "adr"):
            self.assertNotIn(proibido, codigo)

    def test_nome_fora_da_regra_nao_e_livre(self):
        # a.com.br responde 404 no RDAP como um nome livre, mas e "Dominio invalido"
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = open(os.path.join(os.path.dirname(__file__), "site_modelo", "ficha.js"), encoding="utf-8").read()
        funcao = re.search(r"function foraDaRegra\(exibe\) \{.*?\n  \}", js, re.S).group(0)
        nomes = ["a.com.br", "ab.com.br", "123.com.br", "2a.com.br", "é.com.br", "café.com.br",
                 "a" * 26 + ".com.br", "a" * 27 + ".com.br"]
        saida = subprocess.run([node, "-e", funcao + f"; process.stdout.write(JSON.stringify({json.dumps(nomes)}.map(foraDaRegra)))"],
                               capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(saida), [True, False, True, False, True, False, False, True])

    def test_resumo_da_ficha_na_lista(self):
        """O "i" da linha abre o resumo da ficha (Ficha.abrir); o nome segue levando ao Registro.br."""
        from garimpo.web import paginas
        modelo = os.path.join(os.path.dirname(__file__), "site_modelo")
        ferramenta = next(p for p in paginas.carregar(modelo) if p.tipo == "ferramenta")
        # classico, antes do modulo da lista: a linha pergunta por window.Ficha ao se
        # desenhar; e depois do disputa.js, que faz a consulta ao RDAP
        self.assertLess(ferramenta.scripts.index("disputa.js"), ferramenta.scripts.index("ficha.js"))
        self.assertLess(ferramenta.scripts.index("ficha.js"), ferramenta.scripts.index("lista/app.js"))
        self.assertIn('id="p-calendario"', ferramenta.corpo)
        js = open(os.path.join(modelo, "ficha.js"), encoding="utf-8").read()
        self.assertRegex(js, r"window\.Ficha = \{[^}]*\babrir\b")
        # Ctrl, Cmd, Shift e o botao do meio seguem o link, como qualquer link
        ouvinte = re.search(r"closest\('\[data-ficha\]'\).*?abrir\(", js, re.S).group(0)
        for condicao in ("e.button !== 0", "e.metaKey", "e.ctrlKey", "e.shiftKey", "e.altKey"):
            self.assertIn(condicao, ouvinte)
        linha = open(os.path.join(modelo, "lista", "linha.js"), encoding="utf-8").read()
        botao = re.search(r"function botaoFicha\(it\) \{.*?\n\}", linha, re.S).group(0)
        self.assertIn('data-ficha="${nome}" aria-haspopup="dialog"', botao)
        # o nome segue levando direto ao Registro.br; o leitor de tela ouve
        # para onde vai pela descricao, que nao entra no texto copiado
        self.assertIn('<a href="${url}" target="_blank" rel="noopener noreferrer" '
                      'aria-describedby="nota-nome-registro">${dominio}</a>${botaoFicha(it)}', linha)
        ferramenta = open(os.path.join(modelo, "conteudo", "ferramenta.html"), encoding="utf-8").read()
        self.assertIn('<span id="nota-nome-registro" hidden>abre no Registro.br, em nova aba</span>',
                      ferramenta)
        # o resumo so tem o que e daquele nome: nenhum texto que valeria para qualquer um
        resumo = js[js.index("// ------------------------------------------------------------ resumo"):
                    js.index("window.Ficha = {")]
        for generico in ("Estimativa pelo histórico", "Alguns nomes são reservados",
                         "Contagem do próprio Liberados", "R$ 40", "Como calculamos"):
            self.assertNotIn(generico, resumo)

    def test_ancora_nao_vira_consulta(self):
        # regressao: /quando-volta/#travou consultava o RDAP de "travou.com.br".
        # So ?d= pede consulta; o hash fica so para os ids das secoes (compartilhar.js).
        js = open(os.path.join(os.path.dirname(__file__), "site_modelo", "ficha.js"), encoding="utf-8").read()
        self.assertNotIn("location.hash", js)
        pedido = re.search(r"const pedido = .*?;", js, re.S).group(0)
        self.assertEqual(pedido, "const pedido = new URLSearchParams(location.search).get('d');")


class TestSeuNome(unittest.TestCase):
    """/seu-nome/: ate tres .com.br do nome, pela ficha, com pausa."""

    RAIZ = os.path.dirname(os.path.abspath(__file__))

    def node(self, roteiro):
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        # relogio falso: setTimeout avanca o relogio e dispara na hora, entao
        # as pausas de 2,5 s viram numeros e o teste nao espera nada. fetch
        # falso anota toda URL (nenhuma pode ser do RDAP: essa passa so por
        # Disputa.consultar, que conta as chamadas e o instante de cada uma)
        preparo = """
let t = 1e12;
Date.now = () => t;
globalThis.setTimeout = (fn, ms) => { t += Math.max(0, ms || 0); Promise.resolve().then(fn); return 0; };
globalThis.clearTimeout = () => {};
globalThis.window = globalThis;
const urls = [];
globalThis.fetch = async (url) => { urls.push(String(url)); return { ok: false, status: 404, text: async () => '', json: async () => null }; };
globalThis.document = { querySelector: () => null, addEventListener: () => {} };
globalThis.location = { search: '', origin: 'https://liberados.com.br' };
globalThis.history = { replaceState: () => {} };
const chamadas = [];
window.Disputa = { consultar: async (c) => { chamadas.push([c, t]); return { status: 404, recurso: '', json: null }; },
                   bloqueado: () => false };
const fs = require('fs');
for (const f of ['site_modelo/ficha.js', 'site_modelo/seu-nome.js']) (0, eval)(fs.readFileSync(f, 'utf8'));
const alvo = () => ({ dataset: {}, innerHTML: '', querySelector: () => null });
"""
        return subprocess.run([node, "-e", preparo + roteiro], capture_output=True, text=True,
                              check=True, cwd=self.RAIZ).stdout

    def test_candidatos(self):
        casos = ["Maria Silva", "João da Conceição", "Ana Maria dos Santos", "Li", "a", "", "   ",
                 "mariasilva.com.br", "Zé 123", "SILVA silva", "João Silva Filho",
                 "Pedro Souza Júnior", "Ana Costa Neta", "Carlos Neto", "José da Silva Jr."]
        saida = self.node(f"console.log(JSON.stringify({json.dumps(casos)}.map(SeuNome.candidatos)))")
        r = dict(zip(casos, json.loads(saida)))
        self.assertEqual(r["Maria Silva"], ["mariasilva.com.br", "silva.com.br", "maria.com.br"])
        self.assertEqual(r["João da Conceição"], ["joaoconceicao.com.br", "conceicao.com.br", "joao.com.br"])
        self.assertEqual(r["Ana Maria dos Santos"], ["anasantos.com.br", "santos.com.br", "ana.com.br"])
        self.assertEqual(r["Li"], ["li.com.br"])
        self.assertEqual((r["a"], r[""], r["   "]), ([], [], []))
        self.assertEqual(r["mariasilva.com.br"], ["mariasilva.com.br"])
        # so numeros nao vale como rotulo do .br
        self.assertEqual(r["Zé 123"], ["ze123.com.br", "ze.com.br"])
        self.assertEqual(r["SILVA silva"], ["silvasilva.com.br", "silva.com.br"])
        # Filho, Junior, Neto no fim nao sao sobrenome; com dois nomes so, sao
        self.assertEqual(r["João Silva Filho"], ["joaosilva.com.br", "silva.com.br", "joao.com.br"])
        self.assertEqual(r["Pedro Souza Júnior"], ["pedrosouza.com.br", "souza.com.br", "pedro.com.br"])
        self.assertEqual(r["Ana Costa Neta"], ["anacosta.com.br", "costa.com.br", "ana.com.br"])
        self.assertEqual(r["Carlos Neto"], ["carlosneto.com.br", "neto.com.br", "carlos.com.br"])
        self.assertEqual(r["José da Silva Jr."], ["josesilva.com.br", "silva.com.br", "jose.com.br"])

    def test_maria_silva_tres_consultas_em_sequencia_com_pausa(self):
        saida = self.node("""
(async () => {
  const nomes = SeuNome.candidatos('Maria Silva');
  const alvos = nomes.map(alvo);
  await SeuNome.conferirTodos(nomes, { conferir: (n, i) => Ficha.conferir(n, n, alvos[i]) });
  await new Promise((r) => setImmediate(r));
  console.log(JSON.stringify({ nomes, chamadas, urls, cartoes: alvos.map((a) => a.innerHTML) }));
})();
""")
        r = json.loads(saida)
        self.assertEqual([c for c, _ in r["chamadas"]],
                         ["domain/mariasilva.com.br", "domain/silva.com.br", "domain/maria.com.br"])
        instantes = [t for _, t in r["chamadas"]]
        self.assertTrue(all(b - a >= 2000 for a, b in zip(instantes, instantes[1:])), instantes)
        self.assertFalse([u for u in r["urls"] if "rdap" in u])
        for nome, cartao in zip(r["nomes"], r["cartoes"]):
            self.assertIn(f"<code>{nome}</code> está livre agora", cartao)

    def test_fora_do_indice_nunca_vira_nunca(self):
        # o indice de passagens so guarda quem passou 2 vezes ou mais: fora dele e
        # "no maximo uma vez", ou "a primeira vez" se a rodada de agora ja esta nele
        saida = self.node("""
globalThis.fetch = async (url) => {
  urls.push(String(url));
  if (String(url).endsWith('rodadas.json')) {
    return { ok: true, json: async () => ({ fatias: 256, rodadas: ['2017-09-13', '2026-08-12', '2026-09-09'] }) };
  }
  return { ok: true, status: 200, text: async () => 'outro.com.br\\t0,1\\n', json: async () => null };
};
const respostas = {
  'domain/livre.com.br': { status: 404, recurso: '', json: null },
  'domain/a.com.br': { status: 404, recurso: '', json: null },
  'domain/narodada.com.br': { status: 200, recurso: 'release-process-running;date=2026-09-16T18:00:00Z',
                              json: { objectClassName: 'domain', status: ['pending create'] } },
};
window.Disputa.consultar = async (c) => respostas[c];
const comBlocos = () => { const b = {}; return { dataset: {}, innerHTML: '',
  querySelector: (s) => (b[s] = b[s] || { innerHTML: '' }), blocos: b }; };
(async () => {
  const saida = {};
  for (const nome of ['livre.com.br', 'narodada.com.br', 'a.com.br']) {
    const a = comBlocos();
    await Ficha.conferir(nome, nome, a);
    for (let i = 0; i < 20; i += 1) await new Promise((r) => setImmediate(r));
    saida[nome] = a.blocos['.ficha-passagens'].innerHTML;
  }
  console.log(JSON.stringify(saida));
})();
""")
        r = json.loads(saida)
        self.assertIn("no máximo uma vez", r["livre.com.br"])
        self.assertIn("3 listas de que temos cópia, desde 2017", r["livre.com.br"])
        self.assertIn("primeira vez que ele aparece na lista", r["narodada.com.br"])
        # nome fora da regra (1 letra): "nao pode ser registrado", sem historico de rodadas
        self.assertEqual(r["a.com.br"], "")
        for texto in r.values():
            self.assertNotIn("nunca", texto.lower())

    def test_bloqueio_para_a_fila(self):
        # depois de um 429, disputa.js bloqueia por 5 min: os nomes que faltam nao sao consultados
        saida = self.node("""
(async () => {
  let n = 0;
  await SeuNome.conferirTodos(['a.com.br', 'b.com.br', 'c.com.br'], {
    conferir: async () => { n += 1; throw new Error('limite'); },
    aoErro: () => false });
  process.stdout.write(String(n));
})();
""")
        self.assertEqual(saida.strip(), "1")

    def test_nada_guardado_e_n_na_url(self):
        js = open(os.path.join(self.RAIZ, "site_modelo", "seu-nome.js"), encoding="utf-8").read()
        codigo = "\n".join(l for l in js.splitlines() if not l.strip().startswith(("*", "//", "/*")))
        for proibido in ("localStorage", "sessionStorage", "fetch(", "XMLHttpRequest", "sendBeacon", "cookie"):
            self.assertNotIn(proibido, codigo)
        self.assertIn("new URLSearchParams(location.search).get('n')", js)
        self.assertNotIn("location.hash", js)

    def test_portas(self):
        from garimpo.web import paginas
        self.assertIn("/seu-nome/", dict(paginas.CAMINHOS_RODAPE))     # em toda pagina
        ramos = open(os.path.join(self.RAIZ, "garimpo", "web", "ramos.py"), encoding="utf-8").read()
        self.assertIn('href="/seu-nome/"', ramos)


class TestDisputa(unittest.TestCase):
    """Quem disputa (web/disputa.js): 429 com pausa de 5 min, /entity/ a parte, trocar de nome no meio da carga."""

    RAIZ = os.path.dirname(__file__)

    def node(self, roteiro, respostas=None, extra=""):
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        # DOM minimo: um <dialog> unico com os ids fixos que disputa.js usa
        # (nunca um parser de HTML de verdade: o innerHTML do template e
        # ignorado, e cada id vira um elemento a parte, pre-criado).
        preparo = """
const vm = require('vm');
const fs = require('fs');
const chamadas = [];
const respostas = RESPOSTAS;
function elFactory() {
  const listeners = {};
  return {
    _text: '', _html: '', hidden: false, disabled: false, className: '',
    get textContent() { return this._text; }, set textContent(v) { this._text = v; },
    get innerHTML() { return this._html; }, set innerHTML(v) { this._html = v; },
    setAttribute() {},
    addEventListener(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); },
  };
}
let dlg = null;
function makeDialog() {
  const elems = {};
  ['disputa-titulo', 'disputa-resumo', 'disputa-caixa', 'disputa-corpo', 'disputa-status', 'disputa-mais', 'disputa-fechar']
    .forEach((id) => { elems[id] = elFactory(); });
  const d = elFactory();
  d.open = false;
  d.showModal = function () { this.open = true; };
  d.close = function () { this.open = false; };
  d.querySelector = (sel) => { const m = /^#([\\w-]+)$/.exec(sel); return m ? elems[m[1]] : null; };
  return d;
}
const doc = {
  createElement(tag) { if (tag === 'dialog') { dlg = makeDialog(); return dlg; } return elFactory(); },
  body: { appendChild() {} },
  addEventListener() {},
};
const ctx = {
  console, Promise, Map, Set, String, Date, Array, Object, JSON, Math,
  encodeURIComponent, decodeURIComponent,
  // PAUSA real (2,5 s) viraria um teste lento sem mudar o que se prova aqui
  setTimeout: (fn, ms) => setTimeout(fn, ms > 20 ? 3 : ms), clearTimeout,
  document: doc,
  fetch: async (url) => {
    chamadas.push(url);
    const caminho = url.replace('https://rdap.registro.br/', '');
    const def = respostas[caminho];
    if (!def) return { status: 404, ok: false, headers: { get: () => null }, json: async () => null };
    const [status, cabecalhos, corpo] = def;
    return {
      status, ok: status >= 200 && status < 300,
      headers: { get: (k) => (cabecalhos && cabecalhos[(k || '').toLowerCase()]) || null },
      json: async () => corpo,
    };
  },
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(ARQUIVO, 'utf8'), ctx);
vm.runInContext(EXTRA, ctx);
const D = ctx.Disputa;
// funcoes extras (de outro arquivo, ex. consultarRdap do app.js) rodam
// DENTRO do contexto sandboxado tambem, para 'window' resolver la: expostas
// aqui so como atalho para o roteiro do teste (que roda no node de fora).
const consultarRdap = ctx.consultarRdap;
const dialogo = () => dlg;
const esperarAte = async (pred, tentativas) => {
  for (let i = 0; i < (tentativas || 400); i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 3));
  }
  return false;
};
(async () => {
""".replace("ARQUIVO", json.dumps(os.path.join(self.RAIZ, "web", "disputa.js")))
        preparo = preparo.replace("RESPOSTAS", json.dumps(respostas or {})).replace("EXTRA", json.dumps(extra))
        p = subprocess.run([node, "-e", preparo + roteiro + "\n})();"], capture_output=True, text=True)
        if p.returncode != 0:
            self.fail(p.stderr)
        return json.loads(p.stdout)

    def test_429_no_domain_bloqueia_5_min_e_e_a_mesma_mensagem_da_conferencia(self):
        # um 429 no /domain/ (aqui, o de "quem disputa") bloqueia
        # tambem consultarRdap (app.js), que usa a mesma window.Disputa.consultar
        app_js = _fonte_da_lista()
        consultar_rdap = re.search(r"async function consultarRdap\(dominio\) \{.*?\n\}", app_js, re.S).group(0)
        respostas = {
            "domain/a.com.br": [429, {"nicbr-rate-limit-exceeded": "true"}, None],
            "domain/b.com.br": [200, {"nicbr-resource": ""}, {"objectClassName": "domain", "status": ["active"]}],
        }
        r = self.node("""
let erro1 = null;
try { await D.consultar('domain/a.com.br'); } catch (e) { erro1 = e.message; }
let erro2 = null;
try { await consultarRdap('b.com.br'); } catch (e) { erro2 = e.message; }
process.stdout.write(JSON.stringify({
  erro1, erro2, bloqueado: D.bloqueado(), mensagem: D.mensagemBloqueio(), chamadas,
}));
""", respostas, extra=consultar_rdap)
        self.assertEqual(r["erro1"], "Não deu para conferir agora; tente de novo em 5 min.")
        self.assertEqual(r["erro2"], r["erro1"])          # mesma mensagem em consultarRdap
        self.assertEqual(r["mensagem"], r["erro1"])
        self.assertTrue(r["bloqueado"])
        # a 2a chamada nem saiu: nenhuma consulta nova ao RDAP durante o bloqueio
        self.assertEqual(r["chamadas"], ["https://rdap.registro.br/domain/a.com.br"])

    def test_429_no_entity_nao_bloqueia_o_domain_e_pula_so_o_resto_do_lote(self):
        # regressao: um 429 no /entity/ (de UM candidato CNPJ) esvaziava a
        # lista inteira e travava os tickets seguintes. Doc1 e cnpj (429 na
        # entidade), doc2 tambem e cnpj (a entidade dele tem de ser pulada,
        # sem tentar), doc3 e cpf (nunca pede /entity/).
        respostas = {
            "domain/x.com.br": [200, {}, {"publicIds": [
                {"type": "ticket", "identifier": "1"}, {"type": "ticket", "identifier": "2"},
                {"type": "ticket", "identifier": "3"}]}],
            "domain/x.com.br?ticket=1": [200, {}, {
                "entities": [{"roles": ["registrant"], "publicIds": [{"type": "cnpj", "identifier": "11.111.111/0001-11"}],
                             "vcardArray": ["vcard", [["fn", {}, "text", "Empresa Um"]]]}],
                "events": [{"eventAction": "registration", "eventDate": "2026-09-01T00:00:00Z"}]}],
            "entity/11111111000111": [429, {"nicbr-rate-limit-exceeded": "true"}, None],
            "domain/x.com.br?ticket=2": [200, {}, {
                "entities": [{"roles": ["registrant"], "publicIds": [{"type": "cnpj", "identifier": "22.222.222/0001-22"}],
                             "vcardArray": ["vcard", [["fn", {}, "text", "Empresa Dois"]]]}],
                "events": [{"eventAction": "registration", "eventDate": "2026-09-02T00:00:00Z"}]}],
            "entity/22222222000122": [200, {}, {"nicbr_domainCount": 9}],   # nunca deveria ser chamado
            "domain/x.com.br?ticket=3": [200, {}, {
                "entities": [{"roles": ["registrant"], "publicIds": [{"type": "cpf", "identifier": "123.***.***-00"}],
                             "vcardArray": ["vcard", [["fn", {}, "text", "Fulano De Tal"]]]}],
                "events": [{"eventAction": "registration", "eventDate": "2026-09-03T00:00:00Z"}]}],
        }
        r = self.node("""
D.abrir('x.com.br');
await esperarAte(() => dialogo().querySelector('#disputa-status').textContent.endsWith('lidos.'));
process.stdout.write(JSON.stringify({
  status: dialogo().querySelector('#disputa-status').textContent,
  corpo: dialogo().querySelector('#disputa-corpo').innerHTML,
  chamadas, bloqueado: D.bloqueado(),
}));
""", respostas)
        self.assertEqual(r["status"], "todos lidos.")     # os 3 tickets, nao so o 1o
        for esperado in ("Empresa Um", "Empresa Dois", "Fulano De Tal"):
            self.assertIn(esperado, r["corpo"])
        self.assertNotIn("…", r["corpo"])                 # nenhuma linha ficou pendente
        self.assertIn("https://rdap.registro.br/entity/11111111000111", r["chamadas"])
        # a entidade do ticket 2 foi pulada: so uma consulta a /entity/ no lote
        self.assertNotIn("https://rdap.registro.br/entity/22222222000122", r["chamadas"])
        self.assertEqual(sum(1 for c in r["chamadas"] if "/entity/" in c), 1)
        # um 429 so no /entity/ (endpoint bem mais restrito) nao bloqueia o /domain/
        self.assertFalse(r["bloqueado"])

    def test_trocar_de_nome_durante_a_carga_mostra_so_o_segundo(self):
        # regressao: abrir b.com.br enquanto a.com.br ainda carregava
        # pintava os candidatos de a por cima do titulo de b, e b nunca
        # carregava. abrir() e sincrono ate o 1o await de carregar(), entao
        # duas chamadas seguidas (sem esperar) reproduzem a corrida de
        # verdade: quando a 2a roda, ocupado ja esta true por causa da 1a.
        respostas = {
            "domain/a.com.br": [200, {}, {"publicIds": [{"type": "ticket", "identifier": "1"}]}],
            "domain/a.com.br?ticket=1": [200, {}, {
                "entities": [{"roles": ["registrant"], "publicIds": [{"type": "cpf", "identifier": "1.***-00"}],
                             "vcardArray": ["vcard", [["fn", {}, "text", "Nao Deveria Aparecer"]]]}]}],
            "domain/b.com.br": [200, {}, {"publicIds": []}],
        }
        r = self.node("""
D.abrir('a.com.br');
D.abrir('b.com.br');
await esperarAte(() => /nenhum ticket/.test(dialogo().querySelector('#disputa-status').textContent));
process.stdout.write(JSON.stringify({
  titulo: dialogo().querySelector('#disputa-titulo').textContent,
  status: dialogo().querySelector('#disputa-status').textContent,
  corpo: dialogo().querySelector('#disputa-corpo').innerHTML,
  chamadas,
}));
""", respostas)
        self.assertEqual(r["titulo"], "Quem disputa b.com.br")
        self.assertEqual(r["status"], "nenhum ticket visível para este nome.")
        self.assertNotIn("Nao Deveria Aparecer", r["corpo"])
        self.assertIn("https://rdap.registro.br/domain/a.com.br", r["chamadas"])
        self.assertIn("https://rdap.registro.br/domain/b.com.br", r["chamadas"])
        # a corrida corta a.com.br assim que b.com.br e aberto: nunca busca o ticket dele
        self.assertNotIn("https://rdap.registro.br/domain/a.com.br?ticket=1", r["chamadas"])

    # 19/09/2026, cagada.com.br: a lista dizia "pedido pendente, 1 candidato"
    # (leitura de 3 dias antes); o painel dizia "nenhum ticket visivel" e
    # mostrava "carregar mais 0". A resposta registrada e a real daquele dia.
    ROTEIRO_ABRIR = """
D.abrir(NOME, 'há 3 d');
await esperarAte(() => /\\.$/.test(dialogo().querySelector('#disputa-status').textContent));
const q = (id) => dialogo().querySelector('#' + id);
process.stdout.write(JSON.stringify({
  status: q('disputa-status').textContent, resumo: q('disputa-resumo').textContent,
  corpo: q('disputa-corpo').innerHTML, caixaOculta: q('disputa-caixa').hidden,
  maisOculto: q('disputa-mais').hidden, mais: q('disputa-mais').textContent, chamadas,
}));
"""

    def abrir(self, nome, respostas):
        return self.node(self.ROTEIRO_ABRIR.replace("NOME", json.dumps(nome)), respostas)

    def test_registrado_sem_ticket_explica_com_a_data_do_registro(self):
        r = self.abrir("cagada.com.br", {"domain/cagada.com.br": [200, {}, {
            "objectClassName": "domain", "ldhName": "cagada.com.br",
            "events": [{"eventAction": "registration", "eventDate": "2026-09-16T18:23:14Z"}],
            "entities": [{"roles": ["registrant"]}, {"roles": ["technical"]}]}]})
        self.assertEqual(r["status"], "Este nome já está registrado (o RDAP dá 16/09/2026, 15:23 como data "
                                      "do registro) e o RDAP não lista mais o ticket do pedido. O número da lista "
                                      "é de uma leitura feita há 3 d.")
        self.assertTrue(r["maisOculto"])
        self.assertNotIn("carregar mais 0", r["mais"])
        self.assertTrue(r["caixaOculta"])                 # sem tabela so com cabecalho
        self.assertEqual(r["chamadas"], ["https://rdap.registro.br/domain/cagada.com.br"])

    def test_pedido_pendente_mostra_o_candidato(self):
        r = self.abrir("p.com.br", {
            "domain/p.com.br": [200, {}, {"objectClassName": "domain", "status": ["pending create"],
                                          "publicIds": [{"type": "ticket", "identifier": "32300001"}]}],
            "domain/p.com.br?ticket=32300001": [200, {}, {
                "entities": [{"roles": ["registrant"], "publicIds": [{"type": "cpf", "identifier": "123.***.***-00"}],
                             "vcardArray": ["vcard", [["fn", {}, "text", "Fulano Exemplo"]]]}],
                "events": [{"eventAction": "registration", "eventDate": "2026-09-16T17:00:00Z"}]}],
        })
        self.assertEqual(r["status"], "todos lidos.")
        self.assertIn("Fulano Exemplo", r["corpo"])
        self.assertFalse(r["caixaOculta"])
        self.assertTrue(r["maisOculto"])
        # pedido comum: o ticket unico aparece, entao a frase da rodada nao cabe
        self.assertEqual(r["resumo"], "1 ticket visível, do primeiro ao último.")

    def test_404_diz_que_esta_livre(self):
        r = self.abrir("sumiu.com.br", {})
        self.assertTrue(r["status"].startswith("Este nome está livre agora"))
        self.assertIn("leitura feita há 3 d", r["status"])
        self.assertTrue(r["maisOculto"])

    def test_travado_e_rodada_sem_ticket(self):
        r = self.abrir("t.com.br", {"domain/t.com.br": [200, {"nicbr-resource": "release-process-waiting"},
                                                        {"objectClassName": "domain"}]})
        self.assertTrue(r["status"].startswith("Este nome travou e espera a próxima rodada"))
        r = self.abrir("r.com.br", {"domain/r.com.br": [200, {"nicbr-resource": "release-process-running;date=x"},
                                                        {"objectClassName": "domain", "status": ["pending create"]}]})
        self.assertIn("zero aqui pode ser um", r["status"])

    def test_carregar_mais_escondido_vence_o_css(self):
        # .conferir (extra.css) e inline-block e vencia o [hidden] do navegador
        css = open(os.path.join(self.RAIZ, "web", "style.css"), encoding="utf-8").read()
        self.assertIn(".dialogo-disputa [hidden] { display: none; }", css)

    def test_contagem_da_lista_leva_a_idade_da_leitura(self):
        """A contagem e de uma leitura, nao de agora. A idade visivel e uma
        por linha, ao lado do selo (carimboDaLinha), e nao se repete embaixo
        da contagem. Na contagem, o 'lido ha' fica no texto para leitor de
        tela."""
        js = _fonte_da_lista()
        funcao = re.search(r"function competindo\(it\) \{.*?\n\}", js, re.S).group(0)
        self.assertIn("const ao = lido ? `, lido ${lido}` : '';", funcao)
        # >=2, 0 e n (Disputa e o texto de reserva) levam o "lido ha"
        self.assertGreaterEqual(funcao.count("${esc(ao)}") + funcao.count("${ao}") + funcao.count("quantos + ao"), 4)
        self.assertNotIn("idade-contagem", funcao)
        self.assertIn("'1 candidato'", funcao)
        linha = re.search(r"function linha\(it\) \{.*?\n\}", js, re.S).group(0)
        self.assertEqual(linha.count("carimboDaLinha(it)"), 1)

class TestPontoCom(unittest.TestCase):
    """O .com do mesmo nome (web/pontocom.js): interruptor, lote e ficha, tudo no navegador."""

    RAIZ = os.path.dirname(__file__)
    # respostas reais de 15/09/2026, com o nome trocado por um neutro e so
    # com os campos que o modulo le (e, no
    # titular, com e-mail e telefone de proposito: nao podem sair)
    VERISIGN = {
        "objectClassName": "domain", "ldhName": "CARRO.COM",
        "links": [{"rel": "self", "href": "https://rdap.verisign.com/com/v1/domain/carro.com"},
                  {"rel": "related", "href": "https://rdap.godaddy.com/v1/domain/CARRO.COM"}],
        "status": ["client delete prohibited", "client renew prohibited",
                   "client transfer prohibited", "client update prohibited"],
        "events": [{"eventAction": "registration", "eventDate": "2002-05-13T18:30:01Z"},
                   {"eventAction": "expiration", "eventDate": "2027-05-13T18:30:01Z"},
                   {"eventAction": "last changed", "eventDate": "2026-04-15T14:41:40Z"}],
        "entities": [{"objectClassName": "entity", "roles": ["registrar"],
                      "publicIds": [{"type": "IANA Registrar ID", "identifier": "146"}],
                      "vcardArray": ["vcard", [["version", {}, "text", "4.0"],
                                               ["fn", {}, "text", "GoDaddy.com, LLC"]]]}],
        "nameservers": [{"ldhName": "DAMAO.NS.GIANTPANDA.COM"}, {"ldhName": "YANGGUANG.NS.GIANTPANDA.COM"}],
    }
    PRIVADO = {"entities": [{"roles": ["registrant"], "vcardArray": ["vcard", [
        ["fn", {}, "text", "Registration Private"], ["org", {}, "text", "Domains By Proxy, LLC"],
        ["adr", {"cc": "US"}, "text", ["", "", "", "Tempe", "Arizona", "85281", ""]],
        ["tel", {"type": "voice"}, "uri", "tel:+1.4806242599"]]]}]}
    AMAZON = {"entities": [{"roles": ["registrant"], "vcardArray": ["vcard", [
        ["fn", {}, "text", "Hostmaster, Amazon Legal Dept."], ["org", {}, "text", "Amazon Technologies, Inc."],
        ["adr", {}, "text", ["P.O. Box 8102", "", "", "Reno", "NV", "89507", "US"]],
        ["email", {}, "text", "hostmaster@amazon.com"], ["tel", {}, "uri", "tel:+1.2062664064"]]]}]}
    HUGE = {"entities": [{"roles": ["registrant"], "vcardArray": ["vcard", [
        ["fn", {}, "text", "Domain Admin / This Domain is For Sale"], ["org", {}, "text", "HugeDomains.com"]]]}]}

    def js(self):
        return open(os.path.join(self.RAIZ, "web", "pontocom.js"), encoding="utf-8").read()

    def node(self, roteiro, respostas=None, pilulas=None):
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        # o modulo roda num contexto com o minimo de navegador: fetch de
        # mentira (respostas por URL, e conta as chamadas), DOM vazio, sem
        # localStorage (como numa aba que o bloqueia). `pilulas` (fqdns do
        # .com) simula quem esta na tela para [data-com]; o roteiro dispara
        # o MutationObserver de verdade chamando `disparar()`.
        preparo = """
const vm = require('vm');
const fs = require('fs');
const chamadas = [];
const classes = [];
const respostas = RESPOSTAS;
const pilulas = PILULAS.map((f) => ({ dataset: { com: f }, nodeType: 1,
  matches: (sel) => sel === '[data-com]', querySelector: () => null }));
let observador = null;
const disparar = () => observador && observador([{ addedNodes: pilulas }]);
const ctx = {
  URL, Promise, Map, Set, String, Date, Array, Object, JSON, Math, Intl, console, setTimeout, clearTimeout,
  CSS: { escape: (s) => s },
  MutationObserver: class { constructor(cb) { observador = cb; } observe() {} },
  document: {
    addEventListener() {},
    querySelectorAll: (sel) => sel === '[data-com]' ? pilulas : [],
    querySelector: () => null, activeElement: null,
    createElement: () => ({ setAttribute() {}, addEventListener() {} }),
    documentElement: { classList: { toggle: (c, v) => classes.push([c, v]) } },
    body: { appendChild() {} },
  },
  fetch: async (url) => {
    chamadas.push(url);
    const nome = url.split('/').pop().toLowerCase();
    const [status, json] = respostas[nome] || [404, null];
    return { status, json: async () => json };
  },
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(ARQUIVO, 'utf8'), ctx);
const P = ctx.PontoCom;
(async () => {
""".replace("ARQUIVO", json.dumps(os.path.join(self.RAIZ, "web", "pontocom.js")))
        preparo = preparo.replace("RESPOSTAS", json.dumps(respostas or {})).replace("PILULAS", json.dumps(pilulas or []))
        saida = subprocess.run([node, "-e", preparo + roteiro + "\n})();"],
                               capture_output=True, text=True, check=True).stdout
        return json.loads(saida)

    def test_rotulo_serve_para_toda_extensao_br(self):
        r = self.node("process.stdout.write(JSON.stringify(['carro.com.br', 'Carro.app.br', "
                      "'nome.sorocaba.br', 'café.com.br', '-x.com.br', '', 'a_b.com.br'].map(P.rotulo)));")
        self.assertEqual(r, ["carro", "carro", "nome", "xn--caf-dma", "", "", ""])

    def test_ler_a_resposta_da_verisign(self):
        venda = dict(self.VERISIGN, nameservers=[{"ldhName": "NS1.AFTERNIC.COM"}])
        caindo = dict(venda, status=["redemption period"])
        parado = dict(self.VERISIGN, nameservers=[{"ldhName": "NS1.PARKINGCREW.NET"}])
        r = self.node(f"""
process.stdout.write(JSON.stringify([
  P.ler(404, null), P.ler(200, {json.dumps(self.VERISIGN)}), P.ler(200, {json.dumps(venda)}),
  P.ler(200, {json.dumps(caindo)}).estado, P.ler(200, {{ objectClassName: 'error' }}).estado,
  P.ler(200, {json.dumps(parado)}),
]));""")
        self.assertEqual(r[0], {"estado": "livre"})
        self.assertEqual(r[1], {
            "estado": "com_dono", "desde": "2002-05-13T18:30:01Z", "expira": "2027-05-13T18:30:01Z",
            "alterado": "2026-04-15T14:41:40Z", "registrador": "GoDaddy.com, LLC", "iana": "146",
            "situacoes": self.VERISIGN["status"],
            "servidores": ["damao.ns.giantpanda.com", "yangguang.ns.giantpanda.com"],
            "vitrine": "", "estacionamento": "",
            "ficha": "https://rdap.godaddy.com/v1/domain/CARRO.COM"})
        self.assertEqual((r[2]["estado"], r[2]["vitrine"]), ("a_venda", "Afternic"))
        # vencido manda mais que vitrine: o nome esta caindo
        self.assertEqual(r[3], "caindo")
        self.assertEqual(r[4], "erro")
        # estacionamento nao e venda: continua "tem dono"
        self.assertEqual((r[5]["estado"], r[5]["estacionamento"]), ("com_dono", "ParkingCrew"))

    def test_dono_so_nome_organizacao_e_pais(self):
        r = self.node(f"""
process.stdout.write(JSON.stringify([
  P.lerTitular({json.dumps(self.PRIVADO)}), P.lerTitular({json.dumps(self.AMAZON)}),
  P.lerTitular({json.dumps(self.HUGE)}), P.lerTitular({{ entities: [] }}),
]));""")
        self.assertEqual(r[0], {"encontrado": True, "nome": "", "organizacao": "Domains By Proxy, LLC",
                                "pais": "US", "oculto": True, "aVenda": False})
        self.assertEqual(r[1], {"encontrado": True, "nome": "Hostmaster, Amazon Legal Dept.",
                                "organizacao": "Amazon Technologies, Inc.", "pais": "US",
                                "oculto": False, "aVenda": False})
        self.assertTrue(r[2]["aVenda"])
        self.assertFalse(r[3]["encontrado"])
        saida = json.dumps(r)
        for proibido in ("hostmaster@amazon.com", "4806242599", "2062664064", "Reno", "Tempe", "85281"):
            self.assertNotIn(proibido, saida)

    def test_so_consulta_registrador_da_lista(self):
        r = self.node("""process.stdout.write(JSON.stringify([
  'https://rdap.godaddy.com/v1/domain/X.COM', 'https://dreamhost.rdap.tucows.com/domain/x.com',
  'https://rdap.godaddy.com.mal.example/v1/domain/x.com', 'http://rdap.godaddy.com/v1/domain/x.com',
  'https://rdap.hosting.kr/domain/x.com', 'nao e url',
].map(P.permitido)));""")
        self.assertEqual(r, [True, True, False, False, False, False])

    def test_travas_em_portugues(self):
        r = self.node("""process.stdout.write(JSON.stringify([
  P.travas(['client transfer prohibited', 'client delete prohibited']),
  P.travas(['server delete prohibited', 'server transfer prohibited']),
  P.travas(['redemption period']), P.travas(['active']),
]));""")
        self.assertEqual(r[0], ["travas comuns do registrador contra transferência sem autorização"])
        self.assertEqual(r[1], ["travado pelo próprio registro do .com"])
        self.assertIn("prazo de resgate", r[2][0])
        self.assertEqual(r[3], ["ativo, sem travas"])

    def test_lote_uma_consulta_por_nome_para_no_429_e_resume(self):
        respostas = {"carro.com": [200, self.VERISIGN], "cheio.com": [429, None],
                     "venda.com": [200, dict(self.VERISIGN, nameservers=[{"ldhName": "ns1.afternic.com"}])]}
        r = self.node("""
const primeiro = await P.conferirLote(['carro.com', 'livre.com', 'carro.com', 'venda.com']);
const texto = P.textoDoResumo(P.resumo(['carro.com', 'livre.com', 'venda.com']));
chamadas.length = 0;
const segundo = await P.conferirLote(['carro.com', 'cheio.com', 'a.com', 'b.com', 'c.com', 'd.com']);
process.stdout.write(JSON.stringify({ primeiro, texto, segundo, chamadas,
  pilula: P.botao('carro.app.br'), livre: P.botao('livre.com.br'), desligado: classes }));""", respostas)
        self.assertEqual(r["primeiro"], {"total": 3, "feitos": 3, "parou": "", "cancelado": False})
        self.assertEqual(r["texto"], "3 nomes conferidos: 1 com .com livre (livre.com); 1 parece à venda.")
        # o que ja estava no cache nao e consultado de novo; o 429 para o lote
        self.assertNotIn("https://rdap.verisign.com/com/v1/domain/carro.com", r["chamadas"])
        self.assertTrue(r["segundo"]["parou"].startswith("o registro do .com pediu uma pausa"))
        self.assertLess(len(r["chamadas"]), 5)
        self.assertIn(".com tem dono", r["pilula"])
        self.assertIn('data-com="carro.com"', r["pilula"])
        self.assertIn("pontocom-livre", r["livre"])
        # sem nada guardado, o interruptor nasce desligado
        self.assertEqual(r["desligado"], [["com-ligado", False]])

    def test_erro_nao_vira_laco_pelo_observer(self):
        # F7 (regressao com 6 pilulas: 10 consultas/s sem fim). Erro na
        # Verisign (ou rede caida) repinta a pilula, o MutationObserver
        # acorda -- e sem a correcao isso reconsultava o lote inteiro sem fim, nome a nome (PARALELO=2
        # e menor que o lote, entao o resto nunca tentado reaparecia em
        # "faltam" a cada disparo). O observer nunca deve refazer nem quem
        # deu erro nem o resto do lote que parou nele; so alternar() (acao
        # explicita) refaz.
        nomes = ["a.com", "b.com", "c.com", "d.com", "e.com", "f.com"]
        respostas = {n: [500, None] for n in nomes}
        r = self.node("""
P.alternar();                       // liga: 1o lote, para no 1o erro (PARALELO=2)
await new Promise((r) => setTimeout(r, 200));
const depoisDeLigar = chamadas.length;
disparar(); disparar(); disparar(); // observer acordando repetido (repintar em laco)
await new Promise((r) => setTimeout(r, 500));
const depoisDoObservador = chamadas.length;
P.alternar(); P.alternar();         // desliga e religa: acao explicita
await new Promise((r) => setTimeout(r, 200));
const depoisDeReligar = chamadas.length;
process.stdout.write(JSON.stringify({ depoisDeLigar, depoisDoObservador, depoisDeReligar }));
""", respostas, pilulas=nomes)
        self.assertEqual(r["depoisDeLigar"], 2)
        # o observer nao reconsulta quem deu erro nem o resto do lote que parou: nenhuma chamada nova
        self.assertEqual(r["depoisDoObservador"], 2)
        # religar e uma acao de quem olha: reconsulta (novo lote, para de novo no 1o erro)
        self.assertEqual(r["depoisDeReligar"], 4)

    def test_nunca_le_contato_do_dono(self):
        codigo = "\n".join(l for l in self.js().splitlines() if not l.strip().startswith(("*", "//", "/*")))
        for proibido in ("'email'", "'tel'", "legalRepresentative", "publicIds.find((p) => p.type === 'cpf"):
            self.assertNotIn(proibido, codigo)
        # do adr so sai o codigo do pais
        titular = codigo[codigo.index("function lerTitular"):codigo.index("function travas")]
        self.assertNotRegex(titular, r"adr\[3\]\[[0-5]\]")

    def test_interruptor_na_lista_e_no_app_local(self):
        from garimpo.web import paginas
        modelo = os.path.join(self.RAIZ, "site_modelo")
        ferramenta = next(p for p in paginas.carregar(modelo) if p.tipo == "ferramenta")
        self.assertIn("pontocom.js", ferramenta.scripts)
        self.assertLess(ferramenta.scripts.index("pontocom.js"), ferramenta.scripts.index("lista/app.js"))
        import exportar_site
        self.assertIn("pontocom.js", exportar_site.COMPARTILHADOS)
        for pagina in (os.path.join(modelo, "conteudo", "ferramenta.html"), os.path.join(self.RAIZ, "web", "index.html")):
            html = open(pagina, encoding="utf-8").read()
            self.assertRegex(html, r'data-pontocom-alternar aria-pressed="false"')
            self.assertIn("data-pontocom-status", html)
        self.assertIn('src="/static/pontocom.js"', open(os.path.join(self.RAIZ, "web", "index.html"), encoding="utf-8").read())
        self.assertIn("window.PontoCom.botao(", _fonte_da_lista())
        self.assertIn("window.PontoCom.botao(",
                      open(os.path.join(self.RAIZ, "web", "app.js"), encoding="utf-8").read())
        css = open(os.path.join(self.RAIZ, "web", "style.css"), encoding="utf-8").read()
        self.assertIn(":root:not(.com-ligado) .pontocom { display: none; }", css)


class TestInicioEntreRodadas(unittest.TestCase):
    """A pagina inicial leva o calendario e o cartao de quem volta na proxima."""

    def test_calendario_e_cartao_na_ferramenta(self):
        from garimpo.web import paginas
        modelo = os.path.join(os.path.dirname(__file__), "site_modelo")
        inicio = next(p for p in paginas.carregar(modelo) if p.slug == "")
        self.assertIn("agenda.js", inicio.scripts)
        layout = open(os.path.join(modelo, "layout.html"), encoding="utf-8").read()
        dados = {"gerado_em": "2026-09-17T12:00:00+00:00", "itens": [],
                 "rodada": {"inicio": "2026-09-09T15:00:00-03:00", "fim": "2026-09-16T15:00:00-03:00"}}
        html = paginas.render(inicio, layout, dados)
        cal = json.loads(re.search(r'id="p-calendario">(.*?)</script>', html).group(1))
        self.assertIn("2026-10-14", cal["aberturas"])
        self.assertIn('data-filtro="aguardando"', html)
        self.assertIn('id="lembrar-rodada"', html)
        # o script de tema continua sendo o primeiro <script> sem atributo (hash da CSP)
        self.assertIn("localStorage", re.search(r"<script>(.*?)</script>", html, re.S).group(1))


class TestPaginasPorRamo(unittest.TestCase):
    """/dominios/<ramo>/: texto pela fase, marca fora, pagina rala com noindex."""

    STATUS = ["LIBERACAO_LIVRE", "LIBERACAO_DISPUTADA", "COMPETITIVO", "LIVRE",
              "REGISTRADO", "AGUARDANDO_LIBERACAO", "INDISPONIVEL", "LIVRE_COM_TICKET"]

    def dados(self, gerado, n_pet=12):
        cats = [{"nome": "pet", "rotulo": "Pet"}, {"nome": "saude", "rotulo": "Saúde"}]
        itens = []
        for i in range(n_pet):
            itens.append([f"pet{i:02d}.com.br", 0, 0, 60 - i, 0, [], 0, 0, 0, 0, 1, 0, 0])
        itens.append(["petdisputa.com.br", 1, 3, 90, 0, [], 0, 0, 0, 0, 1, 0, 0])
        itens.append(["petmarca.com.br", 0, 0, 99, 0, [], 2, 0, 0, 0, 1, 0, 0])       # marca: fora
        itens.append(["petdono.com.br", 4, 0, 98, 0, [], 0, 0, 0, 0, 1, 0, 0])        # registrado: fora
        itens.append(["saude1.com.br", 0, 0, 70, 0, [], 0, 0, 0, 0, 2, 0, 0])
        return {"gerado_em": gerado, "status": self.STATUS, "categorias": cats,
                "motivos": [], "total_rodada": 1000, "itens": itens,
                "rodada": {"inicio": "2026-09-09T15:00:00-03:00", "fim": "2026-09-16T15:00:00-03:00"}}

    TODOS = {"extensoes": ["com.br"], "motivos": [],
             "itens": [["pet", 0, 50, [], 0, 1], ["petx", 0, 50, [], 0, 1], ["saude", 0, 50, [], 0, 2]]}

    def pagina(self, campos, slug):
        return next(c for c in campos if c["slug"] == slug)

    def test_nome_abre_a_ficha_em_dialogo(self):
        """O link de cada nome abre a ficha num dialogo; o robo e o Ctrl+clique seguem o link."""
        from garimpo.web import paginas, ramos
        dados = self.dados("2026-09-14T12:00:00+00:00")
        pet = self.pagina(ramos.paginas(dados, self.TODOS), "dominios/pet")
        self.assertEqual(pet["scripts"], ("disputa.js", "agenda.js", "ficha.js"))
        self.assertIn('<a rel="nofollow" href="/quando-volta/?d=pet00.com.br" data-ficha="pet00.com.br" '
                      'aria-haspopup="dialog">pet00.com.br</a>', pet["corpo"])
        # a ficha preve a volta pelas datas das rodadas: o build preenche o calendario
        layout = open(os.path.join(os.path.dirname(__file__), "site_modelo", "layout.html"), encoding="utf-8").read()
        pagina = paginas.Pagina(**pet)
        html = paginas.render(pagina, layout, dados)
        self.assertRegex(html, r'id="p-calendario">\{"gerado_em": [^<]*"aberturas": \["20')
        self.assertIn('<script src="/ficha.js"></script>', html)
        # o JSON do calendario nao vai para o espelho que agentes de IA leem
        self.assertNotIn("aberturas", paginas.markdown_da_pagina(pagina, dados))

    def test_rodada_aberta(self):
        from garimpo.web import ramos
        campos = ramos.paginas(self.dados("2026-09-14T12:00:00+00:00"), self.TODOS)
        pet = self.pagina(campos, "dominios/pet")
        self.assertTrue(pet["indexavel"])
        self.assertIn("<strong>2</strong> domínios .br de pet, de 1.000 da lista", pet["corpo"])
        self.assertIn("12 estavam sem candidato visível, 1 disputados", pet["corpo"])
        self.assertIn("3 candidatos", pet["corpo"])
        self.assertNotIn("petmarca", pet["corpo"])
        self.assertNotIn("petdono", pet["corpo"])
        self.assertIn('href="/?ramo=pet"', pet["corpo"])
        self.assertLessEqual(len(pet["descricao"]), 160)
        self.assertIn("setembro de 2026", pet["titulo_busca"])
        # saude tem um nome so: fica de pe, sem indexar, e fora da lista de outros ramos
        saude = self.pagina(campos, "dominios/saude")
        self.assertFalse(saude["indexavel"])
        self.assertNotIn("/dominios/saude/", pet["corpo"])

    def test_rodada_fechada_fala_do_que_sobrou(self):
        from garimpo.web import ramos
        campos = ramos.paginas(self.dados("2026-09-18T12:00:00+00:00"), self.TODOS)
        pet = self.pagina(campos, "dominios/pet")
        self.assertIn("A rodada fechou", pet["corpo"])
        self.assertIn("fechou sem candidato visível", pet["corpo"])
        self.assertIn("travou: volta na rodada de 14/10", pet["corpo"])
        self.assertIn("foi a leilão", ramos.situacao(["x.com.br", 2, 2, 1, 1, [], 0, 1, 0, 0, 0],
                                                     self.dados("2026-09-18T12:00:00+00:00"),
                                                     ramos.fase_de(self.dados("2026-09-18T12:00:00+00:00")))[1])

    def test_recorte_com_links(self):
        """
        /dominios/com-links/ ("dominios expirados .br com backlinks"): so com
        o indice de links, ordenada pelo numero de links, citados primeiro, e
        com a mesma regra da etiqueta (um link so nao entra).
        """
        from garimpo.web import ramos
        d = self.dados("2026-09-18T12:00:00+00:00")
        # sem o indice: a pagina nao existe
        self.assertNotIn("dominios/com-links", [c["slug"] for c in ramos.paginas(d, self.TODOS)])
        d["grafo_links"] = "jul-set/2026"
        links = {"pet00.com.br": [3, 0], "pet01.com.br": [40, 0], "pet02.com.br": [5, 2],
                 "pet03.com.br": [1, 0], "saude1.com.br": [1, 1]}
        for it in d["itens"]:
            it.append(links.get(it[0], 0))
        todos = dict(self.TODOS, itens=self.TODOS["itens"] + [["x", 0, 50, [], 0, 0, [9, 1]]])
        pag = self.pagina(ramos.paginas(d, todos), "dominios/com-links")
        self.assertEqual(pag["titulo_busca"], "Domínios expirados .br com backlinks: a lista de setembro de 2026")
        self.assertIn("<strong>1</strong> domínios .br com links de outros sites", pag["corpo"])
        self.assertIn("jul-set/2026", pag["corpo"])
        self.assertIn("5 · 2 muito citados", pag["corpo"])
        # citado primeiro (pet02), depois pelo numero (pet01 40, pet00 3); um link so fora
        ordem = [n for n in ("pet02", "pet01", "pet00", "saude1", "pet03") if n in pag["corpo"]]
        self.assertEqual(ordem, ["pet02", "pet01", "pet00", "saude1"])
        self.assertLess(pag["corpo"].index("pet02"), pag["corpo"].index("pet01"))
        self.assertLess(pag["corpo"].index("pet01"), pag["corpo"].index("pet00"))
        self.assertNotIn("pet03.com.br", pag["corpo"])
        self.assertIn('href="/?ordem=links"', pag["corpo"])
        self.assertNotIn("O ramo é adivinhado", pag["corpo"])

    def test_sitemap_e_llms_pulam_pagina_rala(self):
        from garimpo.web import paginas, ramos
        lista = [paginas.Pagina(**c) for c in ramos.paginas(self.dados("2026-09-14T12:00:00+00:00"), self.TODOS)]
        mapa = paginas.sitemap(lista, "https://ex.br")
        self.assertIn("https://ex.br/dominios/pet/", mapa)
        self.assertNotIn("https://ex.br/dominios/saude/", mapa)
        self.assertNotIn("dominios/saude", paginas.llms_txt(lista, "https://ex.br"))
        layout = open(os.path.join(os.path.dirname(__file__), "site_modelo", "layout.html"), encoding="utf-8").read()
        rala = next(p for p in lista if p.slug == "dominios/saude")
        self.assertIn('content="noindex, follow"', paginas.render(rala, layout, {}))
        pet = next(p for p in lista if p.slug == "dominios/pet")
        html = paginas.render(pet, layout, self.dados("2026-09-14T12:00:00+00:00"))
        self.assertNotIn("noindex", html)
        self.assertIn('"ItemList"', html)
        self.assertIn('<a class="aba" href="/" aria-current="page">', html)


class TestPagina404(unittest.TestCase):
    """
    Endereco que nao existe (ex.: as URLs /products/... que o Google ainda
    conhece da loja que usou o dominio antes) precisa de corpo: sem um
    404.html na raiz, o servidor de estaticos responde vazio.
    """

    def test_conteudo_da_pagina(self):
        from garimpo.web import paginas
        erro = paginas.pagina_404()
        self.assertFalse(erro.indexavel)
        self.assertLessEqual(len(erro.descricao), 160)
        layout = open(os.path.join(os.path.dirname(__file__), "site_modelo", "layout.html"),
                      encoding="utf-8").read()
        html = paginas.render(erro, layout, {}, base="https://ex.br")
        self.assertIn("<h1>Esta página não existe</h1>", html)
        self.assertIn('content="noindex, follow"', html)
        self.assertIn('href="/perguntas/"', html)
        self.assertIn('href="/sobre/"', html)
        self.assertIn('id="ficha-404-nome"', html)
        self.assertIn('<script src="/erro404.js"></script>', html)
        # sem method/action: a CSP tem form-action 'none' (cabecalhos HTTP do site),
        # um <form> de verdade nunca submeteria; erro404.js e que navega
        self.assertNotIn("action=", html)


class TestNavegacaoNoCelular(unittest.TestCase):
    """
    Regressao: a aba ativa ficava fora da
    tela em 5 das 8 secoes a 390px, um link para um trecho abria com o
    titulo escondido sob o cabecalho fixo (768 a 1279px), Shift+Tab podia
    deixar o foco coberto (WCAG 2.4.11) e nao havia "Pular para o
    conteudo".
    """

    def setUp(self):
        self.raiz = os.path.dirname(__file__)
        self.modelo = os.path.join(self.raiz, "site_modelo")
        self.tema_js = open(os.path.join(self.modelo, "tema.js"), encoding="utf-8").read()
        self.extra_css = open(os.path.join(self.modelo, "extra.css"), encoding="utf-8").read()
        self.layout = open(os.path.join(self.modelo, "layout.html"), encoding="utf-8").read()
        self.style_css = open(os.path.join(self.raiz, "web", "style.css"), encoding="utf-8").read()

    def test_pular_para_o_conteudo_e_primeiro_filho_do_body(self):
        from garimpo.web import paginas
        inicio = next(p for p in paginas.carregar(self.modelo) if p.slug == "")
        html = paginas.render(inicio, self.layout, {}, base="https://ex.br")
        corpo = html.split("<body>", 1)[1]
        # o link de pular vem antes de qualquer outro elemento visivel, e
        # main leva tabindex para receber o foco quando o link e ativado
        self.assertLess(corpo.index('class="pular"'), corpo.index("<header>"))
        self.assertIn('href="#conteudo"', corpo)
        self.assertIn('<main id="conteudo" tabindex="-1"', html)

    def test_pular_nao_mexe_no_script_inline_do_head(self):
        # o hash da CSP e calculado sobre o script inline do <head>: a
        # navegacao so mexe no <body>
        cabeca, resto = self.layout.split("<body>", 1)
        self.assertIn("localStorage.getItem(\"tema\")", cabeca)
        self.assertNotIn("pular", cabeca)

    def test_tema_js_rola_a_aba_ativa_mesmo_sem_botao_de_tema(self):
        # o script tem de rodar mesmo em paginas sem #btn-tema (nenhuma
        # hoje, mas o guard "if (!botao) return" barrava tudo que vinha
        # depois dele); a rolagem e a altura do cabecalho ficam antes
        antes_do_guard = self.tema_js.split("if (!botao) return;")[0]
        self.assertIn('aria-current="page"', antes_do_guard)
        self.assertIn("scrollLeft", antes_do_guard)
        self.assertIn("--altura-cabecalho", antes_do_guard)
        self.assertIn("ResizeObserver", antes_do_guard)

    def test_seta_das_abas_fora_da_faixa_e_so_no_celular(self):
        # a mascara sozinha nao avisa que as abas continuam. A seta nasce
        # antes do guard (roda em toda pagina), fica fora da faixa (a
        # mascara a esfumaria e um filho a mais tiraria o :last-child da
        # ultima aba) e some no fim da faixa, com folga de 1px para
        # scrollLeft fracionario
        antes_do_guard = self.tema_js.split("if (!botao) return;")[0]
        self.assertIn("abas-seta", antes_do_guard)
        self.assertIn("insertAdjacentElement('afterend'", antes_do_guard)
        self.assertIn("faixa.scrollLeft + faixa.clientWidth < faixa.scrollWidth - 1", antes_do_guard)
        self.assertNotIn(".style.", antes_do_guard.split("abas-seta", 1)[1].split("Publica a altura")[0])
        # escondida fora do celular; visivel so dentro do bloco de ate 64rem
        self.assertIn(".abas-seta { display: none; }", self.extra_css)
        bloco = self.extra_css.split(".abas-seta { display: none; }", 1)[1]
        self.assertTrue(bloco.lstrip().startswith("@media (max-width: 63.999rem)"))

    def test_extra_css_publica_scroll_padding_e_mascara_da_faixa(self):
        self.assertIn("scroll-padding-top: calc(var(--altura-cabecalho, 7rem)", self.extra_css)
        # a mascara e o padding-inline-end tem de usar o mesmo valor: com
        # 2.5rem de mascara contra 2rem de padding, o degrade cai dentro do
        # texto da ultima aba em vez de cair so no padding reservado
        mascara = re.search(r"mask-image: linear-gradient\(90deg, #000 calc\(100% - ([\d.]+rem)\)", self.extra_css)
        padding = re.search(r"padding-inline-end: ([\d.]+rem);", self.extra_css)
        self.assertIsNotNone(mascara)
        self.assertIsNotNone(padding)
        self.assertEqual(mascara.group(1), padding.group(1))
        self.assertIn("scroll-padding-inline-end: " + padding.group(1), self.extra_css)
        self.assertIn(".aba:last-child { scroll-snap-align: end; }", self.extra_css)
        # as duas margens (scroll-padding do html e scroll-margin do titulo)
        # nao podem voltar a somar 5.5rem + 7rem: o titulo ficaria coberto
        self.assertNotIn("scroll-margin-top: 5.5rem", self.extra_css)

    def test_style_css_tem_o_link_de_pular(self):
        self.assertIn(".pular {", self.style_css)
        self.assertIn(".pular:focus { top:", self.style_css)


class TestEnsaioDasFases(unittest.TestCase):
    """
    A virada de outubro ensaiada em cinco instantes, no build e no app.js.
    O erro que ele pega: de 12/10 a 14/10 as 15h, o inicio dizendo "Rodada
    aberta", os ramos "candidatar-se e de graca ate 21/10" e a FAQ "a
    proxima e em 11/11".

    Os dados imitam o estado real: na virada o varrer.py esquece as leituras e
    nao consulta antes da abertura, entao a lista chega sem nenhuma leitura e a
    primeira so vem com a varredura seguinte a abertura.
    """

    STATUS = TestPaginasPorRamo.STATUS
    RODADA = {"inicio": "2026-10-14T15:00:00-03:00", "fim": "2026-10-21T15:00:00-03:00"}
    # instante, fase no build, fase no app.js, proxima rodada, com leitura?
    INSTANTES = [
        ("2026-10-12T09:00:00-03:00", "lista", "lista", "14/10/2026", False),
        ("2026-10-14T14:59:00-03:00", "lista", "lista", "14/10/2026", True),
        ("2026-10-14T15:01:00-03:00", "aberta", "aberta", "11/11/2026", False),
        ("2026-10-21T16:00:00-03:00", "fechada", "assentando", "11/11/2026", True),
        ("2026-10-22T12:00:00-03:00", "fechada", "assentando", "11/11/2026", True),
    ]
    FRASE_INICIO = ("A lista da rodada de outubro saiu: as candidaturas abrem em 14/10, às 15h, "
                    "e vão até 21/10, às 15h (horário de Brasília).")
    FRASE_RAMOS = ("A lista de outubro saiu; as candidaturas abrem em 14/10 às 15h e vão até "
                   "21/10 às 15h")
    TODOS = {"extensoes": ["com.br"], "motivos": [], "marcas": ["OK", "ATENCAO", "RISCO"],
             "itens": [[f"pet{i:02d}", 0, 70 - i, [], 0, 1] for i in range(14)]
             + [["petmarca", 0, 99, [], 2, 1]]}

    def dados(self, agora, com_leitura):
        itens = []
        if com_leitura:
            # 14/10 14h59: leitura de antes da abertura (status 5) nao vale nada;
            # depois do fim, o que sobrou
            situacao = 5 if agora < self.RODADA["inicio"] else 0
            itens = [[f"pet{i:02d}.com.br", situacao, 0, 70 - i, 0, [], 0, 0, 0, 0, 1, 0, 0]
                     for i in range(12)]
        return {"gerado_em": datetime.datetime.fromisoformat(agora).astimezone(
                    datetime.timezone.utc).isoformat(),
                "status": self.STATUS, "categorias": [{"nome": "pet", "rotulo": "Pet"}],
                "motivos": [], "total_rodada": 1000, "total_elegiveis": 0,
                "nao_verificados": 1000 - len(itens), "itens": itens, "rodada": dict(self.RODADA)}

    def test_build_nos_cinco_instantes(self):
        from garimpo.web import paginas, ramos
        modelo = os.path.join(os.path.dirname(__file__), "site_modelo")
        layout = open(os.path.join(modelo, "layout.html"), encoding="utf-8").read()
        inicio = next(p for p in paginas.carregar(modelo) if p.slug == "")
        for agora, fase, _, proxima, com_leitura in self.INSTANTES:
            with self.subTest(agora=agora):
                d = self.dados(agora, com_leitura)
                self.assertEqual(paginas.fase_da_rodada(d), fase)
                self.assertIn(f">{proxima}<", paginas.preencher_numeros(
                    '<span id="p-proxima-rodada">-</span>', d))
                html = paginas.render(inicio, layout, d)
                linha = re.search(r'id="fase-inicio">(.*?)</p>', html, re.S).group(1)
                passos = paginas.passos_da_pagina(d)
                pet = next(c for c in ramos.paginas(d, self.TODOS) if c["slug"] == "dominios/pet")
                self.assertNotIn("Conferimos 0", pet["corpo"])
                self.assertNotIn("petmarca", pet["corpo"])
                self.assertTrue(pet["indexavel"])
                if fase == "lista":
                    self.assertEqual(linha, self.FRASE_INICIO)
                    self.assertIn("Candidate-se de 14/10, 15h, a 21/10, 15h", passos)
                    self.assertIn('href="/dominios/"', passos)   # link nos passos
                    self.assertIn(self.FRASE_RAMOS, pet["corpo"])
                    self.assertIn("a conferência de quem já pediu cada nome começa com a rodada, "
                                  "em 14/10, às 15h", pet["corpo"])
                    self.assertIn("a conferir a partir de 14/10", pet["corpo"])
                    # 15 na lista (a marca conta), 14 listados
                    self.assertIn("<strong>15</strong> domínios .br de pet", pet["corpo"])
                    self.assertIn("Ver os 14 na lista", pet["corpo"])
                    self.assertIn("a conferência de quem já pediu cada nome começa com a rodada, "
                                  "em 14/10, às 15h", html)
                    for errado in ("Rodada aberta", "Candidaturas ainda valem", "andidate-se agora",
                                   "até o fim da rodada", "Candidatar-se é de graça até",
                                   "estavam sem candidato visível", "Situação na última leitura"):
                        self.assertNotIn(errado, html + pet["corpo"])
                elif fase == "aberta":
                    self.assertIn("Rodada atual: de", linha)
                    self.assertEqual(passos, "")
                    # os passos ficam os do modelo (ferramenta.html), que ja
                    # linka /dominios/ no primeiro, sem precisar
                    # de passos_da_pagina tambem preencher a aberta
                    passos_html = re.search(r'<ol class="passos"[^>]*>(.*?)</ol>', html, re.S).group(1)
                    self.assertIn('href="/dominios/"', passos_html)
                    # abriu e a primeira leitura ainda nao chegou: a lista, sem "0"
                    self.assertIn("Candidatar-se é de graça até 21/10/2026", pet["corpo"])
                    self.assertIn("a conferência de quem já pediu cada nome começou com a rodada, "
                                  "em 14/10, às 15h, e a primeira leitura chega nas próximas "
                                  "horas.</p>", pet["corpo"])
                    self.assertNotIn(self.FRASE_RAMOS, pet["corpo"])
                    # sem nenhum nome conferido: nem "sem concorrente a vista" no
                    # inicio, nem "leitura ate" no indice dos ramos
                    sub = re.search(r'id="p-subtitulo">(.*?)</p>', html, re.S).group(1)
                    self.assertNotIn("sem concorrente à vista", sub)
                    self.assertIn("começou com a rodada, em 14/10, às 15h; a primeira leitura "
                                  "chega nas próximas horas", sub)
                    indice = next(c for c in ramos.paginas(d, self.TODOS) if c["slug"] == "dominios")
                    self.assertNotIn("leitura até", indice["corpo"])
                    self.assertIn("a primeira leitura chega nas próximas horas", indice["corpo"])
                else:
                    self.assertIn("Candidate-se de 11/11 a 18/11", passos)
                    self.assertIn('href="/dominios/"', passos)   # link nos passos
                    self.assertIn("A rodada fechou", pet["corpo"])
                    self.assertNotIn(self.FRASE_RAMOS, pet["corpo"])

    def _app_js(self):
        return _fonte_da_lista()

    def test_app_js_nos_cinco_instantes(self):
        """O mesmo ensaio no faseDaRodada, em dois fusos: a frase e sempre a de Brasilia."""
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = self._app_js()
        trechos = [re.search(r"const FUSO = .*?;", js).group(0),
                   re.search(r"function horaCurta\(d\) \{.*?\n\}", js, re.S).group(0),
                   re.search(r"const MESES_EXTENSO = \[.*?\];", js, re.S).group(0),
                   re.search(r"const HORAS_DE_AVISO.*?\nfunction faseDaRodada.*?\n\}", js, re.S).group(0)]
        instantes = [a for a, *_ in self.INSTANTES]
        roteiro = "\n".join(trechos) + (
            f"\nfor (const a of {json.dumps(instantes)}) console.log(JSON.stringify("
            f"faseDaRodada({json.dumps(self.RODADA['inicio'])}, {json.dumps(self.RODADA['fim'])}, new Date(a))));")
        for fuso in ("America/Sao_Paulo", "America/Manaus", "Europe/Lisbon"):
            with self.subTest(fuso=fuso):
                saida = subprocess.run([node, "-e", roteiro], capture_output=True, text=True, check=True,
                                       env={**os.environ, "TZ": fuso}).stdout.splitlines()
                r = [json.loads(l) for l in saida]
                self.assertEqual([x["fase"] for x in r], [f for _, _, f, _, _ in self.INSTANTES])
                self.assertEqual(r[0]["completo"], self.FRASE_INICIO)
                self.assertEqual(r[1]["curto"], "abre em 14/10")
                self.assertEqual((r[2]["dia"], r[2]["hora"]), ("21/10", "15:00"))
                self.assertIn("(horário de Brasília)", r[2]["completo"])

    def test_app_js_so_aceita_agora_na_previa_e_para_o_relogio(self):
        js = self._app_js()
        desvio = js[js.index("const DESVIO_DO_RELOGIO"):js.index("const agoraMs")]
        self.assertIn("['127.0.0.1', 'localhost'].includes(location.hostname)", desvio)
        # depois do ultimo marco o relogio para de vez
        tique = js[js.index("function tiqueDoRelogio"):js.index("function pararRelogio")]
        self.assertIn("relogio.alvo = null;", tique)
        andar = js[js.index("function andarRelogio"):js.index("function iniciarRelogio")]
        self.assertIn("if (!relogio.alvo) return;", andar[andar.index("tiqueDoRelogio();"):])
        # nenhum "new Date()" sem argumento sobra nas contas de fase e relogio
        for nome in ("function rodadaFechada", "function proximaAberturaDoCalendario",
                     "function pintarBotaoDaRodada", "function marcoPadrao"):
            corpo = js[js.index(nome):]
            corpo = corpo[:corpo.index("\n}\n")]
            self.assertNotIn("new Date()", corpo)
            self.assertNotIn("Date.now()", corpo)


class TestArquivoDoDominio(unittest.TestCase):
    """
    O indice "este dominio ja teve site?" (garimpo/dominio/arquivo.py).

    O caso de teste e o pneus.com.br de verdade, lido do sparkline em
    18/09/2026: e um caso facil de ler errado (redirecionamento tomado por
    abandono), e se o indice o errar, erra tudo.
    """

    # recorte fiel do sparkline do pneus.com.br: 1999 serviu pagina, 2019
    # redirecionou (maio, o mes em que passou a apontar para sunset-tires)
    PNEUS = {"years": {"1999": [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                       "2019": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0]},
             "status": {"1999": "244444444444", "2019": "444434444444"}}

    def test_le_so_os_meses_com_captura(self):
        """
        A letra "4" de enchimento nos meses vazios NAO e erro: sem o filtro
        por `years`, o pneus.com.br viraria 22 meses de 404 inventados.
        """
        from garimpo.dominio import arquivo
        r = arquivo.de_sparkline("pneus.com.br", self.PNEUS)
        self.assertEqual((r.primeira, r.ultima), ("199901", "201905"))
        self.assertEqual(r.capturas, 2)
        self.assertEqual(r.servindo, 1)          # jan/1999
        self.assertEqual(r.redirecionando, 1)    # mai/2019
        self.assertEqual(r.estado, arquivo.SERVIU)

    def test_tres_estados(self):
        from garimpo.dominio import arquivo
        nada = arquivo.de_sparkline("x.com.br", {"years": {}, "status": {}})
        self.assertEqual(nada.estado, arquivo.NUNCA)
        so_redir = arquivo.de_sparkline("y.com.br", {
            "years": {"2020": [1] + [0] * 11}, "status": {"2020": "3" + "4" * 11}})
        self.assertEqual(so_redir.estado, arquivo.REDIRECIONOU)

    def test_linha_vai_e_volta(self):
        from garimpo.dominio import arquivo
        r = arquivo.de_sparkline("pneus.com.br", self.PNEUS, "20260918")
        self.assertEqual(arquivo.ler_linha(arquivo.linha(r)), r)
        self.assertIsNone(arquivo.ler_linha("lixo"))
        self.assertIsNone(arquivo.ler_linha("a\tb\tc\tnao-numero\t0\t0\tx"))

    def test_mesma_fatia_das_passagens(self):
        """O navegador acha as duas coisas pelo mesmo hash, ja escrito em JS."""
        from garimpo.dominio import arquivo, passagens
        r = arquivo.de_sparkline("pneus.com.br", self.PNEUS)
        (chave,) = arquivo.agrupar([r]).keys()
        self.assertEqual(chave, passagens.fatia("pneus.com.br"))

    def test_cdx_da_o_mesmo_numero(self):
        """A queda para a CDX nao pode mudar a resposta."""
        from garimpo.adaptadores.wayback import Captura
        from garimpo.dominio import arquivo
        capturas = [Captura("199901", "200"), Captura("201905", "301")]
        via_cdx = arquivo.de_capturas("pneus.com.br", capturas)
        via_spark = arquivo.de_sparkline("pneus.com.br", self.PNEUS)
        self.assertEqual(via_cdx, via_spark)

    def test_bloqueio_e_recusa_de_conexao_tambem(self):
        """F9: o Internet Archive barra recusando a conexao, nao com 429."""
        import urllib.error
        from garimpo.adaptadores import wayback
        self.assertTrue(wayback._e_bloqueio(
            OSError("<urlopen error [Errno 111] Connection refused>")))
        self.assertTrue(wayback._e_bloqueio(
            OSError("_ssl.c:993: The handshake operation timed out")))
        self.assertTrue(wayback._e_bloqueio(
            urllib.error.HTTPError("u", 429, "x", None, None)))
        self.assertFalse(wayback._e_bloqueio(
            urllib.error.HTTPError("u", 503, "x", None, None)))


class TestLinksDoDominio(unittest.TestCase):
    """
    O indice "quem aponta para este dominio?" (garimpo/dominio/links.py),
    montado com o grafo de dominios do CommonCrawl.
    """

    def test_notacao_invertida_vai_e_volta(self):
        from garimpo.dominio import links
        self.assertEqual(links.invertido("Bicharia.com.br "), "br.com.bicharia")
        self.assertEqual(links.direto("br.com.bicharia"), "bicharia.com.br")

    def test_resumo_conta_cada_dominio_uma_vez_e_ignora_o_proprio(self):
        from garimpo.dominio import links
        r = links.resumir("bicharia.com.br", "jul-set/2026", 133_000_000, 4_000_000, 20_000_000, [
            ("globo.com", 900), ("globo.com", 50),        # repetido: vale a melhor posicao
            ("bicharia.com.br", 1),                        # autolink nao conta
            ("abril.com.br", 3_000), ("vakinha.com.br", 2_000_000),
            ("a.net", 70_000_000), ("b.net", 80_000_000), ("c.org.br", 90_000_000),
            ("blogspot.com", 40), ("google.com.br", 30),   # plataformas
        ])
        self.assertEqual(r.referentes, 8)
        self.assertEqual(r.referentes_br, 4)
        self.assertEqual(r.fortes, 2)                      # globo e abril; plataforma nao
        self.assertEqual(r.plataformas, 2)
        self.assertEqual(r.principais, ("globo.com", "abril.com.br", "vakinha.com.br",
                                        "a.net", "b.net"))

    def test_plataforma_pelo_primeiro_rotulo(self):
        from garimpo.dominio import links
        for d in ("blogspot.com", "blogspot.com.br", "google.com", "google.de",
                  "linktr.ee", "wixsite.com", "t.co", "x.com"):
            self.assertTrue(links.plataforma(d), d)
        for d in ("globo.com", "wikipedia.org", "usp.br", "googleblog.com", "abril.com.br",
                  "academia.org.br", "x.org", "web.de", "archive.com.br", "medium.com.br"):
            self.assertFalse(links.plataforma(d), d)
        for d in ("academia.edu", "archive.org", "medium.com", "myshopify.com"):
            self.assertTrue(links.plataforma(d), d)

    def test_linha_vai_e_volta(self):
        from garimpo.dominio import links
        r = links.resumir("x.com.br", "jul-set/2026", 100, 5, 7, [("a.com", 1), ("b.com.br", 2)])
        self.assertEqual(links.ler_linha(links.linha(r)), r)
        sem = links.resumir("y.com.br", "jul-set/2026", 100, 90, 90, [])
        self.assertEqual(links.ler_linha(links.linha(sem)), sem)
        self.assertEqual(links.linha(sem).split("\t")[-1], "")
        self.assertIsNone(links.ler_linha("lixo"))
        self.assertIsNone(links.ler_linha("a\tb\tnao-numero\t1\t1\t1\t1\t1\t0\t"))

    def test_mesma_fatia_das_passagens(self):
        from garimpo.dominio import links, passagens
        r = links.resumir("pneus.com.br", "g", 1, 1, 1, [])
        (chave,) = links.agrupar([r]).keys()
        self.assertEqual(chave, passagens.fatia("pneus.com.br"))

    def _ficha(self, linha_do_indice, nome="bicharia.com.br"):
        """Roda linksDoNome do ficha.js no node, com fetch e DOM falsos."""
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = open(os.path.join(os.path.dirname(__file__), "site_modelo", "ficha.js"),
                  encoding="utf-8").read()
        pedacos = [re.search(r"  const esc = .*?\[c\]\)\);", js, re.S).group(0),
                   re.search(r"  const LINKS = .*?;", js).group(0),
                   re.search(r"  const TOPO_PCT = .*?;", js).group(0),
                   re.search(r"  const MUITOS = .*?;", js).group(0)]
        for nome_fn in ("fnv1a32", "faixaDaWeb", "doDoBr", "linksDoNome"):
            pedacos.append(re.search(r"  (async )?function " + nome_fn + r"\(.*?\n  \}", js, re.S).group(0))
        roteiro = "\n".join(pedacos) + f"""
const bloco = {{ innerHTML: '', querySelector: () => null }};
const alvo = {{ dataset: {{ nome: {json.dumps(nome)} }}, querySelector: () => bloco }};
globalThis.fetch = async () => ({{ ok: true, text: async () => {json.dumps(linha_do_indice)} }});
linksDoNome({json.dumps(nome)}, alvo).then(() => process.stdout.write(bloco.innerHTML));
"""
        return subprocess.run([node, "-e", roteiro], capture_output=True, text=True,
                              check=True).stdout

    def test_ficha_mostra_quem_aponta_com_data_e_denominador(self):
        linha = ("bicharia.com.br\tjul-set/2026\t133241980\t4354345\t23858443\t190\t150\t12\t"
                 "0\tglobo.com,abril.com.br,<b>x</b>.com\n")
        html = self._ficha(linha)
        self.assertIn("190 domínios apontam", html)
        self.assertIn("jul-set/2026", html)
        self.assertIn("150 deles <code>.br</code>", html)
        self.assertIn("12 deles estão entre o 1 milhão", html)
        self.assertIn("de 133 milhões", html)
        self.assertIn("entre os 4% mais citados", html)
        # os nomes vao para a lista agrupada logo abaixo, nunca em chips no texto
        self.assertNotIn("globo.com", html)
        self.assertIn('<div class="ficha-referentes"></div>', html)
        self.assertNotIn("<b>", html)                  # nome vindo de fora e escapado
        self.assertIn("é um piso", html)
        self.assertIn("não garante posição no Google", html)
        self.assertNotIn("spam", html.split("ficha-nota")[0])

    def test_ficha_na_cauda_nao_da_posicao_e_convida_a_conferir_spam(self):
        """
        Na cauda do grafo milhoes de nos empatam: a posicao do nome nao
        aparece. Muitos referentes sem nenhum muito citado e o padrao da
        fazenda de links: a ficha convida a conferir, sem dar veredito.
        """
        linha = ("camisetacriativa.com.br\tjul-set/2026\t133241980\t60000000\t70000000\t"
                 "45\t0\t0\t0\tspam1.com,spam2.net\n")
        html = self._ficha(linha, "camisetacriativa.com.br")
        self.assertIn("45 domínios apontam", html)
        self.assertIn("Nenhum deles está entre o 1 milhão", html)
        self.assertNotIn("% mais citados", html)
        self.assertIn("vale conferir se não vêm de spam", html)
        self.assertNotIn("<code>spam1.com</code>", html)
        poucos = linha.replace("\t45\t", "\t2\t")
        html = self._ficha(poucos, "camisetacriativa.com.br")
        self.assertNotIn("spam antes", html)

    def test_ficha_concorda_no_singular(self):
        um = "a.com.br\tjul-set/2026\t133241980\t900000\t900000\t1\t1\t1\t0\tglobo.com\n"
        html = self._ficha(um, "a.com.br")
        self.assertIn("Um domínio aponta", html)
        self.assertIn("Ele está entre o 1 milhão", html)
        self.assertIn(", e ele é <code>.br</code>", html)
        self.assertNotIn("deles", html)
        dois = "a.com.br\tjul-set/2026\t133241980\t90000000\t9\t2\t0\t0\t1\tpequeno.net\n"
        html = self._ficha(dois, "a.com.br")
        self.assertIn("Um deles é plataforma", html)
        self.assertIn("O outro não está entre", html)
        self.assertNotIn("1 deles", html)

    def test_ficha_calada_com_numero_estragado(self):
        for ruim in ("a.com.br\tg\tx\t1\t1\t3\t0\t0\t0\t\n",
                     "a.com.br\tg\t100\t1\t1\tnada\t0\t0\t0\t\n",
                     "a.com.br\tg\t0\t1\t1\t3\t0\t0\t0\t\n"):
            self.assertEqual(self._ficha(ruim, "a.com.br"), "", ruim)

    def test_ficha_e_indice_concordam_no_numero_de_colunas(self):
        from garimpo.dominio import links
        js = open(os.path.join(os.path.dirname(__file__), "site_modelo", "ficha.js"),
                  encoding="utf-8").read()
        self.assertIn(f"campos.length !== {links.COLUNAS}", js)

    def test_ficha_separa_plataforma_de_citacao(self):
        """
        O caso real do 79bet.tv.br no grafo jul-set/2026: 6 referentes, e os
        "fortes" eram google.com, myshopify.com e parecidos. Plataforma nao
        pode virar "site conhecido citou", nem esconder o convite a conferir.
        """
        linha = ("aposta.tv.br\tjul-set/2026\t133241980\t181588\t795882\t14\t0\t0\t3\t"
                 "programujte.com,metooo.es\n")
        html = self._ficha(linha, "aposta.tv.br")
        self.assertIn("3 deles são plataformas onde qualquer um publica", html)
        self.assertIn("Nenhum dos outros está entre o 1 milhão", html)
        self.assertNotIn("% mais citados", html)          # posicao so com citacao de verdade
        self.assertIn("vale conferir se não vêm de spam", html)   # 11 sem plataforma
        so = ("so.com.br\tjul-set/2026\t133241980\t9000000\t9000000\t2\t0\t0\t2\t\n")
        html = self._ficha(so, "so.com.br")
        self.assertIn("Todos são plataformas", html)
        self.assertNotIn("Nenhum", html)

    def test_ficha_calada_sem_linha_ou_com_linha_de_outro_formato(self):
        """Fora do indice e "o CommonCrawl nao viu", nunca "sem links"."""
        self.assertEqual(self._ficha("outro.com.br\tg\t1\t1\t1\t0\t0\t0\t0\t\n"), "")
        self.assertEqual(self._ficha("bicharia.com.br\t201201\t201701\t9\t9\t0\t20260917\n"), "")


class TestLinksNaNotaENaLista(unittest.TestCase):
    """
    O indice de links vira nota (dois niveis) e etiqueta ("N links"). Sem o
    indice (clone sem docs/historico/), tudo fica como antes.
    """

    LINKS = {"forte.com.br": (12, 3), "medio.com.br": (2, 0),
             "citado.com.br": (1, 1), "um.com.br": (1, 0), "um.net.br": (1, 0)}

    def _vocab(self, links=None):
        from garimpo.adaptadores.dicionarios import Vocabularios
        from garimpo.dominio.relevancia import Lexico
        return Vocabularios(frozenset(), frozenset(), Lexico(links=links or {}))

    def test_dois_niveis_e_um_link_so_conta_pouco_no_com_br(self):
        from garimpo.dominio import relevancia as r
        sem = self._vocab()
        com = self._vocab(self.LINKS)
        def delta(nome):
            return pool.nota_de(nome, com).valor - pool.nota_de(nome, sem).valor
        self.assertEqual(delta("forte.com.br"), r.PESO_LINKS_CITADO)
        self.assertEqual(delta("medio.com.br"), r.PESO_LINKS)
        self.assertEqual(delta("citado.com.br"), r.PESO_LINKS)     # 1 so, mas muito citado
        self.assertEqual(delta("um.com.br"), r.PESO_UM_REFERENTE_COM_BR)
        self.assertEqual(delta("um.net.br"), 0)                    # lista de vencidos
        self.assertEqual(delta("fora.com.br"), 0)
        self.assertIn("links de sites muito citados",
                      pool.nota_de("forte.com.br", com).motivos)

    def test_links_fortes_entram_na_conferencia_com_qualquer_nota(self):
        """
        Nome com links fortes entra no pool abaixo do corte, como o
        elegivel: senao a lista mostra "nao verificado" justo nele.
        """
        from garimpo.adaptadores.registrobr import Rodada
        rodada = Rodada(liberacao=["xqzw-1.com.br", "xqzw-2.com.br", "xqzw-3.com.br"],
                        elegiveis=set(), em_leilao=set(), inicio=None, fim=None)
        vocab = self._vocab({"xqzw-1.com.br": (30, 0), "xqzw-2.com.br": (6, 1),
                             "xqzw-3.com.br": (4, 0)})
        self.assertLess(pool.nota_de("xqzw-1.com.br", vocab).valor, pool.NOTA_MINIMA)
        candidatos, resumo = pool.montar(rodada, vocab)
        self.assertEqual(sorted(c.dominio for c in candidatos), ["xqzw-1.com.br", "xqzw-2.com.br"])
        self.assertEqual(resumo.descartados, 1)
        self.assertTrue(all(c.fonte == "liberacao" for c in candidatos))
        # sem o indice, nada muda
        self.assertEqual(pool.montar(rodada, self._vocab())[0], [])
        # links_minimo: quem tem esse tanto de referentes tambem entra
        com = pool.montar(rodada, vocab, links_minimo=2)[0]
        self.assertIn("xqzw-3.com.br", {c.dominio for c in com})
        alto = pool.montar(rodada, vocab, links_minimo=5)[0]
        self.assertNotIn("xqzw-3.com.br", {c.dominio for c in alto})

    def test_visitas_fortes_entram_na_conferencia_sem_pesar_na_nota(self):
        from garimpo.adaptadores.dicionarios import Vocabularios
        from garimpo.adaptadores.registrobr import Rodada
        from garimpo.dominio.relevancia import Lexico
        rodada = Rodada(liberacao=["xqzw-1.com.br", "xqzw-2.com.br"], elegiveis=set(),
                        em_leilao=set(), inicio=None, fim=None)
        vocab = Vocabularios(frozenset(), frozenset(),
                             Lexico(trafego={"xqzw-1.com.br": 100000, "xqzw-2.com.br": 500000}))
        self.assertEqual(pool.nota_de("xqzw-1.com.br", vocab).valor,
                         pool.nota_de("xqzw-1.com.br", self._vocab()).valor)
        self.assertEqual([c.dominio for c in pool.montar(rodada, vocab)[0]], ["xqzw-1.com.br"])

    def test_criterio_na_pagina_sai_do_modulo(self):
        from garimpo.casos import instantaneo
        from garimpo.dominio import relevancia as r
        pesos = {b["peso"] for b in instantaneo.criterios(45)["bonus"]}
        self.assertTrue({r.PESO_LINKS_CITADO, r.PESO_LINKS} <= pesos)

    def test_dados_json_leva_os_links_no_fim(self):
        from garimpo.casos import instantaneo
        from garimpo.dominio.situacao import Situacao
        c = Candidato(dominio="forte.com.br", nota=80, situacao=Situacao.LIBERACAO_LIVRE)
        d = Candidato(dominio="nada.com.br", nota=80, situacao=Situacao.LIBERACAO_LIVRE)
        meta = instantaneo.Metadados(gerado_em="x")
        itens = {i[0]: i for i in instantaneo.exportar([c, d], meta, links=self.LINKS)["itens"]}
        self.assertEqual(itens["forte.com.br"][instantaneo.I_LINKS], [12, 3])
        self.assertEqual(itens["nada.com.br"][instantaneo.I_LINKS], 0)
        # e o leitor antigo continua lendo o item
        self.assertEqual([x.dominio for x in instantaneo.candidatos_de(
            instantaneo.exportar([c], meta, links=self.LINKS))], ["forte.com.br"])

    def test_todos_json_so_ganha_o_campo_quem_tem_link(self):
        from garimpo.adaptadores.registrobr import Rodada
        from garimpo.casos import todos
        rodada = Rodada(liberacao=["forte.com.br", "nada.com.br"], elegiveis=set(),
                        em_leilao=set(), inicio=None, fim=None)
        itens = {i[0]: i for i in todos.montar(rodada, self._vocab(self.LINKS))["itens"]}
        self.assertEqual(itens["forte"][6], [12, 3])
        self.assertEqual(len(itens["nada"]), 6)

    def test_le_o_indice_sem_plataforma_e_sem_pasta(self):
        from garimpo.adaptadores import dicionarios
        from garimpo.dominio import links
        pasta = tempfile.mkdtemp()
        try:
            r = links.resumir("x.com.br", "g", 100, 5, 5,
                              [("globo.com", 3), ("blogspot.com", 1), ("a.net", 90_000_000)])
            for chave, linhas in links.agrupar([r]).items():
                with open(os.path.join(pasta, f"{chave}.txt"), "w") as f:
                    f.write("\n".join(linhas) + "\n")
            self.assertEqual(dicionarios.Repositorio(pasta, links=pasta).links(),
                             {"x.com.br": (2, 1)})              # blogspot fora
            self.assertEqual(dicionarios.Repositorio(pasta, links=None).links(), {})
            self.assertEqual(dicionarios.Repositorio(pasta, links=pasta + "/nao").links(), {})
        finally:
            import shutil
            shutil.rmtree(pasta)

    def _etiqueta(self, lk):
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = _fonte_da_lista()
        pedacos = [re.search(r"^const LK = .*?;", js, re.M).group(0), "const D = 0;"]
        for nome in ("esc", "num", "linksDe", "etiquetaLinks"):
            pedacos.append(re.search(r"^function " + nome + r"\(.*?\n\}", js, re.S | re.M).group(0))
        roteiro = "\n".join(pedacos) + f"\nprocess.stdout.write(etiquetaLinks({json.dumps(['x.com.br'] + [0] * 12 + [lk])}));"
        return subprocess.run([node, "-e", roteiro], capture_output=True, text=True,
                              check=True).stdout

    def _links_js(self, chamada):
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = open(os.path.join(os.path.dirname(__file__), "site_modelo", "lista", "links.js"),
                  encoding="utf-8").read()
        util = _fonte_da_lista()
        pedacos = [re.search(r"^function " + n + r"\(.*?\n\}", util, re.S | re.M).group(0)
                   for n in ("esc", "num")]
        pedacos += [re.search(r"^const " + n + r" = \{.*?\n\};", js, re.S | re.M).group(0)
                    for n in ("MARCAS", "EXPLICA")]
        pedacos += [re.search(r"^const " + n + r" = .*?;$", js, re.M).group(0) for n in ("MUITOS", "SPAM")]
        pedacos += [re.search(r"^function " + n + r"\(.*?\n\}", js, re.S | re.M).group(0)
                    for n in ("fnv1a32", "fatiaDe", "grupos")]
        return subprocess.run([node, "-e", "\n".join(pedacos) + f"\nprocess.stdout.write({chamada});"],
                              capture_output=True, text=True, check=True).stdout

    def test_lista_de_quem_aponta_acha_a_fatia_e_agrupa(self):
        from garimpo.dominio import passagens
        for nome in ("groupon.com.br", "bicharia.com.br", "café.com.br"):
            self.assertEqual(self._links_js(f"fatiaDe({json.dumps(nome)})"), passagens.fatia(nome))
        html = self._links_js(json.dumps([["p", "blogspot.com"], ["c", "globo.com"],
                                          ["", "a.net"], ["", "<b>x</b>.com"]]).join(("grupos(", ")")))
        self.assertLess(html.index("Muito citados"), html.index("Outros sites"))
        self.assertLess(html.index("Outros sites"), html.index("Plataformas"))
        self.assertIn("<li>globo.com</li>", html)
        self.assertIn("(2)", html)                                  # os dois "outros"
        self.assertNotIn("<b>", html)                               # nome de fora escapado
        self.assertNotIn("<a ", html)                               # texto, nunca link
        # o peso diz o grupo: muito citado em destaque, plataforma apagada
        self.assertIn('<ul class="links-lista citados"><li>globo.com</li>', html)
        self.assertIn('<ul class="links-lista plataformas">', html)
        self.assertIn('<h3 class="links-titulo">', html)
        self.assertNotIn("spam", html)                              # poucos e um citado
        self.assertIn('<h4 class="links-titulo">', self._links_js('grupos([["", "a.net"]], "h4")'))
        # muitos e nenhum muito citado: o convite a conferir, como na ficha
        muitos = json.dumps([["", f"s{i}.net"] for i in range(10)])
        self.assertIn("vale conferir se não vêm de spam", self._links_js(f"grupos({muitos})"))
        self.assertNotIn("spam", self._links_js(f"grupos({muitos[:-1]}, [\"c\", \"globo.com\"]])"))

    def test_etiqueta_na_lista(self):
        self.assertEqual(self._etiqueta(0), "")
        self.assertEqual(self._etiqueta([1, 0]), "")               # um so
        normal = self._etiqueta([3, 0])
        self.assertIn(">3 links<", normal)
        # e botao: abre a lista de quem aponta (lista/links.js)
        self.assertIn('<button type="button"', normal)
        self.assertIn('data-links="x.com.br"', normal)
        self.assertIn('aria-haspopup="dialog"', normal)
        self.assertIn("e ele é muito citado", self._etiqueta([1, 1]))
        self.assertNotIn("citado", normal.split(">")[0])
        forte = self._etiqueta([114, 30])
        self.assertIn('class="pilula-links citado"', forte)
        self.assertIn("114 links", forte)
        self.assertIn("30 deles muito citados", forte)             # leitor de tela ouve
        self.assertIn("1 link", self._etiqueta([1, 1]))


class TestPassadoDoDominio(unittest.TestCase):
    """
    Os indices do passado alem do Wayback e dos links (garimpo/dominio/passado.py):
    trafego (CrUX), citacoes (GDELT, Wikipedia, Wikidata) e a classificacao
    do Cloudflare Intel, com as tres secoes da ficha que os leem.
    """

    def test_subdominio_conta_para_o_nome(self):
        from garimpo.dominio import passado
        nomes = {"bicharia.com.br", "loja.net.br"}
        self.assertEqual(passado.dono_da_origem("www.bicharia.com.br", nomes), "bicharia.com.br")
        self.assertEqual(passado.dono_da_origem("A.B.Bicharia.com.br.", nomes), "bicharia.com.br")
        self.assertIsNone(passado.dono_da_origem("outra.com.br", nomes))
        self.assertIsNone(passado.dono_da_origem("bicharia.com.br.evil.com", nomes))

    def test_trafego_vai_e_volta_com_a_melhor_faixa(self):
        from garimpo.dominio import passado
        t = passado.resumir_trafego("x.com.br", {"202501": 500000, "202104": 100000, "202608": 1000000}, 23)
        self.assertEqual((t.melhor, t.meses, t.primeiro, t.ultimo), (100000, 3, "202104", "202608"))
        self.assertEqual(passado.ler_trafego(passado.linha_trafego(t)), t)
        self.assertIsNone(passado.ler_trafego("lixo"))

    def test_citacoes_vao_e_voltam_com_rotulo_estranho(self):
        from garimpo.dominio import passado
        c = passado.Citacoes("x.com.br", 2, ("globo.com", "uol.com.br"), 3,
                             ("Brasil Telecom", "A | B, C"))
        volta = passado.ler_citacoes(passado.linha_citacoes(c))
        self.assertEqual(volta.oficial_de, ("Brasil Telecom", "A / B, C"))
        self.assertEqual((volta.veiculos, volta.principais, volta.artigos), (2, c.principais, 3))

    def test_classificacao_tira_ruido_junta_e_marca_o_ruim(self):
        from garimpo.dominio import passado
        hist = [
            {"categories": [{"id": 177}], "start": "2024-04-13T12:21Z", "end": "2025-12-20T23Z"},
            {"categories": [{"id": 75}], "start": "2021-03-01T00Z", "end": "2022-01-01T00Z"},
            {"categories": [{"id": 75}], "start": "2022-01-01T00Z", "end": "2024-06-01T00Z"},
            {"categories": [{"id": 99}, {"id": 182}, {"id": 176}], "start": "2025-12-20T23Z"},
            {"categories": [{"id": 9999}], "start": "2025-01-01T00Z"},   # sem rotulo: fora
        ]
        c = passado.classificar("x.com.br", hist, "20260930")
        self.assertEqual([(p.rotulo, p.inicio, p.fim) for p in c.periodos],
                         [("Negócios", "202103", "202406"), ("Apostas", "202512", "")])
        self.assertTrue(c.ruim)
        self.assertEqual(passado.ler_classificacao(passado.linha_classificacao(c)), c)
        vazio = passado.classificar("y.com.br", None, "20260930")
        self.assertEqual(passado.linha_classificacao(vazio), "y.com.br\t20260930\t")
        self.assertFalse(vazio.ruim)

    def test_toda_categoria_da_classificacao_tem_rotulo_ou_e_ruido(self):
        """A lista de 30/09/2026 (gateway/categories): nenhum id fica sem destino."""
        from garimpo.dominio import passado
        ids = {66, 195, 67, 125, 133, 170, 186, 75, 183, 89, 185, 182, 90, 91, 189, 144, 150,
               70, 74, 76, 79, 92, 96, 100, 106, 107, 116, 120, 121, 122, 127, 139, 156, 164,
               99, 190, 101, 137, 103, 146, 77, 98, 108, 110, 111, 118, 126, 129, 172, 168, 113,
               179, 166, 115, 119, 124, 141, 161, 85, 87, 102, 157, 135, 138, 180, 162, 140, 142,
               143, 169, 177, 128, 68, 178, 80, 187, 83, 176, 175, 117, 134, 131, 188, 191, 151,
               153, 73, 82, 88, 148, 65, 181, 71, 72, 173, 78, 84, 86, 94, 97, 104, 105, 112, 171,
               114, 174, 93, 130, 132, 136, 147, 149, 154, 158, 152, 69, 184, 81, 95, 109, 194,
               123, 192, 145, 193, 155, 159, 160, 163, 165, 167}
        self.assertEqual(ids - set(passado.CATEGORIAS) - passado.RUIDO, set())
        for cid in (99, 131, 133, 117, 191):
            self.assertTrue(passado.CATEGORIAS[cid][1], cid)
        self.assertFalse(passado.CATEGORIAS[75][1])
        self.assertFalse(passado.CATEGORIAS[128][1])      # estacionado: neutro

    def test_sinal_ruim_mais_recente_vai_aos_dois_json(self):
        from garimpo.adaptadores import dicionarios
        from garimpo.adaptadores.registrobr import Rodada
        from garimpo.casos import instantaneo, todos
        from garimpo.dominio import passado
        from garimpo.dominio.situacao import Situacao
        pasta = tempfile.mkdtemp()
        try:
            linhas = {
                "a.com.br": "a.com.br\t20260930\tr:Estacionado ou à venda:202103:202201;:Pets:202201:202405;r:Apostas:202512:",
                "b.com.br": "b.com.br\t20260930\t:Pets:202201:",
                "c.com.br": "c.com.br\t20260930\t",
            }
            for chave, ls in passado.agrupar(linhas).items():
                with open(os.path.join(pasta, f"{chave}.txt"), "w", encoding="utf-8") as f:
                    f.write("\n".join(ls) + "\n")
            sinais = dicionarios.Repositorio(pasta, categorias=pasta).sinais_ruins()
        finally:
            import shutil
            shutil.rmtree(pasta)
        self.assertEqual(sinais, {"a.com.br": "Apostas"})
        c = Candidato(dominio="a.com.br", nota=80, situacao=Situacao.LIBERACAO_LIVRE)
        item = instantaneo.exportar([c], instantaneo.Metadados(gerado_em="x"), sinais=sinais)["itens"][0]
        self.assertEqual(item[instantaneo.I_SINAL], "Apostas")
        rodada = Rodada(liberacao=["a.com.br", "b.com.br"], elegiveis=set(), em_leilao=set(),
                        inicio=None, fim=None)
        itens = {i[0]: i for i in todos.montar(rodada, ({"a"}, set()), sinais=sinais)["itens"]}
        self.assertEqual(itens["a"][6:], [0, "Apostas"])      # sem links: 0 no lugar deles
        self.assertEqual(len(itens["b"]), 6)

    def test_aviso_de_sinal_na_lista(self):
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = _fonte_da_lista()
        pedacos = [re.search(r"^const SN = .*?;", js, re.M).group(0),
                   re.search(r"^function esc\(.*?\n\}", js, re.S | re.M).group(0),
                   re.search(r"^function avisoDeSinal\(.*?\n\}", js, re.S | re.M).group(0)]
        def rodar(sinal):
            it = ["x.com.br"] + [0] * 13 + [sinal]
            return subprocess.run([node, "-e", "\n".join(pedacos) + f"\nprocess.stdout.write(avisoDeSinal({json.dumps(it)}));"],
                                  capture_output=True, text=True, check=True).stdout
        self.assertIn("classificado como apostas", rodar("Apostas"))
        self.assertIn("aviso-sinal", rodar("Apostas"))
        self.assertNotIn("<b>", rodar("<b>x</b>"))
        self.assertEqual(rodar(0), "")

    def _ficha(self, funcao, pasta_linha):
        import shutil
        node = shutil.which("node")
        if not node:
            self.skipTest("sem node")
        js = open(os.path.join(os.path.dirname(__file__), "site_modelo", "ficha.js"),
                  encoding="utf-8").read()
        pedacos = [re.search(r"  const esc = .*?\[c\]\)\);", js, re.S).group(0),
                   re.search(r"  const MESES = .*?\];", js, re.S).group(0)]
        for c in ("TRAFEGO", "CITACOES", "CATEGORIAS"):
            pedacos.append(re.search(r"  const " + c + r" = .*?;", js).group(0))
        for nome_fn in ("fnv1a32", "mesAno", "linhaDoIndice", "pintarSecao", "faixaDoCrux", funcao):
            pedacos.append(re.search(r"  (async )?function " + nome_fn + r"\(.*?\n  \}", js, re.S).group(0))
        roteiro = "\n".join(pedacos) + f"""
const bloco = {{ innerHTML: '' }};
const alvo = {{ dataset: {{ nome: 'x.com.br' }}, querySelector: () => bloco }};
globalThis.fetch = async () => ({{ ok: true, text: async () => {json.dumps(pasta_linha)} }});
{funcao}('x.com.br', alvo).then(() => process.stdout.write(bloco.innerHTML));
"""
        return subprocess.run([node, "-e", roteiro], capture_output=True, text=True, check=True).stdout

    def test_ficha_trafego(self):
        html = self._ficha("trafegoDoNome", "x.com.br\t100000\t3\t23\t202104\t202608\t202104:100000\n")
        self.assertIn("Esteve entre os 100 mil sites mais abertos do Brasil", html)
        self.assertIn("em 3 dos 23 retratos", html)
        self.assertIn("de abr/2021 a ago/2026", html)
        self.assertIn("CrUX", html)
        fraco = self._ficha("trafegoDoNome", "x.com.br\t1000000\t1\t23\t202501\t202501\t202501:1000000\n")
        self.assertIn("Teve visitas de verdade", fraco)
        self.assertIn("em um dos 23", fraco)
        self.assertIn("entre os <strong>1 milhão de</strong> sites", fraco)
        self.assertEqual(self._ficha("trafegoDoNome", "outro.com.br\t1000\t1\t23\t202501\t202501\t\n"), "")

    def test_ficha_citacoes(self):
        html = self._ficha("citacoesDoNome", "x.com.br\t14\tabril.com.br,<b>y</b>.com\t1\tBrasil Telecom\n")
        self.assertIn("14 sites de notícia linkaram para ele entre 2016 e 2019", html)
        self.assertIn("<code>abril.com.br</code>", html)
        self.assertNotIn("<b>", html)
        self.assertIn("uma página da Wikipédia em português aponta para ele", html)
        self.assertIn("Special:LinkSearch?target=*.x.com.br", html)
        self.assertIn("site oficial de <strong>Brasil Telecom</strong>", html)
        self.assertEqual(self._ficha("citacoesDoNome", "x.com.br\t0\t\t0\t\n"), "")

    def test_ficha_categorias(self):
        html = self._ficha("categoriasDoNome", "x.com.br\t20260930\t:Negócios:202103:202406;r:Apostas:202512:\n")
        self.assertIn("Atenção ao que o site já foi", html)
        # o mais novo primeiro, o ruim em destaque
        self.assertLess(html.index("Apostas"), html.index("Negócios"))
        self.assertIn("<strong>Apostas</strong> (desde dez/2025)", html)
        self.assertIn("Negócios (de mar/2021 a jun/2024)", html)
        self.assertIn("consultada em 30/09/2026", html)
        neutro = self._ficha("categoriasDoNome", "x.com.br\t20260930\t:Pets:202201:\n")
        self.assertIn("O que o site era", neutro)
        self.assertNotIn("Atenção", neutro)
        # consultado sem historico: calado
        self.assertEqual(self._ficha("categoriasDoNome", "x.com.br\t20260930\t\n"), "")


def _contraste(cor1, cor2):
    """Razao de contraste WCAG entre duas cores #rgb ou #rrggbb."""
    def luminancia(cor):
        cor = cor.lstrip("#")
        if len(cor) == 3:
            cor = "".join(c * 2 for c in cor)
        r, g, b = (int(cor[i:i + 2], 16) / 255 for i in (0, 2, 4))
        canal = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)
    l1, l2 = sorted((luminancia(cor1), luminancia(cor2)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


class TestContrasteNoEscuro(unittest.TestCase):
    """
    No escuro, --azul vira claro (#7fb0f7), e os botoes principais, os
    passos 1-2-3, a letra atual e os botoes da ficha com `color: #fff`
    dariam 2,2:1, contra os 4,5:1 do WCAG. A borda dos campos (--borda)
    tambem so daria 1,3:1 contra o fundo. --sobre-azul e --borda-campo resolvem os dois.
    """

    def setUp(self):
        self.raiz = os.path.dirname(__file__)
        self.style_css = open(os.path.join(self.raiz, "web", "style.css"), encoding="utf-8").read()
        self.extra_css = open(os.path.join(self.raiz, "site_modelo", "extra.css"), encoding="utf-8").read()

    def _token(self, bloco, nome):
        m = re.search(re.escape(nome) + r":\s*(#[0-9a-fA-F]{3,6})", bloco)
        self.assertIsNotNone(m, f"{nome} nao encontrado no bloco")
        return m.group(1)

    def test_sobre_azul_e_borda_campo_tem_contraste_nos_tres_blocos(self):
        # :root (claro), o media prefers-color-scheme e o data-tema=escuro:
        # os dois blocos escuros tem de concordar, senao o sistema e a
        # escolha manual do tema divergem (os dois blocos existem de
        # proposito e precisam repetir os mesmos valores)
        raiz = self.style_css.split(":root {", 1)[1].split("\n}", 1)[0]
        escuro_sistema = self.style_css.split(
            '@media (prefers-color-scheme: dark) {', 1)[1].split("\n  }", 1)[0]
        escuro_manual = self.style_css.split(
            ':root[data-tema="escuro"] {', 1)[1].split("\n}", 1)[0]
        for nome, bloco in (("claro", raiz), ("escuro (sistema)", escuro_sistema),
                             ("escuro (data-tema)", escuro_manual)):
            azul = self._token(bloco, "--azul")
            sobre_azul = self._token(bloco, "--sobre-azul")
            papel = self._token(bloco if "--papel" in bloco else raiz, "--papel")
            borda_campo = self._token(bloco, "--borda-campo")
            self.assertGreaterEqual(_contraste(sobre_azul, azul), 4.5,
                                     f"--sobre-azul sobre --azul no {nome}")
            self.assertGreaterEqual(_contraste(borda_campo, papel), 3.0,
                                     f"--borda-campo sobre --papel no {nome}")
        self.assertEqual(self._token(escuro_sistema, "--sobre-azul"),
                          self._token(escuro_manual, "--sobre-azul"))
        self.assertEqual(self._token(escuro_sistema, "--borda-campo"),
                          self._token(escuro_manual, "--borda-campo"))

    def test_nenhum_fff_fixo_sobre_azul(self):
        # o unico color: #fff que pode sobrar e o do button.perigo (sem
        # uso no site, comentado); nenhum outro par com background/border
        # var(--azul) pode voltar a usar #fff fixo em vez de --sobre-azul
        for css, nome in ((self.style_css, "style.css"), (self.extra_css, "extra.css")):
            for m in re.finditer(r"\{[^{}]*var\(--azul\)[^{}]*\}", css):
                regra = m.group(0)
                if "perigo" in css[max(0, m.start() - 40):m.start()]:
                    continue
                self.assertNotIn("color: #fff", regra,
                                  f"{nome}: {regra!r} ainda usa #fff fixo sobre --azul")

    def test_piso_de_treze_px_nos_textos_pequenos_da_lista(self):
        # desde 29/09/2026 o tamanho vem de um dos dois tokens de letra
        # pequena (--letra-mini, --letra-apoio), definidos em style.css
        tokens = {t: float(v) for t, v in re.findall(
            r"--(letra-[a-z]+):\s*([\d.]+)rem", self.style_css)}
        self.assertEqual(set(tokens), {"letra-mini", "letra-apoio"})
        self.assertGreaterEqual(min(tokens.values()), 0.8125)
        for nome, css in (
            (".selo", self.style_css), (".pilula-elegivel", self.style_css),
            ("button.pontocom", self.style_css), (".cartao .ajuda", self.style_css),
            (".idade", self.extra_css), (".conferir", self.extra_css),
            (".etiqueta", self.extra_css),
        ):
            bloco = re.search(re.escape(nome) + r"\s*\{([^}]*)\}", css)
            self.assertIsNotNone(bloco, nome)
            tamanho = re.search(r"font-size:\s*(?:([\d.]+)rem|var\(--(letra-[a-z]+)\))",
                                bloco.group(1))
            self.assertIsNotNone(tamanho, nome)
            valor = float(tamanho.group(1)) if tamanho.group(1) else tokens[tamanho.group(2)]
            self.assertGreaterEqual(valor, 0.8125, nome)
